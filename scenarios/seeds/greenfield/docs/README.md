# URL Shortener

Short links with click analytics. Built and evolved through governed
engineering runs; see `docs/` for operations notes.

## Run locally

```bash
uvicorn shortener.api:app --reload
```

## API

| Endpoint | Behavior |
|---|---|
| `POST /v1/links` | Create a short link (201); idempotent replay via `idempotency_key` (200) |
| `GET /{code}` | 307 temporary redirect; 404 unknown; 410 disabled |
| `GET /v1/links/{code}` | Link metadata |
| `GET /v1/links/{code}/stats` | Click totals (eventually consistent) |
| `DELETE /v1/links/{code}` | Soft disable (admin token required) |
| `GET /health/live` / `GET /health/ready` | Liveness / readiness |

Errors use one envelope: `{"error": {"code": "...", "message": "..."}}`.
The OpenAPI document is served at `/openapi.json`.

## Test

```bash
python -m pytest tests -q
```
