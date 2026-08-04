"""Orchestration engine behavior: the differentiating claims of the system.

Covers: parallel fan-out/fan-in, durable pause/resume on approvals, process
restart recovery, bounded retry, deterministic-failure repair loops, policy
fallback, safe-stop with preserved workspace, and selective re-planning that
preserves unaffected work.
"""

from types import SimpleNamespace

import pytest

from control_plane.approvals import ApprovalService
from control_plane.artifacts import ArtifactRegistry
from control_plane.engine import Engine
from control_plane.evidence import EvidenceRunner
from control_plane.models import RunStatus, TaskStatus
from control_plane.policy import PolicyEngine
from control_plane.store import Store
from control_plane.workspace import WorkspaceManager

POLICY_PATH = "policies/default.yaml"


def build_system(tmp_path, db_name="state.db"):
    store = Store(tmp_path / db_name)
    policy = PolicyEngine(POLICY_PATH, store)
    workspaces = WorkspaceManager(tmp_path / "ase", store, policy)
    registry = ArtifactRegistry(store)
    approvals = ApprovalService(store)
    evidence = EvidenceRunner(store, registry, workspaces, policy, tmp_path / "ase" / "evidence")
    engine = Engine(tmp_path, store, policy, workspaces, registry, approvals,
                    evidence, backoff=0.01)
    return SimpleNamespace(store=store, policy=policy, workspaces=workspaces,
                           registry=registry, approvals=approvals,
                           evidence=evidence, engine=engine, root=tmp_path)


def write_seed(root, rel, files: dict[str, str]):
    for name, content in files.items():
        path = root / rel / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return rel


def base_scenario(**overrides):
    scenario = {
        "name": "synthetic",
        "request": "Do the synthetic thing.",
        "requirement": {
            "goals": ["a goal"],
            "acceptance_criteria": ["a criterion"],
            "ambiguities": [],
        },
        "plan": [],
    }
    scenario.update(overrides)
    return scenario


def approve_pending(system, run_id, approver="jb", reason="ok"):
    pending = system.store.list_approvals(run_id=run_id, status="pending")
    assert pending, "expected a pending approval"
    system.approvals.resolve(pending[-1].id, approve=True, approver=approver, reason=reason)
    return pending[-1]


def event_types(system, run_id):
    return [e.type for e in system.store.get_events(run_id)]


class TestHappyPathLifecycle:
    def test_full_run_to_promotion(self, tmp_path):
        system = build_system(tmp_path)
        write_seed(tmp_path, "seeds/impl", {"app.py": "print('app')\n",
                                            "check.py": "print('ok')\n"})
        scenario = base_scenario(plan=[
            {"name": "impl", "capability": "apply_seed",
             "params": {"seed": "seeds/impl", "parents": ["requirement"]}},
            {"name": "integrate", "capability": "integrate_change",
             "depends_on": ["impl"], "params": {"parents": ["proposal_impl"]}},
            {"name": "verify", "capability": "run_checks", "depends_on": ["integrate"],
             "params": {"checks": [{"name": "smoke", "argv": ["{python}", "check.py"]}]}},
            {"name": "assemble", "capability": "assemble_release", "depends_on": ["verify"]},
        ])
        run = system.engine.start(scenario)
        run = system.engine.tick(run.id, scenario)
        # Paused at the final release gate.
        assert run.status == RunStatus.AWAITING_APPROVAL
        approval = approve_pending(system, run.id)
        assert approval.scope == "release.promote"
        run = system.engine.tick(run.id, scenario)
        assert run.status == RunStatus.PROMOTED
        # The approved change actually landed on workload main.
        assert (system.workspaces.workload / "app.py").exists()
        types = event_types(system, run.id)
        for expected in ["run.created", "requirement.versioned", "plan.created",
                         "gate.passed", "evidence.recorded", "approval.requested",
                         "approval.resolved", "release.promoted", "run.completed"]:
            assert expected in types, f"missing event {expected}"

    def test_fan_out_same_wave(self, tmp_path):
        system = build_system(tmp_path)
        write_seed(tmp_path, "seeds/a", {"a.py": "A = 1\n"})
        write_seed(tmp_path, "seeds/b", {"b.py": "B = 2\n"})
        scenario = base_scenario(plan=[
            {"name": "impl_a", "capability": "apply_seed", "params": {"seed": "seeds/a"}},
            {"name": "impl_b", "capability": "apply_seed", "params": {"seed": "seeds/b"}},
            {"name": "integrate", "capability": "integrate_change",
             "depends_on": ["impl_a", "impl_b"]},
        ])
        run = system.engine.start(scenario)
        run = system.engine.tick(run.id, scenario)
        assert run.status == RunStatus.REVIEW_READY
        waves = [e.payload["tasks"] for e in system.store.get_events(run.id)
                 if e.type == "wave.started"]
        assert any(set(w) == {"impl_a", "impl_b"} for w in waves), \
            f"fan-out wave not found in {waves}"
        # Fan-in: integrate ran strictly after both.
        assert any(w == ["integrate"] for w in waves)


