# 05 — Validation, Evaluation, and Demonstration

## Validation principle

The system must prove both:

1. **the produced URL-shortener change is correct enough to review**, and
2. **the engineering workflow behaved correctly while producing it**.

Testing only the generated service leaves the most important part of the assignment unverified.

## Two-layer verification model

### A. Control-plane verification

| Concern | Tests/evidence |
|---|---|
| Graph validity | Cycle detection, missing dependency rejection, ready-task calculation |
| Parallel execution | Independent tasks run concurrently; fan-in waits for all required outputs |
| State durability | Kill/restart test resumes from last committed state without duplicate side effect |
| Idempotency | Replayed events/tasks do not create duplicate commits, approvals or artifacts |
| Approval | Run pauses durably; wrong/expired/stale approval is rejected; valid approval resumes |
| Policy | Allowed, denied and approval-required actions tested; policy version recorded |
| Retry | Only retryable failures repeat; attempt and time budgets enforced |
| Fallback | Classified failure selects an allowed alternate path |
| Safe-stop | Active side effects cease; state/evidence persist; workspace disposition recorded |
| Rollback | Failed integration restores known repository checkpoint; rollback event recorded |
| Re-planning | Changed upstream artifact invalidates the correct descendants only |
| Evidence binding | Checks from a different revision cannot satisfy the current gate |
| Structured outputs | Invalid agent output is rejected/repaired within budget |
| Isolation | Writes outside permitted workspace and unauthorized network/secret access fail |
| Audit | Material actions have actor, time, input/output digest, outcome and trace linkage |

### B. URL-shortener verification

| Layer | Examples |
|---|---|
| Unit | URL validation, code generation, expiry, redirect decision, analytics aggregation |
| Property-based | Code alphabet/length invariants, round trips, collision handling, time boundaries |
| Persistence | Unique constraints, migration forward path, populated-baseline compatibility |
| API contract | OpenAPI conformance, error schema, status codes, idempotency behavior |
| Integration | Create → resolve → event → stats; update → cache invalidation → analytics continuity |
| Concurrency | Colliding creates, concurrent updates, event accounting |
| Security | Unsafe scheme rejection, auth failures, injection, secret scan, dependency scan |
| Reliability | Database/cache/event-sink failure, timeouts, restarts and degraded behavior |
| Performance | Defined cold/hot workload with percentiles, error rate and environment recorded |
| Operations | Liveness/readiness semantics, migration startup behavior, structured logs |

## Verification is stronger than model self-review

Use agents for interpretation, design critique, test proposal and failure diagnosis. Use deterministic systems for:

- schema validation;
- graph invariants;
- compiling/type checking;
- unit/integration/contract tests;
- static analysis and dependency/secret scanning;
- migration execution;
- diff size and protected-file checks;
- artifact digesting;
- latency and error measurement.

An independent reviewer agent may add useful critique, but it does not replace objective gates.

## Requirement-to-evidence traceability matrix

The final repository should contain a live version of this matrix with artifact IDs and run links.

| Assignment requirement | Planned evidence | Scenario |
|---|---|---|
| Understand intent and ambiguity | Versioned normalized requirement; ambiguity scores; approved assumption | All, especially ambiguous |
| Decompose with dependencies | Persisted DAG; task contracts; graph validation | All |
| Brownfield reasoning | Code/data/API impact artifact tied to base revision | Brownfield |
| Sequential and parallel paths | Trace showing fan-out/fan-in and dependency scheduling | Greenfield |
| Stateful execution | Restart/resume test and durable run record | Greenfield/control-plane tests |
| Cross-stage context and lineage | Artifact provenance graph and input/output digests | All |
| Human checkpoints | Scoped plan/schema/final approvals and stale-approval test | Brownfield, ambiguous |
| Retry and fallback | Injected transient failure and alternate-path event | Greenfield/fault tests |
| Rollback and safe-stop | Failed integration rollback; budget-exhaustion safe-stop report | Brownfield/fault tests |
| Policy guardrails | Allow/deny/approval-required policy test results | All |
| Observability | Trace, event log and run metrics | All |
| Reliability metrics | Calculated report with definitions and sample size | Across runs |
| Dynamic re-plan | Requirement update, selective invalidation and plan v2/v3 diff | Ambiguous |
| Production-quality output | Code, schemas, tests, docs, migrations and review package | Greenfield/brownfield |
| Risk and trade-offs | Versioned risk register and final residual-risk section | All |
| Controlled autonomy | Permission manifests plus approval decisions | All |

