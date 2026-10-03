---
title: Welcome to the Newsletter
date: 2026-10-03
author: aiwonderland
summary: A quick tour of how this Git-driven newsletter service works.
---

# Welcome 👋

This newsletter is powered by **plain Markdown files** living in a Git repository.
When you push a new post to the `content/` directory (or to a remote repository you
point at via `GIT_REPO_URL`), a webhook triggers the server, which:

1. Pulls the latest content.
2. Re-renders every page.
3. Emails every confirmed subscriber.

## How to publish

Just commit a new `.md` file:

```bash
git add content/my-update.md
git commit -m "Publish my update"
git push
```

A few Markdown features are supported:

- **Bold**, *italic*, `inline code`
- Fenced code blocks with syntax highlighting
- Tables, task lists, and footnotes (GitHub-flavoured)
- YAML frontmatter for title, date, author and summary

> Tip: keep summaries short — they're used as the email preview.

Thanks for subscribing!
