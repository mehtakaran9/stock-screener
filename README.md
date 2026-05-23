# StockScreener Pro

<p align="center">
  <a href="https://github.com/mehtakaran9/stock-screener/actions/workflows/daily-scan.yml">
    <img src="https://github.com/mehtakaran9/stock-screener/actions/workflows/daily-scan.yml/badge.svg?branch=main" alt="Daily Scan" />
  </a>
  <a href="https://github.com/mehtakaran9/stock-screener/actions/workflows/daily-scan.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/mehtakaran9/stock-screener/daily-scan.yml?branch=main&label=tests" alt="Tests" />
  </a>
  <a href="https://www.python.org/">
    <img src="https://img.shields.io/badge/python-3.11-3776ab.svg" alt="Python 3.11" />
  </a>
  <a href="https://react.dev/">
    <img src="https://img.shields.io/badge/react-19-61dafb.svg" alt="React 19" />
  </a>
  <a href="https://www.typescriptlang.org/">
    <img src="https://img.shields.io/badge/TypeScript-strict-3178c6.svg" alt="TypeScript strict" />
  </a>
</p>

A real-time technical stock screener that identifies oversold recovery setups across the S&P 500 universe. Scan results stream live to the browser via Server-Sent Events and are emailed daily through a GitHub Actions cron job — no paid infrastructure required.

**Strategy: oversold mean-reversion** — buy panic selloffs in large-cap stocks that are still in structural uptrends.

> **Backtest results** · 5-year S&P 500 · 666,534 ticker-days · 10-filter sweep
>
> | Metric | Value |
> |--------|-------|
> | 3-month win rate | **67%** |
> | Average 3-month return | **+7.7%** |
> | Signals per year | ~26 |
> | Recommended hold | 63 trading days (3 months) |

---

## What it does

On each scan, the screener downloads 2 years of daily OHLCV data for up to 500 S&P 500 tickers and applies **10 filters** in sequence:

| # | Filter | Threshold | Rationale |
|---|--------|-----------|-----------|
| 1 | Day change | **< −5%** | Panic selloff — entry signal for mean-reversion |
| 2 | Market cap | **> $1 B** | Liquidity — eliminates micro/nano-caps |
| 3 | Price | **> $5** | Avoids penny stocks |
| 4 | Volume | **> 500 K shares** | Confirms real participation on the selloff day |
| 5 | RVOL | **> 3.5×** | Capitulation volume surge (panic, not routine selling) |
| 6 | RSI (14) | **< 30** | Extreme oversold / capitulation |
| 7 | SMA 200 | **Price > 75% of SMA200** | Structural uptrend still intact — not in freefall |
| 8 | EMA stack | **EMA20 > EMA50 > EMA200** | Macro trend aligned across all timeframes |
| 9 | SMA 50 | **Price ≤ 90% of SMA50** | Deep discount below 50-day trend — +6.1pp win rate vs. base |
| 10 | Sector | **Excludes Health Care, Comm. Services, Utilities** | Empirically −10 to −17pp win rate on panic-selloff setups |

Tickers that pass all 10 filters are surfaced in the UI as potential recovery trade candidates and emailed every 15 minutes from 11 AM to 4 PM ET on NYSE trading days. Recommended hold: **63 trading days (3 months)**.

Each result also includes computed entry / stop levels for the snap-back trade:

| # | Entry | Calculation | Stop | Calculation |
|---|-------|-------------|------|-------------|
| ① | Buy today | current price | Tight stop | price − 1.0 × ATR14 |
| ② | EMA8 reclaim | EMA8 value | Under EMA8 | EMA8 − 0.5 × ATR14 |
| ③ | SMA200 test | SMA200 value | Under SMA200 | SMA200 − 0.5 × ATR14 |

Risk per share for each scenario is shown in the expanded row of the web UI table.

### Backtest calibration

The filter thresholds above were chosen empirically via a **full 5-year sweep** of every S&P 500 constituent (2021–2026):

| Step | Method | Outcome |
|------|--------|---------|
| Base sweep | 18 threshold combinations tested against 666K ticker-days | Best base: **RSI < 30, Day < −5%, RVOL > 3.5×, SMA200 > 75%, EMA stack** |
| Second-layer sweep | 10 additional filters tested on top of the base | `price ≤ 90% SMA50` adds **+6.1pp** (N = 52 signals) |
| Sector sweep | All 11 GICS sectors scored for 3-month win rate | Communication Services (44%), Utilities (50%), Health Care (56%) all underperform the **61% base** — excluded |
| Final result | Combined configuration | **67% 3-month win rate · +7.7% avg return** |

Run the backtest yourself: `python3 -m backend.reverse_backtest --refine --base-rsi 30 --base-rvol 3.5`

### API output fields

Every matched stock returns the following fields from `/api/scan`:

| Field | Description | Filter? |
|-------|-------------|---------|
| `ticker`, `exchange` | Symbol and listing exchange | — |
| `price`, `change` | Last price; day change % | change < −5% |
| `volume` | Day volume (shares) | > 500K |
| `vol_ratio` | Volume ÷ 20-day avg volume | > 3.5× (RVOL) |
| `market_cap` | Market capitalisation | > $1B |
| `rsi` | RSI(14) | < 30 (extreme oversold) |
| `macd`, `macd_signal`, `macd_hist` | MACD line, signal, histogram | informational |
| `ema8`, `ema20`, `ema50`, `ema200` | Exponential moving averages | EMA20 > EMA50 > EMA200 |
| `sma200` | 200-day simple moving average | price > 75% of SMA200 |
| `sma50` | 50-day simple moving average | price ≤ 90% of SMA50 |
| `bb_upper`, `bb_middle`, `bb_lower` | Bollinger Bands (20, 2) | informational |
| `atr14` | Average True Range (14) | — |
| `entry1/2/3` | Three recovery entry price levels | — |
| `stop1/2/3` | Corresponding stop loss levels | — |

