"""
Conviction Screener v3 — multi-factor, ~1–2 high-conviction picks per week.

Targets 20%+ moves in 42 trading days by stacking tight technical gates with
real-money alt-data confirmation. A ticker must pass 8 technical filters AND
have at least one alt-data confirmation signal (insider buying, earnings beat
streak, or bullish options flow).

Technical gate tightening vs. v2:
  RVOL: 3.5× vs 1.5×    (capitulation volume, not routine selling)
  RSI:  25 vs 35         (true capitulation reading)
  New:  ATR candle ≥ 1.5× ATR14  (extreme panic day vs. routine drop)

Alt-data confirmation (at least 1 required, scored 0–3):
  insider_buys_30d ≥ 1   : insiders buying their own dip (SEC Form 4, free)
  earnings_beat_streak ≥ 2: business still healthy despite price collapse
  options_call_anomaly    : put/call ratio < 0.5 (bullish options positioning)

Usage: imported by main.py; not run directly.
"""
import asyncio
import logging
import os
import pathlib
import random
from logging.handlers import RotatingFileHandler
from typing import List

import pandas as pd
import pandas_ta_classic as ta
import yfinance as yf

from backend.scanner import (
    _is_rate_limit,
    _fetch_market_caps_bulk_async,
    get_full_market_tickers,
)

logger = logging.getLogger("scanner_v3")
logger.setLevel(logging.DEBUG)
_LOG_PATH = pathlib.Path(__file__).parent / "scanner_v3.log"
_file_handler = RotatingFileHandler(str(_LOG_PATH), maxBytes=5_000_000, backupCount=3)
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
logger.addHandler(_file_handler)

# Screening parameters — conviction screener calibrated for ~1–2 signals/week.
#
# Strategy: multi-factor extreme dislocation + alt-data confirmation.
# Start with the proven v2 SMA200 dislocation gate (price < 70% SMA200)
# then apply tighter technical gates AND require real-money confirmation.
# Expected precision: higher than v2's ~15% for 30%+ because of alt-data gate.
#
CONFIG_V3 = {
    "MIN_MARKET_CAP":       1_000_000_000,  # > $1B (same liquidity floor)
    "MIN_PRICE":            5.0,            # > $5 (no penny stocks)
    "MIN_VOLUME":           500_000,        # > 500K shares on the selloff day
    "MAX_DAY_CHANGE":      -5.0,            # day change < −5% (panic trigger)
    "MIN_RVOL":             3.5,            # RVOL > 3.5× (capitulation surge; up from v2's 1.5×)
    "MAX_RSI":              25.0,           # RSI < 25 (true capitulation; tighter than v2's 35)
    "MAX_SMA200_RATIO":     0.70,           # price < 70% of SMA200 (same dislocation gate as v2)
    "MAX_SMA50_RATIO":      0.90,           # price < 90% of SMA50 (same)
    "MIN_ATR_CANDLE":       1.5,            # candle body ≥ 1.5× ATR14 (extreme panic day)
    "MIN_INSIDER_BUYS_30D": 1,              # ≥ 1 SEC Form 4 insider buy in last 30 days
    "MIN_EARNINGS_BEATS":   2,              # ≥ 2 consecutive quarterly EPS beats
    "MAX_DAYS_TO_EARNINGS": 7,              # skip if earnings within 7 days (binary event)
    "OPTIONS_PCR_THRESHOLD": 0.50,         # put/call ratio < 0.5 → bullish options anomaly
}


