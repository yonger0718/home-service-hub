"""Reward engine hooks in the write paths: entries, transfers, refunds, splits, rules, accounts, deletes and the
review flag (spec 4.1, 5.1, 5.3, 5.4; plan P1, P6, P10, P11, P12)."""

import re
import threading
from datetime import date
from decimal import Decimal

import pytest
from psycopg2.errors import LockNotAvailable
from sqlalchemy import event, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.models import Account, EntryGroup, LedgerEntry, Project, RewardAccrual, RewardRule
from app.models.einvoice import Einvoice, EinvoiceLink
from app.models.statements import AccountStatement, ReconciliationCase, StatementRevision
from app.schemas.writes import AccountIn, BalanceAdjustmentIn, EntryUpdateIn, RefundIn, RewardRuleIn, SplitIn, TransferIn
from app.services import entry_write_service as ews
from app.services import (
    reward_engine, reward_posting, reward_rule_service, settings_service, settlement_service, split_service,
    transfer_service,
)
from app.services.entry_write_service import write_rule_links
from app.services.errors import RETRY, CodedConflictError
from tests.helpers import (
    make_account, make_definition, make_entry, make_instance, race, schedule_line,
)

DAY = date(2026, 11, 20)
NOV_WINDOW = (date(2026, 11, 16), date(2026, 12, 15))


def _card(db, name="測試卡", closing_day=15, **columns):
    return make_account(db, name, closing_day=closing_day, is_credit=True, **columns)


def _rule(db, card, **columns):
    values = dict(account_id=card.id, name="測試規則", method="percent", rate=Decimal("1"), posting="after_window",
                  post_month_offset=1, post_day=5)
    values.update(columns)
    rule = RewardRule(**values)
    db.add(rule)
    db.flush()
    return rule


def _spend(db, card, amount, day=DAY, rules=(), **columns):
    entry = make_entry(db, card, f"-{amount}", entry_date=day, **columns)
    write_rule_links(db, entry.id, [r.id for r in rules])
    reward_engine.sync_entry(db, entry.id)
    return entry


def _payload(card, **fields) -> dict:
    body = {"account_id": card.id, "kind": "expense", "amount": "1000", "entry_date": DAY.isoformat()}
    body.update(fields)
    return body


def _accruals(db, **where):
    db.expire_all()
    query = select(RewardAccrual).order_by(RewardAccrual.id)
    for key, value in where.items():
        query = query.where(getattr(RewardAccrual, key) == value)
    return list(db.scalars(query))


def _one(db, **where):
    rows = _accruals(db, **where)
    assert len(rows) == 1, rows
    return rows[0]


def _post(db, accrual, *, project_id=None, amount=None):
    """Freeze an accrual the way the daily job will (Task 7): a reward row plus state `posted`."""
    card = db.get(Account, db.get(RewardRule, accrual.rule_id).account_id)
    reward = make_entry(db, card, str(amount or accrual.amount),
                        kind="reward", entry_date=date(2027, 1, 5), source="rule",
                        reward_source_entry_id=accrual.source_entry_id, reward_rule_id=accrual.rule_id,
                        project_id=project_id)
    accrual.state, accrual.ledger_entry_id = "posted", reward.id
    db.flush()
    return reward


def _entry(db, entry_id) -> LedgerEntry:
    db.expire_all()
    return db.get(LedgerEntry, entry_id)


# --- create paths --------------------------------------------------------------------------------------------


