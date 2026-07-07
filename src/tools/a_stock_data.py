import datetime as dt
import json
import logging
import re

import requests

from src.data.models import CompanyNews, FinancialMetrics, InsiderTrade, LineItem, Price

logger = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
TIMEOUT = 15

ETF_TRACKING_INDEXES = {
    # Core broad-market ETFs
    "159361": ("000510", "sh", "CSI A500"),
    "512050": ("000510", "sh", "CSI A500"),
    "510300": ("000300", "sh", "CSI 300"),
    "159919": ("000300", "sh", "CSI 300"),
    "510310": ("000300", "sh", "CSI 300"),
    "510330": ("000300", "sh", "CSI 300"),
    # Mid/small-cap beta
    "510500": ("000905", "sh", "CSI 500"),
    "159922": ("000905", "sh", "CSI 500"),
    "512500": ("000905", "sh", "CSI 500"),
    "159845": ("000852", "sh", "CSI 1000"),
    "512100": ("000852", "sh", "CSI 1000"),
    # Defensive dividend ETFs
    "512890": ("000922", "sh", "CSI Dividend"),
    "515080": ("000922", "sh", "CSI Dividend"),
    "510880": ("000015", "sh", "SSE Dividend"),
    # Growth and overseas-China ETFs
    "159915": ("399006", "sz", "ChiNext Index"),
    "588000": ("000688", "sh", "STAR 50"),
    "588080": ("000688", "sh", "STAR 50"),
    "513180": ("HSTECH", "hk", "Hang Seng TECH Index"),
    "513130": ("HSTECH", "hk", "Hang Seng TECH Index"),
    # Common style/large-cap ETFs
    "510050": ("000016", "sh", "SSE 50"),
    "159949": ("399673", "sz", "ChiNext 50"),
}


def normalize_a_stock_ticker(ticker: str) -> str | None:
    """Return a 6-digit A-share/China ETF code, or None for non-China tickers."""
    raw = ticker.strip().upper()
    raw = raw.replace(" ", "")

    suffix_match = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", raw)
    if suffix_match:
        return suffix_match.group(1)

    prefix_match = re.fullmatch(r"(SH|SZ|BJ)(\d{6})", raw)
    if prefix_match:
        return prefix_match.group(2)

    if re.fullmatch(r"\d{6}", raw):
        return raw

    return None


def is_a_stock_ticker(ticker: str) -> bool:
    return normalize_a_stock_ticker(ticker) is not None


def _market_prefix(code: str) -> str:
    if code.startswith(("5", "6", "9")):
        return "sh"
    if code.startswith(("4", "8")):
        return "bj"
    return "sz"


def _secid(code: str) -> str:
    return ("1." if _market_prefix(code) == "sh" else "0.") + code


def _tencent_symbol(code: str, market: str | None = None) -> str:
    if market == "hk":
        return f"hk{code}"
    if market:
        return f"{market}{code}"
    return f"{_market_prefix(code)}{code}"


