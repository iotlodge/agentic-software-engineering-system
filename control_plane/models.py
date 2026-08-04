"""Typed domain models for the control plane.

Every cross-stage handoff in the system is one of these records. Agents and
workers communicate exclusively through versioned, digest-addressed artifacts;
the orchestrator reasons over this structured state, never over free-form chat.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(5)}"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def digest_of(data: Any) -> str:
    """Canonical content digest used to bind approvals and evidence to exact content."""
    blob = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()[:24]


class RunStatus(StrEnum):
    CREATED = "created"
    EXECUTING = "executing"
    AWAITING_APPROVAL = "awaiting_approval"
    REPLANNING = "replanning"
    SAFE_STOPPED = "safe_stopped"
    REVIEW_READY = "review_ready"
    PROMOTED = "promoted"
    FAILED = "failed"
    DENIED = "denied"

    @property
    def terminal(self) -> bool:
        return self in {
            RunStatus.SAFE_STOPPED,
            RunStatus.REVIEW_READY,
            RunStatus.PROMOTED,
            RunStatus.FAILED,
            RunStatus.DENIED,
        }


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STALE = "stale"
    CANCELLED = "cancelled"
    WAIVED = "waived"

    @property
    def satisfies_dependency(self) -> bool:
        return self in {TaskStatus.SUCCEEDED, TaskStatus.WAIVED}


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    def __ge__(self, other: object) -> bool:  # type: ignore[override]
        order = ["low", "medium", "high", "critical"]
        if isinstance(other, RiskLevel):
            return order.index(self.value) >= order.index(other.value)
        return NotImplemented


class FailureClass(StrEnum):
    """Classification drives control behavior: retry, repair, fallback, or stop."""

    TRANSIENT = "transient"          # retry with backoff within budget
    DETERMINISTIC = "deterministic"  # do not repeat; spawn bounded repair task
    INVALID_OUTPUT = "invalid_output"  # schema violation; one constrained repair
    POLICY_DENIED = "policy_denied"  # never retry; fallback or stop
    BUDGET_EXHAUSTED = "budget_exhausted"  # safe-stop


class Decision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    APPROVAL_REQUIRED = "approval_required"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    STALE = "stale"  # subject content changed after the request was raised


class Budgets(BaseModel):
    max_attempts_per_task: int = 2
    max_repairs: int = 2
    max_replans: int = 3
    max_waves: int = 60
    max_elapsed_seconds: int = 1800


class GateSpec(BaseModel):
    """A policy checkpoint evaluated when its owning task succeeds."""

    action: str                      # e.g. "plan.adopt", "schema.migrate", "release.promote"
    resource: str = ""
    subject_artifacts: list[str] = Field(default_factory=list)  # digest-bind any approval to these


class Task(BaseModel):
    id: str = Field(default_factory=lambda: new_id("task"))
    run_id: str
    name: str                        # stable, human-readable within the run
    capability: str                  # resolved by the worker registry
    plan_version: int = 1
    params: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)  # task names
    status: TaskStatus = TaskStatus.PENDING
    risk: RiskLevel = RiskLevel.LOW
    gate: GateSpec | None = None
    gate_cleared: bool = False
    attempts: int = 0
    max_attempts: int = 2
    error: str | None = None
    failure_class: FailureClass | None = None
    input_digests: dict[str, str] = Field(default_factory=dict)
    output_artifact_ids: list[str] = Field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None


class Artifact(BaseModel):
    id: str = Field(default_factory=lambda: new_id("art"))
    run_id: str
    name: str                        # e.g. "requirement", "plan", "impact_report"
    kind: str                        # requirement | plan | impact | patch | evidence | ...
    version: int = 1
    data: dict[str, Any] = Field(default_factory=dict)
    digest: str = ""
    parents: list[str] = Field(default_factory=list)  # artifact ids (provenance edges)
    produced_by: str = ""            # task name
    revision: str = ""               # workload git revision the artifact is bound to
    stale: bool = False
    created_at: str = Field(default_factory=utcnow)

    def model_post_init(self, __context: Any) -> None:
        if not self.digest:
            self.digest = digest_of(self.data)


class Approval(BaseModel):
    id: str = Field(default_factory=lambda: new_id("apr"))
    run_id: str
    scope: str                       # assumption | plan | schema_change | release
    title: str
    description: str = ""
    risk: RiskLevel = RiskLevel.HIGH
    subject_digests: dict[str, str] = Field(default_factory=dict)  # artifact name -> digest
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_at: str = Field(default_factory=utcnow)
    resolved_at: str | None = None
    approver: str | None = None
    reason: str | None = None
    expires_at: str | None = None
    resume_task: str | None = None   # task to unblock when approved


class PolicyRecord(BaseModel):
    id: str = Field(default_factory=lambda: new_id("pol"))
    run_id: str
    action: str
    resource: str = ""
    subject: str = ""                # capability/role requesting the action
    decision: Decision
    rule: str = ""                   # matching rule id
    policy_version: str = ""
    reasons: list[str] = Field(default_factory=list)
    ts: str = Field(default_factory=utcnow)


class Event(BaseModel):
    """Append-only audit record. seq is assigned by the store."""

    seq: int | None = None
    run_id: str
    type: str
    task: str | None = None
    actor: str = "orchestrator"
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: str = Field(default_factory=utcnow)


class Run(BaseModel):
    id: str = Field(default_factory=lambda: new_id("run"))
    scenario: str = ""
    request: str = ""
    mode: str = "mock"               # mock | live
    status: RunStatus = RunStatus.CREATED
    risk: RiskLevel = RiskLevel.MEDIUM
    requirement_version: int = 0
    plan_version: int = 0
    replans: int = 0
    repairs: int = 0
    waves: int = 0
    base_revision: str = ""
    current_revision: str = ""
    branch: str = ""
    workspace_path: str = ""
    pending_approval_id: str | None = None
    stop_reason: str | None = None
    revision_applied: bool = False
    budgets: Budgets = Field(default_factory=Budgets)
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)
