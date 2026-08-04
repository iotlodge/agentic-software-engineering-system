# 01 — Assignment Deconstruction

## Executive interpretation

The assignment is not principally asking for a URL shortener. A competent engineer can build a basic shortener quickly, so the domain is intentionally ordinary. The evaluator wants to see whether the candidate can build an **agentic engineering system** that behaves like a controlled delivery organization:

- understands imperfect requests;
- creates and updates an executable plan;
- coordinates specialist capabilities across the SDLC;
- acts on a real codebase rather than only generating prose;
- gathers objective evidence;
- stops or escalates when authority or confidence is insufficient;
- recovers from tool, test, or requirement failures;
- presents a reviewable change with full lineage.

The critical phrase is “non-linear, stateful execution with governance rather than simple linear task chaining.” A fixed script that calls `planner → coder → tester → writer` will fail the spirit of the assignment even if it produces correct code.

## Three systems are actually in scope

### 1. The target software system

The URL-shortener service must be credible enough to exercise design, implementation, testing, security, reliability, analytics, and change management.

### 2. The engineering execution system

This is the agentic control plane that converts a requirement into a repository change. It owns workflow state, task dependencies, agent permissions, evidence, approvals, retries, re-planning, and completion decisions.

### 3. The assurance system

This proves the result is safe and reviewable. It includes deterministic checks, policy evaluation, provenance, metrics, audit events, change summaries, release criteria, and human approval records.

Treating these as separate systems avoids a common failure: building a polished shortener with a thin chat wrapper and calling it an agentic engineering platform.

## Stakeholders and their needs

| Stakeholder | Primary need | Evidence they should receive |
|---|---|---|
| Requester / product owner | Intent correctly understood | Normalized requirement, acceptance criteria, open questions, approved assumptions |
| Engineer | Coherent and maintainable change | Architecture notes, plan, diffs, tests, decision records |
| Reviewer | Small, explainable, verifiable change | Impact map, review summary, risk classification, check results |
| Security/compliance owner | Policies enforced before risky actions | Policy results, dependency/secret/security scans, exception approvals |
| Release owner | Clear readiness and rollback posture | Release checklist, migration/rollback notes, evidence bundle |
| Platform operator | Stable workflow and diagnosable failures | Durable run state, logs/traces, retry history, MTTR and latency metrics |
| Evaluator | Proof of agentic orchestration | Graph transitions, parallel branches, re-plan event, approval pause, safe-stop, lineage |

## Explicit requirements translated into testable capabilities

| Brief requirement | System capability | Observable proof |
|---|---|---|
| Requirement understanding | Normalize request into structured intent | Requirement artifact with actors, goals, constraints, acceptance criteria, ambiguity and confidence |
| Task decomposition | Build dependency-aware work graph | Tasks have IDs, owners/capabilities, dependencies, exit conditions and status |
| Brownfield reasoning | Map requested behavior to existing code and contracts | Impact report names modules, data flows, tests, APIs, migrations and risks |
| Non-linear orchestration | Choose next work based on state and evidence | Runtime graph shows branching, fan-out/fan-in, loops and conditional transitions |
| Context preservation | Maintain an authoritative run state | Each artifact is versioned and linked to its inputs and generating action |
| Decision lineage | Record material choices and their causes | Decision records link requirement → task → change → check → approval |
| Human approval | Pause before high-impact actions | Durable pending-approval state with scope, evidence, approver and outcome |
| Bounded retries | Retry only classified transient failures | Attempt counters, backoff, retry limit and terminal disposition |
| Fallback/rollback/safe-stop | Contain failure and restore a known state | Clean worktree, revert plan, checkpoint restoration, no unauthorized release |
| Guardrails | Evaluate actions against explicit policy | Allow/deny/approval-required decision with policy version and rationale |
| Observability | Diagnose each run and compare performance | Structured events, traces, metrics and final run report |
| Dynamic re-planning | Invalidate and recompute affected downstream work | Requirement revision causes selective invalidation and a new plan version |
| Engineering outputs | Produce a complete review package | Code, schemas, tests, docs, API spec, change summary and release evidence |
| Validation | Combine generated work with deterministic evidence | Test/lint/type/security/contract results; failed checks block completion |

