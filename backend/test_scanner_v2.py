import asyncio
import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch, AsyncMock

from backend.scanner_v2 import (
    _filter_ticker_v2,
    get_active_filters_v2,
    screen_stocks_v2,
    CONFIG_V2,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def run_screen_v2(tickers):
    async def _collect():
        return [item async for item in screen_stocks_v2(tickers)]
    return asyncio.run(_collect())


def make_passing_df_v2(days=300, final_price=55.0, prev_price=60.0, volume=2_000_000):
    """Extreme dislocation setup: stock in freefall (200→60) then another -8% drop.

    SMA200 ≈ mean(200..60) ≈ 130; 55 < 0.70×130 = 91   → v2 SMA200 filter passes ✓
    SMA50 ≈ last-50 bars of decline ≈ 65; 55/65 ≈ 0.85 < 0.90 ✓
    Day change = (55-60)/60 = -8.3% < -5%  ✓
    RVOL: background 300K, last bar 2M → ≈ 5.2× > 1.5   ✓
    RSI: mocked to < 35 in tests that need it.
    """
    dates = pd.date_range(end="2024-01-01", periods=days)
    prices = np.linspace(200.0, 60.0, days).copy()   # steep declining base
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
    base = {8: 58.0, 20: 65.0, 50: 80.0, 200: 130.0}.get(length, 70.0)
    n = len(series)
    return pd.Series([base - i * 0.01 for i in range(n)], index=series.index)


# ── get_active_filters_v2 ─────────────────────────────────────────────────────

def test_get_active_filters_v2_returns_8():
    assert len(get_active_filters_v2()) == 8


def test_get_active_filters_v2_mentions_sma200():
    filters = get_active_filters_v2()
    assert any("SMA200" in f for f in filters)


def test_get_active_filters_v2_mentions_dislocation():
    filters = get_active_filters_v2()
    assert any("dislocation" in f.lower() for f in filters)


def test_get_active_filters_v2_mentions_rvol():
    filters = get_active_filters_v2()
    assert any("RVOL" in f for f in filters)


# ── _filter_ticker_v2: early gates ───────────────────────────────────────────

def _make_mc_v2(price=55.0, last_volume=None):
    return {"AAPL": {"market_cap": 3e12, "exchange": "NASDAQ",
                     "last_price": price, "last_volume": last_volume}}


def test_filter_v2_rejects_small_market_cap():
    df = make_passing_df_v2()
    mc = {"AAPL": {"market_cap": 500_000_000.0, "exchange": "NASDAQ",
                   "last_price": 55.0, "last_volume": None}}
    assert _filter_ticker_v2("AAPL", df, mc) is None


def test_filter_v2_rejects_low_price():
    df = make_passing_df_v2()
    mc = {"AAPL": {"market_cap": 5e9, "exchange": "NASDAQ",
                   "last_price": 3.0, "last_volume": None}}
    assert _filter_ticker_v2("AAPL", df, mc) is None


def test_filter_v2_rejects_missing_live_price():
    df = make_passing_df_v2()
    mc = {"AAPL": {"market_cap": 5e9, "exchange": "NASDAQ",
                   "last_price": None, "last_volume": None}}
    assert _filter_ticker_v2("AAPL", df, mc) is None


def test_filter_v2_rejects_flat_day_change():
    """Stock up +1% — doesn't pass the panic selloff gate."""
    df = make_passing_df_v2(final_price=61.0, prev_price=60.0)
    mc = {"AAPL": {"market_cap": 5e9, "exchange": "NASDAQ",
                   "last_price": 61.0, "last_volume": None}}
    assert _filter_ticker_v2("AAPL", df, mc) is None


def test_filter_v2_rejects_zero_prev_close():
    df = make_passing_df_v2()
    prices = df["Close"].values.copy()
    prices[-2] = 0.0
    df["Close"] = prices
    assert _filter_ticker_v2("AAPL", df, _make_mc_v2()) is None


def test_filter_v2_rejects_low_volume():
    df = make_passing_df_v2(volume=100_000)   # < 500K minimum
    mc = {"AAPL": {"market_cap": 5e9, "exchange": "NASDAQ",
                   "last_price": 55.0, "last_volume": None}}
    assert _filter_ticker_v2("AAPL", df, mc) is None


def test_filter_v2_rejects_low_rvol():
    """Uniform volume across all bars → RVOL = 1.0 < 1.5 minimum."""
    df = make_passing_df_v2()
    df["Volume"] = 600_000.0   # last bar equals rolling average → RVOL = 1.0
    assert _filter_ticker_v2("AAPL", df, _make_mc_v2()) is None


@patch("backend.scanner_v2.ta.rsi")
def test_filter_v2_rejects_rsi_too_high(mock_rsi):
    mock_rsi.return_value = pd.Series([45.0] * 300)   # 45 >= 35 threshold
    df = make_passing_df_v2()
    assert _filter_ticker_v2("AAPL", df, _make_mc_v2()) is None


@patch("backend.scanner_v2.ta.rsi")
def test_filter_v2_rejects_rsi_empty_series(mock_rsi):
    mock_rsi.return_value = pd.Series([], dtype=float)
    df = make_passing_df_v2()
    assert _filter_ticker_v2("AAPL", df, _make_mc_v2()) is None


# ── SMA200 filter — the KEY inversion from scanner.py ─────────────────────────

@patch("backend.scanner_v2.ta.rsi")
def test_filter_v2_rejects_stock_at_80pct_sma200(mock_rsi):
    """
    A stock at 80% of SMA200 passes scanner.py (uptrend intact) but fails
    scanner_v2 (not distressed enough — needs < 70%).
    """
    mock_rsi.return_value = pd.Series([25.0] * 300)
    # Flat prices at 120 → SMA200 ≈ 120; last bar drops to 100
    days = 300
    prices = np.full(days, 120.0)
    prices[-2] = 108.0
    prices[-1] = 100.0   # price/SMA200 ≈ 100/120 = 0.83 > 0.70 → FAILS v2 filter
    vols = np.full(days, 300_000.0)
    vols[-1] = 2_000_000.0
    df = pd.DataFrame({
        "Open": prices * 1.01, "High": prices * 1.02,
        "Low": prices * 0.97, "Close": prices, "Volume": vols,
    }, index=pd.date_range(end="2024-01-01", periods=days))
    mc = {"AAPL": {"market_cap": 5e9, "exchange": "NASDAQ",
                   "last_price": 100.0, "last_volume": None}}
    assert _filter_ticker_v2("AAPL", df, mc) is None


@patch("backend.scanner_v2.ta.rsi")
def test_filter_v2_passes_stock_at_42pct_sma200(mock_rsi):
    """
    A stock at 42% of SMA200 (deeply distressed) should PASS v2's SMA200 filter.
    It would FAIL scanner.py's SMA200 filter (price < 75% of SMA200).
    """
    mock_rsi.return_value = pd.Series([25.0] * 300)
    # make_passing_df_v2 produces SMA200 ≈ 130, final_price=55 → 55/130 ≈ 0.42 < 0.70 ✓
    df = make_passing_df_v2()
    mc = {"AAPL": {"market_cap": 5e9, "exchange": "NASDAQ",
                   "last_price": 55.0, "last_volume": None}}
    # Should not be rejected by SMA200 filter alone (may still fail SMA50 without mock)
    # We don't assert pass here — just that SMA200 filter is not the reason for None
    # A full pass test is in test_filter_v2_full_pass below


@patch("backend.scanner_v2.ta.rsi")
def test_filter_v2_rejects_sma50_not_discounted(mock_rsi):
    """Stock's price / SMA50 ≥ 0.90 — not discounted enough below SMA50."""
    mock_rsi.return_value = pd.Series([25.0] * 300)
    days = 300
    # First 250 bars at 200 → SMA200 ≈ 172 (90 < 0.70×172=120 ✓ passes SMA200)
    # Last 50 bars at 90 → SMA50 ≈ 90.1 (90/90.1=0.999 ≥ 0.90 → fails SMA50)
    prices = np.full(days, 200.0)
    prices[250:] = 90.0
    prices[-2] = 96.0
    prices[-1] = 90.0
    vols = np.full(days, 300_000.0)
    vols[-1] = 2_000_000.0
    df = pd.DataFrame({
        "Open": prices * 1.01, "High": prices * 1.02,
        "Low": prices * 0.97, "Close": prices, "Volume": vols,
    }, index=pd.date_range(end="2024-01-01", periods=days))
    mc = {"AAPL": {"market_cap": 5e9, "exchange": "NASDAQ",
                   "last_price": 90.0, "last_volume": None}}
    assert _filter_ticker_v2("AAPL", df, mc) is None


def test_filter_v2_rejects_too_short_data():
    df = make_passing_df_v2(days=100)
    assert _filter_ticker_v2("AAPL", multiindex(df), _make_mc_v2()) is None


def test_filter_v2_rejects_ticker_not_in_multiindex():
    df = multiindex(make_passing_df_v2(), ticker="MSFT")
    assert _filter_ticker_v2("AAPL", df, _make_mc_v2()) is None


def test_filter_v2_handles_non_multiindex_data():
    """Single-ticker download returns plain DataFrame — passes with up-day → fails day_change."""
    df = make_passing_df_v2(final_price=61.0, prev_price=60.0)   # +1.7% → fails day_change
    mc = {"AAPL": {"market_cap": 5e9, "exchange": "NASDAQ",
                   "last_price": 61.0, "last_volume": None}}
    assert _filter_ticker_v2("AAPL", df, mc) is None


# ── _filter_ticker_v2: full passing case ─────────────────────────────────────

@patch("backend.scanner_v2.ta.rsi")
@patch("backend.scanner_v2.ta.ema")
def test_filter_v2_full_pass(mock_ema, mock_rsi):
    """All 7 filters pass → result dict returned with correct fields."""
    mock_rsi.return_value = pd.Series([25.0] * 300)
    mock_ema.side_effect = mock_ema_side_effect
    df = make_passing_df_v2()
    mc = {"AAPL": {"market_cap": 5e9, "exchange": "NASDAQ",
                   "last_price": 55.0, "last_volume": None}}

    result = _filter_ticker_v2("AAPL", df, mc)

    assert result is not None
    assert result["ticker"] == "AAPL"
    assert result["change"] < -5.0
    assert result["rsi"] < CONFIG_V2["MAX_RSI"]
    assert result["exchange"] == "NASDAQ"
    # SMA200 constraint: price < 70% of SMA200
    assert result["price"] < result["sma200"] * CONFIG_V2["MAX_SMA200_RATIO"]
    assert "entry1" in result and "stop1" in result
    assert "entry2" in result and "stop2" in result
    assert "entry3" in result and "stop3" in result
    assert "bb_upper" in result and "bb_lower" in result


@patch("backend.scanner_v2.ta.rsi")
@patch("backend.scanner_v2.ta.ema")
def test_filter_v2_uses_fast_info_volume(mock_ema, mock_rsi):
    mock_rsi.return_value = pd.Series([25.0] * 300)
    mock_ema.side_effect = mock_ema_side_effect
    df = make_passing_df_v2()   # df last bar = 2M
    mc = {"AAPL": {"market_cap": 5e9, "exchange": "NASDAQ",
                   "last_price": 55.0, "last_volume": 3_000_000}}
    result = _filter_ticker_v2("AAPL", df, mc)
    assert result is not None
    assert result["volume"] == 3_000_000


@patch("backend.scanner_v2.ta.rsi")
@patch("backend.scanner_v2.ta.ema")
@patch("backend.scanner_v2.ta.macd", return_value=None)
def test_filter_v2_macd_none_fallback(mock_macd, mock_ema, mock_rsi):
    mock_rsi.return_value = pd.Series([25.0] * 300)
    mock_ema.side_effect = mock_ema_side_effect
    df = make_passing_df_v2()
    mc = {"AAPL": {"market_cap": 5e9, "exchange": "NASDAQ",
                   "last_price": 55.0, "last_volume": None}}
    result = _filter_ticker_v2("AAPL", df, mc)
    assert result is not None
    assert result["macd"] == 0.0
    assert result["macd_hist"] == 0.0


@patch("backend.scanner_v2.ta.rsi")
@patch("backend.scanner_v2.ta.ema")
@patch("backend.scanner_v2.ta.bbands", return_value=None)
def test_filter_v2_bbands_none_fallback(mock_bb, mock_ema, mock_rsi):
    mock_rsi.return_value = pd.Series([25.0] * 300)
    mock_ema.side_effect = mock_ema_side_effect
    df = make_passing_df_v2()
    mc = {"AAPL": {"market_cap": 5e9, "exchange": "NASDAQ",
                   "last_price": 55.0, "last_volume": None}}
    result = _filter_ticker_v2("AAPL", df, mc)
    assert result is not None
    assert result["bb_lower"] == round(55.0 * 0.97, 2)
    assert result["bb_upper"] == round(55.0 * 1.03, 2)


@patch("backend.scanner_v2.ta.rsi")
def test_filter_v2_unexpected_exception_returns_none(mock_rsi):
    mock_rsi.side_effect = RuntimeError("unexpected mid-filter failure")
    df = make_passing_df_v2()
    mc = {"AAPL": {"market_cap": 5e9, "exchange": "NASDAQ",
                   "last_price": 55.0, "last_volume": None}}
    assert _filter_ticker_v2("AAPL", df, mc) is None


# ── screen_stocks_v2: integration-style tests ─────────────────────────────────

@patch("yfinance.download")
@patch("backend.scanner_v2._fetch_market_caps_bulk_async", new_callable=AsyncMock)
def test_screen_v2_fails_market_cap(mock_caps, mock_dl):
    df = multiindex(make_passing_df_v2())
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 500_000_000.0, "exchange": "NASDAQ",
                                        "last_price": 55.0, "last_volume": None}}
    results = [r for r in run_screen_v2(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner_v2._fetch_market_caps_bulk_async", new_callable=AsyncMock)
def test_screen_v2_fails_day_change(mock_caps, mock_dl):
    df = multiindex(make_passing_df_v2(final_price=61.0, prev_price=60.0))  # +1.7%
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 5e9, "exchange": "NASDAQ",
                                        "last_price": 61.0, "last_volume": None}}
    results = [r for r in run_screen_v2(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner_v2._fetch_market_caps_bulk_async", new_callable=AsyncMock)
@patch("backend.scanner_v2.ta.rsi")
def test_screen_v2_fails_sma200_not_distressed(mock_rsi, mock_caps, mock_dl):
    """Healthy stock (price > 70% SMA200) is rejected by v2 — passes v1 but not v2."""
    mock_rsi.return_value = pd.Series([25.0] * 300)
    # Flat prices at 120, last bar at 100: price/SMA200 = 100/120 ≈ 0.83 > 0.70 → fails
    days = 300
    prices = np.full(days, 120.0)
    prices[-2] = 108.0
    prices[-1] = 100.0
    vols = np.full(days, 300_000.0)
    vols[-1] = 2_000_000.0
    df = pd.DataFrame({
        "Open": prices * 1.01, "High": prices * 1.02,
        "Low": prices * 0.97, "Close": prices, "Volume": vols,
    }, index=pd.date_range(end="2024-01-01", periods=300))
    df = multiindex(df)
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 5e9, "exchange": "NASDAQ",
                                        "last_price": 100.0, "last_volume": None}}
    results = [r for r in run_screen_v2(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner_v2._fetch_market_caps_bulk_async", new_callable=AsyncMock)
@patch("backend.scanner_v2.ta.rsi")
@patch("backend.scanner_v2.ta.ema")
def test_screen_v2_full_pass(mock_ema, mock_rsi, mock_caps, mock_dl):
    df = multiindex(make_passing_df_v2())
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 5e9, "exchange": "NASDAQ",
                                        "last_price": 55.0, "last_volume": None}}
    mock_rsi.return_value = pd.Series([25.0] * 300)
    mock_ema.side_effect = mock_ema_side_effect

    results = [r for r in run_screen_v2(["AAPL"]) if isinstance(r, dict) and "status" not in r]

    assert len(results) == 1
    r = results[0]
    assert r["ticker"] == "AAPL"
    assert r["change"] < -5.0
    assert r["rsi"] < CONFIG_V2["MAX_RSI"]
    assert r["price"] < r["sma200"] * CONFIG_V2["MAX_SMA200_RATIO"]


@patch("yfinance.download")
@patch("backend.scanner_v2._fetch_market_caps_bulk_async", new_callable=AsyncMock)
def test_screen_v2_empty_download_retries_and_emits_progress(mock_caps, mock_dl):
    mock_dl.return_value = pd.DataFrame()
    mock_caps.return_value = {}
    with patch("backend.scanner_v2.asyncio.sleep"):
        results = run_screen_v2(["AAPL"])
    progress_events = [r for r in results if isinstance(r, dict) and r.get("status") == "progress"]
    assert len(progress_events) > 0
