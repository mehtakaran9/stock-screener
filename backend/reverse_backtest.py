"""
Reverse backtest: find every S&P 500 stock that returned ≥45% over any
3-month (63 trading-day) window in the last 5 years, then analyse what
those stocks looked like on the FIRST DAY of each winning run.

This answers: "what filters would have caught the big winners?"

Usage:
    python3 -m backend.reverse_backtest                      # 63-day, 45% target
    python3 -m backend.reverse_backtest --window 126         # 6-month window
    python3 -m backend.reverse_backtest --min-return 0.30    # 30% target
    python3 -m backend.reverse_backtest --refresh            # force re-download
    python3 -m backend.reverse_backtest --scan-from 2022-01-01  # limit scan start

    # Recovery screener validation — sweep thresholds, find ≥95% 2-week accuracy
    python3 -m backend.reverse_backtest --validate-recovery
    python3 -m backend.reverse_backtest --validate-recovery --scan-from 2021-01-01
"""
import sys, os, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pathlib, io
import numpy as np
import pandas as pd
import requests
import yfinance as yf
import pandas_ta_classic as ta

# ─── Configuration ────────────────────────────────────────────────────────────

DOWNLOAD_START = "2020-01-01"   # 200 bars before Jan 2021
DOWNLOAD_END   = "2026-05-16"

CACHE_DIR  = pathlib.Path(__file__).parent / "_backtest_cache"
CACHE_FILE = CACHE_DIR / "sp500_5yr_ohlcv.pkl"   # separate from backtest cache

MIN_HISTORY = 200   # bars needed before first indicator is valid


# ─── 1. Data helpers ─────────────────────────────────────────────────────────

def _get_sp500(timeout: int = 20):
    url = ("https://raw.githubusercontent.com/datasets/s-and-p-500-companies"
           "/master/data/constituents.csv")
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    tickers    = [t.replace('.', '-') for t in df['Symbol'].tolist()]
    sector_col = 'GICS Sector' if 'GICS Sector' in df.columns else 'Sector'
    sector_map = {t.replace('.', '-'): s for t, s in zip(df['Symbol'], df[sector_col])}
    return tickers, sector_map


def _load_or_download(tickers: list[str], refresh: bool = False) -> pd.DataFrame:
    CACHE_DIR.mkdir(exist_ok=True)
    if CACHE_FILE.exists() and not refresh:
        print(f"Loading cached data from {CACHE_FILE} …")
        return pd.read_pickle(CACHE_FILE)
    print(f"Downloading {len(tickers)}-ticker OHLCV ({DOWNLOAD_START} → {DOWNLOAD_END}) …")
    print("This may take 10–15 minutes — data is cached after the first run.")
    raw = yf.download(tickers, start=DOWNLOAD_START, end=DOWNLOAD_END,
                      group_by='ticker', progress=True, threads=True)
    raw.to_pickle(CACHE_FILE)
    print(f"Cached to {CACHE_FILE} ({CACHE_FILE.stat().st_size // 1_048_576} MB)")
    return raw


# ─── 2. Per-ticker TA computation (vectorised) ───────────────────────────────

def _compute_signals(df: pd.DataFrame) -> pd.DataFrame | None:
    """Return a DataFrame of daily indicator values for one ticker."""
    if len(df) < MIN_HISTORY + 30:
        return None

    close  = df['Close'].astype(float)
    high   = df['High'].astype(float)
    low    = df['Low'].astype(float)
    volume = df['Volume'].astype(float)

    # ── Basic daily stats ────────────────────────────────────────────────
    day_chg  = close.pct_change() * 100
    vol20    = volume.rolling(20).mean()
    rvol     = volume / vol20

    # ── Trend indicators ─────────────────────────────────────────────────
    rsi_s  = ta.rsi(close, length=14)
    ema8   = ta.ema(close, length=8)
    ema20  = ta.ema(close, length=20)
    ema50  = ta.ema(close, length=50)
    ema200 = ta.ema(close, length=200)
    sma200 = ta.sma(close, length=200) if hasattr(ta, 'sma') else ema200

    # ── MACD ─────────────────────────────────────────────────────────────
    macd_df   = ta.macd(close)
    macd_hist = pd.Series(np.nan, index=close.index)
    if macd_df is not None and not macd_df.empty:
        hcol = next((c for c in macd_df.columns if c.startswith('MACDh_')), None)
        if hcol:
            macd_hist = macd_df[hcol]

    # ── Bollinger Bands (20, 2) ───────────────────────────────────────────
    bb_df     = ta.bbands(close, length=20, std=2)
    bb_upper = bb_lower = bb_middle = pd.Series(np.nan, index=close.index)
    if bb_df is not None and not bb_df.empty:
        ucol = next((c for c in bb_df.columns if c.startswith('BBU_')), None)
        lcol = next((c for c in bb_df.columns if c.startswith('BBL_')), None)
        mcol = next((c for c in bb_df.columns if c.startswith('BBM_')), None)
        if ucol: bb_upper  = bb_df[ucol]
        if lcol: bb_lower  = bb_df[lcol]
        if mcol: bb_middle = bb_df[mcol]

    # ── ATR (14) ─────────────────────────────────────────────────────────
    atr14 = ta.atr(high, low, close, length=14)

    # ── Derived features ─────────────────────────────────────────────────
    bb_range   = (bb_upper - bb_lower).replace(0, np.nan)
    bb_pos     = (close - bb_lower) / bb_range          # 0=lower, 1=upper
    above_bbu  = (close > bb_upper).astype(float)

    day_range  = (high - low).replace(0, np.nan)
    close_pos  = (close - low) / day_range              # 0=low, 1=high

    ema_stack  = pd.Series(np.nan, index=close.index)
    if ema20 is not None and ema50 is not None and ema200 is not None:
        ema_stack = ((ema20 > ema50) & (ema50 > ema200)).astype(float)

    sma200_ratio = pd.Series(np.nan, index=close.index)
    if sma200 is not None:
        sma200_ratio = close / sma200.replace(0, np.nan)

    price_vs_ema8 = pd.Series(np.nan, index=close.index)
    if ema8 is not None:
        price_vs_ema8 = close / ema8.replace(0, np.nan)

    atr_candle = pd.Series(np.nan, index=close.index)
    if atr14 is not None:
        atr_candle = (high - low) / atr14.replace(0, np.nan)

    out = pd.DataFrame({
        'close':         close,
        'day_chg':       day_chg,
        'rvol':          rvol,
        'rsi':           rsi_s,
        'macd_hist':     macd_hist,
        'ema_stack':     ema_stack,
        'price_vs_ema8': price_vs_ema8,
        'sma200_ratio':  sma200_ratio,
        'bb_pos':        bb_pos,
        'above_bbu':     above_bbu,
        'close_pos':     close_pos,
        'atr_candle':    atr_candle,
    }, index=df.index)

    return out.dropna(subset=['rsi', 'ema_stack'])


