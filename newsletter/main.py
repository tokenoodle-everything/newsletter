"""FastAPI entrypoint for the newsletter service."""

from __future__ import annotations

import hashlib
import hmac
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired

from .config import PROJECT_ROOT, get_settings
from .admin import api_router as admin_api_router, web_router as admin_web_router
from .database import (
    add_subscriber,
    confirm_subscriber,
    count_confirmed,
    get_conn,
    get_dispatched,
    get_subscriber_by_email,
    get_subscriber_by_id,
    init_db,
    list_confirmed,
    record_delivery,
    record_dispatched,
    unsubscribe,
)
from .emails import confirm_email, new_post_email
from .git_sync import sync_content
from .mailer import Mail, MailError, send_mail
from .models import SubscribeRequest, SubscribeResponse
from .renderer import Post, discover_posts, index_by_slug
from .tokens import load_token, make_confirm_token, make_unsubscribe_token

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("newsletter")


SITE_NAME = "Newsletter"
TAGLINE = "News and announcements, delivered straight from a Git repository."


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    await init_db()
    try:
        await sync_content(settings)
    except Exception as exc:
        log.warning("Initial content sync failed: %s", exc)
    yield


app = FastAPI(title=SITE_NAME, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "static")), name="static")
app.include_router(admin_web_router)
app.include_router(admin_api_router)
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "newsletter" / "templates"))
templates.env.filters.setdefault(
    "fmtdate",
    lambda d: d.strftime("%B %d, %Y") if isinstance(d, datetime) else "",
)


