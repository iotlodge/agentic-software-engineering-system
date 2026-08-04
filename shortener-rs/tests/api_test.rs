//! Contract parity tests: the same behaviors the Python suite asserts.

use axum::body::Body;
use axum::http::{Request, StatusCode};
use http_body_util::BodyExt;
use serde_json::{json, Value};
use tower::ServiceExt;

use shortener_rs::{app, db::Db};

fn test_app() -> axum::Router {
    app(Db::open(":memory:").unwrap(), "test-admin")
}

async fn send(router: &axum::Router, req: Request<Body>) -> (StatusCode, Value) {
    let resp = router.clone().oneshot(req).await.unwrap();
    let status = resp.status();
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    let body = if bytes.is_empty() {
        json!(null)
    } else {
        serde_json::from_slice(&bytes).unwrap_or(json!(null))
    };
    (status, body)
}

fn post_json(uri: &str, payload: Value) -> Request<Body> {
    Request::post(uri)
        .header("content-type", "application/json")
        .body(Body::from(payload.to_string()))
        .unwrap()
}

async fn make_link(router: &axum::Router, url: &str) -> Value {
    let (status, body) = send(router, post_json("/v1/links", json!({"url": url}))).await;
    assert_eq!(status, StatusCode::CREATED);
    body
}

#[tokio::test]
async fn create_resolve_stats_lifecycle() {
    let router = test_app();
    let link = make_link(&router, "https://example.com/target").await;
    let code = link["code"].as_str().unwrap().to_string();
    assert_eq!(code.len(), 7);
    assert_eq!(link["version"], 1);

    let resp = router
        .clone()
        .oneshot(Request::get(format!("/{code}")).body(Body::empty()).unwrap())
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::TEMPORARY_REDIRECT); // 307
    assert_eq!(
        resp.headers().get("location").unwrap(),
        "https://example.com/target"
    );

    let (status, stats) = send(
        &router,
        Request::get(format!("/v1/links/{code}/stats")).body(Body::empty()).unwrap(),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(stats["clicks"], 1);
}

#[tokio::test]
async fn invalid_destinations_rejected_with_envelope() {
    let router = test_app();
    for bad in ["ftp://example.com/x", "https://user:pw@example.com", ""] {
        let (status, body) = send(&router, post_json("/v1/links", json!({"url": bad}))).await;
        assert_eq!(status, StatusCode::BAD_REQUEST, "url: {bad}");
        assert_eq!(body["error"]["code"], "invalid_request");
        assert!(body["error"]["message"].is_string());
    }
}

#[tokio::test]
async fn idempotent_replay_returns_same_code() {
    let router = test_app();
    let payload = json!({"url": "https://example.com", "idempotency_key": "req-1"});
    let (s1, b1) = send(&router, post_json("/v1/links", payload.clone())).await;
    let (s2, b2) = send(&router, post_json("/v1/links", payload)).await;
    assert_eq!(s1, StatusCode::CREATED);
    assert_eq!(s2, StatusCode::OK);
    assert_eq!(b1["code"], b2["code"]);
}

#[tokio::test]
async fn unknown_404_disabled_410() {
    let router = test_app();
    let (status, body) = send(
        &router,
        Request::get("/zzzzzzz").body(Body::empty()).unwrap(),
    )
    .await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["error"]["code"], "not_found");

    let link = make_link(&router, "https://example.com").await;
    let code = link["code"].as_str().unwrap();
    let (status, _) = send(
        &router,
        Request::delete(format!("/v1/links/{code}"))
            .header("x-admin-token", "test-admin")
            .body(Body::empty())
            .unwrap(),
    )
    .await;
    assert_eq!(status, StatusCode::NO_CONTENT);
    let (status, body) = send(
        &router,
        Request::get(format!("/{code}")).body(Body::empty()).unwrap(),
    )
    .await;
    assert_eq!(status, StatusCode::GONE);
    assert!(body["error"]["message"].as_str().unwrap().contains("disabled"));
}

#[tokio::test]
async fn expired_link_410_boundary_inclusive() {
    let router = test_app();
    let (status, link) = send(
        &router,
        post_json("/v1/links", json!({
            "url": "https://example.com",
            "expires_at": "2020-01-01T00:00:00+00:00"
        })),
    )
    .await;
    assert_eq!(status, StatusCode::CREATED);
    let code = link["code"].as_str().unwrap();
    let (status, body) = send(
        &router,
        Request::get(format!("/{code}")).body(Body::empty()).unwrap(),
    )
    .await;
    assert_eq!(status, StatusCode::GONE);
    assert!(body["error"]["message"].as_str().unwrap().contains("expired"));
}

#[tokio::test]
async fn update_auth_version_and_conflict() {
    let router = test_app();
    let link = make_link(&router, "https://example.com").await;
    let code = link["code"].as_str().unwrap().to_string();

    // No token -> 401; wrong token -> 403.
    let payload = json!({"url": "https://example.com/new", "version": 1});
    let (status, _) = send(
        &router,
        Request::patch(format!("/v1/links/{code}"))
            .header("content-type", "application/json")
            .body(Body::from(payload.to_string()))
            .unwrap(),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    let (status, _) = send(
        &router,
        Request::patch(format!("/v1/links/{code}"))
            .header("content-type", "application/json")
            .header("x-admin-token", "wrong")
            .body(Body::from(payload.to_string()))
            .unwrap(),
    )
    .await;
    assert_eq!(status, StatusCode::FORBIDDEN);

    // Valid update bumps version; stale expected_version conflicts.
    let (status, body) = send(
        &router,
        Request::patch(format!("/v1/links/{code}"))
            .header("content-type", "application/json")
            .header("x-admin-token", "test-admin")
            .body(Body::from(payload.to_string()))
            .unwrap(),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["version"], 2);
    let (status, body) = send(
        &router,
        Request::patch(format!("/v1/links/{code}"))
            .header("content-type", "application/json")
            .header("x-admin-token", "test-admin")
            .body(Body::from(
                json!({"url": "https://example.com/b", "version": 1}).to_string(),
            ))
            .unwrap(),
    )
    .await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(body["error"]["code"], "version_conflict");
}

#[tokio::test]
async fn analytics_identity_survives_destination_update() {
    let router = test_app();
    let link = make_link(&router, "https://example.com").await;
    let code = link["code"].as_str().unwrap().to_string();
    send(&router, Request::get(format!("/{code}")).body(Body::empty()).unwrap()).await;
    send(
        &router,
        Request::patch(format!("/v1/links/{code}"))
            .header("content-type", "application/json")
            .header("x-admin-token", "test-admin")
            .body(Body::from(
                json!({"url": "https://example.com/new", "version": 1}).to_string(),
            ))
            .unwrap(),
    )
    .await;
    send(&router, Request::get(format!("/{code}")).body(Body::empty()).unwrap()).await;
    let (_, stats) = send(
        &router,
        Request::get(format!("/v1/links/{code}/stats")).body(Body::empty()).unwrap(),
    )
    .await;
    assert_eq!(stats["clicks"], 2);
}

#[tokio::test]
async fn health_endpoints() {
    let router = test_app();
    let (status, body) = send(
        &router,
        Request::get("/health/live").body(Body::empty()).unwrap(),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["status"], "ok");
    let (status, body) = send(
        &router,
        Request::get("/health/ready").body(Body::empty()).unwrap(),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["status"], "ready");
}
