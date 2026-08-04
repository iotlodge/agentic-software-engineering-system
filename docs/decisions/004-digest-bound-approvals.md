# ADR 004 — Approvals bound to content digests, validated twice

**Status:** accepted

## Context

A terminal `input()` prompt is not an approval system: it is not durable, not
scoped, not auditable, and — critically — not bound to *what* was approved.

## Decision

An Approval is a persisted record with scope, risk, approver, reason,
timestamps, optional expiry, and `subject_digests`: a snapshot of the content
digests of its subject artifacts. Staleness is enforced at two points:

1. **Resolution** — approving a request whose subjects changed fails and marks
   the approval stale.
2. **Consumption** — an already-approved record is re-verified when the engine
   resumes; if content changed in between, resumption is refused and the gate
   re-opens.

A re-plan that touches a pending approval's subjects proactively marks it stale.

## Consequences

- "Approve, then quietly change the diff" is structurally impossible.
- Approval provenance doubles as audit evidence in the release report.
- The cost is that benign upstream republishing invalidates approvals; that is
  the intended behavior (renewed authority where risk changed), demonstrated in
  the ambiguous scenario.
