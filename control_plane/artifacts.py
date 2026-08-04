"""Versioned artifact registry with provenance and selective invalidation.

Artifacts form a provenance DAG (parent edges by artifact id). When an
upstream artifact gains a new version, only its transitive descendants become
stale; unaffected evidence remains valid. This is what makes re-planning
selective instead of a full restart.
"""

from __future__ import annotations

from typing import Any

from .models import Artifact, Event, Run
from .store import Store


class ArtifactRegistry:
    def __init__(self, store: Store):
        self.store = store

    def publish(
        self,
        run: Run,
        name: str,
        kind: str,
        data: dict[str, Any],
        parents: list[str] | None = None,
        produced_by: str = "",
        revision: str = "",
    ) -> Artifact:
        """Publish a new version of a named artifact, linked to its inputs."""
        previous = self.store.get_artifact(run.id, name)
        parent_ids = []
        for parent_name in parents or []:
            parent = self.store.get_artifact(run.id, parent_name)
            if parent is not None:
                parent_ids.append(parent.id)
        artifact = Artifact(
            run_id=run.id, name=name, kind=kind, data=data,
            version=(previous.version + 1) if previous else 1,
            parents=parent_ids, produced_by=produced_by, revision=revision,
        )
        self.store.save_artifact(artifact)
        self.store.append_event(Event(
            run_id=run.id, type="artifact.created", task=produced_by or None,
            payload={"artifact_id": artifact.id, "name": name, "kind": kind,
                     "version": artifact.version, "digest": artifact.digest,
                     "parents": parent_ids, "revision": revision},
        ))
        return artifact

    def invalidate_descendants(self, run: Run, changed_name: str) -> list[str]:
        """Mark every transitive descendant of the named artifact stale.

        Returns the distinct names of invalidated artifacts. The changed
        artifact itself is NOT marked stale (its new version is the truth).
        """
        artifacts = self.store.get_artifacts(run.id)
        changed = self.store.get_artifact(run.id, changed_name)
        if changed is None:
            return []
        # Old versions of the changed artifact are stale by definition.
        for art in artifacts:
            if art.name == changed_name and art.version < changed.version and not art.stale:
                art.stale = True
                self.store.save_artifact(art)
        children: dict[str, list[Artifact]] = {}
        for art in artifacts:
            for pid in art.parents:
                children.setdefault(pid, []).append(art)
        # Seed traversal from ALL versions of the changed artifact: descendants
        # were derived from prior versions, which is exactly why they are stale.
        frontier = [a.id for a in artifacts if a.name == changed_name]
        stale_names: list[str] = []
        seen: set[str] = set()
        while frontier:
            node = frontier.pop()
            for child in children.get(node, []):
                if child.id in seen or child.name == changed_name:
                    continue
                seen.add(child.id)
                if not child.stale:
                    child.stale = True
                    self.store.save_artifact(child)
                    if child.name not in stale_names:
                        stale_names.append(child.name)
                    self.store.append_event(Event(
                        run_id=run.id, type="artifact.invalidated",
                        payload={"artifact_id": child.id, "name": child.name,
                                 "cause": changed_name},
                    ))
                frontier.append(child.id)
        return stale_names

    def provenance(self, run_id: str) -> list[dict[str, Any]]:
        """Flat provenance listing for display: node + parent names."""
        artifacts = self.store.get_artifacts(run_id)
        by_id = {a.id: a for a in artifacts}
        return [
            {
                "id": a.id, "name": a.name, "kind": a.kind, "version": a.version,
                "digest": a.digest, "stale": a.stale, "produced_by": a.produced_by,
                "revision": a.revision,
                "parents": [by_id[p].name for p in a.parents if p in by_id],
            }
            for a in artifacts
        ]
