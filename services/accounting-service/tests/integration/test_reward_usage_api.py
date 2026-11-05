"""GET /accounts/{id}/reward-usage and accruals on entries (reward posting engine spec 6, 5.2.2)."""
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app import auth
from app.models import RewardAccrual, RewardRule
from app.services import ledger_service
from tests.helpers import make_entry

NOV = (date(2026, 11, 1), date(2026, 11, 30))


def _rule(seed, card, **extra):
    extra.setdefault("window", "calendar_month")
    return seed.rule(card, name=extra.pop("name", "測試規則"), **extra)


def _accrual(db, rule, source, amount, state="estimated", ledger_entry_id=None, **extra):
    row = RewardAccrual(source_entry_id=source.id, rule_id=rule.id, window_start=NOV[0], window_end=NOV[1],
                        post_on=extra.pop("post_on", date(2026, 12, 5)), estimate=Decimal(amount),
                        amount=Decimal(amount), state=state, ledger_entry_id=ledger_entry_id, **extra)
    db.add(row)
    db.flush()
    return row


def _usage(client, card, **params):
    res = client.get(f"/accounts/{card.id}/reward-usage", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def _card_with_spends(seed, db, n=2):
    card = seed.account("測試卡", is_credit=True)
    spends = [seed.entry(card, "-1000", day=date(2026, 11, 3 + i)) for i in range(n)]
    db.flush()
    return card, spends


def test_usage_reports_used_estimated_remaining(client, db_session, seed):
    card, (a, b) = _card_with_spends(seed, db_session)
    rule = _rule(seed, card, total_cap=Decimal("100"))
    _accrual(db_session, rule, a, "30", state="posted")
    _accrual(db_session, rule, b, "20")
    db_session.commit()
    [item] = _usage(client, card, date="2026-11-10")
    assert item["rule_ids"] == [rule.id] and item["name"] == "測試規則" and item["shared_cap_id"] is None
    assert (item["window_start"], item["window_end"]) == ("2026-11-01", "2026-11-30")
    assert (Decimal(item["used"]), Decimal(item["estimated"]), Decimal(item["cap"]), Decimal(item["remaining"])) == (
        Decimal(30), Decimal(20), Decimal(100), Decimal(50))


def test_usage_counts_external_and_forfeited(client, db_session, seed):
    card, (a, b) = _card_with_spends(seed, db_session)
    rule = _rule(seed, card, total_cap=Decimal("100"))
    reward = make_entry(db_session, card, "7", kind="income", entry_date=date(2026, 11, 20),
                        reward_source_entry_id=a.id, reward_rule_id=rule.id)
    gone = _accrual(db_session, rule, b, "40", state="forfeited")  # no ledger row: counts 0
    assert gone.ledger_entry_id is None
    db_session.commit()
    [item] = _usage(client, card, date="2026-11-10")
    assert Decimal(item["used"]) == Decimal(7) and Decimal(item["remaining"]) == Decimal(93)


def test_usage_remaining_clamped_at_zero(client, db_session, seed):
    card, (a, b) = _card_with_spends(seed, db_session)
    rule = _rule(seed, card, total_cap=Decimal("10"))
    _accrual(db_session, rule, a, "30", state="posted")
    db_session.commit()
    [item] = _usage(client, card, date="2026-11-10")
    assert Decimal(item["remaining"]) == 0


def test_usage_shared_group_once(client, db_session, seed):
    card, (a, b) = _card_with_spends(seed, db_session)
    group = uuid4()
    r1 = _rule(seed, card, name="甲", total_cap=Decimal("100"), shared_cap_id=group)
    r2 = _rule(seed, card, name="乙", total_cap=Decimal("80"), shared_cap_id=group)
    _accrual(db_session, r1, a, "10", state="posted")
    _accrual(db_session, r2, b, "5")
    db_session.commit()
    [item] = _usage(client, card, date="2026-11-10")
    assert item["rule_ids"] == [r1.id, r2.id] and item["name"] == "甲" and item["shared_cap_id"] == str(group)
    assert (Decimal(item["used"]), Decimal(item["estimated"]), Decimal(item["cap"]), Decimal(item["remaining"])) == (
        Decimal(10), Decimal(5), Decimal(80), Decimal(65))


def test_usage_uncapped_nulls(client, db_session, seed):
    card, _ = _card_with_spends(seed, db_session)
    _rule(seed, card)
    db_session.commit()
    [item] = _usage(client, card, date="2026-11-10")
    assert item["cap"] is None and item["remaining"] is None
    assert Decimal(item["used"]) == 0 and Decimal(item["estimated"]) == 0


def test_usage_only_in_date_enabled_rules(client, db_session, seed):
    card, _ = _card_with_spends(seed, db_session)
    _rule(seed, card, name="停用", enabled=False)
    _rule(seed, card, name="過期", ends_on=date(2026, 10, 31))
    _rule(seed, card, name="未開始", starts_on=date(2026, 12, 1))
    live = _rule(seed, card, name="有效")
    db_session.commit()
    assert [i["rule_ids"] for i in _usage(client, card, date="2026-11-10")] == [[live.id]]


def test_usage_default_date_today(client, db_session, seed, monkeypatch):
    card, _ = _card_with_spends(seed, db_session)
    _rule(seed, card)
    db_session.commit()
    monkeypatch.setattr(ledger_service, "_today", lambda: date(2026, 11, 15))
    [item] = _usage(client, card)
    assert (item["window_start"], item["window_end"]) == ("2026-11-01", "2026-11-30")


def test_usage_unknown_account_404(client):
    assert client.get("/accounts/999999/reward-usage").status_code == 404


def test_usage_read_scope_ok_for_hermes(client, db_session, seed, monkeypatch):
    monkeypatch.setenv(auth.TOKENS_ENV, "spa:spa-token,hermes:hermes-token")
    monkeypatch.setenv(auth.SCOPES_ENV, "spa=legacy; hermes=read,propose,write")
    card, _ = _card_with_spends(seed, db_session)
    _rule(seed, card)
    db_session.commit()
    res = client.get(f"/accounts/{card.id}/reward-usage", params={"date": "2026-11-10"},
                     headers={"Authorization": "Bearer hermes-token"})
    assert res.status_code == 200 and len(res.json()) == 1


def test_entry_detail_lists_accruals(client, db_session, seed):
    card, (a, b) = _card_with_spends(seed, db_session)
    rule = _rule(seed, card, name="乙規則")
    row = _accrual(db_session, rule, a, "10")
    db_session.commit()
    body = client.get(f"/entries/{a.id}").json()
    assert body["accruals"] == [{
        "id": row.id, "rule_id": rule.id, "rule_name": "乙規則", "estimate": "10.0000", "amount": "10.0000",
        "state": "estimated", "post_on": "2026-12-05", "ledger_entry_id": None}]
    assert client.get(f"/entries/{b.id}").json()["accruals"] == []


def test_entry_list_includes_accruals(client, db_session, seed):
    card, (a, b) = _card_with_spends(seed, db_session)
    rule = _rule(seed, card)
    _accrual(db_session, rule, b, "12")
    db_session.commit()
    items = {i["id"]: i for i in client.get(f"/accounts/{card.id}/entries").json()["items"]}
    assert items[a.id]["accruals"] == [] and [x["amount"] for x in items[b.id]["accruals"]] == ["12.0000"]
