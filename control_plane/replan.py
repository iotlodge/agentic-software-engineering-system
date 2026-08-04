"""Selective re-planning.

When an upstream contract changes (a requirement facet such as the analytics
consistency target), the system must invalidate only affected descendants,
preserve unaffected work, produce a new plan version, and renew approvals
whose subjects changed. Restarting everything is wasteful; silently
continuing is unsafe.
"""

from __future__ import annotations

import copy
from typing import Any

from . import graph
from .approvals import ApprovalService
from .artifacts import ArtifactRegistry
from .models import Event, RiskLevel, Run, RunStatus, Task, TaskStatus
from .store import Store


def effective_scenario(scenario: dict[str, Any], run: Run) -> dict[str, Any]:
    """Scenario spec with the scripted revision merged in once it has been
    applied. Deterministic across process restarts because revision_applied
    is persisted on the run."""
    if not run.revision_applied or "revision" not in scenario:
        return scenario
    merged = copy.deepcopy(scenario)
    revision = merged["revision"]
    if "requirement" in revision:
        merged.setdefault("requirement", {}).update(revision["requirement"])
    upserts = {t["name"]: t for t in revision.get("plan_upserts", [])}
    plan = merged.get("plan", [])
    for i, entry in enumerate(plan):
        if entry["name"] in upserts:
            plan[i] = upserts.pop(entry["name"])
    plan.extend(upserts.values())
    return merged


class Replanner:
    def __init__(self, store: Store, registry: ArtifactRegistry, approvals: ApprovalService):
        self.store = store
        self.registry = registry
        self.approvals = approvals

    def revise(self, run: Run, revision: dict[str, Any], actor: str = "human") -> dict[str, Any]:
        """Apply an upstream requirement revision and selectively invalidate.

        revision spec:
          contract: name of the requirement-facet artifact that changed
          data: new content for that artifact
          reason: human-readable cause
          plan_upserts: task definitions to update/add in the new plan version
          require_reapproval: pause for renewed approval of the revised plan
        """
        if run.replans >= run.budgets.max_replans:
            raise RuntimeError(f"re-plan budget exhausted ({run.budgets.max_replans})")

        contract_name = revision["contract"]
        # A pending approval whose subject is about to change is stale by
        # definition — close that gate before touching upstream state.
        if run.pending_approval_id:
            from .models import ApprovalStatus
            pending = self.store.get_approval(run.pending_approval_id)
            if pending is not None and pending.status == ApprovalStatus.PENDING:
                pending.status = ApprovalStatus.STALE
                self.store.save_approval(pending)
                self.store.append_event(Event(
                    run_id=run.id, type="approval.stale",
                    payload={"approval_id": pending.id,
                             "cause": f"upstream revision of {contract_name}"},
                ))
            run.pending_approval_id = None
        # 1. Persist the new upstream version.
        new_contract = self.registry.publish(
            run, contract_name, "contract", revision["data"],
            parents=["requirement"], produced_by=f"revision:{actor}",
        )
        run.requirement_version += 1
        self.store.append_event(Event(
            run_id=run.id, type="requirement.revised", actor=actor,
            payload={"contract": contract_name, "version": new_contract.version,
                     "requirement_version": run.requirement_version,
                     "reason": revision.get("reason", "")},
        ))

        # 2. Traverse provenance; mark descendants stale.
        stale_artifacts = self.registry.invalidate_descendants(run, contract_name)

        # 3. Recompute affected tasks: producers of stale artifacts plus their
        #    task-graph descendants. Everything else keeps its status/evidence.
        tasks = self.store.get_tasks(run.id)
        producers = {
            t.name for t in tasks
            for a in self.store.get_artifacts(run.id)
            if a.stale and a.produced_by == t.name and a.name in stale_artifacts
        }
        affected = producers | graph.descendants(tasks, producers)
        invalidated = []
        for t in tasks:
            if t.name in affected and t.status != TaskStatus.PENDING:
                t.status = TaskStatus.PENDING
                t.attempts = 0
                t.gate_cleared = False
                t.error = None
                t.failure_class = None
                self.store.save_task(t)
                invalidated.append(t.name)
                self.store.append_event(Event(
                    run_id=run.id, type="task.invalidated", task=t.name,
                    payload={"cause": contract_name},
                ))

        # 4. New plan version with the structural upserts.
        run.replans += 1
        run.plan_version += 1
        previous_plan = self.store.get_artifact(run.id, "plan")
        plan_tasks = copy.deepcopy(previous_plan.data.get("tasks", [])) if previous_plan else []
        upserts = {t["name"]: t for t in revision.get("plan_upserts", [])}
        for i, entry in enumerate(plan_tasks):
            if entry["name"] in upserts:
                plan_tasks[i] = upserts.pop(entry["name"])
        new_entries = list(upserts.values())
        plan_tasks.extend(new_entries)
        self.registry.publish(
            run, "plan", "plan",
            {"version": run.plan_version, "tasks": plan_tasks,
             "risk": run.risk, "requirement_version": run.requirement_version},
            parents=["requirement", "impact_report"], produced_by=f"revision:{actor}",
        )
        for entry in new_entries:
            existing = self.store.get_task(run.id, entry["name"])
            if existing is None:
                self.store.save_task(_task_from_entry(run, entry))
        # Existing tasks whose definition changed get their new params.
        for t in self.store.get_tasks(run.id):
            for entry in plan_tasks:
                if entry["name"] == t.name and t.name in affected:
                    t.capability = entry.get("capability", t.capability)
                    t.params = entry.get("params", t.params)
                    t.depends_on = entry.get("depends_on", t.depends_on)
                    t.risk = RiskLevel(entry.get("risk", t.risk))
                    t.plan_version = run.plan_version
                    self.store.save_task(t)
        self.store.append_event(Event(
            run_id=run.id, type="plan.revised", actor=actor,
            payload={"plan_version": run.plan_version,
                     "invalidated_tasks": invalidated,
                     "added_tasks": [e["name"] for e in new_entries],
                     "preserved_tasks": [t.name for t in tasks if t.name not in affected
                                         and t.status == TaskStatus.SUCCEEDED]},
        ))
        run.revision_applied = True
        self.store.save_run(run)

        # 5. Renewed authority where the contract changed.
        if revision.get("require_reapproval", True):
            self.approvals.request(
                run, scope="plan.revised",
                title=f"Re-approve plan v{run.plan_version} after {contract_name} change",
                description=revision.get("reason", ""),
                risk=run.risk,
                subject_artifacts=["plan", contract_name],
            )
        else:
            run.status = RunStatus.EXECUTING
            self.store.save_run(run)
        return {"invalidated_tasks": invalidated, "stale_artifacts": stale_artifacts,
                "added_tasks": [e["name"] for e in new_entries]}


def _task_from_entry(run: Run, entry: dict[str, Any]) -> Task:
    from .models import GateSpec

    gate = None
    if "gate" in entry:
        gate = GateSpec(
            action=entry["gate"]["action"],
            resource=entry["gate"].get("resource", ""),
            subject_artifacts=entry["gate"].get("subject_artifacts", []),
        )
    return Task(
        run_id=run.id,
        name=entry["name"],
        capability=entry["capability"],
        plan_version=run.plan_version,
        params=entry.get("params", {}),
        depends_on=entry.get("depends_on", []),
        risk=RiskLevel(entry.get("risk", "low")),
        gate=gate,
        max_attempts=entry.get("max_attempts", run.budgets.max_attempts_per_task),
    )
