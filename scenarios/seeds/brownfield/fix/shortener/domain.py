"""Link domain logic: validation, code allocation, expiry.

Codes are opaque, case-sensitive identifiers. Destinations are restricted to
http(s) without embedded credentials. Expiry uses absolute UTC timestamps;
a link is expired at exactly its expiry instant (``expires_at <= now``).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

CODE_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
CODE_LENGTH = 7
MAX_URL_LENGTH = 2048
ALLOWED_SCHEMES = {"http", "https"}


class LinkValidationError(ValueError):
    pass


@dataclass
class Link:
    code: str
    url: str
    created_at: str
    expires_at: str | None = None
    disabled: bool = False
    version: int = 1


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def validate_url(url: str) -> str:
    """Return the URL if acceptable as a redirect destination, else raise."""
    if not url or len(url) > MAX_URL_LENGTH:
        raise LinkValidationError(f"url must be 1..{MAX_URL_LENGTH} characters")
    parts = urlsplit(url)
    if parts.scheme not in ALLOWED_SCHEMES:
        raise LinkValidationError("only http and https destinations are allowed")
    if not parts.netloc:
        raise LinkValidationError("url must include a host")
    if parts.username is not None or parts.password is not None:
        raise LinkValidationError("credentials embedded in urls are not allowed")
    return url


def validate_expiry(expires_at: str | None) -> str | None:
    if expires_at is None:
        return None
    try:
        parsed = datetime.fromisoformat(expires_at)
    except ValueError as exc:
        raise LinkValidationError(f"expires_at must be ISO-8601: {exc}") from exc
    if parsed.tzinfo is None:
        raise LinkValidationError("expires_at must be timezone-aware (UTC)")
    return parsed.astimezone(timezone.utc).isoformat()


def generate_code(length: int = CODE_LENGTH) -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))


def is_expired(link: Link, now: datetime | None = None) -> bool:
    if link.expires_at is None:
        return False
    now = now or utcnow()
    return datetime.fromisoformat(link.expires_at) <= now


def is_servable(link: Link, now: datetime | None = None) -> bool:
    return not link.disabled and not is_expired(link, now)
