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

@patch('backend.scanner.requests.get')
def test_get_full_market_tickers_success(mock_get):
    mock_response = MagicMock()
    mock_response.text = "Symbol\nAAPL\nMSFT"
    mock_response.raise_for_status = MagicMock()
    mock_get.return_value = mock_response
    tickers, is_full = get_full_market_tickers()
    assert tickers == ['AAPL', 'MSFT']
    assert is_full is True

@patch('backend.scanner.requests.get')
def test_get_full_market_tickers_fail(mock_get):
    mock_get.side_effect = Exception("Network error")
    tickers, is_full = get_full_market_tickers()
    # Should return the fallback list
    assert "AAPL" in tickers
    assert len(tickers) > 2
    assert is_full is False

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
@patch('backend.scanner._fetch_market_caps_bulk')
def test_screen_stocks_success(mock_caps, mock_download):
    df = generate_mock_data(change_pct=0.05)
    df.columns = pd.MultiIndex.from_product([['AAPL'], df.columns])
    mock_download.return_value = df
    mock_caps.return_value = {'AAPL': {'market_cap': 2_000_000_000.0, 'exchange': 'NASDAQ'}}

    results = [r for r in run_screen(['AAPL']) if isinstance(r, dict) and 'status' not in r]

    assert len(results) == 1
    assert results[0]['ticker'] == 'AAPL'
    assert results[0]['change'] > 3

@patch('yfinance.download')
@patch('backend.scanner._fetch_market_caps_bulk')
def test_screen_stocks_fails_market_cap(mock_caps, mock_download):
    df = generate_mock_data()
    df.columns = pd.MultiIndex.from_product([['AAPL'], df.columns])
    mock_download.return_value = df
    mock_caps.return_value = {'AAPL': {'market_cap': 500_000_000.0, 'exchange': 'NASDAQ'}}  # Fails 1B market cap

    results = [r for r in run_screen(['AAPL']) if isinstance(r, dict) and 'status' not in r]
    assert len(results) == 0

@patch('yfinance.download')
@patch('backend.scanner._fetch_market_caps_bulk')
def test_screen_stocks_fails_day_change(mock_caps, mock_download):
    df = generate_mock_data(change_pct=0.01)  # Fails 3% change
    df.columns = pd.MultiIndex.from_product([['AAPL'], df.columns])
    mock_download.return_value = df
    mock_caps.return_value = {'AAPL': {'market_cap': 2_000_000_000.0, 'exchange': 'NASDAQ'}}

    results = [r for r in run_screen(['AAPL']) if isinstance(r, dict) and 'status' not in r]
    assert len(results) == 0

@patch('yfinance.download')
@patch('backend.scanner._fetch_market_caps_bulk')
def test_screen_stocks_fails_volume(mock_caps, mock_download):
    df = generate_mock_data(volume=100000)  # Fails 500K volume
    df.columns = pd.MultiIndex.from_product([['AAPL'], df.columns])
    mock_download.return_value = df
    mock_caps.return_value = {'AAPL': {'market_cap': 2_000_000_000.0, 'exchange': 'NASDAQ'}}

    results = [r for r in run_screen(['AAPL']) if isinstance(r, dict) and 'status' not in r]
    assert len(results) == 0
