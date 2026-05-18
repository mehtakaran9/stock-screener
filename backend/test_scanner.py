import asyncio
import pytest
import pandas as pd
import numpy as np
import pandas_ta_classic as ta
from unittest.mock import patch, MagicMock, AsyncMock

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


def make_passing_df(days=300, final_price=130.0, prev_price=145.0, volume=2_000_000):
    """Recovery setup: strong uptrend (100→200) then panic crash.

    Background vol = 300K, last bar = volume (default 2M).
    RVOL ≈ (19×300K + 2M)/20 = 385K → 2M/385K ≈ 5.2× > 3.5 ✓
    Day change = (130 − 145)/145 = −10.3% < −5% ✓
    SMA200 ≈ mean(100..200) ≈ 166; 130 > 0.75×166 = 124 ✓
    SMA50 ≈ 189 (last 50 bars of 100→200 trend); 130/189 ≈ 0.69 < 0.90 ✓ (NEW)
    EMA20 > EMA50 > EMA200 holds on rising 100→200 base; mocked in full-pass tests.
    RSI must be mocked to < 30 for full-pass tests.
    """
    dates = pd.date_range(end="2024-01-01", periods=days)
    prices = np.linspace(100.0, 200.0, days).copy()
    prices[-1] = final_price
    prices[-2] = prev_price
    BACKGROUND_VOL = 300_000
    vols = np.full(days, BACKGROUND_VOL, dtype=float)
    vols[-1] = float(volume)
    return pd.DataFrame({
        "Open":   prices * 1.01,
        "High":   prices * 1.02,
        "Low":    prices * 0.97,
        "Close":  prices,
        "Volume": vols,
    }, index=dates)


def multiindex(df, ticker="AAPL"):
    df.columns = pd.MultiIndex.from_product([[ticker], df.columns])
    return df


def mock_ema_side_effect(series, length=None, **kw):
    # EMA20 > EMA50 > EMA200 satisfies MA stacking; gently rising for slope check
    base = {8: 155.0, 20: 148.0, 50: 130.0, 200: 110.0}.get(length, 140.0)
    n = len(series)
    return pd.Series([base + i * 0.01 for i in range(n)], index=series.index)


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

def test_get_active_filters_returns_10():
    filters = get_active_filters()
    assert len(filters) == 10


def test_get_active_filters_contains_day_change():
    filters = get_active_filters()
    assert any("Day change" in f for f in filters)


# ── screen_stocks: early filter failures ─────────────────────────────────────

@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
def test_screen_stocks_fails_market_cap(mock_caps, mock_dl):
    df = multiindex(make_passing_df())
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 500_000_000.0, "exchange": "NASDAQ", "last_price": 150.0}}
    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
def test_screen_stocks_fails_day_change(mock_caps, mock_dl):
    # +1% on the day — not a panic selloff (needs < −5%)
    df = multiindex(make_passing_df(final_price=101.0, prev_price=100.0))
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 101.0}}
    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
def test_screen_stocks_fails_volume(mock_caps, mock_dl):
    df = multiindex(make_passing_df(volume=100_000))  # 100K < 500K minimum
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 150.0}}
    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
def test_screen_stocks_fails_rvol(mock_caps, mock_dl):
    """Uniform volume across all bars → RVOL = 1.0 < 3.5× minimum."""
    df = make_passing_df()
    df["Volume"] = 600_000.0  # last bar same as average → RVOL = 1.0
    df = multiindex(df)
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 150.0}}
    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
@patch("backend.scanner.ta.rsi")
def test_screen_stocks_fails_rsi_not_oversold(mock_rsi, mock_caps, mock_dl):
    """RSI = 45 is not oversold enough — filter requires RSI < 30."""
    df = multiindex(make_passing_df())
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 150.0}}
    mock_rsi.return_value = pd.Series([45.0] * 300)  # RSI >= 30 → fails filter
    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
