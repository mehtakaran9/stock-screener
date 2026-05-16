import asyncio
import pandas as pd
import pandas_ta_classic as ta
import yfinance as yf
from yahooquery import Ticker
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

def get_active_filters():
    return [
        f"Day Change > {int(CONFIG['MIN_DAY_CHANGE']*100)}%",
        f"Market Cap > ${CONFIG['MIN_MARKET_CAP']/1_000_000_000:.0f}B",
        f"Price > ${CONFIG['MIN_PRICE']}",
        f"Above {int(CONFIG['SMA200_RATIO']*100)}% SMA200",
        f"Price > {int(CONFIG['EMA8_RATIO']*100)}% of 8EMA",
        f"Volume > {CONFIG['MIN_VOLUME']/1000:.0f}K"
    ]

def get_full_market_tickers() -> List[str]:
    """
    Fetches a list of active US tickers. 
    """
    try:
        url = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv"
        df = pd.read_csv(url)
        tickers = df['Symbol'].tolist()
        return [t.replace('.', '-') for t in tickers]
    except Exception as e:
        logger.error(f"Error fetching tickers: {e}")
        return ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "NVDA", "AMD", "NFLX", "PYPL"]

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
                        downloaded_data = await asyncio.to_thread(
                            yf.download, chunk,
                            period=period, group_by='ticker', progress=False, threads=False
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

                all_info = await asyncio.to_thread(lambda: Ticker(chunk).summary_detail)

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

                        info = all_info.get(ticker)
                        if not isinstance(info, dict):
                            try:
                                logger.debug(f"Retrying metadata for {ticker}")
                                t = ticker
                                info = await asyncio.to_thread(lambda: Ticker(t).summary_detail.get(t, {}))
                            except Exception as e:
                                logger.warning(f"Failed to fetch metadata for {ticker}: {e}")
                                info = {}

                        if not isinstance(info, dict):
                            logger.debug(f"{ticker} info still not a dict")
                            continue

                        market_cap = info.get('marketCap', 0)

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
                        logger.debug(f"Error processing {ticker}: {e}")
                        continue

            except Exception as e:
                logger.error(f"Error downloading chunk: {e}")
                processed_count += len(chunk)
                yield {"status": "progress", "current": processed_count, "ticker": "Error in chunk"}
                continue

if __name__ == "__main__":
    async def _test():
        tickers = get_full_market_tickers()[:20]
        print(f"Testing with {len(tickers)} tickers...")
        async for res in screen_stocks(tickers):
            print(f"Found: {res}")
    asyncio.run(_test())
