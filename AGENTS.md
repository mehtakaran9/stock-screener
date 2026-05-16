# AGENTS.md - Stock Screener Project

## What
This project is a **Technical Stock Screener** that identifies high-probability trading setups based on momentum and trend-following criteria. It scans the US stock market (S&P 500 constituents by default) and filters for stocks that are breaking out while maintaining structural strength.

### Core Filters:
- **Momentum**: Day Change > 3%
- **Liquidity**: Market Cap > $1B, Price > $5, Daily Volume > 500K shares
- **Trend**: Above 200-day Simple Moving Average (SMA)
- **Mean Reversion/Support**: Riding (within 2% of) the 8-day Exponential Moving Average (EMA)
- **Breakout**: Current price above 1-year resistance (12-month high)

## Why
- **FastAPI Backend**: Chosen for its high performance and native support for asynchronous streaming (SSE), allowing users to see results in real-time without waiting for a full market scan.
- **React + Vite Frontend**: Provides a modern, responsive developer experience.
- **Lightweight Charts (TradingView)**: The gold standard for financial visualizations, used here to overlay critical indicators (8EMA, 200SMA) on price action.
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
    - Displays results in a sortable table.
    - Fetches detailed history for the selected stock and renders an interactive chart.

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
- Add volume breakout relative to average (e.g., Volume > 2x 20-day Avg).
- Implement custom ticker list upload.
- Add sound alerts when a new stock is found during a live scan.
- Support for multiple timeframes (Intraday, Weekly).
