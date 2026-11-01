"""Reward rule CRUD under /accounts/{id}/reward-rules (reward rules design §5.3)."""
from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import select

from app.models import EntryRewardRule, RewardRule

def _body(**fields) -> dict:
    body = {"name": "國內", "method": "percent", "rate": "1.5", "posting": "after_window",
            "post_month_offset": 1, "post_day": 15}
    body.update(fields)
    return body

def _post(client, account_id, **fields):
    response = client.post(f"/accounts/{account_id}/reward-rules", json=_body(**fields))
    assert response.status_code == 201, response.text
    return response.json()

def test_create_appends_last_and_returns_the_row(client, db_session, seed):
    card = seed.account("華航卡", is_credit=True)
    seed.rule(card, name="舊", sort_order=4)
    db_session.commit()
    created = _post(client, card.id, description="國內一般消費")
    assert (created["account_id"], created["name"], created["rate"], created["sort_order"], created["moze_id"]) == (
        card.id, "國內", "1.5000", 5, None)
    assert (created["delay_days"], created["post_month_offset"], created["post_day"], created["shared_cap_id"]) == (0, 1, 15, None)
    assert client.get(f"/accounts/{card.id}/reward-rules").json()[-1]["id"] == created["id"]

def test_create_on_unknown_account_is_404_and_refs_are_checked(client, db_session, seed):
    card = seed.account("華航卡")
    archived = seed.account("舊點數", is_archived=True)
    db_session.commit()
    assert client.post("/accounts/999999/reward-rules", json=_body()).status_code == 404
    bad_account = client.post(f"/accounts/{card.id}/reward-rules", json=_body(reward_account_id=archived.id))
    bad_project = client.post(f"/accounts/{card.id}/reward-rules", json=_body(reward_project_id=999999))
    assert bad_account.status_code == 422 and bad_account.json()["detail"][0]["loc"][-1] == "reward_account_id"
    assert bad_project.status_code == 422 and bad_project.json()["detail"][0]["loc"][-1] == "reward_project_id"

@pytest.mark.parametrize("fields,field", [
    ({"rate": None}, "rate"), ({"fixed_amount": "10"}, "fixed_amount"),
    ({"method": "fixed", "rate": None}, "fixed_amount"), ({"method": "fixed", "fixed_amount": "50"}, "rate"),
    ({"starts_on": "2026-02-01", "ends_on": "2026-01-01"}, "ends_on"),
])
def test_cross_field_rules_are_422_naming_the_field(client, db_session, seed, fields, field):
    card = seed.account("華航卡")
    db_session.commit()
    response = client.post(f"/accounts/{card.id}/reward-rules", json=_body(**fields))
    assert response.status_code == 422 and response.json()["detail"][0]["loc"][-1] == field, response.text

def test_put_writes_the_whole_row_and_keeps_moze_id_and_sort_order(client, db_session, seed):
    card = seed.account("華航卡")
    rule = seed.rule(card, name="國內", moze_id="B-1", sort_order=3)
    db_session.commit()
    response = client.put(f"/accounts/{card.id}/reward-rules/{rule.id}",
                          json=_body(name="國內 2%", rate="2", posting="after_transaction", delay_days=3, is_enabled=False))
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["name"], body["rate"], body["posting"], body["delay_days"], body["post_month_offset"], body["post_day"]) == (
        "國內 2%", "2.0000", "after_transaction", 3, 0, 1)
    assert (body["is_enabled"], body["moze_id"], body["sort_order"]) == (False, "B-1", 3)

def test_put_on_another_accounts_rule_is_404(client, db_session, seed):
    card, other = seed.account("華航卡"), seed.account("玉山卡")
    rule = seed.rule(other)
    db_session.commit()
    assert client.put(f"/accounts/{card.id}/reward-rules/{rule.id}", json=_body()).status_code == 404

def test_basic_overlap_is_409_naming_the_other_rule(client, db_session, seed):
    card = seed.account("華航卡")
    old = seed.rule(card, name="基本舊", is_basic=True, starts_on=date(2026, 1, 1), ends_on=date(2026, 6, 30))
    db_session.commit()
    clash = client.post(f"/accounts/{card.id}/reward-rules",
                        json=_body(name="基本新", is_basic=True, starts_on="2026-06-30", ends_on=None))
    fine = client.post(f"/accounts/{card.id}/reward-rules",
                       json=_body(name="基本新", is_basic=True, starts_on="2026-07-01", ends_on=None))
    assert clash.status_code == 409 and clash.json()["message"].startswith(f"basic_rule_overlap: rule {old.id}")
    assert fine.status_code == 201

