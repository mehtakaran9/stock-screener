# StockScreener Pro

A real-time technical stock screener that identifies momentum breakout setups across the S&P 500 universe. Scan results stream live to the browser via Server-Sent Events and are emailed daily through a GitHub Actions cron job — no paid infrastructure required.

---

## What it does

On each scan, the screener downloads 2 years of daily OHLCV data for up to 500 S&P 500 tickers and applies six filters in sequence:

| Filter | Threshold | Rationale |
|--------|-----------|-----------|
| Day change | > 3% | Momentum — significant single-day move |
| Market cap | > $1 B | Liquidity — eliminates micro/nano-caps |
| Price | > $5 | Avoids penny stocks |
| Volume | > 500 K shares | Confirms institutional participation |
| SMA 200 | Price > 75% of SMA200 | Stock is in a long-term uptrend |
| EMA 8 | Price > 80% of EMA8 | Stock is near near-term momentum, not extended |
| RSI (14) | 50 – 70 | Momentum confirmed without being overbought |
| MACD histogram | > 0 | Bullish crossover active (MACD line above signal) |
| EMA 50 | Price > EMA50 | Medium-term uptrend intact |
| EMA 200 | Price > EMA200 | Long-term uptrend intact |
| Bollinger Band | Price < BB upper (20, 2) | Not overextended above the upper band |

Tickers that pass all eleven filters are surfaced in the UI as potential swing trade candidates and emailed at 12 PM ET on NYSE trading days.

Each result also includes computed swing trade levels:
- **Entry levels**: ① breakout now (current price), ② EMA8 pullback, ③ BB midline (SMA20) deep dip
- **Stop levels**: ① 1×ATR below entry, ② 0.5×ATR below EMA8, ③ 0.5×ATR below SMA50 (trend break)

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  GitHub Actions (free)                  │
│  Cron: 12 PM ET Mon–Fri  ·  workflow_dispatch (manual) │
│  backend/run_scan.py → scanner.py → notifications.py   │
└───────────────────┬─────────────────────────────────────┘
                    │ email via SMTP
                    ▼
              Gmail / any SMTP

┌──────────────────┐        ┌──────────────────────────┐
│  Vercel (free)   │◄──SSE──│   Render.com (free)      │
│  React + Vite    │        │   FastAPI + Uvicorn       │
│  frontend/       │        │   backend/main.py         │
└──────────────────┘        └──────────────────────────┘
```

- **GitHub Actions** owns the scheduled scan and email. Two cron entries handle EDT/EST daylight-saving transitions; `is_nyse_trading_day()` guards against duplicate fires on transition weeks.
- **Render.com** hosts the FastAPI backend on the free tier (512 MB RAM; sleeps after 15 min inactivity). APScheduler is disabled on Render via `DISABLE_SCHEDULER=true`; it remains active for local development.
- **Vercel** hosts the static React build. `VITE_API_URL` points to the Render service.
- **Tickers** are fetched from the [S&P 500 constituents CSV](https://github.com/datasets/s-and-p-500-companies) at scan time; a 10-ticker fallback list is used if the fetch fails.

---

## Project structure

```
stock-screener/
├── backend/
│   ├── main.py            # FastAPI app — /api/scan (SSE), /api/filters, /api/history/{ticker}
│   ├── scanner.py         # Core screening engine (yfinance + pandas-ta-classic)
│   ├── scheduler.py       # APScheduler wrapper (local dev only)
│   ├── run_scan.py        # Standalone entrypoint for GitHub Actions
│   ├── notifications.py   # Dark-themed HTML email builder + SMTP sender
│   ├── requirements.txt
│   ├── test_scanner.py    # pytest unit tests for scanner
│   └── test_main.py       # pytest unit tests for API
├── frontend/
│   ├── src/
│   │   ├── App.tsx             # Root component — SSE consumer, scan controls, progress bar
│   │   ├── App.css             # Dark-theme design tokens + layout
│   │   └── components/
│   │       └── StockTable.tsx  # Results table with Google Finance ticker links
│   ├── index.html
│   ├── package.json
│   └── vite.config.ts
├── .github/
│   └── workflows/
│       └── daily-scan.yml  # Cron + manual dispatch workflow
├── render.yaml             # Render.com deploy blueprint
└── AGENTS.md               # AI agent onboarding notes
```

---

## Local development

### Prerequisites
- Python 3.10+
- Node.js 18+

### Backend

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r backend/requirements.txt

uvicorn backend.main:app --reload
# API available at http://localhost:8000
```

