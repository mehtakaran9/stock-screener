from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import json
import asyncio
import logging
import os
import pathlib
import time
import warnings
from rich.logging import RichHandler

# Suppress noisy multiprocessing warnings at shutdown
warnings.filterwarnings("ignore", category=UserWarning, module="multiprocessing")
from backend.scanner import get_full_market_tickers, screen_stocks, get_active_filters

# Configure logging with Rich
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True)]
)

# Suppress noisy logs from dependencies
logging.getLogger("yfinance").setLevel(logging.WARNING)
logging.getLogger("peewee").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

logger = logging.getLogger("main")

CACHE_FILE = pathlib.Path(__file__).parent / "scan_cache.json"
CACHE_TTL = 600  # 10 minutes


def _load_cache() -> tuple[list, int] | None:
    """Return (results, total) from a valid cache file. Returns None on miss/expiry."""
    if not CACHE_FILE.exists():
        return None
    try:
        with CACHE_FILE.open() as f:
            data = json.load(f)
        if time.time() - data["timestamp"] > CACHE_TTL:
            CACHE_FILE.unlink(missing_ok=True)
            return None
        return data["results"], data["total"]
    except Exception as e:
        logger.warning(f"Cache read error: {e}")
        CACHE_FILE.unlink(missing_ok=True)
        return None


def _save_cache(results: list, total: int) -> None:
    try:
        with CACHE_FILE.open("w") as f:
            json.dump({"timestamp": time.time(), "results": results, "total": total}, f)
        logger.info(f"Scan cache written ({len(results)} matches, expires in {CACHE_TTL}s)")
    except Exception as e:
        logger.warning(f"Cache write error: {e}")


app = FastAPI()

# Enable CORS for React frontend
_allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["GET"],
    allow_headers=["*"],
)

@app.api_route("/", methods=["GET", "HEAD"])
async def root():
    return {"message": "Stock Screener API is running. Visit /docs for API documentation."}

@app.get("/api/filters")
async def get_filters():
    return {"filters": get_active_filters()}

@app.get("/api/scan")
async def scan_market():
    async def event_generator():
        cached = await asyncio.to_thread(_load_cache)
        if cached is not None:
            results, total = cached
            logger.info(f"Serving {len(results)} results from cache")
            yield f"data: {json.dumps({'status': 'progress', 'total': total, 'current': total})}\n\n"
            for stock in results:
                yield f"data: {json.dumps({'status': 'result', 'data': stock})}\n\n"
            yield f"data: {json.dumps({'status': 'complete', 'total': total, 'from_cache': True})}\n\n"
            return

        tickers, is_full = await asyncio.to_thread(get_full_market_tickers)
        if not is_full:
            yield f"data: {json.dumps({'status': 'warning', 'message': 'S&P 500 list unavailable; scanning fallback tickers only.'})}\n\n"
        target_tickers = tickers[:500]

        yield f"data: {json.dumps({'status': 'progress', 'total': len(target_tickers), 'current': 0})}\n\n"

        results: list = []
        async for update in screen_stocks(target_tickers):
            if isinstance(update, dict):
                if update.get('status') == 'progress':
                    yield f"data: {json.dumps({'status': 'progress', 'total': len(target_tickers), 'current': update['current'], 'ticker': update.get('ticker')})}\n\n"
                else:
                    results.append(update)
                    yield f"data: {json.dumps({'status': 'result', 'data': update})}\n\n"

        await asyncio.to_thread(_save_cache, results, len(target_tickers))
        yield f"data: {json.dumps({'status': 'complete', 'total': len(target_tickers)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