class TestApprovalFlow:
    def _risky_scenario(self, tmp_path):
        write_seed(tmp_path, "seeds/impl", {"app.py": "print('x')\n"})
        return base_scenario(plan=[
            {"name": "impl", "capability": "apply_seed", "risk": "high",
             "params": {"seed": "seeds/impl"}},
        ])

    def test_high_risk_plan_pauses_for_approval(self, tmp_path):
        system = build_system(tmp_path)
        scenario = self._risky_scenario(tmp_path)
        run = system.engine.start(scenario)
        run = system.engine.tick(run.id, scenario)
        assert run.status == RunStatus.AWAITING_APPROVAL
        pending = system.store.list_approvals(run_id=run.id, status="pending")
        assert pending[0].scope == "plan.adopt"
        assert "plan" in pending[0].subject_digests
        # Idle tick while waiting changes nothing.
        run = system.engine.tick(run.id, scenario)
        assert run.status == RunStatus.AWAITING_APPROVAL
        approve_pending(system, run.id)
        run = system.engine.tick(run.id, scenario)
        assert run.status == RunStatus.REVIEW_READY

    def test_rejection_terminates_with_reason(self, tmp_path):
        system = build_system(tmp_path)
        scenario = self._risky_scenario(tmp_path)
        run = system.engine.start(scenario)
        run = system.engine.tick(run.id, scenario)
        pending = system.store.list_approvals(run_id=run.id, status="pending")[0]
        system.approvals.resolve(pending.id, approve=False, approver="jb",
                                 reason="too risky for this sprint")
        run = system.engine.tick(run.id, scenario)
        assert run.status == RunStatus.DENIED
        assert "too risky" in (run.stop_reason or "")


class TestDurability:
    def test_restart_resumes_from_persisted_state(self, tmp_path):
        system = build_system(tmp_path)
        scenario = base_scenario(plan=[
            {"name": "impl", "capability": "apply_seed", "risk": "high",
             "params": {"seed": "seeds/impl"}},
        ])
        write_seed(tmp_path, "seeds/impl", {"app.py": "print('x')\n"})
        run = system.engine.start(scenario)
        run = system.engine.tick(run.id, scenario)
        assert run.status == RunStatus.AWAITING_APPROVAL
        # Simulate a full process restart: brand-new object graph, same DB.
        reborn = build_system(tmp_path)
        approve_pending(reborn, run.id)
        resumed = reborn.engine.tick(run.id, scenario)
        assert resumed.status == RunStatus.REVIEW_READY

    def test_crashed_running_task_is_requeued(self, tmp_path):
        system = build_system(tmp_path)
        write_seed(tmp_path, "seeds/impl", {"app.py": "print('x')\n"})
        scenario = base_scenario(plan=[
            {"name": "impl", "capability": "apply_seed", "params": {"seed": "seeds/impl"}},
        ])
        run = system.engine.start(scenario)
        run = system.engine.tick(run.id, scenario)
        assert run.status == RunStatus.REVIEW_READY
        # Forge a crash: a task left RUNNING in the store, run not terminal.
        task = system.store.get_task(run.id, "impl")
        task.status = TaskStatus.RUNNING
        system.store.save_task(task)
        run.status = RunStatus.EXECUTING
        system.store.save_run(run)
        run = system.engine.tick(run.id, scenario)
        assert run.status == RunStatus.REVIEW_READY
        assert "task.recovered" in event_types(system, run.id)


