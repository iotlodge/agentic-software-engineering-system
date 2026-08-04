"""Deterministic evidence collection.

An agent saying "tests pass" is not evidence. This runner executes real
commands in the run's worktree, strips secrets from the environment, and
records exit code, duration, output digest and the exact git revision the
check ran against. Evidence from a different revision cannot satisfy a gate.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from pathlib import Path

from .artifacts import ArtifactRegistry
from .models import Artifact, Event, Run
from .policy import PolicyEngine
from .workspace import WorkspaceManager, _git

_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


def _clean_env() -> dict[str, str]:
    """Sub-process environment with credentials removed."""
    return {
        k: v for k, v in os.environ.items()
        if not any(marker in k.upper() for marker in _SECRET_MARKERS)
    }


class EvidenceRunner:
    def __init__(self, store, registry: ArtifactRegistry, workspaces: WorkspaceManager,
                 policy: PolicyEngine, evidence_dir: str | Path):
        self.store = store
        self.registry = registry
        self.workspaces = workspaces
        self.policy = policy
        self.evidence_dir = Path(evidence_dir)

    def run_check(
        self,
        run: Run,
        name: str,
        argv: list[str],
        timeout: int = 300,
        produced_by: str = "",
        parents: list[str] | None = None,
    ) -> Artifact:
        """Execute one check and publish an evidence artifact bound to the revision."""
        self.policy.enforce(run.id, "command.run", resource=Path(argv[0]).name, subject="verifier")
        worktree = self.workspaces.worktree(run)
        revision = _git(worktree, "rev-parse", "HEAD")
        started = time.monotonic()
        try:
            proc = subprocess.run(
                argv, cwd=worktree, env=_clean_env(),
                capture_output=True, text=True, timeout=timeout,
            )
            exit_code, output = proc.returncode, proc.stdout + proc.stderr
        except subprocess.TimeoutExpired:
            exit_code, output = -1, f"check timed out after {timeout}s"
        duration = round(time.monotonic() - started, 3)

        out_dir = self.evidence_dir / run.id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{name}.txt"
        out_path.write_text(output)
        output_digest = "sha256:" + hashlib.sha256(output.encode()).hexdigest()[:24]

        artifact = self.registry.publish(
            run, name=f"evidence_{name}", kind="evidence",
            data={
                "check": name,
                "argv": argv,
                "exit_code": exit_code,
                "passed": exit_code == 0,
                "duration_s": duration,
                "output_path": str(out_path),
                "output_digest": output_digest,
                "output_tail": output[-2000:],
            },
            parents=parents, produced_by=produced_by, revision=revision,
        )
        self.store.append_event(Event(
            run_id=run.id, type="evidence.recorded", task=produced_by or None,
            payload={"check": name, "passed": exit_code == 0, "exit_code": exit_code,
                     "revision": revision, "duration_s": duration},
        ))
        return artifact

    def evidence_valid_for(self, run: Run, revision: str) -> tuple[list[Artifact], list[Artifact]]:
        """Split current evidence into (valid_for_revision, mismatched)."""
        arts = [a for a in self.store.get_artifacts(run.id, include_stale=False) if a.kind == "evidence"]
        valid = [a for a in arts if a.revision == revision]
        mismatched = [a for a in arts if a.revision != revision]
        return valid, mismatched
