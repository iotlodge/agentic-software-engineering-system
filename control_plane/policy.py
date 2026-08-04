"""Policy engine: allow / deny / approval_required as code and data.

Rules live in YAML (see ``policies/default.yaml``). Evaluation is first-match
over ordered rules; anything unmatched falls to the default decision (deny).
Every evaluation is persisted as a PolicyRecord so the audit trail shows which
rule fired, under which policy version, and why.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

import yaml

from .models import Decision, PolicyRecord, RiskLevel
from .store import Store


class PolicyDenied(Exception):
    def __init__(self, record: PolicyRecord):
        self.record = record
        super().__init__(f"policy denied {record.action} on {record.resource!r}: {', '.join(record.reasons)}")


class PolicyEngine:
    def __init__(self, policy_path: str | Path, store: Store):
        self.path = Path(policy_path)
        self.store = store
        doc = yaml.safe_load(self.path.read_text())
        self.version = str(doc.get("version", "0"))
        self.default = Decision(doc.get("default", "deny"))
        self.rules: list[dict[str, Any]] = doc.get("rules", [])

    def evaluate(
        self,
        run_id: str,
        action: str,
        resource: str = "",
        subject: str = "",
        risk: RiskLevel = RiskLevel.LOW,
        context: dict[str, Any] | None = None,
    ) -> PolicyRecord:
        context = context or {}
        matched_rule, decision, reasons = None, self.default, ["no_matching_rule"]
        for rule in self.rules:
            if not fnmatch.fnmatch(action, rule.get("action", "*")):
                continue
            if "resource" in rule and not fnmatch.fnmatch(resource, rule["resource"]):
                continue
            if "subject" in rule and not fnmatch.fnmatch(subject, rule["subject"]):
                continue
            if "when_risk_at_least" in rule and not (risk >= RiskLevel(rule["when_risk_at_least"])):
                continue
            gte = rule.get("when_gte")
            if gte and not (float(context.get(gte["key"], 0)) >= float(gte["value"])):
                continue
            matched_rule = rule
            decision = Decision(rule["decision"])
            reasons = list(rule.get("reasons", []))
            break
        record = PolicyRecord(
            run_id=run_id,
            action=action,
            resource=resource,
            subject=subject,
            decision=decision,
            rule=matched_rule.get("id", "") if matched_rule else "default",
            policy_version=self.version,
            reasons=reasons,
        )
        self.store.save_policy_record(record)
        return record

    def enforce(self, run_id: str, action: str, **kwargs: Any) -> PolicyRecord:
        """Evaluate and raise on deny. approval_required is returned to the caller
        so the orchestrator can pause at a gate rather than crash."""
        record = self.evaluate(run_id, action, **kwargs)
        if record.decision == Decision.DENY:
            raise PolicyDenied(record)
        return record
