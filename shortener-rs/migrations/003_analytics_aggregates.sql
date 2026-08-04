-- Performance change: aggregate table for fast, bounded-delay stats reads.
CREATE TABLE IF NOT EXISTS click_totals (
    code TEXT PRIMARY KEY,
    clicks INTEGER NOT NULL DEFAULT 0,
    last_click TEXT
);

CREATE INDEX IF NOT EXISTS idx_click_events_code ON click_events(code);

-- Backfill totals from any events recorded before this migration.
INSERT OR REPLACE INTO click_totals (code, clicks, last_click)
SELECT code, COUNT(*), MAX(ts) FROM click_events GROUP BY code;