The APScheduler fires at 12 PM ET on trading days in local mode. To suppress it:

```bash
DISABLE_SCHEDULER=true uvicorn backend.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
# UI available at http://localhost:5173
```

The frontend talks to `http://localhost:8000` by default (`VITE_API_URL` unset).

### Running tests

```bash
python3 -m pytest backend/ -v
```

---

## Cloud deployment

### GitHub Actions — daily scan + email

#### Required secrets

Go to **Settings → Secrets and variables → Actions → Repository secrets** and add:

| Secret | Example |
|--------|---------|
| `EMAIL_SMTP_HOST` | `smtp.gmail.com` |
| `EMAIL_SMTP_PORT` | `587` |
| `EMAIL_USER` | `you@gmail.com` |
| `EMAIL_PASSWORD` | 16-char Gmail App Password ¹ |
| `EMAIL_TO` | `recipient@example.com` |

¹ Gmail requires an **App Password** (not your account password) when 2-Step Verification is enabled.  
Create one at **myaccount.google.com → Security → App Passwords**.

#### Manual workflow dispatch options

Trigger a run from **Actions → Daily Market Scan → Run workflow** and choose:

| Input | `true` | `false` |
|-------|--------|---------|
| `full_scan` | Run the full scan, skip trading-day check | Skip scan |
| `send_email` | Send results email | Suppress email |

**Testing SMTP credentials:** Set `full_scan=false, send_email=true`. This skips the scan entirely and sends a styled test email with sample data in seconds — useful for verifying SMTP config without waiting for a full scan.

---

### Render.com — FastAPI backend

1. Connect your GitHub repo in the Render dashboard (or use the included `render.yaml` blueprint).
2. Set these environment variables in the Render dashboard:

| Variable | Value |
|----------|-------|
| `ALLOWED_ORIGINS` | `https://your-app.vercel.app` |
| `DISABLE_SCHEDULER` | `true` |
| `PYTHONPATH` | `/opt/render/project/src` |

---

### Vercel — React frontend

1. Import the repo in Vercel; set the **Root Directory** to `frontend/`.
2. Add one environment variable:

| Variable | Value |
|----------|-------|
| `VITE_API_URL` | `https://your-service.onrender.com` |

---

## Email format

Scan results are delivered as a dark-themed HTML email that mirrors the web UI:

- **Tickers** are hyperlinked to their Google Finance page (`google.com/finance/quote/AAPL:NASDAQ`)
- **Change %** is colour-coded: teal for gains, red for losses
- **Volume** and **Market Cap** use compact notation (`52.30M`, `3.29T`)
- Columns: Ticker · Price · Change % · Volume · Market Cap · EMA8 · SMA200
- When `full_scan=false, send_email=true` is triggered, an amber banner labels the data as a test

---

## Key dependencies

| Package | Purpose |
|---------|---------|
| `fastapi` + `uvicorn` | Async HTTP server with SSE streaming |
| `yfinance` | OHLCV data downloads + market cap via `fast_info` |
| `pandas-ta-classic` | EMA / SMA calculation (NumPy 2.0 compatible fork) |
| `pandas-market-calendars` | NYSE trading-day check |
| `apscheduler` | In-process scheduler (local dev only) |
| `rich` | Coloured terminal logging |
| `requests` | S&P 500 constituents CSV fetch |
| `pytz` | Timezone handling for ET cron logic |
