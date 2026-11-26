"""PATCH /settings/reconciliation/auto-pay: the write-scope switch + start date (notification center E3a)."""
import pytest

from app import auth
from app.services import settings_service

URL = "/settings/reconciliation/auto-pay"
ADMIN = {"Authorization": "Bearer adm-token"}
WRITE = {"Authorization": "Bearer wr-token"}
READ = {"Authorization": "Bearer rd-token"}


@pytest.fixture(autouse=True)
def _reconciliation_on(monkeypatch):
    monkeypatch.setenv(auth.FEATURE_ENV, "true")
    monkeypatch.setenv(auth.TOKENS_ENV, "adm:adm-token,wr:wr-token,rd:rd-token")
    monkeypatch.setenv(auth.SCOPES_ENV, "adm=read,write,admin; wr=read,write; rd=read")


def _put(client, **auto_pay):
    current = client.get("/settings/reconciliation", headers=ADMIN).json()
    body = {"account_map": {"manual/測試卡": 1}, "dirty_enabled": current["dirty_enabled"], "auto_pay": auto_pay,
            "einvoice": {"date_window_days": 5}}
    res = client.put("/settings/reconciliation", headers=ADMIN, json=body)
    assert res.status_code == 200, res.text
    return res.json()


def _settings(client):
    return client.get("/settings/reconciliation", headers=ADMIN).json()


def test_turn_on_merges_and_bumps_the_version(client):
    before = _put(client, twin_window_days=9, extra_holidays=["2026-12-31"])
    res = client.patch(URL, headers=WRITE, json={"enabled": True, "enabled_from": "2026-12-01"})
    assert res.status_code == 200, res.text
    assert res.json() == {"enabled": True, "enabled_from": "2026-12-01", "version": before["version"] + 1}
    after = _settings(client)
    assert after["auto_pay"] == {**before["auto_pay"], "enabled": True, "enabled_from": "2026-12-01"}
    assert after["auto_pay"]["twin_window_days"] == 9 and after["auto_pay"]["extra_holidays"] == ["2026-12-31"]
    others = ("auto_pay", "version")
    assert {k: v for k, v in after.items() if k not in others} == {k: v for k, v in before.items() if k not in others}


def test_turn_off_keeps_the_start_date(client):
    _put(client, enabled=True, enabled_from="2026-12-01")
    res = client.patch(URL, headers=WRITE, json={"enabled": False})
    assert res.status_code == 200, res.text
    assert res.json()["enabled"] is False and res.json()["enabled_from"] == "2026-12-01"
    assert _settings(client)["auto_pay"]["enabled_from"] == "2026-12-01"


def test_start_date_alone_while_on(client):
    before = _put(client, enabled=True, enabled_from="2026-12-01")
    res = client.patch(URL, headers=WRITE, json={"enabled_from": "2027-01-01"})
    assert res.status_code == 200, res.text
    assert res.json() == {"enabled": True, "enabled_from": "2027-01-01", "version": before["version"] + 1}


@pytest.mark.parametrize("stored, body", [
    ({}, {"enabled": True}),
    ({"enabled": True, "enabled_from": "2026-12-01"}, {"enabled_from": None}),
    ({}, {"enabled": True, "enabled_from": None}),
])
def test_on_without_a_start_date_is_refused_and_stores_nothing(client, stored, body):
    before = _put(client, **stored)
    res = client.patch(URL, headers=WRITE, json=body)
    assert res.status_code == 422, res.text
    assert "auto_pay" in str(res.json()["detail"]) and "enabled_from" in str(res.json()["detail"])
    assert _settings(client) == before


@pytest.mark.parametrize("body", [
    {}, {"max_amount": "1"}, {"enabled": True, "enabled_from": "2026-12-01", "retry_days": 1},
    {"enabled": "yes"}, {"enabled_from": "27/10/2026"}, {"enabled_from": 20261201}])
def test_bad_bodies_are_refused(client, body):
    before = _put(client)
    res = client.patch(URL, headers=WRITE, json=body)
    assert res.status_code == 422, res.text
    assert _settings(client) == before


def test_scopes(client):
    _put(client)
    assert client.patch(URL, headers=READ, json={"enabled": False}).status_code == 403
    assert client.patch(URL, headers=ADMIN, json={"enabled": False}).status_code == 200
    # the whole-document PUT stays admin-only
    body = {"account_map": {}, "auto_pay": {"enabled": False}}
    assert client.put("/settings/reconciliation", headers=WRITE, json=body).status_code == 403


def test_service_merge_creates_the_row(db_session):
    out = settings_service.merge_auto_pay_switch(db_session, {"enabled": True, "enabled_from": "2026-12-01"})
    db_session.commit()
    assert out == {"enabled": True, "enabled_from": "2026-12-01", "version": 2}
    assert settings_service.read_auto_pay_settings(db_session)["max_amount"] == "200000"
