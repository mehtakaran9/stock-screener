from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import json
import asyncio
from backend.scanner import get_full_market_tickers, screen_stocks
import yfinance as yf
import pandas_ta_classic as ta
import pandas as pd

app = FastAPI()

# Enable CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/scan")
async def scan_market():
    async def event_generator():
        tickers = get_full_market_tickers()
        # For prototype, we might want to limit this or allow user to specify
        # For now, let's scan a decent chunk
        target_tickers = tickers[:500] 
        
        yield f"data: {json.dumps({'status': 'progress', 'total': len(target_tickers), 'current': 0})}\n\n"
        
        count = 0
        for stock in screen_stocks(target_tickers):
            count += 1
            yield f"data: {json.dumps({'status': 'result', 'data': stock})}\n\n"
            # Optional: yield progress every X stocks if no result found
            await asyncio.sleep(0.01) # Yield control
            
        yield f"data: {json.dumps({'status': 'complete'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/api/history/{ticker}")
async def get_history(ticker: str):
    """
    Returns historical data for charting.
    Includes 8EMA and 200SMA.
    """
    df = yf.download(ticker, period="2y", progress=False)
    if df.empty:
        return {"error": "No data found"}
    
    # Calculate indicators
    df['EMA8'] = ta.ema(df['Close'], length=8)
    df['SMA200'] = ta.sma(df['Close'], length=200)
    
    df = df.reset_index()
    # Convert to format suitable for lightweight-charts (timestamp in seconds)
    history = []
    for _, row in df.iterrows():
        # yfinance reset_index usually names the date column 'Date' or 'index' depending on version
        date_col = 'Date' if 'Date' in df.columns else 'index'
        history.append({
            "time": int(row[date_col].timestamp()),
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
