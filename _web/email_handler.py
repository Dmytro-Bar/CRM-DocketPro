"""
Email sending module for DocketPro CRM.
Uses stdlib smtplib — no extra dependencies required.
Supports STARTTLS (port 587, Gmail / most providers)
and SSL (port 465, ukr.net / meta.ua).
"""

import asyncio
import os
import smtplib
import ssl
from email import encoders
from email.header import Header
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional
from urllib.parse import quote as urlquote

from config import (
    SMTP_HOST, SMTP_PORT,
    SMTP_USER, SMTP_PASSWORD,
    EMAIL_FROM, EMAIL_FROM_NAME,
)


# ── Core sender ───────────────────────────────────────────────────

def _send_sync(
    to_email: "str | list",
    subject: str,
    body_html: str,
    attachment_path: Optional[str] = None,
    attachment_name: Optional[str] = None,
) -> None:
    """Blocking SMTP send. Runs in thread pool — do not call from async context directly.
    to_email can be a single address string or a list of address strings."""
    recipients = [to_email] if isinstance(to_email, str) else list(to_email)
    recipients = [e.strip() for e in recipients if e and str(e).strip()]
    if not recipients:
        return

    msg = MIMEMultipart("mixed")
    msg["From"]    = f"{EMAIL_FROM_NAME} <{EMAIL_FROM}>"
    msg["To"]      = ", ".join(recipients)
    msg["Subject"] = Header(subject, "utf-8").encode()

    msg.attach(MIMEText(body_html, "html", "utf-8"))

    if attachment_path and os.path.exists(attachment_path):
        with open(attachment_path, "rb") as fh:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(fh.read())
        encoders.encode_base64(part)
        fname = attachment_name or os.path.basename(attachment_path)
        part.add_header(
            "Content-Disposition",
            f"attachment; filename*=UTF-8''{urlquote(fname)}"
        )
        msg.attach(part)

    port = int(SMTP_PORT or 587)
    ctx  = ssl.create_default_context()

    if port == 465:
        # SSL from the start (ukr.net, meta.ua)
        with smtplib.SMTP_SSL(SMTP_HOST, port, context=ctx) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(EMAIL_FROM, recipients, msg.as_bytes())
    else:
        # STARTTLS (Gmail port 587, most other providers)
        with smtplib.SMTP(SMTP_HOST, port) as server:
            server.ehlo()
            server.starttls(context=ctx)
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(EMAIL_FROM, recipients, msg.as_bytes())


async def send_email(
    to_email: "str | list",
    subject: str,
    body_html: str,
    attachment_path: Optional[str] = None,
    attachment_name: Optional[str] = None,
) -> None:
    """Async wrapper — runs SMTP in thread pool, does not block the event loop.
    to_email can be a single address string or a list of address strings."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        lambda: _send_sync(to_email, subject, body_html, attachment_path, attachment_name),
    )


def email_configured() -> bool:
    """Return True if all required SMTP settings are present."""
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD and EMAIL_FROM)


# ── HTML body templates ───────────────────────────────────────────

def _wrap(content: str) -> str:
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;'
        'line-height:1.6;color:#333;max-width:600px;margin:0 auto">'
        + content
        + "</div>"
    )


def body_invoice(
    invoice_no: str,
    client_name: str,
    sum_str: str,
    due_date: str,
    our_name: str,
) -> str:
    return _wrap(f"""
<p>Шановні колеги,</p>
<p>
  Надсилаємо рахунок <strong>{invoice_no}</strong>
  для <strong>{client_name}</strong>
  на суму <strong>{sum_str}&nbsp;грн</strong>.
</p>
<p>Термін оплати: <strong>{due_date}</strong>.</p>
<p>PDF рахунку у вкладенні.</p>
<br>
<p>З повагою,<br><strong>{our_name}</strong></p>
""")


def body_act(
    act_no: str,
    client_name: str,
    sum_str: str,
    our_name: str,
) -> str:
    return _wrap(f"""
<p>Шановні колеги,</p>
<p>
  Надсилаємо акт виконаних робіт <strong>{act_no}</strong>
  для <strong>{client_name}</strong>
  на суму <strong>{sum_str}&nbsp;грн</strong>.
</p>
<p>Просимо підписати акт та надіслати нам один підписаний примірник.</p>
<p>PDF акту у вкладенні.</p>
<br>
<p>З повагою,<br><strong>{our_name}</strong></p>
""")


def body_reminder(
    invoice_no: str,
    client_name: str,
    sum_str: str,
    due_line: str,
    our_name: str,
) -> str:
    return _wrap(f"""
<p>Шановні колеги,</p>
<p>
  Нагадуємо про несплачений рахунок <strong>{invoice_no}</strong>
  для <strong>{client_name}</strong>
  на суму <strong>{sum_str}&nbsp;грн</strong>.
</p>
<p>{due_line}</p>
<p>Лист-нагадування у вкладенні.</p>
<br>
<p>З повагою,<br><strong>{our_name}</strong></p>
""")
