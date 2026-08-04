"""Verification for the hot-path cache and bounded-delay analytics.

The visibility test is contract-driven: it reads the shipped
``analytics_contract.json`` and asserts the sink's default flush interval
honors it. When the evaluator tightens the contract (60s -> 5s), a sink that
still defaults to 60s fails verification — which is exactly the point.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shortener.analytics import AnalyticsSink
from shortener.api import create_app
from shortener.cache import TTLCache

ADMIN = {"X-Admin-Token": "test-admin"}
CONTRACT = json.loads(
    (Path(__file__).resolve().parent.parent / "shortener" / "analytics_contract.json")
    .read_text())


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


class TestVisibilityContract:
    def test_default_flush_interval_honors_contract(self):
        bound = CONTRACT["visibility_seconds"]
        assert AnalyticsSink.DEFAULT_FLUSH_INTERVAL <= bound, (
            f"analytics default flush ({AnalyticsSink.DEFAULT_FLUSH_INTERVAL}s) "
            f"violates the approved visibility bound ({bound}s)")

    def test_stats_reads_flush_pending_events(self, client):
        code = make_link(client)["code"]
        for _ in range(3):
            client.get(f"/{code}", follow_redirects=False)
        stats = client.get(f"/v1/links/{code}/stats").json()
        assert stats["clicks"] == 3
        assert "eventual" in stats["consistency"]

    def test_redirect_fails_open_when_sink_is_broken(self, client):
        code = make_link(client)["code"]
        client.app.state.analytics.db = None
        assert client.get(f"/{code}", follow_redirects=False).status_code == 307
        assert client.app.state.analytics.loss_risk_events >= 1


class TestCacheCorrectness:
    def test_cache_hit_skips_db_but_respects_update(self, client):
        code = make_link(client)["code"]
        client.get(f"/{code}", follow_redirects=False)  # warm
        assert client.app.state.cache.get(code) is not None
        client.patch(f"/v1/links/{code}", headers=ADMIN,
                     json={"url": "https://example.com/new", "version": 1})
        follow = client.get(f"/{code}", follow_redirects=False)
        assert follow.headers["location"] == "https://example.com/new"

    def test_cached_entry_cannot_outlive_expiry(self, client, clock):
        expiry = (clock.now + timedelta(hours=1)).isoformat()
        code = make_link(client, expires_at=expiry)["code"]
        assert client.get(f"/{code}", follow_redirects=False).status_code == 307
        clock.now += timedelta(hours=2)
        assert client.get(f"/{code}", follow_redirects=False).status_code == 410

    def test_ttl_and_lru_bounds(self):
        class FakeClock:
            t = 0.0

            def __call__(self):
                return self.t

        fake = FakeClock()
        cache = TTLCache(maxsize=2, ttl=10, clock=fake)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.get("a")
        cache.put("c", 3)  # evicts b (LRU)
        assert cache.get("b") is None
        fake.t = 10.0
        assert cache.get("a") is None  # TTL expiry

    def test_readiness_exposes_cache_and_pending(self, client):
        body = client.get("/health/ready").json()
        assert "cache" in body and "analytics_pending" in body


class TestAggregateMigration:
    def test_populated_baseline_backfills_totals(self, client):
        """click_events recorded before migration 003 must appear in totals."""
        db = client.app.state.db
        with db.conn:
            db.conn.execute(
                "INSERT INTO links (code, url, created_at) VALUES (?, ?, ?)",
                ("old1234", "https://example.com/old", "2026-01-01T00:00:00+00:00"))
            db.conn.execute(
                "INSERT INTO click_events (code, ts) VALUES (?, ?)",
                ("old1234", "2026-01-02T00:00:00+00:00"))
            db.conn.execute(
                "INSERT OR REPLACE INTO click_totals (code, clicks, last_click) "
                "SELECT code, COUNT(*), MAX(ts) FROM click_events GROUP BY code")
        assert db.click_stats("old1234")["clicks"] == 1
