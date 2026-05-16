import asyncio
import pytest
import pandas as pd
import numpy as np
import pandas_ta_classic as ta
from backend.scanner import screen_stocks, get_full_market_tickers
from unittest.mock import patch, MagicMock

def run_screen(tickers):
    """Collect all items from the async screen_stocks generator synchronously."""
    async def _collect():
        return [item async for item in screen_stocks(tickers)]
    return asyncio.run(_collect())

@patch('pandas.read_csv')
def test_get_full_market_tickers_success(mock_read_csv):
    mock_df = pd.DataFrame({'Symbol': ['AAPL', 'MSFT']})
    mock_read_csv.return_value = mock_df
    tickers = get_full_market_tickers()
    assert tickers == ['AAPL', 'MSFT']

@patch('pandas.read_csv')
def test_get_full_market_tickers_fail(mock_read_csv):
    mock_read_csv.side_effect = Exception("Network error")
    tickers = get_full_market_tickers()
    # Should return the default list
    assert "AAPL" in tickers
    assert len(tickers) > 2

def generate_mock_data(ticker="AAPL", price_start=100, price_end=150, volume=600000, days=300, change_pct=0.05, should_pass_ema=True):
    dates = pd.date_range(end='2024-01-01', periods=days)
    close_prices = np.linspace(price_start, price_end, days)
    
    # Calculate EMA8 for the second to last day
    ema8_prev = ta.ema(pd.Series(close_prices[:-1]), length=8).iloc[-1]
    alpha = 2/9
    
    if should_pass_ema:
        # Price_t = 1.01 * EMA_t
        price_t = (1.01 * ema8_prev * (1 - alpha)) / (1 - 1.01 * alpha)
    else:
        # Force it to fail EMA8 by being too far away (e.g., 5% away)
        price_t = (1.05 * ema8_prev * (1 - alpha)) / (1 - 1.05 * alpha)
        
    close_prices[-1] = price_t
    
    # Adjust change_pct if needed
    current_change = (close_prices[-1] - close_prices[-2]) / close_prices[-2]
    if change_pct > 0.03 and current_change <= 0.03:
        close_prices[-2] = close_prices[-1] / (1 + change_pct)
    elif change_pct <= 0.03 and current_change > 0.03:
        close_prices[-2] = close_prices[-1] / (1 + change_pct)
    
    high_prices = close_prices * 1.005
    # Ensure 1Y high (252 days) is lower than current price for breakout test
    high_prices[:-1] = np.minimum(high_prices[:-1], close_prices[-1] * 0.95)
    
    df = pd.DataFrame({
        'Open': close_prices * 0.99,
        'High': high_prices,
        'Low': close_prices * 0.98,
        'Close': close_prices,
        'Volume': volume
    }, index=dates)
    
    return df

@patch('yfinance.download')
@patch('backend.scanner.Ticker')
def test_screen_stocks_success(mock_ticker, mock_download):
    # Setup mock data that passes all filters
    df = generate_mock_data(change_pct=0.05) # Passes 3% change
    
    # Mock yfinance download
    # MultiIndex columns as returned by yf.download(group_by='ticker')
    df.columns = pd.MultiIndex.from_product([['AAPL'], df.columns])
    mock_download.return_value = df
    
    # Mock yahooquery Ticker
    mock_ticker_instance = MagicMock()
    mock_ticker_instance.summary_detail = {
        'AAPL': {'marketCap': 2000000000} # Passes 1B market cap
    }
    mock_ticker.return_value = mock_ticker_instance
    
    results = [r for r in run_screen(['AAPL']) if isinstance(r, dict) and 'status' not in r]
    
    assert len(results) == 1
    assert results[0]['ticker'] == 'AAPL'
    assert results[0]['change'] > 3

@patch('yfinance.download')
@patch('backend.scanner.Ticker')
def test_screen_stocks_fails_market_cap(mock_ticker, mock_download):
    df = generate_mock_data()
    df.columns = pd.MultiIndex.from_product([['AAPL'], df.columns])
    mock_download.return_value = df
    
    mock_ticker_instance = MagicMock()
    mock_ticker_instance.summary_detail = {
        'AAPL': {'marketCap': 500000000} # Fails 1B market cap
    }
    mock_ticker.return_value = mock_ticker_instance
    
    results = [r for r in run_screen(['AAPL']) if isinstance(r, dict) and 'status' not in r]
    assert len(results) == 0

@patch('yfinance.download')
@patch('backend.scanner.Ticker')
def test_screen_stocks_fails_day_change(mock_ticker, mock_download):
    df = generate_mock_data(change_pct=0.01) # Fails 3% change
    df.columns = pd.MultiIndex.from_product([['AAPL'], df.columns])
    mock_download.return_value = df
    
    mock_ticker_instance = MagicMock()
    mock_ticker_instance.summary_detail = {
        'AAPL': {'marketCap': 2000000000}
    }
    mock_ticker.return_value = mock_ticker_instance
    
    results = [r for r in run_screen(['AAPL']) if isinstance(r, dict) and 'status' not in r]
    assert len(results) == 0

@patch('yfinance.download')
@patch('backend.scanner.Ticker')
def test_screen_stocks_fails_volume(mock_ticker, mock_download):
    df = generate_mock_data(volume=100000) # Fails 500K volume
    df.columns = pd.MultiIndex.from_product([['AAPL'], df.columns])
    mock_download.return_value = df
    
    mock_ticker_instance = MagicMock()
    mock_ticker_instance.summary_detail = {
        'AAPL': {'marketCap': 2000000000}
    }
    mock_ticker.return_value = mock_ticker_instance
    
    results = [r for r in run_screen(['AAPL']) if isinstance(r, dict) and 'status' not in r]
    assert len(results) == 0
