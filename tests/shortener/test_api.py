"""API behavior: CRUD, redirects, auth, optimistic concurrency, analytics,
cache invalidation, health, and the error envelope contract."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from shortener.api import create_app

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
    app = create_app(db_path=":memory:", admin_token="test-admin",
                     flush_interval=0.0, now_fn=clock)
    with TestClient(app) as c:
        yield c


def make_link(client, url="https://example.com/target", **kwargs):
    resp = client.post("/v1/links", json={"url": url, **kwargs})
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestCreate:
    def test_create_returns_code_and_short_url(self, client):
        body = make_link(client)
        assert len(body["code"]) == 7
        assert body["short_url"].endswith(body["code"])
        assert body["version"] == 1

    def test_invalid_scheme_rejected_with_error_envelope(self, client):
        resp = client.post("/v1/links", json={"url": "ftp://example.com"})
        assert resp.status_code == 400
        error = resp.json()["error"]
        assert error["code"] == "invalid_request"
        assert "http" in error["message"]

    def test_idempotent_replay_returns_same_code(self, client):
        first = client.post("/v1/links", json={
            "url": "https://example.com", "idempotency_key": "req-1"})
        replay = client.post("/v1/links", json={
            "url": "https://example.com", "idempotency_key": "req-1"})
        assert first.status_code == 201
        assert replay.status_code == 200
        assert first.json()["code"] == replay.json()["code"]

    def test_unique_codes_across_creates(self, client):
        codes = {make_link(client, url=f"https://example.com/{i}")["code"]
                 for i in range(30)}
        assert len(codes) == 30


class TestResolve:
    def test_redirect_is_temporary_307(self, client):
        code = make_link(client)["code"]
        resp = client.get(f"/{code}", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == "https://example.com/target"

    def test_unknown_code_404(self, client):
        resp = client.get("/zzzzzzz", follow_redirects=False)
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"

    def test_disabled_code_410(self, client):
        code = make_link(client)["code"]
        assert client.delete(f"/v1/links/{code}", headers=ADMIN).status_code == 204
        resp = client.get(f"/{code}", follow_redirects=False)
        assert resp.status_code == 410
        assert "disabled" in resp.json()["error"]["message"]

    def test_expired_code_410_even_when_cached(self, client, clock):
        expiry = (clock.now + timedelta(hours=1)).isoformat()
        code = make_link(client, expires_at=expiry)["code"]
        # Warm the cache while still valid.
        assert client.get(f"/{code}", follow_redirects=False).status_code == 307
        clock.now += timedelta(hours=2)
        resp = client.get(f"/{code}", follow_redirects=False)
        assert resp.status_code == 410
        assert "expired" in resp.json()["error"]["message"]


class TestUpdate:
    def test_requires_auth(self, client):
        code = make_link(client)["code"]
        resp = client.patch(f"/v1/links/{code}",
                            json={"url": "https://example.com/new", "version": 1})
        assert resp.status_code == 401
        resp = client.patch(f"/v1/links/{code}", headers={"X-Admin-Token": "wrong"},
                            json={"url": "https://example.com/new", "version": 1})
        assert resp.status_code == 403

    def test_update_bumps_version_and_redirects_to_new_target(self, client):
        code = make_link(client)["code"]
        # Warm cache with the old destination first.
        client.get(f"/{code}", follow_redirects=False)
        resp = client.patch(f"/v1/links/{code}", headers=ADMIN,
                            json={"url": "https://example.com/new", "version": 1})
        assert resp.status_code == 200
        assert resp.json()["version"] == 2
        follow = client.get(f"/{code}", follow_redirects=False)
        assert follow.headers["location"] == "https://example.com/new"  # cache invalidated

    def test_lost_update_conflict_409(self, client):
        code = make_link(client)["code"]
        ok = client.patch(f"/v1/links/{code}", headers=ADMIN,
                          json={"url": "https://example.com/a", "version": 1})
        assert ok.status_code == 200
        stale = client.patch(f"/v1/links/{code}", headers=ADMIN,
                             json={"url": "https://example.com/b", "version": 1})
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "version_conflict"


class TestAnalytics:
    def test_clicks_visible_in_stats_with_stated_consistency(self, client):
        code = make_link(client)["code"]
        for _ in range(3):
            client.get(f"/{code}", follow_redirects=False)
        stats = client.get(f"/v1/links/{code}/stats").json()
        assert stats["clicks"] == 3
        assert stats["last_click"] is not None
        assert "eventual" in stats["consistency"]

    def test_analytics_continuity_across_destination_update(self, client):
        code = make_link(client)["code"]
        client.get(f"/{code}", follow_redirects=False)
        client.patch(f"/v1/links/{code}", headers=ADMIN,
                     json={"url": "https://example.com/new", "version": 1})
        client.get(f"/{code}", follow_redirects=False)
        stats = client.get(f"/v1/links/{code}/stats").json()
        assert stats["clicks"] == 2  # aggregated by immutable link identity

    def test_redirect_fails_open_when_sink_is_broken(self, client):
        code = make_link(client)["code"]
        app = client.app
        app.state.analytics.db = None  # simulate a dead analytics backend
        resp = client.get(f"/{code}", follow_redirects=False)
        assert resp.status_code == 307  # redirect unaffected
        assert app.state.analytics.loss_risk_events >= 1


class TestHealth:
    def test_liveness(self, client):
        assert client.get("/health/live").json() == {"status": "ok"}

    def test_readiness_reports_cache_and_analytics(self, client):
        body = client.get("/health/ready").json()
        assert body["status"] == "ready"
        assert "cache" in body and "analytics_pending" in body

    def test_readiness_503_when_db_gone(self, client):
        client.app.state.db.conn.close()
        resp = client.get("/health/ready")
        assert resp.status_code == 503


class TestContract:
    """The OpenAPI document is part of the deliverable; routes and the error
    envelope are contract-tested so drift fails the build."""

    REQUIRED_PATHS = {
        "/v1/links": {"post"},
        "/v1/links/{code}": {"get", "patch", "delete"},
        "/v1/links/{code}/stats": {"get"},
        "/health/live": {"get"},
        "/health/ready": {"get"},
        "/{code}": {"get"},
    }

    def test_openapi_exposes_contract_paths(self, client):
        spec = client.get("/openapi.json").json()
        for path, methods in self.REQUIRED_PATHS.items():
            assert path in spec["paths"], f"missing path {path}"
            assert methods <= set(spec["paths"][path]), f"missing methods on {path}"

    def test_error_envelope_shape_is_uniform(self, client):
        cases = [
            client.get("/zzzzzzz", follow_redirects=False),
            client.post("/v1/links", json={"url": "ftp://x.com"}),
        ]
        for resp in cases:
            body = resp.json()
            assert set(body) == {"error"}
            assert {"code", "message"} <= set(body["error"])
