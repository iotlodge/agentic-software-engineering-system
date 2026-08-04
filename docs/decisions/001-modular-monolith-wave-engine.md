# ADR 001 — Modular-monolith control plane with a wave-based engine

**Status:** accepted

## Context

The blueprint requires non-linear, stateful orchestration: conditional
transitions, parallel branches, synchronization, re-planning, and recovery. It
also has to be runnable and inspectable by a reviewer on a laptop.

## Decision

One Python process with clean module seams (engine, store, policy, approvals,
artifacts, workspace, evidence) rather than microservices or a heavyweight
workflow framework. The engine advances runs in **waves**: each iteration
computes the ready set from the persisted graph, executes it in a thread pool,
then processes failures → gates → plan materialization → scripted revisions.
`tick()` returns whenever the run pauses (approval) or terminates; calling it
again is resume.

## Consequences

- Fan-out/fan-in is real (thread pool; `wave.started` events record wave
  membership) without distributed-systems overhead.
- Every wave boundary is a durable checkpoint, which makes restart recovery
  trivial to reason about: reload, re-queue anything left `RUNNING`, continue.
- A framework (Temporal, LangGraph, Airflow) would obscure exactly the behavior
  being evaluated. The durable run model, gates and recovery ARE the design;
  the modest scheduler is deliberately transparent. The trade-off — waves add
  a small synchronization barrier per iteration — is acceptable at this scale
  and is measured (`waves` metric).
