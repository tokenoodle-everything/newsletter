"""Smoke test the admin API (cookie + token paths) and HTML dashboard."""

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# Reset DB so the test is deterministic
data = ROOT / "data"
if data.exists():
    import shutil

    shutil.rmtree(data)

from fastapi.testclient import TestClient
from newsletter.config import get_settings
from newsletter.main import app

settings = get_settings()
assert settings.admin_token, "ADMIN_TOKEN must be set in .env for this test"


def main() -> int:
    # Use TestClient as context manager so lifespan runs (init_db + sync_content)
    with TestClient(app) as c:
        token = settings.admin_token
        return _run(c, token)


def _run(c: TestClient, token: str) -> int:

    # 1. Unauthenticated request rejected
    r = c.get("/admin/api/stats")
    assert r.status_code == 401, r.text

    # 2. Wrong token rejected
    r = c.get("/admin/api/stats", headers={"X-Admin-Token": "nope"})
    assert r.status_code == 401, r.text

    # 3. Valid token returns JSON
    r = c.get("/admin/api/stats", headers={"X-Admin-Token": token})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "subscribers" in body and "deliveries" in body and "content" in body
    print(
        "stats:",
        body["subscribers"],
        body["deliveries"],
        "posts=",
        body["content"]["posts"],
    )

    # 4. Login page is reachable
    r = c.get("/admin/login")
    assert r.status_code == 200
    assert "Admin login" in r.text

    # 5. Cookie-based session works
    r = c.post("/admin/login", data={"token": token}, follow_redirects=False)
    assert r.status_code == 303
    assert "newsletter_admin" in r.cookies
    r = c.get("/admin/api/stats")
    assert r.status_code == 200, r.text
    print("cookie auth OK")

    # 6. Subscribers list
    r = c.get("/admin/api/subscribers", headers={"X-Admin-Token": token})
    assert r.status_code == 200, r.text
    print("subscribers:", r.json())

    # 7. Add a subscriber, confirm, search, delete
    from newsletter import database as db
    from newsletter.tokens import make_confirm_token, make_unsubscribe_token

    async def setup():
        await db.init_db()
        async with db.get_conn() as conn:
            sid = await db.add_subscriber(conn, "test1@example.com", "x", "y")
            await db.confirm_subscriber(conn, sid)
            sid2 = await db.add_subscriber(conn, "test2@example.com", "x", "y")
            await conn.execute(
                "UPDATE subscribers SET confirm_token=?, unsubscribe_token=? WHERE id=?",
                (make_confirm_token(sid), make_unsubscribe_token(sid), sid),
            )
            await conn.execute(
                "UPDATE subscribers SET confirm_token=?, unsubscribe_token=? WHERE id=?",
                (make_confirm_token(sid2), make_unsubscribe_token(sid2), sid2),
            )
            # And record some deliveries
            await db.record_delivery(conn, "welcome", sid, "sent")
            await db.record_delivery(conn, "welcome", sid2, "failed", "smtp error")

    asyncio.run(setup())

    r = c.get("/admin/api/subscribers?q=test", headers={"X-Admin-Token": token})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 2, items
    print("search found", len(items), "subscribers")

    r = c.get("/admin/api/deliveries?status=sent", headers={"X-Admin-Token": token})
    assert r.status_code == 200, r.text
    sent_items = r.json()["items"]
    assert all(i["status"] == "sent" for i in sent_items)
    print("sent deliveries:", len(sent_items))

    r = c.get("/admin/api/deliveries?status=failed", headers={"X-Admin-Token": token})
    failed_items = r.json()["items"]
    assert len(failed_items) == 1 and failed_items[0]["error"] == "smtp error"
    print("failed deliveries:", len(failed_items))

    r = c.post("/admin/api/sync", headers={"X-Admin-Token": token})
    assert r.status_code == 200, r.text
    print("sync:", r.json())

    # 8. Dashboard HTML
    r = c.get("/admin")
    assert r.status_code == 200, r.text
    assert "Newsletter Admin" in r.text
    assert "/admin/api/stats" in r.text  # JS endpoint referenced
    print("dashboard HTML OK")

    # 9. Delete a subscriber
    sub_id = items[0]["id"]
    r = c.delete(f"/admin/api/subscribers/{sub_id}", headers={"X-Admin-Token": token})
    assert r.status_code == 200, r.text
    print("deleted subscriber", sub_id)

    print("Admin test OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
