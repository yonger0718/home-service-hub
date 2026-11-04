"""Reward engine hooks in the write paths: entries, transfers, refunds, splits, rules, accounts, deletes and the
review flag (spec 4.1, 5.1, 5.3, 5.4; plan P1, P6, P10, P11, P12)."""

import re
import threading
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import sessionmaker

from app.models import Account, EntryGroup, LedgerEntry, Project, RewardAccrual, RewardRule
from app.models.statements import AccountStatement, ReconciliationCase, StatementRevision
from app.schemas.writes import AccountIn, EntryUpdateIn, RefundIn, RewardRuleIn, SplitIn, TransferIn
from app.services import entry_write_service as ews
from app.services import (
    reward_engine, reward_rule_service, settings_service, settlement_service, split_service, transfer_service,
)
from app.services.entry_write_service import write_rule_links
from app.services.errors import RETRY, CodedConflictError
from tests.helpers import (
    make_account, make_definition, make_entry, make_instance, race, schedule_line, wait_until_blocked,
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


_LOCK = re.compile(r"FOR (UPDATE|SHARE|NO KEY UPDATE)")


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


@pytest.mark.parametrize("purchase_first", [True, False])
def test_rule_put_and_purchase_put_race_price_on_committed_values(pg_engine, db_session, purchase_first):
    """Finding 1: a purchase PUT (100 -> 200) and a rule PUT (1% -> 2%) on one accrual. Whichever runs second waits
    on the purchase row and then prices on the other's committed values: 200 x 2% = 4, never 2 (100 x 2% or
    200 x 1%)."""
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

    first, second = (put_purchase, put_rule) if purchase_first else (put_rule, put_purchase)
    assert race(pg_engine, first, second) == "committed"
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


def test_rule_put_source_set_growth_fails_closed_instead_of_deadlocking(pg_engine, db_session, monkeypatch):
    """PR #290 re-check P1-B: rule PUT R locks its sources (purchase `high`) in its first batch. Meanwhile purchase
    `low` (a lower id) gets linked to the rule and commits, and writer W locks `low` and then waits on `high` (two
    rows in id order, as a split over both takes them). R's second batch finds `low`: waiting on it would close the
    cycle (40P01); it is locked NOWAIT and R fails with the retryable 409 `retry` instead, and W commits."""
    card = _card(db_session)
    rule = _rule(db_session, card)
    low = make_entry(db_session, card, "-100", entry_date=DAY)  # lower id, not linked yet
    high = _spend(db_session, card, 300, rules=[rule])
    card_id, rule_id, low_id, high_id = card.id, rule.id, low.id, high.id
    payload = RewardRuleIn(**_rule_body(rate="2"))
    db_session.commit()

    paused, go = threading.Event(), threading.Event()
    real = reward_engine._estimated_of_rules
    calls = {"n": 0}

    def estimated_of_rules(db, rule_ids):
        if threading.current_thread().name == "r":
            calls["n"] += 1
            if calls["n"] == 2:  # R holds its first batch (`high`)
                paused.set()
                assert go.wait(15)
        return real(db, rule_ids)

    monkeypatch.setattr(reward_engine, "_estimated_of_rules", estimated_of_rules)
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    sessions = {"r": factory(), "w": factory()}
    # the transactions stay open so each session keeps this connection (and pid) for its function
    pids = {key: session.execute(text("SELECT pg_backend_pid()")).scalar_one() for key, session in sessions.items()}
    out: dict = {}

    def rule_put(db):
        reward_rule_service.update(db, card_id, rule_id, payload)

    def writer(db):
        for entry_id in (low_id, high_id):
            db.execute(select(LedgerEntry.id).where(LedgerEntry.id == entry_id).with_for_update())

    def runner(key, fn):
        session = sessions[key]
        try:
            fn(session)
            session.commit()
            out[key] = "committed"
        except Exception as exc:  # noqa: BLE001  (asserted below)
            session.rollback()
            out[key] = exc

    r = threading.Thread(target=runner, args=("r", rule_put), name="r", daemon=True)
    w = threading.Thread(target=runner, args=("w", writer), name="w", daemon=True)
    r.start()
    try:
        assert paused.wait(10)
        with factory() as link:  # the source set grows: `low` joins the rule and commits
            write_rule_links(link, low_id, [rule_id])
            reward_engine.sync_entry(link, low_id)
            link.commit()
        w.start()
        wait_until_blocked(pg_engine, pids["w"], blocker_pid=pids["r"])  # W holds `low`, waits on `high`
    finally:
        go.set()
        for thread in (r, w):
            thread.join(20)
        for session in sessions.values():
            session.close()
    assert not r.is_alive() and not w.is_alive(), out
    assert out["w"] == "committed", out
    assert isinstance(out["r"], CodedConflictError) and out["r"].code == RETRY, out
    assert _one(db_session, source_entry_id=high_id).estimate == Decimal("3.0000")  # R rolled back: still 1%
