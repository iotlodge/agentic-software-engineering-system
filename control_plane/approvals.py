"""Durable, scoped, digest-bound human approvals.

An approval is bound to the content digests of its subject artifacts. If any
subject artifact changes after the request is raised (or after approval but
before consumption), the approval is stale and the gate closes again. A
terminal prompt is not an approval system; this survives process restarts and
records approver identity, decision, reason and timestamps.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .models import (
    Approval,
    ApprovalStatus,
    Event,
    RiskLevel,
    Run,
    RunStatus,
    utcnow,
)
from .store import Store


class ApprovalError(Exception):
    pass


class ApprovalService:
    def __init__(self, store: Store):
        self.store = store

    def request(
        self,
        run: Run,
        scope: str,
        title: str,
        description: str = "",
        risk: RiskLevel = RiskLevel.HIGH,
        subject_artifacts: list[str] | None = None,
        resume_task: str | None = None,
        expires_at: str | None = None,
    ) -> Approval:
        """Persist a pending approval and pause the run on it."""
        digests = {}
        for name in subject_artifacts or []:
            art = self.store.get_artifact(run.id, name)
            if art is not None:
                digests[name] = art.digest
        approval = Approval(
            run_id=run.id, scope=scope, title=title, description=description,
            risk=risk, subject_digests=digests, resume_task=resume_task,
            expires_at=expires_at,
        )
        self.store.save_approval(approval)
        run.pending_approval_id = approval.id
        run.status = RunStatus.AWAITING_APPROVAL
        self.store.save_run(run)
        self.store.append_event(Event(
            run_id=run.id, type="approval.requested",
            payload={"approval_id": approval.id, "scope": scope, "risk": risk,
                     "subjects": digests, "title": title},
        ))
        return approval

    def _staleness(self, approval: Approval) -> list[str]:
        """Subject artifacts whose current digest no longer matches the request."""
        changed = []
        for name, digest in approval.subject_digests.items():
            current = self.store.get_artifact(approval.run_id, name)
            if current is None or current.digest != digest:
                changed.append(name)
        return changed

    def resolve(self, approval_id: str, approve: bool, approver: str, reason: str = "") -> Approval:
        approval = self.store.get_approval(approval_id)
        if approval is None:
            raise ApprovalError(f"unknown approval {approval_id}")
        if approval.status != ApprovalStatus.PENDING:
            raise ApprovalError(f"approval {approval_id} already {approval.status}")
        if approval.expires_at and utcnow() > approval.expires_at:
            approval.status = ApprovalStatus.EXPIRED
            self.store.save_approval(approval)
            raise ApprovalError(f"approval {approval_id} expired")
        changed = self._staleness(approval)
        if changed:
            approval.status = ApprovalStatus.STALE
            self.store.save_approval(approval)
            self.store.append_event(Event(
                run_id=approval.run_id, type="approval.stale", actor=approver,
                payload={"approval_id": approval.id, "changed_subjects": changed},
            ))
            raise ApprovalError(
                f"approval {approval_id} is stale; changed subjects: {', '.join(changed)}"
            )
        approval.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
        approval.approver = approver
        approval.reason = reason
        approval.resolved_at = utcnow()
        self.store.save_approval(approval)
        self.store.append_event(Event(
            run_id=approval.run_id, type="approval.resolved", actor=approver,
            payload={"approval_id": approval.id, "decision": approval.status,
                     "scope": approval.scope, "reason": reason},
        ))
        return approval

    def consume(self, run: Run) -> Approval | None:
        """Validate the run's pending approval at resume time.

        Returns the approval if approved-and-still-valid; raises if rejected
        or stale; returns None if still pending (caller keeps waiting).
        """
        if not run.pending_approval_id:
            return None
        approval = self.store.get_approval(run.pending_approval_id)
        if approval is None:
            raise ApprovalError("pending approval record missing")
        if approval.status == ApprovalStatus.PENDING:
            return None
        if approval.status == ApprovalStatus.REJECTED:
            raise ApprovalError(f"approval {approval.id} was rejected: {approval.reason or 'no reason given'}")
        if approval.status in {ApprovalStatus.EXPIRED, ApprovalStatus.STALE}:
            raise ApprovalError(f"approval {approval.id} is {approval.status}")
        # Approved — re-verify digests at the moment of consumption.
        changed = self._staleness(approval)
        if changed:
            approval.status = ApprovalStatus.STALE
            self.store.save_approval(approval)
            raise ApprovalError(
                f"approved content changed before use ({', '.join(changed)}); re-approval required"
            )
        run.pending_approval_id = None
        self.store.save_run(run)
        return approval
