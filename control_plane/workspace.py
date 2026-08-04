"""Isolated repository workspaces.

Each run gets its own branch and git worktree of the workload repository.
Agents never touch the mainline; promotion to main happens only after the
final human gate. Every write is policy-checked; checkpoints and rollbacks
are real git operations recorded in the audit trail.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .models import Event, Run
from .policy import PolicyEngine
from .store import Store


class WorkspaceError(Exception):
    pass


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=ase-orchestrator", "-c", "user.email=ase@localhost", *args],
        cwd=cwd, capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise WorkspaceError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


class WorkspaceManager:
    def __init__(self, base_dir: str | Path, store: Store, policy: PolicyEngine):
        self.base = Path(base_dir)
        self.workload = self.base / "workload"
        self.store = store
        self.policy = policy

    # -- workload repository lifecycle ---------------------------------------
    def ensure_workload(self) -> Path:
        if not (self.workload / ".git").exists():
            self.workload.mkdir(parents=True, exist_ok=True)
            _git(self.workload, "init", "-b", "main")
            _git(self.workload, "commit", "--allow-empty", "-m", "chore: initialize workload repository")
        return self.workload

    def workload_revision(self) -> str:
        return _git(self.workload, "rev-parse", "HEAD")

    # -- per-run worktrees ----------------------------------------------------
    def create(self, run: Run) -> Path:
        self.ensure_workload()
        worktree = self.base / "runs" / run.id / "worktree"
        worktree.parent.mkdir(parents=True, exist_ok=True)
        branch = f"run/{run.id}"
        _git(self.workload, "worktree", "add", "-b", branch, str(worktree), "HEAD")
        run.branch = branch
        run.workspace_path = str(worktree)
        run.base_revision = _git(worktree, "rev-parse", "HEAD")
        run.current_revision = run.base_revision
        self.store.save_run(run)
        self.store.append_event(Event(
            run_id=run.id, type="workspace.created",
            payload={"branch": branch, "path": str(worktree), "base_revision": run.base_revision},
        ))
        return worktree

    def worktree(self, run: Run) -> Path:
        if not run.workspace_path:
            raise WorkspaceError(f"run {run.id} has no workspace")
        return Path(run.workspace_path)

    # -- policy-checked file operations ---------------------------------------
    def write_file(self, run: Run, rel_path: str, content: str) -> None:
        rel = Path(rel_path)
        if rel.is_absolute() or ".." in rel.parts:
            raise WorkspaceError(f"path escapes workspace: {rel_path}")
        self.policy.enforce(run.id, "workspace.write", resource=rel_path, subject="implementer")
        target = self.worktree(run) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def sync_seed(self, run: Run, seed_dir: str | Path) -> list[str]:
        """Copy a seed tree into the worktree through the policy-checked path.

        Returns the relative paths written. Seeds are complete file contents
        (not patches) so re-application after a crash is idempotent.
        """
        seed = Path(seed_dir)
        if not seed.is_dir():
            raise WorkspaceError(f"seed directory missing: {seed}")
        written = []
        for src in sorted(seed.rglob("*")):
            if src.is_dir() or src.name == ".gitkeep":
                continue
            rel = str(src.relative_to(seed))
            self.write_file(run, rel, src.read_text())
            written.append(rel)
        return written

    # -- revisions, diffs, rollback -------------------------------------------
    def commit(self, run: Run, message: str) -> str:
        wt = self.worktree(run)
        _git(wt, "add", "-A")
        status = _git(wt, "status", "--porcelain")
        if status:
            _git(wt, "commit", "-m", message)
        run.current_revision = _git(wt, "rev-parse", "HEAD")
        self.store.save_run(run)
        return run.current_revision

    def diff_stats(self, run: Run) -> dict:
        wt = self.worktree(run)
        raw = _git(wt, "diff", "--numstat", run.base_revision, "HEAD")
        files, insertions, deletions = 0, 0, 0
        for line in raw.splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                files += 1
                insertions += int(parts[0]) if parts[0].isdigit() else 0
                deletions += int(parts[1]) if parts[1].isdigit() else 0
        return {"files_changed": files, "insertions": insertions,
                "deletions": deletions, "changed_lines": insertions + deletions}

    def changed_files(self, run: Run) -> list[str]:
        wt = self.worktree(run)
        raw = _git(wt, "diff", "--name-only", run.base_revision, "HEAD")
        return [l for l in raw.splitlines() if l]

    def rollback(self, run: Run, revision: str, reason: str = "") -> None:
        wt = self.worktree(run)
        _git(wt, "reset", "--hard", revision)
        _git(wt, "clean", "-fd")
        run.current_revision = revision
        self.store.save_run(run)
        self.store.append_event(Event(
            run_id=run.id, type="rollback.executed",
            payload={"restored_revision": revision, "reason": reason},
        ))

    # -- promotion and safe-stop ----------------------------------------------
    def promote(self, run: Run) -> str:
        """Merge the run branch into workload main. Only called after the
        final release approval has been consumed."""
        _git(self.workload, "merge", "--no-ff", run.branch, "-m",
             f"merge {run.branch}: {run.scenario or run.id} (approved release)")
        merged = self.workload_revision()
        self.store.append_event(Event(
            run_id=run.id, type="release.promoted",
            payload={"merged_revision": merged, "branch": run.branch},
        ))
        return merged

    def preserve_for_recovery(self, run: Run, reason: str) -> str:
        """Safe-stop support: keep the worktree, write recovery instructions."""
        note = self.base / "runs" / run.id / "RECOVERY.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(
            f"# Recovery notes for {run.id}\n\n"
            f"- Stop reason: {reason}\n"
            f"- Branch: `{run.branch}`\n"
            f"- Worktree (preserved): `{run.workspace_path}`\n"
            f"- Base revision: `{run.base_revision}`\n"
            f"- Last revision: `{run.current_revision}`\n\n"
            "The workload mainline was not modified. Inspect the worktree, then\n"
            "either resume the run (`ase resume <run_id>`) after fixing the cause,\n"
            "or remove the worktree (`git worktree remove <path>`) to abandon it.\n"
        )
        return str(note)

    def remove_worktree(self, run: Run) -> None:
        wt = self.worktree(run)
        try:
            _git(self.workload, "worktree", "remove", "--force", str(wt))
        except WorkspaceError:
            shutil.rmtree(wt, ignore_errors=True)
