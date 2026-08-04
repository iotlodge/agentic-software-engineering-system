//! SQLite persistence — the system of record.
//!
//! Applies the SAME migration files as the Python service (embedded at
//! compile time), tracked in `schema_migrations`. Unique-code allocation
//! retries on collision; destination updates use optimistic concurrency.

use std::sync::Mutex;

use chrono::Utc;
use rusqlite::{params, Connection, OptionalExtension};
use serde::Serialize;

use crate::domain::generate_code;

const MIGRATIONS: &[(&str, &str)] = &[
    ("001_init.sql", include_str!("../migrations/001_init.sql")),
    ("002_expiry_update.sql", include_str!("../migrations/002_expiry_update.sql")),
    ("003_analytics_aggregates.sql", include_str!("../migrations/003_analytics_aggregates.sql")),
];
const MAX_CODE_ATTEMPTS: usize = 5;

#[derive(Debug, Clone, Serialize)]
pub struct Link {
    pub code: String,
    pub url: String,
    pub created_at: String,
    pub expires_at: Option<String>,
    pub disabled: bool,
    pub version: i64,
}

#[derive(Debug)]
pub enum DbError {
    NotFound(String),
    Conflict(String),
    Internal(String),
}

impl From<rusqlite::Error> for DbError {
    fn from(e: rusqlite::Error) -> Self {
        DbError::Internal(e.to_string())
    }
}

pub struct Db {
    conn: Mutex<Connection>,
}

impl Db {
    pub fn open(path: &str) -> Result<Self, DbError> {
        let conn = if path == ":memory:" {
            Connection::open_in_memory()?
        } else {
            Connection::open(path)?
        };
        let db = Db { conn: Mutex::new(conn) };
        db.migrate()?;
        Ok(db)
    }

    fn migrate(&self) -> Result<(), DbError> {
        let conn = self.conn.lock().unwrap();
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS schema_migrations \
             (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)",
        )?;
        for (name, sql) in MIGRATIONS {
            let applied: Option<String> = conn
                .query_row(
                    "SELECT version FROM schema_migrations WHERE version = ?1",
                    params![name],
                    |r| r.get(0),
                )
                .optional()?;
            if applied.is_none() {
                conn.execute_batch(sql)?;
                conn.execute(
                    "INSERT INTO schema_migrations VALUES (?1, ?2)",
                    params![name, Utc::now().to_rfc3339()],
                )?;
            }
        }
        Ok(())
    }

    pub fn ping(&self) -> bool {
        self.conn
            .lock()
            .unwrap()
            .query_row("SELECT 1", [], |r| r.get::<_, i64>(0))
            .is_ok()
    }

    fn get_locked(conn: &Connection, code: &str) -> Result<Link, DbError> {
        conn.query_row(
            "SELECT code, url, created_at, expires_at, disabled, version \
             FROM links WHERE code = ?1",
            params![code],
            |r| {
                Ok(Link {
                    code: r.get(0)?,
                    url: r.get(1)?,
                    created_at: r.get(2)?,
                    expires_at: r.get(3)?,
                    disabled: r.get::<_, i64>(4)? != 0,
                    version: r.get(5)?,
                })
            },
        )
        .optional()?
        .ok_or_else(|| DbError::NotFound(code.to_string()))
    }

    /// Returns (link, created); created is false on an idempotent replay.
    pub fn create_link(
        &self,
        url: &str,
        expires_at: Option<String>,
        idempotency_key: Option<String>,
    ) -> Result<(Link, bool), DbError> {
        let conn = self.conn.lock().unwrap();
        if let Some(key) = &idempotency_key {
            let existing: Option<String> = conn
                .query_row(
                    "SELECT code FROM idempotency_keys WHERE key = ?1",
                    params![key],
                    |r| r.get(0),
                )
                .optional()?;
            if let Some(code) = existing {
                return Ok((Self::get_locked(&conn, &code)?, false));
            }
        }
        let created_at = Utc::now().to_rfc3339();
        let mut chosen: Option<String> = None;
        for _ in 0..MAX_CODE_ATTEMPTS {
            let code = generate_code();
            match conn.execute(
                "INSERT INTO links (code, url, created_at, expires_at) \
                 VALUES (?1, ?2, ?3, ?4)",
                params![code, url, created_at, expires_at],
            ) {
                Ok(_) => {
                    chosen = Some(code);
                    break;
                }
                Err(rusqlite::Error::SqliteFailure(e, _))
                    if e.code == rusqlite::ErrorCode::ConstraintViolation =>
                {
                    continue; // collision: regenerate and retry
                }
                Err(e) => return Err(e.into()),
            }
        }
        let code = chosen.ok_or_else(|| {
            DbError::Internal(format!(
                "could not allocate a unique code in {MAX_CODE_ATTEMPTS} attempts"
            ))
        })?;
        if let Some(key) = &idempotency_key {
            conn.execute(
                "INSERT INTO idempotency_keys VALUES (?1, ?2)",
                params![key, code],
            )?;
        }
        Ok((Self::get_locked(&conn, &code)?, true))
    }

    pub fn get_link(&self, code: &str) -> Result<Link, DbError> {
        Self::get_locked(&self.conn.lock().unwrap(), code)
    }

    /// Optimistic concurrency: fails with Conflict on a lost update.
    pub fn update_destination(
        &self,
        code: &str,
        new_url: &str,
        expected_version: i64,
    ) -> Result<Link, DbError> {
        let conn = self.conn.lock().unwrap();
        let old = Self::get_locked(&conn, code)?;
        let changed = conn.execute(
            "UPDATE links SET url = ?1, version = version + 1 \
             WHERE code = ?2 AND version = ?3",
            params![new_url, code, expected_version],
        )?;
        if changed == 0 {
            return Err(DbError::Conflict(format!(
                "link {code} is at version {}, not {expected_version}",
                old.version
            )));
        }
        conn.execute(
            "INSERT INTO link_audit (code, action, old_url, new_url, ts) \
             VALUES (?1, 'update_destination', ?2, ?3, ?4)",
            params![code, old.url, new_url, Utc::now().to_rfc3339()],
        )?;
        Self::get_locked(&conn, code)
    }

    pub fn disable_link(&self, code: &str) -> Result<(), DbError> {
        let conn = self.conn.lock().unwrap();
        let changed = conn.execute(
            "UPDATE links SET disabled = 1 WHERE code = ?1",
            params![code],
        )?;
        if changed == 0 {
            return Err(DbError::NotFound(code.to_string()));
        }
        conn.execute(
            "INSERT INTO link_audit (code, action, ts) VALUES (?1, 'disable', ?2)",
            params![code, Utc::now().to_rfc3339()],
        )?;
        Ok(())
    }

    pub fn record_click(&self, code: &str, ts: &str) -> Result<(), DbError> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO click_events (code, ts) VALUES (?1, ?2)",
            params![code, ts],
        )?;
        conn.execute(
            "INSERT INTO click_totals (code, clicks, last_click) VALUES (?1, 1, ?2) \
             ON CONFLICT(code) DO UPDATE SET clicks = clicks + 1, \
             last_click = excluded.last_click",
            params![code, ts],
        )?;
        Ok(())
    }

    pub fn click_stats(&self, code: &str) -> Result<(i64, Option<String>), DbError> {
        let conn = self.conn.lock().unwrap();
        let row = conn
            .query_row(
                "SELECT clicks, last_click FROM click_totals WHERE code = ?1",
                params![code],
                |r| Ok((r.get::<_, i64>(0)?, r.get::<_, Option<String>>(1)?)),
            )
            .optional()?;
        Ok(row.unwrap_or((0, None)))
    }
}
