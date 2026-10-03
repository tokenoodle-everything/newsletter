"""Render Markdown posts into HTML and discover them on disk."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

import markdown
import yaml

log = logging.getLogger(__name__)


@dataclass
class Post:
    slug: str
    title: str
    date: Optional[datetime] = None
    summary: Optional[str] = None
    author: Optional[str] = None
    raw_markdown: str = ""
    html: str = ""
    source_path: Optional[Path] = None
    extra: dict = field(default_factory=dict)

    @property
    def path(self) -> str:
        return f"/posts/{self.slug}"


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def _split_frontmatter(text: str) -> tuple[dict, str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        log.warning("Failed to parse frontmatter: %s", exc)
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, text[m.end() :]


def _slugify(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"\.md$", "", name)
    name = re.sub(r"[^a-z0-9]+", "-", name)
    return name.strip("-") or "post"


def _coerce_date(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    # YAML parses bare YYYY-MM-DD into datetime.date
    import datetime as _dt

    if isinstance(value, _dt.date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        for fmt in (
            "%Y-%m-%d",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S%z",
        ):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return None


_MD = markdown.Markdown(
    extensions=[
        "fenced_code",
        "tables",
        "sane_lists",
        "codehilite",
        "toc",
        "attr_list",
        "md_in_html",
    ],
    extension_configs={
        "codehilite": {"guess_lang": False, "css_class": "highlight"},
    },
    output_format="html5",
)


def _render_markdown(body: str) -> str:
    _MD.reset()
    return _MD.convert(body)


def _auto_summary(html: str, max_chars: int = 240) -> str:
    """Strip HTML tags and produce a short plaintext summary."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    # Don't cut mid-word
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "…"


def discover_posts(content_dir: Path) -> List[Post]:
    """Find every ``*.md`` file under ``content_dir`` and parse it."""
    if not content_dir.exists():
        return []
    posts: list[Post] = []
    for md_file in sorted(content_dir.rglob("*.md")):
        rel = md_file.relative_to(content_dir)
        slug = _slugify(str(rel.with_suffix("")).replace("\\", "/"))
        try:
            posts.append(load_post(md_file, slug))
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("Failed to load post %s: %s", md_file, exc)
    posts.sort(key=lambda p: p.date or datetime.min, reverse=True)
    return posts


def load_post(path: Path, slug: Optional[str] = None) -> Post:
    raw = path.read_text(encoding="utf-8")
    meta, body = _split_frontmatter(raw)
    html = _render_markdown(body)
    title = (
        meta.get("title")
        or meta.get("Title")
        or path.stem.replace("-", " ").replace("_", " ").title()
    )
    summary = meta.get("summary") or meta.get("description")
    if not summary:
        summary = _auto_summary(html)
    return Post(
        slug=slug or _slugify(path.stem),
        title=str(title),
        date=_coerce_date(meta.get("date") or meta.get("published")),
        summary=str(summary) if summary else None,
        author=str(meta["author"]) if meta.get("author") else None,
        raw_markdown=body,
        html=html,
        source_path=path,
        extra={
            k: v
            for k, v in meta.items()
            if k
            not in {"title", "date", "summary", "author", "published", "description"}
        },
    )


def index_by_slug(posts: Iterable[Post]) -> dict[str, Post]:
    return {p.slug: p for p in posts}
