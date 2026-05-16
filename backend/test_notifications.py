import pytest
from unittest.mock import patch, MagicMock, call
from backend.notifications import _chip, _build_html_table, send_scan_results_email, _DUMMY_STOCKS

_STOCK = {
    "ticker": "AAPL", "exchange": "NASDAQ",
    "price": 150.0, "change": 4.0,
    "volume": 1_000_000, "vol_ratio": 2.5,
    "market_cap": 2_000_000_000,
    "rsi": 60.0, "macd": 1.0, "macd_signal": 0.8, "macd_hist": 0.2,
    "ema8": 148.0, "ema50": 140.0, "ema200": 130.0,
    "sma50": 139.0, "sma200": 135.0,
    "bb_upper": 160.0, "bb_middle": 152.0, "bb_lower": 144.0,
    "atr14": 3.0,
    "entry1": 150.0, "entry2": 148.0, "entry3": 152.0,
    "stop1": 147.0, "stop2": 146.5, "stop3": 137.5,
}

_SMTP_ENV = {
    "EMAIL_SMTP_HOST": "smtp.example.com",
    "EMAIL_SMTP_PORT": "587",
    "EMAIL_USER": "user@example.com",
    "EMAIL_PASSWORD": "secret",
}


@pytest.fixture
def recipients_file(tmp_path, monkeypatch):
    """Patch RECIPIENTS_FILE to a temp file pre-populated with one address."""
    rf = tmp_path / "recipients.txt"
    rf.write_text("a@example.com\n")
    monkeypatch.setattr("backend.notifications.RECIPIENTS_FILE", rf)
    return rf


# ── _chip ─────────────────────────────────────────────────────────────────────

def test_chip_contains_label():
    html = _chip("EMA 8", 148.0)
    assert "EMA 8" in html


def test_chip_contains_value():
    html = _chip("EMA 8", 148.0)
    assert "148.00" in html


def test_chip_is_td():
    html = _chip("EMA 8", 148.0)
    assert html.startswith("<td")


# ── _build_html_table ─────────────────────────────────────────────────────────

def test_build_html_table_empty():
    result = _build_html_table([])
    assert "No stocks matched" in result


def test_build_html_table_with_stock():
    result = _build_html_table([_STOCK])
    assert "AAPL" in result
    assert "150.00" in result
    assert "+4.00%" in result


def test_build_html_table_negative_change():
    stock = {**_STOCK, "change": -2.0}
    result = _build_html_table([stock])
    assert "-2.00%" in result


def test_build_html_table_low_vol_ratio():
    stock = {**_STOCK, "vol_ratio": 1.2}
    result = _build_html_table([stock])
    assert "1.2" in result


def test_build_html_table_bearish_macd():
    stock = {**_STOCK, "macd_hist": -0.5}
    result = _build_html_table([stock])
    assert "Bear" in result


def test_build_html_table_is_test_shows_banner():
    result = _build_html_table([_STOCK], is_test=True)
    assert "test email" in result.lower()


def test_build_html_table_not_test_no_banner():
    result = _build_html_table([_STOCK], is_test=False)
    assert "test email" not in result.lower()


def test_build_html_table_multiple_stocks():
    result = _build_html_table([_STOCK, {**_STOCK, "ticker": "MSFT", "exchange": "NASDAQ"}])
    assert "AAPL" in result
    assert "MSFT" in result


def test_build_html_table_google_finance_link():
    result = _build_html_table([_STOCK])
    assert "google.com/finance/quote/AAPL:NASDAQ" in result


def test_build_html_table_ma_chips():
    result = _build_html_table([_STOCK])
    assert "148.00" in result  # ema8 in chip
    assert "SMA 200" in result


def test_build_html_table_swing_levels():
    result = _build_html_table([_STOCK])
    assert "Breakout" in result
    assert "EMA 8 pullback" in result
    assert "BB midline" in result


# ── send_scan_results_email ───────────────────────────────────────────────────

def test_send_email_missing_config_returns_early(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.notifications.RECIPIENTS_FILE", tmp_path / "missing.txt")
    with patch.dict("os.environ", {}, clear=True):
        send_scan_results_email([_STOCK])  # must not raise


def test_send_email_no_stocks_not_test_skips(recipients_file):
    with patch.dict("os.environ", _SMTP_ENV):
        with patch("backend.notifications.smtplib.SMTP") as mock_smtp:
            send_scan_results_email([], is_test=False)
        mock_smtp.assert_not_called()


def test_send_email_smtp_success_single_recipient(recipients_file):
    with patch.dict("os.environ", _SMTP_ENV):
        with patch("backend.notifications.smtplib.SMTP") as mock_smtp_cls:
            mock_server = MagicMock()
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
            send_scan_results_email([_STOCK])
        mock_server.send_message.assert_called_once()


def test_send_email_smtp_success_multiple_recipients(recipients_file):
    recipients_file.write_text("a@ex.com\nb@ex.com\n")
    with patch.dict("os.environ", _SMTP_ENV):
        with patch("backend.notifications.smtplib.SMTP") as mock_smtp_cls:
            mock_server = MagicMock()
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
            send_scan_results_email([_STOCK])
        assert mock_server.send_message.call_count == 2


def test_send_email_is_test_sends_even_with_no_stocks(recipients_file):
    with patch.dict("os.environ", _SMTP_ENV):
        with patch("backend.notifications.smtplib.SMTP") as mock_smtp_cls:
            mock_server = MagicMock()
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
            send_scan_results_email([], is_test=True)
        mock_server.send_message.assert_called_once()


def test_send_email_retry_then_success(recipients_file):
    with patch.dict("os.environ", _SMTP_ENV):
        call_count = {"n": 0}

        def smtp_factory(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ConnectionRefusedError("refused")
            mock = MagicMock()
            mock.__enter__ = MagicMock(return_value=MagicMock())
            mock.__exit__ = MagicMock(return_value=False)
            return mock

        with patch("backend.notifications.smtplib.SMTP", side_effect=smtp_factory):
            with patch("backend.notifications.time.sleep"):
                send_scan_results_email([_STOCK])

        assert call_count["n"] == 2


def test_send_email_all_attempts_fail(recipients_file):
    with patch.dict("os.environ", _SMTP_ENV):
        with patch("backend.notifications.smtplib.SMTP", side_effect=ConnectionRefusedError("refused")):
            with patch("backend.notifications.time.sleep"):
                send_scan_results_email([_STOCK])  # Must not raise


def test_dummy_stocks_list_not_empty():
    assert len(_DUMMY_STOCKS) > 0
    assert "ticker" in _DUMMY_STOCKS[0]
