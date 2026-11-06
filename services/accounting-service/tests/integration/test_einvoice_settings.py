import pytest

from app import auth
from app.services import settings_service


@pytest.fixture(autouse=True)
def _reconciliation_on(monkeypatch):
    monkeypatch.setenv(auth.FEATURE_ENV, "true")


def _body(client, einvoice=None, auto_pay=None):
    current = client.get("/settings/reconciliation").json()
    body = {"account_map": current["account_map"], "dirty_enabled": current["dirty_enabled"],
            "auto_pay": auto_pay or {}}
    return body if einvoice is None else {**body, "einvoice": einvoice}


def test_defaults(client):
    got = client.get("/settings/reconciliation").json()
    assert got["einvoice"] == settings_service.EINVOICE_DEFAULTS == {
        "enabled": False, "remind_unmatched": True, "date_window_days": 3, "mute_prompt_after": 3}


def test_put_stores_block_and_bumps_version(client, db_session):
    got = client.get("/settings/reconciliation").json()
    put = client.put("/settings/reconciliation", json=_body(client, {"enabled": True}))
    assert put.status_code == 200
    after = put.json()
    assert after["einvoice"]["enabled"] is True and after["einvoice"]["date_window_days"] == 3
    assert after["version"] == got["version"] + 1
    assert (after["policy_version"], after["rules_version"]) == (got["policy_version"], got["rules_version"])
    assert settings_service.read_einvoice_settings(db_session)["enabled"] is True


@pytest.mark.parametrize("einvoice, fragment", [
    ({"date_window_days": 8}, "must be an integer in 0..7"),
    ({"date_window_days": -1}, "must be an integer in 0..7"),
    ({"date_window_days": True}, "must be an integer in 0..7"),
    ({"mute_prompt_after": 0}, "must be an integer in 1..10"),
    ({"mute_prompt_after": 11}, "must be an integer in 1..10"),
    ({"mute_prompt_after": True}, "must be an integer in 1..10"),
    ({"enabled": "yes"}, "must be a boolean"),
    ({"bogus": 1}, "unknown key")])
def test_refusals(client, einvoice, fragment):
    res = client.put("/settings/reconciliation", json=_body(client, einvoice))
    detail = str(res.json()["detail"])
    assert res.status_code == 422 and "einvoice" in detail and fragment in detail


def test_put_without_block_reverts_to_defaults(client):
    assert client.put("/settings/reconciliation", json=_body(client, {"enabled": True})).json()["einvoice"]["enabled"]
    after = client.put("/settings/reconciliation", json=_body(client)).json()
    assert after["einvoice"]["enabled"] is False


def test_merge_preferences_keeps_other_keys(client, db_session):
    put = client.put("/settings/reconciliation", json=_body(
        client, {"enabled": True, "date_window_days": 5}, auto_pay={"twin_window_days": 9})).json()
    out = settings_service.merge_einvoice_preferences(db_session, remind_unmatched=False)
    db_session.commit()
    assert out["version"] == put["version"] + 1
    after = client.get("/settings/reconciliation").json()
    assert after["einvoice"] == {"enabled": True, "date_window_days": 5, "remind_unmatched": False,
                                 "mute_prompt_after": 3}
    assert after["auto_pay"] == put["auto_pay"] and after["auto_pay"]["twin_window_days"] == 9
    assert after["version"] == put["version"] + 1
