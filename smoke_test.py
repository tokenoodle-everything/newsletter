"""End-to-end smoke test: sync -> render -> subscribe -> confirm -> broadcast.

Run with:  python smoke_test.py

Uses an in-process mailer stub so no real emails are sent.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import os

os.chdir(ROOT)

# Reset DB (content/ is the source of truth; never delete it)
data_dir = ROOT / "data"
if data_dir.exists():
    shutil.rmtree(data_dir)

# Stub the mailer so nothing leaves the box
from newsletter import mailer

captured: list[mailer.Mail] = []


async def fake_send(m: mailer.Mail, settings=None):
    captured.append(m)


mailer.send_mail = fake_send  # type: ignore[assignment]


async def main() -> int:
    from newsletter.config import get_settings
    from newsletter import database as db
    from newsletter.git_sync import sync_content
    from newsletter.renderer import discover_posts, index_by_slug
    from newsletter.tokens import make_confirm_token, make_unsubscribe_token

    settings = get_settings()

    # 1. Initialise DB and sync content (now a no-op for local source).
    await db.init_db()
    source = await sync_content()
    posts = discover_posts(source)
    assert posts, "Expected at least one post after sync"
    print(f"Discovered posts: {[p.slug for p in posts]}")

    # 2. Subscribe
    async with db.get_conn() as conn:
        sid = await db.add_subscriber(
            conn,
            email="alice@example.com",
            confirm_token="placeholder",
            unsubscribe_token="placeholder",
        )
        await conn.execute(
            "UPDATE subscribers SET confirm_token=?, unsubscribe_token=? WHERE id=?",
            (make_confirm_token(sid), make_unsubscribe_token(sid), sid),
        )

    # 3. Confirm
    async with db.get_conn() as conn:
        ok = await db.confirm_subscriber(conn, sid)
    assert ok
    async with db.get_conn() as conn:
        n = await db.count_confirmed(conn)
    print(f"Confirmed subscribers: {n}")
    assert n == 1

    # 4. Broadcast the welcome post
    from newsletter.main import _broadcast_new_post

    post = index_by_slug(posts)["welcome"]
    sent, failed = await _broadcast_new_post(post)
    print(f"Broadcast result: sent={sent}, failed={failed}")
    assert sent == 1 and failed == 0
    assert len(captured) == 1
    body = captured[0].html
    assert "Welcome" in body
    assert "Unsubscribe" in body
    assert "alice@example.com" not in body  # recipient never echoed in body

    # 5. Unsubscribe
    async with db.get_conn() as conn:
        removed = await db.unsubscribe(conn, sid)
    assert removed
    async with db.get_conn() as conn:
        n = await db.count_confirmed(conn)
    assert n == 0

    print("Smoke test OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
