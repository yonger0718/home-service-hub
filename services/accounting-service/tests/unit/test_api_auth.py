"""Optional bearer-token auth (ACCOUNTING_API_TOKENS). Unset means the middleware is a pure pass-through."""

import asyncio

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.cors import CORSMiddleware

from app.auth import ApiTokenMiddleware, api_auth_status, parse_tokens
from app.main import app

TOKEN = "s3cret-token-value"


@pytest.fixture()
def probe_app() -> FastAPI:
    """A tiny app behind the middleware that echoes the client label; needs no database."""
    probe = FastAPI()
    probe.add_middleware(ApiTokenMiddleware)

    @probe.get("/health")
    def health():
        return {"status": "ok"}

    @probe.get("/whoami")
    def whoami(request: Request):
        return {"label": getattr(request.state, "client_label", "<unset>")}

    @probe.post("/entries")
    def write():
        return {"ok": True}

    return probe


# --- label parsing ---------------------------------------------------------------------------


def test_parse_tokens_empty_or_unset_means_disabled():
    assert parse_tokens(None) == ()
    assert parse_tokens("") == ()
    assert parse_tokens("  , ,") == ()


def test_parse_tokens_reads_labels_and_bare_tokens():
    assert parse_tokens(" spa:abc , agent-x:def:ghi ,plain ") == (
        ("spa", "abc"),
        ("agent-x", "def:ghi"),
        (None, "plain"),
    )


def test_parse_tokens_empty_label_is_no_label():
    assert parse_tokens(":abc") == ((None, "abc"),)


def test_parse_tokens_rejects_an_empty_token_instead_of_disabling_auth():
    with pytest.raises(ValueError) as excinfo:
        parse_tokens("spa:")
    assert "spa" in str(excinfo.value)


def test_api_auth_status_never_names_a_token(monkeypatch):
    monkeypatch.delenv("ACCOUNTING_API_TOKENS", raising=False)
    assert api_auth_status() == "API auth: disabled"
    monkeypatch.setenv("ACCOUNTING_API_TOKENS", f"spa:{TOKEN},{TOKEN}2")
    status = api_auth_status()
    assert status.startswith("API auth: enabled (2 tokens")
    assert "spa" in status
    assert TOKEN not in status


# --- disabled: identical behaviour -----------------------------------------------------------




@pytest.mark.parametrize("raw", [None, "", " , "])
def test_disabled_middleware_hands_the_exact_scope_receive_send_through(monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv("ACCOUNTING_API_TOKENS", raising=False)
    else:
        monkeypatch.setenv("ACCOUNTING_API_TOKENS", raw)
    seen = []

    async def inner(scope, receive, send):
        seen.append((scope, receive, send))

    middleware = ApiTokenMiddleware(inner)
    scope = {"type": "http", "method": "POST", "path": "/entries", "headers": [(b"authorization", b"Bearer nope")]}
    snapshot = dict(scope)

    async def receive():  # pragma: no cover - never awaited
        return {}

    async def send(message):  # pragma: no cover - never awaited
        pass

    asyncio.run(middleware(scope, receive, send))
    assert seen == [(scope, receive, send)]
    assert seen[0][0] is scope and scope == snapshot  # no state or header added


def test_disabled_real_app_answers_without_a_header(monkeypatch):
    monkeypatch.delenv("ACCOUNTING_API_TOKENS", raising=False)
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "www-authenticate" not in response.headers


def test_disabled_probe_sets_no_client_label(monkeypatch, probe_app):
    monkeypatch.delenv("ACCOUNTING_API_TOKENS", raising=False)
    response = TestClient(probe_app).get("/whoami", headers={"Authorization": "Bearer whatever"})
    assert response.status_code == 200
    assert response.json() == {"label": "<unset>"}


# --- enabled ---------------------------------------------------------------------------------


@pytest.fixture()
def enabled(monkeypatch):
    monkeypatch.setenv("ACCOUNTING_API_TOKENS", f"spa:{TOKEN},agent-x:other-token,bare-token")
    monkeypatch.delenv("ACCOUNTING_DOCS_PUBLIC", raising=False)


def _assert_unauthorized(response):
    assert response.status_code == 401
    assert response.json() == {"detail": "unauthorized"}
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong"},
        {"Authorization": f"Bearer {TOKEN}x"},
        {"Authorization": f"Basic {TOKEN}"},
        {"Authorization": TOKEN},
        {"Authorization": "Bearer "},
        {"Authorization": f"Bearer spa:{TOKEN}"},
    ],
)
def test_enabled_refuses_missing_or_wrong_tokens(enabled, probe_app, headers):
    client = TestClient(probe_app)
    _assert_unauthorized(client.get("/whoami", headers=headers))
    _assert_unauthorized(client.post("/entries", headers=headers))


def test_enabled_accepts_a_valid_token_and_labels_the_request(enabled, probe_app):
    client = TestClient(probe_app)
    assert client.get("/whoami", headers={"Authorization": f"Bearer {TOKEN}"}).json() == {"label": "spa"}
    assert client.get("/whoami", headers={"Authorization": "bearer other-token"}).json() == {"label": "agent-x"}
    assert client.get("/whoami", headers={"Authorization": "Bearer bare-token"}).json() == {"label": None}
    assert client.post("/entries", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_enabled_health_is_exempt(enabled, probe_app):
    response = TestClient(probe_app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_enabled_real_app_health_exempt_and_root_protected(enabled):
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        _assert_unauthorized(client.get("/"))
        _assert_unauthorized(client.get("/accounts"))
        assert client.get("/", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_enabled_valid_token_reaches_a_database_route(enabled, client):
    _assert_unauthorized(client.get("/accounts"))
    assert client.get("/accounts", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_enabled_docs_protected_unless_docs_public(enabled, monkeypatch):
    with TestClient(app) as client:
        _assert_unauthorized(client.get("/docs"))
        _assert_unauthorized(client.get("/openapi.json"))
        monkeypatch.setenv("ACCOUNTING_DOCS_PUBLIC", "true")
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200
        _assert_unauthorized(client.get("/"))
        monkeypatch.setenv("ACCOUNTING_DOCS_PUBLIC", "false")
        _assert_unauthorized(client.get("/docs"))


def test_enabled_compares_with_hmac_compare_digest(enabled, probe_app, monkeypatch):
    import app.auth as auth

    calls = []
    real = auth.hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(auth.hmac, "compare_digest", spy)
    TestClient(probe_app).get("/whoami", headers={"Authorization": "Bearer bare-token"})
    assert len(calls) == 3  # every configured token is compared; no early exit


def test_auth_sits_inside_cors_so_preflight_and_401s_keep_cors_headers():
    classes = [m.cls for m in app.user_middleware]
    assert ApiTokenMiddleware in classes
    assert classes.index(CORSMiddleware) < classes.index(ApiTokenMiddleware)
