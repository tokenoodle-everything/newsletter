"""Smoke-test the subscribe -> confirm -> unsubscribe HTML flow.

The mailer is stubbed so no real emails are sent.
"""

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from newsletter import mailer

captured: list = []


async def fake_send(m, settings=None):
    captured.append(m)


mailer.send_mail = fake_send

from newsletter import database as db
from newsletter.main import app
from newsletter.tokens import make_confirm_token, make_unsubscribe_token
from fastapi.testclient import TestClient


async def reset_db():
    p = ROOT / "data" / "newsletter.db"
    if p.exists():
        p.unlink()
    await db.init_db()


async def main() -> int:
    await reset_db()
    client = TestClient(app)

    # Subscribe via the form endpoint (mailer is stubbed)
    r = client.post(
        "/subscribe", data={"email": "bob@example.com"}, follow_redirects=False
    )
    print("subscribe:", r.status_code, "(expect 200)")
    assert r.status_code == 200
    assert len(captured) == 1
    confirm_url = captured[-1].html
    # Extract the confirm URL from the HTML body
    import re

    m = re.search(r'href="([^"]+/confirm\?token=[^"]+)"', confirm_url)
    assert m, captured[-1].html
    confirm_url = m.group(1).replace("&amp;", "&")
    print("confirm link:", confirm_url[:80] + "...")

    # Hit the confirm URL
    r = client.get(confirm_url.replace("http://localhost:8000", ""))
    print("confirm:", r.status_code)
    assert r.status_code == 200

    # Subscriber should now be confirmed
    async with db.get_conn() as conn:
        n = await db.count_confirmed(conn)
    print("confirmed count:", n)
    assert n == 1

    # Now exercise the unsubscribe link
    async with db.get_conn() as conn:
        sub = await db.get_subscriber_by_email(conn, "bob@example.com")
    assert sub is not None
    token = sub["unsubscribe_token"]
    r = client.get(f"/unsubscribe?token={token}")
    print("unsubscribe:", r.status_code)
    assert r.status_code == 200

    async with db.get_conn() as conn:
        n = await db.count_confirmed(conn)
    assert n == 0
    print("subscribe flow OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
