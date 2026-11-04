"""Reward engine: accrual sync and window recompute (spec 4.1, 4.4, 5.1-5.3; plan P2, P3, P12)."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import event, select

from app.models import RewardAccrual, RewardRule
from app.services import reward_engine
from app.services.entry_write_service import write_rule_links
from tests.helpers import make_account, make_entry

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


def _spend(db, card, amount, day, rules=(), **columns):
    entry = make_entry(db, card, f"-{amount}", entry_date=day, **columns)
    write_rule_links(db, entry.id, [r.id for r in rules])
    return entry


def _accruals(db, **where):
    query = select(RewardAccrual).order_by(RewardAccrual.id)
    for key, value in where.items():
        query = query.where(getattr(RewardAccrual, key) == value)
    return list(db.scalars(query.execution_options(populate_existing=True)))


def _one(db, **where):
    rows = _accruals(db, **where)
    assert len(rows) == 1, rows
    return rows[0]


def test_linked_purchase_creates_accrual(db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    reward_engine.sync_entry(db_session, spend.id)
    row = _one(db_session)
    assert (row.source_entry_id, row.rule_id, row.state) == (spend.id, rule.id, "estimated")
    assert (row.window_start, row.window_end) == NOV_WINDOW
    assert row.estimate == Decimal("10.0000") and row.amount == Decimal("10.0000")
    assert row.post_on == date(2027, 1, 5)


def test_unlinked_purchase_no_accrual(db_session):
    card = _card(db_session)
    _rule(db_session, card)
    spend = _spend(db_session, card, 1000, date(2026, 11, 20))
    reward_engine.sync_entry(db_session, spend.id)
    assert _accruals(db_session) == []


def test_placeholder_rule_no_accrual(db_session):
    card = _card(db_session)
    rule = _rule(db_session, card, method="fixed", rate=None, fixed_amount=Decimal("0"))
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    reward_engine.sync_entry(db_session, spend.id)
    assert _accruals(db_session) == []


def test_moze_reward_row_blocks_accrual(db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    make_entry(db_session, card, "10", kind="reward", entry_date=date(2026, 12, 20), source="moze_import",
               reward_source_entry_id=spend.id, reward_rule_id=rule.id)
    reward_engine.sync_entry(db_session, spend.id)
    assert _accruals(db_session) == []


def test_cap_shared_across_window(db_session):
    card = _card(db_session)
    rule = _rule(db_session, card, total_cap=Decimal("15"))
    late = _spend(db_session, card, 1000, date(2026, 11, 25), [rule])
    early = _spend(db_session, card, 1000, date(2026, 11, 18), [rule])
    reward_engine.sync_entry(db_session, late.id)
    reward_engine.sync_entry(db_session, early.id)
    # Allotment follows entry_date, not id or sync order.
    assert _one(db_session, source_entry_id=early.id).amount == Decimal("10.0000")
    assert _one(db_session, source_entry_id=late.id).amount == Decimal("5.0000")


def test_moze_rows_count_as_used(db_session):
    card = _card(db_session)
    rule = _rule(db_session, card, total_cap=Decimal("15"))
    earlier = _spend(db_session, card, 1200, date(2026, 11, 17), [rule])
    make_entry(db_session, card, "12", kind="reward", entry_date=date(2026, 12, 20), source="moze_import",
               reward_source_entry_id=earlier.id, reward_rule_id=rule.id)
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    reward_engine.sync_entry(db_session, earlier.id)
    reward_engine.sync_entry(db_session, spend.id)
    assert _one(db_session).source_entry_id == spend.id
    assert _one(db_session).amount == Decimal("3.0000")


def test_moze_rows_outside_window_not_used(db_session):
    card = _card(db_session)
    rule = _rule(db_session, card, total_cap=Decimal("15"))
    before = _spend(db_session, card, 1200, date(2026, 11, 15), [rule])  # previous cycle
    make_entry(db_session, card, "12", kind="reward", entry_date=date(2026, 12, 20), source="moze_import",
               reward_source_entry_id=before.id, reward_rule_id=rule.id)
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    reward_engine.sync_entry(db_session, spend.id)
    assert _one(db_session).amount == Decimal("10.0000")


def test_shared_cap_group_min_cap(db_session):
    card = _card(db_session)
    group = uuid.uuid4()
    rule_a = _rule(db_session, card, name="測試規則A", total_cap=Decimal("20"), shared_cap_id=group)
    rule_b = _rule(db_session, card, name="測試規則B", total_cap=Decimal("30"), shared_cap_id=group)
    spend_a = _spend(db_session, card, 1500, date(2026, 11, 18), [rule_a])
    spend_b = _spend(db_session, card, 1500, date(2026, 11, 19), [rule_b])
    reward_engine.sync_entry(db_session, spend_b.id)
    reward_engine.sync_entry(db_session, spend_a.id)
    a = _one(db_session, rule_id=rule_a.id)
    b = _one(db_session, rule_id=rule_b.id)
    assert (a.amount, b.amount) == (Decimal("15.0000"), Decimal("5.0000"))
    assert a.amount + b.amount == Decimal("20")


def test_relink_removes_estimated(db_session):
    card = _card(db_session)
    rule = _rule(db_session, card, total_cap=Decimal("15"))
    first = _spend(db_session, card, 1000, date(2026, 11, 18), [rule])
    second = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    reward_engine.sync_entry(db_session, first.id)
    reward_engine.sync_entry(db_session, second.id)
    assert _one(db_session, source_entry_id=second.id).amount == Decimal("5.0000")
    write_rule_links(db_session, first.id, [])
    reward_engine.sync_entry(db_session, first.id)
    assert _accruals(db_session, source_entry_id=first.id) == []
    assert _one(db_session, source_entry_id=second.id).amount == Decimal("10.0000")


def test_frozen_accrual_untouched_by_sync(db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    reward_engine.sync_entry(db_session, spend.id)
    row = _one(db_session)
    row.state = "posted"
    spend.amount = Decimal("-2000")
    spend.entry_date = date(2026, 12, 20)
    db_session.flush()
    reward_engine.sync_entry(db_session, spend.id)
    row = _one(db_session)
    assert (row.state, row.estimate, row.amount) == ("posted", Decimal("10.0000"), Decimal("10.0000"))
    assert (row.window_start, row.window_end) == NOV_WINDOW


def test_unflushed_frozen_state_survives_sync(db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    reward_engine.sync_entry(db_session, spend.id)
    _one(db_session).state = "posted"  # not flushed (sessions run autoflush=False)
    spend.amount = Decimal("-3000")
    spend.entry_date = date(2026, 12, 20)
    reward_engine.sync_entry(db_session, spend.id)
    row = _one(db_session)
    assert (row.state, row.estimate, row.amount) == ("posted", Decimal("10.0000"), Decimal("10.0000"))
    assert (row.window_start, row.window_end) == NOV_WINDOW


def test_unlink_keeps_frozen_accrual(db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    reward_engine.sync_entry(db_session, spend.id)
    _one(db_session).state = "posted"
    write_rule_links(db_session, spend.id, [])
    reward_engine.sync_entry(db_session, spend.id)
    assert _one(db_session).state == "posted"


def test_refund_accrual_in_purchase_window(db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    reward_engine.sync_entry(db_session, spend.id)
    refund = make_entry(db_session, card, "300", kind="refund", entry_date=date(2026, 12, 20),
                        refunds_entry_id=spend.id)
    reward_engine.sync_entry(db_session, refund.id)
    row = _one(db_session, source_entry_id=refund.id)
    assert row.rule_id == rule.id
    assert (row.window_start, row.window_end) == NOV_WINDOW
    assert row.estimate == Decimal("-3.0000") and row.amount == Decimal("-3.0000")
    assert row.post_on == date(2027, 1, 5)  # P3: post_on_for(rule, purchase window, refund date)


def test_refund_post_on_after_transaction_uses_refund_date(db_session):
    card = _card(db_session)
    rule = _rule(db_session, card, posting="after_transaction", delay_days=3)
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    refund = make_entry(db_session, card, "300", kind="refund", entry_date=date(2026, 12, 20),
                        refunds_entry_id=spend.id)
    reward_engine.sync_entry(db_session, refund.id)
    assert _one(db_session, source_entry_id=refund.id).post_on == date(2026, 12, 23)


def test_fixed_rule_partial_refund_is_proportional(db_session):
    card = _card(db_session)
    rule = _rule(db_session, card, method="fixed", rate=None, fixed_amount=Decimal("50"))
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    refund = make_entry(db_session, card, "300", kind="refund", entry_date=date(2026, 11, 22),
                        refunds_entry_id=spend.id)
    reward_engine.sync_entry(db_session, spend.id)
    assert _one(db_session, source_entry_id=spend.id).estimate == Decimal("50.0000")
    assert _one(db_session, source_entry_id=refund.id).estimate == Decimal("-15.0000")


def test_foreign_spend_uses_account_amount(db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    spend = _spend(db_session, card, 3200, date(2026, 11, 20), [rule], original_amount=Decimal("-100"),
                   original_currency="USD", fx_rate=Decimal("32"))
    reward_engine.sync_entry(db_session, spend.id)
    assert _one(db_session).estimate == Decimal("32.0000")


def test_rule_window_change_moves_estimated_only(db_session, monkeypatch):
    card = _card(db_session)
    rule = _rule(db_session, card, total_cap=Decimal("15"))
    posted = _spend(db_session, card, 1000, date(2026, 11, 18), [rule])
    estimated = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    reward_engine.sync_entry(db_session, posted.id)
    reward_engine.sync_entry(db_session, estimated.id)
    _one(db_session, source_entry_id=posted.id).state = "posted"
    db_session.flush()
    assert _one(db_session, source_entry_id=estimated.id).amount == Decimal("5.0000")
    # Every estimated accrual with a source moves with the rule. One the engine cannot re-derive (null source; it
    # sorts last) stays in the old window, capped to 0 behind the other two.
    stays = RewardAccrual(source_entry_id=None, rule_id=rule.id, window_start=NOV_WINDOW[0],
                          window_end=NOV_WINDOW[1], estimate=Decimal("10"), amount=Decimal("10"))
    db_session.add(stays)
    db_session.flush()
    reward_engine.recompute_window(db_session, rule.id, NOV_WINDOW[0])
    assert _one(db_session, id=stays.id).amount == Decimal("0.0000")

    calls = []
    real = reward_engine.recompute_window
    monkeypatch.setattr(reward_engine, "recompute_window",
                        lambda db, rule_id, start, **kw: (calls.append((rule_id, start)), real(db, rule_id, start, **kw)))
    rule.window = "calendar_month"
    db_session.flush()
    reward_engine.recompute_rules(db_session, [rule.id])
    assert (rule.id, NOV_WINDOW[0]) in calls and (rule.id, date(2026, 11, 1)) in calls
    # The old window's remainder is recomputed: with the moved accrual gone, it gets the cap left after the posted 10.
    assert _one(db_session, id=stays.id).amount == Decimal("5.0000")

    frozen = _one(db_session, source_entry_id=posted.id)
    moved = _one(db_session, source_entry_id=estimated.id)
    assert (frozen.window_start, frozen.window_end, frozen.amount) == (*NOV_WINDOW, Decimal("10.0000"))
    assert (moved.window_start, moved.window_end) == (date(2026, 11, 1), date(2026, 11, 30))
    assert moved.post_on == date(2026, 12, 5)
    # The new window holds only the moved accrual, so the posted one no longer eats its cap.
    assert moved.amount == Decimal("10.0000")


def test_rule_rate_change_reestimates(db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    reward_engine.sync_entry(db_session, spend.id)
    rule.rate = Decimal("2")
    db_session.flush()
    reward_engine.recompute_rules(db_session, [rule.id])
    row = _one(db_session)
    assert (row.estimate, row.amount) == (Decimal("20.0000"), Decimal("20.0000"))


def test_recompute_account_rebuckets_on_closing_day_change(db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    reward_engine.sync_entry(db_session, spend.id)
    card.closing_day = 25
    db_session.flush()
    reward_engine.recompute_account(db_session, card.id)
    row = _one(db_session)
    assert (row.window_start, row.window_end) == (date(2026, 10, 26), date(2026, 11, 25))


def test_combined_card_uses_parent_closing_day(db_session):
    parent = _card(db_session, "測試主卡", closing_day=15)
    child = _card(db_session, "測試附卡", closing_day=None, combined_account_id=parent.id)
    loose = _card(db_session, "測試卡", closing_day=None)
    assert reward_engine.card_closing_day(db_session, parent) == 15
    assert reward_engine.card_closing_day(db_session, child) == 15
    assert reward_engine.card_closing_day(db_session, loose) is None
    rule = _rule(db_session, child)
    spend = _spend(db_session, child, 1000, date(2026, 11, 20), [rule])
    reward_engine.sync_entry(db_session, spend.id)
    row = _one(db_session)
    assert (row.window_start, row.window_end) == NOV_WINDOW


def test_refund_follows_purchase_relink(db_session):
    card = _card(db_session)
    rule_a = _rule(db_session, card, name="測試規則A")
    rule_b = _rule(db_session, card, name="測試規則B")
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule_a])
    refund = make_entry(db_session, card, "300", kind="refund", entry_date=date(2026, 12, 20),
                        refunds_entry_id=spend.id)
    reward_engine.sync_entry(db_session, spend.id)
    assert _one(db_session, source_entry_id=refund.id).rule_id == rule_a.id

    write_rule_links(db_session, spend.id, [rule_b.id])
    reward_engine.sync_entry(db_session, spend.id)
    row = _one(db_session, source_entry_id=refund.id)
    assert row.rule_id == rule_b.id
    assert _accruals(db_session, rule_id=rule_a.id) == []

    spend.entry_date = date(2026, 12, 20)
    db_session.flush()
    reward_engine.sync_entry(db_session, spend.id)
    row = _one(db_session, source_entry_id=refund.id)
    assert (row.window_start, row.window_end) == (date(2026, 12, 16), date(2027, 1, 15))
    assert _one(db_session, source_entry_id=spend.id).window_start == date(2026, 12, 16)


def test_refund_amount_follows_purchase_amount_for_fixed(db_session):
    card = _card(db_session)
    rule = _rule(db_session, card, method="fixed", rate=None, fixed_amount=Decimal("50"))
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    refund = make_entry(db_session, card, "300", kind="refund", entry_date=date(2026, 11, 22),
                        refunds_entry_id=spend.id)
    reward_engine.sync_entry(db_session, spend.id)
    spend.amount = Decimal("-600")
    db_session.flush()
    reward_engine.sync_entry(db_session, spend.id)
    assert _one(db_session, source_entry_id=refund.id).estimate == Decimal("-25.0000")


def test_bank_confirmed_and_forfeited_count_ledger_amount(db_session):
    card = _card(db_session)
    rule = _rule(db_session, card, total_cap=Decimal("20"))
    confirmed_src = _spend(db_session, card, 1000, date(2026, 11, 17))
    forfeited_src = _spend(db_session, card, 1000, date(2026, 11, 18))
    reward_row = make_entry(db_session, card, "8", kind="reward", entry_date=date(2027, 1, 5), source="rule",
                            reward_source_entry_id=confirmed_src.id, reward_rule_id=rule.id)
    db_session.add_all([
        RewardAccrual(source_entry_id=confirmed_src.id, rule_id=rule.id, window_start=NOV_WINDOW[0],
                      window_end=NOV_WINDOW[1], estimate=Decimal("10"), amount=Decimal("10"),
                      state="bank_confirmed", ledger_entry_id=reward_row.id),
        RewardAccrual(source_entry_id=forfeited_src.id, rule_id=rule.id, window_start=NOV_WINDOW[0],
                      window_end=NOV_WINDOW[1], estimate=Decimal("10"), amount=Decimal("10"), state="forfeited"),
    ])
    db_session.flush()
    spend = _spend(db_session, card, 1500, date(2026, 11, 20), [rule])
    reward_engine.sync_entry(db_session, spend.id)
    assert _one(db_session, source_entry_id=spend.id).amount == Decimal("12.0000")


def test_orphan_frozen_accrual_counts(db_session):
    card = _card(db_session)
    rule = _rule(db_session, card, total_cap=Decimal("15"))
    db_session.add(RewardAccrual(source_entry_id=None, rule_id=rule.id, window_start=NOV_WINDOW[0],
                                 window_end=NOV_WINDOW[1], estimate=Decimal("10"), amount=Decimal("10"),
                                 state="posted"))
    db_session.flush()
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    reward_engine.sync_entry(db_session, spend.id)
    assert _one(db_session, source_entry_id=spend.id).amount == Decimal("5.0000")


def test_delete_estimated_for_returns_windows(db_session):
    card = _card(db_session)
    rule = _rule(db_session, card, total_cap=Decimal("15"))
    first = _spend(db_session, card, 1000, date(2026, 11, 18), [rule])
    second = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    reward_engine.sync_entry(db_session, first.id)
    reward_engine.sync_entry(db_session, second.id)
    pairs = reward_engine.delete_estimated_for(db_session, [first.id])
    assert pairs == {(rule.id, NOV_WINDOW[0])}
    assert _accruals(db_session, source_entry_id=first.id) == []
    for rule_id, start in pairs:
        reward_engine.recompute_window(db_session, rule_id, start)
    assert _one(db_session, source_entry_id=second.id).amount == Decimal("10.0000")


def test_delete_estimated_for_keeps_frozen(db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    reward_engine.sync_entry(db_session, spend.id)
    _one(db_session).state = "posted"
    db_session.flush()
    assert reward_engine.delete_estimated_for(db_session, [spend.id]) == set()
    assert _one(db_session).state == "posted"


def test_engine_never_commits(db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    reward_engine.sync_entry(db_session, spend.id)
    db_session.rollback()
    assert _accruals(db_session) == []


def _capture_for_update(db):
    statements: list[str] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" in statement:
            statements.append(statement)

    return statements, capture


def test_recompute_window_lock_contract(db_session):
    card = _card(db_session)
    group = uuid.uuid4()
    rule_a = _rule(db_session, card, name="測試規則A", total_cap=Decimal("20"), shared_cap_id=group)
    rule_b = _rule(db_session, card, name="測試規則B", total_cap=Decimal("20"), shared_cap_id=group)
    spend_a = _spend(db_session, card, 1000, date(2026, 11, 18), [rule_a])
    spend_b = _spend(db_session, card, 1000, date(2026, 11, 19), [rule_b])
    reward_engine.sync_entry(db_session, spend_a.id)
    reward_engine.sync_entry(db_session, spend_b.id)
    db_session.commit()  # a fresh transaction: the syncs above already hold the window

    statements, capture = _capture_for_update(db_session)
    bind = db_session.connection().engine
    event.listen(bind, "before_cursor_execute", capture)
    try:
        reward_engine.recompute_window(db_session, rule_a.id, NOV_WINDOW[0])
        assert len(statements) == 1
        assert "reward_accrual" in statements[0] and "ORDER BY reward_accrual.id" in statements[0]
        statements.clear()
        reward_engine.recompute_window(db_session, rule_a.id, NOV_WINDOW[0], locked=True)
        assert statements == []
    finally:
        event.remove(bind, "before_cursor_execute", capture)


def test_lock_windows_locks_whole_group_in_one_statement(db_session):
    card = _card(db_session)
    group = uuid.uuid4()
    rule_a = _rule(db_session, card, name="測試規則A", shared_cap_id=group)
    rule_b = _rule(db_session, card, name="測試規則B", shared_cap_id=group)
    spend_a = _spend(db_session, card, 1000, date(2026, 11, 18), [rule_a])
    spend_b = _spend(db_session, card, 1000, date(2026, 11, 19), [rule_b])
    reward_engine.sync_entry(db_session, spend_a.id)
    reward_engine.sync_entry(db_session, spend_b.id)
    db_session.commit()  # a fresh transaction: the syncs above already hold the window

    statements, capture = _capture_for_update(db_session)
    bind = db_session.connection().engine
    event.listen(bind, "before_cursor_execute", capture)
    try:
        rows = reward_engine.lock_windows(db_session, [(rule_a.id, NOV_WINDOW[0])])
    finally:
        event.remove(bind, "before_cursor_execute", capture)
    assert len(statements) == 1
    assert [r.id for r in rows] == sorted(r.id for r in rows)
    assert {r.rule_id for r in rows} == {rule_a.id, rule_b.id}

    statements.clear()  # the window is held now: a second lock_windows reads it fresh and locks nothing again
    event.listen(bind, "before_cursor_execute", capture)
    try:
        again = reward_engine.lock_windows(db_session, [(rule_b.id, NOV_WINDOW[0])])
    finally:
        event.remove(bind, "before_cursor_execute", capture)
    assert statements == []
    assert [r.id for r in again] == [r.id for r in rows]


def test_purchase_sync_with_refunds_takes_one_accrual_lock(db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    refunds = [make_entry(db_session, card, amount, kind="refund", entry_date=day, refunds_entry_id=spend.id)
               for amount, day in (("100", date(2026, 11, 25)), ("200", date(2026, 12, 20)))]
    reward_engine.sync_entry(db_session, spend.id)

    statements, capture = _capture_for_update(db_session)
    bind = db_session.connection().engine
    event.listen(bind, "before_cursor_execute", capture)
    try:
        spend.entry_date = date(2026, 12, 20)
        db_session.flush()
        reward_engine.sync_entry(db_session, spend.id)
    finally:
        event.remove(bind, "before_cursor_execute", capture)
    assert len([s for s in statements if "reward_accrual" in s]) == 1
    for refund, value in zip(refunds, ("-1.0000", "-2.0000")):
        row = _one(db_session, source_entry_id=refund.id)
        assert (row.window_start, row.estimate) == (date(2026, 12, 16), Decimal(value))


def test_locked_recompute_requires_held_window_locks(db_session):
    card = _card(db_session)
    group = uuid.uuid4()
    rule_a = _rule(db_session, card, name="測試規則A", shared_cap_id=group)
    rule_b = _rule(db_session, card, name="測試規則B", shared_cap_id=group)
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule_a])
    reward_engine.sync_entry(db_session, spend.id)
    db_session.commit()

    with pytest.raises(RuntimeError, match="holds no lock_windows lock"):
        reward_engine.recompute_window(db_session, rule_a.id, NOV_WINDOW[0], locked=True)
    reward_engine.lock_windows(db_session, [(rule_b.id, NOV_WINDOW[0])])  # group-expanded: covers rule A too
    reward_engine.recompute_window(db_session, rule_a.id, NOV_WINDOW[0], locked=True)

    db_session.commit()
    with pytest.raises(RuntimeError):
        reward_engine.recompute_window(db_session, rule_a.id, NOV_WINDOW[0], locked=True)
    reward_engine.lock_windows(db_session, [(rule_a.id, NOV_WINDOW[0])])
    db_session.rollback()
    with pytest.raises(RuntimeError):
        reward_engine.recompute_window(db_session, rule_a.id, NOV_WINDOW[0], locked=True)
    # Another window is not covered by this one's lock.
    reward_engine.lock_windows(db_session, [(rule_a.id, NOV_WINDOW[0])])
    with pytest.raises(RuntimeError):
        reward_engine.recompute_window(db_session, rule_a.id, date(2026, 12, 16), locked=True)
