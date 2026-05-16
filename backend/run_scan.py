#!/usr/bin/env python3
"""
Standalone scan runner for GitHub Actions cron.
Usage: python -m backend.run_scan
Required env vars: EMAIL_SMTP_HOST, EMAIL_USER, EMAIL_PASSWORD, EMAIL_TO
Optional env vars: EMAIL_SMTP_PORT (default 587)
"""
import asyncio
import logging
import os
import sys
from datetime import datetime

import pandas_market_calendars as mcal
import pytz

from backend.scanner import get_full_market_tickers, screen_stocks
from backend.notifications import send_scan_results_email

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("run_scan")


def is_nyse_trading_day() -> bool:
    nyse = mcal.get_calendar('NYSE')
    today_et = datetime.now(pytz.timezone('US/Eastern')).date()
    schedule = nyse.schedule(start_date=today_et.isoformat(), end_date=today_et.isoformat())
    return not schedule.empty


async def main() -> int:
    test_mode = os.getenv("TEST_EMAIL", "").lower() == "true"

    if not test_mode and not is_nyse_trading_day():
        logger.info("Not a NYSE trading day — skipping scan.")
        return 0

    if test_mode:
        logger.info("Test mode — skipping trading day check, running full scan.")

    tickers, is_full = get_full_market_tickers()
    if not is_full:
        logger.warning("Using fallback ticker list — S&P 500 CSV unavailable.")

    target = tickers[:500]
    logger.info(f"Starting scan of {len(target)} tickers...")

    results = []
    async for update in screen_stocks(target):
        if isinstance(update, dict) and update.get('status') != 'progress':
            results.append(update)
            logger.info(f"Match: {update['ticker']} ${update['price']:.2f} ({update['change']:.1f}%)")

    logger.info(f"Scan complete. {len(results)} matches found.")

    if results:
        send_scan_results_email(results)
    elif test_mode:
        logger.info("No matches found, but sending email anyway (test mode).")
        send_scan_results_email([])
    else:
        logger.info("No matches — skipping email.")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
