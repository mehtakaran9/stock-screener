"""
Conviction Screener Research — 20%+ moves in 42 trading days.

Builds the empirical foundation for scanner_v3.py thresholds by characterizing
which signal combinations best predict 20%+ returns within 42 trading days
across 10 years of S&P 500 data.

Key new signal: ATR candle body ratio (abs(close-open) / ATR14) distinguishes
extreme panic days from routine selling.

Data: reuses sp500_10yr_ohlcv.pkl from bigmove_research if available.

Usage:
    python3 -m backend.conviction_research                         # default: 20%+ in 42d
    python3 -m backend.conviction_research --window 21 --min-return 0.20
    python3 -m backend.conviction_research --with-alt-data         # adds earnings features
"""
import argparse
import logging
import pathlib
import numpy as np
import pandas as pd

from backend.reverse_backtest import (
    _get_sp500,
    _compute_signals,
    _first_winning_days,
    MIN_HISTORY,
)

logger = logging.getLogger(__name__)

_CACHE_FILE = pathlib.Path(__file__).parent / "sp500_10yr_ohlcv.pkl"
MIN_N = 5


# ─── 1. Data loading (reuses bigmove cache) ──────────────────────────────────

def _load_or_download(tickers: list[str], refresh: bool = False) -> pd.DataFrame:
    if not refresh and _CACHE_FILE.exists():
        print(f"  Loading cached 10yr OHLCV from {_CACHE_FILE} …")
        return pd.read_pickle(str(_CACHE_FILE))

    import yfinance as yf
    print(f"  Downloading 10yr OHLCV for {len(tickers)} tickers (this may take 10–20 min) …")
    frames = []
    chunk_size = 50
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i : i + chunk_size]
        try:
            raw = yf.download(chunk, period="10y", group_by="ticker", progress=False, threads=False)
            frames.append(raw)
            print(f"    chunk {i // chunk_size + 1}/{(len(tickers) + chunk_size - 1) // chunk_size} done")
        except Exception as e:
            logger.warning(f"Chunk {i // chunk_size + 1} failed: {e}")

    if not frames:
        raise RuntimeError("All download chunks failed — check internet connectivity.")

    combined = pd.concat(frames, axis=1)
    combined.to_pickle(str(_CACHE_FILE))
    print(f"  Saved to {_CACHE_FILE}")
    return combined


# ─── 2. ATR candle body signal ───────────────────────────────────────────────

def _add_atr_candle_signal(df: pd.DataFrame) -> pd.DataFrame:
    """Add atr_candle_ratio = abs(close - open) / atr14 for each row."""
    import pandas_ta_classic as ta
    if "Open" not in df.columns or "Close" not in df.columns:
        df["atr_candle_ratio"] = np.nan
        return df
    atr = ta.atr(df["High"], df["Low"], df["Close"], length=14)
    if atr is None or atr.empty:
        df["atr_candle_ratio"] = np.nan
        return df
    body = (df["Close"] - df["Open"]).abs()
    df["atr_candle_ratio"] = (body / atr.values).round(3)
    return df


# ─── 3. Find conviction entries (20%+ in window days) ────────────────────────

