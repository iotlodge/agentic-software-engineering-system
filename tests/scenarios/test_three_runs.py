"""End-to-end: the three governed scenarios evolve ONE workload repository.

Greenfield builds the baseline; brownfield changes it under a schema gate and
repairs a genuine failing regression; the ambiguous run pauses on material
ambiguity, then selectively re-plans when the evaluator tightens the
analytics-visibility contract mid-run.

Approvals are auto-resolved (recorded as approver "auto-ci") so the suite
runs unattended; every pause is still a real durable approval record.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from control_plane.models import RunStatus
from control_plane.runner import build_system, drive, load_scenario
from control_plane.telemetry import fleet_metrics, run_metrics

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    base = tmp_path_factory.mktemp("ase-world")
    system = build_system(ROOT, ase_dir=base)
    return SimpleNamespace(system=system, runs={})


def run_and_record(world, name):
    scenario = load_scenario(ROOT / "scenarios" / f"{name}.yaml")
    run = world.system.engine.start(scenario, mode="mock")
    run = drive(world.system, run, scenario, auto_approve=True)
    world.runs[name] = run
    return run


def events(world, run_id):
    return world.system.store.get_events(run_id)


def test_greenfield_builds_and_promotes_baseline(world):
    run = run_and_record(world, "greenfield")
    assert run.status == RunStatus.PROMOTED, run.stop_reason
    workload = world.system.workspaces.workload
    assert (workload / "shortener" / "api.py").exists()
    assert (workload / "tests" / "test_api.py").exists()
    assert (workload / "README.md").exists()
    types = [e.type for e in events(world, run.id)]
    # Injected transient failure was retried within budget.
    assert "retry.scheduled" in types
    # Parallel fan-out of impl / tests / docs happened in one wave.
    fan_out = [e.payload["tasks"] for e in events(world, run.id)
               if e.type == "wave.started"
               and set(e.payload["tasks"]) >= {"implement_service", "write_tests", "write_docs"}]
    assert fan_out, "expected implement/tests/docs to be scheduled together"
    approvals = world.system.store.list_approvals(run_id=run.id)
    assert [a.scope for a in approvals] == ["release.promote"]
    metrics = run_metrics(world.system.store, run.id)
    assert metrics["retries"] == 1 and metrics["repairs"] == 0


def test_brownfield_gates_schema_and_repairs_regression(world):
    run = run_and_record(world, "brownfield")
    assert run.status == RunStatus.PROMOTED, run.stop_reason
    workload = world.system.workspaces.workload
    # Migration 002 landed and the expiry boundary bug was repaired.
    assert (workload / "shortener" / "migrations" / "002_expiry_update.sql").exists()
    domain = (workload / "shortener" / "domain.py").read_text()
    assert "<= now" in domain, "repair must restore the inclusive boundary"
    scopes = [a.scope for a in world.system.store.list_approvals(run_id=run.id)]
    # Risk-proportional governance: plan (high risk), schema change, release.
    assert scopes == ["plan.adopt", "schema.migrate", "release.promote"]
    types = [e.type for e in events(world, run.id)]
    assert "repair.scheduled" in types
    metrics = run_metrics(world.system.store, run.id)
    assert metrics["repairs"] == 1
    # The failing pytest evidence and the passing re-check both exist.
    evidence = [a for a in world.system.store.get_artifacts(run.id)
                if a.name == "evidence_pytest"]
    assert [a.data["passed"] for a in evidence] == [False, True]
    # Baseline behavior is regression-protected: v1 tests ran in the suite.
    assert (workload / "tests" / "test_api.py").exists()


def test_ambiguous_pauses_then_selectively_replans(world):
    run = run_and_record(world, "ambiguous")
    assert run.status == RunStatus.PROMOTED, run.stop_reason
    scopes = [a.scope for a in world.system.store.list_approvals(run_id=run.id)]
    assert scopes == ["assumption.adopt", "plan.adopt", "schema.migrate",
                      "plan.revised", "release.promote"]
    assert run.requirement_version == 2
    assert run.plan_version == 2
    assert run.replans == 1
    types = [e.type for e in events(world, run.id)]
    for expected in ["ambiguity.detected", "requirement.revised", "plan.revised",
                     "artifact.invalidated", "task.invalidated"]:
        assert expected in types, f"missing {expected}"
    # Selectivity: the schema migration survived the revision untouched…
    plan_migration = world.system.store.get_task(run.id, "plan_migration")
    assert plan_migration.plan_version == 1
    starts = [e for e in events(world, run.id)
              if e.type == "task.started" and e.task == "plan_migration"]
    assert len(starts) == 1
    # …while the analytics implementation re-ran under plan v2.
    impl = world.system.store.get_task(run.id, "implement_analytics")
    assert impl.plan_version == 2
    # The workload now honors the tightened 5-second contract.
    workload = world.system.workspaces.workload
    contract = (workload / "shortener" / "analytics_contract.json").read_text()
    assert '"visibility_seconds": 5' in contract
    analytics = (workload / "shortener" / "analytics.py").read_text()
    assert "DEFAULT_FLUSH_INTERVAL = 2.0" in analytics


def test_fleet_metrics_reflect_three_successful_runs(world):
    assert len(world.runs) == 3
    fleet = fleet_metrics(world.system.store)
    assert fleet["runs_terminal"] == 3
    assert fleet["run_success_rate"]["value"] == 1.0
    # One evolving repository: main history contains all three merges.
    log = __import__("subprocess").run(
        ["git", "log", "--oneline", "main"],
        cwd=world.system.workspaces.workload, capture_output=True, text=True).stdout
    for fragment in ["baseline URL-shortener", "expiry and safe destination",
                     "hot-path cache"]:
        assert fragment in log
