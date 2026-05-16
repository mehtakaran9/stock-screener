import smtplib
import os
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import pandas as pd
import logging

logger = logging.getLogger("notifications")

def send_scan_results_email(stocks_data: list):
    """
    Formats scan results into an HTML table and sends via SMTP.
    Requires: EMAIL_SMTP_HOST, EMAIL_SMTP_PORT, EMAIL_USER, EMAIL_PASSWORD, EMAIL_TO
    """
    smtp_host = os.getenv("EMAIL_SMTP_HOST")
    smtp_port = int(os.getenv("EMAIL_SMTP_PORT", 587))
    smtp_user = os.getenv("EMAIL_USER")
    smtp_pass = os.getenv("EMAIL_PASSWORD")
    email_to = os.getenv("EMAIL_TO")

    if not all([smtp_host, smtp_user, smtp_pass, email_to]):
        logger.error("Email configuration missing. Cannot send notification.")
        return

    df = pd.DataFrame(stocks_data)
    
    # HTML Table generation
    html_content = f"""
    <html>
        <head>
            <style>
                table {{ border-collapse: collapse; width: 100%; }}
                th, td {{ padding: 8px; text-align: left; border-bottom: 1px solid #ddd; }}
                th {{ background-color: #f2f2f2; }}
            </style>
        </head>
        <body>
            <h2>Stock Scan Results</h2>
            {df.to_html(index=False, classes='table table-striped')}
        </body>
    </html>
    """

    msg = MIMEMultipart()
    msg['Subject'] = "Stock Screener Daily Scan Results"
    msg['From'] = smtp_user
    msg['To'] = email_to
    msg.attach(MIMEText(html_content, 'html'))

    for attempt in range(3):
        try:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)
                logger.info(f"Email sent successfully to {email_to}")
                return
        except Exception as e:
            if attempt < 2:
                wait = 30 * (attempt + 1)
                logger.warning(f"Email attempt {attempt + 1} failed: {e}. Retrying in {wait}s")
                time.sleep(wait)
            else:
                logger.error(f"All email attempts failed: {e}")