def test_create_entry_with_rules_accrues(client, db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    db_session.commit()
    response = client.post("/entries", json=_payload(card, reward_rule_ids=[rule.id]))
    assert response.status_code == 201, response.text
    row = _one(db_session)
    assert (row.source_entry_id, row.rule_id, row.state) == (response.json()["id"], rule.id, "estimated")
    assert (row.window_start, row.window_end) == NOV_WINDOW
    assert row.amount == Decimal("10.0000")
    assert response.json()["review_reason"] is None


def test_transfer_leg_rules_accrue(client, db_session):
    card = _card(db_session)
    wallet = make_account(db_session, "測試錢包")
    rule = _rule(db_session, card)
    db_session.commit()
    response = client.post("/transfers", json={"from_account_id": card.id, "to_account_id": wallet.id,
                                               "out_amount": "1000", "entry_date": DAY.isoformat(),
                                               "reward_rule_ids": [rule.id]})
    assert response.status_code == 201, response.text
    row = _one(db_session)
    assert (row.source_entry_id, row.amount) == (response.json()["out_entry_id"], Decimal("10.0000"))


def test_refund_route_creates_negative_accrual(client, db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    db_session.commit()
    purchase_id = client.post("/entries", json=_payload(card, reward_rule_ids=[rule.id])).json()["id"]
    response = client.post(f"/entries/{purchase_id}/refund",
                           json={"amount": "300", "entry_date": date(2026, 12, 20).isoformat()})
    assert response.status_code == 201, response.text
    row = _one(db_session, source_entry_id=response.json()["id"])
    assert (row.rule_id, row.estimate, row.amount) == (rule.id, Decimal("-3.0000"), Decimal("-3.0000"))
    assert (row.window_start, row.window_end) == NOV_WINDOW  # the purchase's window


def test_split_paths_accrue(client, db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    db_session.commit()
    response = client.post("/splits", json={"entry_date": DAY.isoformat(), "members": [
        {"account_id": card.id, "kind": "expense", "amount": "1000", "reward_rule_ids": [rule.id]},
        {"account_id": card.id, "kind": "expense", "amount": "200"},
    ]})
    assert response.status_code == 201, response.text
    member_id = response.json()["members"][0]["id"]
    row = _one(db_session)
    assert (row.source_entry_id, row.amount) == (member_id, Decimal("10.0000"))


# --- review flag ---------------------------------------------------------------------------------------------


def test_put_after_post_flags_and_keeps_amount(client, db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    db_session.commit()
    purchase_id = client.post("/entries", json=_payload(card, reward_rule_ids=[rule.id])).json()["id"]
    _post(db_session, _one(db_session))
    db_session.commit()

    response = client.put(f"/entries/{purchase_id}", json=_payload(card, amount="2000", reward_rule_ids=[rule.id]))
    assert response.status_code == 200, response.text
    assert (response.json()["needs_review"], response.json()["review_reason"]) == (True, "reward_posted")
    row = _one(db_session)
    assert (row.state, row.amount) == ("posted", Decimal("10.0000"))
    entry = _entry(db_session, purchase_id)
    assert (entry.needs_review, entry.review_reason) == (True, "reward_posted")

    # A later edit that changes no amount, date, account or rule link keeps the flag.
    response = client.put(f"/entries/{purchase_id}", json=_payload(card, amount="2000", description="備註",
                                                                  reward_rule_ids=[rule.id]))
    assert response.status_code == 200, response.text
    entry = _entry(db_session, purchase_id)
    assert (entry.needs_review, entry.review_reason, entry.description) == (True, "reward_posted", "備註")


def test_put_without_frozen_accrual_sets_no_flag(client, db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    db_session.commit()
    purchase_id = client.post("/entries", json=_payload(card, reward_rule_ids=[rule.id])).json()["id"]
    response = client.put(f"/entries/{purchase_id}", json=_payload(card, amount="2000", reward_rule_ids=[rule.id]))
    assert (response.json()["needs_review"], response.json()["review_reason"]) == (False, None)
    assert _one(db_session).amount == Decimal("20.0000")


def test_transfer_update_after_post_flags(client, db_session):
    card = _card(db_session)
    wallet = make_account(db_session, "測試錢包")
    rule = _rule(db_session, card)
    db_session.commit()
    body = {"from_account_id": card.id, "to_account_id": wallet.id, "out_amount": "1000",
            "entry_date": DAY.isoformat(), "reward_rule_ids": [rule.id]}
    created = client.post("/transfers", json=body).json()
    _post(db_session, _one(db_session))
    db_session.commit()

    response = client.put(f"/transfers/{created['transfer_group_id']}", json={**body, "out_amount": "3000"})
    assert response.status_code == 200, response.text
    out_leg = _entry(db_session, created["out_entry_id"])
    assert (out_leg.needs_review, out_leg.review_reason) == (True, "reward_posted")
    row = _one(db_session)
    assert (row.state, row.amount) == ("posted", Decimal("10.0000"))
    in_leg = _entry(db_session, created["in_entry_id"])
    assert (in_leg.needs_review, in_leg.review_reason) == (False, None)

    # A cosmetic transfer edit keeps the out leg's flag.
    response = client.put(f"/transfers/{created['transfer_group_id']}", json={**body, "out_amount": "3000",
                                                                              "description": "備註"})
    assert response.status_code == 200, response.text
    out_leg = _entry(db_session, created["out_entry_id"])
    assert (out_leg.needs_review, out_leg.review_reason) == (True, "reward_posted")


# --- deletes -------------------------------------------------------------------------------------------------


def test_delete_purchase_drops_estimated_keeps_posted(client, db_session):
    card = _card(db_session)
    rule_a = _rule(db_session, card, name="測試規則A")
    rule_b = _rule(db_session, card, name="測試規則B")
    purchase = _spend(db_session, card, 1000, rules=[rule_a, rule_b])
    reward = _post(db_session, _one(db_session, rule_id=rule_b.id))
    purchase_id, reward_id, rule_a_id, rule_b_id = purchase.id, reward.id, rule_a.id, rule_b.id
    db_session.commit()

    assert client.delete(f"/entries/{purchase_id}").status_code == 204
    assert _accruals(db_session, rule_id=rule_a_id) == []
    survivor = _one(db_session, rule_id=rule_b_id)
    assert (survivor.state, survivor.source_entry_id, survivor.ledger_entry_id) == ("posted", None, reward_id)
    reward = _entry(db_session, reward_id)
    assert (reward.needs_review, reward.review_reason, reward.reward_source_entry_id) == (True, "reward_posted", None)


def test_delete_purchase_recomputes_window(client, db_session):
    card = _card(db_session)
    rule = _rule(db_session, card, total_cap=Decimal("15"))
    first = _spend(db_session, card, 1000, date(2026, 11, 18), [rule])
    second = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    first_id, second_id = first.id, second.id
    db_session.commit()
    assert _one(db_session, source_entry_id=second_id).amount == Decimal("5.0000")
    assert client.delete(f"/entries/{first_id}").status_code == 204
    assert _one(db_session).amount == Decimal("10.0000")


def test_delete_purchase_drops_refund_estimates(client, db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    db_session.commit()
    purchase_id = client.post("/entries", json=_payload(card, reward_rule_ids=[rule.id])).json()["id"]
    refund_id = client.post(f"/entries/{purchase_id}/refund",
                            json={"amount": "300", "entry_date": date(2026, 12, 20).isoformat()}).json()["id"]
    assert len(_accruals(db_session)) == 2

    assert client.delete(f"/entries/{purchase_id}").status_code == 204
    assert _accruals(db_session) == []
    refund = _entry(db_session, refund_id)
    assert refund is not None and refund.refunds_entry_id is None  # refunds stay, unlinked


def test_delete_engine_reward_row_forfeits(client, db_session):
    card = _card(db_session)
    rule = _rule(db_session, card, total_cap=Decimal("15"))
    first = _spend(db_session, card, 1000, date(2026, 11, 18), [rule])
    second = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    reward = _post(db_session, _one(db_session, source_entry_id=first.id))
    first_id, second_id, reward_id = first.id, second.id, reward.id
    db_session.commit()
    assert _one(db_session, source_entry_id=second_id).amount == Decimal("5.0000")

    assert client.delete(f"/entries/{reward_id}").status_code == 204
    forfeited = _one(db_session, source_entry_id=first_id)
    assert (forfeited.state, forfeited.ledger_entry_id, forfeited.amount) == ("forfeited", None, Decimal("10.0000"))
    assert _entry(db_session, reward_id) is None
    # A forfeited accrual without a ledger row uses 0 of the cap (5.2.2): the later purchase gets its full estimate.
    assert _one(db_session, source_entry_id=second_id).amount == Decimal("10.0000")


def test_owner_reward_delete_clears_purchase_flag(client, db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    db_session.commit()
    purchase_id = client.post("/entries", json=_payload(card, reward_rule_ids=[rule.id])).json()["id"]
    reward_id = _post(db_session, _one(db_session)).id
    db_session.commit()
    setup = client.put(f"/entries/{purchase_id}", json=_payload(card, amount="2000", reward_rule_ids=[rule.id]))
    assert setup.status_code == 200, setup.text
    purchase = _entry(db_session, purchase_id)
    assert (purchase.needs_review, purchase.review_reason) == (True, "reward_posted")

    assert client.delete(f"/entries/{reward_id}").status_code == 204
    purchase = _entry(db_session, purchase_id)
    assert (purchase.needs_review, purchase.review_reason) == (False, None)


def test_owner_reward_delete_keeps_other_review_flag(client, db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    purchase = _spend(db_session, card, 1000, rules=[rule])
    reward = _post(db_session, _one(db_session))
    purchase.needs_review = True  # flagged for some other reason (no review_reason)
    purchase_id, reward_id = purchase.id, reward.id
    db_session.commit()
    assert client.delete(f"/entries/{reward_id}").status_code == 204
    purchase = _entry(db_session, purchase_id)
    assert (purchase.needs_review, purchase.review_reason) == (True, None)


def test_delete_moze_reward_row_still_refused(client, db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    purchase = make_entry(db_session, card, "-1000", entry_date=DAY)
    reward = make_entry(db_session, card, "10", kind="reward", entry_date=date(2026, 12, 20), source="moze_import",
                        reward_source_entry_id=purchase.id, reward_rule_id=rule.id)
    reward_id = reward.id
    db_session.commit()
    response = client.delete(f"/entries/{reward_id}")
    assert response.status_code == 409
    assert "reward" in response.json()["message"]
    assert _entry(db_session, reward_id) is not None


_LOCK = re.compile(r"FOR (UPDATE|SHARE|NO KEY UPDATE|KEY SHARE)")


def _lock_kind(statement: str) -> str:
    if statement.lstrip().startswith("UPDATE ledger_entry"):
        return "ledger_entry_update"
    match = re.search(r"\bFROM (\w+)", statement)
    return match.group(1) if match else statement


def _ints(parameters) -> set[int]:
    """Every int bound in a statement's parameters (lists flattened; bools are not ids)."""
    values = parameters.values() if isinstance(parameters, dict) else (parameters or ())
    out: set[int] = set()
    for value in values:
        for item in value if isinstance(value, (list, tuple)) else (value,):
            if type(item) is int:
                out.add(item)
    return out


def test_engine_reward_delete_lock_order(db_session):
    """P12: schedule rows → entry_group → the purchase row FOR UPDATE (tier 1, its flag cleared) → the accrual
    window (ONE statement, by id, tier 2) → the reward row's ledger locks and updates; nothing touches the purchase
    and no accrual is locked after the accrual window lock."""
    card = _card(db_session)
    filler = make_account(db_session, "測試錢包")
    for _ in range(5):  # keep the purchase id apart from small ints the statements also bind
        make_entry(db_session, filler, "-1", entry_date=DAY)
    rule = _rule(db_session, card, total_cap=Decimal("15"))
    first = _spend(db_session, card, 1000, date(2026, 11, 18), [rule])
    _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    group = EntryGroup(kind="reward_claim")
    db_session.add(group)
    db_session.flush()
    reward = _post(db_session, _one(db_session, source_entry_id=first.id))
    reward.group_id = group.id
    first.needs_review, first.review_reason = True, "reward_posted"
    definition = make_definition(db_session, [schedule_line("income", card, "10")])
    make_instance(db_session, definition, 1, date(2027, 1, 5), status="posted", entries=[reward])
    purchase_id, reward_id = first.id, reward.id
    db_session.commit()

    captured: list[tuple[str, set[int]]] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if _LOCK.search(statement) or statement.lstrip().startswith("UPDATE ledger_entry"):
            captured.append((statement, _ints(parameters)))

    bind = db_session.connection().engine
    event.listen(bind, "before_cursor_execute", capture)
    try:
        ews.delete_entry(db_session, reward_id)
    finally:
        event.remove(bind, "before_cursor_execute", capture)
    db_session.commit()

    kinds = [_lock_kind(statement) for statement, _ in captured]
    on_purchase = [i for i, (_, ids) in enumerate(captured) if purchase_id in ids and kinds[i].startswith("ledger_entry")]
    assert kinds.count("reward_accrual") == 1, kinds
    accrual_at = kinds.index("reward_accrual")
    assert "ORDER BY reward_accrual.id" in captured[accrual_at][0]
    schedule_at = [i for i, k in enumerate(kinds) if k.startswith("schedule_")]
    group_at = [i for i, k in enumerate(kinds) if k == "entry_group"]
    purchase_lock = [i for i in on_purchase if kinds[i] == "ledger_entry"]
    reward_rows = [i for i, (_, ids) in enumerate(captured) if reward_id in ids and kinds[i].startswith("ledger_entry")]
    assert schedule_at and group_at and purchase_lock and reward_rows, kinds
    assert max(schedule_at) < min(group_at) < purchase_lock[0] < accrual_at < min(reward_rows), kinds
    assert all(i < accrual_at for i in on_purchase), (kinds, on_purchase)  # nothing on the purchase after tier 2
    db_session.expire_all()
    assert _one(db_session, ledger_entry_id=None, source_entry_id=purchase_id).state == "forfeited"
    purchase = db_session.get(LedgerEntry, purchase_id)
    assert (purchase.needs_review, purchase.review_reason) == (False, None)


def test_forfeited_accrual_without_reward_row_sets_no_flag(client, db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    db_session.commit()
    purchase_id = client.post("/entries", json=_payload(card, reward_rule_ids=[rule.id])).json()["id"]
    accrual = _one(db_session)
    accrual.state = "forfeited"  # e.g. a 0 allotment at posting: no ledger row (5.1)
    db_session.commit()
    response = client.put(f"/entries/{purchase_id}", json=_payload(card, amount="2000", reward_rule_ids=[rule.id]))
    assert response.status_code == 200, response.text
    assert (response.json()["needs_review"], response.json()["review_reason"]) == (False, None)


# --- project moves -------------------------------------------------------------------------------------------


def test_project_change_moves_reward_project(client, db_session):
    card = _card(db_session)
    other = Project(name="測試專案乙")
    new = Project(name="測試專案甲")
    db_session.add_all([other, new])
    db_session.flush()
    rule_a = _rule(db_session, card, name="測試規則A")
    rule_b = _rule(db_session, card, name="測試規則B")
    db_session.commit()
    purchase_id = client.post("/entries", json=_payload(card, reward_rule_ids=[rule_a.id, rule_b.id])).json()["id"]
    loose = _post(db_session, _one(db_session, rule_id=rule_a.id))
    kept = _post(db_session, _one(db_session, rule_id=rule_b.id), project_id=other.id)
    loose_id, kept_id, other_id, new_id = loose.id, kept.id, other.id, new.id
    db_session.commit()

    response = client.put(f"/entries/{purchase_id}", json=_payload(card, project_id=new_id,
                                                                  reward_rule_ids=[rule_a.id, rule_b.id]))
    assert response.status_code == 200, response.text
    assert _entry(db_session, loose_id).project_id == new_id
    assert _entry(db_session, kept_id).project_id == other_id
    purchase = _entry(db_session, purchase_id)
    assert (purchase.needs_review, purchase.review_reason) == (False, None)  # a project is not a price


def test_project_change_moves_old_project_reward(client, db_session):
    card = _card(db_session)
    old = Project(name="測試專案舊")
    new = Project(name="測試專案新")
    db_session.add_all([old, new])
    db_session.flush()
    rule = _rule(db_session, card)
    old_id, new_id = old.id, new.id
    db_session.commit()

    purchase_id = client.post("/entries", json=_payload(card, project_id=old_id,
                                                         reward_rule_ids=[rule.id])).json()["id"]
    reward_id = _post(db_session, _one(db_session), project_id=old_id).id
    db_session.commit()
    response = client.put(f"/entries/{purchase_id}", json=_payload(card, project_id=new_id, reward_rule_ids=[rule.id]))
    assert response.status_code == 200, response.text
    assert _entry(db_session, reward_id).project_id == new_id

    # The same through a split keep_full member whose only change is the project (_apply_meta).
    members = [
        {"account_id": card.id, "kind": "expense", "amount": "500", "project_id": old_id, "reward_rule_ids": [rule.id]},
        {"account_id": card.id, "kind": "expense", "amount": "200", "project_id": old_id},
    ]
    split = client.post("/splits", json={"entry_date": DAY.isoformat(), "members": members}).json()
    member_id = split["members"][0]["id"]
    accrual = _one(db_session, source_entry_id=member_id)
    split_reward_id = _post(db_session, accrual, project_id=old_id).id
    db_session.commit()
    edited = [{**members[0], "id": member_id, "project_id": new_id},
              {**members[1], "id": split["members"][1]["id"]}]
    response = client.put(f"/splits/{split['group_id']}", json={"entry_date": DAY.isoformat(), "members": edited})
    assert response.status_code == 200, response.text
    assert _entry(db_session, split_reward_id).project_id == new_id


def test_split_meta_edit_keeps_reward_flag(client, db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    db_session.commit()
    members = [
        {"account_id": card.id, "kind": "expense", "amount": "500", "reward_rule_ids": [rule.id]},
        {"account_id": card.id, "kind": "expense", "amount": "200"},
    ]
    split = client.post("/splits", json={"entry_date": DAY.isoformat(), "members": members}).json()
    member_id, other_id = split["members"][0]["id"], split["members"][1]["id"]
    _post(db_session, _one(db_session, source_entry_id=member_id))
    db_session.commit()

    priced = [{**members[0], "id": member_id, "amount": "800"}, {**members[1], "id": other_id}]
    assert client.put(f"/splits/{split['group_id']}", json={"entry_date": DAY.isoformat(), "members": priced}).status_code == 200
    member = _entry(db_session, member_id)
    assert (member.needs_review, member.review_reason) == (True, "reward_posted")

    renamed = [{**priced[0], "name": "改名"}, priced[1]]
    assert client.put(f"/splits/{split['group_id']}", json={"entry_date": DAY.isoformat(), "members": renamed}).status_code == 200
    member = _entry(db_session, member_id)
    assert (member.needs_review, member.review_reason, member.name) == (True, "reward_posted", "改名")

    kept = [{"id": member_id, "keep": True, "description": "備註"}, priced[1]]
    assert client.put(f"/splits/{split['group_id']}", json={"entry_date": DAY.isoformat(), "members": kept}).status_code == 200
    member = _entry(db_session, member_id)
    assert (member.needs_review, member.review_reason, member.description) == (True, "reward_posted", "備註")


# --- rule and account writes ---------------------------------------------------------------------------------


def _rule_body(**fields) -> dict:
    body = {"name": "測試規則", "method": "percent", "rate": "1", "posting": "after_window",
            "post_month_offset": 1, "post_day": 5}
    body.update(fields)
    return body


def _statement(db, card, period_start, period_end, *, hint=None):
    statement = AccountStatement(account_id=card.id, kind="card", currency="TWD", period_start=period_start,
                                 period_end=period_end, statement_total=Decimal("0"), origin="manual", mode="live")
    db.add(statement)
    db.flush()
    revision = StatementRevision(statement_id=statement.id, revision=1, parser="test", parser_version="1", raw={},
                                 period_start=period_start, period_end=period_end, statement_total=Decimal("0"),
                                 guardrail={}, guardrail_ok=True)
    db.add(revision)
    db.flush()
    statement.current_revision_id = revision.id
    if hint is not None:
        db.add(ReconciliationCase(statement_id=statement.id, revision_id=revision.id, kind="line_unmatched",
                                  context={"hint": hint}, status="open"))
    db.flush()
    return statement


def _needs_recheck(db, statement_id) -> bool:
    db.expire_all()
    return db.get(AccountStatement, statement_id).needs_recheck


def test_rule_put_recomputes_estimated(client, db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    _spend(db_session, card, 1000, rules=[rule])
    early = _statement(db_session, card, date(2026, 10, 16), date(2026, 11, 15), hint="reward_early")
    quiet = _statement(db_session, card, date(2026, 9, 16), date(2026, 10, 15), hint="bank_only")
    card_id, rule_id, early_id, quiet_id = card.id, rule.id, early.id, quiet.id
    db_session.commit()
    assert _one(db_session).amount == Decimal("10.0000")

    response = client.put(f"/accounts/{card_id}/reward-rules/{rule_id}", json=_rule_body(rate="2"))
    assert response.status_code == 200, response.text
    row = _one(db_session)
    assert (row.estimate, row.amount) == (Decimal("20.0000"), Decimal("20.0000"))
    assert _needs_recheck(db_session, early_id) is True  # P6
    assert _needs_recheck(db_session, quiet_id) is False


def test_rule_create_joining_group_recomputes(client, db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    _spend(db_session, card, 1000, rules=[rule])
    card_id, rule_id = card.id, rule.id
    db_session.commit()
    assert _one(db_session).amount == Decimal("10.0000")

    response = client.post(f"/accounts/{card_id}/reward-rules",
                           json=_rule_body(name="測試規則B", total_cap="4", share_cap_with=rule_id))
    assert response.status_code == 201, response.text
    row = _one(db_session)
    assert (row.estimate, row.amount) == (Decimal("10.0000"), Decimal("4.0000"))  # the group cap is now 4


def _account_body(card, **fields) -> dict:
    body = {"name": card.name, "currency": card.currency, "is_credit": True, "closing_day": card.closing_day,
            "combined_account_id": card.combined_account_id}
    body.update(fields)
    return body


def test_closing_day_change_rebuckets(client, db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    _spend(db_session, card, 1000, rules=[rule])
    early = _statement(db_session, card, date(2026, 10, 16), date(2026, 11, 15), hint="reward_early")
    card_id, body, early_id = card.id, _account_body(card, closing_day=25), early.id
    db_session.commit()

    response = client.put(f"/accounts/{card_id}", json=body)
    assert response.status_code == 200, response.text
    row = _one(db_session)
    assert (row.window_start, row.window_end) == (date(2026, 10, 26), date(2026, 11, 25))
    assert _needs_recheck(db_session, early_id) is True  # P6


def test_parent_closing_day_change_rebuckets_children(client, db_session):
    parent = _card(db_session, "測試主卡", closing_day=15)
    child = _card(db_session, "測試附卡", closing_day=None, combined_account_id=parent.id)
    rule = _rule(db_session, child)
    _spend(db_session, child, 1000, rules=[rule])
    early = _statement(db_session, child, date(2026, 10, 16), date(2026, 11, 15), hint="reward_early")
    parent_id, body, early_id = parent.id, _account_body(parent, closing_day=25), early.id
    db_session.commit()
    assert (_one(db_session).window_start, _one(db_session).window_end) == NOV_WINDOW

    response = client.put(f"/accounts/{parent_id}", json=body)
    assert response.status_code == 200, response.text
    row = _one(db_session)
    assert (row.window_start, row.window_end) == (date(2026, 10, 26), date(2026, 11, 25))
    assert _needs_recheck(db_session, early_id) is True  # P6 on the child's statements too


def test_account_put_without_closing_day_change_leaves_accruals(client, db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    _spend(db_session, card, 1000, rules=[rule])
    early = _statement(db_session, card, date(2026, 10, 16), date(2026, 11, 15), hint="reward_early")
    card_id, body, early_id = card.id, _account_body(card, name="測試卡改名"), early.id
    db_session.commit()
    assert client.put(f"/accounts/{card_id}", json=body).status_code == 200
    assert (_one(db_session).window_start, _one(db_session).window_end) == NOV_WINDOW
    assert _needs_recheck(db_session, early_id) is False


# --- concurrency: rule recompute and split writes (PR #290 review, P12) ----------------------------------------


def _capture_locks(db, fn) -> list[tuple[str, set[int]]]:
    """Run fn() and return every row-locking statement it issued (with the ints it bound), in order."""
    captured: list[tuple[str, set[int]]] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if _LOCK.search(statement):
            captured.append((statement, _ints(parameters)))

    bind = db.connection().engine
    event.listen(bind, "before_cursor_execute", capture)
    try:
        fn()
    finally:
        event.remove(bind, "before_cursor_execute", capture)
    return captured


def _fillers(db, count=5):
    """Ledger rows that keep the tested ids apart from the small ints the statements also bind."""
    filler = make_account(db, "測試錢包")
    for _ in range(count):
        make_entry(db, filler, "-1", entry_date=DAY)


def test_rule_put_first_then_purchase_put_prices_on_committed_values(pg_engine, db_session):
    """Finding 1, rule first: a rule PUT (1% -> 2%) holds the rule rows and the purchase (its NOWAIT source lock);
    a purchase PUT (100 -> 200) waits and then prices on the committed rule: 200 x 2% = 4. (The purchase-first
    order is `test_rule_put_while_purchase_put_holds_the_purchase_gets_retry`.)"""
    card = _card(db_session)
    rule = _rule(db_session, card)
    spend = _spend(db_session, card, 100, rules=[rule])
    card_id, rule_id, spend_id = card.id, rule.id, spend.id
    db_session.commit()
    assert _one(db_session).amount == Decimal("1.0000")
    payload = EntryUpdateIn(account_id=card_id, kind="expense", amount=Decimal("200"), entry_date=DAY,
                            posted_date=DAY, reward_rule_ids=[rule_id])

    def put_purchase(db):
        ews.update_entry(db, spend_id, payload)

    def put_rule(db):
        reward_rule_service.update(db, card_id, rule_id, RewardRuleIn(**_rule_body(rate="2")))

    assert race(pg_engine, put_rule, put_purchase) == "committed"
    row = _one(db_session)
    assert (row.estimate, row.amount) == (Decimal("4.0000"), Decimal("4.0000"))


def test_rule_recompute_locks_sources_before_windows(db_session):
    """P12 in recompute_rules: the accruals' sources (a refund and the purchase it refunds) FOR UPDATE in id order
    (tier 1) before ONE window lock (tier 2); no ledger row is locked after it."""
    card = _card(db_session)
    _fillers(db_session)
    rule = _rule(db_session, card)
    buy = _spend(db_session, card, 1000, rules=[rule])
    db_session.commit()
    refund_id = settlement_service.refund(db_session, buy.id, RefundIn(amount=Decimal("200"), entry_date=DAY))
    buy_id, rule_id, card_id = buy.id, rule.id, card.id
    db_session.commit()

    captured = _capture_locks(db_session, lambda: reward_rule_service.update(
        db_session, card_id, rule_id, RewardRuleIn(**_rule_body(rate="2"))))
    db_session.commit()

    kinds = [_lock_kind(statement) for statement, _ in captured]
    assert kinds.count("reward_accrual") == 1, kinds
    accrual_at = kinds.index("reward_accrual")
    sources = [i for i, (_, ids) in enumerate(captured) if kinds[i] == "ledger_entry" and {buy_id, refund_id} & ids]
    assert sources and max(sources) < accrual_at, kinds
    assert {buy_id, refund_id} <= set().union(*(captured[i][1] for i in sources))
    assert "ORDER BY ledger_entry.id" in captured[sources[0]][0]
    assert all(k != "ledger_entry" for k in kinds[accrual_at + 1:]), kinds
    assert _one(db_session, source_entry_id=buy_id).amount == Decimal("20.0000")
    assert _one(db_session, source_entry_id=refund_id).amount == Decimal("-4.0000")


def test_parent_closing_day_change_takes_one_window_lock(db_session):
    """A closing_day change on a card with a combined child recomputes both cards in one pass: both cards'
    purchases are locked before ONE window lock (was one `lock_windows` per card)."""
    _fillers(db_session)
    parent = _card(db_session, "測試主卡", closing_day=15)
    child = _card(db_session, "測試附卡", closing_day=None, combined_account_id=parent.id)
    own = _spend(db_session, parent, 1000, rules=[_rule(db_session, parent)])
    theirs = _spend(db_session, child, 500, rules=[_rule(db_session, child, name="測試規則B")])
    parent_id, own_id, theirs_id = parent.id, own.id, theirs.id
    body = AccountIn(**_account_body(parent, closing_day=25))
    db_session.commit()

    captured = _capture_locks(db_session, lambda: settings_service.update_account(db_session, parent_id, body))
    db_session.commit()

    kinds = [_lock_kind(statement) for statement, _ in captured]
    assert kinds.count("reward_accrual") == 1, kinds
    accrual_at = kinds.index("reward_accrual")
    sources = [i for i, (_, ids) in enumerate(captured) if kinds[i] == "ledger_entry" and {own_id, theirs_id} & ids]
    assert sources and max(sources) < accrual_at, kinds
    for source_id in (own_id, theirs_id):
        row = _one(db_session, source_entry_id=source_id)
        assert (row.window_start, row.window_end) == (date(2026, 10, 26), date(2026, 11, 25))


DEC_DAY = date(2026, 12, 20)  # the Dec window (12-16..01-15) of a closing-day-15 card


def _split_in(members, **group) -> SplitIn:
    return SplitIn(**{"entry_date": DAY, **group, "members": members})


def _member(card, rule, amount, day, **fields) -> dict:
    return {"account_id": card.id, "kind": "expense", "amount": amount, "entry_date": day,
            "reward_rule_ids": [rule.id], **fields}


def test_split_writes_take_one_window_lock(db_session):
    """Finding 2: every split write locks the union of its windows in ONE statement, before any member write: a
    create, a convert, and a PUT that drops one member, re-prices one into another window and adds a new one."""
    card = _card(db_session)
    _fillers(db_session)
    rule = _rule(db_session, card)
    _spend(db_session, card, 300, rules=[rule])  # a row in the Nov window: its lock is a real row lock
    _spend(db_session, card, 300, DEC_DAY, rules=[rule])
    db_session.commit()

    def window_locks(fn):
        captured = _capture_locks(db_session, fn)
        db_session.commit()
        return [_lock_kind(statement) for statement, _ in captured].count("reward_accrual")

    created = {}

    def create():
        created["result"] = split_service.create_split_result(db_session, _split_in(
            [_member(card, rule, "100", DAY), _member(card, rule, "200", DEC_DAY)]))

    assert window_locks(create) == 1
    first_id, second_id = (entry_id for entry_id, _ in created["result"].members)

    def put():
        split_service.update_split(db_session, created["result"].group_id, _split_in(
            [_member(card, rule, "400", DAY, id=second_id), _member(card, rule, "50", DEC_DAY)]))

    assert window_locks(put) == 1
    assert _accruals(db_session, source_entry_id=first_id) == []
    moved = _one(db_session, source_entry_id=second_id)
    assert (moved.window_start, moved.amount) == (NOV_WINDOW[0], Decimal("4.0000"))

    anchor = _spend(db_session, card, 600, DEC_DAY, rules=[rule])
    anchor_id = anchor.id
    db_session.commit()

    def convert():
        split_service.convert_to_split(db_session, anchor_id, _split_in(
            [_member(card, rule, "500", DAY, id=anchor_id), _member(card, rule, "100", DEC_DAY)]))

    assert window_locks(convert) == 1
    converted = _one(db_session, source_entry_id=anchor_id)
    assert (converted.window_start, converted.amount) == (NOV_WINDOW[0], Decimal("5.0000"))


def test_two_splits_on_one_card_opposite_window_order_no_deadlock(pg_engine, db_session, monkeypatch):
    """Finding 2: split X re-prices its Nov member then its Dec member, split Y (same card, same rule) its Dec
    member then its Nov member. Each transaction pauses after its first window lock until the other has one too
    (or 2 s pass). With one window lock per member write the two each held one window and waited on the other
    (40P01); with one union lock per split the second waits on the first and both commit."""
    card = _card(db_session)
    rule = _rule(db_session, card)
    groups = {}
    for key in ("x", "y"):
        result = split_service.create_split_result(db_session, _split_in(
            [_member(card, rule, "100", DAY), _member(card, rule, "200", DEC_DAY)]))
        groups[key] = (result.group_id, [entry_id for entry_id, _ in result.members])
    db_session.commit()

    def payload(key, order):
        nov_id, dec_id = groups[key][1]
        members = {"nov": _member(card, rule, "1000", DAY, id=nov_id),
                   "dec": _member(card, rule, "2000", DEC_DAY, id=dec_id)}
        return _split_in([members[name] for name in order])

    plans = {"x": payload("x", ("nov", "dec")), "y": payload("y", ("dec", "nov"))}
    arrived = {"x": threading.Event(), "y": threading.Event()}
    real = reward_engine.lock_windows

    def lock_windows(db, pairs, **kwargs):
        rows = real(db, pairs, **kwargs)
        me = threading.current_thread().name
        if me in arrived and not arrived[me].is_set():
            arrived[me].set()
            arrived["y" if me == "x" else "x"].wait(2)
        return rows

    monkeypatch.setattr(reward_engine, "lock_windows", lock_windows)
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    out: dict = {}

    def runner(key):
        session = factory()
        try:
            split_service.update_split(session, groups[key][0], plans[key])
            session.commit()
            out[key] = "committed"
        except Exception as exc:  # noqa: BLE001  (asserted below)
            session.rollback()
            out[key] = exc
        finally:
            session.close()

    threads = [threading.Thread(target=runner, args=(key,), name=key, daemon=True) for key in ("x", "y")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)
    assert not any(thread.is_alive() for thread in threads), out
    assert out == {"x": "committed", "y": "committed"}, out
    amounts = sorted(row.amount for row in _accruals(db_session))
    assert amounts == [Decimal("10.0000")] * 2 + [Decimal("20.0000")] * 2


def _transfer_in(source, target, rule, amount, day) -> TransferIn:
    return TransferIn(from_account_id=source.id, to_account_id=target.id, out_amount=Decimal(amount), entry_date=day,
                      reward_rule_ids=[rule.id])


def _link_in_leg(db, group_id, rule):
    """Only the out leg takes rule links through the API; an imported in leg may carry one. Seed that shape so both
    legs of the transfer have accruals (in windows of two different rules)."""
    _, in_leg = transfer_service.transfer_legs(db, group_id)
    write_rule_links(db, in_leg.id, [rule.id])
    reward_engine.sync_entry(db, in_leg.id)
    return in_leg


def _window_lock_count(db, fn) -> int:
    captured = _capture_locks(db, fn)
    db.commit()
    return [_lock_kind(statement) for statement, _ in captured].count("reward_accrual")


def test_transfer_writes_take_one_window_lock(db_session):
    """Concern 2 of the fix round: a transfer write locks both legs' old and new windows in ONE statement (create,
    an update that moves the out leg to another window and drops the in leg's accrual, and a delete)."""
    card_a, card_b = _card(db_session, "測試卡"), _card(db_session, "測試卡B")
    _fillers(db_session)
    rule_a, rule_b = _rule(db_session, card_a), _rule(db_session, card_b, name="測試規則B")
    _spend(db_session, card_a, 300, rules=[rule_a])
    _spend(db_session, card_b, 300, rules=[rule_b])
    db_session.commit()

    made = {}

    def create():
        made["group"] = transfer_service.create_transfer(db_session, _transfer_in(card_a, card_b, rule_a, "1000", DAY))

    assert _window_lock_count(db_session, create) == 1
    in_leg_id = _link_in_leg(db_session, made["group"], rule_b).id
    db_session.commit()
    assert len(_accruals(db_session, source_entry_id=in_leg_id)) == 1

    def put():
        transfer_service.update_transfer(db_session, made["group"], _transfer_in(card_a, card_b, rule_a, "2000", DEC_DAY))

    assert _window_lock_count(db_session, put) == 1
    out_leg, _ = transfer_service.transfer_legs(db_session, made["group"])
    out_id = out_leg.id
    moved = _one(db_session, source_entry_id=out_id)
    assert (moved.window_start, moved.amount) == (date(2026, 12, 16), Decimal("20.0000"))
    assert _accruals(db_session, source_entry_id=in_leg_id) == []

    assert _window_lock_count(db_session, lambda: ews.delete_entry(db_session, out_id)) == 1
    assert _accruals(db_session, source_entry_id=out_id) == []


def test_two_transfers_opposite_leg_window_order_no_deadlock(pg_engine, db_session, monkeypatch):
    """Concern 2: transfer X (A -> B) syncs its out leg in rule A's window, then its in leg in rule B's; transfer Y
    (B -> A) the other way round. Each transaction pauses after its first window lock until the other has one too
    (or 2 s pass). With one window lock per leg the two each held one window and waited on the other (40P01); with
    one union lock per transfer the second waits on the first and both commit."""
    card_a, card_b = _card(db_session, "測試卡"), _card(db_session, "測試卡B")
    rule_a, rule_b = _rule(db_session, card_a), _rule(db_session, card_b, name="測試規則B")
    x = transfer_service.create_transfer(db_session, _transfer_in(card_a, card_b, rule_a, "1000", DAY))
    _link_in_leg(db_session, x, rule_b)
    y = transfer_service.create_transfer(db_session, _transfer_in(card_b, card_a, rule_b, "1000", DAY))
    _link_in_leg(db_session, y, rule_a)
    db_session.commit()
    plans = {"x": (x, _transfer_in(card_a, card_b, rule_a, "3000", DAY)),
             "y": (y, _transfer_in(card_b, card_a, rule_b, "3000", DAY))}
    arrived = {"x": threading.Event(), "y": threading.Event()}
    real = reward_engine.lock_windows

    def lock_windows(db, pairs, **kwargs):
        rows = real(db, pairs, **kwargs)
        me = threading.current_thread().name
        if me in arrived and not arrived[me].is_set():
            arrived[me].set()
            arrived["y" if me == "x" else "x"].wait(2)
        return rows

    monkeypatch.setattr(reward_engine, "lock_windows", lock_windows)
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    out: dict = {}

    def runner(key):
        session = factory()
        try:
            transfer_service.update_transfer(session, *plans[key])
            session.commit()
            out[key] = "committed"
        except Exception as exc:  # noqa: BLE001  (asserted below)
            session.rollback()
            out[key] = exc
        finally:
            session.close()

    threads = [threading.Thread(target=runner, args=(key,), name=key, daemon=True) for key in ("x", "y")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)
    assert not any(thread.is_alive() for thread in threads), out
    assert out == {"x": "committed", "y": "committed"}, out
    assert sorted(row.amount for row in _accruals(db_session)) == [Decimal("30.0000")] * 2  # in-leg accruals gone



# --- the rule-row tier (PR #290 re-check at c6ad92e7) ----------------------------------------------------------


def test_rule_put_while_purchase_put_holds_the_purchase_gets_retry(pg_engine, db_session):
    """Purchase first: a purchase PUT holds its row, the rule FOR SHARE and the window (not committed). A rule PUT
    then never waits: its rule lock (or source lock) is NOWAIT and it answers the retryable 409 `retry` at once, no
    40P01; the purchase commits. (Before: the rule PUT waited on the purchase row.)"""
    card = _card(db_session)
    rule = _rule(db_session, card)
    spend = _spend(db_session, card, 100, rules=[rule])
    card_id, rule_id, spend_id = card.id, rule.id, spend.id
    db_session.commit()
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    out: dict = {}

    def put_rule():
        with factory() as db:
            try:
                reward_rule_service.update(db, card_id, rule_id, RewardRuleIn(**_rule_body(rate="2")))
                db.commit()
                out["rule"] = "committed"
            except Exception as exc:  # noqa: BLE001  (asserted below)
                db.rollback()
                out["rule"] = exc

    with factory() as purchase:
        ews.update_entry(purchase, spend_id, EntryUpdateIn(account_id=card_id, kind="expense", amount=Decimal("200"),
                                                           entry_date=DAY, posted_date=DAY, reward_rule_ids=[rule_id]))
        thread = threading.Thread(target=put_rule, daemon=True)
        thread.start()
        thread.join(3)
        answered_at_once = not thread.is_alive()
        purchase.commit()
    thread.join(10)
    assert answered_at_once, "the rule PUT waited on the purchase write"
    assert isinstance(out["rule"], CodedConflictError) and out["rule"].code == RETRY, out
    row = _one(db_session)
    assert (row.estimate, db_session.get(RewardRule, rule_id).rate) == (Decimal("2.0000"), Decimal("1"))


def test_new_purchase_waits_on_rule_put_and_prices_on_the_new_rate(pg_engine, db_session):
    """Rule first: a rule PUT (1% -> 2%) holds the rule row; a NEW purchase linked to the rule (nothing the PUT could
    lock) waits on the rule row (FOR SHARE) and prices on the committed 2%: estimate 4. (Before: it priced on the old
    1% without waiting, estimate 2.)"""
    card = _card(db_session)
    rule = _rule(db_session, card)
    card_id, rule_id = card.id, rule.id
    db_session.commit()

    def put_rule(db):
        reward_rule_service.update(db, card_id, rule_id, RewardRuleIn(**_rule_body(rate="2")))

    def new_purchase(db):
        _spend(db, db.get(Account, card_id), 200, rules=[db.get(RewardRule, rule_id)])

    outcome = None
    try:
        outcome = race(pg_engine, put_rule, new_purchase)
    finally:
        row = _one(db_session)
        assert row.estimate == Decimal("4.0000")
    assert outcome == "committed"


def test_writer_and_rule_writer_lock_orders(db_session):
    """P12 (rule-row tier): an accrual writer (purchase PUT) takes its row, then the rule FOR SHARE, then the window;
    a rule writer (rule PUT) takes the rules FOR UPDATE NOWAIT first, then sources and windows, every lock NOWAIT,
    and never a FOR SHARE on a rule."""
    card = _card(db_session)
    _fillers(db_session)
    rule = _rule(db_session, card)
    spend = _spend(db_session, card, 100, rules=[rule])
    card_id, rule_id, spend_id = card.id, rule.id, spend.id
    db_session.commit()

    writer = _capture_locks(db_session, lambda: ews.update_entry(db_session, spend_id, EntryUpdateIn(
        account_id=card_id, kind="expense", amount=Decimal("200"), entry_date=DAY, posted_date=DAY,
        reward_rule_ids=[rule_id])))
    db_session.commit()
    kinds = [_lock_kind(statement) for statement, _ in writer]
    source = next(i for i, (_, ids) in enumerate(writer) if kinds[i] == "ledger_entry" and spend_id in ids)
    shared = kinds.index("reward_rule")
    assert "FOR SHARE" in writer[shared][0]
    assert source < kinds.index("account") < shared < kinds.index("reward_accrual"), kinds

    rule_writer = _capture_locks(db_session, lambda: reward_rule_service.update(
        db_session, card_id, rule_id, RewardRuleIn(**_rule_body(rate="2"))))
    db_session.commit()
    kinds = [_lock_kind(statement) for statement, _ in rule_writer]
    assert kinds[0] == "account" and "FOR KEY SHARE" in rule_writer[0][0], kinds  # card tier (re-check 81f88597)
    assert kinds[1] == "reward_rule" and "FOR UPDATE" in rule_writer[1][0], kinds
    assert all("NOWAIT" in statement for statement, _ in rule_writer), [st for st, _ in rule_writer]
    assert not any("FOR SHARE" in statement for statement, _ in rule_writer)
    source = next(i for i, (_, ids) in enumerate(rule_writer) if kinds[i] == "ledger_entry" and spend_id in ids)
    assert 0 < source < kinds.index("reward_accrual"), kinds
    assert _one(db_session).estimate == Decimal("4.0000")


# --- the card tier (PR #290 re-check at 81f88597: closing_day vs a new rule / accrual pricing) ------------------

CLOSING_25 = (date(2026, 10, 26), date(2026, 11, 25))  # the 11/20 purchase's window once closing_day is 25


def _attempt(pg_engine, fn, wait: float = 3.0) -> dict:
    """fn(session) in its own transaction on another connection, committed; waits `wait` s. Returns
    {"done": answered within `wait`, "out": "committed" | the exception | None, "thread": ...}."""
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    result: dict = {"out": None}

    def run():
        with factory() as db:
            try:
                fn(db)
                db.commit()
                result["out"] = "committed"
            except Exception as exc:  # noqa: BLE001  (asserted by the caller)
                db.rollback()
                result["out"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(wait)
    result.update(done=not thread.is_alive(), thread=thread)
    return result


def _is_retry(out) -> bool:
    return isinstance(out, CodedConflictError) and out.code == RETRY


def _new_rule(card_id, **fields):
    def create(db):
        reward_rule_service.create(db, card_id, RewardRuleIn(**_rule_body(name="測試新規則", **fields)))
    return create


def _new_purchase(card_id, amount=1000):
    def buy(db):
        rule = db.scalar(select(RewardRule).where(RewardRule.account_id == card_id, RewardRule.name == "測試新規則"))
        _spend(db, db.get(Account, card_id), amount, rules=[rule])
    return buy


def _manual_post(card_id):
    def post(db):
        rule_id = db.scalar(select(RewardRule.id).where(RewardRule.account_id == card_id))
        accrual_id = db.scalar(select(RewardAccrual.id).where(RewardAccrual.rule_id == rule_id))
        reward_posting.post_manual(db, accrual_id, date(2026, 12, 1))
    return post


def _phantom_interleave(pg_engine, db_session, writer_card_id, body, rule_card_id, steps):
    """dev-astra's 81f88597 interleave: a card write C (closing_day / parent) is flushed but not committed while a
    NEW rule N (no shared cap) is created on `rule_card_id` and then each of `steps` (a purchase, a manual post) runs,
    each in its own transaction while C is open. The card tier makes N answer 409 `retry` at once (or a step wait
    for C); every step refused or waiting is run again after C commits. Returns the first attempt's outcomes."""
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    seen: list = []
    with factory() as card_write:
        settings_service.update_account(card_write, writer_card_id, AccountIn(**body))
        card_write.flush()
        for fn in [_new_rule(rule_card_id, **({"posting": "manual"} if len(steps) > 1 else {})), *steps]:
            attempt = _attempt(pg_engine, fn)
            seen.append(attempt)
            if attempt["out"] != "committed":
                break
        card_write.commit()
    for attempt in seen:
        attempt["thread"].join(10)
    redo = [_new_rule(rule_card_id, **({"posting": "manual"} if len(steps) > 1 else {})), *steps][
        sum(1 for a in seen if a["out"] == "committed"):]
    for fn in redo:
        again = _attempt(pg_engine, fn, wait=10)
        assert again["out"] == "committed", again
    return seen


def _assert_card_tier_held(seen):
    """N answered 409 `retry` at once, or the step after it waited on C (never priced on C's uncommitted card)."""
    first = seen[0]
    assert first["done"], "the rule POST waited on the card write"
    assert _is_retry(first["out"]) or any(not a["done"] for a in seen[1:]), [a["out"] for a in seen]


def test_closing_day_change_then_new_rule_then_purchase_prices_on_the_new_closing_day(pg_engine, db_session):
    """81f88597 P1: C changes closing_day 15 -> 25 on a card with no rules (flushed, not committed); N creates a rule
    on it; P buys on 11/20 under that rule. With the card tier N gets 409 `retry` at once (C holds the card FOR
    UPDATE) and P is priced after C commits: window 10/26-11/25. (Before: N and P committed while C was open and P
    was priced on closing_day 15, window 11/16-12/15, which C's finished recompute never revisited.)"""
    card = _card(db_session)
    card_id, body = card.id, _account_body(card, closing_day=25)
    db_session.commit()
    seen = _phantom_interleave(pg_engine, db_session, card_id, body, card_id, [_new_purchase(card_id)])
    row = _one(db_session)
    assert (row.window_start, row.window_end) == CLOSING_25
    _assert_card_tier_held(seen)


def test_closing_day_change_then_new_manual_rule_then_manual_post_freezes_the_new_window(pg_engine, db_session):
    """The same interleave with a manual post after the purchase: the posted (frozen) accrual is in the closing_day
    25 window. (Before: the post ran while C was open and froze the 11/16-12/15 window for good.)"""
    card = _card(db_session)
    card_id, body = card.id, _account_body(card, closing_day=25)
    db_session.commit()
    seen = _phantom_interleave(pg_engine, db_session, card_id, body, card_id,
                               [_new_purchase(card_id), _manual_post(card_id)])
    row = _one(db_session)
    assert (row.state, row.window_start, row.window_end) == ("posted", *CLOSING_25)
    _assert_card_tier_held(seen)


def test_new_rule_on_a_child_card_during_parent_closing_day_change(pg_engine, db_session):
    """Contract 2 (re-check 81f88597): the parent's closing_day 15 -> 25 is in flight; a NEW rule on its combined
    child (no own closing_day) and a purchase under it. The rule writer takes the child and its effective parent FOR
    KEY SHARE NOWAIT: 409 `retry` at once; the purchase is then priced on the parent's 25. (Before: priced on 15.)"""
    parent = _card(db_session, "測試主卡", closing_day=15)
    child = _card(db_session, "測試附卡", closing_day=None, combined_account_id=parent.id)
    parent_id, child_id, body = parent.id, child.id, _account_body(parent, closing_day=25)
    db_session.commit()
    seen = _phantom_interleave(pg_engine, db_session, parent_id, body, child_id, [_new_purchase(child_id)])
    row = _one(db_session)
    assert (row.window_start, row.window_end) == CLOSING_25
    _assert_card_tier_held(seen)


def _held_purchase(pg_engine, card_id, rule_id, spend_id):
    """A purchase PUT on the card (100 -> 200) that holds its locks (not committed): the session, to commit."""
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    purchase = factory()
    ews.update_entry(purchase, spend_id, EntryUpdateIn(account_id=card_id, kind="expense", amount=Decimal("200"),
                                                       entry_date=DAY, posted_date=DAY, reward_rule_ids=[rule_id]))
    return purchase


def test_closing_day_change_while_a_purchase_holds_its_card_gets_retry_at_the_card_tier(pg_engine, db_session):
    """A purchase PUT holds its row, the card FOR KEY SHARE, the rule FOR SHARE and the window; a closing_day change
    of that card answers 409 `retry` at once, refused by its FIRST engine lock (the card rows FOR UPDATE NOWAIT),
    never a wait. (Before: no card lock; the refusal came later, from the rule row.) After the purchase commits the
    retried change re-buckets it."""
    card = _card(db_session)
    rule = _rule(db_session, card)
    spend = _spend(db_session, card, 100, rules=[rule])
    card_id, rule_id, spend_id, body = card.id, rule.id, spend.id, _account_body(card, closing_day=25)
    db_session.commit()
    refused_at: list[str] = []

    def change(db):
        def capture(conn, cursor, statement, parameters, context, executemany):
            if "NOWAIT" in statement:
                refused_at.append(_lock_kind(statement) + (" FOR UPDATE" if "FOR UPDATE" in statement else ""))
        bind = db.connection().engine
        event.listen(bind, "before_cursor_execute", capture)
        try:
            settings_service.update_account(db, card_id, AccountIn(**body))
        finally:
            event.remove(bind, "before_cursor_execute", capture)

    purchase = _held_purchase(pg_engine, card_id, rule_id, spend_id)
    try:
        attempt = _attempt(pg_engine, change)
    finally:
        purchase.commit()
        purchase.close()
    attempt["thread"].join(10)
    assert attempt["done"], "the closing_day change waited on the purchase"
    assert _is_retry(attempt["out"]), attempt["out"]
    assert refused_at and refused_at[-1] == "account FOR UPDATE", refused_at  # the card tier refused it
    assert _attempt(pg_engine, lambda db: settings_service.update_account(db, card_id, AccountIn(**body)),
                    wait=10)["out"] == "committed"
    row = _one(db_session)
    assert (row.window_start, row.window_end, row.estimate) == (*CLOSING_25, Decimal("2.0000"))


def test_purchase_in_flight_on_a_parent_while_another_card_is_re_parented_onto_it(pg_engine, db_session):
    """Contract 1 / tests (re-check 81f88597): a purchase PUT on card A holds A FOR KEY SHARE; card X is re-parented
    onto A. A re-parent is a card write on the new parent too (A's set of children is stable only under A's lock),
    so it answers 409 `retry` at once; once the purchase commits it succeeds and X's accruals take A's closing_day.
    (Before: the re-parent took no lock on A.)"""
    a = _card(db_session, "測試主卡", closing_day=25)
    x = _card(db_session, "測試附卡", closing_day=None)
    rule_a, rule_x = _rule(db_session, a), _rule(db_session, x, name="測試附卡規則")
    spend_a = _spend(db_session, a, 100, rules=[rule_a])
    _spend(db_session, x, 1000, rules=[rule_x])
    a_id, x_id, rule_a_id, spend_a_id = a.id, x.id, rule_a.id, spend_a.id
    body = _account_body(x, combined_account_id=a.id)
    db_session.commit()
    assert (_one(db_session, rule_id=rule_x.id).window_start, _one(db_session, rule_id=rule_x.id).window_end) == (
        date(2026, 11, 1), date(2026, 11, 30))  # calendar month: no closing_day anywhere

    purchase = _held_purchase(pg_engine, a_id, rule_a_id, spend_a_id)
    try:
        attempt = _attempt(pg_engine, lambda db: settings_service.update_account(db, x_id, AccountIn(**body)))
    finally:
        purchase.commit()
        purchase.close()
    attempt["thread"].join(10)
    assert attempt["done"] and _is_retry(attempt["out"]), attempt["out"]
    again = _attempt(pg_engine, lambda db: settings_service.update_account(db, x_id, AccountIn(**body)), wait=10)
    assert again["out"] == "committed", again
    row = _one(db_session, rule_id=rule_x.id)
    assert (row.window_start, row.window_end) == CLOSING_25


def test_ordinary_account_writes_beside_a_purchase_in_flight(pg_engine, db_session):
    """Contract 4: ordinary writes of the account row while a purchase PUT holds the card FOR KEY SHARE.
    - a note edit (NO KEY UPDATE: no unique column changes) neither waits nor gets 409;
    - a rename (PostgreSQL takes FOR UPDATE for an UPDATE of a unique column, `account_name_key`) waits for the
      purchase, as it already did for any FK check of a new row on the card, then commits;
    - a balance adjustment (its account row FOR UPDATE, no accrual) waits for the purchase, then commits.
    No deadlock and no 409 on any of them."""
    card = _card(db_session)
    rule = _rule(db_session, card)
    spend = _spend(db_session, card, 100, rules=[rule])
    card_id, rule_id, spend_id = card.id, rule.id, spend.id
    note, rename = _account_body(card, note="測試備註"), _account_body(card, name="測試卡改名", note="測試備註")
    db_session.commit()

    purchase = _held_purchase(pg_engine, card_id, rule_id, spend_id)
    try:
        noted = _attempt(pg_engine, lambda db: settings_service.update_account(db, card_id, AccountIn(**note)))
        renamed = _attempt(pg_engine, lambda db: settings_service.update_account(db, card_id, AccountIn(**rename)),
                           wait=0.5)
        rename_waited = not renamed["done"]
        renamed["thread"].join(0.1)
    finally:
        purchase.commit()
        purchase.close()
    renamed["thread"].join(10)
    assert noted["done"] and noted["out"] == "committed", noted["out"]
    assert rename_waited and renamed["out"] == "committed", renamed["out"]

    purchase = _held_purchase(pg_engine, card_id, rule_id, spend_id)
    try:
        adjusted = _attempt(pg_engine, lambda db: ews.create_balance_adjustment(db, BalanceAdjustmentIn(
            account_id=card_id, target_balance=Decimal("-500"), entry_date=DAY)), wait=0.5)
        adjust_waited = not adjusted["done"]
    finally:
        purchase.commit()
        purchase.close()
    adjusted["thread"].join(10)
    assert adjust_waited and adjusted["out"] == "committed", adjusted["out"]
    assert _one(db_session).estimate == Decimal("2.0000")


def test_card_and_card_writer_lock_orders(db_session):
    """P12 card tier, listener: an accrual writer (purchase PUT) takes its row (tier 1), then the card FOR KEY SHARE,
    then the rule FOR SHARE, then the window. A card writer (closing_day PUT) takes its own account row (FOR NO KEY
    UPDATE, before anything else), then the card rows FOR UPDATE NOWAIT before any rule, statement, source or window
    lock (all NOWAIT), holds no ledger row before its card lock, and never takes a card FOR KEY SHARE before it."""
    card = _card(db_session)
    _fillers(db_session)
    rule = _rule(db_session, card)
    spend = _spend(db_session, card, 100, rules=[rule])
    card_id, rule_id, spend_id = card.id, rule.id, spend.id
    body = _account_body(card, closing_day=25)
    db_session.commit()

    writer = _capture_locks(db_session, lambda: ews.update_entry(db_session, spend_id, EntryUpdateIn(
        account_id=card_id, kind="expense", amount=Decimal("200"), entry_date=DAY, posted_date=DAY,
        reward_rule_ids=[rule_id])))
    db_session.commit()
    kinds = [_lock_kind(statement) for statement, _ in writer]
    source = next(i for i, (_, ids) in enumerate(writer) if kinds[i] == "ledger_entry" and spend_id in ids)
    card_lock = next(i for i, (st, ids) in enumerate(writer) if kinds[i] == "account" and card_id in ids)
    assert "FOR KEY SHARE" in writer[card_lock][0]
    assert source < card_lock < kinds.index("reward_rule") < kinds.index("reward_accrual"), kinds

    card_writer = _capture_locks(db_session, lambda: settings_service.update_account(
        db_session, card_id, AccountIn(**body)))
    db_session.commit()
    kinds = [_lock_kind(statement) for statement, _ in card_writer]
    assert kinds[0] == "account" and "FOR NO KEY UPDATE" in card_writer[0][0], kinds
    card_lock = next(i for i, (st, _) in enumerate(card_writer)
                     if kinds[i] == "account" and re.search(r"FOR UPDATE NOWAIT", st))
    engine = [i for i, kind in enumerate(kinds) if kind in ("reward_rule", "account_statement", "ledger_entry",
                                                            "reward_accrual")]
    assert engine and card_lock < min(engine), kinds
    assert all("NOWAIT" in card_writer[i][0] for i in engine), [card_writer[i][0] for i in engine]
    assert not any("KEY SHARE" in card_writer[i][0] for i in range(card_lock)), kinds
    row = _one(db_session)
    assert (row.window_start, row.window_end) == CLOSING_25


def test_card_share_then_card_write_in_one_transaction_fails_closed(db_session):
    """P12 no-upgrade guard on card rows: FOR KEY SHARE (an accrual path) then FOR UPDATE (a card write) in one
    transaction raises RuntimeError instead of risking an upgrade deadlock."""
    card = _card(db_session)
    rule = _rule(db_session, card)
    db_session.commit()
    reward_engine.share_rules(db_session, [rule.id])
    with pytest.raises(RuntimeError, match="never upgraded"):
        reward_engine.lock_cards_for_write(db_session, [card.id])
    db_session.rollback()



def test_deleting_a_parent_rebuckets_its_combined_children(client, db_session):
    """Contract 1 (re-check 81f88597): deleting a parent clears its children's combined_account_id, so a child
    without its own closing_day moves from the parent's closing_day 15 to the calendar month: a card write (the
    parent and children FOR UPDATE NOWAIT, the child's rules FOR UPDATE NOWAIT) that re-buckets the child's estimates.
    (Before: the delete left the accrual in the 11/16-12/15 window.)"""
    parent = _card(db_session, "測試主卡", closing_day=15)
    child = _card(db_session, "測試附卡", closing_day=None, combined_account_id=parent.id)
    rule = _rule(db_session, child)
    _spend(db_session, child, 1000, rules=[rule])
    parent_id = parent.id
    db_session.commit()
    assert (_one(db_session).window_start, _one(db_session).window_end) == NOV_WINDOW

    response = client.delete(f"/accounts/{parent_id}")
    assert response.status_code == 204, response.text
    row = _one(db_session)
    assert (row.window_start, row.window_end) == (date(2026, 11, 1), date(2026, 11, 30))


HELD_TABLE = {"child": "ledger_entry", "refund": "ledger_entry", "window": "reward_accrual",
              "reward_row": "ledger_entry", "settler": "ledger_entry", "einvoice": "einvoice",
              "doomed_reward_row": "reward_accrual"}


@pytest.mark.parametrize("held", sorted(HELD_TABLE))
def test_lock_cascade_nowait_never_waits(pg_engine, db_session, held):
    """#296 M10: `entry_write_service.lock_cascade_nowait` takes every row a delete inside reconcile locks or writes,
    NOWAIT (AGENT-425: the plain UPDATEs and FK actions of the delete must find their rows held). A held fee child,
    refund, accrual window, frozen accrual's reward row (flagged `reward_posted`), settling row (link cleared),
    linked e-invoice (the unlink trigger UPDATEs it) or the accrual of a doomed reward row (its `ledger_entry_id`
    goes NULL) raises LockNotAvailable at once (the caller's busy code), never a wait."""
    card = _card(db_session)
    rule = _rule(db_session, card)
    spend = _spend(db_session, card, 100, rules=[rule])
    child = make_entry(db_session, card, "-5", kind="fee", entry_date=DAY, parent_entry_id=spend.id)
    refund = make_entry(db_session, card, "20", kind="refund", entry_date=DAY, refunds_entry_id=spend.id)
    settler = make_entry(db_session, card, "30", kind="income", entry_date=DAY, settles_entry_id=spend.id)
    accrual = _one(db_session, source_entry_id=spend.id)
    reward = _post(db_session, accrual) if held in ("reward_row", "doomed_reward_row") else None
    invoice = Einvoice(number="AB00000001", issue_date=DAY, period="11511", seller_tax_id="00000001",
                       seller_name="測試商店", total_amount=Decimal("100"), status_raw="開立", source_drive_id="fake")
    db_session.add(invoice)
    db_session.flush()
    db_session.add(EinvoiceLink(invoice_id=invoice.id, entry_id=spend.id, linked_by="owner"))
    db_session.flush()
    target = {"child": child.id, "refund": refund.id, "window": accrual.id, "settler": settler.id,
              "reward_row": reward and reward.id, "einvoice": invoice.id, "doomed_reward_row": accrual.id}[held]
    doomed = [reward.id] if held == "doomed_reward_row" else [spend.id]
    window = (rule.id, accrual.window_start)
    db_session.commit()
    holder = pg_engine.connect()
    try:
        holder.begin()
        holder.execute(text(f"SELECT id FROM {HELD_TABLE[held]} WHERE id = :id FOR UPDATE"), {"id": target})
        db_session.execute(text("SET lock_timeout = '3s'"))
        with pytest.raises(OperationalError) as caught:
            ews.lock_cascade_nowait(db_session, doomed)
        assert isinstance(caught.value.orig, LockNotAvailable)
        assert "lock timeout" not in str(caught.value.orig)  # NOWAIT, not a wait cut short
        db_session.rollback()
    finally:
        holder.rollback()
        holder.close()
        db_session.execute(text("SET lock_timeout = 0"))
    ews.lock_cascade_nowait(db_session, doomed)  # free again: takes everything
    assert window in reward_engine.held_windows(db_session)
    db_session.rollback()
