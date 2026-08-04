"""Durable run state on SQLite.

Design constraints:
- A process restart must lose nothing: every record is committed at write time.
- Events are append-only; current state is materialized in mutable tables but
  the audit trail is never overwritten.
- Connections are opened per operation (WAL mode, busy timeout) so worker
  threads never share a connection.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import (
    Approval,
    Artifact,
    Event,
    PolicyRecord,
    Run,
    Task,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, status TEXT, scenario TEXT, data TEXT,
    created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, run_id TEXT, name TEXT, status TEXT,
    plan_version INTEGER, data TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_run_name ON tasks(run_id, name);
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY, run_id TEXT, name TEXT, kind TEXT,
    version INTEGER, stale INTEGER, data TEXT);
CREATE INDEX IF NOT EXISTS idx_artifacts_run ON artifacts(run_id);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY, run_id TEXT, status TEXT, scope TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS policy_decisions (
    id TEXT PRIMARY KEY, run_id TEXT, action TEXT, decision TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, type TEXT,
    task TEXT, ts TEXT, data TEXT);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # :memory: needs a persistent connection; files get one per operation.
        self._mem = sqlite3.connect(":memory:", check_same_thread=False) if self.path == ":memory:" else None
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        if self._mem is not None:
            return self._mem
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _write(self, sql: str, params: tuple) -> None:
        conn = self._conn()
        try:
            with conn:
                conn.execute(sql, params)
        finally:
            if self._mem is None:
                conn.close()

    def _read(self, sql: str, params: tuple = ()) -> list[tuple]:
        conn = self._conn()
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            if self._mem is None:
                conn.close()

    # -- runs -----------------------------------------------------------------
    def save_run(self, run: Run) -> None:
        from .models import utcnow

        run.updated_at = utcnow()
        self._write(
            "INSERT INTO runs VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "status=excluded.status, data=excluded.data, updated_at=excluded.updated_at",
            (run.id, run.status, run.scenario, run.model_dump_json(), run.created_at, run.updated_at),
        )

    def get_run(self, run_id: str) -> Run | None:
        rows = self._read("SELECT data FROM runs WHERE id=?", (run_id,))
        return Run.model_validate_json(rows[0][0]) if rows else None

    def list_runs(self) -> list[Run]:
        rows = self._read("SELECT data FROM runs ORDER BY created_at")
        return [Run.model_validate_json(r[0]) for r in rows]

    # -- tasks ----------------------------------------------------------------
    def save_task(self, task: Task) -> None:
        self._write(
            "INSERT INTO tasks VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "status=excluded.status, plan_version=excluded.plan_version, data=excluded.data",
            (task.id, task.run_id, task.name, task.status, task.plan_version, task.model_dump_json()),
        )

    def get_tasks(self, run_id: str) -> list[Task]:
        rows = self._read("SELECT data FROM tasks WHERE run_id=? ORDER BY rowid", (run_id,))
        return [Task.model_validate_json(r[0]) for r in rows]

    def get_task(self, run_id: str, name: str) -> Task | None:
        rows = self._read("SELECT data FROM tasks WHERE run_id=? AND name=?", (run_id, name))
        return Task.model_validate_json(rows[0][0]) if rows else None

    def delete_task(self, run_id: str, name: str) -> None:
        self._write("DELETE FROM tasks WHERE run_id=? AND name=?", (run_id, name))

    # -- artifacts ------------------------------------------------------------
    def save_artifact(self, artifact: Artifact) -> None:
        self._write(
            "INSERT INTO artifacts VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "version=excluded.version, stale=excluded.stale, data=excluded.data",
            (artifact.id, artifact.run_id, artifact.name, artifact.kind,
             artifact.version, int(artifact.stale), artifact.model_dump_json()),
        )

    def get_artifacts(self, run_id: str, include_stale: bool = True) -> list[Artifact]:
        sql = "SELECT data FROM artifacts WHERE run_id=?"
        if not include_stale:
            sql += " AND stale=0"
        return [Artifact.model_validate_json(r[0]) for r in self._read(sql + " ORDER BY rowid", (run_id,))]

    def get_artifact(self, run_id: str, name: str, latest: bool = True) -> Artifact | None:
        """Latest non-stale version of a named artifact (or latest of any if all stale)."""
        arts = [a for a in self.get_artifacts(run_id) if a.name == name]
        if not arts:
            return None
        fresh = [a for a in arts if not a.stale]
        pool = fresh or arts
        return max(pool, key=lambda a: a.version)

    # -- approvals ------------------------------------------------------------
    def save_approval(self, approval: Approval) -> None:
        self._write(
            "INSERT INTO approvals VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "status=excluded.status, data=excluded.data",
            (approval.id, approval.run_id, approval.status, approval.scope, approval.model_dump_json()),
        )

    def get_approval(self, approval_id: str) -> Approval | None:
        rows = self._read("SELECT data FROM approvals WHERE id=?", (approval_id,))
        return Approval.model_validate_json(rows[0][0]) if rows else None

    def list_approvals(self, run_id: str | None = None, status: str | None = None) -> list[Approval]:
        sql, params = "SELECT data FROM approvals", []
        clauses = []
        if run_id:
            clauses.append("run_id=?"); params.append(run_id)
        if status:
            clauses.append("status=?"); params.append(status)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return [Approval.model_validate_json(r[0]) for r in self._read(sql + " ORDER BY rowid", tuple(params))]

    # -- policy decisions -----------------------------------------------------
    def save_policy_record(self, rec: PolicyRecord) -> None:
        self._write(
            "INSERT OR REPLACE INTO policy_decisions VALUES (?,?,?,?,?)",
            (rec.id, rec.run_id, rec.action, rec.decision, rec.model_dump_json()),
        )

    def get_policy_records(self, run_id: str) -> list[PolicyRecord]:
        rows = self._read("SELECT data FROM policy_decisions WHERE run_id=? ORDER BY rowid", (run_id,))
        return [PolicyRecord.model_validate_json(r[0]) for r in rows]

    # -- events (append-only) -------------------------------------------------
    def append_event(self, event: Event) -> None:
        self._write(
            "INSERT INTO events (run_id, type, task, ts, data) VALUES (?,?,?,?,?)",
            (event.run_id, event.type, event.task, event.ts,
             json.dumps({"actor": event.actor, "payload": event.payload})),
        )

    def get_events(self, run_id: str) -> list[Event]:
        rows = self._read("SELECT seq, run_id, type, task, ts, data FROM events WHERE run_id=? ORDER BY seq", (run_id,))
        out = []
        for seq, rid, typ, task, ts, data in rows:
            extra = json.loads(data)
            out.append(Event(seq=seq, run_id=rid, type=typ, task=task, ts=ts,
                             actor=extra.get("actor", ""), payload=extra.get("payload", {})))
        return out
