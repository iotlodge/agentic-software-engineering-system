"""Dependency-graph semantics: validation, readiness, and staleness traversal.

A chain encodes order; this graph encodes readiness, dependency, concurrency,
invalidation and recovery. The plan graph is data (Task records), not
hard-coded control flow.
"""

from __future__ import annotations

from .models import Task, TaskStatus


class GraphError(Exception):
    pass


def validate(tasks: list[Task]) -> None:
    """Reject unknown dependencies and cycles (Kahn's algorithm)."""
    names = {t.name for t in tasks}
    for t in tasks:
        for dep in t.depends_on:
            if dep not in names:
                raise GraphError(f"task {t.name!r} depends on unknown task {dep!r}")
    indegree = {t.name: len(t.depends_on) for t in tasks}
    children: dict[str, list[str]] = {t.name: [] for t in tasks}
    for t in tasks:
        for dep in t.depends_on:
            children[dep].append(t.name)
    queue = [n for n, d in indegree.items() if d == 0]
    seen = 0
    while queue:
        node = queue.pop()
        seen += 1
        for child in children[node]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if seen != len(tasks):
        cyclic = sorted(n for n, d in indegree.items() if d > 0)
        raise GraphError(f"dependency cycle involving: {', '.join(cyclic)}")


def ready_tasks(tasks: list[Task]) -> list[Task]:
    """Pending tasks whose dependencies are all satisfied — the next wave."""
    by_name = {t.name: t for t in tasks}
    out = []
    for t in tasks:
        if t.status != TaskStatus.PENDING:
            continue
        if all(by_name[d].status.satisfies_dependency for d in t.depends_on if d in by_name):
            out.append(t)
    return out


def running_tasks(tasks: list[Task]) -> list[Task]:
    return [t for t in tasks if t.status == TaskStatus.RUNNING]


def unfinished(tasks: list[Task]) -> list[Task]:
    return [t for t in tasks if t.status in {TaskStatus.PENDING, TaskStatus.RUNNING}]


def all_satisfied(tasks: list[Task]) -> bool:
    return all(t.status.satisfies_dependency or t.status == TaskStatus.CANCELLED for t in tasks)


def descendants(tasks: list[Task], roots: set[str]) -> set[str]:
    """All task names transitively depending on any root (roots excluded)."""
    children: dict[str, list[str]] = {t.name: [] for t in tasks}
    for t in tasks:
        for dep in t.depends_on:
            children.setdefault(dep, []).append(t.name)
    out: set[str] = set()
    frontier = list(roots)
    while frontier:
        node = frontier.pop()
        for child in children.get(node, []):
            if child not in out:
                out.add(child)
                frontier.append(child)
    return out
