"""Capability workers.

A worker is a bounded executor: defined objective, scoped context, permitted
tools, typed output (published artifacts), and an escalation path (typed
exceptions the engine classifies). Workers propose and produce; the engine and
policy decide whether the workflow advances.

In mock mode the workers are deterministic (seed-driven), which is what makes
the orchestration behavior testable in CI independent of model variability.
In live mode the same capabilities are backed by LLM role agents behind the
same contracts (see agents/roles.py).
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .artifacts import ArtifactRegistry
from .evidence import EvidenceRunner
from .models import Event, GateSpec, RiskLevel, Run, Task
from .policy import PolicyEngine
from .store import Store
from .workspace import WorkspaceManager


class TransientFailure(Exception):
    """Retryable: timeouts, temporary provider errors. Engine retries with backoff."""


class DeterministicFailure(Exception):
    """Not retryable as-is: failing check, wrong output. Engine schedules repair."""


class InvalidOutput(Exception):
    """Structured output violated its schema. One constrained repair, then escalate."""


@dataclass
class WorkerContext:
    run: Run
    store: Store
    registry: ArtifactRegistry
    workspaces: WorkspaceManager
    evidence: EvidenceRunner
    policy: PolicyEngine
    scenario: dict[str, Any]
    project_root: Path
    agents: Any = None  # agents.roles.RoleAgents when mode == "live"


def _maybe_inject_transient(ctx: WorkerContext, task: Task) -> None:
    """Scenario-scripted fault injection for demonstrating retry behavior."""
    for inj in ctx.scenario.get("inject", []):
        if inj.get("task") == task.name and inj.get("class") == "transient":
            if task.attempts <= int(inj.get("times", 1)):
                raise TransientFailure(f"injected transient failure #{task.attempts} for {task.name}")


# --------------------------------------------------------------------------- #
# capabilities
# --------------------------------------------------------------------------- #

def normalize_requirement(ctx: WorkerContext, task: Task) -> dict:
    """Turn the raw request into structured intent with explicit ambiguity."""
    if ctx.run.mode == "live" and ctx.agents is not None:
        req = ctx.agents.normalize(ctx.run.request, ctx.scenario)
    else:
        spec = ctx.scenario.get("requirement", {})
        req = {
            "request": ctx.run.request,
            "goals": spec.get("goals", []),
            "non_goals": spec.get("non_goals", []),
            "acceptance_criteria": spec.get("acceptance_criteria", []),
            "ambiguities": spec.get("ambiguities", []),
            "proposed_assumptions": spec.get("assumptions", []),
        }
    ctx.run.requirement_version += 1
    req["version"] = ctx.run.requirement_version
    ctx.registry.publish(ctx.run, "requirement", "requirement", req, produced_by=task.name)
    # Requirement facets ("contracts", e.g. the analytics consistency target)
    # are separate versioned artifacts so a later revision invalidates only
    # their descendants — the provenance anchor for selective re-planning.
    for contract in ctx.scenario.get("contracts", []):
        ctx.registry.publish(ctx.run, contract["name"], "contract",
                             contract.get("data", {}),
                             parents=["requirement"], produced_by=task.name)
    ctx.store.save_run(ctx.run)
    ctx.store.append_event(Event(
        run_id=ctx.run.id, type="requirement.versioned", task=task.name,
        payload={"version": ctx.run.requirement_version},
    ))
    material = [a for a in req.get("ambiguities", []) if a.get("materiality") == "material"]
    if material:
        ctx.store.append_event(Event(
            run_id=ctx.run.id, type="ambiguity.detected", task=task.name,
            payload={"topics": [a["topic"] for a in material]},
        ))
        # Material ambiguity raises a human gate on this task: the proposed
        # reversible assumptions must be approved before analysis proceeds.
        contract_names = [c["name"] for c in ctx.scenario.get("contracts", [])]
        task.gate = GateSpec(action="assumption.adopt", subject_artifacts=["requirement", *contract_names])
    return {"ambiguities": len(req.get("ambiguities", [])), "material": len(material)}


def analyze_repository(ctx: WorkerContext, task: Task) -> dict:
    """Bounded impact map of the existing workload repository."""
    worktree = ctx.workspaces.worktree(ctx.run)
    modules = sorted(
        str(p.relative_to(worktree))
        for p in worktree.rglob("*.py") if ".git" not in p.parts
    )
    hints = ctx.scenario.get("impact_hints", {})
    report = {
        "base_revision": ctx.run.base_revision,
        "existing_modules": modules,
        "greenfield": len(modules) == 0,
        "impacted_areas": hints.get("impacted_areas", []),
        "data_flows": hints.get("data_flows", []),
        "risks": hints.get("risks", []),
        "compatibility_notes": hints.get("compatibility_notes", []),
    }
    ctx.registry.publish(ctx.run, "impact_report", "impact", report,
                         parents=["requirement"], produced_by=task.name)
    return {"modules": len(modules), "impacted_areas": len(report["impacted_areas"])}


def create_plan(ctx: WorkerContext, task: Task) -> dict:
    """Produce the dependency-graph plan as data. The engine materializes it
    into Task records only after the plan gate passes."""
    if ctx.run.mode == "live" and ctx.agents is not None:
        plan_tasks = ctx.agents.plan(ctx.run, ctx.scenario)
    else:
        plan_tasks = ctx.scenario.get("plan", [])
    if not plan_tasks:
        raise InvalidOutput("planner produced an empty task list")
    risk_order = ["low", "medium", "high", "critical"]
    top = max((t.get("risk", "low") for t in plan_tasks), key=risk_order.index)
    ctx.run.risk = RiskLevel(top)
    ctx.run.plan_version += 1
    ctx.store.save_run(ctx.run)
    plan = {
        "version": ctx.run.plan_version,
        "tasks": plan_tasks,
        "risk": top,
        "requirement_version": ctx.run.requirement_version,
    }
    ctx.registry.publish(ctx.run, "plan", "plan", plan,
                         parents=["requirement", "impact_report"], produced_by=task.name)
    ctx.store.append_event(Event(
        run_id=ctx.run.id, type="plan.created", task=task.name,
        payload={"version": ctx.run.plan_version, "tasks": len(plan_tasks), "risk": top},
    ))
    # Risk-proportional autonomy: policy decides below whether this plan
    # needs a human. The gate carries the computed risk.
    task.gate = GateSpec(action="plan.adopt", subject_artifacts=["plan"])
    return {"tasks": len(plan_tasks), "risk": top}


def apply_seed(ctx: WorkerContext, task: Task) -> dict:
    """Implementer/tester/doc-writer in mock mode: write a proposal file set
    into the isolated worktree. No commit here — integration is a separate,
    synchronized step."""
    _maybe_inject_transient(ctx, task)
    seed_rel = task.params["seed"]
    seed_dir = ctx.project_root / seed_rel
    written = ctx.workspaces.sync_seed(ctx.run, seed_dir)
    if not written:
        raise DeterministicFailure(f"seed {seed_rel} produced no files")
    artifact_name = task.params.get("artifact", f"proposal_{task.name}")
    ctx.registry.publish(
        ctx.run, artifact_name, "patch",
        {"files": written, "seed": seed_rel, "description": task.params.get("description", "")},
        parents=task.params.get("parents", ["plan"]), produced_by=task.name,
    )
    return {"files_written": len(written)}


def integrate_change(ctx: WorkerContext, task: Task) -> dict:
    """Synchronization point: commit the combined proposals as one revision,
    then submit the integrated diff to policy."""
    _maybe_inject_transient(ctx, task)
    message = task.params.get("message", f"change: {ctx.run.scenario or ctx.run.id}")
    revision = ctx.workspaces.commit(ctx.run, message)
    stats = ctx.workspaces.diff_stats(ctx.run)
    ctx.policy.enforce(ctx.run.id, "change.integrate", resource=ctx.run.branch,
                       subject="orchestrator", risk=ctx.run.risk, context=stats)
    ctx.registry.publish(
        ctx.run, "change_set", "change",
        {"revision": revision, "message": message, **stats,
         "files": ctx.workspaces.changed_files(ctx.run)},
        parents=task.params.get("parents", ["plan"]), produced_by=task.name,
        revision=revision,
    )
    return {"revision": revision, **stats}


def run_checks(ctx: WorkerContext, task: Task) -> dict:
    """Deterministic verification. A failing required check is a deterministic
    failure — the engine will schedule a bounded repair, never a blind retry."""
    _maybe_inject_transient(ctx, task)
    results = []
    for check in task.params.get("checks", []):
        argv = [a.replace("{python}", sys.executable) for a in check["argv"]]
        art = ctx.evidence.run_check(
            ctx.run, check["name"], argv,
            timeout=int(check.get("timeout", 300)),
            produced_by=task.name, parents=check.get("parents", ["change_set"]),
        )
        results.append((check["name"], art.data["passed"], bool(check.get("required", True))))
    failed = [name for name, passed, required in results if required and not passed]
    if failed:
        raise DeterministicFailure(f"required checks failed: {', '.join(failed)}")
    return {"checks": len(results), "failed": 0}


_SECRET_PATTERNS = [
    (re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}"), "hardcoded credential"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "provider API key literal"),
    (re.compile(r"(?<![\w.])eval\s*\(", ), "eval() usage"),
    (re.compile(r"shell\s*=\s*True"), "subprocess shell=True"),
]


def security_scan(ctx: WorkerContext, task: Task) -> dict:
    """Static source scan of the integrated change: credential literals and
    dangerous constructs. Findings are published evidence, high severity fails."""
    worktree = ctx.workspaces.worktree(ctx.run)
    findings = []
    for path in sorted(worktree.rglob("*.py")):
        if ".git" in path.parts:
            continue
        text = path.read_text(errors="ignore")
        for item in _SECRET_PATTERNS:
            pattern, label = item[0], item[-1]
            for match in pattern.finditer(text):
                line = text[: match.start()].count("\n") + 1
                severity = "high" if "credential" in label or "key" in label else "medium"
                findings.append({"file": str(path.relative_to(worktree)), "line": line,
                                 "finding": label, "severity": severity})
    revision = ctx.run.current_revision
    ctx.registry.publish(
        ctx.run, "evidence_security_scan", "evidence",
        {"check": "security_scan", "passed": not any(f["severity"] == "high" for f in findings),
         "findings": findings, "files_scanned": True},
        parents=["change_set"], produced_by=task.name, revision=revision,
    )
    high = [f for f in findings if f["severity"] == "high"]
    if high:
        raise DeterministicFailure(f"security scan found {len(high)} high-severity issue(s)")
    return {"findings": len(findings), "high": 0}


def assemble_release(ctx: WorkerContext, task: Task) -> dict:
    """Build the review package: evidence inventory, diff stats, decisions,
    residual risks. Its gate (release.promote) is the final human checkpoint."""
    from .summary import build_release_report

    report = build_release_report(ctx)
    ctx.registry.publish(ctx.run, "release_report", "release", report,
                         parents=["change_set"], produced_by=task.name,
                         revision=ctx.run.current_revision)
    task.gate = GateSpec(action="release.promote", subject_artifacts=["release_report"])
    return {"evidence_items": len(report["evidence"]), "complete": report["evidence_complete"]}


def noop(ctx: WorkerContext, task: Task) -> dict:
    _maybe_inject_transient(ctx, task)
    if task.params.get("fail"):
        raise DeterministicFailure("scripted failure")
    if task.params.get("always_transient"):
        raise TransientFailure("scripted transient failure")
    return {"ok": True}


REGISTRY: dict[str, Callable[[WorkerContext, Task], dict]] = {
    "normalize_requirement": normalize_requirement,
    "analyze_repository": analyze_repository,
    "create_plan": create_plan,
    "apply_seed": apply_seed,
    "integrate_change": integrate_change,
    "run_checks": run_checks,
    "security_scan": security_scan,
    "assemble_release": assemble_release,
    "noop": noop,
}
