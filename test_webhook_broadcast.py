"""Verify the GitHub webhook actually broadcasts new posts and dedupes properly."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Use a temp content dir so this test doesn't depend on what's in ./content
TEST_CONTENT = Path(tempfile.mkdtemp(prefix="newsletter-test-"))
os.environ["CONTENT_DIR"] = str(TEST_CONTENT)
os.environ["GIT_REPO_URL"] = str(TEST_CONTENT)  # local source = same dir
os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "test-secret")
os.environ.setdefault("TOKEN_SECRET", "test-token-secret")
os.environ.setdefault("ADMIN_TOKEN", "test-admin-token")
os.environ.setdefault("PUBLIC_BASE_URL", "http://localhost:8000")

from newsletter.config import get_settings
get_settings.cache_clear()  # force re-read with new env vars

os.chdir(ROOT)

# Reset state for a deterministic test
data_dir = ROOT / "data"
if data_dir.exists():
    shutil.rmtree(data_dir)

TEST_CONTENT.mkdir(parents=True, exist_ok=True)
# Seed one existing post so we have a baseline
(TEST_CONTENT / "welcome.md").write_text(
    "---\ntitle: Welcome\ndate: 2026-10-03\nsummary: Initial post\n---\n\n"
    "# Welcome\n\nFirst post.\n",
    encoding="utf-8",
)


# --- Stub the mailer so nothing leaves the box ---
from newsletter import mailer
captured: list = []


async def fake_send(m, settings=None):
    captured.append(m)


mailer.send_mail = fake_send  # type: ignore[assignment]


# --- Set up a confirmed subscriber directly so we don't depend on /subscribe ---
async def main() -> int:
    from newsletter import database as db
    from newsletter.config import get_settings
    from newsletter.main import app, _post_content_hash
    from newsletter.tokens import make_confirm_token, make_unsubscribe_token

    settings = get_settings()
    await db.init_db()

    async with db.get_conn() as conn:
        sid = await db.add_subscriber(conn, "alice@example.com", "x", "y")
        await conn.execute(
            "UPDATE subscribers SET confirm_token=?, unsubscribe_token=? WHERE id=?",
            (make_confirm_token(sid), make_unsubscribe_token(sid), sid),
        )
        await db.confirm_subscriber(conn, sid)

    from fastapi.testclient import TestClient

    # Helper for posting the webhook
    def post_push() -> dict:
        body = json.dumps({"ref": "refs/heads/main"}).encode()
        sig = "sha256=" + hmac.new(
            settings.github_webhook_secret.encode(), body, hashlib.sha256
        ).hexdigest()
        r = c.post(
            "/webhook/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "push",
                "X-Hub-Signature-256": sig,
            },
        )
        assert r.status_code == 200, r.text
        return r.json()

    with TestClient(app) as c:
        # ===== 1. First push after seed: only the seeded post (welcome) sends =====
        result = post_push()
        delivered = result["delivered"]
        print("seed push:", [d["slug"] for d in delivered])
        slugs = [d["slug"] for d in delivered]
        assert "welcome" in slugs, slugs
        assert len(captured) == 1, captured
        assert captured[0].to == "alice@example.com"

        captured.clear()

        # ===== 2. Second push with no new posts: dedupe, nothing sent =====
        result = post_push()
        print("idempotent push:", result["delivered"])
        assert captured == [], "Idempotent push should not re-send"

        # ===== 3. New post added: should broadcast =====
        new_path = TEST_CONTENT / "my-new-post.md"
        new_path.write_text(
            "---\ntitle: Hello from a new post\n"
            "date: 2026-10-03\n"
            "summary: A test of the webhook broadcast pipeline.\n"
            "---\n\n# Hi there\n\nThis is brand new.\n",
            encoding="utf-8",
        )
        result = post_push()
        slugs = [d["slug"] for d in result["delivered"]]
        print("new-post push:", slugs)
        assert "my-new-post" in slugs
        assert "welcome" not in slugs, "welcome was already sent, should dedupe"
        assert len(captured) == 1, captured
        assert "Hello from a new post" in captured[0].subject

        # ===== 4. Editing the new post: should re-broadcast =====
        captured.clear()
        new_path.write_text(
            "---\ntitle: Hello from a new post\n"
            "date: 2026-10-03\n"
            "summary: A test of the webhook broadcast pipeline (edited).\n"
            "---\n\n# Hi there\n\nThis is brand new AND edited.\n",
            encoding="utf-8",
        )
        result = post_push()
        print("edit push:", [d["slug"] for d in result["delivered"]])
        assert len(captured) == 1, "Edited content should re-broadcast"
        body_blob = captured[0].html + captured[0].text
        assert "edited" in body_blob, body_blob[:500]

        # ===== 5. Admin "rebroadcast" endpoint forces another send =====
        captured.clear()
        admin_token = settings.admin_token
        assert admin_token
        r = c.post(
            "/admin/api/posts/my-new-post/rebroadcast",
            headers={"X-Admin-Token": admin_token},
        )
        assert r.status_code == 200, r.text
        # Next webhook pushes without content change: should re-send because we cleared
        result = post_push()
        print("after admin rebroadcast:", [d["slug"] for d in result["delivered"]])
        assert len(captured) == 1, "Admin rebroadcast should force re-send"

        # ===== 6. rebroadcast-all clears everything =====
        captured.clear()
        r = c.post(
            "/admin/api/posts/rebroadcast-all",
            headers={"X-Admin-Token": admin_token},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["cleared"] >= 2
        result = post_push()
        print("after rebroadcast-all:", [d["slug"] for d in result["delivered"]])
        assert len(captured) == 2, captured  # welcome + my-new-post both re-sent

    print("Webhook broadcast test OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
