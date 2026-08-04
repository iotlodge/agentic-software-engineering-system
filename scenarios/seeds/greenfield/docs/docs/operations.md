# Operations

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `SHORTENER_DB` | `:memory:` | SQLite database path |
| `SHORTENER_ADMIN_TOKEN` | `dev-admin` | Token for administrative endpoints |

## Data

- SQLite is the system of record. Migrations live in `shortener/migrations/`
  and apply automatically at startup, tracked in `schema_migrations`.
- Analytics are click events; recording is fail-open (a broken analytics path
  never breaks redirects; a loss-risk counter is exposed on `/health/ready`).

## Privacy

Click events record only the link code and a timestamp — no IP addresses,
user agents, or query values.
