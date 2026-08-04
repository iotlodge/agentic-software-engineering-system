"""Release-readiness assembly: the final engineering summary.

"Tests passed" alone is insufficient. The release report inventories the exact
revision, the evidence bound to it, policy decisions, approvals, failures and
recoveries, and residual risks — everything a reviewer needs to trust or
reject the change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .workers import WorkerContext


def build_release_report(ctx: "WorkerContext") -> dict[str, Any]:
    run = ctx.run
    artifacts = ctx.store.get_artifacts(run.id, include_stale=False)
    evidence = [a for a in artifacts if a.kind == "evidence"]
    requirement = ctx.store.get_artifact(run.id, "requirement")
    change = ctx.store.get_artifact(run.id, "change_set")
    approvals = ctx.store.list_approvals(run_id=run.id)
    policies = ctx.store.get_policy_records(run.id)
    events = ctx.store.get_events(run.id)

    evidence_rows = [
        {
            "check": a.data.get("check", a.name),
            "passed": bool(a.data.get("passed")),
            "revision": a.revision,
            "revision_current": a.revision == run.current_revision,
            "digest": a.digest,
        }
        for a in evidence
    ]
    evidence_complete = bool(evidence_rows) and all(
        r["passed"] and r["revision_current"] for r in evidence_rows
    )

    failures = [e for e in events if e.type == "task.failed"]
    retries = [e for e in events if e.type == "retry.scheduled"]
    repairs = [e for e in events if e.type == "repair.scheduled"]
    rollbacks = [e for e in events if e.type == "rollback.executed"]

    report = {
        "run_id": run.id,
        "scenario": run.scenario,
        "request": run.request,
        "requirement_version": run.requirement_version,
        "plan_version": run.plan_version,
        "risk": run.risk,
        "base_revision": run.base_revision,
        "output_revision": run.current_revision,
        "branch": run.branch,
        "goals": (requirement.data.get("goals", []) if requirement else []),
        "acceptance_criteria": (requirement.data.get("acceptance_criteria", []) if requirement else []),
        "approved_assumptions": (requirement.data.get("proposed_assumptions", []) if requirement else []),
        "change": (change.data if change else {}),
        "evidence": evidence_rows,
        "evidence_complete": evidence_complete,
        "approvals": [
            {"scope": a.scope, "status": a.status, "approver": a.approver,
             "reason": a.reason, "risk": a.risk}
            for a in approvals
        ],
        "policy_decisions": {
            "evaluated": len(policies),
            "denied": sum(1 for p in policies if p.decision == "deny"),
            "approval_required": sum(1 for p in policies if p.decision == "approval_required"),
        },
        "recovery_activity": {
            "task_failures": len(failures),
            "retries": len(retries),
            "repairs": len(repairs),
            "rollbacks": len(rollbacks),
            "replans": run.replans,
        },
        "residual_risks": ctx.scenario.get("residual_risks", []),
        "rollback_notes": ctx.scenario.get("rollback_notes", []),
        "limitations": ctx.scenario.get("limitations", []),
    }
    _write_markdown(ctx, report)
    return report


def _write_markdown(ctx: "WorkerContext", report: dict[str, Any]) -> None:
    lines = [
        f"# Engineering summary — {report['scenario'] or report['run_id']}",
        "",
        f"- **Run:** `{report['run_id']}` (risk: {report['risk']})",
        f"- **Request:** {report['request'].strip()}",
        f"- **Requirement version:** {report['requirement_version']}"
        f" · **Plan version:** {report['plan_version']}",
        f"- **Base revision:** `{report['base_revision'][:12]}`"
        f" → **Output revision:** `{report['output_revision'][:12]}` on `{report['branch']}`",
        "",
        "## Change",
    ]
    change = report["change"]
    if change:
        lines.append(
            f"{change.get('files_changed', 0)} files, +{change.get('insertions', 0)}"
            f"/-{change.get('deletions', 0)} — {change.get('message', '')}"
        )
        lines += [f"- `{f}`" for f in change.get("files", [])[:40]]
    lines += ["", "## Evidence (bound to output revision)"]
    for row in report["evidence"]:
        mark = "PASS" if row["passed"] else "FAIL"
        cur = "current" if row["revision_current"] else "STALE REVISION"
        lines.append(f"- [{mark}] {row['check']} @ `{row['revision'][:12]}` ({cur})")
    lines.append(f"\n**Evidence complete:** {report['evidence_complete']}")
    lines += ["", "## Approvals"]
    for a in report["approvals"]:
        lines.append(f"- {a['scope']}: {a['status']}"
                     + (f" by {a['approver']} — {a['reason'] or 'no reason recorded'}" if a["approver"] else ""))
    rec = report["recovery_activity"]
    lines += [
        "", "## Governance and recovery",
        f"- Policy evaluations: {report['policy_decisions']['evaluated']}"
        f" (denied: {report['policy_decisions']['denied']},"
        f" approval-required: {report['policy_decisions']['approval_required']})",
        f"- Task failures: {rec['task_failures']} · retries: {rec['retries']}"
        f" · repairs: {rec['repairs']} · rollbacks: {rec['rollbacks']} · re-plans: {rec['replans']}",
    ]
    for title, key in (("Residual risks", "residual_risks"),
                       ("Rollback notes", "rollback_notes"),
                       ("Limitations", "limitations")):
        items = report.get(key, [])
        if items:
            lines += ["", f"## {title}"] + [f"- {i}" for i in items]
    out = ctx.workspaces.base / "runs" / ctx.run.id / "summary.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
