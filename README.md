# newsletter

A self-hosted, Git-driven newsletter service.

- Authors write posts in **Markdown** inside a Git repository.
- A **GitHub Webhook** triggers the backend on every push.
- The backend syncs content, renders HTML pages, and emails every
  confirmed subscriber.
- Subscriptions live in a local **SQLite** database — no third-party
  newsletter platform required.
- Email delivery works through **SMTP** (e.g. ImprovMX as a relay) or any
  HTTP mail API (Mailgun / SendGrid / Resend style).
- Compliance-friendly: every email contains a working **unsubscribe** link
  and subscribers go through double-opt-in confirmation.

Built with **FastAPI**.

---

## Quick start

```bash
# 1. Create a venv & install deps
python -m venv .venv
. .venv/Scripts/activate          # Windows
# source .venv/bin/activate       # macOS / Linux
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# edit .env — set PUBLIC_BASE_URL, GITHUB_WEBHOOK_SECRET, mail settings, ...

# 3. Run
python run.py
# -> http://127.0.0.1:8000
```

The first run will create `data/newsletter.db` and pull / clone the source
content repository configured via `GIT_REPO_URL`.

## Configuration

All settings are loaded from environment variables (or a `.env` file) — see
[`.env.example`](.env.example). Important keys:

| Variable | Purpose |
| --- | --- |
| `PUBLIC_BASE_URL` | Base URL used to build absolute links in emails |
| `GIT_REPO_URL` | Local path (`./news`) or remote URL of the content repo |
| `GIT_BRANCH` | Optional branch to check out |
| `CONTENT_DIR` | Local directory the synced content is mirrored into |
| `GITHUB_WEBHOOK_SECRET` | Shared secret configured in the GitHub webhook |
| `MAIL_BACKEND` | `smtp` or `http` |
| `SMTP_*` | SMTP / ImprovMX settings |
| `MAIL_HTTP_*` | HTTP mail API settings |
| `TOKEN_SECRET` | Secret for signing confirmation / unsubscribe tokens |

## Content format

Each post is a `.md` file. Optional YAML frontmatter controls metadata:

```markdown
---
title: My update
date: 2026-10-03
author: Jane Doe
summary: A short teaser shown in the email body.
---

# Hello

Write the rest of your post here…
```

The slug is derived from the filename (`my-update.md` → `/posts/my-update`).

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET`  | `/` | Latest posts (HTML) |
| `GET`  | `/archive` | All posts (HTML) |
| `GET`  | `/posts/{slug}` | Single post (HTML) |
| `POST` | `/subscribe` | Subscribe via the web form |
| `GET`  | `/confirm?token=…` | Confirm a subscription |
| `GET` / `POST` | `/unsubscribe?token=…` | Unsubscribe |
| `GET`  | `/api/posts` | JSON list of posts |
| `POST` | `/api/subscribe` | JSON subscribe |
| `GET`  | `/api/stats` | Subscriber count |
| `POST` | `/webhook/github` | GitHub webhook receiver |
| `GET`  | `/healthz` | Liveness probe |

## Setting up the GitHub webhook

In your content repository's settings → **Webhooks** → **Add webhook**:

- **Payload URL**: `https://your-domain.example.com/webhook/github`
- **Content type**: `application/json`
- **Secret**: must match `GITHUB_WEBHOOK_SECRET` in `.env`
- **Events**: "Just the push event" is sufficient.

When you push a new `.md` file, GitHub will hit the webhook, the server
will pull the latest content and email all confirmed subscribers about the
new post.

### Broadcast dedupe

The webhook tracks each post's content hash in a `dispatched_posts`
SQLite table. On every push it only emails **posts whose content has
changed since the last successful send** (or that have never been sent).
Editing a post and re-pushing will re-send to all confirmed subscribers.

To force a re-blast:

- One post: `POST /admin/api/posts/{slug}/rebroadcast` (token auth)
- Everything: `POST /admin/api/posts/rebroadcast-all`
- All pushes (env): set `NEWSLETTER_FORCE_REBROADCAST=true`

## Local development tips

- Set `MAIL_BACKEND=http` to a request-capture service like
  [Mailpit](https://mailpit.axllent.org/) or a local `nc` listener while
  iterating.
- Use the existing `content/` directory for the content repo (already
  populated with a sample `welcome.md`).
- For tests / re-runs, delete `data/newsletter.db` to reset subscribers.

## Admin dashboard & API

The service ships with a token-protected admin area at `/admin` (HTML) and
`/admin/api/*` (JSON).

Set `ADMIN_TOKEN` in your `.env` to enable it:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"  # generate
echo "ADMIN_TOKEN=the-generated-value" >> .env
```

Open <http://localhost:8000/admin/login>, paste the token and you'll get a
12-hour signed cookie session. All admin API requests also accept the token
via the `X-Admin-Token` request header for scripting:

```bash
curl -H "X-Admin-Token: $ADMIN_TOKEN" http://localhost:8000/admin/api/stats
```

### Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET`  | `/admin/login` | Login form |
| `POST` | `/admin/login` | Submit token → set session cookie |
| `POST` | `/admin/logout` | Clear session |
| `GET`  | `/admin` | HTML dashboard (Overview/Subscribers/Deliveries/Posts/Actions) |
| `GET`  | `/admin/api/stats` | Aggregate counts (subscribers, deliveries, content) |
| `GET`  | `/admin/api/subscribers` | List + search (`?q=`, `?confirmed=true|false`) |
| `POST` | `/admin/api/subscribers/{id}/confirm` | Manually confirm a pending subscriber |
| `DELETE` | `/admin/api/subscribers/{id}` | Delete a subscriber and their deliveries |
| `GET`  | `/admin/api/deliveries` | Filter by `post_slug`, `status`, `since_hours` |
| `GET`  | `/admin/api/posts` | List discovered posts with metadata |
| `GET`  | `/admin/api/posts/{slug}/raw` | Raw Markdown of a post |
| `POST` | `/admin/api/sync` | Trigger a git sync on demand |

If `ADMIN_TOKEN` is empty, the entire admin area returns `503`.

## Project layout

```
newsletter/
├── newsletter/          # Python package
│   ├── main.py          # FastAPI app
│   ├── config.py        # Settings (pydantic-settings)
│   ├── database.py      # SQLite / aiosqlite helpers
│   ├── git_sync.py      # git clone/pull wrapper
│   ├── renderer.py      # Markdown → Post objects
│   ├── mailer.py        # SMTP + HTTP mail backends
│   ├── emails.py        # Jinja2 email templates
│   ├── tokens.py        # Signed confirm / unsubscribe tokens
│   ├── admin.py         # Admin API + HTML dashboard
│   ├── models.py        # Pydantic API models
│   └── templates/       # HTML templates
├── static/style.css
├── content/             # Markdown posts (also the sync target)
├── data/                # SQLite database (gitignored)
├── .env.example
├── requirements.txt
└── run.py               # `python run.py` entrypoint
```

## License

MIT.
