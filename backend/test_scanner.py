import asyncio
import pytest
import pandas as pd
import numpy as np
import pandas_ta_classic as ta
from unittest.mock import patch, MagicMock, AsyncMock, call

from backend.scanner import (
    _is_rate_limit,
    _fetch_market_caps_bulk,
    _fetch_market_caps_bulk_async,
    get_active_filters,
    get_full_market_tickers,
    screen_stocks,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def run_screen(tickers):
    async def _collect():
        return [item async for item in screen_stocks(tickers)]
    return asyncio.run(_collect())


def make_passing_df(days=300, final_price=160.0, prev_price=152.0, volume=600_000):
    """DataFrame where the last two closes give >3% change and natural indicators pass."""
    dates = pd.date_range(end="2024-01-01", periods=days)
    prices = np.linspace(100.0, 155.0, days).copy()
    prices[-1] = final_price
    prices[-2] = prev_price
    return pd.DataFrame({
        "Open": prices * 0.99,
        "High": prices * 1.01,
        "Low": prices * 0.98,
        "Close": prices,
        "Volume": volume,
    }, index=dates)


def multiindex(df, ticker="AAPL"):
    df.columns = pd.MultiIndex.from_product([[ticker], df.columns])
    return df


def mock_ema_side_effect(series, length=None, **kw):
    val = {8: 155.0, 50: 130.0, 200: 110.0}.get(length, 140.0)
    return pd.Series([val] * len(series), index=series.index)


def mock_macd_df(days=300, hist=0.5):
    return pd.DataFrame({
        "MACD_12_26_9": [1.0] * days,
        "MACDs_12_26_9": [0.5] * days,
        "MACDh_12_26_9": [hist] * days,
    })


def mock_bb_df(days=300, upper=170.0, middle=155.0, lower=140.0):
    return pd.DataFrame({
        "BBL_20_2.0": [lower] * days,
        "BBM_20_2.0": [middle] * days,
        "BBU_20_2.0": [upper] * days,
    })


# ── _is_rate_limit ────────────────────────────────────────────────────────────

def test_is_rate_limit_detects_rate_limit_string():
    exc = Exception("rate limit exceeded")
    assert _is_rate_limit(exc) is True


def test_is_rate_limit_detects_too_many_requests():
    exc = Exception("Too Many Requests")
    assert _is_rate_limit(exc) is True


def test_is_rate_limit_detects_yfratelimit_class_name():
    class YfRateLimitError(Exception):
        pass
    exc = YfRateLimitError("blocked")
    assert _is_rate_limit(exc) is True


def test_is_rate_limit_false_for_generic_error():
    exc = ValueError("some other error")
    assert _is_rate_limit(exc) is False


# ── _fetch_market_caps_bulk ───────────────────────────────────────────────────

def test_fetch_market_caps_success():
    mock_ticker = MagicMock()
    mock_ticker.fast_info.market_cap = 2_000_000_000.0
    mock_ticker.fast_info.exchange = "NMS"
    mock_ticker.fast_info.last_price = 150.0

    with patch("backend.scanner.yf.Ticker", return_value=mock_ticker):
        result = _fetch_market_caps_bulk(["AAPL"])

    assert result["AAPL"]["market_cap"] == 2_000_000_000.0
    assert result["AAPL"]["exchange"] == "NASDAQ"
    assert result["AAPL"]["last_price"] == 150.0


def test_fetch_market_caps_none_market_cap():
    mock_ticker = MagicMock()
    mock_ticker.fast_info.market_cap = None
    mock_ticker.fast_info.exchange = "NYQ"
    mock_ticker.fast_info.last_price = 80.0

    with patch("backend.scanner.yf.Ticker", return_value=mock_ticker):
        result = _fetch_market_caps_bulk(["XOM"])

    assert result["XOM"]["market_cap"] == 0.0
    assert result["XOM"]["last_price"] == 80.0


def test_fetch_market_caps_rate_limit_exhausted_uses_fallback():
    class YfRateLimitError(Exception):
        pass

    with patch("backend.scanner.yf.Ticker", side_effect=YfRateLimitError("rate limit")):
        with patch("backend.scanner.time.sleep"):
            result = _fetch_market_caps_bulk(["AAPL"])

    assert result["AAPL"]["market_cap"] > 0  # fallback large-cap
    assert result["AAPL"]["last_price"] is None


def test_fetch_market_caps_non_rate_limit_error_returns_zero():
    with patch("backend.scanner.yf.Ticker", side_effect=ValueError("unexpected")):
        result = _fetch_market_caps_bulk(["AAPL"])

    assert result["AAPL"]["market_cap"] == 0.0
    assert result["AAPL"]["last_price"] is None


def test_fetch_market_caps_rate_limit_then_success():
    class YfRateLimitError(Exception):
        pass

    call_count = {"n": 0}
    mock_ticker_ok = MagicMock()
    mock_ticker_ok.fast_info.market_cap = 1_500_000_000.0
    mock_ticker_ok.fast_info.exchange = "NMS"
    mock_ticker_ok.fast_info.last_price = 150.0

    def ticker_factory(sym):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise YfRateLimitError("rate limit")
        return mock_ticker_ok

    with patch("backend.scanner.yf.Ticker", side_effect=ticker_factory):
        with patch("backend.scanner.time.sleep"):
            result = _fetch_market_caps_bulk(["AAPL"])

    assert result["AAPL"]["market_cap"] == 1_500_000_000.0
    assert result["AAPL"]["last_price"] == 150.0


# ── _fetch_market_caps_bulk_async ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_market_caps_bulk_async_success():
    mock_ticker = MagicMock()
    mock_ticker.fast_info.market_cap = 2_000_000_000.0
    mock_ticker.fast_info.exchange = "NMS"
    mock_ticker.fast_info.last_price = 150.0

    semaphore = asyncio.Semaphore(5)
    with patch("backend.scanner.yf.Ticker", return_value=mock_ticker):
        with patch("backend.scanner.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            mock_to_thread.return_value = mock_ticker.fast_info
            result = await _fetch_market_caps_bulk_async(["AAPL"], semaphore)

    assert result["AAPL"]["market_cap"] == 2_000_000_000.0
    assert result["AAPL"]["exchange"] == "NASDAQ"
    assert result["AAPL"]["last_price"] == 150.0


@pytest.mark.asyncio
async def test_fetch_market_caps_bulk_async_concurrent():
    """All tickers in the chunk appear in the result (gathered concurrently)."""
    tickers = ["AAPL", "MSFT", "GOOGL"]

    def make_fi():
        fi = MagicMock()
        fi.market_cap = 2_000_000_000.0
        fi.exchange = "NMS"
        fi.last_price = 150.0
        return fi

    semaphore = asyncio.Semaphore(10)
    with patch("backend.scanner.asyncio.to_thread", new_callable=AsyncMock, return_value=make_fi()):
        result = await _fetch_market_caps_bulk_async(tickers, semaphore)

    assert set(result.keys()) == set(tickers)
    assert all(result[t]["last_price"] == 150.0 for t in tickers)
    assert all(result[t]["market_cap"] == 2_000_000_000.0 for t in tickers)


@pytest.mark.asyncio
async def test_fetch_market_caps_bulk_async_rate_limit_fallback():
    class YfRateLimitError(Exception):
        pass

    semaphore = asyncio.Semaphore(5)
    with patch("backend.scanner.asyncio.to_thread", new_callable=AsyncMock, side_effect=YfRateLimitError("rate limit")):
        with patch("backend.scanner.asyncio.sleep", new_callable=AsyncMock):
            result = await _fetch_market_caps_bulk_async(["AAPL"], semaphore)

    assert result["AAPL"]["market_cap"] > 0  # fallback large-cap
    assert result["AAPL"]["last_price"] is None


@pytest.mark.asyncio
async def test_fetch_market_caps_bulk_async_non_rate_limit_error():
    semaphore = asyncio.Semaphore(5)
    with patch("backend.scanner.asyncio.to_thread", new_callable=AsyncMock, side_effect=ValueError("unexpected")):
        result = await _fetch_market_caps_bulk_async(["AAPL"], semaphore)

    assert result["AAPL"]["market_cap"] == 0.0
    assert result["AAPL"]["last_price"] is None


# ── get_full_market_tickers ───────────────────────────────────────────────────

@patch("backend.scanner.requests.get")
def test_get_full_market_tickers_success(mock_get):
    mock_resp = MagicMock()
    mock_resp.text = "Symbol\nAAPL\nMSFT"
    mock_resp.raise_for_status = MagicMock()
    mock_get.return_value = mock_resp
    tickers, is_full = get_full_market_tickers()
    assert tickers == ["AAPL", "MSFT"]
    assert is_full is True


@patch("backend.scanner.requests.get")
def test_get_full_market_tickers_network_fail(mock_get):
    mock_get.side_effect = Exception("Network error")
    tickers, is_full = get_full_market_tickers()
    assert "AAPL" in tickers
    assert is_full is False


@patch("backend.scanner.requests.get")
def test_get_full_market_tickers_replaces_dots_with_dashes(mock_get):
    mock_resp = MagicMock()
    mock_resp.text = "Symbol\nBRK.B\nAAPL"
    mock_resp.raise_for_status = MagicMock()
    mock_get.return_value = mock_resp
    tickers, _ = get_full_market_tickers()
    assert "BRK-B" in tickers


# ── get_active_filters ────────────────────────────────────────────────────────

def test_get_active_filters_returns_11():
    filters = get_active_filters()
    assert len(filters) == 11


def test_get_active_filters_contains_day_change():
    filters = get_active_filters()
    assert any("Day Change" in f for f in filters)


# ── screen_stocks: early filter failures ─────────────────────────────────────

@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
def test_screen_stocks_fails_market_cap(mock_caps, mock_dl):
    df = multiindex(make_passing_df(final_price=160.0, prev_price=152.0))
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 500_000_000.0, "exchange": "NASDAQ", "last_price": 160.0}}
    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