def _float(value: str | int | float | None) -> float | None:
    if value in (None, "", "-", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date_range(start_date: str, end_date: str) -> tuple[dt.date, dt.date]:
    return dt.date.fromisoformat(start_date), dt.date.fromisoformat(end_date)


def get_tracking_index(ticker: str) -> dict | None:
    """Return the known tracking index for an onshore ETF ticker."""
    code = normalize_a_stock_ticker(ticker)
    if not code or code not in ETF_TRACKING_INDEXES:
        return None
    index_code, market, name = ETF_TRACKING_INDEXES[code]
    return {"code": index_code, "market": market, "name": name}


def is_china_etf(ticker: str) -> bool:
    code = normalize_a_stock_ticker(ticker)
    return bool(code and (code in ETF_TRACKING_INDEXES or code.startswith(("15", "51", "56", "58"))))


def get_a_stock_prices(ticker: str, start_date: str, end_date: str) -> list[Price]:
    """Fetch A-share/ETF daily prices from Tencent qfq K-line."""
    code = normalize_a_stock_ticker(ticker)
    if not code:
        return []

    start, end = _date_range(start_date, end_date)
    symbol = f"{_market_prefix(code)}{code}"
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {"param": f"{symbol},day,,,900,qfq"}

    try:
        response = requests.get(url, params=params, headers={"User-Agent": UA, "Referer": "https://gu.qq.com/"}, timeout=TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning("Failed to fetch Tencent K-line for %s: %s", ticker, exc)
        return []

    raw_rows = (payload.get("data") or {}).get(symbol, {})
    rows = raw_rows.get("qfqday") or raw_rows.get("day") or []

    prices: list[Price] = []
    for row in rows:
        try:
            day = dt.date.fromisoformat(row[0])
        except (TypeError, ValueError):
            continue
        if not (start <= day <= end):
            continue
        try:
            prices.append(
                Price(
                    time=f"{row[0]}T00:00:00Z",
                    open=float(row[1]),
                    close=float(row[2]),
                    high=float(row[3]),
                    low=float(row[4]),
                    volume=int(float(row[5])) if len(row) > 5 else 0,
                )
            )
        except (TypeError, ValueError, IndexError):
            continue

    return prices


def get_a_stock_quote(ticker: str) -> dict:
    """Fetch real-time quote and valuation fields from Tencent."""
    code = normalize_a_stock_ticker(ticker)
    if not code:
        return {}

    symbol = f"{_market_prefix(code)}{code}"
    return _get_tencent_quote(symbol, code)


def get_a_stock_index_quote(index_code: str, market: str) -> dict:
    """Fetch real-time index quote and valuation fields from Tencent."""
    symbol = _tencent_symbol(index_code, market)
    return _get_tencent_quote(symbol, index_code)


def _get_tencent_quote(symbol: str, ticker: str) -> dict:
    url = f"https://qt.gtimg.cn/q={symbol}"

    try:
        response = requests.get(url, headers={"User-Agent": UA, "Referer": "https://gu.qq.com/"}, timeout=TIMEOUT)
        response.raise_for_status()
        text = response.content.decode("gbk", errors="ignore")
        body = text.split('"')[1]
        vals = body.split("~")
    except Exception as exc:
        logger.warning("Failed to fetch Tencent quote for %s: %s", ticker, exc)
        return {}

    if len(vals) < 47:
        return {}

    market_cap_yi = _float(vals[44])
    price = _float(vals[3])
    pe_ttm = _float(vals[39])
    pb = _float(vals[46])
    shares = None
    if market_cap_yi and price and price > 0:
        shares = market_cap_yi * 100_000_000 / price

    return {
        "ticker": ticker,
        "name": vals[1],
        "price": price,
        "open": _float(vals[5]) if len(vals) > 5 else None,
        "market_cap": market_cap_yi * 100_000_000 if market_cap_yi else None,
        "pe_ttm": pe_ttm if pe_ttm and pe_ttm > 0 else None,
        "pb": pb if pb and pb > 0 else None,
        "pe_static": _float(vals[52]) if len(vals) > 52 else None,
        "shares_outstanding": shares,
        "currency": "CNY",
    }


def get_a_stock_financial_metrics(ticker: str, end_date: str, period: str = "ttm", limit: int = 10) -> list[FinancialMetrics]:
    """Return FinancialMetrics from Tencent quote valuation fields.

    This is intentionally conservative: fields not available from free A-share
    endpoints are left as None instead of being guessed.
    """
    code = normalize_a_stock_ticker(ticker)
    if not code:
        return []

    quote = get_a_stock_quote(code)
    if not quote:
        return []
    tracking_index = get_tracking_index(code)
    index_quote = None
    if tracking_index:
        index_quote = get_a_stock_index_quote(tracking_index["code"], tracking_index["market"])

    price = quote.get("price")
    valuation_quote = index_quote or quote
    pe_ttm = valuation_quote.get("pe_ttm")
    eps = price / pe_ttm if price and pe_ttm and pe_ttm > 0 else None

    metrics: list[FinancialMetrics] = []
    end = dt.date.fromisoformat(end_date)
    for idx in range(max(1, limit)):
        report_date = end - dt.timedelta(days=idx * 90)
        metrics.append(
            FinancialMetrics(
                ticker=code,
                report_period=report_date.isoformat(),
                period=period,
                currency="CNY",
                market_cap=quote.get("market_cap"),
                enterprise_value=None,
                price_to_earnings_ratio=valuation_quote.get("pe_ttm"),
                price_to_book_ratio=valuation_quote.get("pb"),
                price_to_sales_ratio=None,
                enterprise_value_to_ebitda_ratio=None,
                enterprise_value_to_revenue_ratio=None,
                free_cash_flow_yield=None,
                peg_ratio=None,
                gross_margin=None,
                operating_margin=None,
                net_margin=None,
                return_on_equity=None,
                return_on_assets=None,
                return_on_invested_capital=None,
                asset_turnover=None,
                inventory_turnover=None,
                receivables_turnover=None,
                days_sales_outstanding=None,
                operating_cycle=None,
                working_capital_turnover=None,
                current_ratio=None,
                quick_ratio=None,
                cash_ratio=None,
                operating_cash_flow_ratio=None,
                debt_to_equity=None,
                debt_to_assets=None,
                interest_coverage=None,
                revenue_growth=None,
                earnings_growth=None,
                book_value_growth=None,
                earnings_per_share_growth=None,
                free_cash_flow_growth=None,
                operating_income_growth=None,
                ebitda_growth=None,
                payout_ratio=None,
                earnings_per_share=eps,
                book_value_per_share=(price / quote["pb"] if price and quote.get("pb") and quote["pb"] > 0 else None),
                free_cash_flow_per_share=None,
            )
        )
    return metrics


def get_a_stock_line_items(
    ticker: str,
    line_items: list[str],
    end_date: str,
    period: str = "ttm",
    limit: int = 10,
) -> list[LineItem]:
    """Return LineItem rows with known quote-derived fields and requested keys.

    Most US GAAP line items do not map cleanly to the free A-share endpoints.
    Missing fields are present with None so existing agents degrade gracefully.
    """
    code = normalize_a_stock_ticker(ticker)
    if not code:
        return []

    quote = get_a_stock_quote(code)
    if not quote:
        return []
    tracking_index = get_tracking_index(code)
    index_quote = None
    if tracking_index:
        index_quote = get_a_stock_index_quote(tracking_index["code"], tracking_index["market"])

    metrics = get_a_stock_financial_metrics(code, end_date, period=period, limit=1)
    latest = metrics[0] if metrics else None
    end = dt.date.fromisoformat(end_date)

    rows: list[LineItem] = []
    requested = set(line_items)
    defaults = {
        "market_cap": quote.get("market_cap"),
        "outstanding_shares": quote.get("shares_outstanding"),
        "earnings_per_share": latest.earnings_per_share if latest else None,
        "book_value_per_share": latest.book_value_per_share if latest else None,
        "tracking_index_code": tracking_index["code"] if tracking_index else None,
        "tracking_index_name": tracking_index["name"] if tracking_index else None,
        "tracking_index_pe_ttm": index_quote.get("pe_ttm") if index_quote else None,
        "tracking_index_pb": index_quote.get("pb") if index_quote else None,
        "data_source": "Tencent quote; ETF valuation uses mapped tracking index when available",
    }
    for idx in range(max(1, limit)):
        report_date = end - dt.timedelta(days=idx * 90)
        payload = {
            "ticker": code,
            "report_period": report_date.isoformat(),
            "period": period,
            "currency": "CNY",
        }
        for item in requested:
            payload[item] = defaults.get(item)
        for key, value in defaults.items():
            payload.setdefault(key, value)
        rows.append(LineItem(**payload))
    return rows


def get_a_stock_company_news(
    ticker: str,
    end_date: str,
    start_date: str | None = None,
    limit: int = 100,
) -> list[CompanyNews]:
    code = normalize_a_stock_ticker(ticker)
    if not code:
        return []

    query = json.dumps(
        {
            "uid": "",
            "keyword": code,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "default",
                    "pageIndex": 1,
                    "pageSize": min(limit, 100),
                    "preTag": "",
                    "postTag": "",
                }
            },
        },
        separators=(",", ":"),
    )
    params = {"cb": "jQuery_news", "param": query}
    url = "https://search-api-web.eastmoney.com/search/jsonp"

    try:
        response = requests.get(
            url,
            params=params,
            headers={"User-Agent": UA, "Referer": "https://so.eastmoney.com/"},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        text = response.text
        json_text = text[text.index("(") + 1 : text.rindex(")")]
        payload = json.loads(json_text)
    except Exception as exc:
        logger.warning("Failed to fetch Eastmoney news for %s: %s", ticker, exc)
        return []

    start = dt.date.fromisoformat(start_date) if start_date else None
    end = dt.date.fromisoformat(end_date)
    out: list[CompanyNews] = []
    for article in (payload.get("result") or {}).get("cmsArticleWebOld", []) or []:
        raw_date = str(article.get("date", ""))[:10]
        try:
            article_date = dt.date.fromisoformat(raw_date)
        except ValueError:
            article_date = end
        if start and article_date < start:
            continue
        if article_date > end:
            continue

        title = re.sub(r"<[^>]+>", "", article.get("title", ""))
        content = re.sub(r"<[^>]+>", "", article.get("content", ""))
        out.append(
            CompanyNews(
                ticker=code,
                title=title or content[:80] or f"{code} news",
                author=None,
                source=article.get("mediaName") or "Eastmoney",
                date=f"{article_date.isoformat()}T00:00:00Z",
                url=article.get("url") or "",
                sentiment=None,
            )
        )
        if len(out) >= limit:
            break

    return out


def get_a_stock_insider_trades(
    ticker: str,
    end_date: str,
    start_date: str | None = None,
    limit: int = 1000,
) -> list[InsiderTrade]:
    """A-share insider/director trade disclosure is not mapped yet."""
    return []


def get_a_stock_market_cap(ticker: str, end_date: str) -> float | None:
    quote = get_a_stock_quote(ticker)
    return quote.get("market_cap") if quote else None
