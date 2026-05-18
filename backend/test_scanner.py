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
    _filter_ticker,
    get_active_filters,
    get_full_market_tickers,
    screen_stocks,
    CONFIG,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def run_screen(tickers):
    async def _collect():
        return [item async for item in screen_stocks(tickers)]
    return asyncio.run(_collect())


def make_passing_df(days=300, final_price=160.0, prev_price=152.0, volume=600_000):
    """DataFrame where the last two closes give >3% change and natural indicators pass.

    The last bar gets `volume` shares; all prior bars get volume // 4.
    This means the 20-day average ≈ volume // 4, so RVOL ≈ 4× ≥ 2.0.
    """
    dates = pd.date_range(end="2024-01-01", periods=days)
    prices = np.linspace(100.0, 155.0, days).copy()
    prices[-1] = final_price
    prices[-2] = prev_price
    vols = np.full(days, volume // 4)
    vols[-1] = volume  # last bar = 4× background volume → RVOL ≥ 2
    return pd.DataFrame({
        "Open": prices * 0.99,
        "High": prices * 1.01,
        "Low": prices * 0.98,
        "Close": prices,
        "Volume": vols,
    }, index=dates)


def multiindex(df, ticker="AAPL"):
    df.columns = pd.MultiIndex.from_product([[ticker], df.columns])
    return df


def mock_ema_side_effect(series, length=None, **kw):
    # EMA20 > EMA50 > EMA200 satisfies MA stacking; each series gently rises by 0.01
    # per bar so the slope check (iloc[-1] > iloc[-6]) always passes.
    # EMA8 base is 143 so that make_passing_df's last 3 closes (≈154.8, 152, 160)
    # all clear EMA8 (≈145.97–145.99), satisfying the TREND_CONFIRM_DAYS=3 filter.
    base = {8: 143.0, 20: 148.0, 50: 130.0, 200: 110.0}.get(length, 140.0)
    n = len(series)
    return pd.Series([base + i * 0.01 for i in range(n)], index=series.index)


def mock_macd_df(days=300, hist=0.5):
    return pd.DataFrame({
        "MACD_12_26_9": [1.0] * days,
        "MACDs_12_26_9": [0.5] * days,
        "MACDh_12_26_9": [hist] * days,
    })


def mock_bb_df(days=300, upper=170.0, middle=155.0, lower=140.0, upper_prev=None, lower_prev=None):
    """Return a BB DataFrame.  upper/lower are the LAST row values.
    upper_prev/lower_prev are the second-to-last row values (for divergence tests).
    When omitted, the previous row is slightly narrower so divergence passes."""
    if upper_prev is None:
        upper_prev = upper - 1.0   # bands were narrower → now widening ✓
    if lower_prev is None:
        lower_prev = lower + 1.0
    uppers  = [upper_prev] * (days - 1) + [upper]
    middles = [middle]     * days
    lowers  = [lower_prev] * (days - 1) + [lower]
    return pd.DataFrame({
        "BBL_20_2.0": lowers,
        "BBM_20_2.0": middles,
        "BBU_20_2.0": uppers,
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

def test_get_active_filters_returns_15():
    filters = get_active_filters()
    assert len(filters) == 15


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
    mock_rsi.return_value = pd.Series([45.0] * 300)  # RSI < 55
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
    mock_bb.return_value = mock_bb_df(upper=170.0)  # price 160 < BB upper 170 → fails breakout
    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
@patch("backend.scanner.ta.bbands")
def test_screen_stocks_bb_none_uses_fallback(mock_bb, mock_caps, mock_dl):
    """When bbands returns None, fallback BB upper = price*1.03 > price → fails breakout filter."""
    df = multiindex(make_passing_df())
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 160.0}}
    mock_bb.return_value = None

    with patch("backend.scanner.ta.ema", side_effect=mock_ema_side_effect):
        with patch("backend.scanner.ta.rsi", return_value=pd.Series([60.0] * 300)):
            with patch("backend.scanner.ta.macd", return_value=mock_macd_df(hist=0.5)):
                with patch("backend.scanner.ta.atr", return_value=pd.Series([3.0] * 300)):
                    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]

    # Fallback BB upper ≈ price * 1.03 = 164.8 > price 160 → price not above upper → no result
    assert results == []


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
    # upper=157 < price=160 → breakout passes; divergence: prev=(156,148)→now=(157,147) widens ✓
    mock_bb.return_value = mock_bb_df(upper=157.0, middle=152.0, lower=147.0)
    mock_atr.return_value = pd.Series([3.0] * 300)

    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]

    assert len(results) == 1
    r = results[0]
    assert r["ticker"] == "AAPL"
    assert r["change"] > 3
    assert r["exchange"] == "NASDAQ"
    assert r["vol_ratio"] >= CONFIG["MIN_RVOL"]
    assert "ema20" in r
    assert "entry1" in r and "stop1" in r
    assert "entry2" in r and "stop2" in r
    assert "entry3" in r and "stop3" in r


