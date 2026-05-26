"""
Big-move research: identify which signals precede 30%+ gains in 1–2 months.

10-year S&P 500 backtest — finds every ticker-date where the forward 42-day
(~2 month) return was ≥30%, then analyses what the stock looked like on entry
day using technical indicators and (optionally) alternative data.

Usage:
    python3 -m backend.bigmove_research                     # tech signals, 42-day, 30%
    python3 -m backend.bigmove_research --with-alt-data     # include SEC/FINRA/earnings
    python3 -m backend.bigmove_research --window 21         # 1-month window
    python3 -m backend.bigmove_research --min-return 0.25   # 25% threshold
    python3 -m backend.bigmove_research --refresh           # re-download 10yr data
    python3 -m backend.bigmove_research --ticker AAPL       # single-ticker deep dive
"""
import sys, os, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pathlib
import calendar
import numpy as np
import pandas as pd
import yfinance as yf

from backend.reverse_backtest import (
    _get_sp500, _compute_signals, _first_winning_days, MIN_HISTORY
)

# ─── Configuration ────────────────────────────────────────────────────────────

DOWNLOAD_START = "2015-01-01"   # 200 bars of warmup before Jan 2016
DOWNLOAD_END   = "2026-05-25"

CACHE_DIR  = pathlib.Path(__file__).parent / "_backtest_cache"
CACHE_FILE = CACHE_DIR / "sp500_10yr_ohlcv.pkl"

MIN_N = 30   # minimum samples required to report any result (overfitting guard)


# ─── 1. Data download ────────────────────────────────────────────────────────

def _load_or_download(tickers: list[str], refresh: bool = False) -> pd.DataFrame:
    CACHE_DIR.mkdir(exist_ok=True)
    if CACHE_FILE.exists() and not refresh:
        print(f"Loading cached 10yr data from {CACHE_FILE} …")
        return pd.read_pickle(CACHE_FILE)
    print(f"Downloading {len(tickers)}-ticker OHLCV ({DOWNLOAD_START} → {DOWNLOAD_END}) …")
    print("This may take 20–30 minutes — data is cached after the first run.")
    raw = yf.download(tickers, start=DOWNLOAD_START, end=DOWNLOAD_END,
                      group_by='ticker', progress=True, threads=True)
    raw.to_pickle(CACHE_FILE)
    print(f"Cached to {CACHE_FILE} ({CACHE_FILE.stat().st_size // 1_048_576} MB)")
    return raw


# ─── 2. Move classification heuristic ───────────────────────────────────────

def _classify_move(row: pd.Series) -> str:
    """Label move type from entry-day signals (rough heuristic)."""
    rvol    = row.get("rvol",    np.nan)
    day_chg = row.get("day_chg", np.nan)
    rsi     = row.get("rsi",     np.nan)

    if pd.notna(rvol) and pd.notna(day_chg):
        if rvol > 3.5 and day_chg > 4.0:
            return "catalyst"      # big up day on huge volume → earnings/news
        if rvol > 3.0 and day_chg < -4.0:
            return "recovery"      # panic selloff entry
    if pd.notna(rsi) and rsi < 40:
        return "recovery"          # oversold bounce
    if pd.notna(rvol) and rvol > 2.0:
        return "momentum"          # elevated volume breakout
    return "gradual"


# ─── 3. Days-to-peak helper ──────────────────────────────────────────────────

def _days_to_peak(close: pd.Series, entry_loc: int, window: int,
                  min_return: float) -> int:
    """
    Return how many bars after entry_loc the stock first hit min_return.
    If it never crosses within window, returns window.
    """
    entry_price = close.iloc[entry_loc]
    for offset in range(1, window + 1):
        idx = entry_loc + offset
        if idx >= len(close):
            break
        if (close.iloc[idx] / entry_price - 1) >= min_return:
            return offset
    return window


# ─── 4. Main data collection ─────────────────────────────────────────────────

