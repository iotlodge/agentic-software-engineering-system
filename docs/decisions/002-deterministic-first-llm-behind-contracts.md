# ADR 002 — Deterministic skeleton first; LLMs optional behind worker contracts

**Status:** accepted

## Context

The differentiating claims (gates, recovery, re-planning, evidence binding)
must be provable in CI. Live models are variable, slow, and priced.

## Decision

Phase 1 of the blueprint is the law of the codebase: every orchestration path
runs with deterministic, seed-driven workers (mock mode). LLM roles (requirement
analyst, planner) sit behind the same capability contracts and are enabled with
`--mode live`. The adapter is provider-neutral httpx (Anthropic/OpenAI), with
schema validation and exactly one constrained repair attempt; the planner may
only arrange the scenario's bounded action palette.

Scenario seeds are complete file versions (not patches), so re-application after
a crash is idempotent, and the injected brownfield defect is an honest, scripted
fault — documented in the scenario file itself.

## Consequences

- 116 tests run without credentials; model variability cannot mask or fake
  orchestration behavior.
- Live mode adds interpretation quality without adding authority: agents cannot
  invent capabilities, touch policy, or self-declare completion.
- The mock/live seam is also the demo-resilience plan: the demo never depends
  on a provider being up.
