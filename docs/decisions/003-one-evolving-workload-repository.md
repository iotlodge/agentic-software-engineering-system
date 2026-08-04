# ADR 003 — One workload repository evolved across all three scenarios

**Status:** accepted

## Context

The brief demands both "build from scratch" and brownfield reasoning — a
contradiction if the scenarios are independent.

## Decision

Resolve it temporally: the greenfield run builds the baseline in an empty
`.ase/workload` repository; its **promoted** output is the brownfield input;
the ambiguous run optimizes the twice-evolved service. Each run works on branch
`run/<id>` in its own git worktree; only an approved release merges to `main`.

## Consequences

- The evaluator can trace accumulated history: three merge commits, requirement
  and plan versions, approvals, and evidence across runs
  (`test_fleet_metrics_reflect_three_successful_runs` asserts the merge log).
- Brownfield impact analysis operates on real prior output, not a fixture.
- Scenario order matters; the demo (`ase demo`) encodes it, and each scenario's
  regression suite keeps earlier behavior protected (v1 tests still run — and
  must pass — in v2 and v3 workloads).
