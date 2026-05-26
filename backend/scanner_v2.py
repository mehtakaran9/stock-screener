"""
Big Move Scanner v2 — empirically calibrated from 10-year S&P 500 backtest.

Targets extreme dislocation setups: stocks already deep below SMA200 that
then have an additional panic selloff day. This setup produced a 33.32× lift
and 14.56% precision for 30%+ moves within 42 trading days (2015–2025).

Contrast with scanner.py (recovery screener):
  scanner.py  requires price > 75% of SMA200  (healthy stock having a bad day)
  scanner_v2  requires price < 70% of SMA200  (extreme dislocation — opposite)

Top findings from bigmove_research.py:
  Day < -5% + Price < 70% SMA200  →  33.32× lift, 14.56% precision, +39.3% avg
  Day < -5% + RSI < 30            →  18.34× lift,  8.01% precision, +39.7% avg
  Price < 70% SMA200 + RSI < 30  →  16.25× lift,  7.10% precision, +40.2% avg

Usage: imported by main.py; not run directly.
"""
import asyncio
import random
import pandas as pd
import pandas_ta_classic as ta
import yfinance as yf
import logging
import pathlib
from typing import List
from logging.handlers import RotatingFileHandler

from backend.scanner import (
    _is_rate_limit,
    _fetch_market_caps_bulk_async,
    _EXCHANGE_MAP,
    get_full_market_tickers,
)

logger = logging.getLogger("scanner_v2")
logger.setLevel(logging.DEBUG)
_LOG_PATH = pathlib.Path(__file__).parent / "scanner_v2.log"
_file_handler = RotatingFileHandler(str(_LOG_PATH), maxBytes=5_000_000, backupCount=3)
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
logger.addHandler(_file_handler)

# Screening parameters — calibrated from 10-year S&P 500 big-move research (2015–2025)
#
# Strategy: extreme dislocation recovery — stocks already deeply distressed
# that then have another panic selloff day. When the combo fires, historical
# precision is ~14–15% for 30%+ return within 42 trading days.
# Validated via: python3 -m backend.bigmove_research
#
CONFIG_V2 = {
    "MIN_MARKET_CAP":    1_000_000_000,  # > $1B (same liquidity floor as v1)
    "MIN_PRICE":         5.0,            # > $5 (no penny stocks)
    "MIN_VOLUME":        500_000,        # > 500K shares on the selloff day
    "MAX_DAY_CHANGE":   -5.0,            # day change < −5% (14.91× lift alone)
    "MIN_RVOL":          1.5,            # RVOL > 1.5× (lowered — RVOL not top predictor)
    "MAX_RSI":           35.0,           # RSI < 35 (RSI<30: 3.93× lift; 30–35: 2.0× lift)
    "MAX_SMA200_RATIO":  0.70,           # price < 70% of SMA200 — INVERTED from v1's MIN
    "MAX_SMA50_RATIO":   0.90,           # price < 90% of SMA50 (5.45× lift)
}


def get_active_filters_v2() -> list[str]:
    return [
        f"Day change < {CONFIG_V2['MAX_DAY_CHANGE']:.0f}% (panic selloff into distress)",
        f"Market Cap > ${CONFIG_V2['MIN_MARKET_CAP'] / 1_000_000_000:.0f}B",
        f"Price > ${CONFIG_V2['MIN_PRICE']}",
        f"Volume > {CONFIG_V2['MIN_VOLUME'] / 1000:.0f}K shares",
        f"RVOL > {CONFIG_V2['MIN_RVOL']}× (volume confirmation)",
        f"RSI(14) < {CONFIG_V2['MAX_RSI']:.0f} (oversold)",
        f"Price < {int(CONFIG_V2['MAX_SMA200_RATIO'] * 100)}% of SMA200 (extreme dislocation)",
        f"Price ≤ {int(CONFIG_V2['MAX_SMA50_RATIO'] * 100)}% of SMA50 (below short-term MA)",
    ]


