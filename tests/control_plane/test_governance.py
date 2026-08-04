"""Governance layer: policy decisions, digest-bound approvals, provenance
invalidation, workspace isolation, and revision-bound evidence."""

import sys

import pytest

from control_plane.approvals import ApprovalError, ApprovalService
from control_plane.artifacts import ArtifactRegistry
from control_plane.evidence import EvidenceRunner
from control_plane.models import Decision, RiskLevel, Run
from control_plane.policy import PolicyDenied, PolicyEngine
from control_plane.store import Store
from control_plane.workspace import WorkspaceError, WorkspaceManager

POLICY_PATH = "policies/default.yaml"


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "state.db")


@pytest.fixture()
def policy(store):
    return PolicyEngine(POLICY_PATH, store)


@pytest.fixture()
def registry(store):
    return ArtifactRegistry(store)


@pytest.fixture()
def workspaces(tmp_path, store, policy):
    return WorkspaceManager(tmp_path / "ase", store, policy)


@pytest.fixture()
def run(store, workspaces):
    r = Run(scenario="test", request="do the thing")
    store.save_run(r)
    workspaces.create(r)
    return r


class TestPolicy:
    def test_default_deny_for_unknown_action(self, policy):
        rec = policy.evaluate("r1", "teleport.production")
        assert rec.decision == Decision.DENY
        assert rec.rule == "default"

    def test_secrets_always_denied(self, policy):
        with pytest.raises(PolicyDenied):
            policy.enforce("r1", "secrets.read", resource="ANTHROPIC_API_KEY")

    def test_protected_paths_denied_but_normal_writes_allowed(self, policy):
        assert policy.evaluate("r1", "workspace.write", resource="policies/default.yaml").decision == Decision.DENY
        assert policy.evaluate("r1", "workspace.write", resource="shortener/api.py").decision == Decision.ALLOW

    def test_risk_threshold_escalates_plan_approval(self, policy):
        low = policy.evaluate("r1", "plan.adopt", risk=RiskLevel.MEDIUM)
        high = policy.evaluate("r1", "plan.adopt", risk=RiskLevel.HIGH)
        assert low.decision == Decision.ALLOW
        assert high.decision == Decision.APPROVAL_REQUIRED

    def test_numeric_threshold_gate(self, policy):
        small = policy.evaluate("r1", "change.integrate", context={"changed_lines": 100})
        big = policy.evaluate("r1", "change.integrate", context={"changed_lines": 2000})
        assert small.decision == Decision.ALLOW
        assert big.decision == Decision.APPROVAL_REQUIRED

    def test_every_evaluation_is_recorded(self, policy, store):
        policy.evaluate("r1", "plan.adopt", risk=RiskLevel.LOW)
        policy.evaluate("r1", "secrets.read")
        records = store.get_policy_records("r1")
        assert len(records) == 2
        assert all(r.policy_version == "1" for r in records)


class TestApprovals:
    def test_request_pauses_run_and_resolve_resumes(self, store, registry):
        service = ApprovalService(store)
        run = Run(request="x")
        store.save_run(run)
        registry.publish(run, "plan", "plan", {"tasks": ["a"]})
        ap = service.request(run, scope="plan", title="Approve plan", subject_artifacts=["plan"])
        assert store.get_run(run.id).status == "awaiting_approval"
        service.resolve(ap.id, approve=True, approver="jb", reason="looks right")
        consumed = service.consume(run)
        assert consumed is not None and consumed.approver == "jb"
        assert store.get_run(run.id).pending_approval_id is None

    def test_stale_approval_rejected_when_subject_changes(self, store, registry):
        service = ApprovalService(store)
        run = Run(request="x")
        store.save_run(run)
        registry.publish(run, "plan", "plan", {"tasks": ["a"]})
        ap = service.request(run, scope="plan", title="Approve plan", subject_artifacts=["plan"])
        registry.publish(run, "plan", "plan", {"tasks": ["a", "b"]})  # content changed
        with pytest.raises(ApprovalError, match="stale"):
            service.resolve(ap.id, approve=True, approver="jb")

    def test_approved_then_changed_fails_at_consumption(self, store, registry):
        service = ApprovalService(store)
        run = Run(request="x")
        store.save_run(run)
        registry.publish(run, "plan", "plan", {"tasks": ["a"]})
        ap = service.request(run, scope="plan", title="Approve plan", subject_artifacts=["plan"])
        service.resolve(ap.id, approve=True, approver="jb")
        registry.publish(run, "plan", "plan", {"tasks": ["sneaky_extra"]})
        with pytest.raises(ApprovalError, match="re-approval"):
            service.consume(run)

    def test_rejection_surfaces_reason(self, store, registry):
        service = ApprovalService(store)
        run = Run(request="x")
        store.save_run(run)
        ap = service.request(run, scope="release", title="Ship it")
        service.resolve(ap.id, approve=False, approver="jb", reason="not enough tests")
        with pytest.raises(ApprovalError, match="not enough tests"):
            service.consume(run)