def test_screen_stocks_fails_day_change(mock_caps, mock_dl):
    df = multiindex(make_passing_df(final_price=101.0, prev_price=100.0))  # 1% change
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 101.0}}
    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
def test_screen_stocks_fails_volume(mock_caps, mock_dl):
    df = multiindex(make_passing_df(volume=100_000))  # below 500K
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 160.0}}
    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
def test_screen_stocks_fails_sma200(mock_caps, mock_dl):
    # Keep SMA200 ≈ 200 but set last price very low
    days = 300
    dates = pd.date_range(end="2024-01-01", periods=days)
    prices = np.full(days, 200.0)
    prices[-1] = 10.0
    prices[-2] = 9.5
    df = pd.DataFrame({
        "Open": prices * 0.99, "High": prices * 1.01,
        "Low": prices * 0.98, "Close": prices, "Volume": 600_000,
    }, index=dates)
    df = multiindex(df)
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 10.0}}
    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
@patch("backend.scanner.ta.ema")
def test_screen_stocks_fails_ema8(mock_ema, mock_caps, mock_dl):
    df = multiindex(make_passing_df())
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 160.0}}
    # EMA8 = 250 → 80% of 250 = 200 > price 160 → fails
    mock_ema.side_effect = lambda s, length=None, **kw: pd.Series([250.0] * len(s), index=s.index)
    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