@patch("backend.scanner.ta.rsi")
def test_screen_stocks_fails_sma200(mock_rsi, mock_caps, mock_dl):
    """Stock in structural freefall: price < 75% of SMA200."""
    days = 300
    dates = pd.date_range(end="2024-01-01", periods=days)
    prices = np.full(days, 200.0)
    prices[-1] = 100.0   # −9.1% drop (passes day_change filter)
    prices[-2] = 110.0
    BACKGROUND_VOL = 300_000
    vols = np.full(days, BACKGROUND_VOL, dtype=float)
    vols[-1] = 3_000_000.0   # RVOL ≈ 5.5× > 3.5 ✓
    df = pd.DataFrame({
        "Open": prices * 1.01, "High": prices * 1.02,
        "Low": prices * 0.97, "Close": prices, "Volume": vols,
    }, index=dates)
    df = multiindex(df)
    mock_dl.return_value = df
    # price=100, SMA200≈200 → 100 < 0.75×200=150 → fails SMA200 ratio filter
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 100.0}}
    mock_rsi.return_value = pd.Series([22.0] * days)  # pass RSI filter to reach SMA200 check
    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
@patch("backend.scanner.ta.rsi")
@patch("backend.scanner.ta.ema")
def test_screen_stocks_fails_ema_stack(mock_ema, mock_rsi, mock_caps, mock_dl):
    """Broken EMA stack (EMA20 < EMA50) — no macro uptrend."""
    df = multiindex(make_passing_df())
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 150.0}}
    mock_rsi.return_value = pd.Series([22.0] * 300)

    def broken_stack(series, length=None, **kw):
        val = {8: 155.0, 20: 120.0, 50: 130.0, 200: 110.0}.get(length, 140.0)
        return pd.Series([val + i * 0.01 for i in range(len(series))], index=series.index)

    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 130.0}}
    mock_ema.side_effect = broken_stack
    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


# ── screen_stocks: data quality checks ───────────────────────────────────────

@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
def test_screen_stocks_skips_ticker_not_in_multiindex(mock_caps, mock_dl):
    df = multiindex(make_passing_df(), ticker="MSFT")  # only MSFT in data
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 150.0}}
    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
def test_screen_stocks_skips_too_short_data(mock_caps, mock_dl):
    df = make_passing_df(days=100)  # only 100 rows
    df = multiindex(df)
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 150.0}}
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
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 150.0}}
    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
def test_screen_stocks_non_multiindex_data(mock_caps, mock_dl):
    """Single-ticker download may return a plain (non-MultiIndex) DataFrame."""
    df = make_passing_df(final_price=101.0, prev_price=100.0)  # +1% → fails day_change filter
    mock_dl.return_value = df  # no MultiIndex
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 101.0}}
    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
def test_screen_stocks_empty_download_raises_and_retries(mock_caps, mock_dl):
    """All download attempts return empty data → chunk error path."""
    mock_dl.return_value = pd.DataFrame()
    mock_caps.return_value = {}
    with patch("backend.scanner.asyncio.sleep"):
        results = run_screen(["AAPL"])
    progress_events = [r for r in results if isinstance(r, dict) and r.get("status") == "progress"]
    assert len(progress_events) > 0


# ── screen_stocks: full passing run ──────────────────────────────────────────

@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
@patch("backend.scanner.ta.rsi")
@patch("backend.scanner.ta.ema")
def test_screen_stocks_success(mock_ema, mock_rsi, mock_caps, mock_dl):
    df = multiindex(make_passing_df())
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 130.0}}

    mock_rsi.return_value = pd.Series([22.0] * 300)  # extreme oversold < 30 ✓
    mock_ema.side_effect = mock_ema_side_effect       # EMA20 > EMA50 > EMA200 ✓

    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]

    assert len(results) == 1
    r = results[0]
    assert r["ticker"] == "AAPL"
    assert r["change"] < -5.0            # confirmed panic selloff
    assert r["rsi"] < CONFIG["MAX_RSI"]  # extreme oversold
    assert r["exchange"] == "NASDAQ"
    assert r["vol_ratio"] >= CONFIG["MIN_RVOL"]
    assert "ema20" in r
    assert "entry1" in r and "stop1" in r
    assert "entry2" in r and "stop2" in r
    assert "entry3" in r and "stop3" in r


