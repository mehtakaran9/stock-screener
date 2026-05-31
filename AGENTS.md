# AGENTS.md - Stock Screener Project

## What
This project is a **Technical Stock Screener** for the S&P 500 universe with **one unified screener** (surfaced at `/api/scan-v2`): it buys panic selloffs into extreme dislocation and badges the rare, alt-data-confirmed names as ⭐ **HIGH CONVICTION**. Two earlier strategies are **deprecated/dormant** (code + endpoints kept, not surfaced in the UI or email): the Recovery Scan (v1, `/api/scan`, healthy-uptrend dips) and the standalone Conviction Scan (v3, `/api/scan-v3`). v3 is now folded into the unified scan as the HIGH CONVICTION badge (v3 ⊆ v2 — every v3 pick is already a v2 pick). All scans share identical SSE streaming, caching, and result shapes.

### Recovery Scan — Core Filters (10 total, `/api/scan`) — ⚠️ DEPRECATED / dormant (not surfaced)

Strategy: **oversold mean-reversion** — buy panic selloffs in large-cap stocks with intact macro uptrends.
Calibrated from 5-year S&P 500 reverse backtest (666K ticker-days) + second-layer filter sweep: **70.5% 3-month win rate, +8.6% avg return** (full config, N=44). ⚠️ These figures carry **survivorship bias** — the backtest uses the *current* S&P 500 list applied to historical data (delisted/removed names excluded), so treat them as upper bounds. RVOL is measured against the prior 20 completed days (excludes the signal day).

- **Panic selloff**: Day Change < −5% (entry signal — the opposite of momentum)
- **Liquidity**: Market Cap > $1B, Price > $5, Daily Volume > 500K shares
- **Capitulation volume**: RVOL > 3.5× vs. 20-day average (panic selling, not routine)
- **Extreme oversold**: RSI(14) < 30 (capitulation-level reading)
- **Structural uptrend (SMA200)**: Price > 75% of SMA200 (not in freefall)
- **EMA Stack**: EMA20 > EMA50 > EMA200 (macro trend still aligned)
- **Deep SMA50 discount**: Price ≤ 90% of SMA50 (+3.8pp win rate; validated N=59, 5-yr backtest)
- **Sector exclusion**: Excludes Health Care, Communication Services, Utilities (+2.7pp; validated N=87)

### Big Move Scanner — Core Filters (8 total, `/api/scan-v2`) — ✅ PRIMARY (the unified scan)

Strategy: **extreme dislocation recovery** — stocks already deeply below SMA200 that have an additional panic selloff day.
Calibrated from 10-year S&P 500 backtest (`backend/bigmove_research.py`, 1.27M ticker-days): **33.32× lift, 14.56% precision for 30%+ moves in 42 trading days**. ⚠️ Same **survivorship-bias** caveat as the Recovery Scan — current-constituents-only universe inflates lift/precision; treat as upper bounds.

- **Panic selloff**: Day Change < −5%
- **Liquidity**: Market Cap > $1B, Price > $5, Volume > 500K
- **Volume confirmation**: RVOL > 1.5×
- **Oversold**: RSI(14) < 35
- **Extreme dislocation (SMA200)**: Price **< 70% of SMA200** — inverted from Recovery Scan's > 75%
- **Below SMA50**: Price < 90% of SMA50

### ⭐ HIGH CONVICTION badge — Core Filters (folded into the unified scan; standalone `/api/scan-v3` dormant)

Strategy: **multi-factor extreme dislocation + alt-data confirmation** — the v2 dislocation regime gated far tighter, then required to carry real-money confirmation. Very selective — empirically only a few signals per year, not per week: the tighter technical gates fired ~20× in 5½ yrs of S&P 500 backtest data (≈3–4/yr; the alt-data gate filters further), and ~60% of those went on to gain ≥ 20% within 42 trading days. Research source: `backend/conviction_research.py`.

