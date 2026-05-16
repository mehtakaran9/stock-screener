# AGENTS.md - Stock Screener Project

## What
This project is a **Technical Stock Screener** that identifies high-probability trading setups based on momentum and trend-following criteria. It scans the US stock market (S&P 500 constituents by default) and filters for stocks that are breaking out while maintaining structural strength.

### Core Filters (11 total, applied in sequence):
- **Momentum**: Day Change > 3%
- **Liquidity**: Market Cap > $1B, Price > $5, Daily Volume > 500K shares
- **Trend (SMA200)**: Price > 75% of SMA200
- **Near-term support (EMA8)**: Price > 80% of EMA8
- **RSI(14)**: 50 – 70 (momentum confirmed, not overbought)
- **MACD histogram > 0**: bullish crossover active (MACD line above signal)
- **Price > EMA50**: medium-term uptrend intact
- **Price > EMA200**: long-term uptrend intact
- **Price < BB upper (20, 2)**: not overextended above the upper Bollinger Band

## Why
- **FastAPI Backend**: Chosen for its high performance and native support for asynchronous streaming (SSE), allowing users to see results in real-time without waiting for a full market scan.
- **React + Vite Frontend**: Provides a modern, responsive developer experience.
- **Expandable table rows**: Each matched stock opens a detail panel with MA chips, Bollinger Band levels, and a swing trade levels table (3 entry/stop pairs with risk per share).
- **YFinance & Pandas-TA-Classic**: Reliable open-source libraries for fetching market data and calculating technical indicators in Python (patched for NumPy 2.0 compatibility).

## How
### Architecture:
1.  **Backend Scanning Engine (`backend/scanner.py`)**: 
    - Fetches S&P 500 constituents.
    - Uses `yfinance` to download 2 years of daily OHLCV data in batches.
    - Applies vectorized technical filters using `pandas` and `pandas-ta-classic`.
2.  **Streaming API (`backend/main.py`)**: 
    - Exposes a `/api/scan` endpoint that uses `StreamingResponse` to push JSON objects to the frontend as stocks are identified.
3.  **Frontend Dashboard (`frontend/src/App.tsx`)**:
    - Consumes the SSE stream and updates the local state Reactively.
    - Displays results in a sortable, expandable table with MA chips and swing trade levels in the expanded row.

## How to Run
### 1. Backend
```bash
# From project root
source venv/bin/activate
uvicorn backend.main:app --reload
```
The backend will be available at `http://localhost:8000`.

### 2. Frontend
```bash
cd frontend
npm run dev
```
The frontend will be available at `http://localhost:5173`.

## Future Roadmap
- Implement custom ticker list upload.
- Add sound alerts when a new stock is found during a live scan.
- Support for multiple timeframes (Intraday, Weekly).
