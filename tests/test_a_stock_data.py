from unittest.mock import Mock, patch

from src.tools.a_stock_data import (
    get_a_stock_financial_metrics,
    get_a_stock_line_items,
    get_tracking_index,
    is_a_stock_ticker,
    normalize_a_stock_ticker,
)
from src.tools.api import get_prices


def test_normalize_a_stock_ticker_accepts_common_formats():
    assert normalize_a_stock_ticker("600519") == "600519"
    assert normalize_a_stock_ticker("600519.SH") == "600519"
    assert normalize_a_stock_ticker("sz159915") == "159915"
    assert normalize_a_stock_ticker("BJ832000") == "832000"
    assert normalize_a_stock_ticker("AAPL") is None


def test_is_a_stock_ticker():
    assert is_a_stock_ticker("510300")
    assert is_a_stock_ticker("159361.SZ")
    assert not is_a_stock_ticker("MSFT")


def test_get_tracking_index_for_known_china_etfs():
    assert get_tracking_index("510300") == {"code": "000300", "market": "sh", "name": "CSI 300"}
    assert get_tracking_index("159915.SZ") == {"code": "399006", "market": "sz", "name": "ChiNext Index"}
    assert get_tracking_index("600519") is None


@patch("src.tools.a_stock_data.get_a_stock_index_quote")
@patch("src.tools.a_stock_data.get_a_stock_quote")
def test_china_etf_financial_metrics_use_tracking_index_valuation(mock_quote, mock_index_quote):
    mock_quote.return_value = {
        "ticker": "510300",
        "name": "300ETF",
        "price": 4.8,
        "market_cap": 82_000_000_000,
        "pe_ttm": None,
        "pb": None,
        "shares_outstanding": 17_000_000_000,
    }
    mock_index_quote.return_value = {
        "ticker": "000300",
        "name": "CSI 300",
        "price": 4800,
        "market_cap": 52_000_000_000_000,
        "pe_ttm": 14.4,
        "pb": None,
    }

    metrics = get_a_stock_financial_metrics("510300", "2026-07-03", limit=1)

    assert metrics[0].market_cap == 82_000_000_000
    assert metrics[0].price_to_earnings_ratio == 14.4
    mock_index_quote.assert_called_once_with("000300", "sh")


@patch("src.tools.a_stock_data.get_a_stock_index_quote")
@patch("src.tools.a_stock_data.get_a_stock_quote")
def test_china_etf_line_items_include_tracking_index_context(mock_quote, mock_index_quote):
    mock_quote.return_value = {
        "ticker": "159915",
        "name": "创业板ETF",
        "price": 4.0,
        "market_cap": 60_000_000_000,
        "pe_ttm": None,
        "pb": None,
        "shares_outstanding": 15_000_000_000,
    }
    mock_index_quote.return_value = {
        "ticker": "399006",
        "name": "ChiNext Index",
        "price": 3900,
        "market_cap": 16_000_000_000_000,
        "pe_ttm": 70.5,
        "pb": None,
    }

    rows = get_a_stock_line_items(
        "159915.SZ",
        ["tracking_index_name", "tracking_index_pe_ttm", "outstanding_shares"],
        "2026-07-03",
        limit=1,
    )

    assert rows[0].tracking_index_code == "399006"
    assert rows[0].tracking_index_name == "ChiNext Index"
    assert rows[0].tracking_index_pe_ttm == 70.5
    assert rows[0].outstanding_shares == 15_000_000_000



@patch("src.tools.api._cache")
@patch("src.tools.api.get_a_stock_prices")
@patch("src.tools.api.requests.get")
def test_get_prices_routes_a_stock_to_a_stock_provider(mock_get, mock_a_stock_prices, mock_cache):
    mock_cache.get_prices.return_value = None
    mock_a_stock_prices.return_value = []

    result = get_prices("510300", "2026-06-20", "2026-07-03")

    assert result == []
    mock_a_stock_prices.assert_called_once_with("510300", "2026-06-20", "2026-07-03")
    mock_get.assert_not_called()


@patch("src.tools.api._cache")
@patch("src.tools.api.get_a_stock_prices")
@patch("src.tools.api.requests.get")
def test_get_prices_keeps_us_tickers_on_financial_datasets(mock_get, mock_a_stock_prices, mock_cache):
    mock_cache.get_prices.return_value = None
    response = Mock()
    response.status_code = 500
    mock_get.return_value = response

    result = get_prices("AAPL", "2024-01-01", "2024-01-02")

    assert result == []
    mock_a_stock_prices.assert_not_called()
    assert "financialdatasets.ai/prices" in mock_get.call_args.args[0]