# ── _filter_ticker — new filter tests ────────────────────────────────────────

def _make_mc(price=160.0, last_volume=None):
    return {'AAPL': {'market_cap': 3e12, 'exchange': 'NASDAQ', 'last_price': price, 'last_volume': last_volume}}


def _patches():
    """Return a list of patch decorators for all TA mocks needed to reach late filters."""
    return [
        patch("backend.scanner.ta.rsi", return_value=pd.Series([60.0] * 300)),
        patch("backend.scanner.ta.macd"),
        patch("backend.scanner.ta.ema"),
        patch("backend.scanner.ta.bbands"),
        patch("backend.scanner.ta.atr", return_value=pd.Series([3.0] * 300)),
    ]


@patch("backend.scanner.ta.rsi", return_value=pd.Series([60.0] * 300))
@patch("backend.scanner.ta.macd")
@patch("backend.scanner.ta.ema")
@patch("backend.scanner.ta.bbands")
@patch("backend.scanner.ta.atr", return_value=pd.Series([3.0] * 300))
def test_filter_ticker_uses_fast_info_volume(mock_atr, mock_bb, mock_ema, mock_macd, mock_rsi):
    """fast_info last_volume overrides daily volume for the volume filter."""
    mock_ema.side_effect = mock_ema_side_effect
    mock_macd.return_value = mock_macd_df(hist=0.5)
    mock_bb.return_value = mock_bb_df(upper=157.0, middle=152.0, lower=147.0)
    df = make_passing_df(volume=600_000)
    result = _filter_ticker('AAPL', df, _make_mc(last_volume=800_000))
    assert result is not None
    assert result['volume'] == 800_000


@patch("backend.scanner.ta.rsi", return_value=pd.Series([60.0] * 300))
@patch("backend.scanner.ta.macd")
@patch("backend.scanner.ta.ema")
@patch("backend.scanner.ta.bbands")
@patch("backend.scanner.ta.atr", return_value=pd.Series([3.0] * 300))
def test_filter_ticker_rvol_filter(mock_atr, mock_bb, mock_ema, mock_macd, mock_rsi):
    """Ticker is rejected when RVOL < MIN_RVOL (currently 2.5)."""
    mock_ema.side_effect = mock_ema_side_effect
    mock_macd.return_value = mock_macd_df(hist=0.5)
    mock_bb.return_value = mock_bb_df(upper=157.0, middle=152.0, lower=147.0)
    # Uniform volume → vol_ratio = 1.0 < 2.0
    df = make_passing_df(volume=600_000)
    df['Volume'] = 600_000  # override last bar so avg == last bar → RVOL = 1.0
    result = _filter_ticker('AAPL', df, _make_mc())
    assert result is None


@patch("backend.scanner.ta.rsi", return_value=pd.Series([60.0] * 300))
@patch("backend.scanner.ta.macd")
@patch("backend.scanner.ta.ema")
@patch("backend.scanner.ta.bbands")
@patch("backend.scanner.ta.atr", return_value=pd.Series([3.0] * 300))
def test_filter_ticker_atr_candle_filter(mock_atr, mock_bb, mock_ema, mock_macd, mock_rsi):
    """Ticker is rejected when breakout candle is smaller than 1.5× ATR14."""
    mock_ema.side_effect = mock_ema_side_effect
    mock_macd.return_value = mock_macd_df(hist=0.5)
    mock_bb.return_value = mock_bb_df(upper=157.0, middle=152.0, lower=147.0)
    # ATR=3, threshold=4.5; make candle tiny: High=160.5, Low=159.5 → range=1.0 < 4.5
    df = make_passing_df()
    df['High'] = df['Close'] * 1.001
    df['Low']  = df['Close'] * 0.999
    result = _filter_ticker('AAPL', df, _make_mc())
    assert result is None