def _filter_ticker_v2(ticker: str, data: pd.DataFrame, market_caps: dict) -> dict | None:
    """
    Extreme dislocation screener — 7 filters, calibrated from 10-year backtest.
    Finds stocks deeply below SMA200 that had a further panic selloff today.
    Backtest: 33.32× lift, ~15% precision for 30%+ in 42 days (10-yr S&P 500).
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
        day_change = (price - prev_close) / prev_close   # decimal

        live_vol = info.get("last_volume")
        volume   = live_vol if live_vol is not None else int(raw_vol)

        # ── Filter 1: Market cap + price ─────────────────────────────────────
        if market_cap < CONFIG_V2["MIN_MARKET_CAP"] or price <= CONFIG_V2["MIN_PRICE"]:
            logger.debug(f"{ticker} failed MC/Price")
            return None

        # ── Filter 2: Panic selloff — day change < −5% ───────────────────────
        if day_change * 100 >= CONFIG_V2["MAX_DAY_CHANGE"]:
            logger.debug(f"{ticker} failed day change: {day_change * 100:.2f}%")
            return None

        # ── Filter 3: Volume > 500K ───────────────────────────────────────────
        if volume < CONFIG_V2["MIN_VOLUME"]:
            logger.debug(f"{ticker} failed Volume: {volume}")
            return None

        # ── Filter 4: RVOL > 1.5× ─────────────────────────────────────────────
        avg_vol_20 = float(df["Volume"].rolling(window=20).mean().iloc[-1])
        vol_ratio  = round(volume / avg_vol_20, 2) if avg_vol_20 > 0 else 1.0
        if vol_ratio < CONFIG_V2["MIN_RVOL"]:
            logger.debug(f"{ticker} failed RVOL: {vol_ratio:.2f}")
            return None

        # ── Filter 5: RSI(14) < 35 ────────────────────────────────────────────
        rsi_series = ta.rsi(df["Close"], length=14)
        if rsi_series is None or rsi_series.empty:
            return None
        curr_rsi = round(float(rsi_series.iloc[-1]), 1)
        if curr_rsi >= CONFIG_V2["MAX_RSI"]:
            logger.debug(f"{ticker} failed RSI: {curr_rsi:.1f}")
            return None

        # ── Filter 6: Price < 70% of SMA200 (extreme dislocation) ────────────
        # NOTE: This is the INVERSE of scanner.py's Filter 6.
        # scanner.py requires price > 75% of SMA200 (uptrend intact).
        # scanner_v2 requires price < 70% of SMA200 (extreme dislocation).
        sma200_series = df["Close"].rolling(window=200).mean()
        curr_sma200   = float(sma200_series.iloc[-1])
        if price >= curr_sma200 * CONFIG_V2["MAX_SMA200_RATIO"]:
            logger.debug(f"{ticker} not deeply distressed: {price:.2f} >= 70% × {curr_sma200:.2f}")
            return None

        # ── Filter 7: Price < 90% of SMA50 ───────────────────────────────────
        sma50_series   = df["Close"].rolling(window=50).mean()
        curr_sma50_raw = float(sma50_series.iloc[-1])
        if curr_sma50_raw > 0 and price / curr_sma50_raw >= CONFIG_V2["MAX_SMA50_RATIO"]:
            logger.debug(f"{ticker} price/SMA50={price / curr_sma50_raw:.2f} ≥ {CONFIG_V2['MAX_SMA50_RATIO']}")
            return None

        # ── Display indicators ────────────────────────────────────────────────
        ema8_series = ta.ema(df["Close"], length=8)
        curr_ema8   = round(float(ema8_series.iloc[-1]), 2) if ema8_series is not None and not ema8_series.empty else round(price, 2)

        ema20_series = ta.ema(df["Close"], length=20)
        ema50_series = ta.ema(df["Close"], length=50)
        ema200_series = ta.ema(df["Close"], length=200)
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

        atr_series = ta.atr(df["High"], df["Low"], df["Close"], length=14)
        curr_atr   = round(float(atr_series.iloc[-1]), 2) if atr_series is not None and not atr_series.empty else round(price * 0.02, 2)

        # Entry / stop levels for dislocation recovery plays (wider stops for volatile names)
        entry1 = round(price,           2)   # buy today's close
        entry2 = round(curr_ema8,       2)   # wait for slight stabilisation
        entry3 = round(curr_bb_lower,   2)   # maximum discount at BB lower
        stop1  = round(price - 1.5 * curr_atr, 2)   # wider stop: 1.5× ATR
        stop2  = round(price - 2.5 * curr_atr, 2)   # deep stop: 2.5× ATR
        stop3  = round(price * 0.85,    2)   # hard floor: 15% max loss

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


async def screen_stocks_v2(tickers: List[str]):
    """
    Screens tickers for extreme dislocation setups using CONFIG_V2 filters.
    Yields result dicts and progress/warning events — identical event shape to screen_stocks().
    """
    period      = "2y"
    chunk_size  = 50
    chunks      = [tickers[i:i + chunk_size] for i in range(0, len(tickers), chunk_size)]

    queue: asyncio.Queue = asyncio.Queue()
    processed_count = 0
    chunk_semaphore      = asyncio.Semaphore(5)
    fast_info_semaphore  = asyncio.Semaphore(20)

    async def process_chunk(chunk: list[str], chunk_idx: int) -> None:
        nonlocal processed_count
        await asyncio.sleep(chunk_idx * 0.5)
        logger.debug(f"v2 chunk {chunk_idx}: {chunk}")

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
                    logger.warning(f"v2 chunk {chunk_idx} attempt {attempt + 1} failed ({type(e).__name__}), retrying in {wait:.1f}s")
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"v2 chunk {chunk_idx} failed after {retries} attempts: {e}")
                    for ticker in chunk:
                        processed_count += 1
                        await queue.put({"status": "progress", "current": processed_count, "ticker": ticker})
                    await queue.put(None)
                    return

        for ticker in chunk:
            processed_count += 1
            await queue.put({"status": "progress", "current": processed_count, "ticker": ticker})
            result = _filter_ticker_v2(ticker, data, market_caps)
            if result:
                logger.info(f"v2 match: {ticker} at ${result['price']} ({result['change']}%)")
                await queue.put(result)
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
