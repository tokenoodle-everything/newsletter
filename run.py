"""Convenience launcher for local development.

Usage:
    python run.py
"""

from __future__ import annotations

import uvicorn

from newsletter.config import get_settings


def main() -> None:
    s = get_settings()
    uvicorn.run(
        "newsletter.main:app",
        host=s.host,
        port=s.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
