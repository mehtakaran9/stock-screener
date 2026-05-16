import pytest
from fastapi.testclient import TestClient
from backend.main import app
from unittest.mock import patch, MagicMock
import pandas as pd
import json

client = TestClient(app)

def test_get_history_error():
    with patch('yfinance.download') as mock_download:
        mock_download.return_value = pd.DataFrame()
        response = client.get("/api/history/INVALID")
        assert response.status_code == 200
        assert response.json() == {"error": "No data found"}

@patch('yfinance.download')
def test_get_history_adbe_success(mock_download):
    # Provide at least 200 rows for SMA200
    dates = pd.date_range(end='2024-01-01', periods=250)
    df = pd.DataFrame({
        'Open': [100.0]*250,
        'High': [105.0]*250,
        'Low': [95.0]*250,
        'Close': [102.0]*250,
        'Volume': [1000]*250
    }, index=dates)
    mock_download.return_value = df

    response = client.get("/api/history/ADBE")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 250
    assert "time" in data[0]
    assert "close" in data[0]

@patch('backend.main.get_full_market_tickers')
@patch('backend.main.screen_stocks')
def test_scan_market(mock_screen, mock_get_tickers):
    mock_get_tickers.return_value = ['AAPL']
    mock_screen.return_value = iter([{
        "ticker": "AAPL",
        "price": 150.0,
        "change": 5.0,
        "volume": 1000000,
        "market_cap": 2000000000,
        "ema8": 148.0,
        "sma200": 130.0,
        "high1y": 145.0
    }])

    with client.stream("GET", "/api/scan") as response:
        assert response.status_code == 200
        # iter_lines() returns strings in recent httpx/starlette versions, 
        # but let's be safe and decode if bytes
        data_lines = []
        for line in response.iter_lines():
            if isinstance(line, bytes):
                line = line.decode('utf-8')
            if line.startswith("data:"):
                data_lines.append(line)

        assert len(data_lines) >= 3

        events = [json.loads(line[6:]) for line in data_lines]

        assert events[0]['status'] == 'progress'
        assert events[1]['status'] == 'result'
        assert events[1]['data']['ticker'] == 'AAPL'
        assert events[-1]['status'] == 'complete'
