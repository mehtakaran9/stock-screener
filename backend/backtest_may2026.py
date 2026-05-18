"""
Historical backtest: apply screener filters to one or more scan dates, then
evaluate performance two weeks later.

Downloaded data is cached locally as pickle so repeat runs are instant.

Run with:
    python3 -m backend.backtest_may2026                    # spot-check SCAN_DATES
    python3 -m backend.backtest_may2026 --all-days         # every NYSE day Feb 2025 → Apr 2026
    python3 -m backend.backtest_may2026 --refresh          # force fresh download
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
DOWNLOAD_START = "2024-01-01"   # 200 bars before Feb 2025 (safe margin)
DOWNLOAD_END   = "2026-05-16"   # covers all scan + eval dates

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


# ─── 5. Full YTD run ─────────────────────────────────────────────────────────

def _run_all_days(
    tickers: list[str],
    raw: pd.DataFrame,
    start: str = "2025-02-01",
    eval_days: int = 14,
) -> pd.DataFrame:
    """Scan every NYSE trading day from *start* to 2 weeks before DOWNLOAD_END."""
    import pandas_market_calendars as mcal

    cutoff = (pd.Timestamp(DOWNLOAD_END) - pd.Timedelta(days=eval_days + 5)).strftime("%Y-%m-%d")
    nyse     = mcal.get_calendar("NYSE")
    schedule = nyse.schedule(start_date=start, end_date=cutoff)

    all_signals: list[pd.DataFrame] = []
    total_days = len(schedule)
    for i, scan_dt in enumerate(schedule.index, 1):
        scan_date = scan_dt.strftime("%Y-%m-%d")
        eval_date = (scan_dt + pd.Timedelta(days=eval_days)).strftime("%Y-%m-%d")
        df = _run_single(tickers, raw, scan_date, eval_date)
        if not df.empty:
            df["scan_date"] = scan_date
            df["eval_date"] = eval_date
            all_signals.append(df)
        if i % 50 == 0 or i == total_days:
            found = sum(len(d) for d in all_signals)
            print(f"  {i}/{total_days} days scanned … {found} signal(s) so far", end="\r", flush=True)

    print()
    return pd.concat(all_signals, ignore_index=True) if all_signals else pd.DataFrame()


def _print_aggregate(all_df: pd.DataFrame) -> None:
    sep = "═" * 75
    print(f"\n{sep}")
    print(f"{'ALL SIGNALS':^75}")
    print(sep)

    if all_df.empty:
        print("  No signals found in the date range.")
        return

    fmt = "  {:<12}  {:<7}  {:>9}  {:>8}  {:>6}  {:>6}  {:>9}  {:>9}"
    print(fmt.format("Date", "Ticker", "Entry $", "Day%", "RVOL", "RSI",
                     "1-wk%", "2-wk%"))
    print("  " + "─" * 73)
    for _, row in all_df.sort_values("scan_date").iterrows():
        rw1 = f"{row['return_1wk']:+.2f}%" if pd.notna(row.get('return_1wk')) else "N/A"
        rw2 = f"{row['return_pct']:+.2f}%"  if pd.notna(row['return_pct'])    else "N/A"
        print(fmt.format(
            row['scan_date'],
            row['ticker'],
            f"${row['entry']:.2f}",
            f"{row['day_chg']:+.2f}%",
            f"{row['rvol']:.1f}×",
            f"{row['rsi']:.1f}",
            rw1, rw2,
        ))

    print(f"\n{sep}")
    print(f"{'ACCURACY COMPARISON':^75}")
    print(sep)
    valid = all_df.dropna(subset=["return_pct"])

    for label, col in [("2-week", "return_pct"), ("1-week", "return_1wk")]:
        if col not in all_df.columns:
            continue
        v = all_df.dropna(subset=[col])
        if v.empty:
            continue
        wins  = int((v[col] > 0).sum())
        total = len(v)
        avg   = v[col].mean()
        med   = v[col].median()
        best  = v.loc[v[col].idxmax()]
        worst = v.loc[v[col].idxmin()]
        print(f"\n  {label}:")
        print(f"    Signals       : {total}")
        print(f"    Win rate      : {wins}/{total} ({100*wins//total}%)")
        print(f"    Avg return    : {avg:+.2f}%")
        print(f"    Median return : {med:+.2f}%")
        print(f"    Best          : {best['ticker']} on {best['scan_date']}  {best[col]:+.2f}%")
        print(f"    Worst         : {worst['ticker']} on {worst['scan_date']}  {worst[col]:+.2f}%")

    # Return distribution (2-week)
    if not valid.empty:
        bins   = [(-999,-10),(-10,-5),(-5,0),(0,5),(5,10),(10,999)]
        labels = ["      < -10%","  -10 to -5%","   -5 to  0%","    0 to +5%"," +5 to +10%","      > +10%"]
        print(f"\n  Return distribution (2-week):")
        for (lo, hi), lbl in zip(bins, labels):
            n = int(((valid['return_pct'] > lo) & (valid['return_pct'] <= hi)).sum())
            bar = "█" * n if n else ""
            print(f"    {lbl}  {bar}  ({n})")
    print(sep)


# ─── 6. Entry point ──────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--refresh',  action='store_true',
                        help='Force fresh download, overwriting the cache')
    parser.add_argument('--all-days', action='store_true',
                        help='Scan every NYSE trading day Feb 2025 → Apr 2026')
    args, _ = parser.parse_known_args()

    tickers = _get_sp500_tickers()
    raw     = _load_or_download(tickers, refresh=args.refresh)

    print(f"\nFilters active: RSI {CONFIG['MIN_RSI']}–{CONFIG['MAX_RSI']} | "
          f"RVOL ≥ {CONFIG['MIN_RVOL']} | "
          f"Day change ≥ {int(CONFIG['MIN_DAY_CHANGE']*100)}% | "
          f"ATR candle ≥ {CONFIG['ATR_CANDLE_MULT']}× | "
          f"Close pos ≥ {CONFIG['MIN_CLOSE_POSITION']} | "
          f"BB breakout + widening | MA stack EMA20>EMA50>EMA200")

    if args.all_days:
        print("\nScanning every NYSE trading day Feb 2025 → Apr 2026 …")
        all_df = _run_all_days(tickers, raw)
        # Compute 1-week return (5 trading days ≈ 7 calendar days)
        if not all_df.empty:
            rows_1wk = []
            for _, row in all_df.iterrows():
                ticker = row['ticker']
                entry  = row['entry']
                try:
                    eval_1wk = (pd.Timestamp(row['scan_date']) + pd.Timedelta(days=7)).strftime("%Y-%m-%d")
                    if isinstance(raw.columns, pd.MultiIndex):
                        ep = float(raw.loc[eval_1wk:, (ticker, 'Close')].iloc[0])
                    else:
                        ep = float(raw.loc[eval_1wk:, 'Close'].iloc[0])
                    rows_1wk.append(round((ep - entry) / entry * 100, 2))
                except Exception:
                    rows_1wk.append(float('nan'))
            all_df['return_1wk'] = rows_1wk
        _print_aggregate(all_df)
    else:
        for scan_date, data_end, eval_date, label in SCAN_DATES:
            df = _run_single(tickers, raw, scan_date, eval_date)
            _print_section(scan_date, eval_date, label, df)

    print()


if __name__ == '__main__':
    main()
