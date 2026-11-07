"""PUT /entries/{id}/reward-rules (issue #293 option A): change an entry's reward rule links without rewriting the
row or its fee / discount children, so their statement coverage survives. Same rule checks as PUT /entries, same
edit lock and kind guards; the reward engine re-syncs the entry's accruals. Synthetic data only."""
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models import EntryRewardRule, LedgerEntry, RewardAccrual
from app.services.reconciliation_actions import engine
from tests.integration.statement_fixtures import _active, _card, _cases, _line, _submit, run  # noqa: F401

DAY = date(2026, 9, 3)


def _put(client, entry_id, rule_ids):
    return client.put(f"/entries/{entry_id}/reward-rules", json={"reward_rule_ids": rule_ids})


def _accruals(db, entry_id):
    db.expire_all()
    return list(db.scalars(select(RewardAccrual).where(RewardAccrual.source_entry_id == entry_id)
                           .order_by(RewardAccrual.id)))


def _links(db, entry_id):
    db.expire_all()
    return set(db.scalars(select(EntryRewardRule.rule_id).where(EntryRewardRule.entry_id == entry_id)))


def _error(response):
    return [(error["loc"][-1], error["msg"]) for error in response.json()["detail"]]


def test_link_only_keeps_the_fee_child_and_its_statement_coverage(client, seed, db_session, run):
    """The #293 case: a purchase with a fee child, both claimed by a statement. PUT /entries would delete and
    recreate the child and orphan its coverage; this endpoint leaves both rows and every claim alone."""
    card = _card(seed, db_session)
    parent = seed.entry(card, "-1200", day=DAY, merchant="海外店")
    child = seed.entry(card, "-30", kind="fee", day=DAY, parent_entry_id=parent.id)
    rule = seed.rule(card)
    db_session.commit()
    stmt, (purchase, fee_line), _ = _submit(db_session, run, card,
                                            [_line(1, DAY, "海外店", 1200), _line(2, DAY, "", 30)])
    (case,) = [c for c in _cases(db_session, stmt.id, "open")
               if c.kind == "line_unmatched" and c.line_id == fee_line.id]
    engine.apply_action(db_session, case_id=case.id, action="match",
                        params={"line_id": fee_line.id, "entry_id": child.id}, actor="owner", label="owner-test")
    db_session.commit()
    claims_before = {(c.id, c.line_id, c.entry_id) for c in _active(db_session, stmt.id)}
    assert {(line, entry) for _, line, entry in claims_before} == {(purchase.id, parent.id), (fee_line.id, child.id)}
    parent_id, child_id = parent.id, child.id

    response = _put(client, parent_id, [rule.id])

    assert response.status_code == 200, response.text
    assert [r["id"] for r in response.json()["rules"]] == [rule.id]
    db_session.expire_all()
    children = list(db_session.scalars(select(LedgerEntry).where(LedgerEntry.parent_entry_id == parent_id)))
    assert [(c.id, c.amount) for c in children] == [(child_id, Decimal("-30.0000"))]
    assert {(c.id, c.line_id, c.entry_id) for c in _active(db_session, stmt.id)} == claims_before
    (accrual,) = _accruals(db_session, parent_id)
    assert (accrual.rule_id, accrual.state) == (rule.id, "estimated")


def test_relink_moves_the_accruals_and_empty_clears_them(client, seed, db_session):
    card = seed.account("測試卡", is_credit=True, closing_day=15)
    old, new = seed.rule(card, "舊規則"), seed.rule(card, "新規則")
    db_session.commit()
    created = client.post("/entries", json={"account_id": card.id, "kind": "expense", "amount": "1000",
                                            "entry_date": DAY.isoformat(), "reward_rule_ids": [old.id]})
    entry_id = created.json()["id"]
    assert [a.rule_id for a in _accruals(db_session, entry_id)] == [old.id]

    moved = _put(client, entry_id, [new.id, new.id])

    assert moved.status_code == 200, moved.text
    assert _links(db_session, entry_id) == {new.id}
    assert [(a.rule_id, a.state, a.amount) for a in _accruals(db_session, entry_id)] == [
        (new.id, "estimated", Decimal("10.0000"))]

    cleared = _put(client, entry_id, [])

    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["rules"] == []
    assert _links(db_session, entry_id) == set() and _accruals(db_session, entry_id) == []


def test_the_row_and_its_source_stay_as_they_were(client, seed, db_session):
    """PUT /entries makes an edited entry `manual`; a link-only change is not an edit of the row."""
    card = seed.account("測試卡", is_credit=True, closing_day=15)
    rule = seed.rule(card)
    entry = seed.entry(card, "-500", day=DAY, source="statement", merchant="測試店", description="原本")
    db_session.commit()
    entry_id = entry.id

    response = _put(client, entry_id, [rule.id])

    assert response.status_code == 200, response.text
    db_session.expire_all()
    stored = db_session.get(LedgerEntry, entry_id)
    assert (stored.source, stored.amount, stored.merchant, stored.description, stored.needs_review) == (
        "statement", Decimal("-500.0000"), "測試店", "原本", False)


