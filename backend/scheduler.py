import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pandas_market_calendars as mcal
from datetime import datetime
import pytz
from backend.scanner import get_full_market_tickers, screen_stocks
from backend.notifications import send_scan_results_email

logger = logging.getLogger("scheduler")

async def run_scheduled_scan():
    """Triggered job: runs the scan and sends email."""
    logger.info("Starting scheduled stock scan...")
    
    # 1. Run the scan
    tickers, is_full = get_full_market_tickers()
    if not is_full:
        logger.warning("Using fallback ticker list — S&P 500 CSV unavailable.")
    target_tickers = tickers[:500]
    results = []

    async for update in screen_stocks(target_tickers):
        if isinstance(update, dict) and update.get('status') != 'progress':
            results.append(update)
            
    logger.info(f"Scan complete. Found {len(results)} matches.")

    # 2. Email results
    if results:
        send_scan_results_email(results)
    else:
        logger.info("No results found, skipping email.")

def start_scheduler():
    scheduler = AsyncIOScheduler()
    
    # Schedule for 12:00 PM ET every day
    # Note: Market calendar check will determine if it executes fully
    trigger = CronTrigger(hour=12, minute=0, timezone='US/Eastern')
    
    def market_day_check():
        """Check if today is a trading day on NYSE."""
        nyse = mcal.get_calendar('NYSE')
        today = datetime.now(pytz.timezone('US/Eastern'))
        schedule = nyse.schedule(start_date=today, end_date=today)
        return not schedule.empty

    async def wrapped_scan():
        if market_day_check():
            await run_scheduled_scan()
        else:
            logger.info("Skipping scan: Not a US trading day.")

    scheduler.add_job(wrapped_scan, trigger=trigger)
    scheduler.start()
    logger.info("Scheduler started.")
    return scheduler
