"""Outgoing mail through an SMTP mailbox (username and password), when EMAIL_PROVIDER=smtp.

Microsoft Graph stays the way this service READS the inbox; this is only the way it sends.
smtplib blocks, so the exchange runs in a worker thread rather than holding the event loop -
and every other request on it - for up to the 30 s timeout.
"""
from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.message import EmailMessage

from ..config import settings
from ..core.logging import get_logger

log = get_logger(__name__)


def smtp_selected() -> bool:
    return (settings.email_provider or "auto").strip().lower() == "smtp"


def smtp_configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_user and settings.smtp_password)


async def send_via_smtp(*, to: str, subject: str, body: str, is_html: bool = False) -> None:
    if not smtp_configured():
        raise RuntimeError("EMAIL_PROVIDER=smtp but SMTP_HOST / SMTP_USER / SMTP_PASSWORD are not all set")
    user = settings.smtp_user.strip()
    sender = (settings.smtp_from or user).strip()
    recipients = [a.strip() for a in to.split(",") if a.strip()]
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    if is_html:
        msg.set_content("This message is best viewed in an email client that shows HTML.")
        msg.add_alternative(body, subtype="html")
    else:
        msg.set_content(body)
    use_ssl = bool(settings.smtp_use_ssl) or int(settings.smtp_port) == 465
    context = ssl.create_default_context()

    def _send() -> None:
        if use_ssl:
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=30, context=context) as smtp:
                smtp.login(user, settings.smtp_password)
                smtp.send_message(msg, to_addrs=recipients)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
                smtp.ehlo()
                if settings.smtp_use_tls:
                    smtp.starttls(context=context)
                    smtp.ehlo()
                smtp.login(user, settings.smtp_password)
                smtp.send_message(msg, to_addrs=recipients)

    await asyncio.to_thread(_send)
    log.info("smtp.send_email.sent", to=to, subject=subject[:60], from_addr=sender)