## Metrics definitions

Avoid ambiguous vanity metrics. Define numerator, denominator and clock.

| Metric | Definition |
|---|---|
| Run success rate | Completed runs meeting all required gates / terminal runs, grouped by scenario type |
| First-pass verification rate | Verification stages passing before any repair / verification stages executed |
| Retry frequency | Retry events / task attempts, separated by failure class |
| Rollback frequency | Runs with an executed rollback / runs that performed mutating execution |
| Safe-stop frequency | Safely stopped runs / terminal runs |
| Re-plan frequency | Re-plan events / runs and re-plan events / requirement revisions |
| Invalidated-work ratio | Stale descendant artifacts / active artifacts at time of upstream change |
| MTTR | Mean time from a repairable failure event to the next successful verification for that failure |
| Active execution latency | Sum of workflow compute/tool time excluding human waiting |
| Wall-clock latency | Time from accepted request to terminal state, including approval wait |
| Approval latency | Time from approval request to valid resolution |
| Evidence completeness | Required evidence items present and revision-valid / required items |
| Policy denial rate | Denied action requests / evaluated action requests, grouped by policy |

Report distributions or at least min/median/max when sample size permits. For the three assignment scenarios, explicitly mark values as demo observations.

## Risk register

| Risk | Consequence | Mitigation | Residual limitation |
|---|---|---|---|
| Model produces plausible but false analysis | Unsafe or irrelevant change | Typed outputs, scoped context, independent checks, human gates | Semantic defects can survive tests |
| Prompt injection in repository content | Tool misuse or policy bypass | Treat repository text as untrusted data; system-level tool policy; no secret access | Novel injection paths remain possible |
| Excessive agent permissions | Data loss or external side effects | Least privilege, isolated workspace, deny-by-default network/secrets, approval gates | Host/container isolation quality matters |
| Duplicate execution after restart | Duplicate commits or external writes | Idempotency keys, transactional state changes, side-effect ledger | Third-party APIs may lack idempotency |
| Retry storm / loop | Cost and latency explosion | Per-task/run budgets, failure classification, circuit breaker, safe-stop | May stop before solving a hard defect |
| Parallel patch conflicts | Corrupted or incoherent integration | Isolated patches, ownership boundaries, synchronization and full re-verification | Reduces achievable parallelism |
| Stale approval/evidence | Changed work promoted under old authority | Bind to digests/revisions; invalidate descendants and approvals | Correct dependency modeling is essential |
| Test gaming or deletion | False confidence | Coverage/change policy, protected tests, independent verifier, diff review | Coverage is not correctness |
| Supply-chain compromise | Vulnerable generated system | Locked dependencies, allowlist, vulnerability/license scanning, checksums | Scanner databases and policies evolve |
| Analytics privacy leakage | Compliance/user harm | Data minimization, retention rules, aggregation, no full IP/query logging | Requirements must define applicable regime |
| Cache consistency defect | Wrong redirect or expiry bypass | Source-of-truth DB, bounded TTL, invalidation, outage and time-boundary tests | Distributed invalidation is not fully proven locally |
| Misleading reliability claims | Poor engineering credibility | State test conditions and sample sizes; separate targets from measurements | Prototype evidence cannot prove internet-scale SLOs |

## Release-readiness gate

A run may be marked `review_ready` only if:

- normalized requirements and assumptions are current;
- all tasks required by the current plan are terminal-successful or explicitly waived by an authorized human;
- no required artifact is stale;
- required checks pass on the exact current revision;
- no unresolved critical/high policy finding remains;
- change risk and residual risks are documented;
- migration and rollback implications are present when applicable;
- setup and API documentation reflect the change;
- final approval request includes the diff, evidence and plan/decision summary;
- the approval is valid for the current artifact digests.

“Tests passed” alone is insufficient.

## Final engineering summary template

Every scenario should generate a concise Markdown report with:

