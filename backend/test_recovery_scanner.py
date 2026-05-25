import sys
import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch, MagicMock

from backend.recovery_scanner import (
    _get_sp500_tickers,
    _filter_recovery_ticker,
    screen_stocks,
    main,
    RECOVERY_CONFIG,
    EXCLUDED_SECTORS,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_rc_df(n=250, final_price=130.0, prev_price=145.0, volume=2_000_000, bg_volume=300_000):
    """Rising uptrend 100→200 with a crash bar.

    Defaults: final=130, prev=145 → day_chg = −10.3% < −5% ✓
    RVOL = 2M / 300K = 6.67 > 3.5 ✓
    SMA200 ≈ 162, 130/162 = 0.80 > 0.75 ✓
    SMA50 ≈ 185, 130/185 = 0.70 < 0.90 ✓
    """
    dates = pd.date_range(end="2024-01-01", periods=n)
    prices = np.linspace(100.0, 200.0, n).copy()
    prices[-1] = float(final_price)
    prices[-2] = float(prev_price)
    vols = np.full(n, float(bg_volume))
    vols[-1] = float(volume)
    return pd.DataFrame({
        "Open":   prices * 1.01,
        "High":   prices * 1.02,
        "Low":    prices * 0.97,
        "Close":  prices,
        "Volume": vols,
    }, index=dates)


def make_mc(ticker="AAPL", market_cap=2_000_000_000):
    return {ticker: {"market_cap": float(market_cap)}}


def mock_ema_stack(series, length=None, **kw):
    """EMA20 > EMA50 > EMA200 — satisfies the macro uptrend stack."""
    base = {8: 175.0, 20: 170.0, 50: 155.0, 200: 130.0}.get(length, 150.0)
    return pd.Series([base + i * 0.01 for i in range(len(series))], index=series.index)


# ── _get_sp500_tickers ────────────────────────────────────────────────────────

@patch("backend.recovery_scanner.requests.get")
def test_get_sp500_gics_sector_filters_excluded(mock_get):
    csv = "Symbol,GICS Sector\nAAPL,Information Technology\nHCA,Health Care\nT,Communication Services\nV,Financials"
    mock_resp = MagicMock()
    mock_resp.text = csv
    mock_resp.raise_for_status = MagicMock()
    mock_get.return_value = mock_resp

    tickers = _get_sp500_tickers()

    assert "AAPL" in tickers
    assert "V" in tickers
    assert "HCA" not in tickers       # Health Care excluded
    assert "T" not in tickers         # Communication Services excluded


@patch("backend.recovery_scanner.requests.get")
def test_get_sp500_sector_col_fallback(mock_get):
    """'Sector' column used when 'GICS Sector' is absent."""
    csv = "Symbol,Sector\nAAPL,Technology\nHCA,Health Care"
    mock_resp = MagicMock()
    mock_resp.text = csv
    mock_resp.raise_for_status = MagicMock()
    mock_get.return_value = mock_resp

    tickers = _get_sp500_tickers()

    assert "AAPL" in tickers
    assert "HCA" not in tickers


@patch("backend.recovery_scanner.requests.get")
def test_get_sp500_no_sector_col_returns_all(mock_get):
    csv = "Symbol\nAAPL\nMSFT"
    mock_resp = MagicMock()
    mock_resp.text = csv
    mock_resp.raise_for_status = MagicMock()
    mock_get.return_value = mock_resp

    tickers = _get_sp500_tickers()

    assert tickers == ["AAPL", "MSFT"]


@patch("backend.recovery_scanner.requests.get", side_effect=Exception("Network error"))
def test_get_sp500_fetch_fails_returns_fallback(mock_get):
    tickers = _get_sp500_tickers()
    assert "AAPL" in tickers
    assert len(tickers) == 10


@patch("backend.recovery_scanner.requests.get")
def test_get_sp500_sector_parse_exception_falls_back_to_plain(mock_get):
    """Inner sector-column parse raises → falls back to plain symbol list."""
    mock_resp = MagicMock()
    mock_resp.text = "dummy"
    mock_resp.raise_for_status = MagicMock()
    mock_get.return_value = mock_resp

    mock_df = MagicMock()
    mock_df.columns = pd.Index(["Symbol", "GICS Sector"])
    call_count = [0]

    def getitem_side(key):
        if key == "Symbol":
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("parse error")
            return pd.Series(["AAPL", "MSFT"])  # real Series for tolist() call
        return pd.Series(["Technology", "Technology"])

    mock_df.__getitem__.side_effect = getitem_side

    with patch("backend.recovery_scanner.pd.read_csv", return_value=mock_df):
        tickers = _get_sp500_tickers()

    assert "AAPL" in tickers


@patch("backend.recovery_scanner.requests.get")
def test_get_sp500_replaces_dots_with_dashes(mock_get):
    csv = "Symbol\nBRK.B\nAAPL"
    mock_resp = MagicMock()
    mock_resp.text = csv
    mock_resp.raise_for_status = MagicMock()
    mock_get.return_value = mock_resp

    tickers = _get_sp500_tickers()
    assert "BRK-B" in tickers
    assert "BRK.B" not in tickers


# ── _filter_recovery_ticker ───────────────────────────────────────────────────

def test_filter_too_few_rows():
    result = _filter_recovery_ticker("AAPL", make_rc_df(n=150), make_mc())
    assert result is None


def test_filter_low_market_cap():
    result = _filter_recovery_ticker("AAPL", make_rc_df(), {"AAPL": {"market_cap": 500_000_000.0}})
    assert result is None


def test_filter_low_price():
    result = _filter_recovery_ticker("AAPL", make_rc_df(final_price=4.0, prev_price=4.5), make_mc())
    assert result is None


def test_filter_low_volume():
    result = _filter_recovery_ticker("AAPL", make_rc_df(volume=100_000), make_mc())
    assert result is None


def test_filter_day_change_not_red_enough():
    """Day change is −2%, not past the −5% threshold."""
    result = _filter_recovery_ticker(
        "AAPL",
        make_rc_df(final_price=142.1, prev_price=145.0),  # −2.0%
        make_mc(),
    )
    assert result is None


def test_filter_rvol_too_low():
    """Uniform volume → RVOL = 1.0 < 3.5×."""
    df = make_rc_df()
    df["Volume"] = 600_000.0
    result = _filter_recovery_ticker("AAPL", df, make_mc())
    assert result is None


@patch("backend.recovery_scanner.ta.rsi", return_value=None)
def test_filter_rsi_none(mock_rsi):
    result = _filter_recovery_ticker("AAPL", make_rc_df(), make_mc())
    assert result is None


@patch("backend.recovery_scanner.ta.rsi")
def test_filter_rsi_too_high(mock_rsi):
    """RSI = 30 ≥ MAX_RSI (25) → rejected."""
    mock_rsi.return_value = pd.Series([30.0] * 250)
    result = _filter_recovery_ticker("AAPL", make_rc_df(), make_mc())
    assert result is None


@patch("backend.recovery_scanner.ta.rsi")
def test_filter_sma200_nan(mock_rsi):
    """NaN in SMA200 window → SMA200 ratio check skipped/failed."""
    n = 250
    df = make_rc_df(n=n)
    # One NaN inside the rolling-200 window forces sma200.iloc[-1] = NaN
    df.iloc[50, df.columns.get_loc("Close")] = np.nan
    mock_rsi.return_value = pd.Series([20.0] * n, index=df.index)
    result = _filter_recovery_ticker("AAPL", df, make_mc())
    assert result is None


@patch("backend.recovery_scanner.ta.rsi")
def test_filter_sma200_ratio_too_low(mock_rsi):
    """Price at 60 vs SMA200 ≈ 155 → ratio 0.39 < 0.75."""
    n = 250
    df = make_rc_df(n=n, final_price=60.0, prev_price=70.0)
    mock_rsi.return_value = pd.Series([20.0] * n, index=df.index)
    result = _filter_recovery_ticker("AAPL", df, make_mc())
    assert result is None


@patch("backend.recovery_scanner.ta.rsi")
@patch("backend.recovery_scanner.ta.ema")
def test_filter_ema_stack_fails(mock_ema, mock_rsi):
    """EMA20 < EMA50 → macro uptrend not intact."""
    n = 250
    df = make_rc_df(n=n)
    mock_rsi.return_value = pd.Series([20.0] * n, index=df.index)

    def bad_stack(series, length=None, **kw):
        val = {8: 175, 20: 120, 50: 155, 200: 130}.get(length, 150)
        return pd.Series([float(val)] * len(series), index=series.index)

    mock_ema.side_effect = bad_stack
    result = _filter_recovery_ticker("AAPL", df, make_mc())
    assert result is None


@patch("backend.recovery_scanner.ta.rsi")
@patch("backend.recovery_scanner.ta.ema")
def test_filter_sma50_discount_fail(mock_ema, mock_rsi):
    """price / SMA50 ≥ 0.90 → not a deep enough discount."""
    # Flat data at 150; last bar drops to 142 (-5.33%) → SMA50 = 150
    # 142/150 = 0.947 ≥ 0.90 → fails
    n = 250
    dates = pd.date_range(end="2024-01-01", periods=n)
    prices = np.full(n, 150.0)
    prices[-1] = 142.0
    prices[-2] = 150.0
    vols = np.full(n, 300_000.0)
    vols[-1] = 2_000_000.0
    df = pd.DataFrame({
        "Open": prices * 1.01, "High": prices * 1.02,
        "Low":  prices * 0.97, "Close": prices, "Volume": vols,
    }, index=dates)
    mock_rsi.return_value = pd.Series([20.0] * n, index=df.index)
    mock_ema.side_effect = mock_ema_stack
    result = _filter_recovery_ticker("AAPL", df, make_mc())
    assert result is None


@patch("backend.recovery_scanner.ta.rsi")
@patch("backend.recovery_scanner.ta.ema")
def test_filter_passes_all_returns_result_dict(mock_ema, mock_rsi):
    n = 250
    df = make_rc_df(n=n)
    mock_rsi.return_value = pd.Series([20.0] * n, index=df.index)
    mock_ema.side_effect = mock_ema_stack

    result = _filter_recovery_ticker("AAPL", df, make_mc())

    assert result is not None
    assert result["ticker"] == "AAPL"
    assert result["rsi"] == 20.0
    assert result["change"] < RECOVERY_CONFIG["MAX_DAY_CHG"]
    assert result["vol_ratio"] > RECOVERY_CONFIG["MIN_RVOL"]
    assert "entry1" in result and "stop1" in result


# ── screen_stocks ─────────────────────────────────────────────────────────────

def test_screen_stocks_empty_download_returns_empty():
    with patch("backend.recovery_scanner.yf.download", return_value=pd.DataFrame()):
        with patch("backend.recovery_scanner.yf.Ticker"):
            results = screen_stocks(["AAPL"])
    assert results == []


def test_screen_stocks_market_cap_exception_uses_zero():
    """yf.Ticker raises → market_cap falls back to 0, ticker filtered out."""
    df = make_rc_df()
    raw = df.copy()
    raw.columns = pd.MultiIndex.from_product([["AAPL"], df.columns])

    with patch("backend.recovery_scanner.yf.download", return_value=raw):
        with patch("backend.recovery_scanner.yf.Ticker", side_effect=RuntimeError("fetch error")):
            results = screen_stocks(["AAPL"])

    # market_cap = 0 < MIN_MARKET_CAP → ticker filtered out
    assert results == []


def test_screen_stocks_uses_default_tickers_when_none():
    """screen_stocks(None) calls _get_sp500_tickers internally."""
    with patch("backend.recovery_scanner._get_sp500_tickers", return_value=["AAPL"]) as mock_tickers:
        with patch("backend.recovery_scanner.yf.download", return_value=pd.DataFrame()):
            with patch("backend.recovery_scanner.yf.Ticker"):
                results = screen_stocks(None)
    mock_tickers.assert_called_once()
    assert results == []


@patch("backend.recovery_scanner.ta.rsi")
@patch("backend.recovery_scanner.ta.ema")
def test_screen_stocks_returns_sorted_by_rvol(mock_ema, mock_rsi):
    """Passing tickers sorted by vol_ratio descending."""
    tickers = ["AAPL", "MSFT"]
    dfs = {t: make_rc_df(volume=2_000_000 if t == "AAPL" else 4_000_000) for t in tickers}

    def fake_download(chunk, **kw):
        raw = pd.concat({t: dfs[t] for t in chunk if t in dfs}, axis=1)
        return raw

    n = 250
    mock_rsi.return_value = pd.Series([20.0] * n)
    mock_ema.side_effect = mock_ema_stack

    mock_ticker_info = MagicMock()
    mock_ticker_info.fast_info.market_cap = 2_000_000_000.0
    mock_ticker_info.fast_info.last_price = 130.0

    with patch("backend.recovery_scanner.yf.download", side_effect=fake_download):
        with patch("backend.recovery_scanner.yf.Ticker", return_value=mock_ticker_info):
            results = screen_stocks(tickers)

    # Both tickers should pass; MSFT has higher RVOL → first
    assert len(results) == 2
    assert results[0]["ticker"] == "MSFT"
    assert results[0]["vol_ratio"] >= results[1]["vol_ratio"]


# ── main() ────────────────────────────────────────────────────────────────────

def test_main_no_results_prints_no_match_message():
    with patch("sys.argv", ["recovery_scanner"]):
        with patch("backend.recovery_scanner._get_sp500_tickers", return_value=["AAPL"]):
            with patch("backend.recovery_scanner.screen_stocks", return_value=[]):
                with patch("builtins.print") as mock_print:
                    main()
    output = " ".join(str(c) for c in mock_print.call_args_list)
    assert "No matches" in output


def test_main_with_results_prints_table():
    fake_results = [
        {
            "ticker": f"T{i}",
            "price": 130.0,
            "change": -10.0,
            "vol_ratio": float(10 - i),
            "rsi": 20.0,
            "sma200_ratio": 0.80,
            "atr14": 3.0,
        }
        for i in range(10)
    ]
    with patch("sys.argv", ["recovery_scanner", "--top", "3"]):
        with patch("backend.recovery_scanner._get_sp500_tickers", return_value=["X"]):
            with patch("backend.recovery_scanner.screen_stocks", return_value=fake_results):
                with patch("builtins.print") as mock_print:
                    main()
    output = " ".join(str(c) for c in mock_print.call_args_list)
    # 3 result rows printed (top 3), not 10
    assert "T0" in output
    assert "T3" not in output
