import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from backend.scheduler import start_scheduler, run_scheduled_scan


# ── run_scheduled_scan ────────────────────────────────────────────────────────

async def _result_screen(tickers):
    yield {"ticker": "AAPL", "price": 150.0}


async def _progress_screen(tickers):
    yield {"status": "progress", "current": 1}


async def _fallback_screen(tickers):
    return
    yield  # noqa: unreachable


async def test_run_scheduled_scan_with_results_sends_email():
    with patch("backend.scheduler.get_full_market_tickers", return_value=(["AAPL"], True)):
        with patch("backend.scheduler.screen_stocks", side_effect=_result_screen):
            with patch("backend.scheduler.send_scan_results_email") as mock_send:
                await run_scheduled_scan()
    mock_send.assert_called_once()


async def test_run_scheduled_scan_no_results_no_email():
    with patch("backend.scheduler.get_full_market_tickers", return_value=(["AAPL"], True)):
        with patch("backend.scheduler.screen_stocks", side_effect=_progress_screen):
            with patch("backend.scheduler.send_scan_results_email") as mock_send:
                await run_scheduled_scan()
    mock_send.assert_not_called()


async def test_run_scheduled_scan_fallback_tickers_logs_warning():
    with patch("backend.scheduler.get_full_market_tickers", return_value=(["AAPL"], False)):
        with patch("backend.scheduler.screen_stocks", side_effect=_fallback_screen):
            with patch("backend.scheduler.send_scan_results_email"):
                await run_scheduled_scan()


# ── start_scheduler ───────────────────────────────────────────────────────────

def test_start_scheduler_starts_and_returns():
    with patch("backend.scheduler.AsyncIOScheduler") as mock_cls:
        mock_sched = MagicMock()
        mock_cls.return_value = mock_sched

        result = start_scheduler()

        mock_sched.add_job.assert_called_once()
        mock_sched.start.assert_called_once()
        assert result is mock_sched


# ── wrapped_scan (the inner coroutine registered with the scheduler) ──────────

def _capture_wrapped_scan():
    """Call start_scheduler() with AsyncIOScheduler mocked; returns the captured wrapped_scan coro."""
    captured = {}

    with patch("backend.scheduler.AsyncIOScheduler") as mock_cls:
        mock_sched = MagicMock()
        mock_cls.return_value = mock_sched

        def capture_add_job(fn, trigger):
            captured["fn"] = fn

        mock_sched.add_job.side_effect = capture_add_job
        start_scheduler()

    return captured["fn"]


async def test_wrapped_scan_on_trading_day_calls_run():
    wrapped = _capture_wrapped_scan()

    with patch("backend.scheduler.mcal") as mock_mcal:
        mock_cal = MagicMock()
        mock_cal.schedule.return_value = MagicMock(empty=False)
        mock_mcal.get_calendar.return_value = mock_cal

        with patch("backend.scheduler.run_scheduled_scan", new_callable=AsyncMock) as mock_run:
            await wrapped()

    mock_run.assert_called_once()


async def test_wrapped_scan_on_non_trading_day_skips():
    wrapped = _capture_wrapped_scan()

    with patch("backend.scheduler.mcal") as mock_mcal:
        mock_cal = MagicMock()
        mock_cal.schedule.return_value = MagicMock(empty=True)
        mock_mcal.get_calendar.return_value = mock_cal

        with patch("backend.scheduler.run_scheduled_scan", new_callable=AsyncMock) as mock_run:
            await wrapped()

    mock_run.assert_not_called()
