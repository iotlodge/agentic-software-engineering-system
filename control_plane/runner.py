"""Scenario runner: wires the system together and drives runs.

The same entry points serve the CLI, the dashboard API, and the scenario
integration tests. ``auto_approve`` exists for CI and unattended demos — the
approvals are still real records, attributed to the "auto-ci" approver.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

from .approvals import ApprovalService
from .artifacts import ArtifactRegistry
from .engine import Engine
from .evidence import EvidenceRunner
from .models import Event, Run, RunStatus
from .policy import PolicyEngine
from .store import Store
from .workspace import WorkspaceManager

DEFAULT_ASE_DIR = ".ase"


def build_system(project_root: str | Path, ase_dir: str | Path | None = None,
                 agents=None) -> SimpleNamespace:
    root = Path(project_root)
    base = Path(ase_dir) if ase_dir else root / DEFAULT_ASE_DIR
    store = Store(base / "state.db")
    policy = PolicyEngine(root / "policies" / "default.yaml", store)
    workspaces = WorkspaceManager(base, store, policy)
    registry = ArtifactRegistry(store)
    approvals = ApprovalService(store)
    evidence = EvidenceRunner(store, registry, workspaces, policy, base / "evidence")
    engine = Engine(root, store, policy, workspaces, registry, approvals,
                    evidence, agents=agents)
    return SimpleNamespace(root=root, base=base, store=store, policy=policy,
                           workspaces=workspaces, registry=registry,
                           approvals=approvals, evidence=evidence, engine=engine)


def load_scenario(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text())


def start_scenario(system: SimpleNamespace, scenario_path: str | Path,
                   mode: str = "mock") -> Run:
    scenario = load_scenario(scenario_path)
    run = system.engine.start(scenario, mode=mode)
    # Remember the scenario file so later resumes know what to load.
    system.store.append_event(Event(
        run_id=run.id, type="scenario.bound",
        payload={"path": str(scenario_path)},
    ))
    return run


def drive(system: SimpleNamespace, run: Run, scenario: dict,
          auto_approve: bool = False, approver: str = "auto-ci",
          max_pauses: int = 10) -> Run:
    """Tick the run forward; optionally auto-resolve approval pauses."""
    run = system.engine.tick(run.id, scenario)
    pauses = 0
    while run.status == RunStatus.AWAITING_APPROVAL and auto_approve and pauses < max_pauses:
        pauses += 1
        pending = system.store.list_approvals(run_id=run.id, status="pending")
        if not pending:
            break
        for approval in pending:
            system.approvals.resolve(
                approval.id, approve=True, approver=approver,
                reason=f"auto-approved ({approval.scope}) for unattended run")
        run = system.engine.tick(run.id, scenario)
    return run


def run_scenario(project_root: str | Path, scenario_path: str | Path,
                 mode: str = "mock", auto_approve: bool = False,
                 ase_dir: str | Path | None = None, agents=None) -> tuple[SimpleNamespace, Run]:
    system = build_system(project_root, ase_dir=ase_dir, agents=agents)
    scenario = load_scenario(scenario_path)
    run = system.engine.start(scenario, mode=mode)
    run = drive(system, run, scenario, auto_approve=auto_approve)
    return system, run
