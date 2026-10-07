"""Live demo: hit the running server with a fake GitHub webhook payload,
then add a subscriber and watch the broadcast happen.

Prereqs: a server is running on 127.0.0.1:8000 with ADMIN_TOKEN set.
"""

import asyncio
import hashlib
import hmac
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from newsletter import mailer

# Replace mailer with a capture
captured = []
orig_send = mailer.send_mail


async def fake_send(m, settings=None):
    captured.append(m)

mailer.send_mail = fake_send  # type: ignore


async def main():
    from newsletter import database as db
    from newsletter.tokens import make_confirm_token, make_unsubscribe_token

    await db.init_db()

    # 1. Add a confirmed subscriber
    async with db.get_conn() as conn:
        existing = await db.get_subscriber_by_email(conn, "demo@example.com")
        if existing:
            sid = int(existing["id"])
            await db.confirm_subscriber(conn, sid)
        else:
            sid = await db.add_subscriber(conn, "demo@example.com", "x", "y")
            await db.confirm_subscriber(conn, sid)
        await conn.execute(
            "UPDATE subscribers SET confirm_token=?, unsubscribe_token=? WHERE id=?",
            (make_confirm_token(sid), make_unsubscribe_token(sid), sid),
        )
    print(f"subscriber demo@example.com id={sid}")

    # 2. Drop the new post that triggered this whole conversation
    new_post = ROOT / "content" / "live-test.md"
    new_post.write_text(
        "---\n"
        "title: Live broadcast test\n"
        "date: 2026-10-07\n"
        "summary: This post was just pushed to trigger a real webhook broadcast.\n"
        "---\n\n"
        "# Live test\n\nIf you see this email, the broadcast pipeline works end-to-end.\n",
        encoding="utf-8",
    )
    print(f"created {new_post}")

    # 3. Hit the live webhook
    secret = "change-me-please"
    body = json.dumps({"ref": "refs/heads/main"}).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        "http://127.0.0.1:8000/webhook/github",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": sig,
        },
    )
    try:
        resp = urllib.request.urlopen(req)
        payload = json.loads(resp.read())
        print(f"webhook: {resp.status} {payload}")
    except urllib.error.HTTPError as e:
        print(f"webhook failed: {e.code} {e.read().decode()}")

    # 4. Show what was captured
    print(f"\n=== {len(captured)} email(s) dispatched ===")
    for m in captured:
        print(f"To: {m.to}")
        print(f"Subject: {m.subject}")
        print("HTML preview:", m.html[:300].replace("\n", " "), "...")
        print()

    # 5. Clean up
    new_post.unlink()
    captured.clear()


if __name__ == "__main__":
    asyncio.run(main())