class TestArtifacts:
    def test_versions_increment_per_name(self, store, registry):
        run = Run(request="x")
        store.save_run(run)
        a1 = registry.publish(run, "requirement", "requirement", {"v": 1})
        a2 = registry.publish(run, "requirement", "requirement", {"v": 2})
        assert (a1.version, a2.version) == (1, 2)

    def test_selective_invalidation_spares_unrelated(self, store, registry):
        run = Run(request="x")
        store.save_run(run)
        registry.publish(run, "requirement", "requirement", {"v": 1})
        registry.publish(run, "design", "design", {"v": 1}, parents=["requirement"])
        registry.publish(run, "patch", "patch", {"v": 1}, parents=["design"])
        registry.publish(run, "unrelated_docs", "doc", {"v": 1})  # no provenance link
        registry.publish(run, "requirement", "requirement", {"v": 2})  # upstream change
        stale = registry.invalidate_descendants(run, "requirement")
        assert set(stale) == {"design", "patch"}
        docs = store.get_artifact(run.id, "unrelated_docs")
        assert docs is not None and not docs.stale


class TestWorkspace:
    def test_isolated_worktree_on_run_branch(self, run, workspaces):
        assert run.branch == f"run/{run.id}"
        assert (workspaces.worktree(run) / ".git").exists()
        assert run.base_revision

    def test_path_escape_rejected(self, run, workspaces):
        with pytest.raises(WorkspaceError, match="escapes"):
            workspaces.write_file(run, "../outside.txt", "nope")

    def test_protected_path_write_denied_by_policy(self, run, workspaces):
        with pytest.raises(PolicyDenied):
            workspaces.write_file(run, "policies/default.yaml", "default: allow")

    def test_commit_diff_rollback_cycle(self, run, workspaces):
        workspaces.write_file(run, "src/app.py", "print('v1')\n")
        rev1 = workspaces.commit(run, "feat: v1")
        workspaces.write_file(run, "src/app.py", "print('v2')\nprint('more')\n")
        workspaces.commit(run, "feat: v2")
        assert workspaces.diff_stats(run)["files_changed"] == 1
        workspaces.rollback(run, rev1, reason="test rollback")
        assert (workspaces.worktree(run) / "src/app.py").read_text() == "print('v1')\n"
        assert run.current_revision == rev1

    def test_promote_merges_into_main(self, run, workspaces, store):
        workspaces.write_file(run, "README.md", "# service\n")
        workspaces.commit(run, "docs: readme")
        merged = workspaces.promote(run)
        assert (workspaces.workload / "README.md").exists()
        assert merged != run.base_revision

    def test_mainline_untouched_before_promotion(self, run, workspaces):
        workspaces.write_file(run, "danger.py", "x = 1\n")
        workspaces.commit(run, "feat: danger")
        assert not (workspaces.workload / "danger.py").exists()


class TestEvidence:
    def _runner(self, tmp_path, store, registry, workspaces, policy):
        return EvidenceRunner(store, registry, workspaces, policy, tmp_path / "evidence")

    def test_evidence_bound_to_exact_revision(self, tmp_path, store, registry, workspaces, policy, run):
        runner = self._runner(tmp_path, store, registry, workspaces, policy)
        workspaces.write_file(run, "check.py", "print('ok')\n")
        rev = workspaces.commit(run, "add check")
        art = runner.run_check(run, "smoke", [sys.executable, "check.py"])
        assert art.data["passed"] is True
        assert art.revision == rev
        valid, mismatched = runner.evidence_valid_for(run, rev)
        assert len(valid) == 1 and not mismatched

    def test_stale_revision_evidence_flagged(self, tmp_path, store, registry, workspaces, policy, run):
        runner = self._runner(tmp_path, store, registry, workspaces, policy)
        workspaces.write_file(run, "check.py", "print('ok')\n")
        workspaces.commit(run, "add check")
        runner.run_check(run, "smoke", [sys.executable, "check.py"])
        workspaces.write_file(run, "new.py", "y = 2\n")
        new_rev = workspaces.commit(run, "more code")
        valid, mismatched = runner.evidence_valid_for(run, new_rev)
        assert not valid and len(mismatched) == 1

    def test_secrets_stripped_from_check_environment(self, tmp_path, store, registry, workspaces, policy, run, monkeypatch):
        monkeypatch.setenv("FAKE_API_KEY", "super-secret")
        runner = self._runner(tmp_path, store, registry, workspaces, policy)
        workspaces.write_file(run, "leak.py", "import os; print(os.environ.get('FAKE_API_KEY', 'ABSENT'))\n")
        workspaces.commit(run, "leak probe")
        art = runner.run_check(run, "leak", [sys.executable, "leak.py"])
        assert "ABSENT" in art.data["output_tail"]
        assert "super-secret" not in art.data["output_tail"]

    def test_disallowed_command_denied(self, tmp_path, store, registry, workspaces, policy, run):
        runner = self._runner(tmp_path, store, registry, workspaces, policy)
        with pytest.raises(PolicyDenied):
            runner.run_check(run, "exfil", ["curl", "http://example.com"])