def test_basic_overlap_open_ended_and_disabled_neighbour(client, db_session, seed):
    # Review focus 2: an open-ended enabled basic rule clashes with anything later; a disabled one never does.
    card = seed.account("華航卡")
    seed.rule(card, name="開放", is_basic=True, starts_on=date(2026, 1, 1), ends_on=None)
    disabled_card = seed.account("玉山卡")
    seed.rule(disabled_card, name="停用基本", is_basic=True, enabled=False, starts_on=None, ends_on=None)
    db_session.commit()
    assert client.post(f"/accounts/{card.id}/reward-rules", json=_body(is_basic=True, starts_on="2030-01-01")).status_code == 409
    assert client.post(f"/accounts/{card.id}/reward-rules", json=_body(is_basic=True, is_enabled=False)).status_code == 201
    assert client.post(f"/accounts/{disabled_card.id}/reward-rules", json=_body(is_basic=True)).status_code == 201
    # both-null pair: the disabled card's new basic rule is open-ended, a second open-ended one clashes
    assert client.post(f"/accounts/{disabled_card.id}/reward-rules", json=_body(is_basic=True)).status_code == 409

def test_put_of_an_enabled_basic_rule_does_not_clash_with_itself(client, db_session, seed):
    # Review finding 4: autoflush sees the row being saved; the overlap check must exclude the rule's own id.
    card = seed.account("華航卡")
    rule = seed.rule(card, name="基本", is_basic=True, starts_on=date(2026, 1, 1), ends_on=None)
    db_session.commit()
    response = client.put(f"/accounts/{card.id}/reward-rules/{rule.id}",
                          json=_body(name="基本 1.5", is_basic=True, starts_on="2026-01-01", ends_on=None))
    assert response.status_code == 200, response.text

def test_share_cap_with_joins_leaves_and_refuses_other_accounts(client, db_session, seed):
    card, other = seed.account("華航卡"), seed.account("玉山卡")
    a = seed.rule(card, name="A")
    foreign = seed.rule(other, name="F")
    db_session.commit()
    b = _post(client, card.id, name="B", share_cap_with=a.id)
    db_session.expire_all()
    cap = db_session.get(RewardRule, a.id).shared_cap_id
    assert cap is not None and UUID(b["shared_cap_id"]) == cap
    kept = client.put(f"/accounts/{card.id}/reward-rules/{b['id']}", json=_body(name="B")).json()
    left = client.put(f"/accounts/{card.id}/reward-rules/{b['id']}", json=_body(name="B", share_cap_with="none")).json()
    assert UUID(kept["shared_cap_id"]) == cap and left["shared_cap_id"] is None
    assert client.post(f"/accounts/{card.id}/reward-rules", json=_body(share_cap_with=foreign.id)).status_code == 422
    assert client.put(f"/accounts/{card.id}/reward-rules/{a.id}", json=_body(name="A", share_cap_with=a.id)).status_code == 422

def test_disabling_a_linked_rule_keeps_the_link(client, db_session, seed):
    # Review focus 1: the link survives; editing the purchase still passes check_rules through `attached`.
    card = seed.account("華航卡")
    rule = seed.rule(card)
    entry = seed.entry(card, "-100", day=date(2026, 9, 1))
    db_session.add(EntryRewardRule(entry_id=entry.id, rule_id=rule.id))
    db_session.commit()
    assert client.put(f"/accounts/{card.id}/reward-rules/{rule.id}", json=_body(is_enabled=False)).status_code == 200
    assert db_session.scalar(select(EntryRewardRule.rule_id).where(EntryRewardRule.entry_id == entry.id)) == rule.id
    edited = client.put(f"/entries/{entry.id}", json={"account_id": card.id, "kind": "expense", "amount": "120",
                                                      "entry_date": "2026-09-01", "reward_rule_ids": [rule.id]})
    assert edited.status_code == 200, edited.text
