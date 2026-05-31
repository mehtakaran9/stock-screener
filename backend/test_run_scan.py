import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from backend.run_scan import is_nyse_trading_day, main


# ── is_nyse_trading_day ───────────────────────────────────────────────────────

def test_is_nyse_trading_day_true():
    with patch("backend.run_scan.mcal") as mock_mcal:
        mock_cal = MagicMock()
        mock_cal.schedule.return_value = MagicMock(empty=False)
        mock_mcal.get_calendar.return_value = mock_cal
        assert is_nyse_trading_day() is True


def test_is_nyse_trading_day_false():
    with patch("backend.run_scan.mcal") as mock_mcal:
        mock_cal = MagicMock()
        mock_cal.schedule.return_value = MagicMock(empty=True)
        mock_mcal.get_calendar.return_value = mock_cal
        assert is_nyse_trading_day() is False


def test_is_nyse_trading_day_handles_error():
    """Transient calendar/network failure → return False (skip scan), not crash."""
    with patch("backend.run_scan.mcal") as mock_mcal:
        mock_mcal.get_calendar.side_effect = ConnectionError("network down")
        assert is_nyse_trading_day() is False


# ── main() ────────────────────────────────────────────────────────────────────

async def test_main_skip_scan_send_test_email():
    with patch.dict("os.environ", {"FULL_SCAN": "false", "SEND_EMAIL": "true"}):
        with patch("backend.run_scan.send_scan_results_email") as mock_send:
            result = await main()
    assert result == 0
    mock_send.assert_called_once()
    args, kwargs = mock_send.call_args
    assert kwargs.get("is_test") is True or (len(args) > 1 and args[1] is True)


async def test_main_skip_scan_no_email():
    with patch.dict("os.environ", {"FULL_SCAN": "false", "SEND_EMAIL": "false"}):
        with patch("backend.run_scan.send_scan_results_email") as mock_send:
            result = await main()
    assert result == 0
    mock_send.assert_not_called()


async def test_main_not_trading_day_skips_scan():
    with patch.dict("os.environ", {"FULL_SCAN": ""}):
        with patch("backend.run_scan.is_nyse_trading_day", return_value=False):
            with patch("backend.run_scan.screen_stocks_v2") as mock_screen:
                result = await main()
    assert result == 0
    mock_screen.assert_not_called()


async def _empty_screen(tickers):
    """Async generator that yields nothing."""
    return
    yield  # noqa: unreachable — makes this an async generator


async def _progress_screen(tickers):
    yield {"status": "progress", "current": 1}


async def _result_screen(tickers):
    yield {"ticker": "AAPL", "price": 150.0, "change": 5.0}


async def test_main_manual_trigger_with_results():
    with patch.dict("os.environ", {"FULL_SCAN": "true", "SEND_EMAIL": "true"}):
        with patch("backend.run_scan.get_full_market_tickers", return_value=(["AAPL"], True)):
            with patch("backend.run_scan.screen_stocks_v2", side_effect=_result_screen):
                with patch("backend.run_scan.send_scan_results_email") as mock_send:
                    result = await main()
    assert result == 0
    mock_send.assert_called_once()


async def test_main_manual_trigger_no_results_no_email():
    with patch.dict("os.environ", {"FULL_SCAN": "true", "SEND_EMAIL": "true"}):
        with patch("backend.run_scan.get_full_market_tickers", return_value=(["AAPL"], True)):
            with patch("backend.run_scan.screen_stocks_v2", side_effect=_progress_screen):
                with patch("backend.run_scan.send_scan_results_email") as mock_send:
                    result = await main()
    assert result == 0
    mock_send.assert_not_called()


async def test_main_email_disabled_even_with_results():
    with patch.dict("os.environ", {"FULL_SCAN": "true", "SEND_EMAIL": "false"}):
        with patch("backend.run_scan.get_full_market_tickers", return_value=(["AAPL"], True)):
            with patch("backend.run_scan.screen_stocks_v2", side_effect=_result_screen):
                with patch("backend.run_scan.send_scan_results_email") as mock_send:
                    result = await main()
    assert result == 0
    mock_send.assert_not_called()


async def test_main_fallback_tickers_logs_warning():
    with patch.dict("os.environ", {"FULL_SCAN": "true", "SEND_EMAIL": "false"}):
        with patch("backend.run_scan.get_full_market_tickers", return_value=(["AAPL"], False)):
            with patch("backend.run_scan.screen_stocks_v2", side_effect=_empty_screen):
                result = await main()
    assert result == 0


async def test_main_scheduled_run_on_trading_day():
    with patch.dict("os.environ", {"FULL_SCAN": "", "SEND_EMAIL": "false"}):
        with patch("backend.run_scan.is_nyse_trading_day", return_value=True):
            with patch("backend.run_scan.get_full_market_tickers", return_value=(["AAPL"], True)):
                with patch("backend.run_scan.screen_stocks_v2", side_effect=_empty_screen):
                    result = await main()
    assert result == 0
