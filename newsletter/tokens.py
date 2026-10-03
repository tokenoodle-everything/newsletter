"""Signed tokens used in confirmation / unsubscribe links."""

from __future__ import annotations

from itsdangerous import BadSignature, URLSafeTimedSerializer

from .config import get_settings


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().token_secret, salt="newsletter")


def make_confirm_token(subscriber_id: int) -> str:
    return _serializer().dumps(("confirm", subscriber_id))


def make_unsubscribe_token(subscriber_id: int) -> str:
    return _serializer().dumps(("unsub", subscriber_id))


def load_token(token: str, max_age_seconds: int = 60 * 60 * 24 * 30) -> tuple[str, int]:
    """Return (purpose, subscriber_id). Raises BadSignature / SignatureExpired."""
    data = _serializer().loads(token, max_age=max_age_seconds)
    if not isinstance(data, (list, tuple)) or len(data) != 2:
        raise BadSignature("malformed token")
    purpose, sid = data
    return str(purpose), int(sid)
