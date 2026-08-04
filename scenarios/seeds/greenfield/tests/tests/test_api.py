"""API behavior: create/resolve/analytics lifecycle, idempotency, concurrency,
error envelope, health, and the OpenAPI contract subset for the baseline.

Contract assertions are subsets (never equality) so later releases can add
endpoints without breaking this regression suite.
"""

import threading

import pytest
from fastapi.testclient import TestClient

from shortener.api import create_app

ADMIN = {"X-Admin-Token": "test-admin"}


@pytest.fixture()
def client():
    app = create_app(db_path=":memory:", admin_token="test-admin")
    with TestClient(app) as c:
        yield c


def make_link(client, url="https://example.com/target", **kwargs):
    resp = client.post("/v1/links", json={"url": url, **kwargs})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_resolve_stats_lifecycle(client):
    body = make_link(client)
    assert len(body["code"]) == 7
    resp = client.get(f"/{body['code']}", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "https://example.com/target"
    stats = client.get(f"/v1/links/{body['code']}/stats").json()
    assert stats["clicks"] == 1


def test_idempotent_replay(client):
    first = client.post("/v1/links", json={"url": "https://example.com",
                                           "idempotency_key": "req-1"})
    replay = client.post("/v1/links", json={"url": "https://example.com",
                                            "idempotency_key": "req-1"})
    assert first.status_code == 201 and replay.status_code == 200
    assert first.json()["code"] == replay.json()["code"]


def test_unknown_404_disabled_410(client):
    assert client.get("/zzzzzzz", follow_redirects=False).status_code == 404
    code = make_link(client)["code"]
    assert client.delete(f"/v1/links/{code}", headers=ADMIN).status_code == 204
    resp = client.get(f"/{code}", follow_redirects=False)
    assert resp.status_code == 410


def test_disable_requires_auth(client):
    code = make_link(client)["code"]
    assert client.delete(f"/v1/links/{code}").status_code == 401


def test_invalid_scheme_rejected(client):
    resp = client.post("/v1/links", json={"url": "ftp://example.com"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_request"


def test_concurrent_creates_unique_codes(client):
    codes, lock = [], threading.Lock()

    def create(i):
        resp = client.post("/v1/links", json={"url": f"https://example.com/{i}"})
        with lock:
            codes.append(resp.json()["code"])

    threads = [threading.Thread(target=create, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(codes) == len(set(codes)) == 16


def test_health_endpoints(client):
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").json()["status"] == "ready"


def test_error_envelope_uniform(client):
    for resp in [client.get("/zzzzzzz", follow_redirects=False),
                 client.post("/v1/links", json={"url": "ftp://x.com"})]:
        body = resp.json()
        assert set(body) == {"error"}
        assert {"code", "message"} <= set(body["error"])


def test_openapi_contract_subset(client):
    spec = client.get("/openapi.json").json()
    required = {
        "/v1/links": {"post"},
        "/v1/links/{code}": {"get", "delete"},
        "/v1/links/{code}/stats": {"get"},
        "/health/live": {"get"},
        "/health/ready": {"get"},
        "/{code}": {"get"},
    }
    for path, methods in required.items():
        assert path in spec["paths"], f"missing path {path}"
        assert methods <= set(spec["paths"][path]), f"missing methods on {path}"
