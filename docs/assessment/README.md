# Agentic Software Engineering System — Assignment Assessment

## Purpose

This package interprets the URL-shortener assignment as a systems-engineering problem and proposes a defensible path from brief to working prototype. It is an assessment and solution blueprint, not yet the implementation.

The key conclusion is:

> The URL shortener is the proving workload. The system being evaluated is a governed, stateful software-engineering control plane that can convert requirements into reviewable repository changes while preserving evidence, approvals, safety, and recovery behavior.

## Reading order

1. [01 — Assignment deconstruction](01-assignment-deconstruction.md)  
   What the assignment says, what it really tests, and how success should be framed.
2. [02 — Gaps, assumptions, and questions](02-gaps-assumptions-and-questions.md)  
   Missing specifications, contradictions, proposed defaults, and questions for the evaluator.
3. [03 — Reference architecture and orchestration](03-reference-architecture-and-orchestration.md)  
   Proposed system boundary, components, state model, dependency graph, gates, guardrails, and failure controls.
4. [04 — Scenario and delivery plan](04-scenarios-and-delivery-plan.md)  
   Greenfield, brownfield, and ambiguous scenarios plus an incremental implementation sequence.
5. [05 — Validation, evaluation, and demonstration](05-validation-evaluation-and-demo.md)  
   Verification strategy, metrics, traceability, acceptance criteria, and a reviewer-friendly demo script.

## Recommended one-sentence pitch

“A policy-governed, graph-based engineering workflow that uses specialized agents to plan, implement, test, document, and prepare a URL-shortener change for release, with durable state, human gates, isolated execution, complete lineage, and recovery from failure or changed requirements.”

## Scope recommendation

Build one coherent repository with two clearly separated products:

- **Engineering control plane:** intake, normalization, planning, orchestration, approvals, evidence, policy, metrics, and audit trail.
- **URL-shortener workload:** API, persistence, redirects, analytics, reliability behavior, tests, API contract, and operations documentation.

Demonstrate three runs against the same repository:

1. A greenfield run builds the baseline service.
2. A brownfield run changes the existing service and proves impact analysis/regression protection.
3. An ambiguous run pauses for clarification or proceeds under a reversible, explicitly approved assumption.

This produces a much stronger narrative than three unrelated demos because it shows state accumulation, codebase reasoning, change impact, and controlled evolution.

## Reasoning transparency

These documents provide decision criteria, alternatives, assumptions, evidence expectations, and trade-offs. They intentionally do not expose private model chain-of-thought. The artifact a reviewer needs is a concise, reproducible decision record—not hidden internal deliberation.

## Proposed outcome status

| Item | Status in this package |
|---|---|
| Assignment interpretation | Complete |
| Missing requirements and assumptions | Complete |
| Reference architecture | Proposed |
| Scenario design | Proposed |
| Delivery and validation strategy | Proposed |
| Runnable prototype | Not implemented in this assessment phase |
| Framework/language selection | Recommended defaults, pending constraints |

