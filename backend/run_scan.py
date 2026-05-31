#!/usr/bin/env python3
"""
Standalone scan runner for GitHub Actions cron.
Usage: python -m backend.run_scan
Required env vars: EMAIL_SMTP_HOST, EMAIL_USER, EMAIL_PASSWORD
Optional env vars:
  EMAIL_SMTP_PORT  default 587
  FULL_SCAN        'true' = run scan + skip trading day check (manual trigger)
                   'false' = skip scan
                   ''      = scheduled run (apply trading day check)
  SEND_EMAIL       'false' = suppress email; anything else = send if results found
Recipients are read from backend/recipients.txt (one address per line).
"""
import asyncio
import logging
import os
import sys
from datetime import datetime

import pandas_market_calendars as mcal
import pytz

from backend.scanner import get_full_market_tickers
from backend.scanner_v2 import screen_stocks_v2  # unified scanner (Big Move + ⭐ HIGH CONVICTION)
from backend.notifications import send_scan_results_email, _DUMMY_STOCKS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("run_scan")


def is_nyse_trading_day() -> bool:
    # Wrapped so a transient failure (network/data error building the NYSE
    # calendar) doesn't crash the scheduled job. On error, return False so the
    # scan is skipped rather than running on a possibly non-trading day.
    try:
        nyse = mcal.get_calendar('NYSE')
        today_et = datetime.now(pytz.timezone('US/Eastern')).date()
        schedule = nyse.schedule(start_date=today_et.isoformat(), end_date=today_et.isoformat())
        return not schedule.empty
    except Exception as e:
        logger.warning(f"NYSE trading-day check failed ({e}); skipping scan to be safe")
        return False


async def main() -> int:
    # Empty string means scheduled run — treat as full_scan=true + send_email=true
    full_scan_input = os.getenv("FULL_SCAN", "").lower()
    send_email = os.getenv("SEND_EMAIL", "").lower() != "false"  # default true

    if full_scan_input == "false":
        if send_email:
            logger.info("Scan skipped — sending test email with dummy data.")
            send_scan_results_email(_DUMMY_STOCKS, is_test=True)
        else:
            logger.info("Scan skipped, email disabled — nothing to do.")
        return 0

    # full_scan=true (manual trigger) skips trading day check; scheduled runs don't
    is_manual = full_scan_input == "true"
    if not is_manual and not is_nyse_trading_day():
        logger.info("Not a NYSE trading day — skipping scan.")
        return 0

    if is_manual:
        logger.info("Manual trigger — skipping trading day check, running full scan.")

    tickers, is_full = get_full_market_tickers()
    if not is_full:
        logger.warning("Using fallback ticker list — S&P 500 CSV unavailable.")

    target = tickers[:500]
    logger.info(f"Starting scan of {len(target)} tickers...")

    results = []
    async for update in screen_stocks_v2(target):
        if isinstance(update, dict) and update.get('status') != 'progress':
            results.append(update)
            logger.info(f"Match: {update['ticker']} ${update['price']:.2f} ({update['change']:.1f}%)")

    logger.info(f"Scan complete. {len(results)} matches found.")

    if send_email:
        if results:
            send_scan_results_email(results)
        else:
            logger.info("No matches — skipping email.")
    else:
        logger.info(f"Email disabled — {len(results)} matches found but not sent.")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