# ── screen_stocks: data quality checks ───────────────────────────────────────

@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
def test_screen_stocks_skips_ticker_not_in_multiindex(mock_caps, mock_dl):
    df = multiindex(make_passing_df(), ticker="MSFT")  # only MSFT in data
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 160.0}}
    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
def test_screen_stocks_skips_too_short_data(mock_caps, mock_dl):
    df = make_passing_df(days=100)  # only 100 rows
    df = multiindex(df)
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 160.0}}
    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
def test_screen_stocks_skips_nan_price(mock_caps, mock_dl):
    df = make_passing_df()
    prices = df["Close"].values.copy()
    prices[-1] = float("nan")
    df["Close"] = prices
    df = multiindex(df)
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": None}}
    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
def test_screen_stocks_skips_zero_prev_close(mock_caps, mock_dl):
    df = make_passing_df()
    prices = df["Close"].values.copy()
    prices[-2] = 0.0
    df["Close"] = prices
    df = multiindex(df)
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 160.0}}
    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
@patch("backend.scanner.ta.ema")
def test_screen_stocks_non_multiindex_data(mock_ema, mock_caps, mock_dl):
    """Single-ticker download may return a plain (non-MultiIndex) DataFrame."""
    df = make_passing_df(final_price=101.0, prev_price=100.0)  # 1% → fails change
    mock_dl.return_value = df  # no MultiIndex
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 101.0}}
    mock_ema.side_effect = mock_ema_side_effect
    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