def get_active_filters_v3() -> list[str]:
    return [
        f"Day change < {CONFIG_V3['MAX_DAY_CHANGE']:.0f}% (panic selloff into extreme dislocation)",
        f"Market Cap > ${CONFIG_V3['MIN_MARKET_CAP'] / 1_000_000_000:.0f}B",
        f"Price > ${CONFIG_V3['MIN_PRICE']}",
        f"Volume > {CONFIG_V3['MIN_VOLUME'] / 1000:.0f}K shares",
        f"RVOL > {CONFIG_V3['MIN_RVOL']}× (capitulation volume surge)",
        f"RSI(14) < {CONFIG_V3['MAX_RSI']:.0f} (true capitulation)",
        f"Price < {int(CONFIG_V3['MAX_SMA200_RATIO'] * 100)}% of SMA200 (extreme dislocation)",
        f"Price ≤ {int(CONFIG_V3['MAX_SMA50_RATIO'] * 100)}% of SMA50",
        f"Candle body ≥ {CONFIG_V3['MIN_ATR_CANDLE']}× ATR14 (extreme panic day)",
        "Alt-data: ≥ 1 of {insider buy (30d), earnings beat streak ≥ 2, options call anomaly}",
    ]


def _filter_ticker_v3_technical(
    ticker: str, data: pd.DataFrame, market_caps: dict
) -> dict | None:
    """
    Applies 8 technical filters. Returns a partial result dict if all pass, else None.
    Alt-data filters (filter 9–10) are applied separately by the async caller.
    """
    try:
        if isinstance(data.columns, pd.MultiIndex):
            if ticker not in data.columns.levels[0]:
                logger.debug(f"{ticker} not in data columns")
                return None
            df = data[ticker].dropna()
        else:
            df = data.dropna()

        if len(df) < 200:
            logger.debug(f"{ticker} data too short: {len(df)}")
            return None

        info       = market_caps.get(ticker, {"market_cap": 0.0, "exchange": "NASDAQ", "last_price": None})
        market_cap = info["market_cap"]
        exchange   = info["exchange"]

        raw_price = info.get("last_price")
        if raw_price is None or pd.isna(raw_price):
            logger.debug(f"{ticker} skipped — no live price from fast_info")
            return None

        raw_prev = df["Close"].iloc[-2]
        raw_vol  = df["Volume"].iloc[-1]
        if pd.isna(raw_prev) or pd.isna(raw_vol):  # pragma: no cover
            return None

        price      = float(raw_price)
        prev_close = float(raw_prev)
        if prev_close == 0:
            return None
        day_change = (price - prev_close) / prev_close

        live_vol = info.get("last_volume")
        volume   = live_vol if live_vol is not None else int(raw_vol)

        # ── Filter 1: Market cap + price ─────────────────────────────────────
        if market_cap < CONFIG_V3["MIN_MARKET_CAP"] or price <= CONFIG_V3["MIN_PRICE"]:
            logger.debug(f"{ticker} failed MC/Price")
            return None

        # ── Filter 2: Day change < −5% ───────────────────────────────────────
        if day_change * 100 >= CONFIG_V3["MAX_DAY_CHANGE"]:
            logger.debug(f"{ticker} failed day change: {day_change * 100:.2f}%")
            return None

        # ── Filter 3: Volume > 500K ───────────────────────────────────────────
        if volume < CONFIG_V3["MIN_VOLUME"]:
            logger.debug(f"{ticker} failed Volume: {volume}")
            return None

        # ── Filter 4: RVOL > 3.5× ─────────────────────────────────────────────
        avg_vol_20 = float(df["Volume"].rolling(window=20).mean().iloc[-1])
        vol_ratio  = round(volume / avg_vol_20, 2) if avg_vol_20 > 0 else 1.0
        if vol_ratio < CONFIG_V3["MIN_RVOL"]:
            logger.debug(f"{ticker} failed RVOL: {vol_ratio:.2f}")
            return None

        # ── Filter 5: RSI(14) < 25 ────────────────────────────────────────────
        rsi_series = ta.rsi(df["Close"], length=14)
        if rsi_series is None or rsi_series.empty:
            return None
        curr_rsi = round(float(rsi_series.iloc[-1]), 1)
        if curr_rsi >= CONFIG_V3["MAX_RSI"]:
            logger.debug(f"{ticker} failed RSI: {curr_rsi:.1f}")
            return None

        # ── Filter 6: Price < 70% of SMA200 (extreme dislocation) ────────────
        sma200_series = df["Close"].rolling(window=200).mean()
        curr_sma200   = float(sma200_series.iloc[-1])
        if price >= curr_sma200 * CONFIG_V3["MAX_SMA200_RATIO"]:
            logger.debug(f"{ticker} not deeply distressed: {price:.2f} >= 70% × {curr_sma200:.2f}")
            return None

        # ── Filter 7: Price < 90% of SMA50 ───────────────────────────────────
        sma50_series   = df["Close"].rolling(window=50).mean()
        curr_sma50_raw = float(sma50_series.iloc[-1])
        if curr_sma50_raw > 0 and price / curr_sma50_raw >= CONFIG_V3["MAX_SMA50_RATIO"]:
            logger.debug(f"{ticker} price/SMA50={price / curr_sma50_raw:.2f} ≥ {CONFIG_V3['MAX_SMA50_RATIO']}")
            return None

        # ── Filter 8: Candle body ≥ 1.5× ATR14 (extreme panic day) ───────────
        atr_series = ta.atr(df["High"], df["Low"], df["Close"], length=14)
        curr_atr   = round(float(atr_series.iloc[-1]), 2) if atr_series is not None and not atr_series.empty else round(price * 0.02, 2)

        last_open = float(df["Open"].iloc[-1])
        atr_candle_ratio = abs(price - last_open) / curr_atr if curr_atr > 0 else 0.0
        if atr_candle_ratio < CONFIG_V3["MIN_ATR_CANDLE"]:
            logger.debug(f"{ticker} failed ATR candle: {atr_candle_ratio:.2f} < {CONFIG_V3['MIN_ATR_CANDLE']}")
            return None

        # ── Display indicators ────────────────────────────────────────────────
        ema8_series  = ta.ema(df["Close"], length=8)
        ema20_series = ta.ema(df["Close"], length=20)
        ema50_series = ta.ema(df["Close"], length=50)
        ema200_series = ta.ema(df["Close"], length=200)
        curr_ema8   = round(float(ema8_series.iloc[-1]),   2) if ema8_series   is not None and not ema8_series.empty   else round(price, 2)
        curr_ema20  = round(float(ema20_series.iloc[-1]),  2) if ema20_series  is not None and not ema20_series.empty  else round(price, 2)
        curr_ema50  = round(float(ema50_series.iloc[-1]),  2) if ema50_series  is not None and not ema50_series.empty  else round(price, 2)
        curr_ema200 = round(float(ema200_series.iloc[-1]), 2) if ema200_series is not None and not ema200_series.empty else round(price, 2)

        macd_result = ta.macd(df["Close"])
        if macd_result is not None and not macd_result.empty:
            _mc = next((c for c in macd_result.columns if c.startswith("MACD_")),  None)
            _ms = next((c for c in macd_result.columns if c.startswith("MACDs_")), None)
            _mh = next((c for c in macd_result.columns if c.startswith("MACDh_")), None)
            curr_macd        = round(float(macd_result[_mc].iloc[-1]), 4) if _mc else 0.0
            curr_macd_signal = round(float(macd_result[_ms].iloc[-1]), 4) if _ms else 0.0
            curr_macd_hist   = round(float(macd_result[_mh].iloc[-1]), 4) if _mh else 0.0
        else:
            curr_macd = curr_macd_signal = curr_macd_hist = 0.0

        curr_sma50 = round(curr_sma50_raw, 2)

        bb_result = ta.bbands(df["Close"], length=20, std=2)
        if bb_result is not None and not bb_result.empty:
            _bl = next((c for c in bb_result.columns if c.startswith("BBL_")), None)
            _bm = next((c for c in bb_result.columns if c.startswith("BBM_")), None)
            _bu = next((c for c in bb_result.columns if c.startswith("BBU_")), None)
            curr_bb_lower  = round(float(bb_result[_bl].iloc[-1]), 2) if _bl else round(price * 0.97, 2)
            curr_bb_middle = round(float(bb_result[_bm].iloc[-1]), 2) if _bm else round(price, 2)
            curr_bb_upper  = round(float(bb_result[_bu].iloc[-1]), 2) if _bu else round(price * 1.03, 2)
        else:
            curr_bb_lower  = round(price * 0.97, 2)
            curr_bb_middle = round(price, 2)
            curr_bb_upper  = round(price * 1.03, 2)

        # Entry / stop levels (wider stops: 1.5× and 2.5× ATR — distressed names are volatile)
        entry1 = round(price,         2)
        entry2 = round(curr_ema8,     2)
        entry3 = round(curr_bb_lower, 2)
        stop1  = round(price - 1.5 * curr_atr, 2)
        stop2  = round(price - 2.5 * curr_atr, 2)
        stop3  = round(price * 0.85,  2)

        return {
            "ticker":      ticker,
            "exchange":    exchange,
            "price":       round(float(price), 2),
            "change":      round(float(day_change * 100), 2),
            "volume":      int(volume),
            "vol_ratio":   vol_ratio,
            "market_cap":  int(market_cap),
            "rsi":         curr_rsi,
            "macd":        curr_macd,
            "macd_signal": curr_macd_signal,
            "macd_hist":   curr_macd_hist,
            "ema8":        curr_ema8,
            "ema20":       curr_ema20,
            "ema50":       curr_ema50,
            "ema200":      curr_ema200,
            "sma50":       curr_sma50,
            "sma200":      round(float(curr_sma200), 2),
            "bb_upper":    curr_bb_upper,
            "bb_middle":   curr_bb_middle,
            "bb_lower":    curr_bb_lower,
            "atr14":       curr_atr,
            "entry1":      entry1,
            "entry2":      entry2,
            "entry3":      entry3,
            "stop1":       stop1,
            "stop2":       stop2,
            "stop3":       stop3,
        }
    except Exception as e:
        logger.warning(f"Error processing {ticker} ({type(e).__name__}): {e}")
        return None


