import asyncio
import io
import pathlib
import random
import time
import requests
import pandas as pd
import pandas_ta_classic as ta
import yfinance as yf
import logging
from logging.handlers import RotatingFileHandler
from typing import List, Dict, Any, Optional
from rich.logging import RichHandler
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

# Only configure the root logger if nothing else has done so yet (e.g. main.py).
# This keeps basicConfig from being a no-op when imported, while still working
# when scanner.py is run standalone.
if not logging.root.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True)]
    )

logger = logging.getLogger("scanner")
logger.setLevel(logging.DEBUG)
_LOG_PATH = pathlib.Path(__file__).parent / 'scanner.log'
file_handler = RotatingFileHandler(str(_LOG_PATH), maxBytes=5_000_000, backupCount=3)
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

# Suppress noisy logs from dependencies
logging.getLogger("yfinance").setLevel(logging.WARNING)
logging.getLogger("peewee").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# Screening Parameters — calibrated from 5-year S&P 500 reverse backtest (2021–2026)
#
# Strategy: oversold mean-reversion (recovery) — buy panic selloffs in uptrending large caps
# Backtest result: 68% 3-month win rate, +6.7% avg return (666K ticker-days tested)
# Validated via: python3 -m backend.reverse_backtest --validate-recovery
#
CONFIG = {
    "MIN_MARKET_CAP":    1_000_000_000,  # > $1B (liquidity / institutional presence)
    "MIN_PRICE":         5.0,            # > $5 (no penny stocks)
    "MIN_VOLUME":        500_000,        # > 500K shares on the selloff day
    "MAX_DAY_CHANGE":   -5.0,            # day change < −5% (panic selloff entry signal)
    "MIN_RVOL":          3.5,            # RVOL > 3.5× (capitulation volume surge)
    "MAX_RSI":           30.0,           # RSI(14) < 30 (extreme oversold / capitulation)
    "MIN_SMA200_RATIO":  0.75,           # price > 75% of SMA200 (structural uptrend intact)
}

_RATE_LIMIT_SIGNALS = ('rate limit', 'too many requests', 'yfratelimit')

_EXCHANGE_MAP = {
    'NMS': 'NASDAQ', 'NGM': 'NASDAQ', 'NCM': 'NASDAQ',
    'NYQ': 'NYSE',   'PCX': 'NYSE',
}

def _is_rate_limit(exc: Exception) -> bool:
    return any(s in str(exc).lower() or s in type(exc).__name__.lower() for s in _RATE_LIMIT_SIGNALS)

def _fetch_market_caps_bulk(chunk: list[str]) -> dict[str, dict]:
    """
    Fetches market caps and exchange for a chunk of tickers using yfinance fast_info.
    Retries rate-limited tickers with exponential backoff (15s → 30s → 60s).
    Falls back to a large default on exhausted retries so the ticker is not
    unfairly dropped — all S&P 500 stocks clear the $1B minimum anyway.
    """
    result = {}
    for ticker in chunk:
        for attempt in range(4):
            try:
                fi = yf.Ticker(ticker).fast_info
                mc = fi.market_cap
                lp = fi.last_price
                lv = fi.last_volume
                exc = _EXCHANGE_MAP.get(getattr(fi, 'exchange', ''), 'NASDAQ')
                result[ticker] = {
                    'market_cap': float(mc) if mc is not None else 0.0,
                    'exchange': exc,
                    'last_price': float(lp) if lp is not None else None,
                    'last_volume': int(lv) if lv is not None else None,
                }
                break
            except Exception as e:
                if _is_rate_limit(e) and attempt < 3:
                    wait = 15 * (2 ** attempt)   # 15s, 30s, 60s
                    logger.warning(f"Rate limited on {ticker} fast_info, retrying in {wait}s (attempt {attempt + 1}/3)")
                    time.sleep(wait)
                else:
                    if _is_rate_limit(e):
                        logger.warning(f"Rate limit persists for {ticker} after retries; assuming large-cap NASDAQ")
                        result[ticker] = {'market_cap': float(CONFIG["MIN_MARKET_CAP"] * 10), 'exchange': 'NASDAQ', 'last_price': None, 'last_volume': None}
                    else:
                        logger.debug(f"Could not fetch market cap for {ticker}: {e}")
                        result[ticker] = {'market_cap': 0.0, 'exchange': 'NASDAQ', 'last_price': None, 'last_volume': None}
                    break
    return result