class TestFailureHandling:
    def test_transient_failure_retries_then_succeeds(self, tmp_path):
        system = build_system(tmp_path)
        scenario = base_scenario(
            plan=[{"name": "flaky", "capability": "noop"}],
            inject=[{"task": "flaky", "class": "transient", "times": 1}],
        )
        run = system.engine.start(scenario)
        run = system.engine.tick(run.id, scenario)
        assert run.status == RunStatus.REVIEW_READY
        assert "retry.scheduled" in event_types(system, run.id)
        assert system.store.get_task(run.id, "flaky").attempts == 2

    def test_retry_budget_exhaustion_safe_stops(self, tmp_path):
        system = build_system(tmp_path)
        scenario = base_scenario(plan=[
            {"name": "doomed", "capability": "noop", "params": {"always_transient": True}},
        ])
        run = system.engine.start(scenario)
        run = system.engine.tick(run.id, scenario)
        assert run.status == RunStatus.SAFE_STOPPED
        assert "retry budget exhausted" in run.stop_reason
        # Workspace preserved with recovery instructions; mainline untouched.
        recovery = system.workspaces.base / "runs" / run.id / "RECOVERY.md"
        assert recovery.exists()
        assert (system.workspaces.base / "runs" / run.id / "worktree").exists()

    def test_deterministic_failure_triggers_bounded_repair(self, tmp_path):
        system = build_system(tmp_path)
        write_seed(tmp_path, "seeds/buggy", {"app.py": "print('v1')\n",
                                             "check.py": "import sys; sys.exit(1)\n"})
        write_seed(tmp_path, "seeds/fix", {"check.py": "import sys; sys.exit(0)\n"})
        scenario = base_scenario(
            plan=[
                {"name": "impl", "capability": "apply_seed", "params": {"seed": "seeds/buggy"}},
                {"name": "integrate", "capability": "integrate_change", "depends_on": ["impl"]},
                {"name": "verify", "capability": "run_checks", "depends_on": ["integrate"],
                 "params": {"checks": [{"name": "smoke", "argv": ["{python}", "check.py"]}]}},
            ],
            repair={"verify": {"seed": "seeds/fix",
                               "description": "replace failing check logic"}},
        )
        run = system.engine.start(scenario)
        run = system.engine.tick(run.id, scenario)
        assert run.status == RunStatus.REVIEW_READY
        assert run.repairs == 1
        types = event_types(system, run.id)
        assert "repair.scheduled" in types
        verify = system.store.get_task(run.id, "verify")
        assert verify.status == TaskStatus.SUCCEEDED
        assert "reintegrate_1" in verify.depends_on
        # Both evidence versions exist: the failing one and the passing re-check.
        evidence = [a for a in system.store.get_artifacts(run.id)
                    if a.name == "evidence_smoke"]
        assert len(evidence) == 2
        assert not evidence[0].data["passed"] and evidence[1].data["passed"]

    def test_repair_budget_exhaustion_safe_stops(self, tmp_path):
        system = build_system(tmp_path)
        write_seed(tmp_path, "seeds/buggy", {"check.py": "import sys; sys.exit(1)\n"})
        write_seed(tmp_path, "seeds/nofix", {"other.py": "x = 1\n"})
        scenario = base_scenario(
            budgets={"max_repairs": 1},
            plan=[
                {"name": "impl", "capability": "apply_seed", "params": {"seed": "seeds/buggy"}},
                {"name": "integrate", "capability": "integrate_change", "depends_on": ["impl"]},
                {"name": "verify", "capability": "run_checks", "depends_on": ["integrate"],
                 "params": {"checks": [{"name": "smoke", "argv": ["{python}", "check.py"]}]}},
            ],
            repair={"verify": {"seed": "seeds/nofix", "description": "does not help"}},
        )
        run = system.engine.start(scenario)
        run = system.engine.tick(run.id, scenario)
        assert run.status == RunStatus.SAFE_STOPPED
        assert "repair budget exhausted" in run.stop_reason

    def test_policy_denial_routes_to_fallback(self, tmp_path):
        system = build_system(tmp_path)
        # First seed tries to write into policies/ (denied); fallback seed is clean.
        write_seed(tmp_path, "seeds/naughty", {"policies/weaken.yaml": "default: allow\n"})
        write_seed(tmp_path, "seeds/clean", {"app.py": "print('safe')\n"})
        scenario = base_scenario(plan=[
            {"name": "impl", "capability": "apply_seed",
             "params": {"seed": "seeds/naughty",
                        "fallback": {"params": {"seed": "seeds/clean"}}}},
        ])
        run = system.engine.start(scenario)
        run = system.engine.tick(run.id, scenario)
        assert run.status == RunStatus.REVIEW_READY
        assert "fallback.selected" in event_types(system, run.id)
        records = system.store.get_policy_records(run.id)
        assert any(r.decision == "deny" and r.action == "workspace.write" for r in records)

    def test_policy_denial_without_fallback_safe_stops(self, tmp_path):
        system = build_system(tmp_path)
        write_seed(tmp_path, "seeds/naughty", {"policies/weaken.yaml": "default: allow\n"})
        scenario = base_scenario(plan=[
            {"name": "impl", "capability": "apply_seed", "params": {"seed": "seeds/naughty"}},
        ])
        run = system.engine.start(scenario)
        run = system.engine.tick(run.id, scenario)
        assert run.status == RunStatus.SAFE_STOPPED
        assert "policy denied" in run.stop_reason

    def test_wave_budget_safe_stops(self, tmp_path):
        system = build_system(tmp_path)
        write_seed(tmp_path, "seeds/impl", {"app.py": "print('x')\n"})
        scenario = base_scenario(
            budgets={"max_waves": 2},
            plan=[{"name": "impl", "capability": "apply_seed", "params": {"seed": "seeds/impl"}}],
        )
        run = system.engine.start(scenario)
        run = system.engine.tick(run.id, scenario)
        assert run.status == RunStatus.SAFE_STOPPED
        assert "wave budget" in run.stop_reason


