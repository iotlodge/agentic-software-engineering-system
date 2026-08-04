//! Link domain logic: validation, code allocation, expiry.
//!
//! Mirrors the Python service exactly: codes are opaque, case-sensitive,
//! 7 chars of base62; destinations are http(s) without embedded credentials;
//! a link is expired at exactly its expiry instant (`expires_at <= now`).

use chrono::{DateTime, Utc};
use rand::Rng;

pub const CODE_ALPHABET: &[u8] =
    b"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";
pub const CODE_LENGTH: usize = 7;
pub const MAX_URL_LENGTH: usize = 2048;

pub fn generate_code() -> String {
    let mut rng = rand::thread_rng();
    (0..CODE_LENGTH)
        .map(|_| CODE_ALPHABET[rng.gen_range(0..CODE_ALPHABET.len())] as char)
        .collect()
}

pub fn validate_url(raw: &str) -> Result<(), String> {
    if raw.is_empty() || raw.len() > MAX_URL_LENGTH {
        return Err(format!("url must be 1..{MAX_URL_LENGTH} characters"));
    }
    let parsed = url::Url::parse(raw).map_err(|e| format!("invalid url: {e}"))?;
    match parsed.scheme() {
        "http" | "https" => {}
        _ => return Err("only http and https destinations are allowed".into()),
    }
    if parsed.host_str().is_none() {
        return Err("url must include a host".into());
    }
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return Err("credentials embedded in urls are not allowed".into());
    }
    Ok(())
}

pub fn validate_expiry(expires_at: &Option<String>) -> Result<Option<String>, String> {
    match expires_at {
        None => Ok(None),
        Some(raw) => {
            let parsed = DateTime::parse_from_rfc3339(raw)
                .map_err(|_| "expires_at must be ISO-8601 with timezone (UTC)".to_string())?;
            Ok(Some(parsed.with_timezone(&Utc).to_rfc3339()))
        }
    }
}

pub fn is_expired(expires_at: &Option<String>, now: DateTime<Utc>) -> bool {
    match expires_at {
        None => false,
        Some(raw) => DateTime::parse_from_rfc3339(raw)
            .map(|t| t.with_timezone(&Utc) <= now)
            .unwrap_or(false),
    }
}
