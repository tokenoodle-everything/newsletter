"""Compose the various notification emails."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .renderer import Post

_TEMPLATE_DIR = Path(__file__).parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _format_date(value: datetime | None) -> str:
    if not value:
        return ""
    return value.strftime("%B %d, %Y")


_env.filters["fmtdate"] = _format_date


def render(template_name: str, **ctx) -> str:
    return _env.get_template(template_name).render(**ctx)


def new_post_email(
    post: Post, post_url: str, unsubscribe_url: str, site_name: str
) -> tuple[str, str, str]:
    """Return (subject, html, text) for a new-post notification."""
    subject = f"[{site_name}] {post.title}"
    html = render(
        "email_new_post.html",
        post=post,
        post_url=post_url,
        unsubscribe_url=unsubscribe_url,
        site_name=site_name,
        date_str=_format_date(post.date),
    )
    text = (
        f"{post.title}\n"
        f"{_format_date(post.date)}\n\n"
        f"{post.summary or ''}\n\n"
        f"Read the full post: {post_url}\n\n"
        f"Unsubscribe: {unsubscribe_url}\n"
    )
    return subject, html, text


def confirm_email(confirm_url: str, site_name: str) -> tuple[str, str, str]:
    subject = f"[{site_name}] Confirm your subscription"
    html = render("email_confirm.html", confirm_url=confirm_url, site_name=site_name)
    text = (
        f"Thanks for subscribing to {site_name}.\n\n"
        f"Please confirm your email by visiting:\n{confirm_url}\n\n"
        f"If you did not request this, you can ignore this message.\n"
    )
    return subject, html, text
