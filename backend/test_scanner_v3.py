import asyncio
import os
import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch, AsyncMock, MagicMock

from backend.scanner_v3 import (
    _filter_ticker_v3_technical,
    _fetch_alt_data_v3,
    get_active_filters_v3,
    screen_stocks_v3,
    CONFIG_V3,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def run_screen_v3(tickers):
    async def _collect():
        return [item async for item in screen_stocks_v3(tickers)]
    return asyncio.run(_collect())


def make_passing_df_v3(days=300, final_price=55.0, prev_price=60.0, volume=2_000_000):
    """Extreme dislocation setup (same as v2) with a big intraday candle.

    SMA200 ≈ 130; 55 / 130 ≈ 0.42 < 0.70   → v3 SMA200 gate passes ✓
    SMA50 ≈ 65; 55 / 65 ≈ 0.85 < 0.90        → v3 SMA50 gate passes ✓
    Day change = (55-60)/60 = -8.3% < -5%     ✓
    RVOL: background 300K, last bar 2M → ≈ 5.2× > 3.5   ✓
    Open[-1] = prev_price (60) → candle body = |55-60| = 5 (ATR mocked to 3 → ratio ≈ 1.67) ✓
    RSI: mocked to < 25 in tests that need it.
    """
    dates = pd.date_range(end="2024-01-01", periods=days)
    prices = np.linspace(200.0, 60.0, days).copy()
    prices[-1] = final_price
    prices[-2] = prev_price
    BACKGROUND_VOL = 300_000
    vols = np.full(days, BACKGROUND_VOL, dtype=float)
    vols[-1] = float(volume)
    opens = prices * 1.01
    opens[-1] = float(prev_price)   # opened at prev close, big intraday drop
    return pd.DataFrame({
        "Open":   opens,
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


def _make_mc_v3(price=55.0, last_volume=None):
    return {"AAPL": {"market_cap": 3e12, "exchange": "NASDAQ",
                     "last_price": price, "last_volume": last_volume}}


def _passing_alt_data(score=1):
    return {
        "insider_buys_30d":     1 if score >= 1 else 0,
        "earnings_beat_streak": 0,
        "options_call_anomaly": False,
        "conviction_score":     score,
        "skip_earnings":        False,
    }


# ── get_active_filters_v3 ─────────────────────────────────────────────────────

def test_get_active_filters_v3_returns_10():
    assert len(get_active_filters_v3()) == 10


def test_get_active_filters_v3_mentions_atr_candle():
    filters = get_active_filters_v3()
    assert any("ATR" in f for f in filters)


def test_get_active_filters_v3_mentions_conviction():
    filters = get_active_filters_v3()
    assert any("conviction" in f.lower() or "alt-data" in f.lower() for f in filters)


def test_get_active_filters_v3_mentions_rsi():
    filters = get_active_filters_v3()
    assert any("RSI" in f for f in filters)


def test_get_active_filters_v3_mentions_sma200():
    filters = get_active_filters_v3()
    assert any("SMA200" in f for f in filters)


# ── _filter_ticker_v3_technical: early gates ─────────────────────────────────

def test_filter_v3_rejects_small_market_cap():
    df = make_passing_df_v3()
    mc = {"AAPL": {"market_cap": 500_000_000.0, "exchange": "NASDAQ",
                   "last_price": 55.0, "last_volume": None}}
    assert _filter_ticker_v3_technical("AAPL", df, mc) is None


def test_filter_v3_rejects_low_price():
    df = make_passing_df_v3()
    mc = {"AAPL": {"market_cap": 5e9, "exchange": "NASDAQ",
                   "last_price": 3.0, "last_volume": None}}
    assert _filter_ticker_v3_technical("AAPL", df, mc) is None


def test_filter_v3_rejects_missing_live_price():
    df = make_passing_df_v3()
    mc = {"AAPL": {"market_cap": 5e9, "exchange": "NASDAQ",
                   "last_price": None, "last_volume": None}}
    assert _filter_ticker_v3_technical("AAPL", df, mc) is None


def test_filter_v3_rejects_flat_day_change():
    df = make_passing_df_v3(final_price=61.0, prev_price=60.0)
    mc = {"AAPL": {"market_cap": 5e9, "exchange": "NASDAQ",
                   "last_price": 61.0, "last_volume": None}}
    assert _filter_ticker_v3_technical("AAPL", df, mc) is None


def test_filter_v3_rejects_zero_prev_close():
    df = make_passing_df_v3()
    prices = df["Close"].values.copy()
    prices[-2] = 0.0
    df["Close"] = prices
    assert _filter_ticker_v3_technical("AAPL", df, _make_mc_v3()) is None


def test_filter_v3_rejects_low_volume():
    df = make_passing_df_v3(volume=100_000)
    mc = {"AAPL": {"market_cap": 5e9, "exchange": "NASDAQ",
                   "last_price": 55.0, "last_volume": None}}
    assert _filter_ticker_v3_technical("AAPL", df, mc) is None


def test_filter_v3_rejects_low_rvol():
    """Uniform volume → RVOL ≈ 1.0 < 3.5 minimum (tighter than v2's 1.5×)."""
    df = make_passing_df_v3()
    df["Volume"] = 600_000.0
    assert _filter_ticker_v3_technical("AAPL", df, _make_mc_v3()) is None


@patch("backend.scanner_v3.ta.rsi")
def test_filter_v3_rejects_rsi_above_25(mock_rsi):
    mock_rsi.return_value = pd.Series([30.0] * 300)   # 30 >= 25 — fails v3 (passes v2)
    df = make_passing_df_v3()
    assert _filter_ticker_v3_technical("AAPL", df, _make_mc_v3()) is None


@patch("backend.scanner_v3.ta.rsi")
def test_filter_v3_rejects_rsi_empty_series(mock_rsi):
    mock_rsi.return_value = pd.Series([], dtype=float)
    df = make_passing_df_v3()
    assert _filter_ticker_v3_technical("AAPL", df, _make_mc_v3()) is None


@patch("backend.scanner_v3.ta.rsi")
def test_filter_v3_rejects_stock_at_80pct_sma200(mock_rsi):
    """Healthy stock (price > 70% SMA200) fails v3 — same as v2 SMA200 gate."""
    mock_rsi.return_value = pd.Series([20.0] * 300)
    days = 300
    prices = np.full(days, 120.0)
    prices[-2] = 108.0
    prices[-1] = 100.0   # 100/120 ≈ 0.83 > 0.70 → fails
    vols = np.full(days, 300_000.0)
    vols[-1] = 2_000_000.0
    opens = prices.copy()
    opens[-1] = 108.0
    df = pd.DataFrame({
        "Open": opens, "High": prices * 1.02,
        "Low": prices * 0.97, "Close": prices, "Volume": vols,
    }, index=pd.date_range(end="2024-01-01", periods=days))
    mc = {"AAPL": {"market_cap": 5e9, "exchange": "NASDAQ",
                   "last_price": 100.0, "last_volume": None}}
    assert _filter_ticker_v3_technical("AAPL", df, mc) is None


@patch("backend.scanner_v3.ta.rsi")
def test_filter_v3_rejects_sma50_not_discounted(mock_rsi):
    mock_rsi.return_value = pd.Series([20.0] * 300)
    days = 300
    prices = np.full(days, 200.0)
    prices[250:] = 90.0
    prices[-2] = 96.0
    prices[-1] = 90.0
    vols = np.full(days, 300_000.0)
    vols[-1] = 2_000_000.0
    opens = prices.copy()
    opens[-1] = 96.0
    df = pd.DataFrame({
        "Open": opens, "High": prices * 1.02,
        "Low": prices * 0.97, "Close": prices, "Volume": vols,
    }, index=pd.date_range(end="2024-01-01", periods=days))
    mc = {"AAPL": {"market_cap": 5e9, "exchange": "NASDAQ",
                   "last_price": 90.0, "last_volume": None}}
    assert _filter_ticker_v3_technical("AAPL", df, mc) is None


@patch("backend.scanner_v3.ta.rsi")
@patch("backend.scanner_v3.ta.atr")
def test_filter_v3_rejects_small_atr_candle(mock_atr, mock_rsi):
    """Candle body < 1.5× ATR → fails ATR candle gate."""
    mock_rsi.return_value = pd.Series([20.0] * 300)
    mock_atr.return_value = pd.Series([10.0] * 300)   # ATR = 10
    df = make_passing_df_v3()
    # Open[-1] = 60.0, price = 55.0 → candle body = 5, ratio = 5/10 = 0.5 < 1.5
    assert _filter_ticker_v3_technical("AAPL", df, _make_mc_v3()) is None


def test_filter_v3_rejects_too_short_data():
    df = make_passing_df_v3(days=100)
    assert _filter_ticker_v3_technical("AAPL", multiindex(df), _make_mc_v3()) is None


def test_filter_v3_rejects_ticker_not_in_multiindex():
    df = multiindex(make_passing_df_v3(), ticker="MSFT")
    assert _filter_ticker_v3_technical("AAPL", df, _make_mc_v3()) is None


# ── _filter_ticker_v3_technical: full passing case ───────────────────────────

@patch("backend.scanner_v3.ta.rsi")
@patch("backend.scanner_v3.ta.ema")
@patch("backend.scanner_v3.ta.atr")
def test_filter_v3_technical_full_pass(mock_atr, mock_ema, mock_rsi):
    """All 8 technical filters pass → result dict returned."""
    mock_rsi.return_value = pd.Series([20.0] * 300)
    mock_ema.side_effect = mock_ema_side_effect
    mock_atr.return_value = pd.Series([3.0] * 300)   # ATR=3; candle=|55-60|=5 → ratio=1.67 ≥1.5
    df = make_passing_df_v3()
    mc = _make_mc_v3()

    result = _filter_ticker_v3_technical("AAPL", df, mc)

    assert result is not None
    assert result["ticker"] == "AAPL"
    assert result["change"] < -5.0
    assert result["rsi"] < CONFIG_V3["MAX_RSI"]
    assert result["price"] < result["sma200"] * CONFIG_V3["MAX_SMA200_RATIO"]
    assert "entry1" in result and "stop1" in result
    assert "bb_upper" in result and "bb_lower" in result
    # v3 technical result does NOT include conviction fields yet
    assert "conviction_score" not in result


@patch("backend.scanner_v3.ta.rsi")
@patch("backend.scanner_v3.ta.ema")
@patch("backend.scanner_v3.ta.atr")
@patch("backend.scanner_v3.ta.macd", return_value=None)
def test_filter_v3_macd_none_fallback(mock_macd, mock_atr, mock_ema, mock_rsi):
    mock_rsi.return_value = pd.Series([20.0] * 300)
    mock_ema.side_effect = mock_ema_side_effect
    mock_atr.return_value = pd.Series([3.0] * 300)
    df = make_passing_df_v3()
    result = _filter_ticker_v3_technical("AAPL", df, _make_mc_v3())
    assert result is not None
    assert result["macd"] == 0.0


@patch("backend.scanner_v3.ta.rsi")
def test_filter_v3_unexpected_exception_returns_none(mock_rsi):
    mock_rsi.side_effect = RuntimeError("unexpected mid-filter failure")
    df = make_passing_df_v3()
    assert _filter_ticker_v3_technical("AAPL", df, _make_mc_v3()) is None


# ── _fetch_alt_data_v3 ────────────────────────────────────────────────────────

@patch("yfinance.Ticker")
@patch("backend.alt_data.SecEdgarFetcher")
@patch("backend.alt_data.EarningsFetcher")
def test_alt_data_insider_buy_sets_score(mock_ef_cls, mock_sec_cls, mock_ticker):
    """One insider buy in last 30d → conviction_score = 1."""
    mock_ticker.return_value.calendar = None

    mock_sec = MagicMock()
    insider_df = pd.DataFrame({"date": [pd.Timestamp.now() - pd.Timedelta(days=5)]})
    mock_sec.get.return_value = insider_df
    mock_sec_cls.return_value = mock_sec

    mock_ef = MagicMock()
    mock_ef.get.return_value = pd.DataFrame()
    mock_ef_cls.return_value = mock_ef

    result = _fetch_alt_data_v3("AAPL")

    assert result["insider_buys_30d"] >= 1
    assert result["conviction_score"] >= 1
    assert not result["skip_earnings"]


@patch("yfinance.Ticker")
@patch("backend.alt_data.SecEdgarFetcher")
@patch("backend.alt_data.EarningsFetcher")
def test_alt_data_earnings_beat_streak_sets_score(mock_ef_cls, mock_sec_cls, mock_ticker):
    """Earnings beat streak >= 2 → conviction_score = 1."""
    mock_ticker.return_value.calendar = None

    mock_sec = MagicMock()
    mock_sec.get.return_value = pd.DataFrame()
    mock_sec_cls.return_value = mock_sec

    mock_ef = MagicMock()
    earnings_df = pd.DataFrame({
        "date": pd.date_range(end="2024-01-01", periods=4),
        "beat_streak": [1, 2, 3, 3],
    })
    mock_ef.get.return_value = earnings_df
    mock_ef_cls.return_value = mock_ef

    result = _fetch_alt_data_v3("AAPL")

    assert result["earnings_beat_streak"] >= 2
    assert result["conviction_score"] >= 1


@patch("yfinance.Ticker")
@patch("backend.alt_data.SecEdgarFetcher")
@patch("backend.alt_data.EarningsFetcher")
def test_alt_data_zero_score_when_no_signals(mock_ef_cls, mock_sec_cls, mock_ticker):
    """No signals → conviction_score = 0."""
    mock_ticker.return_value.calendar = None

    mock_sec = MagicMock()
    old_df = pd.DataFrame({"date": [pd.Timestamp.now() - pd.Timedelta(days=60)]})
    mock_sec.get.return_value = old_df
    mock_sec_cls.return_value = mock_sec

    mock_ef = MagicMock()
    earnings_df = pd.DataFrame({
        "date": pd.date_range(end="2024-01-01", periods=2),
        "beat_streak": [0, 1],   # streak = 1 < 2 threshold
    })
    mock_ef.get.return_value = earnings_df
    mock_ef_cls.return_value = mock_ef

    result = _fetch_alt_data_v3("AAPL")

    assert result["conviction_score"] == 0
    assert not result["skip_earnings"]


@patch("yfinance.Ticker")
@patch("backend.alt_data.SecEdgarFetcher")
@patch("backend.alt_data.EarningsFetcher")
def test_alt_data_all_fetches_raise_exception(mock_ef_cls, mock_sec_cls, mock_ticker):
    """All fetches fail → conviction_score = 0, skip_earnings = False."""
    mock_ticker.return_value.calendar = None
    mock_sec_cls.side_effect = RuntimeError("edgar down")
    mock_ef_cls.side_effect = RuntimeError("earnings fetch failed")

    result = _fetch_alt_data_v3("AAPL")

    assert result["conviction_score"] == 0
    assert not result["skip_earnings"]


@patch("backend.scanner_v3.yf.Ticker")
def test_alt_data_skip_earnings_within_7_days(mock_ticker):
    """Earnings within 7 days → skip_earnings=True, short-circuits immediately."""
    cal = {"Earnings Date": [pd.Timestamp.now() + pd.Timedelta(days=3)]}
    mock_ticker.return_value.calendar = cal

    result = _fetch_alt_data_v3("AAPL")

    assert result["skip_earnings"] is True
    assert result["conviction_score"] == 0


@patch("yfinance.Ticker")
@patch("backend.alt_data.SecEdgarFetcher")
@patch("backend.alt_data.EarningsFetcher")
@patch("backend.alt_data.PolygonFetcher")
def test_alt_data_polygon_options_anomaly(mock_pf_cls, mock_ef_cls, mock_sec_cls, mock_ticker):
    """Put/call ratio < 0.5 with POLYGON_API_KEY set → options_call_anomaly=True."""
    mock_ticker.return_value.calendar = None
    mock_sec = MagicMock()
    mock_sec.get.return_value = pd.DataFrame()
    mock_sec_cls.return_value = mock_sec
    mock_ef = MagicMock()
    mock_ef.get.return_value = pd.DataFrame()
    mock_ef_cls.return_value = mock_ef

    mock_pf = MagicMock()
    mock_pf.get_put_call_ratio.return_value = 0.3   # < 0.5 → anomaly
    mock_pf_cls.return_value = mock_pf

    with patch.dict(os.environ, {"POLYGON_API_KEY": "test-key"}):
        result = _fetch_alt_data_v3("AAPL")

    assert result["options_call_anomaly"] is True
    assert result["conviction_score"] >= 1


# ── screen_stocks_v3: integration-style tests ─────────────────────────────────

@patch("yfinance.download")
@patch("backend.scanner_v3._fetch_market_caps_bulk_async", new_callable=AsyncMock)
def test_screen_v3_low_market_cap_rejected(mock_caps, mock_dl):
    df = multiindex(make_passing_df_v3())
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 500_000_000.0, "exchange": "NASDAQ",
                                        "last_price": 55.0, "last_volume": None}}
    results = [r for r in run_screen_v3(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner_v3._fetch_market_caps_bulk_async", new_callable=AsyncMock)
@patch("backend.scanner_v3.ta.rsi")
@patch("backend.scanner_v3.ta.ema")
@patch("backend.scanner_v3.ta.atr")
@patch("backend.scanner_v3._fetch_alt_data_v3")
def test_screen_v3_full_pass_with_alt_data(mock_alt, mock_atr, mock_ema, mock_rsi, mock_caps, mock_dl):
    df = multiindex(make_passing_df_v3())
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 5e9, "exchange": "NASDAQ",
                                        "last_price": 55.0, "last_volume": None}}
    mock_rsi.return_value = pd.Series([20.0] * 300)
    mock_ema.side_effect = mock_ema_side_effect
    mock_atr.return_value = pd.Series([3.0] * 300)
    mock_alt.return_value = _passing_alt_data(score=1)

    results = [r for r in run_screen_v3(["AAPL"]) if isinstance(r, dict) and "status" not in r]

    assert len(results) == 1
    r = results[0]
    assert r["ticker"] == "AAPL"
    assert r["conviction_score"] == 1
    assert r["insider_buys_30d"] == 1


@patch("yfinance.download")
@patch("backend.scanner_v3._fetch_market_caps_bulk_async", new_callable=AsyncMock)
@patch("backend.scanner_v3.ta.rsi")
@patch("backend.scanner_v3.ta.ema")
@patch("backend.scanner_v3.ta.atr")
@patch("backend.scanner_v3._fetch_alt_data_v3")
def test_screen_v3_rejected_when_conviction_zero(mock_alt, mock_atr, mock_ema, mock_rsi, mock_caps, mock_dl):
    """Ticker passes all 8 technical filters but conviction_score=0 → not surfaced."""
    df = multiindex(make_passing_df_v3())
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 5e9, "exchange": "NASDAQ",
                                        "last_price": 55.0, "last_volume": None}}
    mock_rsi.return_value = pd.Series([20.0] * 300)
    mock_ema.side_effect = mock_ema_side_effect
    mock_atr.return_value = pd.Series([3.0] * 300)
    mock_alt.return_value = {
        "insider_buys_30d": 0, "earnings_beat_streak": 0,
        "options_call_anomaly": False, "conviction_score": 0, "skip_earnings": False,
    }

    results = [r for r in run_screen_v3(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner_v3._fetch_market_caps_bulk_async", new_callable=AsyncMock)
@patch("backend.scanner_v3.ta.rsi")
@patch("backend.scanner_v3.ta.ema")
@patch("backend.scanner_v3.ta.atr")
@patch("backend.scanner_v3._fetch_alt_data_v3")
def test_screen_v3_rejected_when_skip_earnings(mock_alt, mock_atr, mock_ema, mock_rsi, mock_caps, mock_dl):
    """Ticker passes technical filters but earnings binary event → not surfaced."""
    df = multiindex(make_passing_df_v3())
    mock_dl.return_value = df
    mock_caps.return_value = {"AAPL": {"market_cap": 5e9, "exchange": "NASDAQ",
                                        "last_price": 55.0, "last_volume": None}}
    mock_rsi.return_value = pd.Series([20.0] * 300)
    mock_ema.side_effect = mock_ema_side_effect
    mock_atr.return_value = pd.Series([3.0] * 300)
    mock_alt.return_value = {
        "insider_buys_30d": 1, "earnings_beat_streak": 3,
        "options_call_anomaly": False, "conviction_score": 2, "skip_earnings": True,
    }

    results = [r for r in run_screen_v3(["AAPL"]) if isinstance(r, dict) and "status" not in r]
    assert results == []


@patch("yfinance.download")
@patch("backend.scanner_v3._fetch_market_caps_bulk_async", new_callable=AsyncMock)
def test_screen_v3_empty_download_emits_progress(mock_caps, mock_dl):
    mock_dl.return_value = pd.DataFrame()
    mock_caps.return_value = {}
    with patch("backend.scanner_v3.asyncio.sleep"):
        results = run_screen_v3(["AAPL"])
    progress_events = [r for r in results if isinstance(r, dict) and r.get("status") == "progress"]
    assert len(progress_events) > 0
