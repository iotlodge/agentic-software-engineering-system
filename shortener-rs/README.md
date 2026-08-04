# shortener-rs

Rust parity implementation of the URL-shortener workload (axum + rusqlite).
Same API contract as the Python reference in `../shortener/`: identical routes,
status codes, error envelope, inclusive expiry boundary, optimistic-concurrency
updates, audit rows — and the **same SQL migration files**, embedded at compile
time. See [ADR 007](../docs/decisions/007-rust-parity-workload.md).

```bash
cargo test    # 8 contract-parity integration tests
cargo run     # http://127.0.0.1:8788  (SHORTENER_DB, SHORTENER_ADMIN_TOKEN, SHORTENER_PORT)
```

Declared divergences (stated, not hidden): clicks are recorded synchronously
(the batched bounded-delay sink is demonstrated on the Python side) and there is
no in-process cache layer; `GET /v1/links/{code}/stats` reports
`"consistency": "synchronous (rust variant)"`.