class TestSelectiveReplan:
    def _scenario(self):
        return base_scenario(
            requirement={
                "goals": ["fast redirects"],
                "acceptance_criteria": ["p95 under target"],
                "ambiguities": [{"topic": "analytics delay",
                                 "materiality": "material",
                                 "proposed_default": "60s visibility"}],
                "assumptions": ["analytics may lag up to 60 seconds"],
            },
            contracts=[{"name": "consistency_contract",
                        "data": {"analytics_delay_s": 60}}],
            plan=[
                {"name": "impl_analytics", "capability": "apply_seed",
                 "params": {"seed": "seeds/analytics", "parents": ["consistency_contract"]}},
                {"name": "impl_validation", "capability": "apply_seed",
                 "params": {"seed": "seeds/validation", "parents": ["requirement"]}},
                {"name": "integrate", "capability": "integrate_change",
                 "depends_on": ["impl_analytics", "impl_validation"],
                 "params": {"parents": ["proposal_impl_analytics", "proposal_impl_validation"]}},
                {"name": "verify", "capability": "run_checks", "depends_on": ["integrate"],
                 "params": {"checks": [{"name": "smoke", "argv": ["{python}", "check.py"]}]}},
            ],
            revision={
                "after_task": "verify",
                "contract": "consistency_contract",
                "data": {"analytics_delay_s": 5},
                "reason": "evaluator tightened analytics visibility from 60s to 5s",
                "plan_upserts": [{"name": "perf_check", "capability": "noop",
                                  "depends_on": ["integrate"]}],
            },
        )

    def test_revision_invalidates_only_affected_work(self, tmp_path):
        system = build_system(tmp_path)
        write_seed(tmp_path, "seeds/analytics", {"analytics.py": "DELAY = 60\n",
                                                 "check.py": "print('ok')\n"})
        write_seed(tmp_path, "seeds/validation", {"validation.py": "STRICT = True\n"})
        scenario = self._scenario()
        run = system.engine.start(scenario)
        run = system.engine.tick(run.id, scenario)
        # Gate 1: material ambiguity — proposed assumptions need a human.
        assert run.status == RunStatus.AWAITING_APPROVAL
        first = approve_pending(system, run.id, reason="60s default accepted")
        assert first.scope == "assumption.adopt"
        assert "consistency_contract" in first.subject_digests
        run = system.engine.tick(run.id, scenario)
        # Gate 2: the scripted upstream revision fired after verify; renewed
        # approval is required because the contract digest changed.
        assert run.status == RunStatus.AWAITING_APPROVAL
        second = system.store.list_approvals(run_id=run.id, status="pending")[0]
        assert second.scope == "plan.revised"
        # Selective invalidation happened before the pause:
        assert system.store.get_task(run.id, "impl_analytics").status == TaskStatus.PENDING
        assert system.store.get_task(run.id, "impl_validation").status == TaskStatus.SUCCEEDED
        validation_artifact = system.store.get_artifact(run.id, "proposal_impl_validation")
        assert not validation_artifact.stale
        analytics_versions = [a for a in system.store.get_artifacts(run.id)
                              if a.name == "proposal_impl_analytics"]
        assert any(a.stale for a in analytics_versions)
        approve_pending(system, run.id, reason="5s target accepted")
        run = system.engine.tick(run.id, scenario)
        assert run.status == RunStatus.REVIEW_READY
        # The re-plan preserved unaffected work and executed the new subgraph.
        assert run.requirement_version == 2
        assert run.plan_version == 2
        assert run.replans == 1
        assert system.store.get_task(run.id, "perf_check").status == TaskStatus.SUCCEEDED
        assert system.store.get_task(run.id, "impl_validation").plan_version == 1
        assert system.store.get_task(run.id, "impl_analytics").plan_version == 2
        starts = [e for e in system.store.get_events(run.id)
                  if e.type == "task.started" and e.task == "impl_analytics"]
        assert len(starts) == 2  # executed once per plan version
        validation_starts = [e for e in system.store.get_events(run.id)
                             if e.type == "task.started" and e.task == "impl_validation"]
        assert len(validation_starts) == 1  # preserved work never re-ran
        revised = [e for e in system.store.get_events(run.id) if e.type == "plan.revised"]
        assert revised and "impl_validation" in revised[0].payload["preserved_tasks"]
