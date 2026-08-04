# ADR 006 — SQLite for durable state; git worktrees for isolation

**Status:** accepted

## Context

Durability and isolation must be real, but a reviewer should not need Docker,
Postgres, or cloud credentials to verify them.

## Decision

- **State:** SQLite (WAL, busy timeout, connection-per-operation). Mutable
  tables materialize current state; the `events` table is append-only and never
  updated. `:memory:` keeps unit tests instant; the file store survives process
  restarts (tested by rebuilding the entire object graph on the same file).
- **Isolation:** each run gets branch `run/<id>` in a dedicated git worktree.
  Writes go through a policy-checked path API (no absolute paths, no `..`,
  protected patterns denied). Promotion to `main` is a real `--no-ff` merge,
  performed only after the release approval is consumed.

## Consequences

- Zero-dependency reproducibility; evidence and diffs are ordinary git objects
  a reviewer can inspect with standard tools.
- SQLite's single-writer model caps worker parallelism; fine at prototype
  scale, and the Store seam is where Postgres would slot in.
- Host-level sandboxing (containers, seccomp) is out of scope and documented as
  a limitation rather than simulated.
