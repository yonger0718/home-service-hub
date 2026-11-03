"""Reward rule CRUD under /accounts/{id}/reward-rules (reward rules design §5.3)."""
import json
from datetime import date
from uuid import UUID

import pytest
from sqlalchemy import select, text

from decimal import Decimal

from app.models import EntryRewardRule, RewardRule
from app.services import reward_rule_service

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


def test_disabling_a_rule_whose_reward_account_was_archived_still_succeeds(client, db_session, seed):
    # Whole-row PUT (the 啟用 switch / 停用) resends untouched refs; an unchanged archived ref must not block it.
    card = seed.account("華航卡", is_credit=True)
    points = seed.account("點數", currency="PTS")
    project = seed.project("旅遊")
    rule = seed.rule(card)
    rule.reward_account_id, rule.reward_project_id = points.id, project.id
    db_session.flush()
    points.is_archived = project.is_archived = True
    db_session.commit()
    response = client.put(f"/accounts/{card.id}/reward-rules/{rule.id}",
                          json=_body(reward_account_id=points.id, reward_project_id=project.id, is_enabled=False))
    assert response.status_code == 200, response.text
    assert (response.json()["is_enabled"], response.json()["reward_account_id"]) == (False, points.id)

def test_rule_on_an_archived_card_paying_into_the_card_itself_can_be_saved(client, db_session, seed):
    card = seed.account("舊卡", is_credit=True, is_archived=True)
    rule = seed.rule(card)  # the import stored the card id explicitly
    db_session.commit()
    response = client.put(f"/accounts/{card.id}/reward-rules/{rule.id}",
                          json=_body(reward_account_id=card.id, is_enabled=False))
    assert response.status_code == 200, response.text
    created = client.post(f"/accounts/{card.id}/reward-rules", json=_body(reward_account_id=card.id))
    assert created.status_code == 201, created.text

def test_switching_to_a_different_archived_reward_account_is_still_422(client, db_session, seed):
    card = seed.account("華航卡", is_credit=True)
    points = seed.account("點數", currency="PTS")
    other = seed.account("舊點數", currency="PTS", is_archived=True)
    rule = seed.rule(card)
    rule.reward_account_id = points.id
    db_session.commit()
    response = client.put(f"/accounts/{card.id}/reward-rules/{rule.id}", json=_body(reward_account_id=other.id))
    assert response.status_code == 422 and response.json()["detail"][0]["loc"][-1] == "reward_account_id", response.text


def test_reorder_writes_positions_and_returns_the_list(client, db_session, seed):
    card = seed.account("華航卡")
    a, b, c = (seed.rule(card, name=n, sort_order=i) for i, n in enumerate("ABC"))
    db_session.commit()
    response = client.put(f"/accounts/{card.id}/reward-rules/order", json={"ids": [c.id, a.id, b.id]})
    assert response.status_code == 200, response.text
    assert [(r["name"], r["sort_order"]) for r in response.json()] == [("C", 0), ("A", 1), ("B", 2)]


def test_reorder_rejects_missing_or_extra_ids(client, db_session, seed):
    # Review focus 3: a stale list (a rule Hermes just added is missing) changes nothing.
    card, other = seed.account("華航卡"), seed.account("玉山卡")
    a, b = seed.rule(card, name="A", sort_order=0), seed.rule(card, name="B", sort_order=1)
    foreign = seed.rule(other)
    db_session.commit()
    for ids in ([a.id], [a.id, b.id, foreign.id], [a.id, a.id]):
        response = client.put(f"/accounts/{card.id}/reward-rules/order", json={"ids": ids})
        assert response.status_code == 422 and response.json()["detail"][0]["loc"][-1] == "ids", ids
    assert [r["sort_order"] for r in client.get(f"/accounts/{card.id}/reward-rules").json()] == [0, 1]


def test_delete_unreferenced_rule_and_refuse_referenced_with_counts(client, db_session, seed):
    card = seed.account("華航卡")
    free, used = seed.rule(card, name="free"), seed.rule(card, name="used")
    purchase = seed.entry(card, "-100")
    db_session.add(EntryRewardRule(entry_id=purchase.id, rule_id=used.id))
    seed.entry(card, "5", kind="reward", reward_rule_id=used.id, reward_source_entry_id=purchase.id)
    seed.entry(card, "7", kind="reward", reward_rule_id=used.id)
    db_session.commit()
    assert client.delete(f"/accounts/{card.id}/reward-rules/{free.id}").status_code == 204
    refused = client.delete(f"/accounts/{card.id}/reward-rules/{used.id}")
    assert refused.status_code == 409
    code, _, detail = refused.json()["message"].partition(": ")
    assert code == "rule_referenced" and json.loads(detail) == {"entries": 1, "rewards": 2}
    assert client.delete(f"/accounts/{card.id}/reward-rules/{free.id}").status_code == 404


def _fields(response) -> set[str]:
    return {e["loc"][-1] for e in response.json()["detail"]}


