import smtplib
import os
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import logging

logger = logging.getLogger("notifications")


def _fmt_number(n: float) -> str:
    """Mirrors the website's formatNumber — no currency prefix, compact suffix."""
    if n >= 1_000_000_000_000:
        return f"{n / 1_000_000_000_000:.2f}T"
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.2f}K"
    return f"{n:,.0f}"


def _build_html_table(stocks_data: list, is_test: bool = False) -> str:
    TH = (
        "padding:10px 18px;text-align:left;font-size:11px;text-transform:uppercase;"
        "letter-spacing:0.06em;color:rgba(255,255,255,0.5);background:#121212;"
        "border-bottom:1px solid #333"
    )
    TD = "padding:12px 18px;border-bottom:1px solid #2a2a2a;font-size:13px"

    if not stocks_data:
        return (
            '<div style="background:#1e1e1e;border-radius:6px;padding:2rem;'
            'text-align:center;color:rgba(255,255,255,0.5);font-family:sans-serif">'
            "No stocks matched the screening criteria today.</div>"
        )

    rows = ""
    for s in stocks_data:
        ticker = s.get("ticker", "")
        exchange = s.get("exchange", "NASDAQ")
        url = f"https://www.google.com/finance/quote/{ticker}:{exchange}"
        change = s.get("change", 0)
        change_color = "#03dac6" if change >= 0 else "#cf6679"
        sign = "+" if change >= 0 else ""

        rows += (
            "<tr>"
            f'<td style="{TD};font-weight:700">'
            f'<a href="{url}" style="color:#bb86fc;text-decoration:none">{ticker}</a></td>'
            f'<td style="{TD};color:rgba(255,255,255,0.87)">${s.get("price", 0):.2f}</td>'
            f'<td style="{TD};color:{change_color};font-weight:600">{sign}{change:.2f}%</td>'
            f'<td style="{TD};color:rgba(255,255,255,0.87)">{_fmt_number(s.get("volume", 0))}</td>'
            f'<td style="{TD};color:rgba(255,255,255,0.87)">{_fmt_number(s.get("market_cap", 0))}</td>'
            f'<td style="{TD};color:rgba(255,255,255,0.87)">${s.get("ema8", 0):.2f}</td>'
            f'<td style="{TD};color:rgba(255,255,255,0.87)">${s.get("sma200", 0):.2f}</td>'
            "</tr>"
        )

    test_banner = (
        '<div style="background:#2a1a00;border:1px solid #f59e0b;border-radius:4px;'
        'padding:10px 16px;margin-bottom:18px;color:#f59e0b;font-size:12px">'
        "&#9888; This is a test email. The data below is sample/dummy data used to verify "
        "email delivery — it does not reflect real market results."
        "</div>"
        if is_test else ""
    )

    return (
        '<div style="background:#121212;padding:28px;border-radius:8px;'
        'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,sans-serif">'
        '<div style="margin-bottom:20px">'
        '<span style="color:#bb86fc;font-size:20px;font-weight:500">StockScreener Pro</span>'
        '<span style="color:rgba(255,255,255,0.5);font-size:13px;margin-left:12px">Daily Scan Results</span>'
        "</div>"
        f"{test_banner}"
        '<div style="background:#1e1e1e;border-radius:4px;overflow:hidden;border:1px solid #333">'
        '<table style="width:100%;border-collapse:collapse">'
        "<thead><tr>"
        f'<th style="{TH}">Ticker</th>'
        f'<th style="{TH}">Price</th>'
        f'<th style="{TH}">Change %</th>'
        f'<th style="{TH}">Volume</th>'
        f'<th style="{TH}">Market Cap</th>'
        f'<th style="{TH}">EMA8</th>'
        f'<th style="{TH}">SMA200</th>'
        f"</tr></thead><tbody>{rows}</tbody></table></div>"
        '<p style="color:rgba(255,255,255,0.3);font-size:11px;margin-top:14px;margin-bottom:0">'
        "Scan run at market close · S&amp;P 500 universe</p>"
        "</div>"
    )


_DUMMY_STOCKS = [
    {"ticker": "AAPL",  "exchange": "NASDAQ", "price": 213.45, "change": 3.82, "volume": 52_300_000, "market_cap": 3_290_000_000_000, "ema8": 208.12, "sma200": 185.60},
    {"ticker": "NVDA",  "exchange": "NASDAQ", "price": 875.20, "change": 5.14, "volume": 41_100_000, "market_cap": 2_150_000_000_000, "ema8": 851.33, "sma200": 620.48},
    {"ticker": "MSFT",  "exchange": "NASDAQ", "price": 421.80, "change": 3.21, "volume": 18_600_000, "market_cap": 3_130_000_000_000, "ema8": 413.55, "sma200": 380.10},
    {"ticker": "XOM",   "exchange": "NYSE",   "price": 118.75, "change": 4.07, "volume": 14_200_000, "market_cap":  470_000_000_000, "ema8": 114.92, "sma200":  98.30},
    {"ticker": "DXCM",  "exchange": "NASDAQ", "price":  62.40, "change": 6.59, "volume":  9_800_000, "market_cap":   23_000_000_000, "ema8":  60.11, "sma200":  52.75},
]


def send_scan_results_email(stocks_data: list, is_test: bool = False):
    """
    Formats scan results into an HTML table and sends via SMTP.
    Requires: EMAIL_SMTP_HOST, EMAIL_SMTP_PORT, EMAIL_USER, EMAIL_PASSWORD, EMAIL_TO
    EMAIL_TO accepts a comma-separated list of addresses; each gets a separate email.
    Pass is_test=True to add a banner noting the data is for testing only.
    """
    smtp_host = os.getenv("EMAIL_SMTP_HOST")
    smtp_port = int(os.getenv("EMAIL_SMTP_PORT", 587))
    smtp_user = os.getenv("EMAIL_USER")
    smtp_pass = os.getenv("EMAIL_PASSWORD")
    recipients = [e.strip() for e in os.getenv("EMAIL_TO", "").split(",") if e.strip()]

    if not all([smtp_host, smtp_user, smtp_pass, recipients]):
        logger.error("Email configuration missing. Cannot send notification.")
        return

    html_content = (
        "<!DOCTYPE html><html><body "
        'style="margin:0;padding:0;background-color:#0a0a0a">'
        '<div style="max-width:900px;margin:0 auto;padding:20px">'
        f"{_build_html_table(stocks_data, is_test=is_test)}"
        "</div></body></html>"
    )

    subject = "Stock Screener — Test Email (Dummy Data)" if is_test else "Stock Screener Daily Scan Results"

    for attempt in range(3):
        try:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                for recipient in recipients:
                    msg = MIMEMultipart()
                    msg['Subject'] = subject
                    msg['From'] = smtp_user
                    msg['To'] = recipient
                    msg.attach(MIMEText(html_content, 'html'))
                    server.send_message(msg)
                    logger.info(f"Email sent to {recipient}")
            return
        except Exception as e:
            if attempt < 2:
                wait = 30 * (attempt + 1)
                logger.warning(f"Email attempt {attempt + 1} failed: {e}. Retrying in {wait}s")
                time.sleep(wait)
            else:
                logger.error(f"All email attempts failed: {e}")
