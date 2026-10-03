"""Centralised application configuration loaded from environment / .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Application settings sourced from environment variables / .env file."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Public URL used to build absolute links in emails (confirm/unsubscribe/view post)
    public_base_url: str = "http://localhost:8000"

    # --- Content / Git source ---
    # When these point at the same path the service uses that directory
    # in-place (no copying). Point `git_repo_url` at a remote URL to have
    # content cloned/pulled into `content_dir`.
    git_repo_url: str = "./content"
    git_branch: str = ""
    content_dir: str = "./content"

    # --- Webhook ---
    github_webhook_secret: str = "change-me-please"

    # --- Email ---
    mail_from: str = "Newsletter <news@example.com>"
    mail_from_name: str = "Newsletter"
    mail_backend: Literal["smtp", "http"] = "smtp"

    # SMTP
    smtp_host: str = "improvmx.com"
    smtp_port: int = 2525
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_use_starttls: bool = True

    # HTTP mail API
    mail_http_url: str = ""
    mail_http_token: str = ""
    mail_http_header: str = "Authorization"
    mail_http_scheme: str = "Bearer"

    # --- Tokens ---
    token_secret: str = Field(
        default="please-change-this-to-a-random-string",
        description="Used to sign confirmation / unsubscribe tokens.",
    )

    # --- Admin ---
    # Token required to access /admin and /admin/api/*.
    # Generate one with:  python -c "import secrets; print(secrets.token_urlsafe(32))"
    # Leave empty to disable the admin area entirely.
    admin_token: str = ""

    # --- Server ---
    host: str = "127.0.0.1"
    port: int = 8000

    # --- Derived paths ---
    @property
    def project_root(self) -> Path:
        return PROJECT_ROOT

    @property
    def db_path(self) -> Path:
        return PROJECT_ROOT / "data" / "newsletter.db"

    @property
    def content_path(self) -> Path:
        p = Path(self.content_dir)
        return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
