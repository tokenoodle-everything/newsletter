"""Admin backend: token-protected API and a tiny HTML dashboard."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import hmac as _hmac
from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import PROJECT_ROOT, get_settings
from .database import (
    count_confirmed,
    delete_subscriber,
    get_conn,
    get_subscriber_by_id,
    search_subscribers,
)
from .git_sync import sync_content
from .renderer import discover_posts, index_by_slug

log = logging.getLogger(__name__)

_COOKIE_NAME = "newsletter_admin"
_COOKIE_MAX_AGE = 60 * 60 * 12  # 12 hours


def _session_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().token_secret, salt="newsletter-admin")


def _make_session_cookie() -> str:
    return _session_serializer().dumps({"role": "admin"})


def _load_session_cookie(value: str) -> bool:
    try:
        data = _session_serializer().loads(value, max_age=_COOKIE_MAX_AGE)
        return isinstance(data, dict) and data.get("role") == "admin"
    except (BadSignature, SignatureExpired):
        return False


def _safe_eq(a: str, b: str) -> bool:
    return _hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _require_admin(
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    cookie_value: Optional[str] = Cookie(default=None, alias=_COOKIE_NAME),
) -> str:
    """Return the auth source ('token' or 'cookie') or raise 401."""
    settings = get_settings()
    expected = settings.admin_token

    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ADMIN_TOKEN is not configured on the server.",
        )

    if x_admin_token and _safe_eq(x_admin_token, expected):
        return "token"
    if cookie_value and _load_session_cookie(cookie_value):
        return "cookie"

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


api_router = APIRouter(prefix="/admin/api", dependencies=[Depends(_require_admin)])
web_router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "newsletter" / "templates"))


# ---------------------------------------------------------------------------
# HTML dashboard (cookie session)
# ---------------------------------------------------------------------------


@web_router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: Optional[str] = None):
    return templates.TemplateResponse(
        request,
        "admin_login.html",
        {"site_name": "Admin", "error": error},
    )


@web_router.post("/login")
async def login_submit(request: Request, token: str = Form("")):
    settings = get_settings()
    if not settings.admin_token:
        return templates.TemplateResponse(
            request,
            "admin_login.html",
            {"site_name": "Admin", "error": "ADMIN_TOKEN is not set on the server."},
            status_code=503,
        )
    if not _safe_eq(token, settings.admin_token):
        return templates.TemplateResponse(
            request,
            "admin_login.html",
            {"site_name": "Admin", "error": "Invalid token."},
            status_code=401,
        )
    response = Response(status_code=303, headers={"Location": "/admin"})
    response.set_cookie(
        _COOKIE_NAME,
        _make_session_cookie(),
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return response


@web_router.post("/logout")
async def logout():
    response = Response(status_code=303, headers={"Location": "/admin/login"})
    response.delete_cookie(_COOKIE_NAME)
    return response


@web_router.get("", response_class=HTMLResponse)
@web_router.get("/", response_class=HTMLResponse)
async def admin_home(
    request: Request,
    _auth: str = Depends(_require_admin),
):
    return templates.TemplateResponse(
        request, "admin_dashboard.html", {"site_name": "Admin"}
    )


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------


@api_router.get("/stats")
async def stats():
    async with get_conn() as conn:
        total = await _count_all(conn, "subscribers")
        confirmed = await count_confirmed(conn)
        pending = await _count_where(conn, "subscribers", "confirmed = 0")
        deliveries_total = await _count_all(conn, "deliveries")
        deliveries_sent = await _count_where(conn, "deliveries", "status = 'sent'")
        deliveries_failed = await _count_where(conn, "deliveries", "status = 'failed'")
        today_start = (
            datetime.now(timezone.utc)
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .isoformat(timespec="seconds")
        )
        deliveries_today = await _count_where(
            conn, "deliveries", f"sent_at >= '{today_start}'"
        )
    settings = get_settings()
    posts = discover_posts(settings.content_path)
    return {
        "subscribers": {
            "total": total,
            "confirmed": confirmed,
            "pending": pending,
        },
        "deliveries": {
            "total": deliveries_total,
            "sent": deliveries_sent,
            "failed": deliveries_failed,
            "today": deliveries_today,
        },
        "content": {
            "posts": len(posts),
            "content_dir": str(settings.content_path),
            "git_repo_url": settings.git_repo_url,
        },
        "server": {
            "now": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    }


@api_router.get("/subscribers")
async def list_subscribers(
    q: Optional[str] = Query(default=None, description="Search by email substring"),
    confirmed: Optional[bool] = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    async with get_conn() as conn:
        rows, total = await search_subscribers(
            conn, q=q, confirmed=confirmed, limit=limit, offset=offset
        )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": r["id"],
                "email": r["email"],
                "confirmed": bool(r["confirmed"]),
                "created_at": r["created_at"],
                "confirmed_at": r["confirmed_at"],
            }
            for r in rows
        ],
    }


@api_router.post("/subscribers/{sid}/confirm")
async def confirm_subscriber_admin(sid: int):
    async with get_conn() as conn:
        sub = await get_subscriber_by_id(conn, sid)
        if not sub:
            raise HTTPException(status_code=404, detail="Subscriber not found")
        await _confirm_in_conn(conn, sid)
    return {"ok": True, "id": sid, "confirmed": True}


@api_router.delete("/subscribers/{sid}")
async def delete_subscriber_admin(sid: int):
    async with get_conn() as conn:
        ok = await delete_subscriber(conn, sid)
    if not ok:
        raise HTTPException(status_code=404, detail="Subscriber not found")
    return {"ok": True, "id": sid, "deleted": True}


@api_router.get("/deliveries")
async def list_deliveries(
    post_slug: Optional[str] = None,
    status_filter: Optional[str] = Query(default=None, alias="status"),
    since_hours: Optional[int] = Query(default=None, ge=0, le=8760),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    where = []
    params: list = []
    if post_slug:
        where.append("post_slug = ?")
        params.append(post_slug)
    if status_filter:
        where.append("status = ?")
        params.append(status_filter)
    if since_hours is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat(
            timespec="seconds"
        )
        where.append("sent_at >= ?")
        params.append(cutoff)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    async with get_conn() as conn:
        cur = await conn.execute(
            f"SELECT COUNT(*) AS n FROM deliveries {where_sql}", params
        )
        total_row = await cur.fetchone()
        total = int(total_row["n"]) if total_row else 0
        cur = await conn.execute(
            f"""
            SELECT d.id, d.post_slug, d.subscriber_id, s.email,
                   d.status, d.error, d.sent_at
            FROM deliveries d
            LEFT JOIN subscribers s ON s.id = d.subscriber_id
            {where_sql}
            ORDER BY d.id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        )
        rows = list(await cur.fetchall())

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": r["id"],
                "post_slug": r["post_slug"],
                "subscriber_id": r["subscriber_id"],
                "email": r["email"],
                "status": r["status"],
                "error": r["error"],
                "sent_at": r["sent_at"],
            }
            for r in rows
        ],
    }


