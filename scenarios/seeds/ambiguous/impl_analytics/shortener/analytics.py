"""Click analytics: asynchronous by contract, bounded delay, fail-open.

Redirect latency must never depend on aggregate computation. Events buffer in
memory and flush to the database in batches; the visibility contract is
"aggregates reflect a click within ``flush_interval`` seconds" (default 60s,
per the approved consistency assumption). Recording never raises — if the
sink is unhealthy the redirect proceeds and a loss-risk counter increments.
"""

from __future__ import annotations

import threading
import time
from collections import deque

from .persistence import Database


class AnalyticsSink:
    DEFAULT_FLUSH_INTERVAL = 60.0  # seconds; the visibility contract bound

    def __init__(self, db: Database, flush_interval: float | None = None):
        self.db = db
        self.flush_interval = (self.DEFAULT_FLUSH_INTERVAL
                               if flush_interval is None else flush_interval)
        self._pending: deque[tuple[str, str]] = deque()
        self._lock = threading.Lock()
        self._last_flush = time.monotonic()
        self.loss_risk_events = 0  # clicks dropped because the sink errored

    def record(self, code: str, ts: str) -> None:
        """Fail-open: analytics unavailability must not break redirects."""
        try:
            with self._lock:
                self._pending.append((code, ts))
                due = time.monotonic() - self._last_flush >= self.flush_interval
            if due:
                self.flush()
        except Exception:
            self.loss_risk_events += 1

    def flush(self) -> int:
        """Drain the buffer into the database. Returns events written."""
        with self._lock:
            batch = list(self._pending)
            self._pending.clear()
            self._last_flush = time.monotonic()
        if batch:
            self.db.record_clicks(batch)
        return len(batch)

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._pending)

    def stats(self, code: str) -> dict:
        """Aggregate read. Flushes first, so visibility delay is bounded by
        the caller's request rather than waiting out the interval."""
        self.flush()
        data = self.db.click_stats(code)
        data["consistency"] = f"eventual, visible within {self.flush_interval:g}s"
        return data
