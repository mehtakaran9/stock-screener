import asyncio
import io
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

# Configure logging with Rich
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True)]
)

logger = logging.getLogger("scanner")
logger.setLevel(logging.DEBUG)
file_handler = RotatingFileHandler('scanner.log', maxBytes=5_000_000, backupCount=3)
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

# Suppress noisy logs from dependencies
logging.getLogger("yfinance").setLevel(logging.WARNING)
logging.getLogger("peewee").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# Screening Parameters (Single Source of Truth)
CONFIG = {
    "MIN_MARKET_CAP": 1_000_000_000,
    "MIN_PRICE": 5.0,
    "MIN_DAY_CHANGE": 0.03,
    "MIN_VOLUME": 500_000,
    "SMA200_RATIO": 0.75,
    "EMA8_RATIO": 0.80
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
                exc = _EXCHANGE_MAP.get(getattr(fi, 'exchange', ''), 'NASDAQ')
                result[ticker] = {'market_cap': float(mc) if mc is not None else 0.0, 'exchange': exc}
                break
            except Exception as e:
                if _is_rate_limit(e) and attempt < 3:
                    wait = 15 * (2 ** attempt)   # 15s, 30s, 60s
                    logger.warning(f"Rate limited on {ticker} fast_info, retrying in {wait}s (attempt {attempt + 1}/3)")
                    time.sleep(wait)
                else:
                    if _is_rate_limit(e):
                        logger.warning(f"Rate limit persists for {ticker} after retries; assuming large-cap NASDAQ")
                        result[ticker] = {'market_cap': float(CONFIG["MIN_MARKET_CAP"] * 10), 'exchange': 'NASDAQ'}
                    else:
                        logger.debug(f"Could not fetch market cap for {ticker}: {e}")
                        result[ticker] = {'market_cap': 0.0, 'exchange': 'NASDAQ'}
                    break
    return result


def get_active_filters():
    return [
        f"Day Change > {int(CONFIG['MIN_DAY_CHANGE']*100)}%",
        f"Market Cap > ${CONFIG['MIN_MARKET_CAP']/1_000_000_000:.0f}B",
        f"Price > ${CONFIG['MIN_PRICE']}",
        f"Volume > {CONFIG['MIN_VOLUME']/1000:.0f}K",
        f"Price > {int(CONFIG['SMA200_RATIO']*100)}% of SMA200",
        f"Price > {int(CONFIG['EMA8_RATIO']*100)}% of EMA8",
        "RSI(14) 50–70",
        "MACD Hist > 0",
        "Price > EMA50",
        "Price > EMA200",
        "Price < BB Upper",
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

async def screen_stocks(tickers: List[str]):
    """
    Screens a list of tickers based on the defined filters.
    Yields results (dict) or progress (int) as they are processed.
    """
    period = "2y"
    processed_count = 0
    chunk_size = 50

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        transient=True,
    ) as progress:
        task = progress.add_task("[green]Scanning market...", total=len(tickers))

        for i in range(0, len(tickers), chunk_size):
            chunk = tickers[i:i + chunk_size]
            try:
                await asyncio.sleep(2 + (i % 3))

                logger.debug(f"Requesting chunk: {chunk}")

                retries = 3
                for attempt in range(retries):
                    try:
                        downloaded_data = await asyncio.wait_for(
                            asyncio.to_thread(
                                yf.download, chunk,
                                period=period, group_by='ticker', progress=False, threads=False
                            ),
                            timeout=18000.0  # 5 hours
                        )
                        if downloaded_data is None or downloaded_data.empty:
                            raise ValueError("Empty data returned")
                        data = downloaded_data
                        break
                    except Exception as e:
                        if attempt < retries - 1:
                            wait = 60 * (attempt + 1) if _is_rate_limit(e) else 5 * (attempt + 1)
                            logger.warning(f"Chunk download attempt {attempt + 1} failed ({type(e).__name__}), retrying in {wait}s")
                            await asyncio.sleep(wait)
                            continue
                        else:
                            raise

                market_caps = await asyncio.wait_for(
                    asyncio.to_thread(_fetch_market_caps_bulk, chunk),
                    timeout=18000.0  # 5 hours
                )

                for ticker in chunk:
                    processed_count += 1
                    progress.update(task, advance=1, description=f"[green]Scanning {ticker}...")

                    yield {"status": "progress", "current": processed_count, "ticker": ticker}

                    try:
                        if isinstance(data.columns, pd.MultiIndex):
                            if ticker not in data.columns.levels[0]:
                                logger.debug(f"{ticker} not in data columns")
                                continue
                            df = data[ticker].dropna()
                        else:
                            df = data.dropna()

                        if len(df) < 200:
                            logger.debug(f"{ticker} data too short: {len(df)}")
                            continue

                        info = market_caps.get(ticker, {'market_cap': 0.0, 'exchange': 'NASDAQ'})
                        market_cap = info['market_cap']
                        exchange = info['exchange']

                        raw_price = df['Close'].iloc[-1]
                        raw_prev = df['Close'].iloc[-2]
                        raw_vol = df['Volume'].iloc[-1]
                        if pd.isna(raw_price) or pd.isna(raw_prev) or pd.isna(raw_vol):
                            logger.debug(f"{ticker} has NaN in price/volume data")
                            continue
                        price = float(raw_price)
                        prev_close = float(raw_prev)
                        if prev_close == 0:
                            logger.debug(f"{ticker} prev_close is zero")
                            continue
                        day_change = (price - prev_close) / prev_close
                        volume = int(raw_vol)

                        if market_cap < CONFIG["MIN_MARKET_CAP"] or price <= CONFIG["MIN_PRICE"]:
                            logger.debug(f"{ticker} failed MC/Price: MC={market_cap}, Price={price}")
                            continue

                        if day_change <= CONFIG["MIN_DAY_CHANGE"]:
                            logger.debug(f"{ticker} failed Change: {day_change*100:.2f}%")
                            continue

                        if volume < CONFIG["MIN_VOLUME"]:
                            logger.debug(f"{ticker} failed Volume: {volume}")
                            continue

                        logger.debug(f"{ticker} PASSED base filters: Price={price}, MC={market_cap}, Vol={volume}")

                        sma200_series = df['Close'].rolling(window=200).mean()
                        curr_sma200 = float(sma200_series.iloc[-1])
                        if price < curr_sma200 * CONFIG["SMA200_RATIO"]:
                            logger.debug(f"{ticker} below {CONFIG['SMA200_RATIO']*100}% SMA200: Price={price}, SMA200={curr_sma200}")
                            continue

                        ema8_series = ta.ema(df['Close'], length=8)
                        if ema8_series is None or ema8_series.empty:
                            continue
                        curr_ema8 = float(ema8_series.iloc[-1])

                        if price < curr_ema8 * CONFIG["EMA8_RATIO"]:
                            logger.debug(f"{ticker} below {CONFIG['EMA8_RATIO']*100}% 8EMA range: Price={price}, 8EMA={curr_ema8}")
                            continue

                        # --- Additional indicators (computed only for filter-passing stocks) ---

                        # RSI-14
                        rsi_series = ta.rsi(df['Close'], length=14)
                        curr_rsi = round(float(rsi_series.iloc[-1]), 1) if rsi_series is not None and not rsi_series.empty else 0.0

                        # MACD (12,26,9)
                        macd_result = ta.macd(df['Close'])
                        if macd_result is not None and not macd_result.empty:
                            _mc = next((c for c in macd_result.columns if c.startswith('MACD_')), None)
                            _ms = next((c for c in macd_result.columns if c.startswith('MACDs_')), None)
                            _mh = next((c for c in macd_result.columns if c.startswith('MACDh_')), None)
                            curr_macd = round(float(macd_result[_mc].iloc[-1]), 4) if _mc else 0.0
                            curr_macd_signal = round(float(macd_result[_ms].iloc[-1]), 4) if _ms else 0.0
                            curr_macd_hist = round(float(macd_result[_mh].iloc[-1]), 4) if _mh else 0.0
                        else:
                            curr_macd = curr_macd_signal = curr_macd_hist = 0.0

                        # Filter: RSI must be in [50, 70] — momentum confirmed, not overbought
                        if not (50 <= curr_rsi <= 70):
                            logger.debug(f"{ticker} failed RSI: {curr_rsi:.1f}")
                            continue

                        # Filter: MACD histogram must be positive — bullish crossover active
                        if curr_macd_hist <= 0:
                            logger.debug(f"{ticker} failed MACD hist: {curr_macd_hist:.4f}")
                            continue

                        # EMA50 / EMA200
                        ema50_series = ta.ema(df['Close'], length=50)
                        ema200_series = ta.ema(df['Close'], length=200)
                        if ema50_series is None or ema50_series.empty:
                            continue
                        if ema200_series is None or ema200_series.empty:
                            continue
                        curr_ema50 = round(float(ema50_series.iloc[-1]), 2)
                        curr_ema200 = round(float(ema200_series.iloc[-1]), 2)

                        # Filter: price > EMA50 — medium-term uptrend
                        if price <= curr_ema50:
                            logger.debug(f"{ticker} failed EMA50: Price={price}, EMA50={curr_ema50}")
                            continue

                        # Filter: price > EMA200 — long-term uptrend
                        if price <= curr_ema200:
                            logger.debug(f"{ticker} failed EMA200: Price={price}, EMA200={curr_ema200}")
                            continue

                        # SMA50
                        curr_sma50 = round(float(df['Close'].rolling(window=50).mean().iloc[-1]), 2)

                        # Bollinger Bands (20, 2)
                        bb_result = ta.bbands(df['Close'], length=20, std=2)
                        if bb_result is not None and not bb_result.empty:
                            _bl = next((c for c in bb_result.columns if c.startswith('BBL_')), None)
                            _bm = next((c for c in bb_result.columns if c.startswith('BBM_')), None)
                            _bu = next((c for c in bb_result.columns if c.startswith('BBU_')), None)
                            curr_bb_lower = round(float(bb_result[_bl].iloc[-1]), 2) if _bl else round(price * 0.97, 2)
                            curr_bb_middle = round(float(bb_result[_bm].iloc[-1]), 2) if _bm else round(price, 2)
                            curr_bb_upper = round(float(bb_result[_bu].iloc[-1]), 2) if _bu else round(price * 1.03, 2)
                        else:
                            curr_bb_lower = round(price * 0.97, 2)
                            curr_bb_middle = round(price, 2)
                            curr_bb_upper = round(price * 1.03, 2)

                        # Filter: price < BB upper — not overextended above the upper band
                        if price >= curr_bb_upper:
                            logger.debug(f"{ticker} failed BB upper: Price={price}, BB_upper={curr_bb_upper}")
                            continue

                        # ATR-14
                        atr_series = ta.atr(df['High'], df['Low'], df['Close'], length=14)
                        curr_atr = round(float(atr_series.iloc[-1]), 2) if atr_series is not None and not atr_series.empty else round(price * 0.02, 2)

                        # Volume ratio vs 20-day average
                        avg_vol_20 = float(df['Volume'].rolling(window=20).mean().iloc[-1])
                        vol_ratio = round(volume / avg_vol_20, 2) if avg_vol_20 > 0 else 1.0

                        # Swing trade levels
                        # Entry: breakout now → EMA8 pullback → BB midline (SMA20) deep pullback
                        # Stop:  1 ATR below entry price → below EMA8 → below SMA50 (trend break)
                        entry1 = round(price, 2)
                        entry2 = round(curr_ema8, 2)
                        entry3 = round(curr_bb_middle, 2)
                        stop1 = round(price - 1.0 * curr_atr, 2)
                        stop2 = round(curr_ema8 - 0.5 * curr_atr, 2)
                        stop3 = round(curr_sma50 - 0.5 * curr_atr, 2)

                        result = {
                            "ticker": ticker,
                            "exchange": exchange,
                            "price": round(float(price), 2),
                            "change": round(float(day_change * 100), 2),
                            "volume": int(volume),
                            "vol_ratio": vol_ratio,
                            "market_cap": int(market_cap),
                            "rsi": curr_rsi,
                            "macd": curr_macd,
                            "macd_signal": curr_macd_signal,
                            "macd_hist": curr_macd_hist,
                            "ema8": round(float(curr_ema8), 2),
                            "ema50": curr_ema50,
                            "ema200": curr_ema200,
                            "sma50": curr_sma50,
                            "sma200": round(float(curr_sma200), 2),
                            "bb_upper": curr_bb_upper,
                            "bb_middle": curr_bb_middle,
                            "bb_lower": curr_bb_lower,
                            "atr14": curr_atr,
                            "entry1": entry1,
                            "entry2": entry2,
                            "entry3": entry3,
                            "stop1": stop1,
                            "stop2": stop2,
                            "stop3": stop3,
                        }
                        logger.info(f"Found breakout: {ticker} at ${result['price']} ({result['change']}%)")
                        yield result

                    except Exception as e:
                        logger.warning(f"Error processing {ticker} ({type(e).__name__}): {e}")
                        continue

            except Exception as e:
                logger.error(f"Error downloading chunk: {e}")
                processed_count += len(chunk)
                yield {"status": "progress", "current": processed_count, "ticker": "Error in chunk"}
                continue

if __name__ == "__main__":
    async def _test():
        tickers, _ = get_full_market_tickers()
        tickers = tickers[:20]
        print(f"Testing with {len(tickers)} tickers...")
        async for res in screen_stocks(tickers):
            print(f"Found: {res}")
    asyncio.run(_test())