- **Panic selloff**: Day Change < −5%
- **Liquidity**: Market Cap > $1B, Price > $5, Volume > 500K
- **Capitulation volume**: RVOL > 3.5× (tighter than v2's 1.5×)
- **True capitulation**: RSI(14) < 25 (tighter than v2's 35)
- **Extreme dislocation (SMA200)**: Price < 70% of SMA200 (same gate as Big Move)
- **Below SMA50**: Price < 90% of SMA50
- **Extreme panic day**: candle body ≥ 1.5× ATR14
- **Alt-data confirmation (≥ 1 required)**: insider buy in last 30d (SEC Form 4) · earnings beat streak ≥ 2 · options call anomaly (put/call < 0.5, requires `POLYGON_API_KEY`). Skips names reporting earnings within 7 days. Adds `insider_buys_30d`, `earnings_beat_streak`, `options_call_anomaly`, `conviction_score` (0–3) to the result.

The (deprecated) Recovery gate (price > 75% of SMA200) and the unified Big Move gate (price < 70% of SMA200) are mutually exclusive. The HIGH CONVICTION badge is a **strict subset of the unified scan** (v3 ⊆ v2): a row earns the badge when it additionally clears the tighter thresholds (RVOL > 3.5×, RSI < 25, candle ≥ 1.5× ATR) **and** scores ≥ 1 on alt-data. The unified scan therefore runs the v2 filter, then annotates qualifying rows in-line — no separate scan. (A row that clears the tighter gates but reports earnings within 7 days stays an un-badged pick rather than being dropped, which is how the standalone v3 differed.)

## Why
- **FastAPI Backend**: Chosen for its high performance and native support for asynchronous streaming (SSE), allowing users to see results in real-time without waiting for a full market scan.
- **React + Vite Frontend**: Provides a modern, responsive developer experience.
- **Expandable table rows**: Each matched stock opens a detail panel with MA chips, Bollinger Band levels, and a swing trade levels table (3 entry/stop pairs with risk per share).
- **YFinance & Pandas-TA-Classic**: Reliable open-source libraries for fetching market data and calculating technical indicators in Python (patched for NumPy 2.0 compatibility).

## How
### Architecture:
1.  **Backend Scanning Engines (`backend/scanner.py`, `backend/scanner_v2.py`, `backend/scanner_v3.py`)**:
    - `scanner.py` (Recovery Scan): Fetches S&P 500 constituents from a public CSV (10-ticker fallback), downloads 2 years of OHLCV data in chunks of 50 via `yfinance`, applies the 10-filter recovery pipeline.
    - `scanner_v2.py` (Big Move Scan): Identical async generator / semaphore / queue structure. Applies the 8-filter extreme dislocation pipeline from `CONFIG_V2`. Research source: `backend/bigmove_research.py` (10-year backtest CLI — run with `python3 -m backend.bigmove_research`).
    - `scanner_v3.py` (Conviction Scan): Same async structure plus a per-ticker alt-data gate. Applies the tighter `CONFIG_V3` technical pipeline, then fetches alt-data via `backend/alt_data.py` only for tickers that clear all technical gates, and surfaces only those with `conviction_score ≥ 1`. Research source: `backend/conviction_research.py`.
2.  **Streaming API (`backend/main.py`)**:
    - Exposes `/api/scan` — SSE endpoint for recovery screener. Results cached to `backend/scan_cache.json` (10 min TTL). A module-level `asyncio.Lock` prevents concurrent full scans.
    - Exposes `/api/filters` — returns the 10 active recovery filter descriptions.
    - Exposes `/api/scan-v2` — identical SSE pattern using `screen_stocks_v2()`. Results cached to `backend/scan_cache_v2.json` (10 min TTL, separate lock).
    - Exposes `/api/filters-v2` — returns the 8 active big-move filter descriptions.
    - Exposes `/api/scan-v3` — identical SSE pattern using `screen_stocks_v3()`. Results cached to `backend/scan_cache_v3.json` (10 min TTL, separate lock).
    - Exposes `/api/filters-v3` — returns the 10 active conviction filter descriptions.
3.  **Frontend Dashboard (`frontend/src/App.tsx`)**:
    - A single unified view scans `/api/scan-v2` (filters from `/api/filters-v2`); `startScan()` opens one EventSource. (The earlier 3-tab `switchMode()` toggle was removed when v1 + standalone v3 were deprecated.)
    - Consumes the SSE stream and updates state reactively.
    - Displays results in a sortable, expandable table (`StockTable.tsx`) with MA chips, Bollinger Band levels, and swing trade levels in the expanded row. The unified scan returns a 31-field JSON shape — the 27-field base plus 4 conviction fields (`insider_buys_30d`, `earnings_beat_streak`, `options_call_anomaly`, `conviction_score`) shown in a Conviction Signals panel; rows with `conviction_score ≥ 1` render a ⭐ HIGH CONVICTION badge.
    - Shows an amber warning banner if the S&P 500 CSV was unavailable and fallback tickers were used.
4.  **GitHub Actions scan + email (`backend/run_scan.py`, `backend/notifications.py`)**:
    - `run_scan.py` runs as a GitHub Actions cron job (once per weekday around 12 PM ET — it fires at both 16:00 & 17:00 UTC and only emails during the noon ET hour, so DST never shifts the digest). It checks whether today is an NYSE trading day, runs the full scan, and calls `send_scan_results_email`.
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
