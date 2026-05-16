from fastapi import FastAPI, HTTPException, Path
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import json
import asyncio
import logging
import warnings
from rich.logging import RichHandler

# Suppress noisy multiprocessing warnings at shutdown
warnings.filterwarnings("ignore", category=UserWarning, module="multiprocessing")
from backend.scanner import get_full_market_tickers, screen_stocks, get_active_filters
import yfinance as yf
import pandas_ta_classic as ta
import pandas as pd

# Configure logging with Rich
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True)]
)

# Suppress noisy logs from dependencies (already in scanner.py, but good to have here too)
logging.getLogger("yfinance").setLevel(logging.WARNING)
logging.getLogger("peewee").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

logger = logging.getLogger("main")

app = FastAPI()

# Enable CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"message": "Stock Screener API is running. Visit /docs for API documentation."}

@app.get("/api/filters")
async def get_filters():
    return {"filters": get_active_filters()}

@app.get("/api/scan")
async def scan_market():
    async def event_generator():
        tickers = await asyncio.to_thread(get_full_market_tickers)
        target_tickers = tickers[:500]

        yield f"data: {json.dumps({'status': 'progress', 'total': len(target_tickers), 'current': 0})}\n\n"

        async for update in screen_stocks(target_tickers):
            if isinstance(update, dict):
                if update.get('status') == 'progress':
                    yield f"data: {json.dumps({'status': 'progress', 'total': len(target_tickers), 'current': update['current'], 'ticker': update.get('ticker')})}\n\n"
                else:
                    yield f"data: {json.dumps({'status': 'result', 'data': update})}\n\n"

        yield f"data: {json.dumps({'status': 'complete'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/api/history/{ticker}")
async def get_history(ticker: str = Path(..., pattern=r"^[A-Z0-9.\-]{1,10}$")):
    """
    Returns historical OHLCV data with 8EMA and 200SMA for charting.
    """
    df = await asyncio.to_thread(yf.download, ticker, period="2y", progress=False)
    if df is None or df.empty:
        raise HTTPException(status_code=404, detail=f"No data found for ticker '{ticker}'")

    if len(df) < 200:
        raise HTTPException(status_code=422, detail=f"Insufficient data for '{ticker}' (need 200 days for SMA200)")

    df['EMA8'] = ta.ema(df['Close'], length=8)
    df['SMA200'] = ta.sma(df['Close'], length=200)

    df = df.reset_index()
    # First column after reset_index is always the date column (index.name from yfinance is 'Date')
    date_col = df.columns[0]

    history = []
    for _, row in df.iterrows():
        ts = row[date_col]
        if hasattr(ts, 'timestamp'):
            timestamp = int(ts.timestamp())
        else:
            try:
                timestamp = int(pd.Timestamp(ts).timestamp())
            except Exception:
                continue

        history.append({
            "time": timestamp,
            "open": float(row['Open']),
            "high": float(row['High']),
            "low": float(row['Low']),
            "close": float(row['Close']),
            "ema8": float(row['EMA8']) if not pd.isna(row['EMA8']) else None,
            "sma200": float(row['SMA200']) if not pd.isna(row['SMA200']) else None,
        })

    return history

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
