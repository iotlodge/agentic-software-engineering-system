-- Brownfield change: optional expiry and safe destination updates.
-- Additive, nullable columns only; existing rows default to "no expiry", v1.
ALTER TABLE links ADD COLUMN expires_at TEXT;
ALTER TABLE links ADD COLUMN version INTEGER NOT NULL DEFAULT 1;

CREATE TABLE IF NOT EXISTS link_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    action TEXT NOT NULL,
    old_url TEXT,
    new_url TEXT,
    ts TEXT NOT NULL
);
