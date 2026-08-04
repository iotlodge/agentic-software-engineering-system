"""Foundation tests: model digests, durable store round-trips, graph semantics."""

import pytest

from control_plane import graph
from control_plane.models import (
    Approval,
    ApprovalStatus,
    Artifact,
    Event,
    RiskLevel,
    Run,
    RunStatus,
    Task,
    TaskStatus,
    digest_of,
)
from control_plane.store import Store


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "state.db")


def make_tasks(run_id: str, spec: dict[str, list[str]]) -> list[Task]:
    return [Task(run_id=run_id, name=n, capability="noop", depends_on=d) for n, d in spec.items()]


class TestModels:
    def test_digest_is_content_addressed(self):
        assert digest_of({"a": 1, "b": 2}) == digest_of({"b": 2, "a": 1})
        assert digest_of({"a": 1}) != digest_of({"a": 2})

    def test_artifact_digest_auto_assigned(self):
        a = Artifact(run_id="r", name="req", kind="requirement", data={"goal": "x"})
        assert a.digest.startswith("sha256:")

    def test_risk_ordering(self):
        assert RiskLevel.HIGH >= RiskLevel.MEDIUM
        assert not (RiskLevel.LOW >= RiskLevel.CRITICAL)

    def test_terminal_statuses(self):
        assert RunStatus.REVIEW_READY.terminal
        assert RunStatus.SAFE_STOPPED.terminal
        assert not RunStatus.EXECUTING.terminal


class TestStore:
    def test_run_round_trip_and_update(self, store):
        run = Run(scenario="greenfield", request="build it")
        store.save_run(run)
        run.status = RunStatus.EXECUTING
        store.save_run(run)
        loaded = store.get_run(run.id)
        assert loaded is not None
        assert loaded.status == RunStatus.EXECUTING
        assert loaded.request == "build it"

    def test_task_upsert_keyed_by_id(self, store):
        t = Task(run_id="r1", name="a", capability="noop")
        store.save_task(t)
        t.status = TaskStatus.SUCCEEDED
        store.save_task(t)
        tasks = store.get_tasks("r1")
        assert len(tasks) == 1
        assert tasks[0].status == TaskStatus.SUCCEEDED

    def test_artifact_latest_prefers_fresh_highest_version(self, store):
        a1 = Artifact(run_id="r1", name="plan", kind="plan", version=1, data={"v": 1})
        a2 = Artifact(run_id="r1", name="plan", kind="plan", version=2, data={"v": 2})
        store.save_artifact(a1)
        store.save_artifact(a2)
        got = store.get_artifact("r1", "plan")
        assert got.version == 2
        a2.stale = True
        store.save_artifact(a2)
        got = store.get_artifact("r1", "plan")
        assert got.version == 1 and not got.stale

    def test_events_append_only_ordered(self, store):
        for i in range(3):
            store.append_event(Event(run_id="r1", type=f"e{i}"))
        events = store.get_events("r1")
        assert [e.type for e in events] == ["e0", "e1", "e2"]
        assert events[0].seq < events[2].seq

    def test_approval_filtering(self, store):
        ap = Approval(run_id="r1", scope="plan", title="approve plan")
        store.save_approval(ap)
        assert store.list_approvals(run_id="r1", status="pending")
        ap.status = ApprovalStatus.APPROVED
        store.save_approval(ap)
        assert not store.list_approvals(run_id="r1", status="pending")

    def test_survives_reopen(self, tmp_path):
        path = tmp_path / "state.db"
        s1 = Store(path)
        run = Run(scenario="s", request="q")
        s1.save_run(run)
        s2 = Store(path)  # fresh handle == process restart
        assert s2.get_run(run.id) is not None


class TestGraph:
    def test_rejects_unknown_dependency(self):
        tasks = make_tasks("r", {"a": ["ghost"]})
        with pytest.raises(graph.GraphError, match="unknown"):
            graph.validate(tasks)

    def test_rejects_cycle(self):
        tasks = make_tasks("r", {"a": ["b"], "b": ["a"]})
        with pytest.raises(graph.GraphError, match="cycle"):
            graph.validate(tasks)

    def test_ready_wave_computation(self):
        tasks = make_tasks("r", {"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]})
        graph.validate(tasks)
        assert {t.name for t in graph.ready_tasks(tasks)} == {"a"}
        tasks[0].status = TaskStatus.SUCCEEDED
        assert {t.name for t in graph.ready_tasks(tasks)} == {"b", "c"}  # fan-out
        tasks[1].status = TaskStatus.SUCCEEDED
        assert {t.name for t in graph.ready_tasks(tasks)} == {"c"}  # fan-in still waits for c
        tasks[2].status = TaskStatus.SUCCEEDED
        assert {t.name for t in graph.ready_tasks(tasks)} == {"d"}

    def test_waived_satisfies_dependency(self):
        tasks = make_tasks("r", {"a": [], "b": ["a"]})
        tasks[0].status = TaskStatus.WAIVED
        assert {t.name for t in graph.ready_tasks(tasks)} == {"b"}

    def test_descendants_traversal(self):
        tasks = make_tasks("r", {"a": [], "b": ["a"], "c": ["b"], "x": []})
        assert graph.descendants(tasks, {"a"}) == {"b", "c"}
        assert graph.descendants(tasks, {"x"}) == set()