def _public_url(path: str) -> str:
    base = get_settings().public_base_url.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def _render(request: Request, template: str, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(
        request, template, {"site_name": SITE_NAME, **ctx}
    )


async def _load_post(slug: str) -> Optional[Post]:
    settings = get_settings()
    posts = discover_posts(settings.content_path)
    return index_by_slug(posts).get(slug)


async def _broadcast_new_post(post: Post) -> tuple[int, int]:
    """Email a new-post notification to every confirmed subscriber.

    Returns ``(sent, failed)``.
    """
    settings = get_settings()
    sent = failed = 0
    async with get_conn() as conn:
        subscribers = await list_confirmed(conn)
    log.info("Broadcasting '%s' to %d subscribers", post.slug, len(subscribers))
    for sub in subscribers:
        unsub_token = make_unsubscribe_token(int(sub["id"]))
        unsub_url = _public_url(f"/unsubscribe?token={unsub_token}")
        post_url = _public_url(f"/posts/{post.slug}")
        subject, html, text = new_post_email(post, post_url, unsub_url, SITE_NAME)
        delivery_status = "failed"
        delivery_error: Optional[str] = None
        try:
            await send_mail(
                Mail(to=sub["email"], subject=subject, html=html, text=text),
                settings=settings,
            )
            delivery_status = "sent"
            sent += 1
        except MailError as exc:
            failed += 1
            delivery_error = str(exc)
            log.warning("Failed to send to %s: %s", sub["email"], delivery_error)
        async with get_conn() as conn2:
            await record_delivery(
                conn2,
                post.slug,
                int(sub["id"]),
                status=delivery_status,
                error=delivery_error,
            )
    return sent, failed


# ---------------------------------------------------------------------------
# Web pages
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    settings = get_settings()
    posts = discover_posts(settings.content_path)
    return _render(
        request,
        "index.html",
        posts=posts[:10],
        tagline=TAGLINE,
    )


@app.get("/archive", response_class=HTMLResponse)
async def archive(request: Request):
    settings = get_settings()
    posts = discover_posts(settings.content_path)
    return _render(request, "archive.html", posts=posts)


@app.get("/posts/{slug}", response_class=HTMLResponse)
async def post_page(request: Request, slug: str):
    post = await _load_post(slug)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return _render(request, "post.html", post=post)


# ---------------------------------------------------------------------------
# Subscribe / confirm / unsubscribe
# ---------------------------------------------------------------------------


async def _create_or_refresh_subscriber(email: str) -> int:
    """Insert a new row or refresh tokens for an existing one. Returns the id."""
    async with get_conn() as conn:
        existing = await get_subscriber_by_email(conn, email)
        if existing is None:
            sid = await add_subscriber(
                conn,
                email=email,
                confirm_token=make_confirm_token(0),
                unsubscribe_token=make_unsubscribe_token(0),
            )
        else:
            sid = int(existing["id"])
        await conn.execute(
            "UPDATE subscribers SET confirm_token=?, unsubscribe_token=? WHERE id=?",
            (make_confirm_token(sid), make_unsubscribe_token(sid), sid),
        )
    return sid


@app.post("/subscribe")
async def subscribe(request: Request, email: str = Form(...)):
    email = email.strip()
    if "@" not in email or len(email) > 254:
        return _render(
            request,
            "message.html",
            title="Invalid email",
            message="That doesn't look like a valid email address.",
        )

    settings = get_settings()
    sid = await _create_or_refresh_subscriber(email)
    confirm_url = _public_url(f"/confirm?token={make_confirm_token(sid)}")
    subject, html, text = confirm_email(confirm_url, SITE_NAME)
    try:
        await send_mail(
            Mail(to=email, subject=subject, html=html, text=text),
            settings=settings,
        )
    except MailError as exc:
        log.error("Failed to send confirmation: %s", exc)
        return _render(
            request,
            "message.html",
            title="Subscription error",
            message="We couldn't send the confirmation email right now. Please try again later.",
        )

    return _render(
        request,
        "message.html",
        title="Check your inbox",
        message=(
            f"We've sent a confirmation link to <strong>{email}</strong>. "
            "Click it to finish subscribing."
        ),
    )


@app.get("/confirm")
async def confirm(request: Request, token: str):
    try:
        purpose, sid = load_token(token)
    except SignatureExpired:
        return _render(
            request,
            "message.html",
            title="Link expired",
            message="This confirmation link has expired. Please subscribe again.",
        )
    except BadSignature:
        raise HTTPException(status_code=400, detail="Invalid token")
    if purpose != "confirm":
        raise HTTPException(status_code=400, detail="Wrong token type")
    async with get_conn() as conn:
        ok = await confirm_subscriber(conn, sid)
    return _render(
        request,
        "message.html",
        title="Subscribed" if ok else "Already subscribed",
        message=(
            "You're now subscribed — look out for the next post in your inbox."
            if ok
            else "This address was already confirmed. Nothing to do."
        ),
    )


@app.get("/unsubscribe")
async def unsubscribe_get(request: Request, token: str):
    return await _do_unsubscribe(request, token)


@app.post("/unsubscribe")
async def unsubscribe_post(request: Request, token: str = Form(...)):
    return await _do_unsubscribe(request, token)


async def _do_unsubscribe(request: Request, token: str) -> Response:
    try:
        purpose, sid = load_token(token)
    except (SignatureExpired, BadSignature):
        return _render(
            request,
            "message.html",
            title="Invalid link",
            message="That unsubscribe link is invalid or has expired.",
        )
    if purpose != "unsub":
        raise HTTPException(status_code=400, detail="Wrong token type")
    async with get_conn() as conn:
        sub = await get_subscriber_by_id(conn, sid)
        await unsubscribe(conn, sid)
    email = sub["email"] if sub else "your address"
    return _render(
        request,
        "message.html",
        title="Unsubscribed",
        message=f"<strong>{email}</strong> has been removed from the list. Sorry to see you go.",
    )


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------


@app.post("/api/subscribe", response_model=SubscribeResponse)
async def api_subscribe(payload: SubscribeRequest):
    settings = get_settings()
    sid = await _create_or_refresh_subscriber(str(payload.email))
    confirm_url = _public_url(f"/confirm?token={make_confirm_token(sid)}")
    subject, html, text = confirm_email(confirm_url, SITE_NAME)
    try:
        await send_mail(
            Mail(to=str(payload.email), subject=subject, html=html, text=text),
            settings=settings,
        )
    except MailError as exc:
        raise HTTPException(status_code=502, detail=f"Mail send failed: {exc}")
    return SubscribeResponse()


@app.get("/api/posts")
async def api_posts():
    settings = get_settings()
    posts = discover_posts(settings.content_path)
    return {
        "posts": [
            {
                "slug": p.slug,
                "title": p.title,
                "date": p.date.isoformat() if p.date else None,
                "summary": p.summary,
                "author": p.author,
                "url": _public_url(p.path),
            }
            for p in posts
        ]
    }


@app.get("/api/stats")
async def api_stats():
    async with get_conn() as conn:
        return {"confirmed_subscribers": await count_confirmed(conn)}


# ---------------------------------------------------------------------------
# GitHub webhook
# ---------------------------------------------------------------------------


def _verify_signature(secret: str, body: bytes, signature_header):
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = (
        "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    )
    return hmac.compare_digest(expected, signature_header)


@app.post("/webhook/github")
async def github_webhook(request: Request):
    settings = get_settings()
    body = await request.body()
    sig = request.headers.get("X-Hub-Signature-256")
    if not _verify_signature(settings.github_webhook_secret, body, sig):
        raise HTTPException(status_code=401, detail="Invalid signature")

    event = request.headers.get("X-GitHub-Event", "")
    if event not in {"push", "workflow_run", "ping"}:
        return {"ok": True, "skipped": f"event {event}"}
    if event == "ping":
        return {"ok": True, "pong": True}

    await sync_content(settings)
    posts = discover_posts(settings.content_path)
    log.info("After sync, %d posts in %s", len(posts), settings.content_path)

    force = settings.force_rebroadcast
    results = []
    for post in posts:
        digest = _post_content_hash(post)
        async with get_conn() as conn:
            prev = await get_dispatched(conn, post.slug)
        if not force and prev is not None and prev["content_hash"] == digest:
            log.info(
                "Skip '%s' — already dispatched (hash %s) at %s",
                post.slug, digest[:8], prev["sent_at"],
            )
            continue
        log.info(
            "Dispatching '%s' (hash %s, force=%s, had_prev=%s)",
            post.slug, digest[:8], force, prev is not None,
        )
        sent, failed = await _broadcast_new_post(post)
        async with get_conn() as conn:
            await record_dispatched(conn, post.slug, digest, recipients=sent)
        results.append({
            "slug": post.slug,
            "sent": sent,
            "failed": failed,
            "recipients": sent,
            "forced": force,
        })

    return {"ok": True, "delivered": results}


def _post_content_hash(post: Post) -> str:
    """Stable hash of the post's meaningful content (frontmatter + body)."""
    h = hashlib.md5()
    h.update((post.title or "").encode("utf-8"))
    h.update(b"\x00")
    h.update(post.raw_markdown.encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


@app.get("/healthz", response_class=PlainTextResponse)
async def healthz():
    return "ok"
