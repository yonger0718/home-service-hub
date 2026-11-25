"""永豐期貨 statement routes (spec §8)."""
from __future__ import annotations

from datetime import date

import pytest

from app.models.sinopac_futures_statement import FuturesStatementDay
from app.services import sinopac_futures_statement_service as svc
from tests.integration.test_futures_statement_import import daily, day_row, monthly

BASE = "/api/portfolio/imports/sinopac-futures-statement"
D = date(2026, 3, 9)


def _body(payload):
    return payload.model_dump(mode="json")


def _day(db):
    return day_row(db, D)


def test_post_daily_imports(client, db_session):
    response = client.post(BASE, json=_body(daily("2026-03-09", balance="100", deposits="100")))
    assert response.status_code == 200
    assert response.json() == {"outcome": "imported", "key": "2026-03-09", "findings": [], "errors": []}
    assert _day(db_session) is not None
    again = client.post(BASE, json=_body(daily("2026-03-09", balance="100", deposits="100")))
    assert again.json()["outcome"] == "duplicate"


def test_post_monthly_imports(client):
    response = client.post(BASE, json=_body(monthly("2026-02", a="0", b="50")))
    assert response.status_code == 200
    assert response.json()["key"] == "2026-02"


def test_post_dry_run_writes_nothing(client, db_session):
    response = client.post(BASE, params={"dry_run": "true"}, json=_body(daily("2026-03-09", balance="5", deposits="5")))
    assert response.json()["outcome"] == "imported"
    assert _day(db_session) is None


def test_post_identity_broken_422_writes_nothing(client, db_session):
    body = _body(daily("2026-03-09", balance="100", deposits="100"))
    body["equity"]["balance"] = "101"
    assert client.post(BASE, json=body).status_code == 422
    assert _day(db_session) is None


def test_post_500_when_rolled_back(client, db_session, monkeypatch):
    def boom(*args, **kwargs):
        raise ValueError("insert failed")

    monkeypatch.setattr(svc, "_apply", boom)
    response = client.post(BASE, json=_body(daily("2026-03-09", balance="100", deposits="100")))
    assert response.status_code == 500
    assert response.json()["errors"] == ["insert failed"]
    assert _day(db_session) is None


def test_validate_route_rolls_back(client, db_session):
    payloads = [_body(daily("2026-03-09", balance="100", deposits="100")), _body(monthly("2026-02", b="1"))]
    response = client.post(f"{BASE}/validate", json={"payloads": payloads})
    assert response.status_code == 200
    assert _day(db_session) is None
    assert client.post(f"{BASE}/validate", json={"payloads": [payloads[0]] * 201}).status_code == 422


def test_pending_shape(client):
    client.post(BASE, json=_body(daily("2026-03-09", balance="100", deposits="100")))
    client.post(BASE, json=_body(daily("2026-03-10", prev="90", balance="100", deposits="10")))
    body = client.get(f"{BASE}/pending").json()
    assert set(body) == {"items", "total", "truncated"}
    assert body["total"] == 1
    assert body["items"][0]["key"] == "2026-03-10"
    assert body["items"][0]["status"] == "findings"
    assert len(body["items"][0]["reason"]) <= 40


def test_ack_by_status(client, db_session):
    client.post(BASE, json=_body(daily("2026-03-09", balance="100", deposits="100")))
    client.post(BASE, json=_body(daily("2026-03-10", prev="90", balance="100", deposits="10")))
    assert client.post(f"{BASE}/days/2026-03-10/ack", params={"status": "conflict"}).status_code == 404
    assert client.post(f"{BASE}/days/2026-03-10/ack", params={"status": "findings"}).status_code == 204
    assert client.get(f"{BASE}/pending").json()["total"] == 0
    assert client.post(f"{BASE}/days/2026-03-10/ack", params={"status": "findings"}).status_code == 404
    assert client.post(f"{BASE}/days/2026-03-11/ack", params={"status": "findings"}).status_code == 404


def test_ack_bad_key_422(client):
    assert client.post(f"{BASE}/days/2026-3/ack", params={"status": "findings"}).status_code == 422
    assert client.post(f"{BASE}/days/2026-13/ack", params={"status": "findings"}).status_code == 422
    assert client.post(f"{BASE}/days/2026-03-09/ack", params={"status": "bogus"}).status_code == 422
    assert client.post(f"{BASE}/days/2026-03-09/ack").status_code == 422


def test_runs_latest(client):
    report = {"run_at": "2026-03-10T00:00:00+00:00", "result": "ok", "files_seen": 2, "posted": 1}
    assert client.post(f"{BASE}/runs", json=report).status_code == 204
    latest = client.get(f"{BASE}/runs/latest").json()
    assert latest["latest"]["result"] == "ok"
    assert latest["latest_ok"]["posted"] == 1


def test_runs_invalid_422(client):
    report = {"run_at": "2026-03-10T00:00:00", "result": "ok", "files_seen": 2, "posted": 1}
    assert client.post(f"{BASE}/runs", json=report).status_code == 422
    report = {"run_at": "2026-03-10T00:00:00+00:00", "result": "weird", "files_seen": 2, "posted": 1}
    assert client.post(f"{BASE}/runs", json=report).status_code == 422
