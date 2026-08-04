"""Control-plane HTTP API + dashboard.

Read-only inspection of runs, tasks, artifacts, events and metrics, plus the
one write operation a human owns: resolving approvals. Resolving an approval
immediately resumes the paused run (same engine, same durable store).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from .approvals import ApprovalError
from .artifacts import ArtifactRegistry
from .runner import build_system, load_scenario
from .telemetry import fleet_metrics, run_metrics

STATIC = Path(__file__).parent / "static"


class ResolveRequest(BaseModel):
    approve: bool
    approver: str
    reason: str = ""


def create_app(project_root: str | Path, ase_dir: str | Path | None = None) -> FastAPI:
    app = FastAPI(title="ASE Control Plane", version="0.1.0")
    system = build_system(project_root, ase_dir=ase_dir)
    app.state.system = system

    def _scenario_for(run_id: str) -> dict | None:
        for event in system.store.get_events(run_id):
            if event.type == "scenario.bound":
                path = Path(event.payload["path"])
                if path.exists():
                    return load_scenario(path)
        run = system.store.get_run(run_id)
        if run and run.scenario:
            candidate = Path(project_root) / "scenarios" / f"{run.scenario}.yaml"
            if candidate.exists():
                return load_scenario(candidate)
        return None

    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        return (STATIC / "dashboard.html").read_text()

    @app.get("/api/runs")
    async def runs():
        return [{
            "id": r.id, "scenario": r.scenario, "status": r.status,
            "risk": r.risk, "mode": r.mode,
            "requirement_version": r.requirement_version,
            "plan_version": r.plan_version, "replans": r.replans,
            "repairs": r.repairs, "created_at": r.created_at,
            "updated_at": r.updated_at, "stop_reason": r.stop_reason,
            "pending_approval_id": r.pending_approval_id,
        } for r in system.store.list_runs()]

    @app.get("/api/runs/{run_id}")
    async def run_detail(run_id: str):
        run = system.store.get_run(run_id)
        if run is None:
            return JSONResponse(status_code=404, content={"error": "unknown run"})
        registry = ArtifactRegistry(system.store)
        return {
            "run": run.model_dump(),
            "tasks": [t.model_dump() for t in system.store.get_tasks(run_id)],
            "artifacts": registry.provenance(run_id),
            "approvals": [a.model_dump() for a in system.store.list_approvals(run_id=run_id)],
            "policy_decisions": [p.model_dump() for p in system.store.get_policy_records(run_id)],
            "metrics": run_metrics(system.store, run_id),
        }

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str, after: int = 0):
        return [e.model_dump() for e in system.store.get_events(run_id)
                if (e.seq or 0) > after]

    @app.get("/api/runs/{run_id}/summary")
    async def run_summary(run_id: str):
        path = system.base / "runs" / run_id / "summary.md"
        if not path.exists():
            return JSONResponse(status_code=404, content={"error": "no summary yet"})
        return {"markdown": path.read_text()}

    @app.get("/api/approvals")
    async def pending_approvals():
        return [a.model_dump() for a in system.store.list_approvals(status="pending")]

    @app.post("/api/approvals/{approval_id}/resolve")
    async def resolve(approval_id: str, payload: ResolveRequest):
        approval = system.store.get_approval(approval_id)
        if approval is None:
            return JSONResponse(status_code=404, content={"error": "unknown approval"})
        try:
            system.approvals.resolve(approval_id, approve=payload.approve,
                                     approver=payload.approver, reason=payload.reason)
        except ApprovalError as exc:
            return JSONResponse(status_code=409, content={"error": str(exc)})
        scenario = _scenario_for(approval.run_id)
        resumed = None
        if scenario is not None:
            run = system.engine.tick(approval.run_id, scenario)
            resumed = run.status
        return {"resolved": True, "run_status": resumed}

    @app.get("/api/metrics")
    async def metrics():
        return fleet_metrics(system.store)

    return app
