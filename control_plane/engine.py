"""The workflow orchestrator.

The engine owns the lifecycle: it schedules ready work in parallel waves,
enforces gates through policy, pauses durably on approvals, classifies
failures into retry / repair / fallback / safe-stop, materializes plans,
applies selective re-planning, and recovers deterministically from process
restarts. Agents propose or execute within their grants; evidence and policy
decide whether the workflow advances.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import graph
from .approvals import ApprovalError, ApprovalService
from .artifacts import ArtifactRegistry
from .evidence import EvidenceRunner
from .models import (
    Approval,
    Budgets,
    Decision,
    Event,
    FailureClass,
    RiskLevel,
    Run,
    RunStatus,
    Task,
    TaskStatus,
    utcnow,
)
from .policy import PolicyDenied, PolicyEngine
from .replan import Replanner, _task_from_entry, effective_scenario
from .store import Store
from .workers import (
    REGISTRY,
    DeterministicFailure,
    InvalidOutput,
    TransientFailure,
    WorkerContext,
)
from .workspace import WorkspaceManager

BOOTSTRAP = [
    {"name": "normalize", "capability": "normalize_requirement", "depends_on": []},
    {"name": "analyze", "capability": "analyze_repository", "depends_on": ["normalize"]},
    {"name": "create_plan", "capability": "create_plan", "depends_on": ["analyze"]},
]


class Engine:
    def __init__(
        self,
        project_root: str | Path,
        store: Store,
        policy: PolicyEngine,
        workspaces: WorkspaceManager,
        registry: ArtifactRegistry,
        approvals: ApprovalService,
        evidence: EvidenceRunner,
        backoff: float = 0.25,
        max_parallel: int = 4,
        agents: Any = None,
    ):
        self.project_root = Path(project_root)
        self.store = store
        self.policy = policy
        self.workspaces = workspaces
        self.registry = registry
        self.approvals = approvals
        self.evidence = evidence
        self.replanner = Replanner(store, registry, approvals)
        self.backoff = backoff
        self.max_parallel = max_parallel
        self.agents = agents

    # ------------------------------------------------------------------ start
    def start(self, scenario: dict[str, Any], mode: str = "mock") -> Run:
        run = Run(
            scenario=scenario.get("name", ""),
            request=scenario.get("request", "").strip(),
            mode=mode,
            budgets=Budgets(**scenario.get("budgets", {})),
        )
        self.store.save_run(run)
        self.store.append_event(Event(
            run_id=run.id, type="run.created",
            payload={"scenario": run.scenario, "mode": mode},
        ))
        self.workspaces.create(run)
        for entry in BOOTSTRAP:
            task = Task(
                run_id=run.id, name=entry["name"], capability=entry["capability"],
                depends_on=entry["depends_on"], plan_version=0,
                max_attempts=run.budgets.max_attempts_per_task,
            )
            self.store.save_task(task)
        return run

    # ------------------------------------------------------------------- tick
    def tick(self, run_id: str, scenario: dict[str, Any]) -> Run:
        """Advance the run until it pauses (approval), stops, or completes.
        Safe to call repeatedly; state is loaded from the durable store."""
        run = self.store.get_run(run_id)
        if run is None:
            raise ValueError(f"unknown run {run_id}")
        if run.status.terminal:
            return run

        ctx = self._ctx(run, scenario)

        if run.status == RunStatus.AWAITING_APPROVAL:
            outcome = self._resume_from_approval(run, ctx)
            if outcome != "continue":
                return self.store.get_run(run.id)

        if run.status in {RunStatus.CREATED, RunStatus.AWAITING_APPROVAL, RunStatus.REPLANNING}:
            run.status = RunStatus.EXECUTING
            self.store.save_run(run)

        while True:
            run.waves += 1
            self.store.save_run(run)
            if run.waves > run.budgets.max_waves:
                return self._safe_stop(run, "wave budget exhausted")
            if self._elapsed_seconds(run) > run.budgets.max_elapsed_seconds:
                return self._safe_stop(run, "elapsed-time budget exhausted")

            scenario_eff = effective_scenario(scenario, run)
            ctx = self._ctx(run, scenario_eff)

            tasks = self._active_tasks(run)
            for t in graph.running_tasks(tasks):
                # A RUNNING task at loop entry means the process died mid-task.
                t.status = TaskStatus.PENDING
                t.attempts = max(0, t.attempts - 1)  # recovery re-run is free
                self.store.save_task(t)
                self.store.append_event(Event(
                    run_id=run.id, type="task.recovered", task=t.name,
                    payload={"note": "process restart during execution; task re-queued"},
                ))
            # Materialize before scheduling: a plan whose gate was cleared via
            # approval must expand into tasks on the resume path too, not only
            # after an in-loop wave.
            self._materialize_plan(run, ctx)
            tasks = self._active_tasks(run)

            ready = graph.ready_tasks(tasks)
            if not ready:
                unfinished = graph.unfinished(tasks)
                failed = [t for t in tasks if t.status == TaskStatus.FAILED]
                if failed:
                    return self._fail(run, "blocked by failed tasks: "
                                      + ", ".join(t.name for t in failed))
                if unfinished:
                    return self._fail(run, "scheduling deadlock: pending tasks "
                                      + ", ".join(t.name for t in unfinished))
                return self._finalize(run, ctx)

            self._execute_wave(run, ready, ctx)

            if self._handle_failures(run, ctx, scenario_eff):
                return self.store.get_run(run.id)
            if self._process_gates(run, ctx):
                return self.store.get_run(run.id)
            self._materialize_plan(run, ctx)
            if self._maybe_scripted_revision(run, ctx, scenario_eff):
                return self.store.get_run(run.id)

    # ---------------------------------------------------------------- helpers
    def _ctx(self, run: Run, scenario: dict[str, Any]) -> WorkerContext:
        return WorkerContext(
            run=run, store=self.store, registry=self.registry,
            workspaces=self.workspaces, evidence=self.evidence,
            policy=self.policy, scenario=scenario,
            project_root=self.project_root, agents=self.agents,
        )

    def _active_tasks(self, run: Run) -> list[Task]:
        return [t for t in self.store.get_tasks(run.id)
                if t.status not in {TaskStatus.CANCELLED, TaskStatus.STALE}]

    @staticmethod
    def _elapsed_seconds(run: Run) -> float:
        started = datetime.fromisoformat(run.created_at)
        return (datetime.now(timezone.utc) - started).total_seconds()

    # ----------------------------------------------------------------- resume
    def _resume_from_approval(self, run: Run, ctx: WorkerContext) -> str:
        try:
            approval = self.approvals.consume(run)
        except ApprovalError as exc:
            run.status = RunStatus.DENIED
            run.stop_reason = str(exc)
            self.store.save_run(run)
            self.store.append_event(Event(
                run_id=run.id, type="run.denied", payload={"reason": str(exc)},
            ))
            return "denied"
        if approval is None:
            return "waiting"  # still pending; stay paused
        return self._after_approval(run, approval, ctx)

    def _after_approval(self, run: Run, approval: Approval, ctx: WorkerContext) -> str:
        if approval.resume_task:
            task = self.store.get_task(run.id, approval.resume_task)
            if task is not None:
                task.gate_cleared = True
                self.store.save_task(task)
                self.store.append_event(Event(
                    run_id=run.id, type="gate.passed", task=task.name,
                    payload={"action": approval.scope, "via": "human_approval",
                             "approver": approval.approver},
                ))
        if approval.scope == "release.promote":
            if ctx.scenario.get("promote_on_approval", True):
                merged = self.workspaces.promote(run)
                run.status = RunStatus.PROMOTED
                run.stop_reason = None
                self.store.save_run(run)
                self.store.append_event(Event(
                    run_id=run.id, type="run.completed",
                    payload={"status": "promoted", "merged_revision": merged},
                ))
            else:
                run.status = RunStatus.REVIEW_READY
                self.store.save_run(run)
                self.store.append_event(Event(
                    run_id=run.id, type="run.completed", payload={"status": "review_ready"},
                ))
            return "terminal"
        return "continue"

    # ------------------------------------------------------------------ waves
    def _execute_wave(self, run: Run, ready: list[Task], ctx: WorkerContext) -> None:
        self.store.append_event(Event(
            run_id=run.id, type="wave.started",
            payload={"wave": run.waves, "tasks": [t.name for t in ready]},
        ))

        def exec_one(task: Task) -> None:
            task.attempts += 1
            task.status = TaskStatus.RUNNING
            task.started_at = utcnow()
            self.store.save_task(task)
            self.store.append_event(Event(
                run_id=run.id, type="task.started", task=task.name,
                payload={"attempt": task.attempts, "capability": task.capability},
            ))
            try:
                worker = REGISTRY[task.capability]
                result = worker(ctx, task)
                task.status = TaskStatus.SUCCEEDED
                task.error = None
                task.failure_class = None
                task.finished_at = utcnow()
                self.store.save_task(task)
                self.store.append_event(Event(
                    run_id=run.id, type="task.completed", task=task.name,
                    payload={"result": result},
                ))
            except TransientFailure as exc:
                self._mark_failed(run, task, FailureClass.TRANSIENT, exc)
            except PolicyDenied as exc:
                self._mark_failed(run, task, FailureClass.POLICY_DENIED, exc)
            except InvalidOutput as exc:
                self._mark_failed(run, task, FailureClass.INVALID_OUTPUT, exc)
            except (DeterministicFailure, Exception) as exc:
                self._mark_failed(run, task, FailureClass.DETERMINISTIC, exc)

        with ThreadPoolExecutor(max_workers=min(self.max_parallel, len(ready))) as pool:
            list(pool.map(exec_one, ready))

    def _mark_failed(self, run: Run, task: Task, cls: FailureClass, exc: Exception) -> None:
        task.status = TaskStatus.FAILED
        task.failure_class = cls
        task.error = str(exc)
        task.finished_at = utcnow()
        self.store.save_task(task)
        self.store.append_event(Event(
            run_id=run.id, type="task.failed", task=task.name,
            payload={"class": cls, "error": str(exc), "attempt": task.attempts},
        ))

    # --------------------------------------------------------------- failures
    def _handle_failures(self, run: Run, ctx: WorkerContext, scenario: dict[str, Any]) -> bool:
        """Route each failed task by failure class. Returns True if the run
        went terminal (safe-stop / fail / rollback)."""
        for task in [t for t in self._active_tasks(run) if t.status == TaskStatus.FAILED]:
            cls = task.failure_class or FailureClass.DETERMINISTIC

            if cls in {FailureClass.TRANSIENT, FailureClass.INVALID_OUTPUT}:
                if task.attempts < task.max_attempts:
                    delay = round(self.backoff * task.attempts, 3)
                    self.store.append_event(Event(
                        run_id=run.id, type="retry.scheduled", task=task.name,
                        payload={"next_attempt": task.attempts + 1, "backoff_s": delay,
                                 "class": cls},
                    ))
                    time.sleep(delay)
                    task.status = TaskStatus.PENDING
                    self.store.save_task(task)
                    continue
                self._safe_stop(run, f"retry budget exhausted on task {task.name!r} "
                                f"({task.attempts} attempts): {task.error}")
                return True

            if cls == FailureClass.POLICY_DENIED:
                fallback = task.params.get("fallback")
                if fallback:
                    task.capability = fallback.get("capability", task.capability)
                    task.params = {**task.params, **fallback.get("params", {})}
                    task.params.pop("fallback", None)
                    task.status = TaskStatus.PENDING
                    task.attempts = 0
                    self.store.save_task(task)
                    self.store.append_event(Event(
                        run_id=run.id, type="fallback.selected", task=task.name,
                        payload={"capability": task.capability,
                                 "reason": "policy denial routed to allowed approach"},
                    ))
                    continue
                self._safe_stop(run, f"policy denied task {task.name!r} with no "
                                f"allowed fallback: {task.error}")
                return True

            # deterministic
            repair_spec = scenario.get("repair", {}).get(task.name)
            if repair_spec and run.repairs < run.budgets.max_repairs:
                run.repairs += 1
                n = run.repairs
                repair_name = f"repair_{task.name}_{n}"
                reintegrate_name = f"reintegrate_{n}"
                self.store.save_task(Task(
                    run_id=run.id, name=repair_name, capability="apply_seed",
                    plan_version=run.plan_version,
                    params={"seed": repair_spec["seed"],
                            "artifact": f"repair_patch_{n}",
                            "description": repair_spec.get("description", ""),
                            "parents": repair_spec.get("parents", ["plan"])},
                    risk=RiskLevel.MEDIUM,
                    max_attempts=run.budgets.max_attempts_per_task,
                ))
                self.store.save_task(Task(
                    run_id=run.id, name=reintegrate_name, capability="integrate_change",
                    plan_version=run.plan_version,
                    depends_on=[repair_name],
                    params={"message": f"fix: repair {task.name} — "
                                       f"{repair_spec.get('description', 'scoped repair')}"},
                    max_attempts=run.budgets.max_attempts_per_task,
                ))
                if reintegrate_name not in task.depends_on:
                    task.depends_on = [*task.depends_on, reintegrate_name]
                task.status = TaskStatus.PENDING
                task.attempts = 0
                task.error = None
                task.failure_class = None
                self.store.save_task(task)
                self.store.save_run(run)
                self.store.append_event(Event(
                    run_id=run.id, type="repair.scheduled", task=task.name,
                    payload={"repair_task": repair_name, "reintegrate_task": reintegrate_name,
                             "repair_number": n, "budget": run.budgets.max_repairs,
                             "description": repair_spec.get("description", "")},
                ))
                continue

            if task.params.get("on_failure") == "rollback":
                self.workspaces.rollback(run, run.base_revision,
                                         reason=f"failed integration: {task.name}")
                self._fail(run, f"task {task.name!r} failed; worktree rolled back "
                           f"to base revision: {task.error}")
                return True

            if repair_spec:
                self._safe_stop(run, f"repair budget exhausted ({run.budgets.max_repairs}) "
                                f"while repairing {task.name!r}: {task.error}")
                return True
            self._safe_stop(run, f"deterministic failure in {task.name!r} with no "
                            f"repair path: {task.error}")
            return True
        return False

    # ------------------------------------------------------------------ gates
    def _process_gates(self, run: Run, ctx: WorkerContext) -> bool:
        """Evaluate gates on newly succeeded tasks. Returns True if paused/denied."""
        for task in self._active_tasks(run):
            if task.status != TaskStatus.SUCCEEDED or task.gate is None or task.gate_cleared:
                continue
            risk = run.risk if task.gate.action == "plan.adopt" else task.risk
            record = self.policy.evaluate(
                run.id, task.gate.action, resource=task.gate.resource,
                subject=task.name, risk=risk,
            )
            if record.decision == Decision.ALLOW:
                task.gate_cleared = True
                self.store.save_task(task)
                self.store.append_event(Event(
                    run_id=run.id, type="gate.passed", task=task.name,
                    payload={"action": task.gate.action, "via": "policy_allow",
                             "rule": record.rule},
                ))
                continue
            if record.decision == Decision.APPROVAL_REQUIRED:
                self.approvals.request(
                    run, scope=task.gate.action,
                    title=f"[{run.scenario or run.id}] {task.gate.action} — {task.name}",
                    description="; ".join(record.reasons),
                    risk=risk,
                    subject_artifacts=task.gate.subject_artifacts,
                    resume_task=task.name,
                )
                return True
            run.status = RunStatus.DENIED
            run.stop_reason = f"policy denied gate {task.gate.action} on {task.name}"
            self.store.save_run(run)
            self.store.append_event(Event(
                run_id=run.id, type="run.denied",
                payload={"gate": task.gate.action, "task": task.name,
                         "reasons": record.reasons},
            ))
            return True
        return False

    # ----------------------------------------------------------- plan expand
    def _materialize_plan(self, run: Run, ctx: WorkerContext) -> None:
        plan_task = self.store.get_task(run.id, "create_plan")
        if plan_task is None or plan_task.status != TaskStatus.SUCCEEDED or not plan_task.gate_cleared:
            return
        plan_art = self.store.get_artifact(run.id, "plan")
        if plan_art is None:
            return
        entries = plan_art.data.get("tasks", [])
        entry_names = {e["name"] for e in entries}
        candidates = [_task_from_entry(run, e) for e in entries]
        graph.validate(candidates)  # acyclic, no unknown deps — before persisting
        for entry, candidate in zip(entries, candidates):
            existing = self.store.get_task(run.id, entry["name"])
            if existing is None:
                self.store.save_task(candidate)
        # Tasks from a previous plan version that the new plan no longer wants.
        for task in self.store.get_tasks(run.id):
            if (task.plan_version > 0 and task.name not in entry_names
                    and not task.name.startswith(("repair_", "reintegrate_"))
                    and task.status == TaskStatus.PENDING):
                task.status = TaskStatus.CANCELLED
                self.store.save_task(task)
                self.store.append_event(Event(
                    run_id=run.id, type="task.cancelled", task=task.name,
                    payload={"reason": "not part of current plan version"},
                ))

    # -------------------------------------------------------------- revision
    def _maybe_scripted_revision(self, run: Run, ctx: WorkerContext,
                                 scenario: dict[str, Any]) -> bool:
        revision = scenario.get("revision")
        if not revision or run.revision_applied:
            return False
        trigger = self.store.get_task(run.id, revision.get("after_task", ""))
        if trigger is None or trigger.status != TaskStatus.SUCCEEDED:
            return False
        return self.revise(run, revision, actor=revision.get("actor", "evaluator-script"))

    def revise(self, run: Run, revision: dict[str, Any], actor: str = "human") -> bool:
        """Apply an upstream revision. Returns True if the run paused or stopped."""
        try:
            self.replanner.revise(run, revision, actor=actor)
        except RuntimeError as exc:
            self._safe_stop(run, str(exc))
            return True
        return run.status == RunStatus.AWAITING_APPROVAL

    # --------------------------------------------------------------- terminal
    def _finalize(self, run: Run, ctx: WorkerContext) -> Run:
        run.status = RunStatus.REVIEW_READY
        run.stop_reason = None
        self.store.save_run(run)
        self.store.append_event(Event(
            run_id=run.id, type="run.completed", payload={"status": "review_ready"},
        ))
        return run

    def _safe_stop(self, run: Run, reason: str) -> Run:
        note = self.workspaces.preserve_for_recovery(run, reason)
        run.status = RunStatus.SAFE_STOPPED
        run.stop_reason = reason
        self.store.save_run(run)
        self.store.append_event(Event(
            run_id=run.id, type="run.safe_stopped",
            payload={"reason": reason, "recovery_note": note,
                     "worktree_preserved": run.workspace_path},
        ))
        return run

    def _fail(self, run: Run, reason: str) -> Run:
        run.status = RunStatus.FAILED
        run.stop_reason = reason
        self.store.save_run(run)
        self.store.append_event(Event(
            run_id=run.id, type="run.failed", payload={"reason": reason},
        ))
        return run