def test_screen_stocks_empty_download_raises_and_retries(mock_caps, mock_dl):
    """All download attempts return empty data → chunk error path."""
    mock_dl.return_value = pd.DataFrame()  # triggers ValueError("Empty data returned")
    mock_caps.return_value = {}
    with patch("backend.scanner.asyncio.sleep"):
        results = run_screen(["AAPL"])
    progress_events = [r for r in results if isinstance(r, dict) and r.get("status") == "progress"]
    assert len(progress_events) > 0  # progress still emitted via error path


# ── screen_stocks: post-EMA8 filter failures ─────────────────────────────────

@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
@patch("backend.scanner.ta.ema")
@patch("backend.scanner.ta.rsi")
def test_screen_stocks_fails_rsi_low(mock_rsi, mock_ema, mock_caps, mock_dl):
    df = multiindex(make_passing_df())
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 160.0}}
    mock_ema.side_effect = mock_ema_side_effect
    mock_rsi.return_value = pd.Series([45.0] * 300)  # RSI < 50
    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
@patch("backend.scanner.ta.ema")
@patch("backend.scanner.ta.rsi")
def test_screen_stocks_fails_rsi_high(mock_rsi, mock_ema, mock_caps, mock_dl):
    df = multiindex(make_passing_df())
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 160.0}}
    mock_ema.side_effect = mock_ema_side_effect
    mock_rsi.return_value = pd.Series([75.0] * 300)  # RSI > 70
    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
@patch("backend.scanner.ta.ema")
@patch("backend.scanner.ta.rsi")
@patch("backend.scanner.ta.macd")
def test_screen_stocks_fails_macd_hist(mock_macd, mock_rsi, mock_ema, mock_caps, mock_dl):
    df = multiindex(make_passing_df())
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 160.0}}
    mock_ema.side_effect = mock_ema_side_effect
    mock_rsi.return_value = pd.Series([60.0] * 300)
    mock_macd.return_value = mock_macd_df(hist=-0.5)  # hist <= 0
    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
@patch("backend.scanner.ta.ema")
@patch("backend.scanner.ta.rsi")
@patch("backend.scanner.ta.macd")
def test_screen_stocks_macd_none_uses_zero(mock_macd, mock_rsi, mock_ema, mock_caps, mock_dl):
    df = multiindex(make_passing_df())
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 160.0}}
    mock_ema.side_effect = mock_ema_side_effect
    mock_rsi.return_value = pd.Series([60.0] * 300)
    mock_macd.return_value = None  # None → falls back to hist=0.0 → fails filter
    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
