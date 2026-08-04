//! URL shortener — Rust parity implementation.
//!
//! Same API contract as the Python service (`shortener/`): same routes, same
//! status codes, same error envelope, same migrations. The control plane is
//! contract-first, so the workload's implementation language is a detail —
//! this crate is the proof.

pub mod db;
pub mod domain;

use std::sync::Arc;

use axum::{
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Json, Redirect, Response},
    routing::get,
    Router,
};
use chrono::Utc;
use serde::Deserialize;
use serde_json::json;

use db::{Db, DbError, Link};
use domain::{is_expired, validate_expiry, validate_url};

pub struct AppState {
    pub db: Db,
    pub admin_token: String,
}

pub fn app(db: Db, admin_token: &str) -> Router {
    let state = Arc::new(AppState { db, admin_token: admin_token.to_string() });
    Router::new()
        .route("/v1/links", axum::routing::post(create_link))
        .route(
            "/v1/links/{code}",
            get(link_details).patch(update_link).delete(disable_link),
        )
        .route("/v1/links/{code}/stats", get(link_stats))
        .route("/health/live", get(live))
        .route("/health/ready", get(ready))
        .route("/{code}", get(resolve))
        .with_state(state)
}

fn error(status: StatusCode, code: &str, message: &str) -> Response {
    (status, Json(json!({"error": {"code": code, "message": message}}))).into_response()
}

impl IntoResponse for DbError {
    fn into_response(self) -> Response {
        match self {
            DbError::NotFound(code) => error(
                StatusCode::NOT_FOUND, "not_found",
                &format!("unknown link code: {code}")),
            DbError::Conflict(msg) => error(
                StatusCode::CONFLICT, "version_conflict", &msg),
            DbError::Internal(msg) => error(
                StatusCode::INTERNAL_SERVER_ERROR, "internal", &msg),
        }
    }
}

fn link_body(link: &Link) -> serde_json::Value {
    json!({
        "code": link.code,
        "short_url": format!("/{}", link.code),
        "url": link.url,
        "created_at": link.created_at,
        "expires_at": link.expires_at,
        "disabled": link.disabled,
        "version": link.version,
    })
}

fn check_admin(state: &AppState, headers: &HeaderMap) -> Result<(), Response> {
    match headers.get("x-admin-token").and_then(|v| v.to_str().ok()) {
        None => Err(error(StatusCode::UNAUTHORIZED, "unauthenticated",
                          "X-Admin-Token header required")),
        Some(token) if token != state.admin_token => {
            Err(error(StatusCode::FORBIDDEN, "forbidden", "invalid admin token"))
        }
        Some(_) => Ok(()),
    }
}

#[derive(Deserialize)]
struct CreateLinkRequest {
    url: String,
    expires_at: Option<String>,
    idempotency_key: Option<String>,
}

#[derive(Deserialize)]
struct UpdateLinkRequest {
    url: String,
    version: i64,
}

async fn create_link(
    State(state): State<Arc<AppState>>,
    Json(payload): Json<CreateLinkRequest>,
) -> Response {
    if let Err(msg) = validate_url(&payload.url) {
        return error(StatusCode::BAD_REQUEST, "invalid_request", &msg);
    }
    let expires_at = match validate_expiry(&payload.expires_at) {
        Ok(v) => v,
        Err(msg) => return error(StatusCode::BAD_REQUEST, "invalid_request", &msg),
    };
    match state.db.create_link(&payload.url, expires_at, payload.idempotency_key) {
        Ok((link, created)) => {
            let status = if created { StatusCode::CREATED } else { StatusCode::OK };
            (status, Json(link_body(&link))).into_response()
        }
        Err(e) => e.into_response(),
    }
}

async fn link_details(
    State(state): State<Arc<AppState>>,
    Path(code): Path<String>,
) -> Response {
    match state.db.get_link(&code) {
        Ok(link) => Json(link_body(&link)).into_response(),
        Err(e) => e.into_response(),
    }
}

async fn link_stats(
    State(state): State<Arc<AppState>>,
    Path(code): Path<String>,
) -> Response {
    if let Err(e) = state.db.get_link(&code) {
        return e.into_response(); // 404 for unknown codes
    }
    match state.db.click_stats(&code) {
        Ok((clicks, last_click)) => Json(json!({
            "code": code,
            "clicks": clicks,
            "last_click": last_click,
            "consistency": "synchronous (rust variant)",
        }))
        .into_response(),
        Err(e) => e.into_response(),
    }
}

async fn update_link(
    State(state): State<Arc<AppState>>,
    Path(code): Path<String>,
    headers: HeaderMap,
    Json(payload): Json<UpdateLinkRequest>,
) -> Response {
    if let Err(resp) = check_admin(&state, &headers) {
        return resp;
    }
    if let Err(msg) = validate_url(&payload.url) {
        return error(StatusCode::BAD_REQUEST, "invalid_request", &msg);
    }
    match state.db.update_destination(&code, &payload.url, payload.version) {
        Ok(link) => Json(link_body(&link)).into_response(),
        Err(e) => e.into_response(),
    }
}

async fn disable_link(
    State(state): State<Arc<AppState>>,
    Path(code): Path<String>,
    headers: HeaderMap,
) -> Response {
    if let Err(resp) = check_admin(&state, &headers) {
        return resp;
    }
    match state.db.disable_link(&code) {
        Ok(()) => StatusCode::NO_CONTENT.into_response(),
        Err(e) => e.into_response(),
    }
}

async fn live() -> Response {
    Json(json!({"status": "ok"})).into_response()
}

async fn ready(State(state): State<Arc<AppState>>) -> Response {
    if !state.db.ping() {
        return error(StatusCode::SERVICE_UNAVAILABLE, "not_ready",
                     "database unavailable");
    }
    Json(json!({"status": "ready"})).into_response()
}

async fn resolve(
    State(state): State<Arc<AppState>>,
    Path(code): Path<String>,
) -> Response {
    let link = match state.db.get_link(&code) {
        Ok(link) => link,
        Err(e) => return e.into_response(),
    };
    let now = Utc::now();
    if link.disabled || is_expired(&link.expires_at, now) {
        let reason = if is_expired(&link.expires_at, now) { "expired" } else { "disabled" };
        return error(StatusCode::GONE, "gone",
                     &format!("link {code} is {reason}"));
    }
    // Fail-open analytics: a recording error must never break the redirect.
    let _ = state.db.record_click(&code, &now.to_rfc3339());
    Redirect::temporary(&link.url).into_response()
}
