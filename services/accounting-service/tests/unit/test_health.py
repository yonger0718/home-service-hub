from fastapi.testclient import TestClient

from app.main import app


def test_liveness_returns_200(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_returns_200_when_db_reachable(client: TestClient):
    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


def test_health_routes_are_registered_once():
    health_routes = [
        route
        for route in app.routes
        if getattr(route, "path", None) in {"/health", "/health/ready"}
        and "GET" in getattr(route, "methods", set())
    ]

    assert len(health_routes) == 2


def test_legacy_accounting_routes_are_removed(client: TestClient):
    for path in ("/transactions/", "/categories/", "/cards/", "/payment-methods/", "/recurring/subscriptions"):
        assert client.get(path).status_code == 404, path
