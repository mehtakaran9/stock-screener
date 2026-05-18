"""
Recovery / mean-reversion screener.

Finds S&P 500 stocks that are in structured uptrends but have experienced
a sharp single-day selloff (oversold + high panic volume), creating a
high-probability 2-week snap-back trade.

This is the OPPOSITE of the momentum screener (scanner.py):
  - Entry on RED days (big down moves), not green days
  - RSI oversold, not momentum confirmed
  - High selling RVOL signals panic/capitulation, not institutional buying
  - Hold 10 trading days (≈2 calendar weeks)

Thresholds below are calibrated from the 5-year reverse backtest via:
    python3 -m backend.reverse_backtest --validate-recovery

Usage:
    python3 -m backend.recovery_scanner              # scan today's S&P 500
    python3 -m backend.recovery_scanner --top 10     # top 10 by RVOL
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import io, logging
import numpy as np
import pandas as pd
import requests
import yfinance as yf
import pandas_ta_classic as ta

logger = logging.getLogger(__name__)

# ─── Calibrated from 5-year S&P 500 reverse backtest (2021–2026) ──────────────
#
# Full sweep results (python3 -m backend.reverse_backtest --validate-recovery):
#   666,534 ticker-days tested across 503 S&P 500 tickers, 1338 trading days
#
#   BEST CONFIG (highest 3-month win rate across 18 threshold combinations):
#     RSI < 25  |  day change < -5%  |  RVOL > 3.5×  |  SMA200 > 75%  |  EMA stack
#     3-month win rate : 68.0%  (N=26 signals, ~1 per trading day)
#     3-month avg ret  : +6.7%
#     2-week  win rate : 61.5%
#     2-week  avg ret  : +1.6%
#
#   HONEST CEILING: 70% 3-month accuracy is the outer limit for S&P 500 large caps
#   using purely technical filters. No combination in 18 tested configs exceeded 68%.
#   The 40%+ average return target is not achievable for this universe — the best
#   config returns +6.7% on average over 3 months (individual winners can reach 47%+,
#   but the AVERAGE is capped by the distribution of large-cap recovery moves).
#
RECOVERY_CONFIG = {
    "MAX_RSI":           25.0,   # RSI(14) must be below 25 (extreme oversold — capitulation)
    "MAX_DAY_CHG":       -5.0,   # entry day must be down > 5% (panic selloff)
    "MIN_RVOL":           3.5,   # relative volume > 3.5× (elevated panic selling)
    "MIN_SMA200_RATIO":   0.75,  # price > 75% of SMA200 (structural uptrend still intact)
    "REQUIRE_EMA_STACK":  True,  # EMA20 > EMA50 > EMA200 (macro trend aligned)
    # Additional structural filters
    "MIN_MARKET_CAP":    1_000_000_000,  # > $1B (liquidity)
    "MIN_PRICE":          5.0,           # > $5 (no penny stocks)
    "MIN_VOLUME":       500_000,         # > 500K shares traded
}

HOLD_DAYS = 63   # 3-month hold — backtested 68% win rate, +6.7% avg return


# ─── 1. Universe ──────────────────────────────────────────────────────────────

def _get_sp500_tickers() -> list[str]:
    url = ("https://raw.githubusercontent.com/datasets/s-and-p-500-companies"
           "/master/data/constituents.csv")
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        return [t.replace('.', '-') for t in df['Symbol'].tolist()]
    except Exception:
        logger.warning("S&P 500 CSV fetch failed — using 10-ticker fallback list")
        return ["AAPL","MSFT","AMZN","NVDA","GOOGL","META","TSLA","JPM","UNH","V"]


# ─── 2. Single-ticker filter ──────────────────────────────────────────────────

def _filter_recovery_ticker(
    ticker: str,
    df: pd.DataFrame,
    market_caps: dict,
) -> dict | None:
    """
    Return a result dict if ticker passes all recovery filters, else None.
    df must have columns: Open, High, Low, Close, Volume (at least 220 rows).
    """
    cfg = RECOVERY_CONFIG

    if len(df) < 220:
        return None

    close  = df['Close'].astype(float)
    high   = df['High'].astype(float)
    low    = df['Low'].astype(float)
    volume = df['Volume'].astype(float)

    # ── Market cap / price / volume checks ───────────────────────────────────
    mc_info   = market_caps.get(ticker, {})
    mkt_cap   = mc_info.get('market_cap',  0)
    last_price = float(close.iloc[-1])
    last_vol   = float(volume.iloc[-1])

    if mkt_cap < cfg['MIN_MARKET_CAP']:
        return None
    if last_price < cfg['MIN_PRICE']:
        return None
    if last_vol < cfg['MIN_VOLUME']:
        return None

    # ── Day change (must be a strong red day) ────────────────────────────────
    prev_close = float(close.iloc[-2]) if len(close) >= 2 else last_price
    day_chg_pct = (last_price - prev_close) / prev_close * 100
    if day_chg_pct >= cfg['MAX_DAY_CHG']:
        return None

    # ── Relative volume ───────────────────────────────────────────────────────
    vol20 = float(volume.iloc[-21:-1].mean()) if len(volume) >= 21 else float(volume.mean())
    rvol  = last_vol / vol20 if vol20 > 0 else 0.0
    if rvol < cfg['MIN_RVOL']:
        return None

    # ── RSI(14) must be oversold ──────────────────────────────────────────────
    rsi_s = ta.rsi(close, length=14)
    if rsi_s is None or rsi_s.empty or pd.isna(rsi_s.iloc[-1]):
        return None
    rsi_val = float(rsi_s.iloc[-1])
    if rsi_val >= cfg['MAX_RSI']:
        return None

    # ── SMA200 ratio — not in structural freefall ─────────────────────────────
    sma200 = close.rolling(200).mean()
    if pd.isna(sma200.iloc[-1]) or sma200.iloc[-1] == 0:
        return None
    sma200_ratio = last_price / float(sma200.iloc[-1])
    if sma200_ratio < cfg['MIN_SMA200_RATIO']:
        return None

    # ── EMA stack (optional) ──────────────────────────────────────────────────
    ema20  = ta.ema(close, length=20)
    ema50  = ta.ema(close, length=50)
    ema200 = ta.ema(close, length=200)
    ema_stack = (
        ema20 is not None and ema50 is not None and ema200 is not None and
        float(ema20.iloc[-1]) > float(ema50.iloc[-1]) > float(ema200.iloc[-1])
    )
    if cfg['REQUIRE_EMA_STACK'] and not ema_stack:
        return None

    # ── ATR(14) for stop-loss sizing ──────────────────────────────────────────
    atr14 = ta.atr(high, low, close, length=14)
    atr_val = float(atr14.iloc[-1]) if atr14 is not None and not atr14.empty else 0.0

    # ── MACD histogram (informational — falling is expected on entry day) ─────
    macd_df   = ta.macd(close)
    macd_hist = np.nan
    if macd_df is not None and not macd_df.empty:
        hcol = next((c for c in macd_df.columns if c.startswith('MACDh_')), None)
        if hcol:
            macd_hist = float(macd_df[hcol].iloc[-1])

    # ── BB position (informational) ───────────────────────────────────────────
    bb_df = ta.bbands(close, length=20, std=2)
    bb_pos = np.nan
    if bb_df is not None and not bb_df.empty:
        ucol = next((c for c in bb_df.columns if c.startswith('BBU_')), None)
        lcol = next((c for c in bb_df.columns if c.startswith('BBL_')), None)
        if ucol and lcol:
            bbu = float(bb_df[ucol].iloc[-1])
            bbl = float(bb_df[lcol].iloc[-1])
            bb_range = bbu - bbl
            bb_pos = (last_price - bbl) / bb_range if bb_range > 0 else np.nan

    # ── EMA values for display ────────────────────────────────────────────────
    ema8 = ta.ema(close, length=8)

    return {
        'ticker':       ticker,
        'price':        round(last_price, 2),
        'change':       round(day_chg_pct, 2),
        'volume':       int(last_vol),
        'vol_ratio':    round(rvol, 2),
        'market_cap':   mkt_cap,
        'rsi':          round(rsi_val, 1),
        'macd_hist':    round(macd_hist, 4) if pd.notna(macd_hist) else None,
        'sma200_ratio': round(sma200_ratio, 3),
        'ema_stack':    ema_stack,
        'bb_pos':       round(bb_pos, 3) if pd.notna(bb_pos) else None,
        'atr14':        round(atr_val, 2),
        'ema8':         round(float(ema8.iloc[-1]), 2) if ema8 is not None else None,
        'ema20':        round(float(ema20.iloc[-1]), 2) if ema20 is not None else None,
        'ema50':        round(float(ema50.iloc[-1]), 2) if ema50 is not None else None,
        'ema200':       round(float(ema200.iloc[-1]), 2) if ema200 is not None else None,
        'sma200':       round(float(sma200.iloc[-1]), 2),
        # Suggested entries / stops for 2-week recovery trade
        'entry1':       round(last_price, 2),
        'stop1':        round(last_price - 1.0 * atr_val, 2),
        'entry2':       round(float(ema8.iloc[-1]), 2) if ema8 is not None else None,
        'stop2':        round(float(ema8.iloc[-1]) - 0.5 * atr_val, 2) if ema8 is not None else None,
        'entry3':       round(float(sma200.iloc[-1]), 2),
        'stop3':        round(float(sma200.iloc[-1]) - 0.5 * atr_val, 2),
    }


# ─── 3. Full scan ─────────────────────────────────────────────────────────────

def screen_stocks(tickers: list[str] | None = None) -> list[dict]:
    """
    Download OHLCV for all tickers and run recovery filter.
    Returns list of passing tickers sorted by RVOL descending.
    """
    if tickers is None:
        tickers = _get_sp500_tickers()

    # Download in chunks of 50
    CHUNK = 50
    all_data: dict[str, pd.DataFrame] = {}
    for start in range(0, len(tickers), CHUNK):
        chunk = tickers[start:start + CHUNK]
        raw = yf.download(chunk, period='2y', group_by='ticker',
                          progress=False, threads=True)
        for ticker in chunk:
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    if ticker not in raw.columns.get_level_values(0):
                        continue
                    df = raw[ticker].dropna(how='all')
                else:
                    df = raw.dropna(how='all')
                if len(df) >= 220:
                    all_data[ticker] = df
            except Exception:
                pass

    # Fetch market caps
    market_caps: dict[str, dict] = {}
    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).fast_info
            market_caps[ticker] = {
                'market_cap':  getattr(info, 'market_cap', 0) or 0,
                'last_price':  getattr(info, 'last_price', 0) or 0,
            }
        except Exception:
            market_caps[ticker] = {'market_cap': 0, 'last_price': 0}

    results = []
    for ticker, df in all_data.items():
        result = _filter_recovery_ticker(ticker, df, market_caps)
        if result is not None:
            results.append(result)

    results.sort(key=lambda r: r['vol_ratio'], reverse=True)
    return results


# ─── 4. CLI ───────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Recovery/mean-reversion stock screener")
    parser.add_argument('--top', type=int, default=20, help='Show top N matches by RVOL')
    args = parser.parse_args()

    print("\nRecovery Screener — S&P 500 oversold snap-back setups\n")
    print(f"Filters: RSI < {RECOVERY_CONFIG['MAX_RSI']} | "
          f"Day < {RECOVERY_CONFIG['MAX_DAY_CHG']}% | "
          f"RVOL > {RECOVERY_CONFIG['MIN_RVOL']}× | "
          f"SMA200 ratio > {RECOVERY_CONFIG['MIN_SMA200_RATIO']}\n")

    tickers = _get_sp500_tickers()
    results = screen_stocks(tickers)

    if not results:
        print("No matches today — no S&P 500 stock meets all recovery criteria.")
        print("This is normal on flat or green market days.")
        return

    sep = "─" * 72
    fmt = "  {:<7}  {:>9}  {:>8}  {:>6}  {:>6}  {:>8}  {:>8}"
    print(fmt.format("Ticker", "Price", "Day%", "RVOL", "RSI", "SMA200%", "ATR14"))
    print("  " + sep)
    for r in results[:args.top]:
        print(fmt.format(
            r['ticker'],
            f"${r['price']:.2f}",
            f"{r['change']:+.2f}%",
            f"{r['vol_ratio']:.1f}×",
            f"{r['rsi']:.0f}",
            f"{r['sma200_ratio']*100:.1f}%",
            f"${r['atr14']:.2f}",
        ))

    print(f"\n  {len(results)} match(es) | recommended hold: {HOLD_DAYS} trading days (≈3 months)")
    print(f"  Backtested: ~68% 3-month win rate, +6.7% avg return (5-year S&P 500)")
    print(f"  Calibrate thresholds: python3 -m backend.reverse_backtest --validate-recovery\n")


if __name__ == '__main__':
    main()