def test_unlinking_a_posted_accrual_flags_the_entry(client, seed, db_session):
    """Same review flag as PUT /entries: a frozen accrual whose rule link changed is the owner's to settle."""
    card = seed.account("測試卡", is_credit=True, closing_day=15)
    rule = seed.rule(card)
    db_session.commit()
    entry_id = client.post("/entries", json={"account_id": card.id, "kind": "expense", "amount": "1000",
                                             "entry_date": DAY.isoformat(), "reward_rule_ids": [rule.id]}).json()["id"]
    (accrual,) = _accruals(db_session, entry_id)
    reward = seed.entry(card, "10", kind="reward", day=date(2026, 10, 5), source="rule",
                        reward_source_entry_id=entry_id, reward_rule_id=rule.id)
    accrual.state, accrual.ledger_entry_id = "posted", reward.id
    db_session.commit()

    response = _put(client, entry_id, [])

    assert response.status_code == 200, response.text
    assert (response.json()["needs_review"], response.json()["review_reason"]) == (True, "reward_posted")
    assert [(a.state, a.amount) for a in _accruals(db_session, entry_id)] == [("posted", Decimal("10.0000"))]


def test_rules_are_checked_like_put_entries(client, seed, db_session):
    card, other = seed.account("測試卡", is_credit=True), seed.account("別張卡", is_credit=True)
    foreign = seed.rule(other)
    disabled = seed.rule(card, "停用", enabled=False)
    entry = seed.entry(card, "-100", day=DAY)
    db_session.commit()
    entry_id = entry.id

    wrong_account = _put(client, entry_id, [foreign.id])
    newly_disabled = _put(client, entry_id, [disabled.id])

    assert wrong_account.status_code == 422
    assert _error(wrong_account) == [("reward_rule_ids", f"rule {foreign.id} does not belong to account {card.id}")]
    assert newly_disabled.status_code == 422
    assert _error(newly_disabled) == [("reward_rule_ids", f"rule {disabled.id} is not enabled on {DAY.isoformat()}")]
    assert _links(db_session, entry_id) == set()

    # A link the entry already has survives after its rule was disabled.
    db_session.add(EntryRewardRule(entry_id=entry_id, rule_id=disabled.id))
    db_session.commit()
    kept = _put(client, entry_id, [disabled.id])
    assert kept.status_code == 200, kept.text
    assert _links(db_session, entry_id) == {disabled.id}


def test_refund_settlement_child_and_transfer_rows_are_refused(client, seed, db_session):
    wallet = seed.account()
    expense = seed.entry(wallet, "-1200", day=DAY)
    refund = seed.entry(wallet, "570", kind="refund", day=DAY, refunds_entry_id=expense.id)
    settlement = seed.entry(wallet, "200", kind="receivable", day=DAY, is_settlement=True)
    child = seed.entry(wallet, "-30", kind="fee", day=DAY, parent_entry_id=expense.id)
    other = seed.account("測試錢包")
    db_session.commit()
    leg_id = client.post("/transfers", json={"from_account_id": wallet.id, "to_account_id": other.id,
                                             "out_amount": "100", "entry_date": DAY.isoformat()}).json()["out_entry_id"]

    responses = {name: _put(client, row_id, []) for name, row_id in
                 (("refund", refund.id), ("settlement", settlement.id), ("child", child.id), ("leg", leg_id))}

    assert {name: r.status_code for name, r in responses.items()} == {
        "refund": 422, "settlement": 422, "child": 422, "leg": 422}
    assert _error(responses["refund"]) == [("kind", "refund entries are edited by deleting and re-refunding")]
    assert _error(responses["settlement"]) == [("kind", "settlement entries are edited by deleting and re-settling")]
    assert _error(responses["child"]) == [("kind", "fee entries are not edited through this endpoint")]
    assert _error(responses["leg"]) == [("kind", "transfer_out entries are not edited through this endpoint")]


def test_unknown_entry_is_404_and_imported_entry_is_locked(client, seed, db_session):
    wallet = seed.account()
    imported = seed.entry(wallet, "-100", day=DAY, source="moze_backup", moze_id="rec-1")
    db_session.commit()

    missing = _put(client, 999999, [])
    locked = _put(client, imported.id, [])

    assert missing.status_code == 404
    assert (locked.status_code, locked.json()["message"]) == (409, "locked_until_cutover")


def test_body_requires_the_rule_list(client, seed, db_session):
    wallet = seed.account()
    entry = seed.entry(wallet, "-100", day=DAY)
    db_session.commit()

    response = client.put(f"/entries/{entry.id}/reward-rules", json={})

    assert response.status_code == 422