1. request and normalized intent;
2. approved assumptions and remaining ambiguity;
3. base/output revisions;
4. plan and material re-plan history;
5. architecture/impact summary;
6. changed artifacts and why;
7. validation evidence and exact revision;
8. policy decisions and human approvals;
9. failures, retries, repairs, rollback/safe-stop activity;
10. metrics and elapsed times;
11. risks, trade-offs and operational implications;
12. limitations and recommended next actions;
13. final status: review-ready, blocked, denied or safely stopped.

## Reviewer-friendly demonstration script

Aim for a 12–15 minute core demo with deeper artifacts available for questions.

### 1. Frame the product — 1 minute

Explain that the shortener is the workload and the control plane is the evaluated system. Show the component boundary and one-sentence thesis.

### 2. Start a greenfield run — 3 minutes

- submit the requirement;
- show structured normalization and DAG;
- point out parallel-ready work and task permissions;
- show real workspace changes and deterministic evidence;
- show final lineage from requirement to check.

Use pre-generated or seeded agent responses if live model latency would dominate the demo; be transparent about the mode.

### 3. Prove brownfield reasoning and governance — 3 minutes

- submit expiry/update enhancement against the baseline;
- show impacted modules, data flow and migration risk;
- reach and display the durable schema approval pause;
- approve it and resume;
- inject or replay a failing regression, bounded repair and successful re-check.

### 4. Prove ambiguity and dynamic re-planning — 3 minutes

- submit “make popular links faster without losing analytics”;
- show the ambiguity gate and proposed measurable assumptions;
- approve an initial contract;
- change the analytics-delay target;
- show new requirement/plan versions, selective invalidation and retained unaffected evidence.

### 5. Prove failure safety — 2 minutes

- trigger a forbidden path or command and show policy denial;
- show a retry budget exhaustion entering safe-stop;
- show that state/evidence remain inspectable and no protected side effect occurred.

### 6. Close on evidence — 2 minutes

- open the final engineering summary;
- show run metrics with definitions;
- state limitations honestly;
- show the one-command reproduction path.

## Demo resilience plan

Live agent demos are inherently variable. Prepare:

- deterministic scenario fixtures;
- a mock/replay provider mode;
- prebuilt containers or locked dependencies;
- seeded failure injection;
- recorded run event logs;
- pre-generated final artifacts tied to known revisions;
- a clean-start check performed before the presentation.

This is not hiding variability. It demonstrates that the engineering system is testable independently of a stochastic model.

## Acceptance checklist for the assignment

### Architecture and orchestration

- [ ] Explicit dependency graph is persisted and inspectable.
- [ ] Conditional, sequential, parallel and synchronization behavior are demonstrated.
- [ ] Entry/exit gates are executable.
- [ ] Cross-stage artifacts are versioned with provenance.
- [ ] Human approvals are durable, scoped and revision-bound.
- [ ] Retry, fallback, rollback and safe-stop have distinct behavior.
- [ ] Dynamic re-planning invalidates affected descendants.

### Security and governance

- [ ] Agents have least-privilege tool/path/network grants.
- [ ] Policy decisions are code-backed and audited.
- [ ] Repository content is treated as untrusted.
- [ ] Secrets and protected branches are inaccessible by default.
- [ ] High-impact changes require independent approval.

### Engineering outputs

- [ ] URL-shortener service runs locally end to end.
- [ ] API/schema and migrations are included.
- [ ] Unit, integration, contract, concurrency and failure tests exist.
- [ ] Setup, architecture, operations and decision docs are current.
- [ ] Each scenario ends with a reviewable engineering summary.

### Evidence and evaluation

- [ ] Three successive scenarios are reproducible.
- [ ] Every core requirement maps to executable evidence.
- [ ] Metrics include definitions, sample size and conditions.
- [ ] Tests/evidence are bound to exact revisions.
- [ ] Risks, limitations and trade-offs are explicit.

## Overall recommendation

Proceed with this assignment, but manage it as an **assurance-first orchestration prototype**. Build the smallest credible URL-shortener domain that generates meaningful engineering risk, then invest the majority of design effort in durable state, graph semantics, permission boundaries, objective evidence, approvals, recovery and replayable demonstrations.

That balance is most likely to satisfy both the explicit rubric and the unstated evaluation question: “Would we trust this system to participate in real software delivery without surrendering human control?”

