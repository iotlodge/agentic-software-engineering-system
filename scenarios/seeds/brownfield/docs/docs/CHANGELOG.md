# Changelog

## 2.0.0 — link expiry and safe destination updates

- Optional absolute-UTC `expires_at` on link creation; expired links return
  **410 Gone** (distinct from 404 unknown). The expiry instant is inclusive.
- `PATCH /v1/links/{code}` updates a destination without changing the code:
  requires `X-Admin-Token`, uses optimistic concurrency via `version`
  (409 on lost update), and writes a `link_audit` row.
- Analytics aggregate by the immutable link code, so destination changes never
  reset or split click history.
- Migration `002_expiry_update.sql` is additive (nullable `expires_at`,
  `version` default 1, new `link_audit` table). Existing rows are untouched.
  **Downgrade note:** rows written after this migration make a clean downgrade
  lossy (expiry/version/audit data would be dropped).