def test_per_unit_requires_unit_spend_and_forbids_rate(client, db_session, seed):
    card = seed.account("測試卡", is_credit=True)
    db_session.commit()
    url = f"/accounts/{card.id}/reward-rules"
    base = dict(method="per_unit", rate=None)
    r = client.post(url, json=_body(**base))
    assert r.status_code == 422 and "unit_spend" in _fields(r)
    r = client.post(url, json=_body(method="per_unit", unit_spend="20", rate="1"))
    assert r.status_code == 422 and "rate" in _fields(r)
    r = client.post(url, json=_body(**base, unit_spend="20"))
    assert r.status_code == 201, r.text
    assert r.json()["unit_spend"] == "20.0000"


def test_percent_forbids_unit_spend(client, db_session, seed):
    card = seed.account("測試卡", is_credit=True)
    db_session.commit()
    r = client.post(f"/accounts/{card.id}/reward-rules", json=_body(unit_spend="20"))
    assert r.status_code == 422 and "unit_spend" in _fields(r)


def test_calendar_month_window_round_trip(client, db_session, seed):
    card = seed.account("測試卡", is_credit=True)
    db_session.commit()
    assert _post(client, card.id, window="calendar_month")["window"] == "calendar_month"


def test_put_keeps_stored_zero_fixed_amount(client, db_session, seed):
    card = seed.account("測試卡", is_credit=True)
    rule = seed.rule(card, name="佔位")
    db_session.flush()
    db_session.execute(text("UPDATE reward_rule SET method='fixed', rate=NULL, fixed_amount=0 WHERE id=:i"), {"i": rule.id})
    db_session.commit()
    url = f"/accounts/{card.id}/reward-rules/{rule.id}"
    body = _body(name="佔位", method="fixed", rate=None, fixed_amount="0", description="新說明")
    r = client.put(url, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["description"] == "新說明"
    r = client.put(url, json=_body(name="佔位", method="percent", rate="1", fixed_amount="0"))
    assert r.status_code == 422 and "fixed_amount" in _fields(r)


def test_post_refuses_zero_fixed_amount(client, db_session, seed):
    card = seed.account("測試卡", is_credit=True)
    db_session.commit()
    r = client.post(f"/accounts/{card.id}/reward-rules", json=_body(method="fixed", rate=None, fixed_amount="0"))
    assert r.status_code == 422 and "fixed_amount" in _fields(r)


def test_put_zero_fixed_amount_on_nonzero_rule_refused(client, db_session, seed):
    card = seed.account("測試卡", is_credit=True)
    rule = seed.rule(card, name="固定")
    db_session.flush()
    db_session.execute(text("UPDATE reward_rule SET method='fixed', rate=NULL, fixed_amount=5 WHERE id=:i"), {"i": rule.id})
    db_session.commit()
    r = client.put(f"/accounts/{card.id}/reward-rules/{rule.id}",
                   json=_body(name="固定", method="fixed", rate=None, fixed_amount="0"))
    assert r.status_code == 422
    assert any(e["loc"][-1] == "fixed_amount" and "must be greater than 0" in e["msg"] for e in r.json()["detail"])


def test_shared_cap_requires_same_window(client, db_session, seed):
    card = seed.account("測試卡", is_credit=True)
    a = seed.rule(card, name="A")
    b = seed.rule(card, name="B", window="calendar_month")
    db_session.commit()
    copy = "rules sharing a cap must use the same window"
    r = client.put(f"/accounts/{card.id}/reward-rules/{b.id}",
                   json=_body(name="B", window="calendar_month", share_cap_with=a.id))
    assert r.status_code == 422
    assert any(e["loc"][-1] == "share_cap_with" and copy in e["msg"] for e in r.json()["detail"])
    r = client.post(f"/accounts/{card.id}/reward-rules",
                    json=_body(name="C", window="calendar_month", share_cap_with=a.id))
    assert r.status_code == 422 and "share_cap_with" in _fields(r)
    db_session.rollback()
    ok = client.put(f"/accounts/{card.id}/reward-rules/{b.id}", json=_body(name="B", share_cap_with=a.id))
    assert ok.status_code == 200, ok.text
    r = client.put(f"/accounts/{card.id}/reward-rules/{a.id}", json=_body(name="A", window="calendar_month"))
    assert r.status_code == 422
    assert any(e["loc"][-1] == "window" and copy in e["msg"] for e in r.json()["detail"])


def test_group_cap_is_minimum(client, db_session, seed):
    card = seed.account("測試卡", is_credit=True)
    a = seed.rule(card, name="A", total_cap=Decimal("300"))
    b = seed.rule(card, name="B", total_cap=Decimal("200"))
    c = seed.rule(card, name="C")
    solo = seed.rule(card, name="S", total_cap=Decimal("50"))
    db_session.commit()
    assert reward_rule_service.group_cap(db_session, solo) == Decimal("50")
    assert reward_rule_service.group_rule_ids(db_session, solo) == [solo.id]
    for r in (a, b, c):
        r.shared_cap_id = UUID(int=7)
    db_session.commit()
    assert reward_rule_service.group_cap(db_session, c) == Decimal("200")
    assert reward_rule_service.group_rule_ids(db_session, c) == sorted([a.id, b.id, c.id])
    a.total_cap = b.total_cap = None
    db_session.commit()
    assert reward_rule_service.group_cap(db_session, a) is None
