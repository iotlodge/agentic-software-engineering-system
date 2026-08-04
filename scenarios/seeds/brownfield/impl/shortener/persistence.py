"""SQLite persistence: the system of record.

v2: adds expiry, optimistic-concurrency versioning and an audit trail for
administrative mutations. Additive migration only — rows created before
migration 002 read back with no expiry and version 1.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .domain import Link, generate_code, utcnow

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
MAX_CODE_ATTEMPTS = 5


class NotFoundError(LookupError):
    pass


class ConflictError(RuntimeError):
    """Optimistic-concurrency conflict: the link changed since it was read."""


class CodeAllocationError(RuntimeError):
    pass


class Database:
    def __init__(self, path: str | None = None):
        self.path = path or ":memory:"
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()

    def migrate(self, migrations_dir: str | Path | None = None) -> list[str]:
        directory = Path(migrations_dir) if migrations_dir else MIGRATIONS_DIR
        with self.lock, self.conn:
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
            applied = {r["version"] for r in
                       self.conn.execute("SELECT version FROM schema_migrations")}
            newly = []
            for script in sorted(directory.glob("*.sql")):
                if script.name in applied:
                    continue
                self.conn.executescript(script.read_text())
                self.conn.execute(
                    "INSERT INTO schema_migrations VALUES (?, ?)",
                    (script.name, utcnow().isoformat()))
                newly.append(script.name)
            return newly

    def ping(self) -> bool:
        try:
            with self.lock:
                self.conn.execute("SELECT 1")
            return True
        except sqlite3.Error:
            return False

    def create_link(self, url: str, expires_at: str | None = None,
                    idempotency_key: str | None = None) -> tuple[Link, bool]:
        """Insert a new link. Returns (link, created); created is False when
        an idempotency key replays an earlier create."""
        with self.lock, self.conn:
            if idempotency_key:
                row = self.conn.execute(
                    "SELECT code FROM idempotency_keys WHERE key = ?",
                    (idempotency_key,)).fetchone()
                if row:
                    return self._get_locked(row["code"]), False
            created_at = utcnow().isoformat()
            for _ in range(MAX_CODE_ATTEMPTS):
                code = generate_code()
                try:
                    self.conn.execute(
                        "INSERT INTO links (code, url, created_at, expires_at) "
                        "VALUES (?, ?, ?, ?)",
                        (code, url, created_at, expires_at))
                    break
                except sqlite3.IntegrityError:
                    continue  # collision: regenerate and retry
            else:
                raise CodeAllocationError(
                    f"could not allocate a unique code in {MAX_CODE_ATTEMPTS} attempts")
            if idempotency_key:
                self.conn.execute(
                    "INSERT INTO idempotency_keys VALUES (?, ?)",
                    (idempotency_key, code))
            return self._get_locked(code), True

    def _get_locked(self, code: str) -> Link:
        row = self.conn.execute(
            "SELECT * FROM links WHERE code = ?", (code,)).fetchone()
        if row is None:
            raise NotFoundError(code)
        keys = row.keys()
        return Link(
            code=row["code"], url=row["url"], created_at=row["created_at"],
            expires_at=row["expires_at"] if "expires_at" in keys else None,
            disabled=bool(row["disabled"]),
            version=row["version"] if "version" in keys else 1,
        )

    def get_link(self, code: str) -> Link:
        with self.lock:
            return self._get_locked(code)

    def update_destination(self, code: str, new_url: str, expected_version: int) -> Link:
        """Optimistic concurrency: fails with ConflictError on a lost update."""
        with self.lock, self.conn:
            old = self._get_locked(code)
            cursor = self.conn.execute(
                "UPDATE links SET url = ?, version = version + 1 "
                "WHERE code = ? AND version = ?",
                (new_url, code, expected_version))
            if cursor.rowcount == 0:
                raise ConflictError(
                    f"link {code} is at version {old.version}, not {expected_version}")
            self.conn.execute(
                "INSERT INTO link_audit (code, action, old_url, new_url, ts) "
                "VALUES (?, 'update_destination', ?, ?, ?)",
                (code, old.url, new_url, utcnow().isoformat()))
            return self._get_locked(code)

    def disable_link(self, code: str) -> None:
        with self.lock, self.conn:
            cursor = self.conn.execute(
                "UPDATE links SET disabled = 1 WHERE code = ?", (code,))
            if cursor.rowcount == 0:
                raise NotFoundError(code)
            self.conn.execute(
                "INSERT INTO link_audit (code, action, ts) VALUES (?, 'disable', ?)",
                (code, utcnow().isoformat()))

    def record_click(self, code: str, ts: str) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO click_events (code, ts) VALUES (?, ?)", (code, ts))

    def click_stats(self, code: str) -> dict:
        with self.lock:
            row = self.conn.execute(
                "SELECT COUNT(*) AS clicks, MAX(ts) AS last_click "
                "FROM click_events WHERE code = ?", (code,)).fetchone()
        return {"clicks": row["clicks"], "last_click": row["last_click"]}
