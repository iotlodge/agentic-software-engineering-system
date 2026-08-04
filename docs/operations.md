# Operations

## Install and verify

```bash
uv sync                    # Python 3.12+, creates .venv
uv run pytest              # full suite (control plane + workload + scenarios)
cd shortener-rs && cargo test   # optional: Rust parity workload
```

## Dockerized environment

`./ase-env.sh` wraps `compose.yaml` (services: `dashboard`, one-shot `demo`,
and — under the `workload` profile — `shortener-py` on :8000 and
`shortener-rs` on :8788):

| Command | Effect |
|---|---|
| `./ase-env.sh start [--all]` | Build (first run) and start the dashboard; `--all` adds both workload services |
| `./ase-env.sh demo` | Run the three governed scenarios inside the container (state persists in the `ase-state` volume) |
| `./ase-env.sh stop` / `restart [--all]` | Stop / bounce; named volumes survive |
| `./ase-env.sh status` / `logs [service]` | Health and logs |
| `./ase-env.sh build` | Rebuild images |
| `./ase-env.sh clean` | Stop and **delete** state volumes (fresh slate) |

The control-plane image includes git (worktrees are real) and never receives
your `.env` — it is dockerignored, and live LLM mode is a host-side concern.

## CLI reference (`ase`)

| Command | Purpose |
|---|---|
| `ase run <scenario.yaml> [--mode mock\|live] [--auto-approve]` | Start a governed run; prints approval instructions on pause |
| `ase approvals` | List pending approvals across runs |
| `ase approve <apr_id> --approver <name> [--reason ...]` | Approve; the run resumes immediately |
| `ase reject <apr_id> --approver <name> --reason ...` | Reject; the run terminates as denied with your reason |
| `ase resume <run_id>` | Resume a paused or restart-recovered run |
| `ase revise <run_id> <revision.yaml>` | Apply an upstream requirement revision (contract/data/plan_upserts) |
| `ase runs` / `ase show <run_id>` | List runs / per-run metrics JSON |
| `ase metrics` | Fleet metrics |
| `ase serve [--port 8787]` | Dashboard (Daylight Glass) |
| `ase demo [--keep] [--mode live]` | All three scenarios, auto-approved; `--keep` accumulates onto existing state |
| `ase agents-smoke` | Live LLM credential sanity check |

## State layout (`.ase/`, gitignored)

```
.ase/
├── state.db            durable store: runs, tasks, artifacts, approvals,
│                       policy decisions, append-only events (SQLite, WAL)
├── workload/           the evolving workload git repository (main)
├── evidence/<run>/     raw check output per evidence artifact
└── runs/<run>/
    ├── worktree/       the run's isolated git worktree (branch run/<id>)
    ├── summary.md      engineering summary (written at release assembly)
    └── RECOVERY.md     written on safe-stop
```

Delete `.ase/` for a clean slate (`ase demo` does this unless `--keep`).

## Environment

| Variable | Used by | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | `--mode live` | LLM credentials (from `.env`; never logged, stripped from all evidence subprocesses) |
| `ASE_LLM_PROVIDER` | live mode | Force `anthropic` or `openai` (default: Anthropic if both present) |
| `ASE_ANTHROPIC_MODEL` / `ASE_OPENAI_MODEL` | live mode | Model overrides |
| `SHORTENER_DB`, `SHORTENER_ADMIN_TOKEN`, `SHORTENER_CACHE_TTL`, `SHORTENER_FLUSH_INTERVAL` | workload | Service configuration (both implementations honor `SHORTENER_DB` / `SHORTENER_ADMIN_TOKEN`) |

## Running the workload directly

```bash
uv run uvicorn shortener.api:app --port 8000     # Python reference (final state)
cd shortener-rs && cargo run                      # Rust parity service on :8788
```

To run the *evolved* workload the scenarios produced:

```bash
cd .ase/workload && PYTHONPATH=. uv run --project ../.. python -m pytest tests -q
```

## Recovery playbook

- **Run paused at an approval** — `ase approvals`, then approve/reject. The
  approval survives restarts.
- **Process died mid-run** — `ase resume <run_id>`. Tasks caught `RUNNING` are
  re-queued (attempt refunded) with a `task.recovered` audit event.
- **Run safe-stopped** — read `.ase/runs/<id>/RECOVERY.md`. The worktree is
  preserved for diagnosis; workload `main` is untouched. Fix the cause, then
  `ase resume <run_id>`, or abandon with `git worktree remove`.
- **Approval refuses with "stale"** — the subject content changed after the
  request was raised. This is the system working; re-run the gate (the engine
  re-opens it) and approve the new digests.
