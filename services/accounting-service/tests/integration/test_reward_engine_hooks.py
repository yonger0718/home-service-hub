"""Reward engine hooks in the write paths: entries, transfers, refunds, splits, rules, accounts, deletes and the
review flag (spec 4.1, 5.1, 5.3, 5.4; plan P1, P6, P10, P11, P12)."""

import re
from datetime import date
from decimal import Decimal

from sqlalchemy import event, select

from app.models import Account, EntryGroup, LedgerEntry, Project, RewardAccrual, RewardRule
from app.models.statements import AccountStatement, ReconciliationCase, StatementRevision
from app.services import entry_write_service as ews
from app.services import reward_engine
from app.services.entry_write_service import write_rule_links
from tests.helpers import make_account, make_definition, make_entry, make_instance, schedule_line

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
    match = re.search(r"\bFROM (\w+)", statement)
    return match.group(1) if match else statement


def test_engine_reward_delete_lock_order(db_session):
    """P12: schedule rows → entry_group → the accrual window (ONE statement, by id) → ledger rows; no accrual lock
    after a ledger lock."""
    card = _card(db_session)
    rule = _rule(db_session, card, total_cap=Decimal("15"))
    first = _spend(db_session, card, 1000, date(2026, 11, 18), [rule])
    _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    group = EntryGroup(kind="reward_claim")
    db_session.add(group)
    db_session.flush()
    reward = _post(db_session, _one(db_session, source_entry_id=first.id))
    reward.group_id = group.id
    definition = make_definition(db_session, [schedule_line("income", card, "10")])
    make_instance(db_session, definition, 1, date(2027, 1, 5), status="posted", entries=[reward])
    reward_id = reward.id
    db_session.commit()

    statements: list[str] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if _LOCK.search(statement):
            statements.append(statement)

    bind = db_session.connection().engine
    event.listen(bind, "before_cursor_execute", capture)
    try:
        ews.delete_entry(db_session, reward_id)
    finally:
        event.remove(bind, "before_cursor_execute", capture)
    db_session.commit()

    kinds = [_lock_kind(s) for s in statements]
    assert kinds.count("reward_accrual") == 1, kinds
    accrual_at = kinds.index("reward_accrual")
    assert "ORDER BY reward_accrual.id" in statements[accrual_at]
    schedule_at = [i for i, k in enumerate(kinds) if k.startswith("schedule_")]
    group_at = [i for i, k in enumerate(kinds) if k == "entry_group"]
    ledger_at = [i for i, k in enumerate(kinds) if k == "ledger_entry"]
    assert schedule_at and group_at and ledger_at, kinds
    assert max(schedule_at) < min(group_at) < accrual_at < min(ledger_at), kinds
    assert _one(db_session, ledger_entry_id=None, source_entry_id=first.id).state == "forfeited"


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
