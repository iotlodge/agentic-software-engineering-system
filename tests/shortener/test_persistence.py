"""Persistence: migrations (including populated-baseline upgrade), unique-code
allocation under concurrency, idempotency, and optimistic concurrency."""

import threading
from pathlib import Path

import pytest

from shortener.domain import utcnow
from shortener.persistence import (
    ConflictError,
    Database,
    NotFoundError,
)

MIGRATIONS = Path("shortener/migrations")


@pytest.fixture()
def db():
    database = Database(":memory:")
    database.migrate()
    return database


class TestMigrations:
    def test_all_apply_in_order_once(self):
        db = Database(":memory:")
        first = db.migrate()
        assert first == sorted(first)
        assert len(first) >= 3
        assert db.migrate() == []  # idempotent

    def test_populated_baseline_upgrades_in_place(self, tmp_path):
        """A v1 database with live rows must migrate forward: existing links
        keep working, gain no-expiry defaults and version 1."""
        db = Database(str(tmp_path / "wl.db"))
        # Apply only 001, insert baseline data, then apply the rest.
        only_001 = tmp_path / "m1"
        only_001.mkdir()
        (only_001 / "001_init.sql").write_text((MIGRATIONS / "001_init.sql").read_text())
        db.migrate(migrations_dir=only_001)
        with db.conn:
            db.conn.execute(
                "INSERT INTO links (code, url, created_at) VALUES (?, ?, ?)",
                ("old1234", "https://example.com/old", utcnow().isoformat()))
            db.conn.execute(
                "INSERT INTO click_events (code, ts) VALUES (?, ?)",
                ("old1234", utcnow().isoformat()))
        applied = db.migrate()  # full set now
        assert "002_expiry_update.sql" in applied
        link = db.get_link("old1234")
        assert link.expires_at is None and link.version == 1
        # 003 backfilled aggregates from pre-existing events.
        assert db.click_stats("old1234")["clicks"] == 1


class TestConcurrency:
    def test_no_duplicate_codes_under_concurrent_creates(self, db):
        codes, errors = [], []
        lock = threading.Lock()

        def create(i):
            try:
                link, _ = db.create_link(f"https://example.com/{i}")
                with lock:
                    codes.append(link.code)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=create, args=(i,)) for i in range(24)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert len(codes) == len(set(codes)) == 24

    def test_concurrent_updates_one_wins_one_conflicts(self, db):
        link, _ = db.create_link("https://example.com")
        results = []

        def update(target):
            try:
                db.update_destination(link.code, target, expected_version=1)
                results.append("ok")
            except ConflictError:
                results.append("conflict")

        threads = [threading.Thread(target=update, args=(f"https://example.com/{i}",))
                   for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(results) == ["conflict", "ok"]


class TestIdempotencyAndAudit:
    def test_idempotency_key_replays(self, db):
        first, created1 = db.create_link("https://example.com", idempotency_key="k1")
        replay, created2 = db.create_link("https://example.com", idempotency_key="k1")
        assert created1 and not created2
        assert first.code == replay.code

    def test_update_and_disable_write_audit_rows(self, db):
        link, _ = db.create_link("https://example.com")
        db.update_destination(link.code, "https://example.com/new", 1)
        db.disable_link(link.code)
        rows = db.conn.execute(
            "SELECT action FROM link_audit WHERE code = ? ORDER BY id",
            (link.code,)).fetchall()
        assert [r["action"] for r in rows] == ["update_destination", "disable"]

    def test_unknown_code_raises(self, db):
        with pytest.raises(NotFoundError):
            db.get_link("nothere")
        with pytest.raises(NotFoundError):
            db.disable_link("nothere")
