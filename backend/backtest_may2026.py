"""
Historical backtest: apply screener filters to one or more scan dates, then
evaluate performance two weeks later.

Downloaded data is cached locally as Parquet so repeat runs are instant.

Run with:
    python3 -m backend.backtest_may2026           # single date (May 1 → May 14)
    python3 -m backend.backtest_may2026 --refresh  # force fresh download
    python3 backend/backtest_may2026.py
"""
import sys, os, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import io, pathlib
import requests
import pandas as pd
import yfinance as yf

from backend.scanner import _filter_ticker, CONFIG

# ─── Configuration ────────────────────────────────────────────────────────────
SCAN_DATES = [
    # (scan_date,   data_end,     eval_date,    label)
    ("2026-04-08", "2026-04-09", "2026-04-22", "2-week"),
    ("2026-05-01", "2026-05-02", "2026-05-14", "2-week"),
    ("2026-05-04", "2026-05-05", "2026-05-11", "1-week"),
    ("2026-05-04", "2026-05-05", "2026-05-14", "10-day"),
    ("2026-05-05", "2026-05-06", "2026-05-12", "1-week"),
    ("2026-05-05", "2026-05-06", "2026-05-14", "9-day"),
]
DOWNLOAD_START = "2024-04-01"   # enough history for 200-period indicators
DOWNLOAD_END   = "2026-05-15"   # covers all scan + eval dates

CACHE_DIR  = pathlib.Path(__file__).parent / "_backtest_cache"
CACHE_FILE = CACHE_DIR / "sp500_ohlcv.pkl"

# ─── 1. S&P 500 tickers ──────────────────────────────────────────────────────

def _get_sp500_tickers() -> list[str]:
    url = ("https://raw.githubusercontent.com/datasets/s-and-p-500-companies"
           "/master/data/constituents.csv")
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    return [t.replace('.', '-') for t in df['Symbol'].tolist()]


# ─── 2. Cache helpers ─────────────────────────────────────────────────────────

def _load_or_download(tickers: list[str], refresh: bool = False) -> pd.DataFrame:
    CACHE_DIR.mkdir(exist_ok=True)

    if CACHE_FILE.exists() and not refresh:
        print(f"Loading cached data from {CACHE_FILE} …")
        return pd.read_pickle(CACHE_FILE)

    print(f"Downloading {len(tickers)}-ticker data "
          f"({DOWNLOAD_START} → {DOWNLOAD_END})…")
    raw = yf.download(
        tickers,
        start=DOWNLOAD_START,
        end=DOWNLOAD_END,
        group_by='ticker',
        progress=True,
        threads=True,
    )
    raw.to_pickle(CACHE_FILE)
    print(f"Cached to {CACHE_FILE} ({CACHE_FILE.stat().st_size // 1_048_576} MB)")
    return raw


# ─── 3. Run a single scan date ───────────────────────────────────────────────

def _run_single(
    tickers: list[str],
    raw: pd.DataFrame,
    scan_date: str,
    eval_date: str,
) -> pd.DataFrame:
    """Return a DataFrame with one row per passing ticker."""
    sliced = raw.loc[:scan_date]

    matches = []
    for ticker in tickers:
        try:
            if isinstance(sliced.columns, pd.MultiIndex):
                if ticker not in sliced.columns.get_level_values(0):
                    continue
                df = sliced[ticker].dropna(how='all')
            else:
                df = sliced.dropna(how='all')

            if len(df) < 200:
                continue

            mc = {
                ticker: {
                    'market_cap':  10_000_000_000.0,
                    'exchange':    'NASDAQ',
                    'last_price':  float(df['Close'].iloc[-1]),
                    'last_volume': int(df['Volume'].iloc[-1]),
                }
            }
            result = _filter_ticker(ticker, df, mc)
            if result is not None:
                matches.append(result)
        except Exception:
            pass

    if not matches:
        return pd.DataFrame()

    # Fetch eval-date prices from the already-downloaded full raw frame
    rows = []
    for r in matches:
        ticker = r['ticker']
        entry  = r['price']
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                ep = float(raw.loc[eval_date:, (ticker, 'Close')].iloc[0])
            else:
                ep = float(raw.loc[eval_date:, 'Close'].iloc[0])
            ret_pct = (ep - entry) / entry * 100
        except Exception:
            ep, ret_pct = float('nan'), float('nan')

        rows.append({
            'ticker':      ticker,
            'entry':       entry,
            'day_chg':     r['change'],
            'rvol':        r['vol_ratio'],
            'rsi':         r['rsi'],
            'eval_close':  round(ep, 2),
            'return_pct':  round(ret_pct, 2),
        })

    return pd.DataFrame(rows)


# ─── 4. Report ────────────────────────────────────────────────────────────────

def _print_section(scan_date: str, eval_date: str, label: str, df: pd.DataFrame) -> None:
    sep = "─" * 72
    print(f"\n{'═'*72}")
    print(f"  SCAN {scan_date}  →  EVAL {eval_date}  ({label})")
    print(f"{'═'*72}")

    if df.empty:
        print("  No matches — the combined breakout criteria found no candidates.")
        print(f"  (Requires: price > BB upper, bands widening, RVOL ≥ {CONFIG['MIN_RVOL']}×,")
        print(f"   RSI {CONFIG['MIN_RSI']}–{CONFIG['MAX_RSI']}, MA stack, ATR candle ≥ {CONFIG['ATR_CANDLE_MULT']}× ATR14)")
        return

    fmt = "  {:<7}  {:>9}  {:>8}  {:>6}  {:>6}  {:>10}  {:>9}"
    print(fmt.format("Ticker", "Entry $", "Day%", "RVOL", "RSI",
                     f"{eval_date[:10]} $", "Return"))
    print("  " + sep)
    for _, row in df.iterrows():
        ret_str  = f"{row['return_pct']:+.2f}%" if pd.notna(row['return_pct']) else "N/A"
        eval_str = f"${row['eval_close']:.2f}"  if pd.notna(row['eval_close']) else "N/A"
        print(fmt.format(
            row['ticker'],
            f"${row['entry']:.2f}",
            f"{row['day_chg']:+.2f}%",
            f"{row['rvol']:.1f}×",
            f"{row['rsi']:.1f}",
            eval_str,
            ret_str,
        ))

    valid = df.dropna(subset=['return_pct'])
    if not valid.empty:
        wins  = int((valid['return_pct'] > 0).sum())
        total = len(valid)
        avg   = valid['return_pct'].mean()
        print(f"\n  {len(df)} match(es)  |  win rate {wins}/{total}  |  avg return {avg:+.2f}%")


# ─── 5. Entry point ──────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--refresh', action='store_true',
                        help='Force fresh download, overwriting the cache')
    args, _ = parser.parse_known_args()

    tickers = _get_sp500_tickers()
    raw     = _load_or_download(tickers, refresh=args.refresh)

    print(f"\nFilters active: RSI {CONFIG['MIN_RSI']}–{CONFIG['MAX_RSI']} | "
          f"RVOL ≥ {CONFIG['MIN_RVOL']} | "
          f"ATR candle ≥ {CONFIG['ATR_CANDLE_MULT']}× | "
          f"price > BB upper (widening) | MA stack EMA20>EMA50>EMA200")

    for scan_date, data_end, eval_date, label in SCAN_DATES:
        df = _run_single(tickers, raw, scan_date, eval_date)
        _print_section(scan_date, eval_date, label, df)

    print()


if __name__ == '__main__':
    main()
