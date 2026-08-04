"""Run metrics computed from the append-only event log.

Definitions are explicit (numerator / denominator / clock) per the evaluation
plan. With a handful of demo runs these are instrument validation and
illustrative observations, not statistically significant reliability claims.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .store import Store


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def run_metrics(store: Store, run_id: str) -> dict[str, Any]:
    run = store.get_run(run_id)
    if run is None:
        return {}
    events = store.get_events(run_id)
    tasks = store.get_tasks(run_id)
    artifacts = store.get_artifacts(run_id)
    policies = store.get_policy_records(run_id)
    approvals = store.list_approvals(run_id=run_id)

    by_type: dict[str, int] = {}
    for e in events:
        by_type[e.type] = by_type.get(e.type, 0) + 1

    # Active execution: sum of task wall time. Wall clock: run creation to last event.
    active = 0.0
    intervals = []
    for t in tasks:
        if t.started_at and t.finished_at:
            start, end = _parse(t.started_at), _parse(t.finished_at)
            active += (end - start).total_seconds()
            intervals.append((start, end, t.name))
    wall = None
    if events:
        wall = (_parse(events[-1].ts) - _parse(run.created_at)).total_seconds()

    # Parallelism proof: distinct task pairs whose execution intervals overlap.
    overlaps = []
    for i in range(len(intervals)):
        for j in range(i + 1, len(intervals)):
            a, b = intervals[i], intervals[j]
            if a[0] < b[1] and b[0] < a[1]:
                overlaps.append(sorted([a[2], b[2]]))

    # Approval latency: request -> resolution, per approval.
    approval_latencies = []
    for a in approvals:
        if a.resolved_at:
            approval_latencies.append(
                round((_parse(a.resolved_at) - _parse(a.requested_at)).total_seconds(), 3))

    # First-pass verification: verification-capability tasks succeeding on attempt 1.
    verif = [t for t in tasks if t.capability in {"run_checks", "security_scan"}]
    first_pass = sum(1 for t in verif if t.status == "succeeded" and t.attempts == 1)

    total_attempts = sum(t.attempts for t in tasks)
    stale_count = sum(1 for a in artifacts if a.stale)

    return {
        "run_id": run_id,
        "scenario": run.scenario,
        "status": run.status,
        "risk": run.risk,
        "requirement_version": run.requirement_version,
        "plan_version": run.plan_version,
        "waves": run.waves,
        "tasks_total": len(tasks),
        "tasks_succeeded": sum(1 for t in tasks if t.status == "succeeded"),
        "task_attempts": total_attempts,
        "retries": by_type.get("retry.scheduled", 0),
        "repairs": by_type.get("repair.scheduled", 0),
        "rollbacks": by_type.get("rollback.executed", 0),
        "replans": run.replans,
        "recovered_tasks": by_type.get("task.recovered", 0),
        "first_pass_verification": {"passed_first": first_pass, "verification_tasks": len(verif)},
        "invalidated_work_ratio": {
            "stale_artifacts": stale_count, "total_artifacts": len(artifacts),
        },
        "policy": {
            "evaluated": len(policies),
            "denied": sum(1 for p in policies if p.decision == "deny"),
            "approval_required": sum(1 for p in policies if p.decision == "approval_required"),
        },
        "approvals": {
            "requested": len(approvals),
            "resolved": sum(1 for a in approvals if a.resolved_at),
            "latencies_s": approval_latencies,
        },
        "active_execution_s": round(active, 3),
        "wall_clock_s": round(wall, 3) if wall is not None else None,
        "parallel_task_pairs": overlaps[:20],
        "events_total": len(events),
    }


def fleet_metrics(store: Store) -> dict[str, Any]:
    runs = store.list_runs()
    terminal = [r for r in runs if r.status.terminal]
    ok = [r for r in terminal if r.status in {"review_ready", "promoted"}]
    return {
        "runs_total": len(runs),
        "runs_terminal": len(terminal),
        "run_success_rate": {
            "numerator": len(ok), "denominator": len(terminal),
            "value": round(len(ok) / len(terminal), 3) if terminal else None,
            "definition": "runs reaching review_ready or promoted / terminal runs",
        },
        "by_scenario": {
            r.scenario or r.id: r.status for r in runs
        },
        "safe_stops": sum(1 for r in terminal if r.status == "safe_stopped"),
        "denied": sum(1 for r in terminal if r.status == "denied"),
    }
