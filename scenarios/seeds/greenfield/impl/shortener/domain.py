"""Link domain logic: validation and code allocation.

Codes are opaque, case-sensitive identifiers. Destinations are restricted to
http(s) without embedded credentials.
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
    disabled: bool = False


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


def generate_code(length: int = CODE_LENGTH) -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))


def is_servable(link: Link) -> bool:
    return not link.disabled
