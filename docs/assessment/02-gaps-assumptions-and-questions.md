# 02 — Gaps, Assumptions, and Questions

## Why the gaps matter

The brief deliberately leaves room for engineering judgment, but several omissions materially affect design and evaluation. The correct response is not to invent certainty. It is to classify each gap, choose a reversible default where possible, and make high-impact assumptions visible to the evaluator.

## Highest-priority ambiguities

| Gap | Why it matters | Recommended working assumption | Approval needed? |
|---|---|---|---|
| Is an LLM provider or model mandated? | Affects reproducibility, cost, structured output and deployment | Use a provider-neutral adapter and one configured model; support a deterministic mock for tests | Only if evaluator mandates a platform |
| Must the prototype actually modify a repository? | Separates a real executor from a workflow simulator | Yes—operate on an isolated Git worktree and produce a reviewable diff/commit | No |
| Is deployment required or only release readiness? | Deployment introduces credentials and external side effects | Stop at a release-ready package by default; make deployment an optional approval-gated adapter | Yes before external deployment |
| What counts as “human approval”? | A simple CLI prompt may be insufficient | Persist a scoped approval request and resume with approver identity, decision, reason and timestamp | Confirm interface expectation |
| Which high-impact actions require approval? | Determines autonomy boundaries | Protected-branch write, external deployment, destructive DB action, secret access, security-policy weakening, large/high-risk diff | Policy should be explicit |
| What does “rollback” cover? | Code, workflow, database and deployment rollback differ | Demonstrate worktree rollback and workflow checkpoint recovery; produce DB/deploy rollback plans where actual rollback is unsafe locally | Confirm expected depth |
| What scale and availability are expected of the shortener? | Changes persistence, caching, IDs and infrastructure | Single-region production-shaped prototype; document horizontal scale path and targets | Confirm targets if scored |
| What analytics are required? | “Analytics” can mean a counter or event pipeline | Asynchronous click events plus aggregate endpoint; collect minimal, privacy-conscious fields | Confirm privacy/retention |
| Are all three scenarios expected live? | Affects demo and implementation scope | Make all three reproducible; demo one complete run and replay evidence for others if time is constrained | Confirm demo duration |
| How is success measured? | Metrics need thresholds and denominators | Define per-run and cross-run measures with a small included benchmark dataset | Confirm any mandated rubric weights |

## Missing target-system requirements

### API behavior

The brief does not specify:

- endpoint shapes and versioning;
- custom aliases;
- link expiration;
- collision behavior;
- idempotency for create requests;
- redirect status (`301`, `302`, `307`, or `308`);
- deletion/disable semantics;
- authentication/authorization;
- analytics consistency and delay;
- error model and rate-limit headers;
- bulk operations;
- URL validation and allowed schemes.

Recommended minimum contract:

| Capability | Proposed behavior |
|---|---|
| Create short link | `POST /v1/links`, accepts destination and optional expiry; returns stable short code |
| Resolve | `GET /{code}`, returns temporary redirect to permit target updates and avoid undesirable permanent caching |
| Link details | `GET /v1/links/{code}` |
| Analytics | `GET /v1/links/{code}/stats`, explicitly eventually consistent |
| Disable | `DELETE /v1/links/{code}` as a soft disable, not physical deletion |
| Health | `/health/live` and `/health/ready` |
| Schema | OpenAPI checked into the repository and contract-tested |

Recommended safety defaults:

- Allow only `http` and `https` destinations.
- Reject credentials embedded in URLs.
- Enforce length limits and normalization rules.
- Treat codes as opaque, case-sensitive identifiers.
- Avoid recording full IP addresses or sensitive query values in analytics.
- Use rate limiting at the service boundary; document local fallback if Redis is absent.
- Use temporary redirects by default and state the cache/analytics rationale.

### Service-level objectives

No latency, availability, durability or recovery targets are given. Proposed prototype targets should be labeled as design targets rather than claimed production measurements:

- resolve endpoint p95 latency target under a defined local load;
- create endpoint success-rate target;
- no duplicate-code mapping under concurrency tests;
- analytics loss tolerance and maximum aggregation delay;
- restore-time and data-loss assumptions;
- load-test envelope tied to the demo hardware.

Using percentages without workload, duration and environment is misleading. The final report should always state the measurement conditions.

## Missing engineering-system requirements

### Execution environment

The brief does not state whether agents may:

