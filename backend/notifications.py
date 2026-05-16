import smtplib
import os
import pathlib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import logging

from backend.utils import (
    BG_EMAIL_BODY, BG_PRIMARY, BG_SECONDARY, BG_DETAIL_ROW, BG_AMBER_BANNER,
    ACCENT, SUCCESS, DANGER, BORDER, BORDER_SUBTLE,
    TEXT_SECONDARY, TEXT_FAINT, AMBER,
    fmt_number, rsi_color, rsi_label,
)

logger = logging.getLogger("notifications")

RECIPIENTS_FILE = pathlib.Path(__file__).parent / "recipients.txt"


def _load_recipients() -> list[str]:
    try:
        return [line.strip() for line in RECIPIENTS_FILE.read_text().splitlines() if line.strip()]
    except FileNotFoundError:
        logger.error(f"Recipients file not found: {RECIPIENTS_FILE}")
        return []
    except Exception as e:
        logger.error(f"Failed to read recipients file: {e}")
        return []


def _chip(label: str, val: float) -> str:
    return (
        f'<td style="padding:6px 10px;border:1px solid {BORDER};background:{BG_SECONDARY};'
        'min-width:64px;text-align:center">'
        f'<div style="font-size:10px;text-transform:uppercase;color:{TEXT_FAINT};'
        f'letter-spacing:0.05em">{label}</div>'
        f'<div style="font-size:12px;font-weight:500;color:rgba(255,255,255,0.87);'
        f'margin-top:2px">${val:.2f}</div>'
        '</td>'
    )