## What “agentic” should mean here

An agentic step is not merely an LLM call. It is a bounded decision-maker with:

- a defined objective;
- explicitly scoped context;
- permitted tools and side effects;
- a typed input/output contract;
- a completion condition;
- a confidence/risk result;
- an escalation path;
- auditable activity.

The orchestrator, not an individual agent, owns the lifecycle. Agents propose or execute within their grants; policy and evidence determine whether the workflow advances.

## What the evaluator is likely to probe

### Can the system distinguish uncertainty from failure?

Ambiguous intent should not be treated like a test failure. The former may require clarification or an approved assumption; the latter requires diagnosis, repair, fallback, or safe-stop.

### Does governance exist in code or only in slides?

Approval gates, policy decisions, permission scopes, retry budgets, and blocked transitions should be executable behavior. A diagram alone will not satisfy “implement an agentic orchestration layer.”

### Is execution genuinely stateful?

A process restart should not erase the plan, approvals, evidence, or last successful checkpoint. A run should resume deterministically from persisted state.

### Is re-planning selective and safe?

If an upstream requirement changes, the system should identify stale descendants, preserve unaffected evidence, create a new plan version, and require renewed approval where risk changed. Restarting everything is wasteful; silently continuing is unsafe.

### Are claims backed by independent checks?

An agent saying “tests pass” is not evidence. The system must capture actual command results, timestamps, source revision, environment identity, and artifact digests.

### Is autonomy proportional to risk?

Reading code, drafting a plan, and changing an isolated worktree are relatively low risk. Schema migrations, security-control changes, writes to protected branches, secret access, external deployment, and destructive operations should require stronger authority.

## The assignment’s internal tension

“Build from scratch” conflicts with the requirement to demonstrate brownfield reasoning. The best resolution is temporal:

1. Begin with an empty or scaffold-only repository.
2. Run the greenfield scenario to create the baseline.
3. Treat that committed baseline as the brownfield system.
4. Apply a meaningful enhancement or defect fix through a second governed run.
5. Submit an intentionally ambiguous third request against the evolved codebase.

This is not a workaround; it creates a coherent system history and lets the evaluator inspect how accumulated context and prior decisions affect later work.

## Recommended definition of “production-grade prototype”

The phrase should mean “production-shaped and demonstrably safe,” not “operating at global internet scale.” The prototype should include:

- modular boundaries and typed interfaces;
- durable workflow state;
- isolated code execution;
- deterministic quality gates;
- explicit security controls;
- database migrations and rollback notes;
- health/readiness behavior;
- structured telemetry;
- reproducible local setup;
- credible failure handling;
- documented scale boundaries and limitations.

It does not need multi-region infrastructure, exhaustive compliance certification, or a fully general autonomous coding platform unless the evaluator explicitly asks for them.

## Likely anti-patterns and why they will score poorly

| Anti-pattern | Why it misses the assignment |
|---|---|
| One “super-agent” with repository and shell access | No separation of duties, weak control surface, hard to audit |
| Linear agent chain | Cannot model conditional work, parallelism, synchronization or re-planning |
| Agents pass free-form chat history | Context becomes unbounded, inconsistent and difficult to validate |
| Human approval is a terminal prompt | Not durable, risk-based, scoped or auditable |
| Retry every failure | Repeats deterministic bugs and can multiply unsafe effects |
| Git revert presented as the only rollback | Does not address databases, external side effects or workflow state |
| Demo only the happy path | Leaves the critical differentiators unproven |
| Build three unrelated scenarios | Loses the opportunity to show history, brownfield evolution and lineage |
| Let LLMs declare completion | Replaces verification with self-assertion |
| Spend most effort on short-code generation | Optimizes the commodity portion instead of orchestration and assurance |

## Strong response thesis

A strong submission should be framed as follows:

> We built a small but real engineering control plane. It represents work as a durable dependency graph, assigns bounded specialist capabilities, runs changes in an isolated repository workspace, uses deterministic evidence and policy to gate transitions, requests human decisions at risk boundaries, and can selectively re-plan or safely stop. The URL shortener and its three successive scenarios prove the lifecycle behavior end to end.

