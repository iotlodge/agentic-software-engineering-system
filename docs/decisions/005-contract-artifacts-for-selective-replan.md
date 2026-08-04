# ADR 005 — Requirement facets as separate artifacts to make re-planning selective

**Status:** accepted

## Context

If every proposal descends from one monolithic `requirement` artifact, any
requirement change invalidates everything — "selective" re-planning degenerates
to a full restart.

## Decision

Material assumptions are published as separate versioned **contract** artifacts
(e.g. `consistency_contract`), parented to the requirement. Plan entries declare
which facet each task's output derives from (`params.parents`). A revision
republishes only the changed contract; provenance traversal then invalidates
exactly its descendants. The plan is orchestration metadata, not a content
parent of proposals — a plan version bump does not stale unaffected work.

## Consequences

- The ambiguous scenario's 60s→5s revision re-runs the analytics subgraph while
  the schema migration, its approval, and requirement-derived work survive —
  asserted by tests at both the engine and scenario level.
- Provenance modeling is now a design responsibility of scenario/plan authors;
  wrong parent declarations produce wrong invalidation. This is stated in the
  risk register rather than papered over.
