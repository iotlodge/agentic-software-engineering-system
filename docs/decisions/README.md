# Architecture Decision Records

| ADR | Decision |
|---|---|
| [001](001-modular-monolith-wave-engine.md) | Modular-monolith control plane with a wave-based engine |
| [002](002-deterministic-first-llm-behind-contracts.md) | Deterministic skeleton first; LLMs optional behind worker contracts |
| [003](003-one-evolving-workload-repository.md) | One workload repository evolved across all three scenarios |
| [004](004-digest-bound-approvals.md) | Approvals bound to content digests, validated twice |
| [005](005-contract-artifacts-for-selective-replan.md) | Requirement facets as separate artifacts to make re-planning selective |
| [006](006-sqlite-and-git-worktrees.md) | SQLite for durable state; git worktrees for isolation |
| [007](007-rust-parity-workload.md) | Rust parity workload to prove the contract is language-agnostic |