def _fetch_alt_data_v3(ticker: str) -> dict:
    """
    Fetches alt-data signals and computes conviction_score (0–3).
    Also checks earnings proximity — sets skip_earnings=True if within 7 days.
    All fetches are wrapped in try/except; failures return zeroes.
    """
    from backend.alt_data import SecEdgarFetcher, EarningsFetcher

    result: dict = {
        "insider_buys_30d":    0,
        "earnings_beat_streak": 0,
        "options_call_anomaly": False,
        "conviction_score":    0,
        "skip_earnings":       False,
    }

    # ── Earnings proximity check (skip binary events) ─────────────────────────
    try:
        cal = yf.Ticker(ticker).calendar
        if cal is not None:
            dates = cal.get("Earnings Date") if isinstance(cal, dict) else (
                cal.loc["Earnings Date"].tolist() if "Earnings Date" in cal.index else []
            )
            if dates:
                next_earn = pd.Timestamp(dates[0])
                days_away = (next_earn - pd.Timestamp.now()).days
                if 0 <= days_away <= CONFIG_V3["MAX_DAYS_TO_EARNINGS"]:
                    result["skip_earnings"] = True
                    return result
    except Exception:
        pass

    # ── Insider buys (SEC EDGAR — always free, cached) ────────────────────────
    try:
        df = SecEdgarFetcher().get(ticker)
        if df is not None and not df.empty and "date" in df.columns:
            cutoff = pd.Timestamp.now() - pd.Timedelta(days=30)
            result["insider_buys_30d"] = int(len(df[df["date"] >= cutoff]))
    except Exception:
        pass

    # ── Earnings beat streak (yfinance, always free) ──────────────────────────
    try:
        ef = EarningsFetcher()
        df = ef.get(ticker)
        if df is not None and not df.empty and "beat_streak" in df.columns:
            result["earnings_beat_streak"] = int(df.iloc[-1]["beat_streak"])
    except Exception:
        pass

    # ── Options flow: put/call ratio < 0.5 = bullish anomaly (Polygon, optional) ──
    if os.getenv("POLYGON_API_KEY"):
        try:
            from backend.alt_data import PolygonFetcher
            pf = PolygonFetcher()
            today_str = pd.Timestamp.now().strftime("%Y-%m-%d")
            pcr = pf.get_put_call_ratio(ticker, today_str)
            if pcr is not None and pcr < CONFIG_V3["OPTIONS_PCR_THRESHOLD"]:
                result["options_call_anomaly"] = True
        except Exception:
            pass

    result["conviction_score"] = (
        int(result["insider_buys_30d"]    >= CONFIG_V3["MIN_INSIDER_BUYS_30D"]) +
        int(result["earnings_beat_streak"] >= CONFIG_V3["MIN_EARNINGS_BEATS"])   +
        int(result["options_call_anomaly"])
    )
    return result


