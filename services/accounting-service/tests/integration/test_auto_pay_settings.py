import pytest

from app import auth
from app.models import Account
from app.services import settings_service
from app.services.reconciliation_actions import audit
from tests.integration.test_settings_crud_api import _account_body, _error_fields


@pytest.fixture(autouse=True)
def _reconciliation_on(monkeypatch):
    monkeypatch.setenv(auth.FEATURE_ENV, "true")


def _body(client, settlement=None, **auto_pay):
    current = client.get("/settings/reconciliation").json()
    body = {"account_map": current["account_map"], "dirty_enabled": current["dirty_enabled"], "auto_pay": auto_pay}
    return body if settlement is None else {**body, "settlement": settlement}


def test_defaults_and_version_bump(client, db_session):
    got = client.get("/settings/reconciliation").json()
    assert got["auto_pay"] == settings_service.AUTO_PAY_DEFAULTS == {
        "enabled": False, "enabled_from": None, "max_amount": "200000", "monthly_total": "500000",
        "twin_window_days": 7, "retry_days": 30, "extra_holidays": [], "extra_workdays": []}
    put = client.put("/settings/reconciliation", json=_body(client, enabled=True, enabled_from="2026-10-27", extra_holidays=["2026-12-31"]))
    assert put.status_code == 200
    after = put.json()
    assert after["auto_pay"]["enabled"] is True and after["auto_pay"]["max_amount"] == "200000"  # defaults overlaid
    assert after["version"] == got["version"] + 1
    assert (after["policy_version"], after["rules_version"]) == (got["policy_version"], got["rules_version"])
    assert settings_service.read_auto_pay_settings(db_session)["enabled_from"] == "2026-10-27"
    assert settings_service.read_auto_pay_settings(db_session)["version"] == after["version"]


@pytest.mark.parametrize("auto_pay", [
    {"enabled": True}, {"enabled_from": "27/10/2026"}, {"twin_window_days": 31}, {"retry_days": -1},
    {"max_amount": "-1"}, {"bogus": 1}, {"extra_holidays": ["2026-12-31"], "extra_workdays": ["2026-12-31"]}])
def test_refusals_name_the_field(client, auto_pay):
    res = client.put("/settings/reconciliation", json=_body(client, **auto_pay))
    assert res.status_code == 422 and "auto_pay" in str(res.json()["detail"])


def test_unknown_settlement_key_is_refused(client):
    res = client.put("/settings/reconciliation", json=_body(client, settlement={"bogus": 1}))
    assert res.status_code == 422 and "settlement" in str(res.json()["detail"])


def test_stand_ins_need_a_go_live_date(client):
    res = client.put("/settings/reconciliation", json=_body(client, settlement={"stand_in_enabled": True, "enabled_from": None}))
    assert res.status_code == 422 and "enabled_from" in str(res.json()["detail"])


def test_settlement_block_defaults_and_targets(client, seed, db_session):
    bank, market, usd = seed.account("交割戶"), seed.account("證券市值"), seed.account("美股", currency="USD")
    db_session.commit()
    assert client.get("/settings/reconciliation").json()["settlement"] == settings_service.SETTLEMENT_DEFAULTS
    for targets in ({str(bank.id): usd.id}, {str(bank.id): 999999}, {str(bank.id): bank.id}, {"x": market.id}):
        res = client.put("/settings/reconciliation", json=_body(client, settlement={"target_accounts": targets}))
        assert res.status_code == 422 and "settlement" in str(res.json()["detail"]), targets
    res = client.put("/settings/reconciliation", json=_body(client, settlement={"target_accounts": {str(bank.id): market.id},
                                                                                "twin_window_days": 5}))
    assert res.status_code == 200 and res.json()["settlement"]["target_accounts"] == {str(bank.id): market.id}
    assert settings_service.read_settlement_settings(db_session)["twin_window_days"] == 5


def test_audit_events_registered():
    assert {"auto_pay_booked", "auto_pay_skipped", "auto_pay_retired", "auto_pay_owner_deleted", "auto_pay_owner_edited",
            "auto_pay_header_changed", "auto_pay_statement_retired", "auto_pay_calendar_stale"} <= audit.EVENTS


def test_link_must_be_non_credit_non_archived_same_currency(client, db_session, seed):
    other_card = seed.account("別卡", is_credit=True)
    archived = seed.account("舊戶", is_archived=True)
    usd = seed.account("美金戶", currency="USD")
    bank = seed.account("薪轉")
    db_session.commit()
    for target in (other_card, archived, usd):
        res = client.post("/accounts", json=_account_body(name=f"卡{target.id}", is_credit=True, auto_pay_account_id=target.id))
        assert res.status_code == 422 and _error_fields(res) == {"auto_pay_account_id"}, target.name
    assert client.post("/accounts", json=_account_body(name="好卡", is_credit=True, auto_pay_account_id=bank.id)).status_code == 201


def test_archiving_the_bank_clears_the_links(client, db_session, seed):
    bank = seed.account("薪轉")
    card = seed.account("卡", is_credit=True, auto_pay_account_id=bank.id)
    db_session.commit()
    res = client.put(f"/accounts/{bank.id}", json=_account_body(name="薪轉", is_archived=True))
    assert res.status_code == 200
    db_session.expire_all()
    assert db_session.get(Account, card.id).auto_pay_account_id is None and db_session.get(Account, card.id).settings_locally_edited
