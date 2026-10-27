from datetime import date
from decimal import Decimal

from notify.dedupe import apply
from notify.state import LastSend, State


def test_new_then_quiet_then_debit_day(fx):
    rep = fx.report(items=[fx.item(statement_id=1, debit_day=date(2026, 11, 6))], today=date(2026, 11, 3))
    out, st, keys = apply(rep, State.empty(), date(2026, 11, 3)); assert out.blocks and keys == {"new:1"}
    out, st, keys = apply(rep, st, date(2026, 11, 4)); assert not out.blocks and keys == frozenset()
    out, st, keys = apply(fx.report(items=[fx.item(statement_id=1, debit_day=date(2026, 11, 6))], today=date(2026, 11, 6)), st, date(2026, 11, 6)); assert out.blocks and keys == {"today:1"}


def test_shortfall_every_day_then_cleared_once(fx):
    rep = fx.report(items=[fx.item(statement_id=1)], shortfall=Decimal("3900"))
    _, st, _ = apply(rep, State.empty(), date(2026, 11, 3)); out, st, keys = apply(rep, st, date(2026, 11, 4)); assert out.blocks and "short:76" in keys
    out, st, keys = apply(fx.report(items=[fx.item(statement_id=1)], shortfall=None), st, date(2026, 11, 5)); assert out.cleared and not out.blocks and "cleared:76" in keys
    out, st, _ = apply(fx.report(items=[fx.item(statement_id=1)], shortfall=None), st, date(2026, 11, 6)); assert not out.cleared


def test_block_keeps_all_items_when_one_triggers(fx):
    rep = fx.report(items=[fx.item(statement_id=1), fx.item(statement_id=2)])
    _, st, _ = apply(rep, State.empty(), date(2026, 11, 3))
    rep2 = fx.report(items=[fx.item(statement_id=1), fx.item(statement_id=2), fx.item(statement_id=3)])
    out, _, _ = apply(rep2, st, date(2026, 11, 4)); assert len(out.blocks[0].items) == 3 and out.blocks[0].projected == rep2.blocks[0].projected


def test_skip_once(fx):
    rep = fx.report(skips=[fx.skip(event_id=9)])
    out, st, keys = apply(rep, State.empty(), date(2026, 11, 3)); assert out.skips and keys == {"skip:9"}
    out, _, _ = apply(rep, st, date(2026, 11, 4)); assert not out.skips


def test_overdue_unpaid_and_rule_only_fire_every_day(fx):
    d = date(2026, 11, 4)
    rep = fx.report(items=[fx.item(1, overdue_days=2), fx.item(2, unpaid_reason="insufficient"),
                           fx.item(None, card_id=98, from_rule=True)])
    _, st, _ = apply(rep, State.empty(), d)
    out, st, keys = apply(rep, st, date(2026, 11, 5))
    assert out.blocks and keys == {"overdue:1", "overdue:2", "overdue:-98"}


def test_manual_block_same_triggers_without_shortfall(fx):
    d = date(2026, 11, 3)
    rep = fx.report(items=[fx.item(5, cls="manual", card_id=93, card_name="黑啤卡", source_id=None)])
    out, st, keys = apply(rep, State.empty(), d)
    assert [i.card_name for i in out.manual] == ["黑啤卡"] and keys == {"new:5"}
    out, st, keys = apply(rep, st, date(2026, 11, 4)); assert not out.manual and not keys
    rep = fx.report(items=[fx.item(5, cls="manual", card_id=93, card_name="黑啤卡", source_id=None, overdue_days=1)])
    out, _, keys = apply(rep, st, date(2026, 11, 5)); assert out.manual and keys == {"overdue:5"}


def test_apply_is_pure(fx):
    rep = fx.report(items=[fx.item(1)], shortfall=Decimal("10"), skips=[fx.skip(3)])
    st = State.empty()
    apply(rep, st, date(2026, 11, 3))
    assert st == State.empty() and not rep.cleared


def test_state_records_first_notified_and_last_shortfall(fx):
    rep = fx.report(items=[fx.item(1)], shortfall=Decimal("3900"))
    _, st, _ = apply(rep, State.empty(), date(2026, 11, 3))
    assert st.statements[1].first_notified == date(2026, 11, 3) and st.sources[76].last_shortfall == Decimal("3900")
    _, st, _ = apply(rep, st, date(2026, 11, 4))
    assert st.statements[1].first_notified == date(2026, 11, 3)


def test_new_key_stable_on_notification_day_but_not_next_day(fx):
    rep = fx.report(items=[fx.item(statement_id=1, debit_day=date(2026, 11, 20))])
    _, st, keys = apply(rep, State.empty(), date(2026, 11, 3)); assert keys == {"new:1"}
    _, _, again = apply(rep, st, date(2026, 11, 3)); assert "new:1" in again
    out, _, later = apply(rep, st, date(2026, 11, 4)); assert "new:1" not in later and not out.blocks


def test_same_day_reapply_keeps_seen_skip_key(fx):
    # Ruling 17: the key set carries every skip in the report; only the rendered list is unseen-only
    d = date(2026, 11, 3)
    rep = fx.report(items=[fx.item(1)], skips=[fx.skip(9)])
    out, st, first = apply(rep, State.empty(), d)
    assert out.skips and "skip:9" in first
    st.last_send = LastSend(d, first)
    out, _, again = apply(rep, st, d)
    assert again == first and not out.skips


def test_same_day_reapply_keeps_cleared_key_with_new_item(fx):
    # day 1 short; day 2 cleared and sent; a same-day re-run keeps cleared:76 (line not repeated);
    # a new item that same day changes the set
    rep = fx.report(items=[fx.item(1)], shortfall=Decimal("3900"))
    _, st, _ = apply(rep, State.empty(), date(2026, 11, 3))
    d = date(2026, 11, 4)
    cleared = fx.report(items=[fx.item(1)], shortfall=None)
    out, st, first = apply(cleared, st, d)
    assert out.cleared and "cleared:76" in first
    st.last_send = LastSend(d, first)
    out, st2, again = apply(cleared, st, d)
    assert again == first and not out.cleared
    out, _, more = apply(fx.report(items=[fx.item(1), fx.item(2)], shortfall=None), st, d)
    assert "cleared:76" in more and "new:2" in more and more != first and not out.cleared
    # the next day the key is gone
    _, _, later = apply(cleared, st2, date(2026, 11, 5))
    assert "cleared:76" not in later
