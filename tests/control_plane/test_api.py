"""Control-plane API: inspection endpoints and approval-resolution resume."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane.api import create_app
from control_plane.models import RunStatus
from control_plane.runner import load_scenario, start_scenario

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def client(tmp_path):
    app = create_app(ROOT, ase_dir=tmp_path / "ase")
    with TestClient(app) as c:
        yield c


def _start_paused_run(client):
    """Start the greenfield scenario through its release gate pause."""
    system = client.app.state.system
    path = ROOT / "scenarios" / "greenfield.yaml"
    run = start_scenario(system, path, mode="mock")
    run = system.engine.tick(run.id, load_scenario(path))
    assert run.status == RunStatus.AWAITING_APPROVAL
    return run


def test_dashboard_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Daylight" in resp.text or "agentic" in resp.text


def test_runs_detail_events_and_metrics(client):
    run = _start_paused_run(client)
    runs = client.get("/api/runs").json()
    assert runs and runs[0]["id"] == run.id
    detail = client.get(f"/api/runs/{run.id}").json()
    assert detail["run"]["status"] == "awaiting_approval"
    assert any(t["name"] == "verify" for t in detail["tasks"])
    assert detail["metrics"]["retries"] == 1  # injected transient, retried
    events = client.get(f"/api/runs/{run.id}/events").json()
    assert any(e["type"] == "wave.started" for e in events)
    assert client.get("/api/metrics").json()["runs_total"] == 1


def test_approval_resolution_resumes_run(client):
    run = _start_paused_run(client)
    pending = client.get("/api/approvals").json()
    assert len(pending) == 1 and pending[0]["scope"] == "release.promote"
    resp = client.post(f"/api/approvals/{pending[0]['id']}/resolve",
                       json={"approve": True, "approver": "jb",
                             "reason": "dashboard approval"})
    assert resp.status_code == 200
    assert resp.json()["run_status"] == "promoted"
    summary = client.get(f"/api/runs/{run.id}/summary").json()
    assert "Engineering summary" in summary["markdown"]


def test_unknown_ids_are_404(client):
    assert client.get("/api/runs/run_nope").status_code == 404
    assert client.post("/api/approvals/apr_nope/resolve",
                       json={"approve": True, "approver": "x"}).status_code == 404
