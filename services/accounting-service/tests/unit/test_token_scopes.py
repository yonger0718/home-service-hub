# tests/unit/test_token_scopes.py
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from app import auth

TOKENS = "spa:spa-token,hermes:hermes-token,worker:worker-token,ops:ops-token"


@pytest.fixture
def env(monkeypatch):
    def _set(scopes=None, feature=None, tokens=TOKENS, restricted=None):
        monkeypatch.setenv(auth.TOKENS_ENV, tokens)
        for name, value in ((auth.SCOPES_ENV, scopes), (auth.FEATURE_ENV, feature), (auth.RESTRICTED_ENV, restricted)):
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)
    return _set


def test_parse_scopes_splits_labels_and_scopes():
    parsed = auth.parse_scopes("spa=legacy; hermes=read,propose ;worker=ingest")
    assert parsed == {"spa": frozenset({"legacy"}), "hermes": frozenset({"read", "propose"}), "worker": frozenset({"ingest"})}


@pytest.mark.parametrize("raw", ["spa=nope", "spa=", "=read", "spa=read;spa=write", "spa:read"])
def test_parse_scopes_rejects_malformed(raw):
    with pytest.raises(ValueError):
        auth.parse_scopes(raw)


def test_validate_passes_for_all_legacy_without_scopes(env):
    env(tokens="spa:a,ops:b")
    auth.validate_auth_config()


def test_validate_refuses_feature_on_without_scopes(env):
    env(feature="true")
    with pytest.raises(ValueError, match="ACCOUNTING_TOKEN_SCOPES"):
        auth.validate_auth_config()


def test_validate_refuses_restricted_label_without_scopes(env):
    env()  # hermes + worker labels present, no scopes
    with pytest.raises(ValueError, match="restricted"):
        auth.validate_auth_config()


@pytest.mark.parametrize(
    "scopes",
    [
        "spa=legacy; hermes=read,propose",            # worker label missing from the map
        "spa=legacy; hermes=read,propose; worker=ingest; ghost=read",  # label not in tokens
        "spa=legacy,ingest; hermes=read; worker=ingest",  # legacy together with ingest
        "spa=legacy; hermes=read,enqueue,legacy; worker=ingest",  # legacy together with enqueue
        "spa=legacy; hermes=admin; worker=ingest",      # admin is fine; this row only checks ops missing below
    ],
)
def test_validate_refuses_incomplete_or_unsafe_maps(env, scopes):
    env(scopes=scopes)
    with pytest.raises(ValueError):
        auth.validate_auth_config()


def test_validate_refuses_bare_tokens_with_scopes(env):
    env(tokens="spa:a,bare-token", scopes="spa=legacy")
    with pytest.raises(ValueError, match="bare"):
        auth.validate_auth_config()


def test_validate_refuses_duplicate_token_values(env):
    env(tokens="spa:x,hermes:x", scopes="spa=legacy; hermes=read,propose")
    with pytest.raises(ValueError, match="duplicate token"):
        auth.validate_auth_config()


def test_validate_accepts_complete_map(env):
    env(scopes="spa=legacy; hermes=read,propose; worker=ingest; ops=legacy,admin", feature="true")
    auth.validate_auth_config()


@pytest.fixture
def probe(env):
    env(scopes="spa=legacy; hermes=read,propose; worker=ingest; ops=legacy,admin")
    app = FastAPI()
    app.add_middleware(auth.ApiTokenMiddleware)

    @app.get("/r", dependencies=[Depends(auth.require("read"))])
    def r():
        return {"ok": True}

    @app.post("/w", dependencies=[Depends(auth.require("write"))])
    def w():
        return {"ok": True}

    @app.post("/p", dependencies=[Depends(auth.require("propose"))])
    def p():
        return {"ok": True}

    @app.api_route("/m", methods=["GET", "POST"], dependencies=[Depends(auth.method_scope())])
    def m(request: Request):
        return {"method": request.method}

    @app.get("/f", dependencies=[Depends(auth.require_feature())])
    def f():
        return {"ok": True}

    return TestClient(app)


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def test_require_allows_legacy_everything(probe):
    assert probe.post("/w", headers=_h("spa-token")).status_code == 200
    assert probe.post("/p", headers=_h("spa-token")).status_code == 200


def test_require_restricts_hermes(probe):
    assert probe.get("/r", headers=_h("hermes-token")).status_code == 200
    assert probe.post("/p", headers=_h("hermes-token")).status_code == 200
    denied = probe.post("/w", headers=_h("hermes-token"))
    assert denied.status_code == 403 and denied.json()["detail"] == "scope"


def test_method_scope_maps_get_to_read_and_post_to_write(probe):
    assert probe.get("/m", headers=_h("hermes-token")).status_code == 200
    assert probe.post("/m", headers=_h("hermes-token")).status_code == 403
    assert probe.post("/m", headers=_h("worker-token")).status_code == 403
    assert probe.get("/m", headers=_h("worker-token")).status_code == 403  # ingest holds no read


def test_require_feature_404_when_off(probe, monkeypatch):
    monkeypatch.delenv(auth.FEATURE_ENV, raising=False)
    assert probe.get("/f", headers=_h("spa-token")).status_code == 404
    monkeypatch.setenv(auth.FEATURE_ENV, "true")
    assert probe.get("/f", headers=_h("spa-token")).status_code == 200


def test_unconfigured_scopes_pass_through(monkeypatch):
    monkeypatch.setenv(auth.TOKENS_ENV, "spa:a")
    monkeypatch.delenv(auth.SCOPES_ENV, raising=False)
    app = FastAPI()
    app.add_middleware(auth.ApiTokenMiddleware)

    @app.post("/w", dependencies=[Depends(auth.require("write"))])
    def w():
        return {"ok": True}

    assert TestClient(app).post("/w", headers=_h("a")).status_code == 200
