"""Click analytics: fail-open recording, eventually consistent reads.

Recording never raises — analytics unavailability must not break redirects.
"""

from __future__ import annotations

from .persistence import Database


class AnalyticsSink:
    def __init__(self, db: Database):
        self.db = db
        self.loss_risk_events = 0  # clicks dropped because the sink errored

    def record(self, code: str, ts: str) -> None:
        try:
            self.db.record_click(code, ts)
        except Exception:
            self.loss_risk_events += 1

    def stats(self, code: str) -> dict:
        data = self.db.click_stats(code)
        data["consistency"] = "eventual"
        return data