def _build_html_table(stocks_data: list, is_test: bool = False) -> str:
    TH = (
        f"padding:10px 18px;text-align:left;font-size:11px;text-transform:uppercase;"
        f"letter-spacing:0.06em;color:{TEXT_SECONDARY};background:{BG_PRIMARY};"
        f"border:1px solid {BORDER}"
    )
    TD = f"padding:12px 18px;border-bottom:1px solid {BORDER_SUBTLE};border-right:1px solid {BORDER_SUBTLE};font-size:13px"
    TD_LAST = f"padding:12px 18px;border-bottom:1px solid {BORDER_SUBTLE};font-size:13px"

    SECTION_LABEL = (
        f"font-size:10px;text-transform:uppercase;color:{TEXT_FAINT};"
        "letter-spacing:0.07em;margin:10px 0 5px"
    )
    LTH = (
        f"padding:5px 10px;font-size:10px;text-transform:uppercase;letter-spacing:0.06em;"
        f"color:{TEXT_FAINT};background:{BG_PRIMARY};border-bottom:1px solid {BORDER};text-align:left"
    )
    LTD = f"padding:8px 10px;border-bottom:1px solid rgba(51,51,51,0.5);font-size:12px"
    LTD_LAST = "padding:8px 10px;font-size:12px"

    if not stocks_data:
        return (
            f'<div style="background:{BG_SECONDARY};padding:2rem;'
            f'text-align:center;color:{TEXT_SECONDARY};font-family:sans-serif">'
            "No stocks matched the screening criteria today.</div>"
        )

    rows = ""
    for s in stocks_data:
        ticker   = s.get("ticker", "")
        exchange = s.get("exchange", "NASDAQ")
        url      = f"https://www.google.com/finance/quote/{ticker}:{exchange}"

        change      = s.get("change", 0)
        change_color = SUCCESS if change >= 0 else DANGER
        sign        = "+" if change >= 0 else ""

        volume    = s.get("volume", 0)
        vol_ratio = s.get("vol_ratio", 1.0)
        vr_color  = SUCCESS if vol_ratio >= 2 else TEXT_SECONDARY

        rsi = s.get("rsi", 0.0)

        macd_hist = s.get("macd_hist", 0.0)
        macd_bull  = macd_hist >= 0
        macd_color = SUCCESS if macd_bull else DANGER
        macd_text  = "&#9650; Bull" if macd_bull else "&#9660; Bear"

        entry1, stop1 = s.get("entry1", 0.0), s.get("stop1", 0.0)
        entry2, stop2 = s.get("entry2", 0.0), s.get("stop2", 0.0)
        entry3, stop3 = s.get("entry3", 0.0), s.get("stop3", 0.0)
        risk1, risk2, risk3 = entry1 - stop1, entry2 - stop2, entry3 - stop3

        # ── Main summary row ──────────────────────────────────────────────────
        rows += (
            "<tr>"
            f'<td style="{TD};font-weight:700">'
            f'  <a href="{url}" style="color:{ACCENT};text-decoration:none">{ticker}</a></td>'
            f'<td style="{TD}">${s.get("price", 0):.2f}</td>'
            f'<td style="{TD};color:{change_color};font-weight:600">{sign}{change:.2f}%</td>'
            f'<td style="{TD}">{fmt_number(volume)}<br>'
            f'  <span style="font-size:11px;color:{vr_color}">{vol_ratio:.1f}&#215; avg</span></td>'
            f'<td style="{TD}">{fmt_number(s.get("market_cap", 0))}</td>'
            f'<td style="{TD};color:{rsi_color(rsi)};font-weight:700">{rsi:.1f}<br>'
            f'  <span style="font-size:10px;font-weight:400;text-transform:uppercase">'
            f'  {rsi_label(rsi)}</span></td>'
            f'<td style="{TD_LAST};color:{macd_color};font-weight:600">{macd_text}</td>'
            "</tr>"
        )

        # ── Detail row: MAs · BB/ATR · Swing levels ───────────────────────────
        rows += (
            f'<tr><td colspan="7" style="padding:10px 18px 16px;background:{BG_DETAIL_ROW};'
            f'border-bottom:2px solid {ACCENT}">'

            f'<div style="{SECTION_LABEL}">Moving Averages</div>'
            '<table style="border-collapse:separate;border-spacing:6px 0"><tr>'
            + _chip("EMA 8",   s.get("ema8",   0.0))
            + _chip("EMA 50",  s.get("ema50",  0.0))
            + _chip("EMA 200", s.get("ema200", 0.0))
            + _chip("SMA 50",  s.get("sma50",  0.0))
            + _chip("SMA 200", s.get("sma200", 0.0))
            + '</tr></table>'

            + f'<div style="{SECTION_LABEL}">Bollinger Bands (20, 2) &amp; ATR</div>'
            '<table style="border-collapse:separate;border-spacing:6px 0"><tr>'
            + _chip("BB Lower", s.get("bb_lower",  0.0))
            + _chip("BB Mid",   s.get("bb_middle", 0.0))
            + _chip("BB Upper", s.get("bb_upper",  0.0))
            + _chip("ATR 14",   s.get("atr14",     0.0))
            + '</tr></table>'

            + f'<div style="{SECTION_LABEL}">Swing Trade Levels</div>'
            '<table style="border-collapse:collapse;width:100%;max-width:480px">'
            '<thead><tr>'
            f'<th style="{LTH}">#</th>'
            f'<th style="{LTH}">Setup</th>'
            f'<th style="{LTH}">Entry</th>'
            f'<th style="{LTH}">Stop</th>'
            f'<th style="{LTH}">Risk / share</th>'
            '</tr></thead><tbody>'

            f'<tr>'
            f'<td style="{LTD};color:rgba(255,255,255,0.5);font-weight:700">1</td>'
            f'<td style="{LTD}">Breakout (now)</td>'
            f'<td style="{LTD};color:{SUCCESS}">${entry1:.2f}</td>'
            f'<td style="{LTD};color:{DANGER}">${stop1:.2f}</td>'
            f'<td style="{LTD};color:{TEXT_SECONDARY}">${risk1:.2f}</td>'
            '</tr>'

            f'<tr>'
            f'<td style="{LTD};color:{TEXT_SECONDARY};font-weight:700">2</td>'
            f'<td style="{LTD}">EMA 8 pullback</td>'
            f'<td style="{LTD};color:{SUCCESS}">${entry2:.2f}</td>'
            f'<td style="{LTD};color:{DANGER}">${stop2:.2f}</td>'
            f'<td style="{LTD};color:{TEXT_SECONDARY}">${risk2:.2f}</td>'
            '</tr>'

            f'<tr>'
            f'<td style="{LTD_LAST};color:{TEXT_SECONDARY};font-weight:700">3</td>'
            f'<td style="{LTD_LAST}">BB midline dip</td>'
            f'<td style="{LTD_LAST};color:{SUCCESS}">${entry3:.2f}</td>'
            f'<td style="{LTD_LAST};color:{DANGER}">${stop3:.2f}</td>'
            f'<td style="{LTD_LAST};color:{TEXT_SECONDARY}">${risk3:.2f}</td>'
            '</tr>'

            '</tbody></table>'
            '</td></tr>'
        )

    test_banner = (
        f'<div style="background:{BG_AMBER_BANNER};border:1px solid {AMBER};'
        f'padding:10px 16px;margin-bottom:18px;color:{AMBER};font-size:12px">'
        "&#9888; This is a test email. The data below is sample/dummy data used to verify "
        "email delivery — it does not reflect real market results."
        "</div>"
        if is_test else ""
    )

    return (
        f'<div style="background:{BG_PRIMARY};padding:28px;'
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif\">"
        '<div style="margin-bottom:20px">'
        f'<span style="color:{ACCENT};font-size:20px;font-weight:500">StockScreener Pro</span>'
        f'<span style="color:{TEXT_SECONDARY};font-size:13px;margin-left:12px">Daily Scan Results</span>'
        "</div>"
        f"{test_banner}"
        f'<div style="background:{BG_SECONDARY};overflow:hidden;border:1px solid {BORDER}">'
        '<table style="width:100%;border-collapse:collapse">'
        "<thead><tr>"
        f'<th style="{TH}">Ticker</th>'
        f'<th style="{TH}">Price</th>'
        f'<th style="{TH}">Change %</th>'
        f'<th style="{TH}">Volume</th>'
        f'<th style="{TH}">Market Cap</th>'
        f'<th style="{TH}">RSI</th>'
        f'<th style="{TH}">MACD</th>'
        f"</tr></thead><tbody>{rows}</tbody></table></div>"
        f'<p style="color:rgba(255,255,255,0.3);font-size:11px;margin-top:14px;margin-bottom:0">'
        "Scan run at market close · S&amp;P 500 universe</p>"
        "</div>"
    )


