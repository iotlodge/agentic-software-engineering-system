"""Domain rules: URL validation, code alphabet, expiry boundaries."""

from datetime import datetime, timedelta, timezone

import pytest

from shortener.domain import (
    CODE_ALPHABET,
    CODE_LENGTH,
    Link,
    LinkValidationError,
    generate_code,
    is_expired,
    is_servable,
    validate_expiry,
    validate_url,
)


class TestUrlValidation:
    @pytest.mark.parametrize("url", [
        "https://example.com/path?q=1",
        "http://sub.domain.example:8080/x",
    ])
    def test_accepts_http_https(self, url):
        assert validate_url(url) == url

    @pytest.mark.parametrize("url,fragment", [
        ("ftp://example.com/file", "http and https"),
        ("javascript:alert(1)", "http and https"),
        ("file:///etc/passwd", "http and https"),
        ("https://user:pw@example.com", "credentials"),
        ("https://", "host"),
        ("", "characters"),
        ("https://example.com/" + "a" * 3000, "characters"),
    ])
    def test_rejects_unsafe_destinations(self, url, fragment):
        with pytest.raises(LinkValidationError, match=fragment):
            validate_url(url)


class TestCodes:
    def test_code_shape(self):
        for _ in range(50):
            code = generate_code()
            assert len(code) == CODE_LENGTH
            assert all(c in CODE_ALPHABET for c in code)

    def test_codes_are_case_sensitive_alphabet(self):
        assert "a" in CODE_ALPHABET and "A" in CODE_ALPHABET


class TestExpiry:
    def _link(self, expires_at):
        return Link(code="abc1234", url="https://example.com",
                    created_at="2026-01-01T00:00:00+00:00", expires_at=expires_at)

    def test_no_expiry_never_expires(self):
        assert not is_expired(self._link(None))

    def test_boundary_exactly_now_is_expired(self):
        now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        link = self._link(now.isoformat())
        assert is_expired(link, now)
        assert not is_expired(link, now - timedelta(seconds=1))

    def test_disabled_is_not_servable(self):
        link = self._link(None)
        link.disabled = True
        assert not is_servable(link)

    def test_expiry_requires_timezone(self):
        with pytest.raises(LinkValidationError, match="timezone"):
            validate_expiry("2026-06-01T12:00:00")

    def test_expiry_normalized_to_utc(self):
        out = validate_expiry("2026-06-01T14:00:00+02:00")
        assert out == "2026-06-01T12:00:00+00:00"