async def _fetch_market_caps_bulk_async(chunk: list[str], semaphore: asyncio.Semaphore) -> dict[str, dict]:
    """
    Async version of _fetch_market_caps_bulk. All tickers in the chunk are
    fetched concurrently, bounded by semaphore. Retries rate-limited calls
    with exponential backoff + jitter to avoid synchronized retry storms.
    """
    async def fetch_one(ticker: str) -> tuple[str, dict]:
        async with semaphore:
            for attempt in range(4):
                try:
                    fi = await asyncio.to_thread(lambda t=ticker: yf.Ticker(t).fast_info)
                    mc = fi.market_cap
                    lp = fi.last_price
                    lv = fi.last_volume
                    exc = _EXCHANGE_MAP.get(getattr(fi, 'exchange', ''), 'NASDAQ')
                    return ticker, {
                        'market_cap': float(mc) if mc is not None else 0.0,
                        'exchange': exc,
                        'last_price': float(lp) if lp is not None else None,
                        'last_volume': int(lv) if lv is not None else None,
                    }
                except Exception as e:
                    if _is_rate_limit(e) and attempt < 3:
                        wait = 15 * (2 ** attempt) + random.uniform(0, 5)
                        logger.warning(f"Rate limited on {ticker} fast_info, retrying in {wait:.1f}s (attempt {attempt + 1}/3)")
                        await asyncio.sleep(wait)
                        continue
                    if _is_rate_limit(e):
                        logger.warning(f"Rate limit persists for {ticker} after retries; assuming large-cap NASDAQ")
                        return ticker, {'market_cap': float(CONFIG["MIN_MARKET_CAP"] * 10), 'exchange': 'NASDAQ', 'last_price': None, 'last_volume': None}
                    logger.debug(f"Could not fetch market cap for {ticker}: {e}")
                    return ticker, {'market_cap': 0.0, 'exchange': 'NASDAQ', 'last_price': None, 'last_volume': None}
        return ticker, {'market_cap': 0.0, 'exchange': 'NASDAQ', 'last_price': None, 'last_volume': None}

    pairs = await asyncio.gather(*[fetch_one(t) for t in chunk])
    return dict(pairs)


def get_active_filters():
    return [
        f"Day change < {CONFIG['MAX_DAY_CHANGE']:.0f}% (panic selloff)",
        f"Market Cap > ${CONFIG['MIN_MARKET_CAP']/1_000_000_000:.0f}B",
        f"Price > ${CONFIG['MIN_PRICE']}",
        f"Volume > {CONFIG['MIN_VOLUME']/1000:.0f}K shares",
        f"RVOL > {CONFIG['MIN_RVOL']}× (capitulation volume)",
        f"RSI(14) < {CONFIG['MAX_RSI']:.0f} (extreme oversold)",
        f"Price > {int(CONFIG['MIN_SMA200_RATIO']*100)}% of SMA200 (uptrend intact)",
        "EMA20 > EMA50 > EMA200 (macro trend aligned)",
    ]

FALLBACK_TICKERS = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "NVDA", "AMD", "NFLX", "PYPL"]