# ── _filter_ticker — direct filter tests ─────────────────────────────────────

def _make_mc(price=130.0, last_volume=None):
    return {'AAPL': {'market_cap': 3e12, 'exchange': 'NASDAQ', 'last_price': price, 'last_volume': last_volume}}


@patch("backend.scanner.ta.rsi", return_value=pd.Series([22.0] * 300))
@patch("backend.scanner.ta.ema")
def test_filter_ticker_uses_fast_info_volume(mock_ema, mock_rsi):
    """fast_info last_volume overrides df volume in the result."""
    mock_ema.side_effect = mock_ema_side_effect
    df = make_passing_df()  # last bar = 2M in df
    result = _filter_ticker('AAPL', df, _make_mc(last_volume=3_000_000))  # fast_info = 3M
    assert result is not None
    assert result['volume'] == 3_000_000


@patch("backend.scanner.ta.rsi", return_value=pd.Series([22.0] * 300))
@patch("backend.scanner.ta.ema")
def test_filter_ticker_rvol_filter(mock_ema, mock_rsi):
    """Ticker is rejected when RVOL < 3.5× (uniform volume → RVOL = 1.0)."""
    mock_ema.side_effect = mock_ema_side_effect
    df = make_passing_df()
    df['Volume'] = 600_000.0  # uniform → rolling avg = last bar → RVOL = 1.0 < 3.5
    result = _filter_ticker('AAPL', df, _make_mc())
    assert result is None


@patch("backend.scanner.ta.rsi", return_value=pd.Series([22.0] * 300))
@patch("backend.scanner.ta.ema")
def test_filter_ticker_ma_stacking_filter(mock_ema, mock_rsi):
    """Ticker is rejected when EMAs are not stacked EMA20 > EMA50 > EMA200."""
    def bad_stack(series, length=None, **kw):
        val = {8: 155.0, 20: 120.0, 50: 130.0, 200: 110.0}.get(length, 140.0)
        return pd.Series([val + i * 0.01 for i in range(len(series))], index=series.index)
    mock_ema.side_effect = bad_stack
    result = _filter_ticker('AAPL', make_passing_df(), _make_mc())
    assert result is None


@patch("yfinance.download")
@patch("backend.scanner._fetch_market_caps_bulk_async", new_callable=AsyncMock)
@patch("backend.scanner.ta.rsi")
@patch("backend.scanner.ta.ema")
def test_screen_stocks_fails_sma50(mock_ema, mock_rsi, mock_caps, mock_dl):
    """Price too close to SMA50 (price/SMA50 ≥ 0.90) — not a deep enough discount."""
    days = 300
    dates = pd.date_range(end="2024-01-01", periods=days)
    # Flat prices at 150 so SMA50 ≈ 150; inject crash bar: prev=160, last=150
    prices = np.full(days, 150.0)
    prices[-2] = 160.0
    prices[-1] = 150.0  # not used by _filter_ticker — overridden by fast_info last_price
    BACKGROUND_VOL = 300_000
    vols = np.full(days, BACKGROUND_VOL, dtype=float)
    vols[-1] = 2_000_000.0  # RVOL ≈ 5.2× ✓
    df = pd.DataFrame({
        "Open": prices * 1.01, "High": prices * 1.02,
        "Low":  prices * 0.97, "Close": prices, "Volume": vols,
    }, index=dates)
    df = multiindex(df)
    mock_dl.return_value = df
    # last_price=150, prev=160 → day_change=−6.25% ✓; SMA50≈150 → 150/150=1.0 ≥ 0.90 → FAILS
    mock_caps.return_value = {"AAPL": {"market_cap": 2_000_000_000.0, "exchange": "NASDAQ", "last_price": 150.0}}
    mock_rsi.return_value = pd.Series([22.0] * days)
    mock_ema.side_effect = mock_ema_side_effect
    results = [r for r in run_screen(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []
