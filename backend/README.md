# Backend — FastAPI Stock Screener

FastAPI service with two stock screening strategies, SSE streaming, 10-minute result caches, and a daily email digest.

## Modules

| File | Role |
|------|------|
| `main.py` | FastAPI app — `/api/scan`, `/api/filters`, `/api/scan-v2`, `/api/filters-v2`, cache |
| `scanner.py` | Recovery screener — 10-filter pipeline (price > 75% SMA200); bulk yfinance downloads, async generator |
| `scanner_v2.py` | Big Move screener — 8-filter extreme dislocation pipeline (price < 70% SMA200); identical async generator shape |
| `notifications.py` | HTML email builder and SMTP sender |
| `run_scan.py` | GitHub Actions entrypoint — trading-day check, scan, email |
| `utils.py` | Design tokens (colours, formatters) shared between email and web UI |
| `bigmove_research.py` | 10-year S&P 500 backtest CLI — identifies signal combos that predict 30%+ moves in 42 days; run with `python3 -m backend.bigmove_research` |
| `alt_data.py` | Alternative data utilities (SEC filings, FINRA short interest, earnings) used by `bigmove_research.py` with `--with-alt-data` |

## Environment variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `ALLOWED_ORIGINS` | No | `http://localhost:5173` | CORS origin allowlist (comma-separated) |
| `ALLOWED_ORIGIN_REGEX` | No | — | Regex for additional allowed origins (e.g. Vercel preview URLs). Use anchors: `^https://your-app[^.]*\.vercel\.app$` |
| `EMAIL_SMTP_HOST` | Yes (email) | — | SMTP server hostname |
| `EMAIL_SMTP_PORT` | No | `587` | SMTP port |
| `EMAIL_USER` | Yes (email) | — | Sender address |
| `EMAIL_PASSWORD` | Yes (email) | — | SMTP password / App Password |
| `FULL_SCAN` | No | — | GitHub Actions only: `true` = run full scan; `false` = skip scan; empty = apply trading-day check |
| `SEND_EMAIL` | No | `true` | GitHub Actions only: `false` = suppress email; anything else = send if results found |

Recipients are read from `backend/recipients.txt` (one address per line, gitignored).

## Running locally

```bash
# from repo root
uvicorn backend.main:app --reload
# API at http://localhost:8000
# Docs at http://localhost:8000/docs
```

## Running tests

```bash
# from repo root
python3 -m pytest backend/ -v
```

## Scan cache

`backend/scan_cache.json` (gitignored) stores the last scan result for 10 minutes so repeated page refreshes skip the full yfinance download. The file is created/updated by the `/api/scan` endpoint and expires automatically.

A parallel cache `backend/scan_cache_v2.json` (gitignored) serves the same purpose for `/api/scan-v2`. Both caches are independent with separate 10-minute TTLs and `asyncio.Lock`s.

## Log file

`backend/scanner.log` (gitignored) is a rotating log (5 MB × 3 backups) written by `scanner.py`. In production on Render it lands in the `backend/` directory of the deployed source.

A parallel log `backend/scanner_v2.log` (gitignored, same rotating config) is written by `scanner_v2.py`.
