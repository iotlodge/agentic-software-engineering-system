# ADR 007 — Rust parity workload

**Status:** accepted

## Context

The control plane claims to be contract-first: the workload's implementation
language should be a detail. A claim like that deserves a proof.

## Decision

`shortener-rs/` implements the same API contract in axum + rusqlite: identical
routes, status codes (201/200 replay, 307, 404, 410, 401/403, 409), error
envelope, inclusive expiry boundary, optimistic concurrency, audit rows — and
it embeds the **same SQL migration files** at compile time. Eight integration
tests mirror the Python suite's contract assertions.

Deliberate divergences are declared, not hidden: analytics are recorded
synchronously (the batched, bounded-delay sink is demonstrated on the Python
side) and there is no cache layer; the stats endpoint reports
`"consistency": "synchronous (rust variant)"`.

## Consequences

- Two implementations against one contract and one schema demonstrate that the
  scenarios' seeds/checks could target either language.
- Maintenance cost of a second implementation; accepted because parity is
  verified by tests, not by promise.