def _find_conviction_moves(
    tickers: list[str],
    raw: pd.DataFrame,
    sector_map: dict,
    window: int = 42,
    min_return: float = 0.20,
    scan_from: str = "2016-01-01",
    single_ticker: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Returns (winners_df, all_df) where winners_df is every entry day that produced
    a forward return >= min_return within window trading days.
    """
    from backend.reverse_backtest import _compute_signals

    winner_rows: list[dict] = []
    all_rows: list[dict] = []
    scan_ts = pd.Timestamp(scan_from)

    target = [single_ticker.upper()] if single_ticker else tickers

    for ticker in target:
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                if ticker not in raw.columns.get_level_values(0):
                    continue
                df = raw[ticker].copy().dropna()
            else:
                df = raw.copy().dropna()

            if len(df) < MIN_HISTORY:
                continue

            df = df[df.index >= "2010-01-01"].copy()
            if len(df) < MIN_HISTORY:
                continue

            df = _add_atr_candle_signal(df)
            sigs = _compute_signals(df)
            fwd = _first_winning_days(df["Close"], window=window, min_return=min_return)

            sector = sector_map.get(ticker, "Unknown")

            for idx in range(MIN_HISTORY, len(df) - window):
                ts = df.index[idx]
                if ts < scan_ts:
                    continue

                sig = sigs.iloc[idx] if idx < len(sigs) else None
                if sig is None:
                    continue

                is_win = bool(fwd.iloc[idx]) if idx < len(fwd) else False
                fwd_ret = None
                if is_win:
                    # Fixed-horizon return at the same `window` used by
                    # _first_winning_days (close.shift(-window)) — NOT the
                    # best-case max over the window, which would overstate the
                    # achievable return. The loop bound guarantees idx+window is valid.
                    entry_close = df["Close"].iloc[idx]
                    exit_close  = df["Close"].iloc[idx + window]
                    fwd_ret = float((exit_close - entry_close) / entry_close * 100)

                atrc = float(df["atr_candle_ratio"].iloc[idx]) if "atr_candle_ratio" in df.columns else np.nan

                row = {
                    "ticker":         ticker,
                    "date":           ts.date(),
                    "sector":         sector,
                    "rsi":            float(sig.get("rsi", np.nan)),
                    "rvol":           float(sig.get("vol_ratio", np.nan)),
                    "day_chg":        float(sig.get("day_chg", np.nan)),
                    "sma200_ratio":   float(sig.get("sma200_ratio", np.nan)),
                    "price_vs_sma50": float(sig.get("price_vs_sma50", np.nan)),
                    "macd_hist":      float(sig.get("macd_hist", np.nan)),
                    "atr_candle_ratio": atrc,
                    "fwd_return":     fwd_ret if is_win else np.nan,
                }
                all_rows.append(row)
                if is_win and fwd_ret is not None:
                    winner_rows.append(row)

        except Exception as e:
            logger.debug(f"Error processing {ticker}: {e}")

    winners_df = pd.DataFrame(winner_rows) if winner_rows else pd.DataFrame()
    all_df     = pd.DataFrame(all_rows)    if all_rows    else pd.DataFrame()
    return winners_df, all_df


# ─── 4. Earnings enrichment (optional) ───────────────────────────────────────

def _add_earnings_features(winners: pd.DataFrame) -> pd.DataFrame:
    """Adds beat_streak for each (ticker, date) pair using EarningsFetcher."""
    from backend.alt_data import EarningsFetcher
    ef = EarningsFetcher()
    streaks = []
    for _, row in winners.iterrows():
        try:
            df = ef.get(row["ticker"])
            if df.empty or "beat_streak" not in df.columns:
                streaks.append(0)
                continue
            as_of = pd.Timestamp(row["date"])
            streak = ef.beat_streak_as_of(row["ticker"], as_of)
            streaks.append(streak)
        except Exception:
            streaks.append(0)
    winners["beat_streak"] = streaks
    return winners


# ─── 5. Pattern analysis ─────────────────────────────────────────────────────

def _analyze_patterns(
    winners: pd.DataFrame,
    all_df: pd.DataFrame,
    window: int,
    min_return: float,
    with_alt: bool = False,
) -> None:
    if winners.empty or all_df.empty:
        print("\n  No winning entry days found.")
        return

    n_win   = len(winners)
    n_all   = len(all_df)
    base_rate = n_win / n_all if n_all else 0.0

    sep = "═" * 80

    print(f"\n{sep}")
    print(f"  CONVICTION PATTERN ANALYSIS  —  {int(min_return*100)}%+ in {window}-day window")
    print(sep)
    print(f"  Qualifying entry days : {n_win}  across {winners['ticker'].nunique() if not winners.empty else 0} tickers")
    print(f"  All ticker-days       : {n_all:,}")
    print(f"  Base rate             : {base_rate*100:.3f}%  (1 in {int(1/base_rate) if base_rate else 0} days qualifies)")

    FEATURES: list[tuple[str, list[tuple[float, float, str]]]] = [
        ("rsi", [
            (-1,  20, "RSI < 20  (extreme oversold)"),
            (20,  25, "RSI 20–25"),
            (25,  30, "RSI 25–30"),
            (30,  35, "RSI 30–35"),
            (35,  40, "RSI 35–40"),
            (40, 200, "RSI > 40"),
        ]),
        ("rvol", [
            (-1, 1.5, "RVOL < 1.5×"),
            (1.5, 2.0, "RVOL 1.5–2.0×"),
            (2.0, 3.0, "RVOL 2.0–3.0×"),
            (3.0, 3.5, "RVOL 3.0–3.5×"),
            (3.5, 5.0, "RVOL 3.5–5.0×"),
            (5.0, 999, "RVOL > 5.0×"),
        ]),
        ("day_chg", [
            (-999, -10, "Day < -10%"),
            (-10,   -7, "Day -10 to -7%"),
            (-7,    -5, "Day -7 to -5%"),
            (-5,    -2, "Day -5 to -2%"),
            (-2,     0, "Day -2 to 0%"),
            (0,    999, "Day > 0%"),
        ]),
        ("sma200_ratio", [
            (-1,   0.50, "Price/SMA200 < 0.50  (extreme)"),
            (0.50, 0.60, "Price/SMA200 0.50–0.60"),
            (0.60, 0.70, "Price/SMA200 0.60–0.70"),
            (0.70, 0.85, "Price/SMA200 0.70–0.85"),
            (0.85, 1.00, "Price/SMA200 0.85–1.00"),
            (1.00, 999,  "Price/SMA200 > 1.00"),
        ]),
        ("price_vs_sma50", [
            (-1,   0.70, "Price/SMA50 < 0.70"),
            (0.70, 0.80, "Price/SMA50 0.70–0.80"),
            (0.80, 0.90, "Price/SMA50 0.80–0.90"),
            (0.90, 1.00, "Price/SMA50 0.90–1.00"),
            (1.00, 999,  "Price/SMA50 > 1.00"),
        ]),
        ("atr_candle_ratio", [
            (-1,  0.5, "ATR candle < 0.5×"),
            (0.5, 1.0, "ATR candle 0.5–1.0×"),
            (1.0, 1.5, "ATR candle 1.0–1.5×"),
            (1.5, 2.0, "ATR candle 1.5–2.0×"),
            (2.0, 3.0, "ATR candle 2.0–3.0×"),
            (3.0, 999, "ATR candle > 3.0×"),
        ]),
    ]

    if with_alt:
        FEATURES.append(("beat_streak", [
            (-1, 0, "Beat streak = 0"),
            (0,  1, "Beat streak = 1"),
            (1,  2, "Beat streak = 2"),
            (2, 99, "Beat streak ≥ 3"),
        ]))

    result_rows: list[dict] = []
    for feat, buckets in FEATURES:
        if feat not in winners.columns or feat not in all_df.columns:
            continue
        for lo, hi, label in buckets:
            w_mask = (winners[feat] > lo) & (winners[feat] <= hi)
            a_mask = (all_df[feat]   > lo) & (all_df[feat]   <= hi)
            n_w, n_a = int(w_mask.sum()), int(a_mask.sum())
            if n_w < MIN_N:
                continue
            precision = n_w / n_a     if n_a    else np.nan
            recall    = n_w / n_win   if n_win  else np.nan
            lift      = precision / base_rate if (pd.notna(precision) and base_rate) else np.nan
            avg_ret   = float(winners.loc[w_mask, "fwd_return"].mean()) if n_w else np.nan
            result_rows.append({
                "feature": label, "n_win": n_w, "n_all": n_a,
                "lift": lift, "precision": precision, "recall": recall, "avg_ret": avg_ret,
            })

    if not result_rows:
        print(f"\n  No single feature bucket had N≥{MIN_N}.")
        return

    results_df = pd.DataFrame(result_rows).sort_values("lift", ascending=False)

    print(f"\n{sep}")
    print(f"  TOP FEATURES BY LIFT  (N≥{MIN_N} required)")
    print(sep)
    hdr = "  {:<45}  {:>6}  {:>7}  {:>8}  {:>7}  {:>8}"
    print(hdr.format("Feature / Bucket", "N_win", "Lift", "Prec%", "Recall", "AvgRet"))
    print("  " + "─" * 81)
    for _, r in results_df.head(30).iterrows():
        lift_s = f"{r['lift']:.2f}×"            if pd.notna(r["lift"])      else "N/A"
        prec_s = f"{r['precision']*100:.2f}%"   if pd.notna(r["precision"]) else "N/A"
        rec_s  = f"{r['recall']*100:.1f}%"      if pd.notna(r["recall"])    else "N/A"
        ret_s  = f"{r['avg_ret']:+.1f}%"        if pd.notna(r["avg_ret"])   else "N/A"
        print(hdr.format(str(r["feature"])[:45], int(r["n_win"]), lift_s, prec_s, rec_s, ret_s))

    # ── Top pairwise combinations ──────────────────────────────────────────
    top5 = results_df.head(5)
    if len(top5) >= 2:
        print(f"\n{sep}")
        print(f"  TOP PAIRWISE COMBINATIONS  (N≥{MIN_N} required)")
        print(sep)
        print(hdr.format("Combination", "N_win", "Lift", "Prec%", "Recall", "AvgRet"))
        print("  " + "─" * 81)

        def _mask(df: pd.DataFrame, label: str) -> pd.Series | None:
            for feat, buckets in FEATURES:
                if feat not in df.columns:
                    continue
                for lo, hi, lbl in buckets:
                    if lbl == label:
                        return (df[feat] > lo) & (df[feat] <= hi)
            return None

        combo_rows: list[dict] = []
        feat_list = top5.to_dict("records")
        for ii in range(len(feat_list)):
            for jj in range(ii + 1, len(feat_list)):
                ra, rb = feat_list[ii], feat_list[jj]
                maw = _mask(winners, ra["feature"])
                mbw = _mask(winners, rb["feature"])
                if maw is None or mbw is None:
                    continue
                combo_w = int((maw & mbw).sum())
                if combo_w < MIN_N:
                    continue
                maa = _mask(all_df, ra["feature"])
                mba = _mask(all_df, rb["feature"])
                n_a = int((maa & mba).sum()) if (maa is not None and mba is not None) else 0
                prec_c = combo_w / n_a if n_a else np.nan
                rec_c  = combo_w / n_win if n_win else np.nan
                lift_c = prec_c / base_rate if (pd.notna(prec_c) and base_rate) else np.nan
                avg_c  = float(winners.loc[(maw & mbw), "fwd_return"].mean()) if combo_w else np.nan
                combo_rows.append({
                    "feature": f"{ra['feature'][:22]} + {rb['feature'][:22]}",
                    "n_win": combo_w, "lift": lift_c,
                    "precision": prec_c, "recall": rec_c, "avg_ret": avg_c,
                })

        combo_rows.sort(key=lambda r: r.get("lift") or 0, reverse=True)
        for r in combo_rows[:15]:
            lift_s = f"{r['lift']:.2f}×"           if pd.notna(r["lift"])      else "N/A"
            prec_s = f"{r['precision']*100:.2f}%"  if pd.notna(r["precision"]) else "N/A"
            rec_s  = f"{r['recall']*100:.1f}%"     if pd.notna(r["recall"])    else "N/A"
            ret_s  = f"{r['avg_ret']:+.1f}%"       if pd.notna(r["avg_ret"])   else "N/A"
            print(hdr.format(str(r["feature"])[:45], int(r["n_win"]), lift_s, prec_s, rec_s, ret_s))


# ─── 6. CLI entry point ───────────────────────────────────────────────────────

def main() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(description="Conviction screener research (20%+ in 42d)")
    parser.add_argument("--window",       type=int,   default=42,
                        help="Forward return window in trading days (default 42)")
    parser.add_argument("--min-return",   type=float, default=0.20,
                        help="Min forward return to qualify (default 0.20 = 20%%)")
    parser.add_argument("--scan-from",    type=str,   default="2016-01-01",
                        help="Ignore entries before this date")
    parser.add_argument("--refresh",      action="store_true",
                        help="Force re-download of 10yr price data")
    parser.add_argument("--with-alt-data", action="store_true",
                        help="Add earnings beat streak features (requires EarningsFetcher)")
    parser.add_argument("--ticker",       type=str,   default=None,
                        help="Restrict to a single ticker")
    args = parser.parse_args()

    print(f"\n=== CONVICTION RESEARCH  ({int(args.min_return*100)}%+ in {args.window}d) ===\n")

    print("Fetching S&P 500 constituent list …")
    tickers, sector_map = _get_sp500()
    print(f"  {len(tickers)} tickers")

    raw = _load_or_download(tickers, refresh=args.refresh)

    target = [args.ticker.upper()] if args.ticker else tickers
    print(f"\nScanning for {int(args.min_return*100)}%+ moves in {args.window}-day window "
          f"(from {args.scan_from}) …")
    winners_df, all_df = _find_conviction_moves(
        target, raw, sector_map,
        window=args.window, min_return=args.min_return,
        scan_from=args.scan_from, single_ticker=args.ticker,
    )

    print(f"\nFound {len(winners_df)} winning entry days "
          f"({winners_df['ticker'].nunique() if not winners_df.empty else 0} unique tickers)")

    if args.with_alt_data and not winners_df.empty:
        print("\nAdding earnings beat streak features …")
        winners_df = _add_earnings_features(winners_df)

    _analyze_patterns(
        winners_df, all_df,
        window=args.window, min_return=args.min_return,
        with_alt=args.with_alt_data,
    )

    print(f"\n{'═'*80}")
    print("  CAVEATS")
    print("═" * 80)
    print("  • Survivorship bias: only current S&P 500 constituents.")
    print(f"  • Base rate for {int(args.min_return*100)}%+ in {args.window}d is ~0.5–1%.")
    print("    Even 5× lift = ~3–5% precision — still many false positives.")
    print("  • Alt-data signals (SEC, Polygon) unavailable for historical backtest.")
    print("═" * 80)
    print()


if __name__ == "__main__":
    main()
