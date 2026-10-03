"""Email sender supporting SMTP (e.g. ImprovMX) and generic HTTP APIs."""

from __future__ import annotations

import asyncio
import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from typing import Optional

import httpx

from .config import Settings, get_settings

log = logging.getLogger(__name__)


@dataclass
class Mail:
    to: str
    subject: str
    html: str
    text: str
    from_addr: Optional[str] = None
    from_name: Optional[str] = None


class MailError(RuntimeError):
    pass


def _format_from(settings: Settings) -> str:
    addr = settings.mail_from
    name = settings.mail_from_name
    if name and name not in addr:
        return formataddr((name, addr))
    return addr


def _build_message(settings: Settings, mail: Mail) -> EmailMessage:
    msg = EmailMessage()
    from_header = (
        formataddr((mail.from_name, mail.from_addr))
        if mail.from_addr and mail.from_name
        else (
            formataddr((mail.from_name, mail.from_addr))
            if mail.from_name
            else _format_from(settings)
        )
    )
    # If both overrides are empty, fall back to configured values
    if not (mail.from_addr or mail.from_name):
        from_header = _format_from(settings)
    msg["From"] = from_header
    msg["To"] = mail.to
    msg["Subject"] = mail.subject
    # Encourage clients to show the HTML version
    msg.set_content(mail.text)
    msg.add_alternative(mail.html, subtype="html")
    # Standard compliance headers
    msg["List-Unsubscribe"] = f"<mailto:{parseaddr(from_header)[1]}>"
    return msg


async def send_mail(mail: Mail, settings: Optional[Settings] = None) -> None:
    settings = settings or get_settings()
    if settings.mail_backend == "smtp":
        await _send_smtp(settings, mail)
    elif settings.mail_backend == "http":
        await _send_http(settings, mail)
    else:
        raise MailError(f"Unknown MAIL_BACKEND: {settings.mail_backend}")


async def _send_smtp(settings: Settings, mail: Mail) -> None:
    msg = _build_message(settings, mail)

    def _do_send() -> None:
        if settings.smtp_use_tls and settings.smtp_port == 465:
            client = smtplib.SMTP_SSL(
                settings.smtp_host, settings.smtp_port, timeout=30
            )
        else:
            client = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
        try:
            client.ehlo()
            if settings.smtp_use_starttls and not (
                settings.smtp_use_tls and settings.smtp_port == 465
            ):
                client.starttls()
                client.ehlo()
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(msg)
        finally:
            try:
                client.quit()
            except Exception:
                client.close()

    try:
        await asyncio.to_thread(_do_send)
    except Exception as exc:
        raise MailError(f"SMTP send failed: {exc}") from exc


async def _send_http(settings: Settings, mail: Mail) -> None:
    if not settings.mail_http_url:
        raise MailError("MAIL_HTTP_URL is required when MAIL_BACKEND=http")
    from_header = _format_from(settings)
    # Mailgun-style form post. Override MAIL_HTTP_HEADER/SCHEME for other APIs.
    payload = {
        "from": from_header,
        "to": mail.to,
        "subject": mail.subject,
        "html": mail.html,
        "text": mail.text,
    }
    headers = {}
    if settings.mail_http_token:
        scheme = settings.mail_http_scheme or "Bearer"
        token = settings.mail_http_token
        if scheme.lower() == "bearer" and " " not in token:
            headers[settings.mail_http_header] = f"Bearer {token}"
        else:
            headers[settings.mail_http_header] = (
                f"{scheme} {token}" if scheme else token
            )
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                settings.mail_http_url, data=payload, headers=headers
            )
        if resp.status_code >= 300:
            raise MailError(f"HTTP mail API {resp.status_code}: {resp.text[:500]}")
    except MailError:
        raise
    except Exception as exc:
        raise MailError(f"HTTP mail send failed: {exc}") from exc