@patch("backend.scanner.ta.rsi", return_value=pd.Series([60.0] * 300))
@patch("backend.scanner.ta.macd")
@patch("backend.scanner.ta.ema")
@patch("backend.scanner.ta.bbands")
@patch("backend.scanner.ta.atr", return_value=pd.Series([3.0] * 300))
def test_filter_ticker_ma_stacking_filter(mock_atr, mock_bb, mock_ema, mock_macd, mock_rsi):
    """Ticker is rejected when EMAs are not stacked EMA20 > EMA50 > EMA200."""
    mock_macd.return_value = mock_macd_df(hist=0.5)
    mock_bb.return_value = mock_bb_df(upper=157.0, middle=152.0, lower=147.0)
    def bad_stack(series, length=None, **kw):
        # EMA8=143 keeps last-3-close trend check passing; EMA20 < EMA50 breaks the stack
        val = {8: 143.0, 20: 120.0, 50: 130.0, 200: 110.0}.get(length, 140.0)
        return pd.Series([val + i * 0.01 for i in range(len(series))], index=series.index)
    mock_ema.side_effect = bad_stack
    result = _filter_ticker('AAPL', make_passing_df(), _make_mc())
    assert result is None


@patch("backend.scanner.ta.rsi", return_value=pd.Series([60.0] * 300))
@patch("backend.scanner.ta.macd")
@patch("backend.scanner.ta.ema")
@patch("backend.scanner.ta.bbands")
@patch("backend.scanner.ta.atr", return_value=pd.Series([3.0] * 300))
def test_filter_ticker_bb_breakout_filter(mock_atr, mock_bb, mock_ema, mock_macd, mock_rsi):
    """Ticker is rejected when price is at or below BB upper (no breakout)."""
    mock_ema.side_effect = mock_ema_side_effect
    mock_macd.return_value = mock_macd_df(hist=0.5)
    mock_bb.return_value = mock_bb_df(upper=170.0)  # price 160 < upper 170 → no breakout
    result = _filter_ticker('AAPL', make_passing_df(), _make_mc())
    assert result is None


@patch("backend.scanner.ta.rsi", return_value=pd.Series([60.0] * 300))
@patch("backend.scanner.ta.macd")
@patch("backend.scanner.ta.ema")
@patch("backend.scanner.ta.bbands")
@patch("backend.scanner.ta.atr", return_value=pd.Series([3.0] * 300))
def test_filter_ticker_bb_divergence_filter(mock_atr, mock_bb, mock_ema, mock_macd, mock_rsi):
    """Ticker is rejected when BB bands are not widening."""
    mock_ema.side_effect = mock_ema_side_effect
    mock_macd.return_value = mock_macd_df(hist=0.5)
    # price (160) > upper (157), but bands narrowing: prev width=12, now width=8
    mock_bb.return_value = mock_bb_df(
        upper=157.0, lower=149.0,          # width now  = 8
        upper_prev=158.0, lower_prev=146.0  # width prev = 12 → narrowing → fail
    )
    result = _filter_ticker('AAPL', make_passing_df(), _make_mc())
    assert result is None


@patch("backend.scanner.ta.rsi", return_value=pd.Series([60.0] * 300))
@patch("backend.scanner.ta.macd")
@patch("backend.scanner.ta.ema")
@patch("backend.scanner.ta.bbands")
@patch("backend.scanner.ta.atr", return_value=pd.Series([1.0] * 300))
def test_filter_ticker_close_position_filter(mock_atr, mock_bb, mock_ema, mock_macd, mock_rsi):
    """Ticker is rejected when close is in the bottom 35% of the day's range."""
    mock_ema.side_effect = mock_ema_side_effect
    mock_macd.return_value = mock_macd_df(hist=0.5)
    mock_bb.return_value = mock_bb_df(upper=157.0, middle=152.0, lower=147.0)
    df = make_passing_df()
    # High is 10% above close, Low is 1% below — close sits near the bottom of the range
    # position = (close - low) / (high - low) = 0.01 / 0.11 ≈ 0.09 < 0.65 → FAIL
    df['High'] = df['Close'] * 1.10
    df['Low']  = df['Close'] * 0.99
    result = _filter_ticker('AAPL', df, _make_mc())
    assert result is None


@patch("backend.scanner.ta.rsi", return_value=pd.Series([60.0] * 300))
@patch("backend.scanner.ta.macd")
@patch("backend.scanner.ta.ema")
@patch("backend.scanner.ta.bbands")
@patch("backend.scanner.ta.atr", return_value=pd.Series([3.0] * 300))
def test_filter_ticker_multiday_ema8_filter(mock_atr, mock_bb, mock_ema, mock_macd, mock_rsi):
    """Ticker is rejected when not all of last 3 closes are above EMA8."""
    mock_macd.return_value = mock_macd_df(hist=0.5)
    mock_bb.return_value = mock_bb_df(upper=157.0, middle=152.0, lower=147.0)
    # EMA8 = 200 for all bars → every Close (≤ 160) < 200 → multi-day check fails
    mock_ema.side_effect = lambda s, length=None, **kw: pd.Series([200.0] * len(s), index=s.index)
    result = _filter_ticker('AAPL', make_passing_df(), _make_mc())
    assert result is None