---

## Architecture

```mermaid
flowchart LR
    browser(["Browser"])

    subgraph vercel ["Vercel · React + Vite"]
        ui["EventSource · SSE consumer\nreal-time progress + results\nSwing level table"]
    end

    subgraph render ["Render · FastAPI + Uvicorn"]
        api["GET /api/scan · GET /api/filters"]
        scanner["scanner.py · asyncio\nparallel chunks · Semaphore(5) · Queue"]
        cache[("scan_cache.json · 10-min TTL")]
        api --> scanner
        scanner <--> cache
    end

    subgraph gha ["GitHub Actions"]
        keepalive["keepalive.yml\n10:50 AM ET · Mon–Fri"]
        daily["daily-scan.yml\nevery 15 min · 11 AM–4 PM ET · Mon–Fri\nrun_scan.py → scanner.py → notifications.py"]
    end

    yf[("yfinance\nOHLCV · market cap · last price")]
    sp500[("S&P 500 CSV\ngithub.com/datasets · ~500 tickers")]
    smtp["SMTP · Gmail"]
    subs(["Subscribers"])

    browser --> vercel
    browser -->|GET /api/scan| render
    render -->|SSE events| browser
    keepalive -->|"GET / · wake"| render
    scanner --> yf
    scanner --> sp500
    daily --> yf
    daily --> sp500
    daily -->|HTML email| smtp
    smtp --> subs
```

- **GitHub Actions** owns the scheduled scan and email. `keepalive.yml` pings Render 10 minutes before the daily scan to avoid cold-start delays. `daily-scan.yml` runs four cron entries (EDT + EST) and guards against duplicate fires on DST transition weeks via `is_nyse_trading_day()`. Recipients are stored in the `EMAIL_LIST` Actions variable and written to `backend/recipients.txt` at runtime.
- **Render** hosts the FastAPI backend (512 MB RAM; sleeps after 15 min of inactivity). The keepalive ping ensures it is warm when the daily scan runs.
- **Vercel** serves the static React build. The browser connects directly to the Render backend via SSE for real-time scan progress. `VITE_API_URL` wires up the Render service URL.
- **Tickers** are fetched from the [S&P 500 constituents CSV](https://github.com/datasets/s-and-p-500-companies) at scan time; a 10-ticker fallback list is used if the fetch fails.

---

## Local development

### Prerequisites
- Python 3.11+
- Node.js 20+

### Backend

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r backend/requirements.txt

uvicorn backend.main:app --reload
# API available at http://localhost:8000
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
# Backend
python3 -m pytest backend/ -v

# Frontend
cd frontend && npm run test
```

---

> For cloud deployment instructions see [DEPLOY.md](DEPLOY.md).

---

## Adding to the subscriber list

The daily scan email is sent to everyone in the `EMAIL_LIST` GitHub Actions variable. There are two ways to add a recipient:

### Option 1 — Admin: edit the variable directly

Go to **Settings → Secrets and variables → Actions → Variables tab → EMAIL_LIST → Edit** and append the address (one per line). Takes effect on the next workflow run.

### Option 2 — Public request via GitHub Issues

This repo has a structured issue form that lets anyone request to be added without exposing their email publicly.

**As a subscriber:**
1. Open a new issue using the **Subscribe to Daily Scan Email** template.
2. Check the consent boxes and submit — do not include your email address in the issue.
3. A maintainer will approve the request and contact you privately via your GitHub notification email to collect your address.

**As the admin (after receiving the address privately):**
1. Go to **Actions → Add Email Subscriber → Run workflow**.
2. Enter the email address and optionally the issue number (to auto-close it with a confirmation comment).
3. Click **Run workflow** — the `EMAIL_LIST` variable is updated immediately.

---

## Email format

Scan results are delivered as a dark-themed HTML email that mirrors the web UI:

- **Tickers** are hyperlinked to their Google Finance page (`google.com/finance/quote/AAPL:NASDAQ`)
- **Change %** is colour-coded: teal for gains, red for losses
- **Volume** and **Market Cap** use compact notation (`52.30M`, `3.29T`)
- Columns: Ticker · Price · Change % · Volume · Market Cap · RSI · MACD
- When `full_scan=false, send_email=true` is triggered, an amber banner labels the data as a test

The web UI exposes all output fields (see table above) and adds an expandable row per ticker showing MA chips, Bollinger Band levels, and the full swing trade levels table (3 scenarios: **Breakout (now) · EMA 8 pullback · BB midline dip**). The email intentionally sends only the 7 summary columns.

---

## Key dependencies

| Package | Purpose |
|---------|---------|
| `fastapi` + `uvicorn` | Async HTTP server with SSE streaming |
| `yfinance` | OHLCV data downloads + market cap via `fast_info` |
| `pandas` | DataFrame-based OHLCV processing and rolling indicator windows |
| `pandas-ta-classic` | EMA / SMA calculation (NumPy 2.0 compatible fork) |
| `pandas-market-calendars` | NYSE trading-day check |
| `rich` | Coloured terminal logging |
| `requests` | S&P 500 constituents CSV fetch |
| `pytz` | Timezone handling for ET cron logic |
