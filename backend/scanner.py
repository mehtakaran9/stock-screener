import asyncio
import io
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

def _fetch_market_caps_bulk(chunk: list[str]) -> dict[str, float]:
    """Fetches market caps for a chunk of tickers using yfinance fast_info."""
    result = {}
    for ticker in chunk:
        try:
            mc = yf.Ticker(ticker).fast_info.market_cap
            result[ticker] = float(mc) if mc is not None else 0.0
        except Exception:
            result[ticker] = 0.0
    return result


def get_active_filters():
    return [
        f"Day Change > {int(CONFIG['MIN_DAY_CHANGE']*100)}%",
        f"Market Cap > ${CONFIG['MIN_MARKET_CAP']/1_000_000_000:.0f}B",
        f"Price > ${CONFIG['MIN_PRICE']}",
        f"Above {int(CONFIG['SMA200_RATIO']*100)}% SMA200",
        f"Price > {int(CONFIG['EMA8_RATIO']*100)}% of 8EMA",
        f"Volume > {CONFIG['MIN_VOLUME']/1000:.0f}K"
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
                            timeout=120.0
                        )
                        if downloaded_data is None or downloaded_data.empty:
                            raise ValueError("Empty data returned")
                        data = downloaded_data
                        break
                    except Exception as e:
                        if attempt < retries - 1:
                            await asyncio.sleep(5 * (attempt + 1))
                            continue
                        else:
                            raise

                market_caps = await asyncio.to_thread(_fetch_market_caps_bulk, chunk)

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

                        market_cap = market_caps.get(ticker, 0.0)

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

                        result = {
                            "ticker": ticker,
                            "price": round(float(price), 2),
                            "change": round(float(day_change * 100), 2),
                            "volume": int(volume),
                            "market_cap": int(market_cap),
                            "ema8": round(float(curr_ema8), 2),
                            "sma200": round(float(curr_sma200), 2)
                        }
                        logger.info(f"[bold green]Found breakout:[/bold green] {ticker} at ${result['price']} ({result['change']}%)")
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