async def screen_stocks_v3(tickers: List[str]):
    """
    Screens tickers for conviction setups: tight technical gates + alt-data confirmation.
    Yields result dicts and progress/warning events — same event shape as v1/v2.
    """
    period     = "2y"
    chunk_size = 50
    chunks     = [tickers[i : i + chunk_size] for i in range(0, len(tickers), chunk_size)]

    queue: asyncio.Queue     = asyncio.Queue()
    processed_count          = 0
    chunk_semaphore          = asyncio.Semaphore(5)
    fast_info_semaphore      = asyncio.Semaphore(20)

    async def process_chunk(chunk: list[str], chunk_idx: int) -> None:
        nonlocal processed_count
        await asyncio.sleep(chunk_idx * 0.5)
        logger.debug(f"v3 chunk {chunk_idx}: {chunk}")

        retries = 3
        data, market_caps = None, {}
        for attempt in range(retries):
            try:
                async with chunk_semaphore:
                    data = await asyncio.wait_for(
                        asyncio.to_thread(
                            yf.download, chunk,
                            period=period, group_by="ticker", progress=False, threads=False
                        ),
                        timeout=180.0,
                    )
                if data is None or data.empty:
                    raise ValueError("Empty data returned")
                market_caps = await _fetch_market_caps_bulk_async(chunk, fast_info_semaphore)
                break
            except Exception as e:
                if attempt < retries - 1:
                    wait = (60 * (attempt + 1) if _is_rate_limit(e) else 5 * (attempt + 1)) + random.uniform(0, 5)
                    logger.warning(
                        f"v3 chunk {chunk_idx} attempt {attempt + 1} failed "
                        f"({type(e).__name__}), retrying in {wait:.1f}s"
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"v3 chunk {chunk_idx} failed after {retries} attempts: {e}")
                    for ticker in chunk:
                        processed_count += 1
                        await queue.put({"status": "progress", "current": processed_count, "ticker": ticker})
                    await queue.put(None)
                    return

        for ticker in chunk:
            processed_count += 1
            await queue.put({"status": "progress", "current": processed_count, "ticker": ticker})

            result = _filter_ticker_v3_technical(ticker, data, market_caps)
            if result:
                # Fetch alt data in a thread — only called for tickers passing all 8 technical gates
                alt = await asyncio.to_thread(_fetch_alt_data_v3, ticker)
                if not alt.get("skip_earnings") and alt["conviction_score"] >= 1:
                    result.update({k: v for k, v in alt.items() if k != "skip_earnings"})
                    logger.info(
                        f"v3 conviction match: {ticker} at ${result['price']} "
                        f"({result['change']}%) score={alt['conviction_score']}"
                    )
                    await queue.put(result)
                else:
                    reason = "earnings binary event" if alt.get("skip_earnings") else f"conviction_score={alt['conviction_score']}"
                    logger.debug(f"{ticker} rejected by alt-data gate: {reason}")

            await asyncio.sleep(0)
        await queue.put(None)

    tasks = [asyncio.create_task(process_chunk(chunk, i)) for i, chunk in enumerate(chunks)]  # noqa: F841

    done = 0
    while done < len(chunks):
        item = await queue.get()
        if item is None:
            done += 1
        else:
            yield item
