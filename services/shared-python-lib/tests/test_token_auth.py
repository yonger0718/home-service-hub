"""shared_lib.token_auth (issue #117): label:token bearer auth with read/write scopes. Fictional tokens only."""
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shared_lib.token_auth import EnvNames, auth_status, install_token_auth, load_config

TOKENS = "spa:spa-secret,feed:feed-secret,jobs:jobs-secret"
SCOPES = "spa=read,write; feed=read; jobs=write"
NAMES = EnvNames.for_prefix("DEMO")


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        return {"status": "ok"}

    @app.get("/api/things")
    def things():
        return {"items": []}

    @app.post("/api/things")
    def make():
        return {"id": 1}

    @app.delete("/api/things/1")
    def remove():
        return {"ok": True}

    return app


@pytest.fixture
def env(monkeypatch):
    for name in ("DEMO_API_TOKENS", "DEMO_TOKEN_SCOPES", "DEMO_DOCS_PUBLIC"):
        monkeypatch.delenv(name, raising=False)

    def set_(tokens=TOKENS, scopes=SCOPES, docs_public=None):
        if tokens is not None:
            monkeypatch.setenv("DEMO_API_TOKENS", tokens)
        if scopes is not None:
            monkeypatch.setenv("DEMO_TOKEN_SCOPES", scopes)
        if docs_public is not None:
            monkeypatch.setenv("DEMO_DOCS_PUBLIC", docs_public)

    return set_


def _client() -> TestClient:
    app = _app()
    install_token_auth(app, "DEMO")
    return TestClient(app)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_no_tokens_is_a_pass_through(env):
    client = _client()
    assert client.get("/api/things").status_code == 200
    assert client.post("/api/things").status_code == 200
    assert auth_status(NAMES) == "API auth: disabled"


def test_missing_or_unknown_token_is_401(env):
    env()
    client = _client()
    for headers in ({}, _auth("nope"), {"Authorization": "Basic spa-secret"}, {"Authorization": "Bearer  spa-secret"}):
        response = client.get("/api/things", headers=headers)
        assert response.status_code == 401, headers
        assert response.json() == {"detail": "unauthorized"}
        assert response.headers["WWW-Authenticate"] == "Bearer"


def test_scheme_is_case_insensitive(env):
    env()
    assert _client().get("/api/things", headers={"Authorization": "bearer spa-secret"}).status_code == 200


def test_read_and_write_scopes_follow_the_method(env):
    env()
    client = _client()
    assert client.get("/api/things", headers=_auth("spa-secret")).status_code == 200
    assert client.post("/api/things", headers=_auth("spa-secret")).status_code == 200
    assert client.get("/api/things", headers=_auth("feed-secret")).status_code == 200
    assert client.head("/api/things", headers=_auth("feed-secret")).status_code in (200, 405)
    for method in ("post", "delete"):
        path = "/api/things" if method == "post" else "/api/things/1"
        response = getattr(client, method)(path, headers=_auth("feed-secret"))
        assert (response.status_code, response.json()) == (403, {"detail": "scope"})
    assert client.post("/api/things", headers=_auth("jobs-secret")).status_code == 200
    assert client.get("/api/things", headers=_auth("jobs-secret")).status_code == 403


def test_health_stays_open_and_docs_open_only_when_public(env):
    env()
    client = _client()
    assert client.get("/health").status_code == 200
    assert client.get("/health/ready").status_code == 200
    assert client.post("/health").status_code == 401  # only GET/HEAD are exempt
    assert client.get("/openapi.json").status_code == 401
    assert client.get("/docs").status_code == 401
    assert client.get("/openapi.json", headers=_auth("feed-secret")).status_code == 200
    env(tokens=None, scopes=None, docs_public="true")
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200


@pytest.mark.parametrize(
    ("tokens", "scopes", "message"),
    [
        ("bare-token", "x=read", "needs a label"),
        ("spa:", "spa=read", "empty token for label 'spa'"),
        ("spa:a,spa:b", "spa=read", "duplicate labels"),
        ("spa:a,feed:a", "spa=read;feed=read", "duplicate token values"),
        ("spa:a", None, "DEMO_TOKEN_SCOPES is required"),
        ("spa:a", "spa=admin", "unknown scope"),
        ("spa:a", "spa=", "no scopes for label 'spa'"),
        ("spa:a", "spa=read;spa=write", "duplicate label 'spa'"),
        ("spa:a,feed:b", "spa=read", "labels without a scope entry: ['feed']"),
        ("spa:a", "spa=read;ghost=read", "labels without a token: ['ghost']"),
    ],
)
def test_malformed_configuration_stops_startup_without_naming_a_token(env, tokens, scopes, message):
    env(tokens=tokens, scopes=scopes)
    with pytest.raises(ValueError) as caught:
        install_token_auth(_app(), "DEMO")
    assert message in str(caught.value)
    for secret in ("bare-token", "a", "b"):
        assert f":{secret}" not in str(caught.value) and f"'{secret}'" not in str(caught.value)


def test_a_configuration_broken_after_startup_fails_closed(env, monkeypatch):
    client = _client()
    monkeypatch.setenv("DEMO_API_TOKENS", "spa:a")  # no scopes
    response = client.get("/api/things", headers=_auth("a"))
    assert (response.status_code, response.json()) == (500, {"detail": "auth misconfigured"})


def test_status_line_names_labels_and_scopes_never_tokens(env):
    env()
    line = auth_status(NAMES)
    assert line == "API auth: enabled (3 tokens: spa=read+write, feed=read, jobs=write)"
    assert "secret" not in line
    assert load_config(NAMES).enabled


def test_token_never_reaches_the_log(env, caplog):
    env()
    caplog.set_level(logging.DEBUG)
    client = _client()
    client.get("/api/things", headers=_auth("spa-secret"))
    client.get("/api/things", headers=_auth("wrong-secret"))
    assert "secret" not in caplog.text