@patch("backend.scanner.ta.ema")
@patch("backend.scanner.ta.rsi")
@patch("backend.scanner.ta.macd")
def test_screen_stocks_fails_ema50(mock_macd, mock_rsi, mock_ema, mock_caps, mock_dl):
    df = multiindex(make_passing_df())
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 160.0}}

    def ema_se(series, length=None, **kw):
        val = {8: 155.0, 50: 200.0, 200: 110.0}.get(length, 140.0)  # EMA50=200 > price 160
        return pd.Series([val] * len(series), index=series.index)

    mock_ema.side_effect = ema_se
    mock_rsi.return_value = pd.Series([60.0] * 300)
    mock_macd.return_value = mock_macd_df(hist=0.5)
    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
@patch("backend.scanner.ta.ema")
@patch("backend.scanner.ta.rsi")
@patch("backend.scanner.ta.macd")
def test_screen_stocks_fails_ema200(mock_macd, mock_rsi, mock_ema, mock_caps, mock_dl):
    df = multiindex(make_passing_df())
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 160.0}}

    def ema_se(series, length=None, **kw):
        val = {8: 155.0, 50: 130.0, 200: 200.0}.get(length, 140.0)  # EMA200=200 > price 160
        return pd.Series([val] * len(series), index=series.index)

    mock_ema.side_effect = ema_se
    mock_rsi.return_value = pd.Series([60.0] * 300)
    mock_macd.return_value = mock_macd_df(hist=0.5)
    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
@patch("backend.scanner.ta.ema")
@patch("backend.scanner.ta.rsi")
@patch("backend.scanner.ta.macd")
@patch("backend.scanner.ta.bbands")
def test_screen_stocks_fails_bb_upper(mock_bb, mock_macd, mock_rsi, mock_ema, mock_caps, mock_dl):
    df = multiindex(make_passing_df())
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 160.0}}
    mock_ema.side_effect = mock_ema_side_effect
    mock_rsi.return_value = pd.Series([60.0] * 300)
    mock_macd.return_value = mock_macd_df(hist=0.5)
    mock_bb.return_value = mock_bb_df(upper=150.0)  # BB upper=150 < price 160 → fails
    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
@patch("backend.scanner.ta.bbands")
def test_screen_stocks_bb_none_uses_fallback(mock_bb, mock_caps, mock_dl):
    """When bbands returns None, fallback values are computed from price (price < BB upper)."""
    df = multiindex(make_passing_df())
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 160.0}}
    mock_bb.return_value = None

    with patch("backend.scanner.ta.ema", side_effect=mock_ema_side_effect):
        with patch("backend.scanner.ta.rsi", return_value=pd.Series([60.0] * 300)):
            with patch("backend.scanner.ta.macd", return_value=mock_macd_df(hist=0.5)):
                with patch("backend.scanner.ta.atr", return_value=pd.Series([3.0] * 300)):
                    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]

    # With fallback BB upper = price * 1.03 ≈ 164.8 > price 160 → should pass BB filter
    assert len(results) == 1


# ── screen_stocks: full passing run ──────────────────────────────────────────

@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
@patch("backend.scanner.ta.rsi")
@patch("backend.scanner.ta.macd")
@patch("backend.scanner.ta.ema")
@patch("backend.scanner.ta.bbands")
@patch("backend.scanner.ta.atr")
def test_screen_stocks_success(mock_atr, mock_bb, mock_ema, mock_macd, mock_rsi, mock_caps, mock_dl):
    df = multiindex(make_passing_df())
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 160.0}}

    mock_ema.side_effect = mock_ema_side_effect
    mock_rsi.return_value = pd.Series([60.0] * 300)
    mock_macd.return_value = mock_macd_df(hist=0.5)
    mock_bb.return_value = mock_bb_df(upper=170.0, middle=155.0, lower=140.0)
    mock_atr.return_value = pd.Series([3.0] * 300)

    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]

    assert len(results) == 1
    r = results[0]
    assert r["ticker"] == "AAPL"
    assert r["change"] > 3
    assert r["exchange"] == "NASDAQ"
    assert r["vol_ratio"] > 0
    assert "entry1" in r and "stop1" in r
    assert "entry2" in r and "stop2" in r
    assert "entry3" in r and "stop3" in r