# ─── 3. Find first day of each winning run ───────────────────────────────────

def _first_winning_days(
    close: pd.Series,
    signals: pd.DataFrame,
    window: int,
    min_return: float,
    scan_from: str,
) -> pd.DatetimeIndex:
    """
    Return the index of the FIRST day in each consecutive run of days where
    the forward return over `window` bars is ≥ min_return.
    """
    fwd_ret = close.shift(-window) / close - 1
    is_win  = (fwd_ret >= min_return).reindex(signals.index, fill_value=False)

    # limit to scan_from
    is_win = is_win[is_win.index >= pd.Timestamp(scan_from)]

    if is_win.sum() == 0:
        return pd.DatetimeIndex([])

    # first day of each consecutive block of True
    shifted = is_win.shift(1, fill_value=False)
    starts  = is_win & ~shifted
    return is_win[starts].index


# ─── 4. Main analysis ─────────────────────────────────────────────────────────

def _run_reverse(
    tickers: list[str],
    raw: pd.DataFrame,
    sector_map: dict[str, str],
    window: int,
    min_return: float,
    scan_from: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Returns (winners_df, all_df) where each row is one ticker-day pair with
    all indicator values.  winners_df contains only first-day-of-winning-run rows.
    """
    winners_rows = []
    all_rows     = []

    total = len(tickers)
    found_tickers = 0

    for i, ticker in enumerate(tickers, 1):
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                if ticker not in raw.columns.get_level_values(0):
                    continue
                df = raw[ticker].dropna(how='all')
            else:
                df = raw.dropna(how='all')

            if len(df) < MIN_HISTORY + window + 5:
                continue

            signals = _compute_signals(df)
            if signals is None or signals.empty:
                continue

            sector = sector_map.get(ticker, 'Unknown')

            # all-days sample (every valid indicator day, thinned to avoid memory bloat)
            for dt, row in signals.iterrows():
                if dt >= pd.Timestamp(scan_from):
                    all_rows.append({
                        'ticker': ticker, 'date': dt, 'sector': sector,
                        **row.to_dict()
                    })

            # winning entry days
            win_dates = _first_winning_days(
                df['Close'].astype(float), signals, window, min_return, scan_from
            )
            for dt in win_dates:
                if dt in signals.index:
                    row = signals.loc[dt]
                    # forward return
                    future_idx = df.index.get_loc(dt) + window
                    fwd_close  = float(df['Close'].iloc[future_idx]) if future_idx < len(df) else np.nan
                    fwd_ret    = (fwd_close - float(df['Close'].loc[dt])) / float(df['Close'].loc[dt]) * 100
                    winners_rows.append({
                        'ticker': ticker, 'date': dt, 'sector': sector,
                        'fwd_return': round(fwd_ret, 2),
                        **row.to_dict()
                    })
                    found_tickers += 1

        except Exception:
            pass

        if i % 50 == 0 or i == total:
            print(f"  {i}/{total} tickers processed … {found_tickers} winning entry days found",
                  end='\r', flush=True)

    print()
    winners_df = pd.DataFrame(winners_rows)
    all_df     = pd.DataFrame(all_rows)
    return winners_df, all_df


# ─── 5. Pattern report ────────────────────────────────────────────────────────

def _pct(n, total):
    return f"{100*n/total:.1f}%" if total else "N/A"


def _lift_table(winners: pd.DataFrame, all_df: pd.DataFrame,
                col: str, buckets: list, labels: list) -> None:
    n_win = len(winners)
    n_all = len(all_df)
    fmt   = "  {:<18}  {:>8}  {:>8}  {:>8}  {:>8}  {:>8}"
    print(fmt.format("Bucket", "Win N", "Win %", "All %", "Lift", "Avg Ret"))
    print("  " + "─" * 68)
    for (lo, hi), lbl in zip(buckets, labels):
        w = winners[(winners[col] > lo) & (winners[col] <= hi)]
        a = all_df[(all_df[col] > lo) & (all_df[col] <= hi)]
        wp = len(w) / n_win if n_win else 0
        ap = len(a) / n_all if n_all else 0
        lift = wp / ap if ap else 0
        avg_ret = w['fwd_return'].mean() if not w.empty and 'fwd_return' in w else np.nan
        ret_str = f"{avg_ret:+.1f}%" if pd.notna(avg_ret) else "N/A"
        print(fmt.format(lbl, len(w), _pct(len(w), n_win),
                         _pct(len(a), n_all), f"{lift:.2f}×", ret_str))


def _print_report(
    winners: pd.DataFrame,
    all_df: pd.DataFrame,
    window: int,
    min_return: float,
    scan_from: str,
) -> None:
    sep = "═" * 75
    n_win   = len(winners)
    n_all   = len(all_df)
    n_ticks = winners['ticker'].nunique() if not winners.empty else 0

    print(f"\n{sep}")
    print(f"  REVERSE BACKTEST — {int(min_return*100)}%+ in {window}-day window  "
          f"(scan from {scan_from})")
    print(sep)
    print(f"  Winning entry days  : {n_win}  across {n_ticks} tickers")
    print(f"  All candidate days  : {n_all}")
    print(f"  Win day hit rate    : {_pct(n_win, n_all)}  of all days qualify")

    if winners.empty:
        print("\n  No winning entries found with these parameters.")
        return

    # ── Top 15 best-returning entries ────────────────────────────────────
    print(f"\n{sep}")
    print(f"{'TOP 15 WINNING ENTRIES (by 3-month return)':^75}")
    print(sep)
    top = winners.nlargest(15, 'fwd_return')
    fmt = "  {:<12}  {:<7}  {:>8}  {:>6}  {:>6}  {:>8}  {:>8}  {:>12}"
    print(fmt.format("Date", "Ticker", "Fwd Ret", "Day%", "RVOL", "RSI", "BB Pos", "Sector"))
    print("  " + "─" * 73)
    for _, r in top.iterrows():
        print(fmt.format(
            str(r['date'])[:10], r['ticker'],
            f"{r['fwd_return']:+.1f}%",
            f"{r['day_chg']:+.1f}%" if pd.notna(r.get('day_chg')) else "N/A",
            f"{r['rvol']:.1f}×"    if pd.notna(r.get('rvol'))    else "N/A",
            f"{r['rsi']:.0f}"      if pd.notna(r.get('rsi'))      else "N/A",
            f"{r['bb_pos']:.2f}"   if pd.notna(r.get('bb_pos'))   else "N/A",
            r.get('sector', 'N/A')[:12],
        ))

    # ── Indicator distributions ───────────────────────────────────────────
    print(f"\n{sep}")
    print(f"{'INDICATOR DISTRIBUTION ON WINNING ENTRY DAYS':^75}")
    print(sep)
    indicators = [
        ('rsi',           'RSI(14)'),
        ('rvol',          'RVOL'),
        ('day_chg',       'Day change %'),
        ('bb_pos',        'BB position (0=lower, 1=upper)'),
        ('close_pos',     'Close pos in day range'),
        ('sma200_ratio',  'Price / SMA200'),
        ('atr_candle',    'ATR candle mult'),
    ]
    pct_fmt = "  {:<30}  {:>8}  {:>8}  {:>8}  {:>8}  {:>8}  {:>8}"
    print(pct_fmt.format("Indicator", "P10", "P25", "P50", "P75", "P90", "mean"))
    print("  " + "─" * 73)
    for col, label in indicators:
        if col not in winners.columns:
            continue
        ws = winners[col].dropna()
        as_ = all_df[col].dropna() if col in all_df else pd.Series(dtype=float)
        if ws.empty:
            continue
        def qfmt(s, q):
            v = s.quantile(q)
            return f"{v:.1f}" if abs(v) < 100 else f"{v:.0f}"
        print(pct_fmt.format(
            f"  Win: {label}",
            qfmt(ws, 0.10), qfmt(ws, 0.25), qfmt(ws, 0.50),
            qfmt(ws, 0.75), qfmt(ws, 0.90), f"{ws.mean():.1f}",
        ))
        if not as_.empty:
            print(pct_fmt.format(
                f"  All: {label}",
                qfmt(as_, 0.10), qfmt(as_, 0.25), qfmt(as_, 0.50),
                qfmt(as_, 0.75), qfmt(as_, 0.90), f"{as_.mean():.1f}",
            ))
        print()

    # ── BB upper breakout: is it predictive? ─────────────────────────────
    print(f"\n{sep}")
    print(f"{'IS PRICE > BB UPPER PREDICTIVE? (current screener requires this)':^75}")
    print(sep)
    for label, cond in [
        ("Above BB upper (current filter)", winners['above_bbu'] == 1),
        ("Between BB middle & upper",       (winners['bb_pos'] > 0.5) & (winners['above_bbu'] != 1)),
        ("Below BB middle",                 winners['bb_pos'] <= 0.5),
    ]:
        subset = winners[cond]
        pct_of_wins = _pct(len(subset), n_win)
        pct_of_all  = _pct(len(all_df[all_df['above_bbu'] == (1 if 'Above BB' in label else 0)]), n_all) \
                      if 'Above BB' in label else "—"
        avg_ret = subset['fwd_return'].mean() if not subset.empty else np.nan
        ret_str = f"{avg_ret:+.1f}%" if pd.notna(avg_ret) else "N/A"
        print(f"  {label:<42}  {len(subset):>5} ({pct_of_wins}) of winners  avg={ret_str}")

    # ── Lift tables ───────────────────────────────────────────────────────
    print(f"\n{sep}")
    print(f"{'LIFT TABLE — RSI':^75}")
    print(sep)
    _lift_table(winners, all_df, 'rsi',
        [(-1,30),(30,40),(40,50),(50,55),(55,60),(60,65),(65,70),(70,75),(75,200)],
        ['< 30 (oversold)','30–40','40–50','50–55','55–60','60–65','65–70','70–75','> 75 (overbought)'])

    print(f"\n{sep}")
    print(f"{'LIFT TABLE — RVOL':^75}")
    print(sep)
    _lift_table(winners, all_df, 'rvol',
        [(-1,0.5),(0.5,1.0),(1.0,1.5),(1.5,2.0),(2.0,2.5),(2.5,3.5),(3.5,999)],
        ['< 0.5×','0.5–1.0×','1.0–1.5×','1.5–2.0×','2.0–2.5×','2.5–3.5×','> 3.5×'])

    print(f"\n{sep}")
    print(f"{'LIFT TABLE — DAY CHANGE %':^75}")
    print(sep)
    _lift_table(winners, all_df, 'day_chg',
        [(-999,-3),(-3,0),(0,1),(1,2),(2,3),(3,5),(5,10),(10,20),(20,999)],
        ['< -3% (red day)','–3 to 0%','0 to 1%','1 to 2%','2 to 3%',
         '3 to 5%','5 to 10%','10 to 20%','> 20%'])

    # ── EMA stack ─────────────────────────────────────────────────────────
    print(f"\n{sep}")
    print(f"{'EMA STACK (EMA20 > EMA50 > EMA200)':^75}")
    print(sep)
    w_stack = (winners['ema_stack'] == 1).sum()
    a_stack = (all_df['ema_stack'] == 1).sum() if 'ema_stack' in all_df else 0
    lift_stack = (w_stack/n_win) / (a_stack/n_all) if a_stack and n_all else 0
    print(f"  Stack intact in winners : {w_stack}/{n_win} ({_pct(w_stack, n_win)})  "
          f"vs all days {_pct(a_stack, n_all)}  → lift {lift_stack:.2f}×")

    # ── Seasonal pattern ─────────────────────────────────────────────────
    print(f"\n{sep}")
    print(f"{'SEASONAL PATTERN (which months produce most winning entries)':^75}")
    print(sep)
    winners['month'] = pd.to_datetime(winners['date']).dt.month
    all_df['month']  = pd.to_datetime(all_df['date']).dt.month
    mfmt = "  {:<12}  {:>8}  {:>8}  {:>8}  {:>10}"
    print(mfmt.format("Month", "Win N", "Win %", "All %", "Lift"))
    print("  " + "─" * 48)
    import calendar
    for m in range(1, 13):
        wm = (winners['month'] == m).sum()
        am = (all_df['month'] == m).sum()
        wp = wm / n_win if n_win else 0
        ap = am / n_all if n_all else 0
        lift = wp / ap if ap else 0
        print(mfmt.format(calendar.month_abbr[m], wm,
                          _pct(wm, n_win), _pct(am, n_all), f"{lift:.2f}×"))

    # ── Sector breakdown ──────────────────────────────────────────────────
    print(f"\n{sep}")
    print(f"{'TOP SECTORS (by winning entry count)':^75}")
    print(sep)
    sec_counts = winners.groupby('sector')['fwd_return'].agg(['count','mean']).sort_values('count', ascending=False)
    sfmt = "  {:<38}  {:>8}  {:>10}"
    print(sfmt.format("Sector", "Win days", "Avg Ret"))
    print("  " + "─" * 58)
    for sec, row in sec_counts.iterrows():
        print(sfmt.format(str(sec)[:38], int(row['count']), f"{row['mean']:+.1f}%"))

    # ── Suggested filter thresholds ───────────────────────────────────────
    print(f"\n{sep}")
    print(f"{'SUGGESTED FILTER CALIBRATION (from reverse backtest)':^75}")
    print(sep)
    rsi_med  = winners['rsi'].median()
    rsi_p25  = winners['rsi'].quantile(0.25)
    rsi_p75  = winners['rsi'].quantile(0.75)
    rvol_p25 = winners['rvol'].quantile(0.25)
    rvol_p75 = winners['rvol'].quantile(0.75)
    chg_p25  = winners['day_chg'].quantile(0.25)
    chg_p75  = winners['day_chg'].quantile(0.75)
    bb_above = (winners['above_bbu'] == 1).mean() * 100

    print(f"  RSI sweet spot (P25–P75 of winners)   : {rsi_p25:.0f} – {rsi_p75:.0f}  (median {rsi_med:.0f})")
    print(f"  RVOL sweet spot (P25–P75 of winners)  : {rvol_p25:.1f}× – {rvol_p75:.1f}×")
    print(f"  Day change sweet spot (P25–P75)        : {chg_p25:+.1f}% – {chg_p75:+.1f}%")
    print(f"  % of winners that were above BB upper  : {bb_above:.1f}%")
    print(f"  % of winners with EMA stack intact     : {_pct(w_stack, n_win)}")
    bb_mid_pct = ((winners['bb_pos'] > 0.5) & (winners['above_bbu'] != 1)).mean() * 100
    print(f"  % of winners between BB middle & upper : {bb_mid_pct:.1f}%")
    print()
    print("  INTERPRETATION:")
    if bb_above < 20:
        print("  → Most winners were NOT above BB upper at entry — current BB breakout")
        print("    filter may be too strict. Consider: price > BB middle as alternative.")
    else:
        print(f"  → {bb_above:.0f}% of winners were above BB upper at entry — BB filter is well-targeted.")
    print(f"  → RSI range {rsi_p25:.0f}–{rsi_p75:.0f} covers the core winning zone.")
    if rvol_p25 < 2.5:
        print(f"  → RVOL as low as {rvol_p25:.1f}× in winners — consider lowering MIN_RVOL from 2.5×.")
    print(sep)


# ─── 6. Recovery screener validation ─────────────────────────────────────────

def _run_validate_recovery(
    tickers: list[str],
    raw: pd.DataFrame,
    sector_map: dict[str, str],
    scan_from: str = '2021-01-01',
) -> None:
    """
    Sweep recovery/mean-reversion filter thresholds across 5-year S&P 500 data.
    For each combination, measures 2-week (10 bar) and 3-month (63 bar) forward
    returns to find what achieves ≥95% win rate with ≥45% avg 3-month return.
    """
    print(f"\n  Collecting ticker-day data with forward returns (scan_from={scan_from}) …")

    rows: list[dict] = []
    total = len(tickers)

    for i, ticker in enumerate(tickers, 1):
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                if ticker not in raw.columns.get_level_values(0):
                    continue
                df = raw[ticker].dropna(how='all')
            else:
                df = raw.dropna(how='all')

            if len(df) < MIN_HISTORY + 63 + 5:
                continue

            signals = _compute_signals(df)
            if signals is None or signals.empty:
                continue

            close  = df['Close'].astype(float)
            fwd_10 = (close.shift(-10) / close - 1) * 100   # 2-week return %
            fwd_21 = (close.shift(-21) / close - 1) * 100   # 1-month return %
            fwd_63 = (close.shift(-63) / close - 1) * 100   # 3-month return %

            sector = sector_map.get(ticker, 'Unknown')

            for dt in signals.index:
                if dt < pd.Timestamp(scan_from):
                    continue
                if dt not in fwd_10.index or pd.isna(fwd_10.loc[dt]):
                    continue
                row = signals.loc[dt]
                rows.append({
                    'ticker':       ticker,
                    'date':         dt,
                    'sector':       sector,
                    'rsi':          float(row['rsi'])          if pd.notna(row.get('rsi'))          else np.nan,
                    'rvol':         float(row['rvol'])          if pd.notna(row.get('rvol'))          else np.nan,
                    'day_chg':      float(row['day_chg'])       if pd.notna(row.get('day_chg'))       else np.nan,
                    'sma200_ratio': float(row['sma200_ratio'])  if pd.notna(row.get('sma200_ratio'))  else np.nan,
                    'bb_pos':       float(row['bb_pos'])        if pd.notna(row.get('bb_pos'))        else np.nan,
                    'ema_stack':    float(row['ema_stack'])     if pd.notna(row.get('ema_stack'))     else np.nan,
                    'macd_hist':    float(row['macd_hist'])     if pd.notna(row.get('macd_hist'))     else np.nan,
                    'fwd_10':       float(fwd_10.loc[dt]),
                    'fwd_21':       float(fwd_21.loc[dt]) if pd.notna(fwd_21.loc[dt]) else np.nan,
                    'fwd_63':       float(fwd_63.loc[dt]) if pd.notna(fwd_63.loc[dt]) else np.nan,
                })
        except Exception:
            pass

        if i % 50 == 0 or i == total:
            print(f"  {i}/{total} tickers … {len(rows):,} rows", end='\r', flush=True)

    print()

    if not rows:
        print("No data collected.")
        return

    df_all = pd.DataFrame(rows)
    df_all['date'] = pd.to_datetime(df_all['date'])
    n_days_total = df_all['date'].dt.date.nunique()
    print(f"  Total ticker-days collected: {len(df_all):,}  across {n_days_total} trading days\n")

    # Threshold grid: (max_rsi, max_day_chg%, min_rvol, min_sma200, need_ema_stack, label)
    configs = [
        # ── Extremely strict — highest expected accuracy ────────────────────────
        (25, -7.0, 4.0, 0.75, True,  "RSI<25 | day<-7% | RVOL>4× | SMA200>75% | stack"),
        (25, -5.0, 3.5, 0.75, True,  "RSI<25 | day<-5% | RVOL>3.5× | SMA200>75% | stack"),
        (30, -7.0, 4.0, 0.75, True,  "RSI<30 | day<-7% | RVOL>4× | SMA200>75% | stack"),
        (30, -5.0, 3.5, 0.75, True,  "RSI<30 | day<-5% | RVOL>3.5× | SMA200>75% | stack"),
        (30, -5.0, 3.5, 0.70, True,  "RSI<30 | day<-5% | RVOL>3.5× | SMA200>70% | stack"),
        # ── Strict — no EMA stack requirement ──────────────────────────────────
        (25, -5.0, 3.5, 0.75, False, "RSI<25 | day<-5% | RVOL>3.5× | SMA200>75%"),
        (30, -7.0, 3.5, 0.75, False, "RSI<30 | day<-7% | RVOL>3.5× | SMA200>75%"),
        (30, -5.0, 3.5, 0.75, False, "RSI<30 | day<-5% | RVOL>3.5× | SMA200>75%"),
        (30, -5.0, 3.5, 0.70, False, "RSI<30 | day<-5% | RVOL>3.5× | SMA200>70%"),
        (30, -5.0, 3.0, 0.70, False, "RSI<30 | day<-5% | RVOL>3× | SMA200>70%"),
        (30, -5.0, 2.5, 0.70, False, "RSI<30 | day<-5% | RVOL>2.5× | SMA200>70%"),
        # ── Moderate strict ─────────────────────────────────────────────────────
        (35, -5.0, 3.5, 0.70, False, "RSI<35 | day<-5% | RVOL>3.5× | SMA200>70%"),
        (35, -5.0, 3.0, 0.70, False, "RSI<35 | day<-5% | RVOL>3× | SMA200>70%"),
        (35, -5.0, 2.5, 0.70, False, "RSI<35 | day<-5% | RVOL>2.5× | SMA200>70%"),
        (35, -3.0, 2.5, 0.70, False, "RSI<35 | day<-3% | RVOL>2.5× | SMA200>70%"),
        (40, -5.0, 2.5, 0.65, False, "RSI<40 | day<-5% | RVOL>2.5× | SMA200>65%"),
        (40, -3.0, 2.0, 0.65, False, "RSI<40 | day<-3% | RVOL>2× | SMA200>65%"),
        # ── Baseline from original reverse backtest findings ────────────────────
        (45, -3.0, 1.5, 0.65, False, "RSI<45 | day<-3% | RVOL>1.5× | SMA200>65% [baseline]"),
    ]

    sep = "═" * 96
    print(f"{sep}")
    print(f"  RECOVERY SCREENER THRESHOLD SWEEP  (5-year S&P 500, scan from {scan_from})")
    print(f"  Target: ≥95% 2-week accuracy  |  ≥45% average 3-month return")
    print(sep)

    hdr = "  {:<52}  {:>5}  {:>7}  {:>7}  {:>7}  {:>7}  {:>7}"
    print(hdr.format("Config", "N", "2wkWin%", "2wkAvg", "3moWin%", "3moAvg", "Sig/Day"))
    print("  " + "─" * 100)

    results = []
    for max_rsi, max_day_chg, min_rvol, min_sma200, need_stack, label in configs:
        mask = (
            df_all['rsi'].notna()          & (df_all['rsi']          <  max_rsi)     &
            df_all['day_chg'].notna()      & (df_all['day_chg']       <  max_day_chg) &
            df_all['rvol'].notna()         & (df_all['rvol']          >  min_rvol)    &
            df_all['sma200_ratio'].notna() & (df_all['sma200_ratio']  >  min_sma200)
        )
        if need_stack:
            mask = mask & (df_all['ema_stack'] == 1)

        subset = df_all[mask].dropna(subset=['fwd_10'])
        n = len(subset)

        if n == 0:
            print(hdr.format(label[:52], 0, "N/A", "N/A", "N/A", "N/A", "N/A"))
            continue

        win_2wk = (subset['fwd_10'] > 0).mean() * 100
        avg_2wk = subset['fwd_10'].mean()
        sub3m   = subset.dropna(subset=['fwd_63'])
        win_3mo = (sub3m['fwd_63'] > 0).mean() * 100 if not sub3m.empty else np.nan
        avg_3mo = sub3m['fwd_63'].mean() if not sub3m.empty else np.nan

        n_unique = subset['date'].dt.date.nunique()
        spd = n / n_unique if n_unique else 0

        results.append({
            'win_2wk': win_2wk, 'win_3mo': win_3mo, 'avg_3mo': avg_3mo,
            'n': n, 'spd': spd, 'label': label, 'subset': subset,
            'max_rsi': max_rsi, 'max_day_chg': max_day_chg,
            'min_rvol': min_rvol, 'min_sma200': min_sma200, 'need_stack': need_stack,
        })

        star = " ★" if (pd.notna(win_3mo) and win_3mo >= 70) else ""
        print(hdr.format(
            label[:52], n,
            f"{win_2wk:.1f}%",
            f"{avg_2wk:+.2f}%",
            f"{win_3mo:.1f}%{star}" if pd.notna(win_3mo) else "N/A",
            f"{avg_3mo:+.2f}%" if pd.notna(avg_3mo) else "N/A",
            f"{spd:.2f}",
        ))

    # ── Find the best config by 3-month win rate (min 20 signals for reliability) ──
    valid = [r for r in results if r['n'] >= 20 and pd.notna(r.get('win_3mo'))]
    if not valid:
        valid = [r for r in results if r['n'] >= 5]
    if not valid:
        print("\n  No config produced ≥5 signals. Try relaxing thresholds or a longer scan_from window.")
        return

    best = max(valid, key=lambda r: r.get('win_3mo') or r['win_2wk'])
    subset = best['subset']

    print(f"\n{sep}")
    print(f"  BEST CONFIG (by 3-month win rate): {best['label']}")
    print(f"  3-month win rate: {best.get('win_3mo', 'N/A'):.1f}%  |  "
          f"2-week win rate: {best['win_2wk']:.1f}%  |  "
          f"N={best['n']}  |  {best['spd']:.2f} signals/day")
    print(sep)

    # ── Return distribution ───────────────────────────────────────────────────
    bins_def = [(-999,-10),(-10,-5),(-5,0),(0,5),(5,10),(10,20),(20,999)]
    bin_lbl  = ["< -10%","–10 to –5%","–5 to 0%","0 to +5%","+5 to +10%","+10 to +20%","> +20%"]
    print(f"\n  2-week return distribution (best config):")
    for (lo, hi), lbl in zip(bins_def, bin_lbl):
        n_b = int(((subset['fwd_10'] > lo) & (subset['fwd_10'] <= hi)).sum())
        bar = "█" * min(n_b, 50)
        print(f"    {lbl:<14}  {bar}  ({n_b})")

    # ── 3-month return for same signals ──────────────────────────────────────
    sub3m = subset.dropna(subset=['fwd_63'])
    if not sub3m.empty:
        n_pos_3m = int((sub3m['fwd_63'] > 0).sum())
        n_45_3m  = int((sub3m['fwd_63'] >= 45).sum())
        print(f"\n  3-month forward return (same signals, n={len(sub3m)}):")
        print(f"    Positive     : {n_pos_3m}/{len(sub3m)} ({100*n_pos_3m/len(sub3m):.1f}%)")
        print(f"    ≥ 45%        : {n_45_3m}/{len(sub3m)} ({100*n_45_3m/len(sub3m):.1f}%)")
        print(f"    Avg return   : {sub3m['fwd_63'].mean():+.2f}%")
        print(f"    Median return: {sub3m['fwd_63'].median():+.2f}%")
        print(f"    Best         : {sub3m.loc[sub3m['fwd_63'].idxmax(), 'ticker']}  {sub3m['fwd_63'].max():+.1f}%")
        print(f"    Worst        : {sub3m.loc[sub3m['fwd_63'].idxmin(), 'ticker']}  {sub3m['fwd_63'].min():+.1f}%")

    # ── Sector breakdown ──────────────────────────────────────────────────────
    sec_stats = subset.groupby('sector').agg(
        n=('fwd_10', 'count'),
        win_pct=('fwd_10', lambda x: (x > 0).mean() * 100),
        avg_ret=('fwd_10', 'mean'),
    ).sort_values('n', ascending=False)
    print(f"\n  Sector breakdown (2-week returns):")
    sfmt = "    {:<38}  {:>5}  {:>8}  {:>8}"
    print(sfmt.format("Sector", "N", "Win%", "Avg Ret"))
    print("    " + "─" * 62)
    for sec, row in sec_stats.iterrows():
        print(sfmt.format(str(sec)[:38], int(row['n']), f"{row['win_pct']:.1f}%", f"{row['avg_ret']:+.2f}%"))

    # ── Sample of recent signals ──────────────────────────────────────────────
    print(f"\n  Recent signals (sorted by date desc):")
    recent = subset.sort_values('date', ascending=False).head(20)
    rfmt = "  {:<12}  {:<7}  {:>5}  {:>7}  {:>5}  {:>8}  {:>9}"
    print(rfmt.format("Date", "Ticker", "RSI", "Day%", "RVOL", "2wkRet", "3moRet"))
    print("  " + "─" * 58)
    for _, r in recent.iterrows():
        r3 = f"{r['fwd_63']:+.1f}%" if pd.notna(r.get('fwd_63')) else "N/A"
        print(rfmt.format(
            str(r['date'])[:10], r['ticker'],
            f"{r['rsi']:.0f}",
            f"{r['day_chg']:+.1f}%",
            f"{r['rvol']:.1f}×",
            f"{r['fwd_10']:+.2f}%",
            r3,
        ))

    # ── Print calibrated RECOVERY_CONFIG ─────────────────────────────────────
    b = best
    stack_str = "True   # EMA20 > EMA50 > EMA200" if b['need_stack'] else "False  # not required for recovery plays"
    print(f"\n{sep}")
    print(f"  CALIBRATED RECOVERY_CONFIG — paste into recovery_scanner.py")
    print(sep)
    print(f"  RECOVERY_CONFIG = {{")
    print(f"      'MAX_RSI':          {b['max_rsi']},    # RSI must be this oversold")
    print(f"      'MAX_DAY_CHG':      {b['max_day_chg']},  # entry day change must be < this %")
    print(f"      'MIN_RVOL':         {b['min_rvol']},   # min relative volume")
    print(f"      'MIN_SMA200_RATIO': {b['min_sma200']},  # price must be > this × SMA200")
    print(f"      'REQUIRE_EMA_STACK': {stack_str},")
    print(f"  }}")
    print(f"\n  Actual 3-month win rate: {b.get('win_3mo', b['win_2wk']):.1f}%  (target ≥70%)")
    print(f"  Actual 2-week win rate : {b['win_2wk']:.1f}%")
    print(f"  Signals per day        : {b['spd']:.2f}  (target 1–4)")
    print(sep)
    print()


# ─── 7. Momentum screener validation ─────────────────────────────────────────

def _run_validate_momentum(
    tickers: list[str],
    raw: pd.DataFrame,
    sector_map: dict[str, str],
    scan_from: str = '2021-01-01',
) -> None:
    """
    Sweep momentum filter thresholds (strict → relaxed) across 5-year S&P 500
    data and measure 3-month (63-bar) forward returns for every matching day.
    Answers: can momentum breakout signals deliver ≥70% 3-month accuracy?
    """
    print(f"\n  Collecting ticker-day data (scan_from={scan_from}) …")

    rows: list[dict] = []
    total = len(tickers)

    for i, ticker in enumerate(tickers, 1):
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                if ticker not in raw.columns.get_level_values(0):
                    continue
                df = raw[ticker].dropna(how='all')
            else:
                df = raw.dropna(how='all')

            if len(df) < MIN_HISTORY + 63 + 5:
                continue

            signals = _compute_signals(df)
            if signals is None or signals.empty:
                continue

            close  = df['Close'].astype(float)
            fwd_63 = (close.shift(-63) / close - 1) * 100

            sector = sector_map.get(ticker, 'Unknown')

            for dt in signals.index:
                if dt < pd.Timestamp(scan_from):
                    continue
                if dt not in fwd_63.index or pd.isna(fwd_63.loc[dt]):
                    continue
                row = signals.loc[dt]
                rows.append({
                    'ticker':       ticker,
                    'date':         dt,
                    'sector':       sector,
                    'rsi':          float(row['rsi'])          if pd.notna(row.get('rsi'))          else np.nan,
                    'rvol':         float(row['rvol'])          if pd.notna(row.get('rvol'))          else np.nan,
                    'day_chg':      float(row['day_chg'])       if pd.notna(row.get('day_chg'))       else np.nan,
                    'sma200_ratio': float(row['sma200_ratio'])  if pd.notna(row.get('sma200_ratio'))  else np.nan,
                    'bb_pos':       float(row['bb_pos'])        if pd.notna(row.get('bb_pos'))        else np.nan,
                    'above_bbu':    float(row['above_bbu'])     if pd.notna(row.get('above_bbu'))     else np.nan,
                    'ema_stack':    float(row['ema_stack'])     if pd.notna(row.get('ema_stack'))     else np.nan,
                    'close_pos':    float(row['close_pos'])     if pd.notna(row.get('close_pos'))     else np.nan,
                    'macd_hist':    float(row['macd_hist'])     if pd.notna(row.get('macd_hist'))     else np.nan,
                    'fwd_63':       float(fwd_63.loc[dt]),
                })
        except Exception:
            pass

        if i % 50 == 0 or i == total:
            print(f"  {i}/{total} tickers … {len(rows):,} rows", end='\r', flush=True)

    print()

    if not rows:
        print("No data collected.")
        return

    df_all = pd.DataFrame(rows)
    df_all['date'] = pd.to_datetime(df_all['date'])
    n_days_total = df_all['date'].dt.date.nunique()
    print(f"  Total ticker-days collected: {len(df_all):,}  across {n_days_total} trading days\n")

    # Threshold grid: (min_rsi, max_rsi, min_day_chg, min_rvol, min_sma200, need_bbu, min_close_pos, label)
    configs = [
        # ── Strict (≈ current scanner.py logic) ────────────────────────────────
        (55, 70,  4.0, 2.5, 0.80, True,  0.65, "RSI 55–70 | day>4% | RVOL>2.5× | BB upper | sma>80%"),
        (55, 70,  4.0, 2.5, 0.75, True,  0.65, "RSI 55–70 | day>4% | RVOL>2.5× | BB upper | sma>75%"),
        (55, 70,  4.0, 2.0, 0.75, True,  0.60, "RSI 55–70 | day>4% | RVOL>2× | BB upper | sma>75%"),
        # ── Strict, no BB upper requirement ────────────────────────────────────
        (55, 70,  4.0, 2.5, 0.80, False, 0.65, "RSI 55–70 | day>4% | RVOL>2.5× | sma>80%"),
        (55, 70,  4.0, 2.0, 0.75, False, 0.60, "RSI 55–70 | day>4% | RVOL>2× | sma>75%"),
        (55, 70,  3.0, 2.0, 0.75, False, 0.60, "RSI 55–70 | day>3% | RVOL>2× | sma>75%"),
        # ── Moderate ───────────────────────────────────────────────────────────
        (50, 75,  3.0, 2.0, 0.75, False, 0.60, "RSI 50–75 | day>3% | RVOL>2× | sma>75%"),
        (50, 75,  3.0, 1.5, 0.75, False, 0.55, "RSI 50–75 | day>3% | RVOL>1.5× | sma>75%"),
        (50, 75,  2.0, 1.5, 0.70, False, 0.55, "RSI 50–75 | day>2% | RVOL>1.5× | sma>70%"),
        # ── Relaxed ────────────────────────────────────────────────────────────
        (45, 80,  2.0, 1.5, 0.70, False, 0.55, "RSI 45–80 | day>2% | RVOL>1.5× | sma>70%"),
        (45, 80,  2.0, 1.0, 0.70, False, 0.50, "RSI 45–80 | day>2% | RVOL>1× | sma>70%"),
        (40, 80,  1.0, 1.0, 0.65, False, 0.50, "RSI 40–80 | day>1% | RVOL>1× | sma>65%"),
    ]

    sep = "═" * 100
    print(f"{sep}")
    print(f"  MOMENTUM SCREENER THRESHOLD SWEEP  (5-year S&P 500, scan from {scan_from})")
    print(f"  Target: ≥70% 3-month accuracy  |  ≥40% average 3-month return  |  EMA stack required for all")
    print(sep)

    hdr = "  {:<52}  {:>6}  {:>8}  {:>8}  {:>7}"
    print(hdr.format("Config", "N", "3moWin%", "3moAvg", "Sig/Day"))
    print("  " + "─" * 84)

    results = []
    for min_rsi, max_rsi, min_day_chg, min_rvol, min_sma200, need_bbu, min_close, label in configs:
        mask = (
            df_all['rsi'].notna()          & (df_all['rsi']          >= min_rsi)      &
            df_all['rsi'].notna()          & (df_all['rsi']          <= max_rsi)      &
            df_all['day_chg'].notna()      & (df_all['day_chg']       >= min_day_chg) &
            df_all['rvol'].notna()         & (df_all['rvol']          >= min_rvol)    &
            df_all['sma200_ratio'].notna() & (df_all['sma200_ratio']  >= min_sma200)  &
            df_all['ema_stack'].notna()    & (df_all['ema_stack']     == 1)           &
            df_all['close_pos'].notna()    & (df_all['close_pos']     >= min_close)
        )
        if need_bbu:
            mask = mask & (df_all['above_bbu'] == 1)

        subset = df_all[mask].dropna(subset=['fwd_63'])
        n = len(subset)

        if n == 0:
            print(hdr.format(label[:52], 0, "N/A", "N/A", "N/A"))
            continue

        win_3mo = (subset['fwd_63'] > 0).mean() * 100
        avg_3mo = subset['fwd_63'].mean()

        n_unique = subset['date'].dt.date.nunique()
        spd = n / n_unique if n_unique else 0

        results.append({
            'win_3mo': win_3mo, 'avg_3mo': avg_3mo, 'n': n, 'spd': spd,
            'label': label, 'subset': subset,
            'min_rsi': min_rsi, 'max_rsi': max_rsi, 'min_day_chg': min_day_chg,
            'min_rvol': min_rvol, 'min_sma200': min_sma200,
            'need_bbu': need_bbu, 'min_close': min_close,
        })

        star = " ★" if win_3mo >= 70 else ""
        print(hdr.format(
            label[:52], n,
            f"{win_3mo:.1f}%{star}",
            f"{avg_3mo:+.2f}%",
            f"{spd:.2f}",
        ))

    # ── Find best by 3-month win rate (min 20 signals) ───────────────────────
    valid = [r for r in results if r['n'] >= 20]
    if not valid:
        print("\n  No config produced ≥20 signals.")
        return

    best = max(valid, key=lambda r: r['win_3mo'])
    subset = best['subset']

    print(f"\n{sep}")
    print(f"  BEST MOMENTUM CONFIG: {best['label']}")
    print(f"  3-month win rate: {best['win_3mo']:.1f}%  |  avg return: {best['avg_3mo']:+.2f}%  |  "
          f"N={best['n']}  |  {best['spd']:.2f} signals/day")
    print(sep)

    # ── 3-month return distribution ───────────────────────────────────────────
    bins_def = [(-999,-20),(-20,-10),(-10,0),(0,10),(10,20),(20,40),(40,999)]
    bin_lbl  = ["< -20%","–20 to –10%","–10 to 0%","0 to +10%","+10 to +20%","+20 to +40%","> +40%"]
    print(f"\n  3-month return distribution (best config):")
    for (lo, hi), lbl in zip(bins_def, bin_lbl):
        n_b = int(((subset['fwd_63'] > lo) & (subset['fwd_63'] <= hi)).sum())
        bar = "█" * min(n_b, 50)
        print(f"    {lbl:<15}  {bar}  ({n_b})")

    n_40 = int((subset['fwd_63'] >= 40).sum())
    print(f"\n  ≥40% 3-month return : {n_40}/{len(subset)} ({100*n_40/len(subset):.1f}% of signals)")
    print(f"  Avg return          : {best['avg_3mo']:+.2f}%")
    print(f"  Median return       : {subset['fwd_63'].median():+.2f}%")
    print(f"  Best signal         : {subset.loc[subset['fwd_63'].idxmax(), 'ticker']}  {subset['fwd_63'].max():+.1f}%")
    print(f"  Worst signal        : {subset.loc[subset['fwd_63'].idxmin(), 'ticker']}  {subset['fwd_63'].min():+.1f}%")

    # ── Sector breakdown ──────────────────────────────────────────────────────
    sec_stats = subset.groupby('sector').agg(
        n=('fwd_63', 'count'),
        win_pct=('fwd_63', lambda x: (x > 0).mean() * 100),
        avg_ret=('fwd_63', 'mean'),
    ).sort_values('n', ascending=False)
    print(f"\n  Sector breakdown (3-month returns):")
    sfmt = "    {:<38}  {:>5}  {:>8}  {:>8}"
    print(sfmt.format("Sector", "N", "Win%", "Avg Ret"))
    print("    " + "─" * 62)
    for sec, row in sec_stats.head(10).iterrows():
        print(sfmt.format(str(sec)[:38], int(row['n']), f"{row['win_pct']:.1f}%", f"{row['avg_ret']:+.2f}%"))

    # ── Recent sample signals ─────────────────────────────────────────────────
    print(f"\n  Top 20 signals by 3-month return:")
    top = subset.nlargest(20, 'fwd_63')
    rfmt = "  {:<12}  {:<7}  {:>5}  {:>6}  {:>5}  {:>9}"
    print(rfmt.format("Date", "Ticker", "RSI", "Day%", "RVOL", "3moRet"))
    print("  " + "─" * 50)
    for _, r in top.iterrows():
        print(rfmt.format(
            str(r['date'])[:10], r['ticker'],
            f"{r['rsi']:.0f}", f"{r['day_chg']:+.1f}%",
            f"{r['rvol']:.1f}×", f"{r['fwd_63']:+.2f}%",
        ))

    # ── Calibrated CONFIG ─────────────────────────────────────────────────────
    b = best
    bbu_str = "True   # price must be above BB upper" if b['need_bbu'] else "False  # BB upper not required"
    print(f"\n{sep}")
    print(f"  CALIBRATED MOMENTUM CONFIG — comparison vs recovery screener")
    print(sep)
    print(f"  {'Strategy':<30}  {'3moWin%':>8}  {'3moAvg':>8}  {'Sig/Day':>8}")
    print(f"  {'─'*58}")
    print(f"  {'Momentum (best above)'::<30}  {b['win_3mo']:>7.1f}%  {b['avg_3mo']:>+7.2f}%  {b['spd']:>8.2f}")
    print(f"  {'Recovery (RSI<25,day<-5%,RVOL>3.5×)'::<30}  {'68.0':>7}%  {'+6.72':>8}%  {'1.00':>8}")
    print()
    if b['win_3mo'] >= 70:
        print(f"  ✓ MOMENTUM WINS — update scanner.py with:")
    else:
        print(f"  ✗ Neither strategy hits 70% — recovery is best available at 68%.")
        print(f"    Best momentum config below; update scanner.py with recovery or momentum:")
    print(f"    MIN_RSI          = {b['min_rsi']}")
    print(f"    MAX_RSI          = {b['max_rsi']}")
    print(f"    MIN_DAY_CHANGE   = {b['min_day_chg']}%")
    print(f"    MIN_RVOL         = {b['min_rvol']}×")
    print(f"    MIN_SMA200_RATIO = {b['min_sma200']}")
    print(f"    REQUIRE_BB_UPPER = {bbu_str}")
    print(f"    MIN_CLOSE_POS    = {b['min_close']}")
    print(sep)
    print()


# ─── 8. Entry point ──────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--window',     type=int,   default=63,
                        help='Forward return window in trading days (default 63 = 3 months)')
    parser.add_argument('--min-return', type=float, default=0.45,
                        help='Minimum forward return to count as a winner (default 0.45 = 45%%)')
    parser.add_argument('--scan-from',  default='2021-01-01',
                        help='Only flag winning entries from this date onwards')
    parser.add_argument('--refresh',    action='store_true',
                        help='Force fresh data download, overwriting cache')
    parser.add_argument('--validate-recovery', action='store_true',
                        help='Sweep recovery screener thresholds (3-month hold accuracy)')
    parser.add_argument('--validate-momentum', action='store_true',
                        help='Sweep momentum screener thresholds (3-month hold accuracy)')
    args, _ = parser.parse_known_args()

    tickers, sector_map = _get_sp500()
    raw = _load_or_download(tickers, refresh=args.refresh)

    if args.validate_recovery:
        print(f"\nRecovery Screener Validation — 3-month hold accuracy sweep\n")
        _run_validate_recovery(tickers, raw, sector_map, scan_from=args.scan_from)
        return

    if args.validate_momentum:
        print(f"\nMomentum Screener Validation — 3-month hold accuracy sweep\n")
        _run_validate_momentum(tickers, raw, sector_map, scan_from=args.scan_from)
        return

    print(f"\nReverse Backtest: find S&P 500 stocks with ≥{int(args.min_return*100)}% return "
          f"in {args.window} trading days\n")
    print(f"\nAnalysing {len(tickers)} tickers — extracting TA indicators …")
    winners, all_df = _run_reverse(
        tickers, raw, sector_map,
        window=args.window,
        min_return=args.min_return,
        scan_from=args.scan_from,
    )

    _print_report(winners, all_df, args.window, args.min_return, args.scan_from)


if __name__ == '__main__':
    main()
