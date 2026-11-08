"""Issue #117: the portfolio API's bearer-token auth (shared_lib.token_auth) on the real app. Fictional tokens only."""
import pytest

TOKENS = "spa:spa-test,accounting:feed-test,worker:jobs-test"
SCOPES = "spa=read,write; accounting=read; worker=write"
FEED = ("/api/portfolio/ledger-feed/settlements?broker=cathay"
        "&settle_from=2026-01-01&settle_to=2026-01-31&from=2026-01-01&to=2026-01-31")
WRITE = "/api/portfolio/fx/refresh"


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_API_TOKENS", TOKENS)
    monkeypatch.setenv("PORTFOLIO_TOKEN_SCOPES", SCOPES)
    monkeypatch.delenv("PORTFOLIO_DOCS_PUBLIC", raising=False)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_without_tokens_the_api_answers_as_before(client, monkeypatch):
    monkeypatch.delenv("PORTFOLIO_API_TOKENS", raising=False)
    monkeypatch.delenv("PORTFOLIO_TOKEN_SCOPES", raising=False)
    assert client.get(FEED).status_code == 200


def test_every_portfolio_route_needs_a_token(client, auth_on):
    for method, path in (("get", FEED), ("get", "/api/portfolio/transactions"), ("post", WRITE), ("get", "/docs")):
        response = getattr(client, method)(path)
        assert (response.status_code, response.json()) == (401, {"detail": "unauthorized"}), path


def test_health_stays_open(client, auth_on):
    assert client.get("/health").status_code != 401
    assert client.get("/health/ready").status_code != 401


def test_read_only_label_reads_the_feed_but_cannot_write(client, auth_on, monkeypatch):
    assert client.get(FEED, headers=_auth("feed-test")).status_code == 200
    response = client.post(WRITE, headers=_auth("feed-test"))
    assert (response.status_code, response.json()) == (403, {"detail": "scope"})


def test_write_only_label_cannot_read(client, auth_on):
    assert client.get(FEED, headers=_auth("jobs-test")).status_code == 403


def test_spa_label_passes_auth_for_both(client, auth_on):
    assert client.get(FEED, headers=_auth("spa-test")).status_code == 200
    assert client.get("/api/portfolio/transactions", headers=_auth("spa-test")).status_code == 200
    assert client.post(WRITE, headers=_auth("spa-test")).status_code not in (401, 403)


def test_auth_sits_inside_cors():
    from fastapi.middleware.cors import CORSMiddleware

    from app.main import app
    from shared_lib.token_auth import TokenAuthMiddleware

    classes = [m.cls for m in app.user_middleware]
    assert classes.index(CORSMiddleware) < classes.index(TokenAuthMiddleware)


def test_preflight_is_answered_and_401_keeps_cors_headers(client, auth_on):
    from fastapi.middleware.cors import CORSMiddleware

    from app.main import app

    cors = next(m for m in app.user_middleware if m.cls is CORSMiddleware)
    origins = [o for o in cors.kwargs.get("allow_origins", []) if o != "*"]
    if not origins:
        pytest.skip("no explicit CORS origin configured")
    origin = origins[0]
    preflight = client.options("/api/portfolio/transactions", headers={
        "Origin": origin, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"})
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == origin
    response = client.get("/api/portfolio/transactions", headers={"Origin": origin})
    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == origin
