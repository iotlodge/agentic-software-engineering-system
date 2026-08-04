"""``ase`` — the control-plane CLI.

    ase run scenarios/greenfield.yaml         start a governed run (pauses at gates)
    ase approvals                             list pending approvals
    ase approve <approval_id> --approver jb   approve (then auto-resumes the run)
    ase reject <approval_id> --approver jb --reason "..."
    ase resume <run_id>                       resume a paused/recovered run
    ase revise <run_id> revision.yaml         apply an upstream requirement revision
    ase runs / ase show <run_id>              inspect state
    ase metrics                               fleet metrics
    ase serve [--port 8787]                   dashboard (Daylight Glass)
    ase demo [--keep]                         all three scenarios, auto-approved
    ase agents-smoke                          live LLM adapter sanity check (needs .env)
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import yaml

from .models import RunStatus
from .runner import build_system, drive, load_scenario, start_scenario
from .telemetry import fleet_metrics, run_metrics

ROOT = Path.cwd()


def _system(args):
    agents = None
    if getattr(args, "mode", "mock") == "live":
        from .agents.adapter import from_env
        from .agents.roles import RoleAgents
        agents = RoleAgents(from_env(str(ROOT / ".env")))
    return build_system(ROOT, agents=agents)


def _scenario_for_run(system, run_id: str) -> dict | None:
    for event in system.store.get_events(run_id):
        if event.type == "scenario.bound":
            path = Path(event.payload["path"])
            if path.exists():
                return load_scenario(path)
    run = system.store.get_run(run_id)
    if run and run.scenario:
        candidate = ROOT / "scenarios" / f"{run.scenario}.yaml"
        if candidate.exists():
            return load_scenario(candidate)
    return None


def _print_status(system, run):
    print(f"\nrun {run.id} [{run.scenario}] -> {run.status}")
    if run.stop_reason:
        print(f"  stop reason: {run.stop_reason}")
    if run.status == RunStatus.AWAITING_APPROVAL:
        for a in system.store.list_approvals(run_id=run.id, status="pending"):
            print(f"\n  ⛩  approval required: {a.scope} (risk {a.risk})")
            print(f"     {a.title}")
            if a.description:
                print(f"     {a.description}")
            for name, digest in a.subject_digests.items():
                print(f"     bound to {name} @ {digest}")
            print(f"\n     approve:  ase approve {a.id} --approver <you> [--reason ...]")
            print(f"     reject:   ase reject {a.id} --approver <you> --reason ...")
    elif run.status in {RunStatus.PROMOTED, RunStatus.REVIEW_READY}:
        summary = system.base / "runs" / run.id / "summary.md"
        if summary.exists():
            print(f"  engineering summary: {summary}")


def cmd_run(args):
    system = _system(args)
    run = start_scenario(system, args.scenario, mode=args.mode)
    scenario = load_scenario(args.scenario)
    run = drive(system, run, scenario, auto_approve=args.auto_approve,
                approver=args.approver)
    _print_status(system, run)


def cmd_resume(args):
    system = _system(args)
    scenario = _scenario_for_run(system, args.run_id)
    if scenario is None:
        sys.exit(f"cannot locate scenario for run {args.run_id}")
    run = system.engine.tick(args.run_id, scenario)
    _print_status(system, run)


def _resolve(args, approve: bool):
    system = _system(args)
    approval = system.store.get_approval(args.approval_id)
    if approval is None:
        sys.exit(f"unknown approval {args.approval_id}")
    try:
        system.approvals.resolve(args.approval_id, approve=approve,
                                 approver=args.approver, reason=args.reason or "")
    except Exception as exc:
        sys.exit(str(exc))
    print(f"approval {args.approval_id} {'approved' if approve else 'rejected'} "
          f"by {args.approver}")
    scenario = _scenario_for_run(system, approval.run_id)
    if scenario is not None:
        run = system.engine.tick(approval.run_id, scenario)
        _print_status(system, run)


def cmd_approve(args):
    _resolve(args, True)


def cmd_reject(args):
    _resolve(args, False)


def cmd_approvals(args):
    system = _system(args)
    pending = system.store.list_approvals(status="pending")
    if not pending:
        print("no pending approvals")
    for a in pending:
        print(f"{a.id}  run={a.run_id}  scope={a.scope}  risk={a.risk}  {a.title}")


def cmd_revise(args):
    system = _system(args)
    run = system.store.get_run(args.run_id)
    if run is None:
        sys.exit(f"unknown run {args.run_id}")
    revision = yaml.safe_load(Path(args.revision).read_text())
    system.engine.revise(run, revision, actor=args.approver)
    run = system.store.get_run(args.run_id)
    _print_status(system, run)


def cmd_runs(args):
    system = _system(args)
    for r in system.store.list_runs():
        print(f"{r.id}  {r.scenario:<12} {r.status:<18} risk={r.risk:<8} "
              f"req v{r.requirement_version} plan v{r.plan_version} "
              f"repairs={r.repairs} replans={r.replans}")


def cmd_show(args):
    system = _system(args)
    print(json.dumps(run_metrics(system.store, args.run_id), indent=2))


def cmd_metrics(args):
    system = _system(args)
    print(json.dumps(fleet_metrics(system.store), indent=2))


def cmd_serve(args):
    import uvicorn

    from .api import create_app
    app = create_app(ROOT)
    print(f"Daylight Glass dashboard -> http://127.0.0.1:{args.port}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


def cmd_demo(args):
    if not args.keep:
        state = ROOT / ".ase"
        if state.exists():
            shutil.rmtree(state)
            print("cleared previous .ase state (use --keep to accumulate)")
    system = _system(args)
    for name in ["greenfield", "brownfield", "ambiguous"]:
        print(f"\n═══ scenario: {name} " + "═" * (46 - len(name)))
        scenario_path = ROOT / "scenarios" / f"{name}.yaml"
        run = start_scenario(system, scenario_path, mode=args.mode)
        scenario = load_scenario(scenario_path)
        run = drive(system, run, scenario, auto_approve=True, approver="demo-operator")
        metrics = run_metrics(system.store, run.id)
        print(f"  -> {run.status}  (waves {metrics['waves']}, retries {metrics['retries']}, "
              f"repairs {metrics['repairs']}, replans {run.replans}, "
              f"approvals {metrics['approvals']['requested']})")
        summary = system.base / "runs" / run.id / "summary.md"
        if summary.exists():
            print(f"  summary: {summary}")
    print("\nfleet:", json.dumps(fleet_metrics(system.store)["run_success_rate"], indent=2))
    print("\ninspect: ase serve   (dashboard)  ·  ase runs  ·  ase show <run_id>")


def cmd_agents_smoke(args):
    from .agents.adapter import from_env
    from .agents.roles import RoleAgents
    adapter = from_env(str(ROOT / ".env"))
    print(f"provider: {adapter.provider.name} · model: {adapter.provider.model}")
    agents = RoleAgents(adapter)
    out = agents.normalize(
        "Shorten URLs and count clicks; must run locally.", {})
    print(json.dumps(out, indent=2)[:1500])


def main():
    parser = argparse.ArgumentParser(prog="ase", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--mode", choices=["mock", "live"], default="mock")
        p.add_argument("--approver", default="cli-user")
        p.set_defaults(auto_approve=False)

    p = sub.add_parser("run", help="start a governed run")
    p.add_argument("scenario")
    p.add_argument("--auto-approve", action="store_true")
    common(p)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("resume", help="resume a paused run")
    p.add_argument("run_id")
    common(p)
    p.set_defaults(func=cmd_resume)

    for name, fn in [("approve", cmd_approve), ("reject", cmd_reject)]:
        p = sub.add_parser(name)
        p.add_argument("approval_id")
        p.add_argument("--approver", required=True)
        p.add_argument("--reason", default="")
        p.set_defaults(func=fn, mode="mock")

    p = sub.add_parser("approvals", help="list pending approvals")
    p.set_defaults(func=cmd_approvals, mode="mock")

    p = sub.add_parser("revise", help="apply an upstream requirement revision")
    p.add_argument("run_id")
    p.add_argument("revision", help="YAML file with contract/data/plan_upserts")
    p.add_argument("--approver", default="cli-user")
    p.set_defaults(func=cmd_revise, mode="mock")

    p = sub.add_parser("runs")
    p.set_defaults(func=cmd_runs, mode="mock")

    p = sub.add_parser("show")
    p.add_argument("run_id")
    p.set_defaults(func=cmd_show, mode="mock")

    p = sub.add_parser("metrics")
    p.set_defaults(func=cmd_metrics, mode="mock")

    p = sub.add_parser("serve", help="dashboard")
    p.add_argument("--port", type=int, default=8787)
    p.set_defaults(func=cmd_serve, mode="mock")

    p = sub.add_parser("demo", help="run all three scenarios, auto-approved")
    p.add_argument("--keep", action="store_true",
                   help="keep existing .ase state instead of starting clean")
    common(p)
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("agents-smoke", help="live LLM sanity check (.env keys)")
    p.set_defaults(func=cmd_agents_smoke, mode="live")

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