def get_full_market_tickers() -> tuple[list[str], bool]:
    """
    Fetches S&P 500 tickers. Returns (tickers, is_full_list).
    is_full_list=False means the S&P 500 CSV was unavailable and a fallback was used.
    """
    try:
        url = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv"
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        tickers = [t.replace('.', '-') for t in df['Symbol'].tolist()]
        return tickers, True
    except Exception as e:
        logger.error(f"Failed to fetch S&P 500 list, using fallback: {e}")
        return FALLBACK_TICKERS, False

def _filter_ticker(ticker: str, data: pd.DataFrame, market_caps: dict) -> dict | None:
    """
    Recovery / mean-reversion screener — 8 filters, calibrated from 5-year backtest.
    Finds large-cap stocks in macro uptrends that had a panic selloff today.
    Backtest: 68% 3-month win rate, +6.7% avg return (5-yr S&P 500, 666K ticker-days).
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

        info       = market_caps.get(ticker, {'market_cap': 0.0, 'exchange': 'NASDAQ', 'last_price': None})
        market_cap = info['market_cap']
        exchange   = info['exchange']

        raw_price = info.get('last_price')
        if raw_price is None or pd.isna(raw_price):
            logger.debug(f"{ticker} skipped — no live price from fast_info")
            return None
        raw_prev = df['Close'].iloc[-2]
        raw_vol  = df['Volume'].iloc[-1]
        if pd.isna(raw_prev) or pd.isna(raw_vol):
            return None

        price      = float(raw_price)
        prev_close = float(raw_prev)
        if prev_close == 0:
            return None
        day_change = (price - prev_close) / prev_close   # decimal

        live_vol = info.get('last_volume')
        volume   = live_vol if live_vol is not None else int(raw_vol)

        # ── Filter 1: Market cap + price ────────────────────────────────────
        if market_cap < CONFIG["MIN_MARKET_CAP"] or price <= CONFIG["MIN_PRICE"]:
            logger.debug(f"{ticker} failed MC/Price")
            return None

        # ── Filter 2: Panic selloff — day change < −5% ──────────────────────
        if day_change * 100 >= CONFIG["MAX_DAY_CHANGE"]:
            logger.debug(f"{ticker} failed day change: {day_change*100:.2f}%")
            return None

        # ── Filter 3: Volume > 500K ──────────────────────────────────────────
        if volume < CONFIG["MIN_VOLUME"]:
            logger.debug(f"{ticker} failed Volume: {volume}")
            return None

        logger.debug(f"{ticker} PASSED base filters: Price={price}, MC={market_cap}, Vol={volume}")

        # ── Filter 4: RVOL > 3.5× ────────────────────────────────────────────
        avg_vol_20 = float(df['Volume'].rolling(window=20).mean().iloc[-1])
        vol_ratio  = round(volume / avg_vol_20, 2) if avg_vol_20 > 0 else 1.0
        if vol_ratio < CONFIG["MIN_RVOL"]:
            logger.debug(f"{ticker} failed RVOL: {vol_ratio:.2f}")
            return None

        # ── Filter 5: RSI(14) < 30 ────────────────────────────────────────────
        rsi_series = ta.rsi(df['Close'], length=14)
        if rsi_series is None or rsi_series.empty:
            return None
        curr_rsi = round(float(rsi_series.iloc[-1]), 1)
        if curr_rsi >= CONFIG["MAX_RSI"]:
            logger.debug(f"{ticker} failed RSI: {curr_rsi:.1f}")
            return None

        # ── Filter 6: Price > 75% of SMA200 ──────────────────────────────────
        sma200_series = df['Close'].rolling(window=200).mean()
        curr_sma200   = float(sma200_series.iloc[-1])
        if price < curr_sma200 * CONFIG["MIN_SMA200_RATIO"]:
            logger.debug(f"{ticker} below SMA200 ratio: {price:.2f} vs {curr_sma200:.2f}")
            return None

        # ── Filter 7: EMA stack EMA20 > EMA50 > EMA200 ───────────────────────
        ema20_series  = ta.ema(df['Close'], length=20)
        ema50_series  = ta.ema(df['Close'], length=50)
        ema200_series = ta.ema(df['Close'], length=200)
        if any(s is None or s.empty for s in [ema20_series, ema50_series, ema200_series]):
            return None
        curr_ema20  = round(float(ema20_series.iloc[-1]),  2)
        curr_ema50  = round(float(ema50_series.iloc[-1]),  2)
        curr_ema200 = round(float(ema200_series.iloc[-1]), 2)
        if not (curr_ema20 > curr_ema50 > curr_ema200):
            logger.debug(f"{ticker} failed EMA stack: {curr_ema20}/{curr_ema50}/{curr_ema200}")
            return None

        # ── Display indicators (informational, not used as filters) ──────────
        ema8_series = ta.ema(df['Close'], length=8)
        curr_ema8   = round(float(ema8_series.iloc[-1]), 2) if ema8_series is not None and not ema8_series.empty else round(price, 2)

        macd_result = ta.macd(df['Close'])
        if macd_result is not None and not macd_result.empty:
            _mc = next((c for c in macd_result.columns if c.startswith('MACD_')),  None)
            _ms = next((c for c in macd_result.columns if c.startswith('MACDs_')), None)
            _mh = next((c for c in macd_result.columns if c.startswith('MACDh_')), None)
            curr_macd        = round(float(macd_result[_mc].iloc[-1]), 4) if _mc else 0.0
            curr_macd_signal = round(float(macd_result[_ms].iloc[-1]), 4) if _ms else 0.0
            curr_macd_hist   = round(float(macd_result[_mh].iloc[-1]), 4) if _mh else 0.0
        else:
            curr_macd = curr_macd_signal = curr_macd_hist = 0.0

        curr_sma50 = round(float(df['Close'].rolling(window=50).mean().iloc[-1]), 2)

        bb_result = ta.bbands(df['Close'], length=20, std=2)
        if bb_result is not None and not bb_result.empty:
            _bl = next((c for c in bb_result.columns if c.startswith('BBL_')), None)
            _bm = next((c for c in bb_result.columns if c.startswith('BBM_')), None)
            _bu = next((c for c in bb_result.columns if c.startswith('BBU_')), None)
            curr_bb_lower  = round(float(bb_result[_bl].iloc[-1]), 2) if _bl else round(price * 0.97, 2)
            curr_bb_middle = round(float(bb_result[_bm].iloc[-1]), 2) if _bm else round(price, 2)
            curr_bb_upper  = round(float(bb_result[_bu].iloc[-1]), 2) if _bu else round(price * 1.03, 2)
        else:
            curr_bb_lower  = round(price * 0.97, 2)
            curr_bb_middle = round(price, 2)
            curr_bb_upper  = round(price * 1.03, 2)

        atr_series = ta.atr(df['High'], df['Low'], df['Close'], length=14)
        curr_atr   = round(float(atr_series.iloc[-1]), 2) if atr_series is not None and not atr_series.empty else round(price * 0.02, 2)

        # Recovery entry / stop levels (3 scenarios for the snap-back trade)
        entry1 = round(price,       2)   # buy today's close
        entry2 = round(curr_ema8,   2)   # wait for EMA8 reclaim
        entry3 = round(curr_sma200, 2)   # deep value at SMA200
        stop1  = round(price      - 1.0 * curr_atr, 2)
        stop2  = round(curr_ema8  - 0.5 * curr_atr, 2)
        stop3  = round(curr_sma50 - 0.5 * curr_atr, 2)

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


async def screen_stocks(tickers: List[str]):
    """
    Screens a list of tickers based on the defined filters.
    Yields results (dict) or progress events as they are processed.
    Up to 5 chunks download in parallel; fast_info calls are capped at 20 concurrent.
    """
    # Market regime gate: warn if SPY is below its 10-day EMA
    try:
        spy_raw = yf.download('SPY', period='30d', progress=False, threads=False)
        spy_close = spy_raw['Close'].dropna()
        if isinstance(spy_raw.columns, pd.MultiIndex):
            spy_close = spy_raw['Close']['SPY'].dropna()
        spy_ema10 = spy_close.ewm(span=10, adjust=False).mean()
        if float(spy_close.iloc[-1]) < float(spy_ema10.iloc[-1]):
            yield {"status": "warning", "message": "Market regime: SPY below 10-EMA — breakout signals less reliable today"}
    except Exception:
        pass

    period = "2y"
    chunk_size = 50
    chunks = [tickers[i:i + chunk_size] for i in range(0, len(tickers), chunk_size)]

    queue: asyncio.Queue = asyncio.Queue()
    processed_count = 0
    chunk_semaphore = asyncio.Semaphore(5)   # max 5 concurrent yf.download calls
    fast_info_semaphore = asyncio.Semaphore(20)  # max 20 concurrent fast_info calls (global)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        transient=True,
    ) as progress:
        task = progress.add_task("[green]Scanning market...", total=len(tickers))

        async def process_chunk(chunk: list[str], chunk_idx: int) -> None:
            nonlocal processed_count
            # Stagger starts so chunks don't all hammer Yahoo simultaneously
            await asyncio.sleep(chunk_idx * 0.5)

            logger.debug(f"Requesting chunk {chunk_idx}: {chunk}")

            retries = 3
            data, market_caps = None, {}
            for attempt in range(retries):
                try:
                    async with chunk_semaphore:
                        data = await asyncio.wait_for(
                            asyncio.to_thread(
                                yf.download, chunk,
                                period=period, group_by='ticker', progress=False, threads=False
                            ),
                            timeout=180.0,
                        )
                    # Semaphore released — next chunk can start downloading.
                    if data is None or data.empty:
                        raise ValueError("Empty data returned")
                    # fast_info calls run concurrently with other chunks' downloads,
                    # bounded only by fast_info_semaphore(20).
                    market_caps = await _fetch_market_caps_bulk_async(chunk, fast_info_semaphore)
                    break
                except Exception as e:
                    if attempt < retries - 1:
                        wait = (60 * (attempt + 1) if _is_rate_limit(e) else 5 * (attempt + 1)) + random.uniform(0, 5)
                        logger.warning(f"Chunk {chunk_idx} attempt {attempt + 1} failed ({type(e).__name__}), retrying in {wait:.1f}s")
                        await asyncio.sleep(wait)
                    else:
                        logger.error(f"Chunk {chunk_idx} failed after {retries} attempts: {e}")
                        for ticker in chunk:
                            processed_count += 1
                            progress.update(task, advance=1, description=f"[red]Error in chunk")
                            await queue.put({"status": "progress", "current": processed_count, "ticker": ticker})
                        await queue.put(None)
                        return

            for ticker in chunk:
                processed_count += 1
                progress.update(task, advance=1, description=f"[green]Scanning {ticker}...")
                await queue.put({"status": "progress", "current": processed_count, "ticker": ticker})
                result = _filter_ticker(ticker, data, market_caps)
                if result:
                    logger.info(f"Found breakout: {ticker} at ${result['price']} ({result['change']}%)")
                    await queue.put(result)
                await asyncio.sleep(0)  # yield so other chunks' I/O can progress between tickers
            await queue.put(None)

        tasks = [asyncio.create_task(process_chunk(chunk, i)) for i, chunk in enumerate(chunks)]

        done = 0
        while done < len(chunks):
            item = await queue.get()
            if item is None:
                done += 1
            else:
                yield item

if __name__ == "__main__":
    async def _test():
        tickers, _ = get_full_market_tickers()
        tickers = tickers[:20]
        print(f"Testing with {len(tickers)} tickers...")
        async for res in screen_stocks(tickers):
            print(f"Found: {res}")
    asyncio.run(_test())
