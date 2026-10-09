# tests/integration/test_scope_enforcement.py
import pytest
from fastapi.routing import APIRoute

from app import auth
from app.main import app

TOKENS = "spa:spa-token,hermes:hermes-token,worker:worker-token"
SCOPES = "spa=legacy; hermes=read,propose; worker=ingest"


@pytest.fixture
def scoped(monkeypatch):
    monkeypatch.setenv(auth.TOKENS_ENV, TOKENS)
    monkeypatch.setenv(auth.SCOPES_ENV, SCOPES)
    monkeypatch.setenv(auth.FEATURE_ENV, "true")


def _mutation_routes():
    for route in app.routes:
        if isinstance(route, APIRoute) and route.methods - {"GET", "HEAD", "OPTIONS"}:
            for method in sorted(route.methods - {"GET", "HEAD", "OPTIONS"}):
                yield method, route.path


def _fill(path: str) -> str:
    out = path
    for name in ("account_id", "entry_id", "group_id", "category_id", "project_id", "counterparty_id",
                 "definition_id", "instance_id", "run_id", "file_id", "statement_id", "sid", "case_id", "proposal_id", "action_id"):
        out = out.replace("{" + name + "}", "1")
    return out


@pytest.mark.parametrize("method,path", list(_mutation_routes()))
def test_hermes_token_cannot_mutate_anything_except_proposals(scoped, client, method, path):
    if path.endswith("/proposals"):
        pytest.skip("propose is the one mutation hermes may perform; covered in test_ingest_api")
    response = client.request(method, _fill(path), headers={"Authorization": "Bearer hermes-token"}, json={})
    assert response.status_code == 403, (method, path, response.text)
    assert response.json()["message"] == "scope"


def test_worker_token_cannot_read_ledger(scoped, client):
    response = client.get("/entries", headers={"Authorization": "Bearer worker-token"})
    assert response.status_code == 403


def test_spa_legacy_token_still_writes(scoped, client, seed, db_session):
    account = seed.account("A")
    db_session.commit()
    response = client.post(
        "/entries",
        headers={"Authorization": "Bearer spa-token"},
        json={"account_id": account.id, "kind": "expense", "amount": "10", "entry_date": "2026-09-01"},
    )
    assert response.status_code == 201


def test_feature_off_keeps_restrictions(scoped, monkeypatch, client):
    monkeypatch.setenv(auth.FEATURE_ENV, "false")
    response = client.post("/entries", headers={"Authorization": "Bearer hermes-token"}, json={})
    assert response.status_code == 403
