"""Domain rules: URL validation and code allocation."""

import pytest

from shortener.domain import (
    CODE_ALPHABET,
    CODE_LENGTH,
    LinkValidationError,
    generate_code,
    validate_url,
)


@pytest.mark.parametrize("url", [
    "https://example.com/path?q=1",
    "http://sub.domain.example:8080/x",
])
def test_accepts_http_https(url):
    assert validate_url(url) == url


@pytest.mark.parametrize("url,fragment", [
    ("ftp://example.com/file", "http and https"),
    ("javascript:alert(1)", "http and https"),
    ("https://user:pw@example.com", "credentials"),
    ("https://", "host"),
    ("", "characters"),
    ("https://example.com/" + "a" * 3000, "characters"),
])
def test_rejects_unsafe_destinations(url, fragment):
    with pytest.raises(LinkValidationError, match=fragment):
        validate_url(url)


def test_code_shape():
    for _ in range(50):
        code = generate_code()
        assert len(code) == CODE_LENGTH
        assert all(c in CODE_ALPHABET for c in code)