_DUMMY_STOCKS = [
    {
        "ticker": "AAPL", "exchange": "NASDAQ",
        "price": 213.45, "change": 3.82,
        "volume": 52_300_000, "vol_ratio": 2.3,
        "market_cap": 3_290_000_000_000,
        "rsi": 61.5, "macd": 1.23, "macd_signal": 0.98, "macd_hist": 0.25,
        "ema8": 208.12, "ema50": 195.40, "ema200": 178.30,
        "sma50": 194.80, "sma200": 185.60,
        "bb_upper": 225.10, "bb_middle": 210.50, "bb_lower": 195.90,
        "atr14": 4.25,
        "entry1": 213.45, "entry2": 208.12, "entry3": 210.50,
        "stop1": 209.20, "stop2": 205.99, "stop3": 192.67,
    },
    {
        "ticker": "NVDA", "exchange": "NASDAQ",
        "price": 875.20, "change": 5.14,
        "volume": 41_100_000, "vol_ratio": 3.1,
        "market_cap": 2_150_000_000_000,
        "rsi": 58.3, "macd": 8.47, "macd_signal": 6.92, "macd_hist": 1.55,
        "ema8": 851.33, "ema50": 790.10, "ema200": 650.80,
        "sma50": 788.20, "sma200": 620.48,
        "bb_upper": 910.40, "bb_middle": 858.70, "bb_lower": 807.00,
        "atr14": 18.90,
        "entry1": 875.20, "entry2": 851.33, "entry3": 858.70,
        "stop1": 856.30, "stop2": 841.88, "stop3": 778.75,
    },
    {
        "ticker": "MSFT", "exchange": "NASDAQ",
        "price": 421.80, "change": 3.21,
        "volume": 18_600_000, "vol_ratio": 1.8,
        "market_cap": 3_130_000_000_000,
        "rsi": 55.7, "macd": 2.10, "macd_signal": 1.75, "macd_hist": 0.35,
        "ema8": 413.55, "ema50": 398.20, "ema200": 365.40,
        "sma50": 397.10, "sma200": 380.10,
        "bb_upper": 435.60, "bb_middle": 415.30, "bb_lower": 395.00,
        "atr14": 7.80,
        "entry1": 421.80, "entry2": 413.55, "entry3": 415.30,
        "stop1": 413.80, "stop2": 409.65, "stop3": 393.20,
    },
    {
        "ticker": "XOM", "exchange": "NYSE",
        "price": 118.75, "change": 4.07,
        "volume": 14_200_000, "vol_ratio": 2.6,
        "market_cap": 470_000_000_000,
        "rsi": 63.1, "macd": 0.88, "macd_signal": 0.61, "macd_hist": 0.27,
        "ema8": 114.92, "ema50": 108.30, "ema200": 100.50,
        "sma50": 107.80, "sma200": 98.30,
        "bb_upper": 122.40, "bb_middle": 115.60, "bb_lower": 108.80,
        "atr14": 2.35,
        "entry1": 118.75, "entry2": 114.92, "entry3": 115.60,
        "stop1": 116.40, "stop2": 113.74, "stop3": 106.68,
    },
    {
        "ticker": "DXCM", "exchange": "NASDAQ",
        "price": 62.40, "change": 6.59,
        "volume": 9_800_000, "vol_ratio": 4.2,
        "market_cap": 23_000_000_000,
        "rsi": 57.8, "macd": 0.42, "macd_signal": 0.28, "macd_hist": 0.14,
        "ema8": 60.11, "ema50": 55.70, "ema200": 49.30,
        "sma50": 55.20, "sma200": 52.75,
        "bb_upper": 65.80, "bb_middle": 61.10, "bb_lower": 56.40,
        "atr14": 1.85,
        "entry1": 62.40, "entry2": 60.11, "entry3": 61.10,
        "stop1": 60.55, "stop2": 59.19, "stop3": 54.28,
    },
]


def send_scan_results_email(stocks_data: list, is_test: bool = False):
    """
    Formats scan results into an HTML table and sends via SMTP.
    Requires: EMAIL_SMTP_HOST, EMAIL_SMTP_PORT, EMAIL_USER, EMAIL_PASSWORD env vars
    and backend/recipients.txt (one address per line).
    Pass is_test=True to add a banner noting the data is for testing only.
    """
    smtp_host = os.getenv("EMAIL_SMTP_HOST")
    smtp_port = int(os.getenv("EMAIL_SMTP_PORT", 587))
    smtp_user = os.getenv("EMAIL_USER")
    smtp_pass = os.getenv("EMAIL_PASSWORD")
    recipients = _load_recipients()

    if not all([smtp_host, smtp_user, smtp_pass, recipients]):
        logger.error("Email configuration missing. Cannot send notification.")
        return

    if not stocks_data and not is_test:
        logger.info("No scan results — skipping email.")
        return

    html_content = (
        "<!DOCTYPE html><html><body "
        f'style="margin:0;padding:0;background-color:{BG_EMAIL_BODY}">'
        '<div style="max-width:1000px;margin:0 auto;padding:20px">'
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