@api_router.get("/posts")
async def list_posts():
    settings = get_settings()
    posts = discover_posts(settings.content_path)
    items = []
    for p in posts:
        mtime = None
        try:
            if p.source_path and p.source_path.exists():
                mtime = datetime.fromtimestamp(p.source_path.stat().st_mtime).isoformat(
                    timespec="seconds"
                )
        except OSError:
            pass
        items.append(
            {
                "slug": p.slug,
                "title": p.title,
                "date": p.date.isoformat() if p.date else None,
                "summary": p.summary,
                "author": p.author,
                "modified_at": mtime,
                "url": f"{settings.public_base_url.rstrip('/')}/posts/{p.slug}",
            }
        )
    return {"total": len(items), "items": items}


@api_router.get("/posts/{slug}/raw", response_class=PlainTextResponse)
async def post_raw(slug: str):
    settings = get_settings()
    posts = index_by_slug(discover_posts(settings.content_path))
    post = posts.get(slug)
    if not post or not post.source_path:
        raise HTTPException(status_code=404, detail="Post not found")
    return PlainTextResponse(post.source_path.read_text(encoding="utf-8"))


@api_router.post("/sync")
async def trigger_sync():
    settings = get_settings()
    source = await sync_content(settings)
    posts = discover_posts(source)
    return {
        "ok": True,
        "synced_into": str(source),
        "posts_after": len(posts),
        "slugs": [p.slug for p in posts],
    }


@api_router.post("/posts/{slug}/rebroadcast")
async def rebroadcast_post(slug: str):
    """Forget a post's dispatched-hash record so the next webhook re-sends it."""
    async with get_conn() as conn:
        cur = await conn.execute(
            "DELETE FROM dispatched_posts WHERE slug = ?", (slug,)
        )
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="No dispatched record for this slug")
    return {"ok": True, "slug": slug, "cleared": True}


@api_router.post("/posts/rebroadcast-all")
async def rebroadcast_all():
    """Clear every dispatched-hash record so the next webhook re-sends everything."""
    async with get_conn() as conn:
        cur = await conn.execute("DELETE FROM dispatched_posts")
    return {"ok": True, "cleared": cur.rowcount}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _count_all(conn, table: str) -> int:
    cur = await conn.execute(f"SELECT COUNT(*) AS n FROM {table}")
    row = await cur.fetchone()
    return int(row["n"]) if row else 0


async def _count_where(conn, table: str, where: str) -> int:
    cur = await conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}")
    row = await cur.fetchone()
    return int(row["n"]) if row else 0


async def _confirm_in_conn(conn, sid: int) -> None:
    from .database import confirm_subscriber

    await confirm_subscriber(conn, sid)
