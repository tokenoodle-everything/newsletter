"""Synchronise the source content repository into a local directory."""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from .config import Settings, get_settings

log = logging.getLogger(__name__)


def _looks_like_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https", "git", "ssh", "file"}


async def _run(cmd: list[str], cwd: Optional[Path] = None) -> tuple[int, str, str]:
    """Run an async subprocess and capture stdout/stderr."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def sync_content(settings: Optional[Settings] = None) -> Path:
    """Ensure ``content_path`` is populated from the configured source.

    * If ``git_repo_url`` is a local path, mirror its ``*.md`` files into
      ``content_path``.
    * If it's a remote URL, ``git clone`` on first run and ``git pull``
      thereafter.

    Always returns the path to the populated content directory
    (``content_path``).
    """
    settings = settings or get_settings()
    target: Path = settings.content_path
    target.mkdir(parents=True, exist_ok=True)

    repo = settings.git_repo_url
    branch_args = ["-b", settings.git_branch] if settings.git_branch else []

    if not _looks_like_url(repo):
        # Local source — mirror into content_path.
        src = Path(repo)
        if not src.is_absolute():
            src = (settings.project_root / src).resolve()
        if not src.exists():
            raise FileNotFoundError(
                f"Local content source not found: {src}. "
                f"Create it or set GIT_REPO_URL to a git URL."
            )

        if src.resolve() == target.resolve():
            log.info("Content source equals CONTENT_DIR; using %s in place", target)
            return target

        log.info("Mirroring local content %s -> %s", src, target)
        src_files = {p.relative_to(src) for p in src.rglob("*.md")}
        target_files = {p.relative_to(target) for p in target.rglob("*.md")}
        for rel in target_files - src_files:
            (target / rel).unlink(missing_ok=True)
        for rel in src_files - target_files:
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((src / rel).read_bytes())
        for rel in src_files & target_files:
            s, d = src / rel, target / rel
            if (
                s.stat().st_mtime_ns != d.stat().st_mtime_ns
                or s.stat().st_size != d.stat().st_size
            ):
                d.write_bytes(s.read_bytes())
        return target

    # Remote URL — clone on first run, pull thereafter.
    is_repo = (target / ".git").exists()
    if not is_repo:
        if any(target.iterdir()):
            # Non-empty and not a repo — refuse to overwrite.
            raise RuntimeError(
                f"Content directory {target} is not empty and not a git repo. "
                f"Clear it or set a different CONTENT_DIR."
            )
        log.info("Cloning %s into %s", repo, target)
        rc, out, err = await _run(["git", "clone", *branch_args, repo, str(target)])
        if rc != 0:
            raise RuntimeError(f"git clone failed: {err.strip() or out.strip()}")
    else:
        log.info("Pulling latest content into %s", target)
        rc, out, err = await _run(["git", "fetch", "--all"], cwd=target)
        if rc != 0:
            raise RuntimeError(f"git fetch failed: {err.strip() or out.strip()}")
        pull_cmd = ["git", "pull", "--ff-only"]
        if settings.git_branch:
            pull_cmd += [settings.git_branch]
        rc, out, err = await _run(pull_cmd, cwd=target)
        if rc != 0:
            # Try a fast-forwardless pull as a fallback
            rc2, out2, err2 = await _run(["git", "pull"], cwd=target)
            if rc2 != 0:
                raise RuntimeError(f"git pull failed: {err.strip() or out.strip()}")
    return target


def wipe_content(settings: Optional[Settings] = None) -> None:
    """Remove the cloned content directory. Used by tests / admin tooling."""
    settings = settings or get_settings()
    p = settings.content_path
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
