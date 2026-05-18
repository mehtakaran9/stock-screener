# Backend — FastAPI Stock Screener

FastAPI service that scans S&P 500 stocks, streams results via Server-Sent Events, and sends a daily email digest.

## Modules

| File | Role |
|------|------|
| `main.py` | FastAPI app — `/api/scan` SSE endpoint, `/api/filters`, cache |
| `scanner.py` | Bulk yfinance downloads, fourteen-filter screening logic, async generator |
| `notifications.py` | HTML email builder and SMTP sender |
| `run_scan.py` | GitHub Actions entrypoint — trading-day check, scan, email |
| `utils.py` | Design tokens (colours, formatters) shared between email and web UI |

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

## Log file

`backend/scanner.log` (gitignored) is a rotating log (5 MB × 3 backups) written by `scanner.py`. In production on Render it lands in the `backend/` directory of the deployed source.