def _find_bigmoves(
    tickers: list[str],
    raw: pd.DataFrame,
    sector_map: dict[str, str],
    window: int,
    min_return: float,
    scan_from: str,
    single_ticker: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Returns (winners_df, all_df).
    winners_df: one row per first-day-of-winning-run entry.
    all_df:     one row per valid indicator day (thinned sample for lift calc).
    """
    winners_rows: list[dict] = []
    all_rows:     list[dict] = []
    found = 0
    target = tickers if single_ticker is None else [single_ticker]

    for i, ticker in enumerate(target, 1):
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

            close  = df['Close'].astype(float)
            sector = sector_map.get(ticker, 'Unknown')

            # all-days sample (every valid indicator day for base-rate computation)
            for dt, sig_row in signals.iterrows():
                if dt >= pd.Timestamp(scan_from):
                    all_rows.append({
                        'ticker': ticker, 'date': dt, 'sector': sector,
                        **sig_row.to_dict()
                    })

            # winning entry days
            win_dates = _first_winning_days(close, signals, window, min_return, scan_from)
            for dt in win_dates:
                if dt not in signals.index:
                    continue
                sig_row  = signals.loc[dt]
                entry_loc = close.index.get_loc(dt)
                fwd_loc   = entry_loc + window
                fwd_close = float(close.iloc[fwd_loc]) if fwd_loc < len(close) else np.nan
                fwd_ret   = ((fwd_close - float(close.loc[dt])) / float(close.loc[dt]) * 100
                             if pd.notna(fwd_close) else np.nan)
                d2peak = _days_to_peak(close, entry_loc, window, min_return)
                winners_rows.append({
                    'ticker':      ticker,
                    'date':        dt,
                    'sector':      sector,
                    'fwd_return':  round(fwd_ret, 2) if pd.notna(fwd_ret) else np.nan,
                    'days_to_peak': d2peak,
                    'move_type':   _classify_move(sig_row),
                    **sig_row.to_dict(),
                })
                found += 1

        except Exception:
            pass

        if i % 50 == 0 or i == len(target):
            print(f"  {i}/{len(target)} tickers … {found} winning entry days found",
                  end='\r', flush=True)

    print()
    return pd.DataFrame(winners_rows), pd.DataFrame(all_rows)


# ─── 5. Alt-data enrichment ──────────────────────────────────────────────────

def _enrich_with_alt_data(
    winners_df: pd.DataFrame,
    all_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Join SEC insider, FINRA short, earnings surprise columns onto both DataFrames."""
    from backend.alt_data import SecEdgarFetcher, EarningsFetcher, FinraShortFetcher

    tickers = list(winners_df['ticker'].unique()) if not winners_df.empty else []

    # ── SEC Form 4 insider buying (past 60 days from each entry date) ──────
    print("  Fetching SEC Form 4 insider data …")
    sec = SecEdgarFetcher()
    insider_map: dict[str, pd.DataFrame] = {}
    for t in tickers:
        insider_map[t] = sec.get(t)

    def _insider_summary(ticker: str, as_of: pd.Timestamp) -> dict:
        df = insider_map.get(ticker, pd.DataFrame())
        if df.empty:
            return {"insider_buy_count": 0, "insider_buy_value": 0.0}
        cutoff = as_of - pd.Timedelta(days=60)
        recent = df[(df["date"] >= cutoff) & (df["date"] < as_of)]
        return {
            "insider_buy_count": len(recent),
            "insider_buy_value": float(recent["total_value"].sum()),
        }

    # ── Earnings surprise history ──────────────────────────────────────────
    print("  Fetching earnings history …")
    ef = EarningsFetcher()
    earnings_map: dict[str, pd.DataFrame] = {}
    for t in tickers:
        earnings_map[t] = ef.get(t)

    def _earnings_features(ticker: str, as_of: pd.Timestamp) -> dict:
        df = earnings_map.get(ticker, pd.DataFrame())
        if df.empty:
            return {"beat_streak": 0, "avg_surprise_4q": np.nan,
                    "days_since_last_earnings": np.nan}
        past = df[df["date"] < as_of]
        if past.empty:
            return {"beat_streak": 0, "avg_surprise_4q": np.nan,
                    "days_since_last_earnings": np.nan}
        last = past.iloc[-1]
        days_since = (as_of - last["date"]).days
        return {
            "beat_streak":           int(last["beat_streak"]),
            "avg_surprise_4q":       float(last["avg_surprise_4q"]) if pd.notna(last.get("avg_surprise_4q")) else np.nan,
            "days_since_last_earnings": days_since,
        }

    # ── FINRA short vol ratio (snapshot from yfinance, not per-date historical) ──
    print("  Fetching short interest snapshots …")
    finra = FinraShortFetcher()
    short_snap: dict[str, float | None] = {}
    for t in tickers:
        short_snap[t] = finra.get_snapshot(t)

    # ── Apply to winners_df ────────────────────────────────────────────────
    if winners_df.empty:
        return winners_df, all_df

    insider_rows = [_insider_summary(r['ticker'], r['date'])
                    for _, r in winners_df.iterrows()]
    earnings_rows = [_earnings_features(r['ticker'], r['date'])
                     for _, r in winners_df.iterrows()]

    winners_df = winners_df.copy()
    for col in ["insider_buy_count", "insider_buy_value"]:
        winners_df[col] = [row[col] for row in insider_rows]
    for col in ["beat_streak", "avg_surprise_4q", "days_since_last_earnings"]:
        winners_df[col] = [row[col] for row in earnings_rows]
    winners_df["short_pct_float"] = winners_df["ticker"].map(
        lambda t: short_snap.get(t)
    )

    # ── Optionally enrich all_df (expensive — skip, alt data is only for winners) ──
    # all_df alt enrichment would require per-date lookups for ~1M rows; not practical.
    # Instead, we compute alt-data base rates from the winners_df column distributions
    # compared against the snapshot distribution from the full ticker list.

    print("  Alt-data enrichment complete.")
    return winners_df, all_df


# ─── 6. Pattern analysis ─────────────────────────────────────────────────────

def _pct(n: int, total: int) -> str:
    return f"{100 * n / total:.1f}%" if total else "N/A"


def _analyze_patterns(
    winners: pd.DataFrame,
    all_df: pd.DataFrame,
    window: int,
    min_return: float,
    with_alt: bool,
) -> None:
    """Print lift / precision / recall ranked table for every feature."""
    n_win = len(winners)
    n_all = len(all_df)
    base_rate = n_win / n_all if n_all else 0

    sep = "═" * 80

    print(f"\n{sep}")
    print(f"  BIG-MOVE PATTERN ANALYSIS  —  {int(min_return*100)}%+ in {window}-day window")
    print(sep)
    print(f"  Qualifying entry days : {n_win}  across {winners['ticker'].nunique() if not winners.empty else 0} tickers")
    print(f"  All ticker-days       : {n_all:,}")
    print(f"  Base rate             : {base_rate*100:.3f}%  (1 in {int(1/base_rate) if base_rate else 0} days qualifies)")

    if winners.empty:
        print("\n  No big-move entry days found. Try --min-return 0.25 or --window 63.")
        return

    # ── Feature bucket definitions ─────────────────────────────────────────
    # (feature_col, list of (lo, hi, label) thresholds)
    FEATURES: list[tuple[str, list[tuple[float, float, str]]]] = [
        ("rsi", [
            (-1, 30,  "RSI < 30  (oversold)"),
            (30, 40,  "RSI 30–40"),
            (40, 50,  "RSI 40–50"),
            (50, 60,  "RSI 50–60"),
            (60, 70,  "RSI 60–70"),
            (70, 200, "RSI > 70  (overbought)"),
        ]),
        ("rvol", [
            (-1,  0.5, "RVOL < 0.5×"),
            (0.5, 1.0, "RVOL 0.5–1.0×"),
            (1.0, 1.5, "RVOL 1.0–1.5×"),
            (1.5, 2.0, "RVOL 1.5–2.0×"),
            (2.0, 3.0, "RVOL 2.0–3.0×"),
            (3.0, 999, "RVOL > 3.0×"),
        ]),
        ("day_chg", [
            (-999, -5,  "Day < -5%"),
            (-5,   -2,  "Day -5 to -2%"),
            (-2,    0,  "Day -2 to 0%"),
            (0,     2,  "Day 0 to +2%"),
            (2,     5,  "Day +2 to +5%"),
            (5,   999,  "Day > +5%"),
        ]),
        ("bb_pos", [
            (-1,   0.2, "BB pos < 0.2  (near lower)"),
            (0.2,  0.5, "BB pos 0.2–0.5"),
            (0.5,  0.8, "BB pos 0.5–0.8"),
            (0.8,  1.0, "BB pos 0.8–1.0  (near upper)"),
            (1.0, 999,  "BB pos > 1.0  (above upper)"),
        ]),
        ("sma200_ratio", [
            (-1,  0.70, "Price/SMA200 < 0.70"),
            (0.70, 0.85, "Price/SMA200 0.70–0.85"),
            (0.85, 1.00, "Price/SMA200 0.85–1.00"),
            (1.00, 1.10, "Price/SMA200 1.00–1.10"),
            (1.10, 999,  "Price/SMA200 > 1.10"),
        ]),
        ("macd_hist", [
            (-999, 0, "MACD hist < 0  (bearish)"),
            (0, 999,  "MACD hist > 0  (bullish)"),
        ]),
        ("ema_stack", [
            (0.5, 1.5, "EMA stack intact  (EMA20>50>200)"),
            (-0.5, 0.5, "EMA stack broken"),
        ]),
        ("price_vs_sma50", [
            (-1,   0.90, "Price/SMA50 < 0.90"),
            (0.90, 0.97, "Price/SMA50 0.90–0.97"),
            (0.97, 1.03, "Price/SMA50 0.97–1.03"),
            (1.03, 1.10, "Price/SMA50 1.03–1.10"),
            (1.10, 999,  "Price/SMA50 > 1.10"),
        ]),
        ("consec_down", [
            (-1,  0, "Consec down = 0"),
            (0,   1, "Consec down = 1"),
            (1,   2, "Consec down = 2"),
            (2,  99, "Consec down ≥ 3"),
        ]),
    ]

    # Alt-data features (added only when --with-alt-data and columns present)
    ALT_FEATURES: list[tuple[str, list[tuple[float, float, str]]]] = [
        ("insider_buy_count", [
            (-1, 0,   "Insider buys (60d) = 0"),
            (0,  1,   "Insider buys (60d) = 1"),
            (1,  2,   "Insider buys (60d) = 2"),
            (2, 999,  "Insider buys (60d) ≥ 3"),
        ]),
        ("insider_buy_value", [
            (-1,       0,       "Insider value = $0"),
            (0,    100_000,     "Insider value < $100K"),
            (100_000, 500_000,  "Insider value $100K–$500K"),
            (500_000, 1e12,     "Insider value > $500K"),
        ]),
        ("beat_streak", [
            (-1, 0, "Beat streak = 0"),
            (0,  1, "Beat streak = 1"),
            (1,  2, "Beat streak = 2"),
            (2, 99, "Beat streak ≥ 3"),
        ]),
        ("avg_surprise_4q", [
            (-999, 0,   "Avg surprise < 0%"),
            (0,    5,   "Avg surprise 0–5%"),
            (5,   10,   "Avg surprise 5–10%"),
            (10, 999,   "Avg surprise > 10%"),
        ]),
    ]

    all_features = list(FEATURES)
    if with_alt:
        for feat, buckets in ALT_FEATURES:
            if feat in winners.columns:
                all_features.append((feat, buckets))

    # ── Compute lift for every bucket ──────────────────────────────────────
    result_rows: list[dict] = []

    for feat, buckets in all_features:
        if feat not in winners.columns:
            continue
        for lo, hi, label in buckets:
            w_mask = (winners[feat] > lo) & (winners[feat] <= hi)
            n_w    = int(w_mask.sum())
            if feat in all_df.columns:
                a_mask = (all_df[feat] > lo) & (all_df[feat] <= hi)
                n_a    = int(a_mask.sum())
            else:
                n_a = 0

            if n_w < MIN_N:
                continue

            precision = n_w / n_a      if n_a    else np.nan
            recall    = n_w / n_win    if n_win  else np.nan
            lift      = precision / base_rate if (pd.notna(precision) and base_rate) else np.nan
            avg_ret   = winners.loc[w_mask, 'fwd_return'].mean() if n_w else np.nan
            result_rows.append({
                'feature':   label,
                'n_win':     n_w,
                'n_all':     n_a,
                'lift':      lift,
                'precision': precision,
                'recall':    recall,
                'avg_ret':   avg_ret,
            })

    if not result_rows:
        print(f"\n  No single feature bucket had N≥{MIN_N}. Try --min-return 0.25 or --window 63.")
        return

    results_df = pd.DataFrame(result_rows).sort_values('lift', ascending=False)

    print(f"\n{sep}")
    print(f"  TOP FEATURES BY LIFT  (N≥{MIN_N} required)")
    print(sep)
    hdr = "  {:<42}  {:>6}  {:>7}  {:>8}  {:>7}  {:>8}"
    print(hdr.format("Feature / Bucket", "N_win", "Lift", "Prec%", "Recall", "AvgRet"))
    print("  " + "─" * 78)
    for _, r in results_df.head(30).iterrows():
        lift_s  = f"{r['lift']:.2f}×" if pd.notna(r['lift']) else "N/A"
        prec_s  = f"{r['precision']*100:.2f}%" if pd.notna(r['precision']) else "N/A"
        rec_s   = f"{r['recall']*100:.1f}%" if pd.notna(r['recall']) else "N/A"
        ret_s   = f"{r['avg_ret']:+.1f}%" if pd.notna(r['avg_ret']) else "N/A"
        print(hdr.format(str(r['feature'])[:42], int(r['n_win']),
                         lift_s, prec_s, rec_s, ret_s))

    # ── Top-5 pairwise combinations ────────────────────────────────────────
    top5 = results_df.head(5)
    if len(top5) >= 2:
        print(f"\n{sep}")
        print(f"  TOP PAIRWISE FEATURE COMBINATIONS  (N≥{MIN_N} required)")
        print(sep)
        print(hdr.format("Combination", "N_win", "Lift", "Prec%", "Recall", "AvgRet"))
        print("  " + "─" * 78)

        combo_rows: list[dict] = []
        feat_rows  = top5.to_dict('records')
        for ii in range(len(feat_rows)):
            for jj in range(ii + 1, len(feat_rows)):
                ra, rb = feat_rows[ii], feat_rows[jj]
                # We need to recreate the masks from the feature definitions
                # Use the stored bucket bounds from all_features by matching label
                ma_w = _mask_from_label(winners, all_features, ra['feature'])
                mb_w = _mask_from_label(winners, all_features, rb['feature'])
                if ma_w is None or mb_w is None:
                    continue
                combo_mask = ma_w & mb_w
                n_c = int(combo_mask.sum())
                if n_c < MIN_N:
                    continue
                if 'feature' in all_df.columns or True:
                    ma_a = _mask_from_label(all_df, all_features, ra['feature'])
                    mb_a = _mask_from_label(all_df, all_features, rb['feature'])
                    n_a_combo = int((ma_a & mb_a).sum()) if (ma_a is not None and mb_a is not None) else 0
                else:
                    n_a_combo = 0
                prec_c = n_c / n_a_combo if n_a_combo else np.nan
                recall_c = n_c / n_win if n_win else np.nan
                lift_c   = prec_c / base_rate if (pd.notna(prec_c) and base_rate) else np.nan
                avg_c    = winners.loc[combo_mask, 'fwd_return'].mean()
                combo_rows.append({
                    'feature':   f"{ra['feature'][:20]} + {rb['feature'][:20]}",
                    'n_win':     n_c,
                    'lift':      lift_c,
                    'precision': prec_c,
                    'recall':    recall_c,
                    'avg_ret':   avg_c,
                })

        combo_rows.sort(key=lambda r: r.get('lift') or 0, reverse=True)
        for r in combo_rows[:15]:
            lift_s  = f"{r['lift']:.2f}×" if pd.notna(r['lift']) else "N/A"
            prec_s  = f"{r['precision']*100:.2f}%" if pd.notna(r['precision']) else "N/A"
            rec_s   = f"{r['recall']*100:.1f}%" if pd.notna(r['recall']) else "N/A"
            ret_s   = f"{r['avg_ret']:+.1f}%" if pd.notna(r['avg_ret']) else "N/A"
            print(hdr.format(str(r['feature'])[:42], int(r['n_win']),
                             lift_s, prec_s, rec_s, ret_s))

    # ── Move type breakdown ────────────────────────────────────────────────
    if 'move_type' in winners.columns:
        print(f"\n{sep}")
        print(f"  MOVE TYPE BREAKDOWN")
        print(sep)
        mt = winners.groupby('move_type').agg(
            n=('fwd_return', 'count'),
            avg_ret=('fwd_return', 'mean'),
            days_to_peak=('days_to_peak', 'mean'),
        ).sort_values('n', ascending=False)
        mfmt = "  {:<15}  {:>6}  {:>9}  {:>12}"
        print(mfmt.format("Move Type", "N", "Avg Ret", "Avg Days→Peak"))
        print("  " + "─" * 48)
        for mtype, row in mt.iterrows():
            print(mfmt.format(
                str(mtype), int(row['n']),
                f"{row['avg_ret']:+.1f}%",
                f"{row['days_to_peak']:.1f}d",
            ))


def _mask_from_label(
    df: pd.DataFrame,
    all_features: list[tuple[str, list]],
    label: str,
) -> pd.Series | None:
    """Reconstruct a boolean mask from a stored bucket label."""
    for feat, buckets in all_features:
        if feat not in df.columns:
            continue
        for lo, hi, lbl in buckets:
            if lbl == label:
                return (df[feat] > lo) & (df[feat] <= hi)
    return None


# ─── 7. Sector breakdown ─────────────────────────────────────────────────────

def _report_sector_breakdown(
    winners: pd.DataFrame,
    all_df: pd.DataFrame,
    min_return: float,
    window: int,
) -> None:
    if winners.empty:
        return
    sep = "═" * 80
    print(f"\n{sep}")
    print(f"  SECTOR BREAKDOWN  —  {int(min_return*100)}%+ in {window}-day window")
    print(sep)

    n_win = len(winners)
    n_all = len(all_df)

    sector_all = all_df.groupby('sector').size().rename('all_days')
    sector_win = winners.groupby('sector').agg(
        win_days=('fwd_return', 'count'),
        avg_ret=('fwd_return', 'mean'),
        med_ret=('fwd_return', 'median'),
    )
    sector_stats = sector_win.join(sector_all, how='outer').fillna(0)
    sector_stats['win_rate'] = sector_stats['win_days'] / sector_stats['all_days'].replace(0, np.nan)
    sector_stats['lift']     = (sector_stats['win_days'] / n_win) / \
                                (sector_stats['all_days'] / n_all).replace(0, np.nan)
    sector_stats = sector_stats.sort_values('win_days', ascending=False)

    sfmt = "  {:<38}  {:>8}  {:>8}  {:>8}  {:>7}  {:>8}"
    print(sfmt.format("Sector", "Win Days", "Win %", "Lift", "AvgRet", "MedRet"))
    print("  " + "─" * 78)
    for sec, row in sector_stats.iterrows():
        lift_s = f"{row['lift']:.2f}×" if pd.notna(row['lift']) else "N/A"
        wr_s   = f"{row['win_rate']*100:.2f}%" if pd.notna(row['win_rate']) else "N/A"
        ar_s   = f"{row['avg_ret']:+.1f}%" if pd.notna(row.get('avg_ret')) and row['win_days'] > 0 else "N/A"
        mr_s   = f"{row['med_ret']:+.1f}%" if pd.notna(row.get('med_ret')) and row['win_days'] > 0 else "N/A"
        print(sfmt.format(
            str(sec)[:38], int(row['win_days']),
            wr_s, lift_s, ar_s, mr_s,
        ))


# ─── 8. Top-winners table ─────────────────────────────────────────────────────

def _print_top_winners(winners: pd.DataFrame, n: int = 20) -> None:
    if winners.empty:
        return
    sep = "═" * 80
    print(f"\n{sep}")
    print(f"  TOP {n} WINNING ENTRIES (by 2-month return)")
    print(sep)
    top = winners.nlargest(n, 'fwd_return')
    fmt = "  {:<12}  {:<7}  {:>8}  {:>5}  {:>6}  {:>5}  {:>10}  {:>10}"
    print(fmt.format("Date", "Ticker", "FwdRet", "RSI", "RVOL", "Day%", "MoveType", "Sector"))
    print("  " + "─" * 78)
    for _, r in top.iterrows():
        print(fmt.format(
            str(r['date'])[:10], r['ticker'],
            f"{r['fwd_return']:+.1f}%",
            f"{r['rsi']:.0f}"    if pd.notna(r.get('rsi'))    else "N/A",
            f"{r['rvol']:.1f}×"  if pd.notna(r.get('rvol'))   else "N/A",
            f"{r['day_chg']:+.1f}%" if pd.notna(r.get('day_chg')) else "N/A",
            str(r.get('move_type', 'N/A'))[:10],
            str(r.get('sector', 'N/A'))[:10],
        ))


# ─── 9. Single-ticker deep dive ───────────────────────────────────────────────

def _single_ticker_report(
    ticker: str,
    winners: pd.DataFrame,
    raw: pd.DataFrame,
    window: int,
    min_return: float,
) -> None:
    sep = "═" * 80
    print(f"\n{sep}")
    print(f"  DEEP DIVE: {ticker}  —  all {int(min_return*100)}%+ entries in {window}-day window")
    print(sep)
    t_wins = winners[winners['ticker'] == ticker].sort_values('date')
    if t_wins.empty:
        print(f"  No qualifying big-move entries found for {ticker}.")
        return

    print(f"  {len(t_wins)} winning entry days:\n")
    fmt = "  {:<12}  {:>8}  {:>5}  {:>6}  {:>6}  {:>8}  {:>8}  {:>8}"
    print(fmt.format("Date", "FwdRet", "RSI", "RVOL", "Day%", "BB Pos", "D2Peak", "MoveType"))
    print("  " + "─" * 78)
    for _, r in t_wins.iterrows():
        print(fmt.format(
            str(r['date'])[:10],
            f"{r['fwd_return']:+.1f}%" if pd.notna(r.get('fwd_return')) else "N/A",
            f"{r['rsi']:.0f}"         if pd.notna(r.get('rsi'))         else "N/A",
            f"{r['rvol']:.1f}×"       if pd.notna(r.get('rvol'))        else "N/A",
            f"{r['day_chg']:+.1f}%"   if pd.notna(r.get('day_chg'))     else "N/A",
            f"{r['bb_pos']:.2f}"      if pd.notna(r.get('bb_pos'))      else "N/A",
            f"{r['days_to_peak']}d"   if pd.notna(r.get('days_to_peak')) else "N/A",
            str(r.get('move_type', 'N/A'))[:8],
        ))
    print(f"\n  Avg fwd return : {t_wins['fwd_return'].mean():+.1f}%")
    print(f"  Avg days→peak  : {t_wins['days_to_peak'].mean():.1f} trading days")


# ─── 10. CLI entry point ──────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="10-year big-move pattern research")
    parser.add_argument("--window",       type=int,   default=42,
                        help="Forward return window in trading days (default 42 ≈ 2 months)")
    parser.add_argument("--min-return",   type=float, default=0.30,
                        help="Minimum forward return to qualify as a big move (default 0.30)")
    parser.add_argument("--scan-from",    type=str,   default="2016-01-01",
                        help="Ignore entries before this date (allows 200-bar warmup)")
    parser.add_argument("--refresh",      action="store_true",
                        help="Force re-download of 10yr price data (ignores cache)")
    parser.add_argument("--with-alt-data", action="store_true",
                        help="Enrich winners with SEC/FINRA/earnings data (slow first run)")
    parser.add_argument("--ticker",       type=str,   default=None,
                        help="Run single-ticker deep dive only")
    args = parser.parse_args()

    print(f"\n=== BIG MOVE RESEARCH  ({int(args.min_return*100)}%+ in {args.window}d) ===\n")

    # ── Load tickers and data ───────────────────────────────────────────────
    print("Fetching S&P 500 constituent list …")
    tickers, sector_map = _get_sp500()
    print(f"  {len(tickers)} tickers")

    target_tickers = [args.ticker.upper()] if args.ticker else tickers
    raw = _load_or_download(tickers, refresh=args.refresh)

    # ── Find big moves ──────────────────────────────────────────────────────
    print(f"\nScanning for {int(args.min_return*100)}%+ moves in {args.window}-day window "
          f"(from {args.scan_from}) …")
    winners_df, all_df = _find_bigmoves(
        target_tickers, raw, sector_map,
        window=args.window, min_return=args.min_return,
        scan_from=args.scan_from,
        single_ticker=args.ticker,
    )

    print(f"\nFound {len(winners_df)} winning entry days "
          f"({winners_df['ticker'].nunique() if not winners_df.empty else 0} unique tickers)")

    # ── Single-ticker deep dive ─────────────────────────────────────────────
    if args.ticker:
        _single_ticker_report(args.ticker.upper(), winners_df, raw,
                              args.window, args.min_return)
        return

    # ── Optional alt-data enrichment ───────────────────────────────────────
    if args.with_alt_data and not winners_df.empty:
        print("\nEnriching with alternative data …")
        winners_df, all_df = _enrich_with_alt_data(winners_df, all_df)

    # ── Reports ────────────────────────────────────────────────────────────
    _print_top_winners(winners_df)
    _analyze_patterns(winners_df, all_df,
                      window=args.window,
                      min_return=args.min_return,
                      with_alt=args.with_alt_data)
    _report_sector_breakdown(winners_df, all_df, args.min_return, args.window)

    print(f"\n{'═'*80}")
    print("  CAVEATS")
    print('═'*80)
    print("  • Survivorship bias: only current S&P 500 constituents are included.")
    print(f"  • Base rate for {int(args.min_return*100)}%+ in {args.window}d is very low (~0.2%).")
    print("    Even 4× lift = ~0.8% precision — expect many false positives in live trading.")
    print("  • FINRA/SEC alt data covers ~2yr history; options flow unavailable without Polygon.")
    print('═'*80)
    print()


if __name__ == "__main__":
    main()
