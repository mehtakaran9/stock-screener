# AGENTS.md - Stock Screener Project

## What
This project is a **Technical Stock Screener** that identifies high-probability trading setups based on momentum and trend-following criteria. It scans the US stock market (S&P 500 constituents by default) and filters for stocks that are breaking out while maintaining structural strength.

### Core Filters (10 total, applied in sequence):

Strategy: **oversold mean-reversion** — buy panic selloffs in large-cap stocks with intact macro uptrends.
Calibrated from 5-year S&P 500 reverse backtest (666K ticker-days) + second-layer filter sweep: **67% 3-month win rate, +7.7% avg return**.

- **Panic selloff**: Day Change < −5% (entry signal — the opposite of momentum)
- **Liquidity**: Market Cap > $1B, Price > $5, Daily Volume > 500K shares
- **Capitulation volume**: RVOL > 3.5× vs. 20-day average (panic selling, not routine)
- **Extreme oversold**: RSI(14) < 30 (capitulation-level reading)
- **Structural uptrend (SMA200)**: Price > 75% of SMA200 (not in freefall)
- **EMA Stack**: EMA20 > EMA50 > EMA200 (macro trend still aligned)
- **Deep SMA50 discount**: Price ≤ 90% of SMA50 (+6.1pp win rate; validated N=52, 5-yr backtest)
- **Sector exclusion**: Excludes Health Care, Communication Services, Utilities (+2.9pp; validated N=78)

## Why
- **FastAPI Backend**: Chosen for its high performance and native support for asynchronous streaming (SSE), allowing users to see results in real-time without waiting for a full market scan.
- **React + Vite Frontend**: Provides a modern, responsive developer experience.
- **Expandable table rows**: Each matched stock opens a detail panel with MA chips, Bollinger Band levels, and a swing trade levels table (3 entry/stop pairs with risk per share).
- **YFinance & Pandas-TA-Classic**: Reliable open-source libraries for fetching market data and calculating technical indicators in Python (patched for NumPy 2.0 compatibility).

## How
### Architecture:
1.  **Backend Scanning Engine (`backend/scanner.py`)**: 
    - Fetches S&P 500 constituents from a public CSV; falls back to a 10-ticker list if unavailable.
    - Uses `yfinance` to download 2 years of daily OHLCV data in chunks of 50 tickers.
    - Applies the 10 filters sequentially using `pandas` and `pandas-ta-classic`.
2.  **Streaming API (`backend/main.py`)**: 
    - Exposes `/api/scan` — a `StreamingResponse` (SSE) endpoint that pushes progress and result events to the frontend as stocks are identified. Results are cached for 10 minutes (`backend/scan_cache.json`). A module-level `asyncio.Lock` ensures only one full scan runs at a time.
    - Exposes `/api/filters` — returns the active filter list for display in the UI.
3.  **Frontend Dashboard (`frontend/src/App.tsx`)**:
    - Consumes the SSE stream and updates state reactively.
    - Displays results in a sortable, expandable table (`StockTable.tsx`) with MA chips, Bollinger Band levels, and swing trade levels in the expanded row.
    - Shows an amber warning banner if the S&P 500 CSV was unavailable and fallback tickers were used.
4.  **GitHub Actions scan + email (`backend/run_scan.py`, `backend/notifications.py`)**:
    - `run_scan.py` runs as a GitHub Actions cron job (every 15 min, 11 AM–4 PM ET, Mon–Fri). It checks whether today is an NYSE trading day, runs the full scan, and calls `send_scan_results_email`.
    - `notifications.py` builds a dark-themed HTML email (7 summary columns + expanded swing levels) and sends it via SMTP to all addresses in the `EMAIL_LIST` Actions variable.
    - Three workflows manage subscriptions: `daily-scan.yml` (scan + email), `add-subscriber.yml` (admin adds an address), `subscribe-request.yml` (fires when an admin approves a subscribe issue).

## How to Run
### 1. Backend
```bash
# From project root
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload
```
The backend will be available at `http://localhost:8000`.

### 2. Frontend
```bash
cd frontend
npm install
npm run dev
```
The frontend will be available at `http://localhost:5173`.

## Future Roadmap
- Implement custom ticker list upload.
- Add sound alerts when a new stock is found during a live scan.
- Support for multiple timeframes (Intraday, Weekly).
