# Evaluation: traceability, metrics, demo

## Requirement → evidence traceability matrix

Every core requirement from the blueprint maps to *executable* evidence. Test
paths are relative to `tests/`.

| Requirement | Evidence | Where |
|---|---|---|
| Understand intent and ambiguity | Versioned requirement artifact; ambiguity gate pauses; approved assumptions become a versioned contract | `scenarios/test_three_runs.py::test_ambiguous…`; `control_plane/test_engine.py::TestSelectiveReplan` |
| Decompose with dependencies | Persisted DAG; cycle/unknown-dep rejection; plan materialized as data | `control_plane/test_models_store_graph.py::TestGraph`; engine happy-path tests |
| Brownfield reasoning | Impact artifact against base revision; regression suite of prior versions must pass | `scenarios/test_three_runs.py::test_brownfield…` |
| Sequential + parallel paths | `wave.started` events show impl/tests/docs in one wave; fan-in at integrate | `test_fan_out_same_wave`; greenfield scenario test |
| Stateful execution / restart | New object graph on same DB resumes; mid-task crash re-queues | `TestDurability` (both tests) |
| Cross-stage lineage | Artifact provenance with digests and revisions; dashboard lineage table | `TestArtifacts`; `api` tests |
| Human checkpoints | Durable approvals; rejection carries reason; stale approvals refused at resolution AND consumption | `TestApprovals`; `TestApprovalFlow` |
| Retry vs fallback vs rollback vs safe-stop | Four distinct behaviors, each with its own test | `TestFailureHandling` (7 tests); `TestWorkspace::test_commit_diff_rollback_cycle` |
| Bounded repair loop | Genuine failing regression (expiry boundary) repaired and re-verified; failing + passing evidence both retained | `test_deterministic_failure_triggers_bounded_repair`; brownfield scenario test |
| Policy guardrails | Default deny; secrets/network/protected paths denied; risk + numeric thresholds; every evaluation recorded | `TestPolicy` (6 tests) |
| Dynamic re-planning | Contract revision invalidates only descendants; preserved work never re-runs; renewed approval demanded | `TestSelectiveReplan`; ambiguous scenario test |
| Evidence binding | Checks bound to exact revision; different-revision evidence flagged | `TestEvidence` (4 tests) |
| Controlled autonomy (LLM) | Schema validation + one repair; bounded planner strips invented actions/foreign params | `test_agents.py` (12 tests) |
| Production-shaped output | Migrations (incl. populated-baseline upgrade), contract tests, concurrency tests, health, error envelope | `shortener/` suite (45 tests) + Rust parity (8 tests) |
| Engineering summary | Release report + markdown summary with revisions, evidence, approvals, recovery activity | generated per run at `.ase/runs/<id>/summary.md` |

## Metric definitions

Computed from the append-only event log (`control_plane/telemetry.py`); each
metric states numerator, denominator, and clock. Highlights:

| Metric | Definition |
|---|---|
| Run success rate | runs reaching `review_ready`/`promoted` ÷ terminal runs |
| First-pass verification | verification tasks succeeding on attempt 1 ÷ verification tasks |
| Retry / repair / rollback counts | occurrences of the corresponding audit events |
| Invalidated-work ratio | stale artifacts ÷ total artifacts at revision time |
| Approval latency | request → resolution, per approval |
| Active execution vs wall clock | Σ task execution time vs run creation → last event |
| Parallelism proof | task pairs with overlapping execution intervals |

With three demo runs these are instrument validation and illustrative
observations, **not** statistically significant reliability claims.

## Reference demo numbers (mock mode, M-series laptop)

| Scenario | Outcome | Waves | Retries | Repairs | Re-plans | Approvals |
|---|---|---|---|---|---|---|
| greenfield | promoted | 8 | 1 (injected transient) | 0 | 0 | 1 (release) |
| brownfield | promoted | 11 | 0 | 1 (boundary bug) | 0 | 3 (plan, schema, release) |
| ambiguous | promoted | 11 | 0 | 0 | 1 (60s→5s) | 5 (assumption, plan, schema, re-plan, release) |

## Reviewer demo script (~12 minutes)

1. **Frame (1 min).** The shortener is the workload; the control plane is the
   system. Show `README.md` and this matrix.
2. **Greenfield (3 min).** `ase run scenarios/greenfield.yaml` — show the
   normalize/analyze/plan waves, parallel fan-out in the dashboard DAG, the
   injected transient retry in the event feed, then approve the release gate
   from the dashboard and show the merge on workload `main`.
3. **Brownfield (3 min).** Start the run; approve the high-risk plan; show the
   durable schema-migration pause and its digest binding; approve; watch the
   boundary regression fail verification, the bounded repair, and the passing
   re-check (both evidence artifacts retained).
4. **Ambiguous (3 min).** Show the ambiguity gate with proposed assumptions;
   approve the 60s contract; after verification the scripted evaluator revision
   lands — walk the stale artifacts (struck through in the lineage table), the
   preserved migration, the re-approval, and the 5s-contract re-verification.
5. **Failure safety (1 min).** `uv run pytest tests/control_plane/test_engine.py -k "safe_stops or fallback" -v` —
   policy denial without fallback, retry-budget exhaustion, preserved worktree.
6. **Close (1 min).** `ase metrics`; open one `summary.md`; state limitations.

Unattended fallback: `ase demo` reproduces the entire arc in ~30 seconds.

## Honest limitations

- Worker isolation is process/path/policy-level, not container/seccomp
  sandboxing; a hostile tool binary is out of scope.
- Live LLM implementer/test-writer roles are scaffolded but the demo scenarios
  drive implementation from seeds; the analyst/planner are the live-mode roles.
  This is deliberate (deterministic proof first) and stated in ADR 002.
- SQLite's single-writer model bounds parallelism; the Store seam is the
  Postgres path.
- p95 latency figures for the workload are design targets under local load,
  not production SLO claims.
- The three-run sample proves behavior, not reliability statistics.
