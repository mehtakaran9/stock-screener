# Backend — FastAPI Stock Screener

FastAPI service with a **unified stock screener** (Big Move extreme-dislocation pipeline + ⭐ HIGH CONVICTION badge), SSE streaming, 10-minute result caches, and a daily email digest. Two earlier strategies — Recovery (v1) and standalone Conviction (v3) — remain as **dormant** endpoints (code kept, not surfaced).

## Modules

| File | Role |
|------|------|
| `main.py` | FastAPI app. **Primary:** `/api/scan-v2` + `/api/filters-v2` (the unified scan). **Dormant:** `/api/scan` + `/api/filters` (v1), `/api/scan-v3` + `/api/filters-v3` (standalone v3). Per-endpoint 10-min caches. |
| `scanner.py` | **Shared base** + dormant Recovery screener. Owns the S&P ticker fetch (`get_full_market_tickers`), market-cap/exchange + rate-limit helpers imported by v2/v3. Its v1 recovery pipeline (price > 75% SMA200) is **dormant** (`/api/scan`). |
| `scanner_v2.py` | **PRIMARY unified screener** — 8-filter extreme-dislocation pipeline (price < 70% SMA200). Rows that also clear the tighter v3 gates + alt-data are flagged `conviction_score ≥ 1` (⭐ HIGH CONVICTION). Surfaced at `/api/scan-v2`. |
| `scanner_v3.py` | Conviction logic — tighter `CONFIG_V3` technical pipeline (RSI < 25, RVOL > 3.5×, candle ≥ 1.5× ATR) + alt-data gate. Its `_filter_ticker_v3_technical` + `_fetch_alt_data_v3` are **reused by the unified scan** to compute the HIGH CONVICTION badge (~3–4 signals/yr); the standalone `/api/scan-v3` generator is **dormant**. |
| `notifications.py` | HTML email builder and SMTP sender |
| `run_scan.py` | GitHub Actions entrypoint — trading-day check, **unified (v2 + ⭐ HIGH CONVICTION) scan**, email |
| `utils.py` | Design tokens (colours, formatters) shared between email and web UI |
| `recovery_scanner.py` | Standalone CLI (**not** wired into the API) — alternative recovery / mean-reversion screener with a ~10-day hold; `python3 -m backend.recovery_scanner` |
| `alt_data.py` | Alternative-data utilities (SEC Form 4 insider buys, earnings beat streak, Polygon options flow) — power the unified scan's ⭐ HIGH CONVICTION badge (via `scanner_v3.py`) and the research CLIs |
| `bigmove_research.py` | 10-year S&P 500 backtest CLI (v2 research) — signal combos that predict 30%+ moves in 42 days; `python3 -m backend.bigmove_research` |
| `conviction_research.py` | Conviction (v3) research CLI — multi-factor + alt-data backtest; `python3 -m backend.conviction_research` |
| `reverse_backtest.py` | 5-year recovery (v1) calibration CLI — base + second-layer filter/sector sweep; `python3 -m backend.reverse_backtest --refine` |
| `backtest_may2026.py` | Point-in-time backtest CLI — applies screener filters on given scan dates and evaluates returns ~2 weeks later; `python3 -m backend.backtest_may2026` |

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

Parallel caches `backend/scan_cache_v2.json` and `backend/scan_cache_v3.json` (gitignored) serve the same purpose for `/api/scan-v2` and `/api/scan-v3`. All three caches are independent with separate 10-minute TTLs and `asyncio.Lock`s.

## Log file

`backend/scanner.log` (gitignored) is a rotating log (5 MB × 3 backups) written by `scanner.py`. In production on Render it lands in the `backend/` directory of the deployed source.

Parallel logs `backend/scanner_v2.log` and `backend/scanner_v3.log` (gitignored, same rotating config) are written by `scanner_v2.py` and `scanner_v3.py`.
