"""Regression suite for the expiry/update change.

The boundary test is the one that catches a strict-comparison defect: a link
expiring exactly *now* must already be expired.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from shortener.api import create_app
from shortener.domain import Link, is_expired, validate_expiry
from shortener.domain import LinkValidationError

ADMIN = {"X-Admin-Token": "test-admin"}


@pytest.fixture()
def clock():
    class Clock:
        def __init__(self):
            self.now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

        def __call__(self):
            return self.now

    return Clock()


@pytest.fixture()
def client(clock):
    app = create_app(db_path=":memory:", admin_token="test-admin", now_fn=clock)
    with TestClient(app) as c:
        yield c


def make_link(client, url="https://example.com/target", **kwargs):
    resp = client.post("/v1/links", json={"url": url, **kwargs})
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestExpiry:
    def test_boundary_exactly_now_is_expired(self):
        now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        link = Link(code="abc1234", url="https://example.com",
                    created_at="2026-01-01T00:00:00+00:00",
                    expires_at=now.isoformat())
        assert is_expired(link, now), "a link expiring exactly now must be expired"
        assert not is_expired(link, now - timedelta(seconds=1))

    def test_expired_link_410_distinct_from_unknown_404(self, client, clock):
        expiry = (clock.now + timedelta(hours=1)).isoformat()
        code = make_link(client, expires_at=expiry)["code"]
        assert client.get(f"/{code}", follow_redirects=False).status_code == 307
        clock.now += timedelta(hours=2)
        resp = client.get(f"/{code}", follow_redirects=False)
        assert resp.status_code == 410
        assert "expired" in resp.json()["error"]["message"]
        assert client.get("/zzzzzzz", follow_redirects=False).status_code == 404

    def test_links_without_expiry_unaffected(self, client, clock):
        code = make_link(client)["code"]
        clock.now += timedelta(days=365)
        assert client.get(f"/{code}", follow_redirects=False).status_code == 307

    def test_expiry_must_be_timezone_aware(self):
        with pytest.raises(LinkValidationError, match="timezone"):
            validate_expiry("2026-06-01T12:00:00")


class TestDestinationUpdate:
    def test_requires_auth(self, client):
        code = make_link(client)["code"]
        body = {"url": "https://example.com/new", "version": 1}
        assert client.patch(f"/v1/links/{code}", json=body).status_code == 401
        assert client.patch(f"/v1/links/{code}", json=body,
                            headers={"X-Admin-Token": "wrong"}).status_code == 403

    def test_update_bumps_version_and_changes_redirect(self, client):
        code = make_link(client)["code"]
        resp = client.patch(f"/v1/links/{code}", headers=ADMIN,
                            json={"url": "https://example.com/new", "version": 1})
        assert resp.status_code == 200 and resp.json()["version"] == 2
        follow = client.get(f"/{code}", follow_redirects=False)
        assert follow.headers["location"] == "https://example.com/new"

    def test_lost_update_is_a_conflict(self, client):
        code = make_link(client)["code"]
        ok = client.patch(f"/v1/links/{code}", headers=ADMIN,
                          json={"url": "https://example.com/a", "version": 1})
        stale = client.patch(f"/v1/links/{code}", headers=ADMIN,
                             json={"url": "https://example.com/b", "version": 1})
        assert ok.status_code == 200 and stale.status_code == 409
        assert stale.json()["error"]["code"] == "version_conflict"

    def test_analytics_identity_survives_destination_change(self, client):
        code = make_link(client)["code"]
        client.get(f"/{code}", follow_redirects=False)
        client.patch(f"/v1/links/{code}", headers=ADMIN,
                     json={"url": "https://example.com/new", "version": 1})
        client.get(f"/{code}", follow_redirects=False)
        stats = client.get(f"/v1/links/{code}/stats").json()
        assert stats["clicks"] == 2  # aggregated by immutable code


class TestMigrationCompatibility:
    def test_pre_migration_rows_read_back_with_defaults(self, client):
        """Rows inserted the v1 way (no expiry columns in the INSERT) must
        read back as never-expiring version-1 links."""
        db = client.app.state.db
        with db.conn:
            db.conn.execute(
                "INSERT INTO links (code, url, created_at) VALUES (?, ?, ?)",
                ("old1234", "https://example.com/old", "2026-01-01T00:00:00+00:00"))
        link = db.get_link("old1234")
        assert link.expires_at is None and link.version == 1
        assert client.get("/old1234", follow_redirects=False).status_code == 307
