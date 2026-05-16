import pandas as pd
import pandas_ta_classic as ta
import yfinance as yf
from yahooquery import Ticker
import logging
from typing import List, Dict, Any, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_full_market_tickers() -> List[str]:
    """
    Fetches a list of active US tickers. 
    """
    try:
        # Try fetching from a reliable CSV or just use a larger static list for the prototype
        # S&P 500 from a common github gist or similar if wikipedia fails
        url = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv"
        df = pd.read_csv(url)
        tickers = df['Symbol'].tolist()
        return [t.replace('.', '-') for t in tickers]
    except Exception as e:
        logger.error(f"Error fetching tickers: {e}")
        return ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "NVDA", "AMD", "NFLX", "PYPL"]

def screen_stocks(tickers: List[str]):
    """
    Screens a list of tickers based on the defined filters.
    Yields results as they are found for streaming.
    """
    period = "2y" 
    
    logger.info(f"Downloading data for {len(tickers)} tickers...")
    
    chunk_size = 50
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i:i + chunk_size]
        try:
            # Removed 'silent=True' as it might not be supported in some versions
            data = yf.download(chunk, period=period, group_by='ticker', progress=False)
            
            # Fetching Market Cap and other info using yahooquery for speed
            t_query = Ticker(chunk)
            all_info = t_query.summary_detail
            
            for ticker in chunk:
                try:
                    # Robust extraction for multi-index vs single index
                    if isinstance(data.columns, pd.MultiIndex):
                        if ticker not in data.columns.levels[0]: continue
                        df = data[ticker].dropna()
                    else:
                        df = data.dropna()
                    
                    if len(df) < 200: # Need at least 200 days for SMA200
                        continue
                    
                    info = all_info.get(ticker, {})
                    if not isinstance(info, dict): continue
                    
                    market_cap = info.get('marketCap', 0)
                    price = float(df['Close'].iloc[-1])
                    prev_close = float(df['Close'].iloc[-2])
                    day_change = (price - prev_close) / prev_close
                    volume = int(df['Volume'].iloc[-1])
                    
                    # 1. Market Cap > 1B and Price > 5
                    if market_cap < 1_000_000_000 or price <= 5:
                        logger.debug(f"{ticker} failed MC/Price: {market_cap}, {price}")
                        continue
                    
                    # 2. Day Change > 3%
                    if day_change <= 0.03:
                        logger.debug(f"{ticker} failed Change: {day_change}")
                        continue
                        
                    # 3. Volume > 500K
                    if volume < 500_000:
                        logger.debug(f"{ticker} failed Volume: {volume}")
                        continue

                    # Technical Indicators
                    # 4. Above 200 SMA
                    sma200_series = df['Close'].rolling(window=200).mean()
                    curr_sma200 = float(sma200_series.iloc[-1])
                    if price <= curr_sma200:
                        logger.debug(f"{ticker} failed SMA200: {price} <= {curr_sma200}")
                        continue
                    
                    # 5. 1Y Resistance Breakout
                    one_year_high = float(df['High'].iloc[:-1].tail(252).max())
                    if price <= one_year_high:
                        logger.debug(f"{ticker} failed 1Y High: {price} <= {one_year_high}")
                        continue
                        
                    # 6. Riding 8EMA
                    ema8_series = ta.ema(df['Close'], length=8)
                    if ema8_series is None or ema8_series.empty: continue
                    curr_ema8 = float(ema8_series.iloc[-1])
                    
                    if price < curr_ema8 or price > curr_ema8 * 1.02:
                        logger.debug(f"{ticker} failed EMA8: {price}, EMA8: {curr_ema8}")
                        continue

                    result = {
                        "ticker": ticker,
                        "price": round(float(price), 2),
                        "change": round(float(day_change * 100), 2),
                        "volume": int(volume),
                        "market_cap": int(market_cap),
                        "ema8": round(float(curr_ema8), 2),
                        "sma200": round(float(curr_sma200), 2),
                        "high1y": round(float(one_year_high), 2)
                    }
                    yield result
                    
                except Exception as e:
                    logger.debug(f"Error processing {ticker}: {e}")
                    continue
        except Exception as e:
            logger.error(f"Error downloading chunk: {e}")
            continue

if __name__ == "__main__":
    # Test run
    tickers = get_full_market_tickers()[:20]
    print(f"Testing with {len(tickers)} tickers...")
    for res in screen_stocks(tickers):
        print(f"Found: {res}")
