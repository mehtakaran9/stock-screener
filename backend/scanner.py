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
                    df = data[ticker].dropna() if len(chunk) > 1 else data.dropna()
                    if df.empty or len(df) < 252: # Need at least a year of data
                        continue
                    
                    info = all_info.get(ticker, {})
                    if not isinstance(info, dict): continue
                    
                    market_cap = info.get('marketCap', 0)
                    price = df['Close'].iloc[-1]
                    prev_close = df['Close'].iloc[-2]
                    day_change = (price - prev_close) / prev_close
                    volume = df['Volume'].iloc[-1]
                    
                    # 1. Market Cap > 1B and Price > 5
                    if market_cap < 1_000_000_000 or price <= 5:
                        continue
                    
                    # 2. Day Change > 3%
                    if day_change <= 0.03:
                        continue
                        
                    # 3. Volume > 500K
                    if volume < 500_000:
                        continue

                    # Technical Indicators
                    # 4. Above 200 SMA
                    sma200 = df['Close'].rolling(window=200).mean()
                    if price <= sma200.iloc[-1]:
                        continue
                    
                    # 5. 1Y Resistance Breakout
                    # 1Y high (excluding today)
                    one_year_high = df['High'].iloc[-253:-1].max()
                    if price <= one_year_high:
                        continue
                        
                    # 6. Riding 8EMA
                    # Price above 8EMA and within 2% of it
                    ema8 = ta.ema(df['Close'], length=8)
                    curr_ema8 = ema8.iloc[-1]
                    if price < curr_ema8 or price > curr_ema8 * 1.02:
                        continue

                    result = {
                        "ticker": ticker,
                        "price": round(float(price), 2),
                        "change": round(float(day_change * 100), 2),
                        "volume": int(volume),
                        "market_cap": int(market_cap),
                        "ema8": round(float(curr_ema8), 2),
                        "sma200": round(float(sma200.iloc[-1]), 2),
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
