"""SQLite-backed subscription store."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Optional

import aiosqlite

from .config import get_settings

log = logging.getLogger(__name__)


SCHEMA = """
CREATE TABLE IF NOT EXISTS subscribers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT    NOT NULL UNIQUE,
    confirmed       INTEGER NOT NULL DEFAULT 0,
    confirm_token   TEXT    NOT NULL,
    unsubscribe_token TEXT  NOT NULL,
    created_at      TEXT    NOT NULL,
    confirmed_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_subscribers_confirmed ON subscribers(confirmed);

CREATE TABLE IF NOT EXISTS deliveries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    post_slug    TEXT NOT NULL,
    subscriber_id INTEGER NOT NULL,
    status       TEXT NOT NULL,
    error        TEXT,
    sent_at      TEXT NOT NULL,
    FOREIGN KEY (subscriber_id) REFERENCES subscribers(id)
);
CREATE INDEX IF NOT EXISTS idx_deliveries_post ON deliveries(post_slug);

CREATE TABLE IF NOT EXISTS dispatched_posts (
    slug           TEXT PRIMARY KEY,
    content_hash   TEXT NOT NULL,
    sent_at        TEXT NOT NULL,
    recipients     INTEGER NOT NULL DEFAULT 0
);
"""


async def init_db() -> None:
    """Create the database file and tables if they don't exist."""
    settings = get_settings()
    db_path: Path = settings.db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as conn:
        await conn.executescript(SCHEMA)
        await conn.commit()
    log.info("Database initialised at %s", db_path)


@asynccontextmanager
async def get_conn() -> AsyncIterator[aiosqlite.Connection]:
    settings = get_settings()
    async with aiosqlite.connect(settings.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        await conn.commit()


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def add_subscriber(
    conn: aiosqlite.Connection, email: str, confirm_token: str, unsubscribe_token: str
) -> int:
    """Insert or refresh a subscriber; returns row id."""
    now = utcnow_iso()
    cur = await conn.execute(
        """
        INSERT INTO subscribers (email, confirmed, confirm_token, unsubscribe_token, created_at)
        VALUES (?, 0, ?, ?, ?)
        ON CONFLICT(email) DO UPDATE SET
            confirm_token = excluded.confirm_token,
            unsubscribe_token = excluded.unsubscribe_token,
            confirmed = 0,
            confirmed_at = NULL
        """,
        (email.lower(), confirm_token, unsubscribe_token, now),
    )
    await cur.execute("SELECT id FROM subscribers WHERE email = ?", (email.lower(),))
    row = await cur.fetchone()
    assert row is not None
    return int(row["id"])


async def get_subscriber_by_email(
    conn: aiosqlite.Connection, email: str
) -> Optional[aiosqlite.Row]:
    cur = await conn.execute(
        "SELECT * FROM subscribers WHERE email = ?", (email.lower(),)
    )
    return await cur.fetchone()


async def get_subscriber_by_id(
    conn: aiosqlite.Connection, sid: int
) -> Optional[aiosqlite.Row]:
    cur = await conn.execute("SELECT * FROM subscribers WHERE id = ?", (sid,))
    return await cur.fetchone()


async def confirm_subscriber(conn: aiosqlite.Connection, sid: int) -> bool:
    cur = await conn.execute(
        "UPDATE subscribers SET confirmed = 1, confirmed_at = ? "
        "WHERE id = ? AND confirmed = 0",
        (utcnow_iso(), sid),
    )
    return cur.rowcount > 0


async def unsubscribe(conn: aiosqlite.Connection, sid: int) -> bool:
    cur = await conn.execute(
        "UPDATE subscribers SET confirmed = 0 WHERE id = ?", (sid,)
    )
    return cur.rowcount > 0


async def list_confirmed(conn: aiosqlite.Connection) -> list[aiosqlite.Row]:
    cur = await conn.execute(
        "SELECT * FROM subscribers WHERE confirmed = 1 ORDER BY id"
    )
    return list(await cur.fetchall())


async def count_confirmed(conn: aiosqlite.Connection) -> int:
    cur = await conn.execute(
        "SELECT COUNT(*) AS n FROM subscribers WHERE confirmed = 1"
    )
    row = await cur.fetchone()
    return int(row["n"]) if row else 0


async def record_delivery(
    conn: aiosqlite.Connection,
    post_slug: str,
    subscriber_id: int,
    status: str,
    error: Optional[str] = None,
) -> None:
    await conn.execute(
        "INSERT INTO deliveries (post_slug, subscriber_id, status, error, sent_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (post_slug, subscriber_id, status, error, utcnow_iso()),
    )


async def delete_subscriber(conn: aiosqlite.Connection, sid: int) -> bool:
    """Hard-delete a subscriber and all their delivery records."""
    await conn.execute("DELETE FROM deliveries WHERE subscriber_id = ?", (sid,))
    cur = await conn.execute("DELETE FROM subscribers WHERE id = ?", (sid,))
    return cur.rowcount > 0


async def search_subscribers(
    conn: aiosqlite.Connection,
    q: Optional[str] = None,
    confirmed: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list, int]:
    """Search subscribers by email substring and/or confirmation state.

    Returns ``(rows, total_matching)``.
    """
    where = []
    params: list = []
    if q:
        where.append("email LIKE ?")
        params.append(f"%{q.lower()}%")
    if confirmed is not None:
        where.append("confirmed = ?")
        params.append(1 if confirmed else 0)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    cur = await conn.execute(
        f"SELECT COUNT(*) AS n FROM subscribers {where_sql}", params
    )
    row = await cur.fetchone()
    total = int(row["n"]) if row else 0

    cur = await conn.execute(
        f"""
        SELECT id, email, confirmed, confirm_token, unsubscribe_token,
               created_at, confirmed_at
        FROM subscribers
        {where_sql}
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, limit, offset],
    )
    return list(await cur.fetchall()), total


async def get_dispatched(conn: aiosqlite.Connection, slug: str) -> Optional[aiosqlite.Row]:
    cur = await conn.execute(
        "SELECT * FROM dispatched_posts WHERE slug = ?", (slug,)
    )
    return await cur.fetchone()


async def record_dispatched(
    conn: aiosqlite.Connection,
    slug: str,
    content_hash: str,
    recipients: int,
) -> None:
    await conn.execute(
        """
        INSERT INTO dispatched_posts (slug, content_hash, sent_at, recipients)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(slug) DO UPDATE SET
            content_hash = excluded.content_hash,
            sent_at = excluded.sent_at,
            recipients = excluded.recipients
        """,
        (slug, content_hash, utcnow_iso(), recipients),
    )