- run arbitrary commands;
- access the public network;
- install dependencies;
- read secrets;
- use containers;
- create branches or commits;
- open or merge pull requests;
- deploy services.

Proposed posture: default deny, grant per role and task. All code execution occurs in a disposable workspace with resource limits and a command allowlist. Network and secret access are separate capabilities, never implicit consequences of shell access.

### Artifact and state contracts

The required contents and storage of cross-stage context are not specified. Free-form documents alone will make dynamic re-planning fragile. Use typed, versioned artifacts for:

- normalized requirements;
- open questions and approved assumptions;
- architecture/impact report;
- plan and dependency graph;
- task results;
- repository change set;
- test and policy evidence;
- risk register;
- approvals;
- release-readiness report.

Human-readable Markdown can be rendered from the same records, but the orchestrator should reason over structured data with stable identifiers.

### Definition of agent boundaries

The brief says “agents” but does not require one process, one model, or one prompt per role. A defensible interpretation is capability-based separation:

- requirement analyst;
- codebase/architecture analyst;
- planner;
- implementer;
- test/verification agent;
- security/policy reviewer;
- documentation/release agent.

Not every role must be a unique LLM instance. Separation should exist in contracts, context, permissions, and accountability. Deterministic tools should perform deterministic work.

### Governance model

Missing details include:

- approval levels;
- who can approve;
- approval expiry;
- whether a changed diff invalidates approval;
- emergency override behavior;
- policy exception handling;
- separation of proposer and approver.

Proposed default: approvals are bound to a content digest, scope, risk level and expiration. If the approved artifact changes materially, the approval is stale and the gate closes again.

## Contradictions and proposed resolutions

### Greenfield “from scratch” versus brownfield reasoning

Resolve by evolving one repository across scenario runs. The greenfield output becomes the brownfield input.

### Production-grade versus prototype

Implement production-shaped seams and safety behavior, but explicitly bound scale and operational claims. Do not spend the assignment on infrastructure that adds little orchestration evidence.

### Autonomous execution versus human oversight

Use risk-tiered autonomy:

| Risk tier | Examples | Default authority |
|---|---|---|
| Low | Read/search code, draft artifacts, run read-only analysis | Automatic |
| Medium | Edit isolated worktree, add dependency, run tests | Automatic within budget; record evidence |
| High | Migration, auth/security changes, large diff, external network write | Human approval before execution or promotion |
| Critical | Production deploy, destructive data action, protected-branch merge, guardrail disable | Explicit named approval; often out of prototype scope |

### Dynamic re-planning versus stable approvals

Version all inputs and derive approval validity from their digests. Re-plan only invalidated descendants. Previously approved work remains valid only when its inputs and risk classification are unchanged.

## Proposed explicit assumptions for the submission

These assumptions are reasonable unless evaluator answers override them:

1. The deliverable is locally runnable with container-based dependencies.
2. External production deployment is optional and disabled by default.
3. The baseline implementation language may be selected by the candidate.
4. PostgreSQL is the system of record; optional Redis improves rate limiting/caching but is not the source of truth.
5. Analytics are eventually consistent and privacy-minimized.
6. Authentication for administrative APIs can be a documented prototype mechanism; public redirects remain unauthenticated.
7. All generated code changes occur on an isolated branch/worktree.
8. The orchestration state is durable and resumable.
9. LLM output is untrusted until schema validation and independent checks pass.
10. Completion means “review-ready and release-ready,” not autonomous merge or production release.
11. The three scenarios are successive changes in one repository.
12. Evaluation emphasizes demonstrated control behavior more than raw feature count.

## Questions to send the evaluator

Use a short set; avoid turning the assignment into a requirements interrogation.

1. Is a specific language, model provider, orchestration framework, or deployment target required?
2. Should the system stop at a review-ready pull request/change package, or must it deploy the URL shortener?
3. Which actions must demonstrate human approval, and what approval interface is acceptable for the prototype?
4. Are there target SLOs, security/compliance standards, analytics fields, or load levels against which the service will be judged?
5. Must all three scenarios execute live during evaluation, and is there a target demo duration or compute budget?
6. Should brownfield work target a provided repository, or may the greenfield output become the brownfield baseline?

## Decisions that should not be blocked on answers

Proceed now with:

- explicit graph orchestration;
- durable state and artifact lineage;
- isolated repository execution;
- versioned structured outputs;
- risk-based policy gates;
- deterministic verification;
- a single evolving shortener repository;
- local reproducibility.

These remain correct under almost any reasonable evaluator response.

