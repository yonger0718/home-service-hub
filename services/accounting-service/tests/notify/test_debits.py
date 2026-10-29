from datetime import date
from decimal import Decimal

from notify.debits import REASON_TEXT, build_daily


def test_window_and_debit_day(fx):
    # 旅遊卡: closing 15, fixed_day 5 → due 11-05 (holiday) → debit 11-06 Fri; lead 2 from Tue 11-03: business days (11-03, 11-06] = 11-04, 11-06 → 2 → in window
    rep = build_daily(fx.snapshot(), fx.cfg())
    item = fx.find(rep, "旅遊卡"); assert item.debit_day == date(2026, 11, 6) and item.in_window and item.cls == "auto"


def test_rule_only_when_statement_missing(fx):
    # Thu 11-05: the rule debit day Mon 11-09 is 2 business days out → in window
    rep = build_daily(fx.snapshot(today=date(2026, 11, 5), statements_for={"超市卡": []}), fx.cfg())
    item = fx.find(rep, "超市卡"); assert item.from_rule and item.amount is None and item.cls == "manual" and item in rep.manual
    assert any(i.from_rule and i.due == date(2026, 11, 7) and i.in_window for i in rep.manual if i.card_name == "超市卡")


def test_cutoff_uses_debit_day(fx):
    # due Thu 11-05 (holiday) → debit Fri 11-06: enabled_from 11-06 keeps it auto, 11-07 makes it manual
    # the fixture puts the string in settings["auto_pay"]["enabled_from"], as the real API does
    assert fx.find(build_daily(fx.snapshot(enabled_from="2026-11-06"), fx.cfg()), "旅遊卡").cls == "auto"
    assert fx.find(build_daily(fx.snapshot(enabled_from="2026-11-07"), fx.cfg()), "旅遊卡").cls == "manual"
    assert fx.find(build_daily(fx.snapshot(auto_pay_enabled=False), fx.cfg()), "旅遊卡").cls == "manual"


def test_link_predicate_evaluated_now(fx):
    for bad in (dict(is_archived=True), dict(is_credit=True), dict(currency="USD")):
        item = fx.find(build_daily(fx.snapshot(source_overrides={76: bad}), fx.cfg()), "旅遊卡")
        assert item.cls == "manual" and item in build_daily(fx.snapshot(source_overrides={76: bad}), fx.cfg()).manual
    assert fx.find(build_daily(fx.snapshot(source_overrides={76: dict(currency="USD")}), fx.cfg()), "旅遊卡").note == "幣別不同，請自行確認"


def test_paid_by_child_card(fx):
    # auto item: exact-amount transfer_in on the 副卡 within twin_window_days of the debit day → dropped
    snap = fx.snapshot(payments={100: [fx.entry(account_id=104, kind="transfer_in", amount="48200", entry_date=date(2026, 11, 1))]})
    assert fx.find(build_daily(snap, fx.cfg()), "旅遊卡") is None


def test_partial_payment_stays_and_projects_full_total(fx):
    snap = fx.snapshot(payments={100: [fx.entry(account_id=100, kind="transfer_in", amount="10000", entry_date=date(2026, 11, 1))]})
    rep = build_daily(snap, fx.cfg()); item = fx.find(rep, "旅遊卡")
    assert item.paid == Decimal("10000") and item.amount == Decimal("48200") and fx.block(rep, "薪轉戶 A").projected == Decimal("4100")
    # two 24,100 payments sum to the total but are not a twin: the auto item stays
    snap = fx.snapshot(payments={100: [fx.entry(account_id=100, kind="transfer_in", amount="24100", entry_date=d) for d in (date(2026, 10, 20), date(2026, 11, 1))]})
    assert fx.find(build_daily(snap, fx.cfg()), "旅遊卡") is not None


def test_payment_window_stops_before_next_closing(fx):
    # manual card 黑啤卡 (closing 15, fixed_day 6 → due 11-06); a transfer_in on 11-20 belongs to the next window
    snap = fx.snapshot(today=date(2026, 11, 25), payments={93: [fx.entry(account_id=93, kind="transfer_in", amount="4378", entry_date=date(2026, 11, 20))]})
    assert fx.find(build_daily(snap, fx.cfg()), "黑啤卡").overdue_days == 19


def test_unexpected_statement_still_projected(fx):
    extra = fx.statement(account_id=100, total="5000", period_end=date(2026, 9, 30), due_date=date(2026, 11, 4))
    rep = build_daily(fx.snapshot(statements_for={"旅遊卡": [fx.statement_default("旅遊卡"), extra]}), fx.cfg())
    assert fx.block(rep, "薪轉戶 A").projected == Decimal("-900") and len([i for i in rep.blocks[0].items if i.card_name == "旅遊卡"]) == 2


def _card(rep, name):
    return [i for i in rep.blocks[0].items + rep.manual if i.card_name == name]


def test_off_cycle_statement_suppresses_missing_line(fx):
    # 超市卡 (closing 25, due fixed_day 7): neither cycle has a match, one off-cycle live statement (period_end 09-20)
    # Fri 11-06: the off-cycle debit day Tue 11-10 is 2 business days out → in window
    off = fx.statement_default("超市卡", period_end=date(2026, 9, 20), due_date=date(2026, 11, 10))
    rep = build_daily(fx.snapshot(today=date(2026, 11, 6), statements_for={"超市卡": [off]}), fx.cfg())
    items = _card(rep, "超市卡")
    # 09-20 > 08-28 (C−2 end + 3) suppresses the C−1 line (due 10-07); 09-20 is not newer than 09-28 (C−1 end + 3),
    # so the current cycle still gets its 帳單未到 (due 11-07)
    assert len(items) == 2
    stmt = [i for i in items if not i.from_rule]; rule = [i for i in items if i.from_rule]
    assert len(stmt) == 1 and stmt[0].amount is not None
    assert len(rule) == 1 and rule[0].due == date(2026, 11, 7)
    assert not any(i.from_rule and i.due == date(2026, 10, 7) for i in items)


def test_newer_off_cycle_statement_suppresses_both_missing_lines(fx):
    # period_end 10-15 is newer than 09-28 (C−1 end + 3): it is the current cycle's bill too → no 帳單未到 at all
    off = fx.statement_default("超市卡", period_end=date(2026, 10, 15), due_date=date(2026, 11, 10))
    rep = build_daily(fx.snapshot(today=date(2026, 11, 6), statements_for={"超市卡": [off]}), fx.cfg())
    items = _card(rep, "超市卡")
    assert len(items) == 1 and not items[0].from_rule and items[0].amount is not None


def test_out_of_window_off_cycle_statement_keeps_current_missing_line(fx):
    # Thu 11-05: the off-cycle statement (period_end 09-20, due 11-20) is out of window; the current cycle's
    # 帳單未到 (due 11-07) still appears, the C−1 line (due 10-07) stays suppressed
    off = fx.statement_default("超市卡", period_end=date(2026, 9, 20), due_date=date(2026, 11, 20))
    rep = build_daily(fx.snapshot(today=date(2026, 11, 5), statements_for={"超市卡": [off]}), fx.cfg())
    rule = [i for i in rep.manual if i.card_name == "超市卡" and i.from_rule]
    assert [i.due for i in rule] == [date(2026, 11, 7)] and rule[0].in_window


def test_source_side_twins_drop_auto_item(fx):
    # (b) grouped transfer_out on the source whose peer transfer_in sits on the card family
    gid = "7b1e0a2c-0000-4000-8000-000000000001"
    snap = fx.snapshot(payments={100: [fx.entry(account_id=100, kind="transfer_in", amount="48200", entry_date=date(2026, 11, 5), transfer_group_id=gid)]},
                       source_entries={76: [fx.entry(account_id=76, kind="transfer_out", amount="-48200", entry_date=date(2026, 11, 5), transfer_group_id=gid)]})
    assert fx.find(build_daily(snap, fx.cfg()), "旅遊卡") is None
    # (b) grouped transfer_out whose peer is elsewhere → not a twin, item stays
    snap = fx.snapshot(source_entries={76: [fx.entry(account_id=76, kind="transfer_out", amount="-48200", entry_date=date(2026, 11, 5), transfer_group_id=gid)]})
    assert fx.find(build_daily(snap, fx.cfg()), "旅遊卡") is not None
    # (c) statement-booked expense on the source
    snap = fx.snapshot(source_entries={76: [fx.entry(account_id=76, kind="expense", amount="-48200", entry_date=date(2026, 11, 4), source="statement")]})
    assert fx.find(build_daily(snap, fx.cfg()), "旅遊卡") is None
    # (c) a hand-entered expense of the same amount is not a twin
    snap = fx.snapshot(source_entries={76: [fx.entry(account_id=76, kind="expense", amount="-48200", entry_date=date(2026, 11, 4), source="manual")]})
    assert fx.find(build_daily(snap, fx.cfg()), "旅遊卡") is not None


def test_null_due_statement_is_manual_note(fx):
    rep = build_daily(fx.snapshot(statements_for={"旅遊卡": [fx.statement_default("旅遊卡", due_date=None)]}), fx.cfg())
    item = fx.find(rep, "旅遊卡"); assert item.cls == "manual" and item.note == "到期日未辨識" and item.debit_day is None


def test_projection_and_shortfall(fx):
    # 薪轉戶 A balance 52,300; 旅遊卡 48,200 (11-06) + 加油卡 8,000 (11-09: outside D = max in-window debit 11-06 → excluded) → projected 4,100, no shortfall
    blk = fx.block(build_daily(fx.snapshot(), fx.cfg()), "薪轉戶 A"); assert blk.projected == Decimal("4100") and blk.shortfall is None
    # a pending expense line of −8,000 on 11-04 from 薪轉戶 A → projected −3,900, shortfall 3,900; a +9,000 income line is ignored
    pending = [fx.instance(lines=[fx.line(account_id=76, amount="-8000")], due_date=date(2026, 11, 4)), fx.instance(lines=[fx.line(account_id=76, amount="9000")], due_date=date(2026, 11, 4))]
    blk = fx.block(build_daily(fx.snapshot(pending=pending), fx.cfg()), "薪轉戶 A"); assert blk.shortfall == Decimal("3900")


def test_mixed_currency_source(fx):
    rep = build_daily(fx.snapshot(extra_cards=[fx.account(id=9, name="海外卡", currency="USD", auto_pay_account_id=76, closing_day=15, due_rule="fixed_day", due_value=5)], statements_for={"海外卡": [fx.statement(account_id=9, currency="USD", total="150", period_end=date(2026, 10, 15), due_date=date(2026, 11, 5))]}), fx.cfg())
    item = fx.find(rep, "海外卡"); assert item.cls == "manual" and item.note == "幣別不同，請自行確認" and item in rep.manual
    assert fx.block(rep, "薪轉戶 A").projected == Decimal("4100")


def test_overdue_manual_and_unpaid_auto(fx):
    snap = fx.snapshot(today=date(2026, 11, 12), audit=[fx.audit_event(id=5, statement_id=155, reason="over_cap", created_at="2026-11-06T06:00:00+08:00")])
    rep = build_daily(snap, fx.cfg())
    assert fx.find(rep, "黑啤卡").overdue_days == 6 and fx.find(rep, "旅遊卡").unpaid_reason == "over_cap"


def test_rule_only_stays_overdue_for_30_days(fx):
    snap = fx.snapshot(today=date(2026, 11, 20), statements_for={"超市卡": []})
    item = fx.find(build_daily(snap, fx.cfg()), "超市卡"); assert item.from_rule and item.overdue_days == 13 and item.note == "帳單未到"
    assert fx.find(build_daily(fx.snapshot(today=date(2026, 12, 7), statements_for={"超市卡": []}), fx.cfg()), "超市卡").overdue_days == 30
    # day 31 drops the 11-07 line; the next cycle's own 帳單未到 (rule due 12-07, debit Mon 12-07) is a different
    # item that is 1 day overdue by then, so the brief's `find(...) is None` cannot hold — assert on the 11-07 line
    rep = build_daily(fx.snapshot(today=date(2026, 12, 8), statements_for={"超市卡": []}), fx.cfg())
    assert [i.due for i in rep.manual if i.card_name == "超市卡"] == [date(2026, 12, 7)]


def test_skips_since_yesterday_0600(fx):
    snap = fx.snapshot(audit=[fx.audit_event(id=1, reason="cutoff", created_at="2026-11-03T06:00:00+08:00"), fx.audit_event(id=2, reason="no_link", created_at="2026-11-03T06:00:01+08:00"), fx.audit_event(id=3, reason="twin", created_at="2026-11-02T05:59:00+08:00")])
    assert [s.event_id for s in build_daily(snap, fx.cfg()).skips] == [2]


def test_reason_text_table():
    assert REASON_TEXT == {
        "no_link": "卡片沒有設定扣款帳戶", "link_ineligible": "扣款帳戶不可用", "over_cap": "超過單筆上限",
        "monthly_budget": "超過當月總額", "twin": "疑似已手動繳過", "duplicate_statement": "重複帳單",
        "header_disputed": "帳單有爭議", "expired": "超過重試期限",
    }


def test_default_world_items(fx):
    # Thu 11-05 = the spec §5.7 sample: the 副卡 is folded into 旅遊卡 (never its own card) and the 0-total
    # previous-cycle statements are dropped
    rep = build_daily(fx.snapshot(today=date(2026, 11, 5)), fx.cfg())
    blk = fx.block(rep, "薪轉戶 A")
    assert [i.card_name for i in blk.items] == ["旅遊卡", "加油卡"] and (blk.projected, blk.shortfall) == (Decimal("-3900"), Decimal("3900"))
    assert [(i.card_name, i.from_rule, i.due) for i in rep.manual] == [("黑啤卡", False, date(2026, 11, 6)), ("超市卡", True, date(2026, 11, 7))]


def test_out_of_window_item_absent_in_window_present(fx):
    # Tue 11-03: 加油卡 (debit Mon 11-09, 3 business days out) and 超市卡's rule line (same day) are not carried
    rep = build_daily(fx.snapshot(), fx.cfg())
    assert [i.card_name for i in fx.block(rep, "薪轉戶 A").items] == ["旅遊卡"] and fx.find(rep, "加油卡") is None
    assert [i.card_name for i in rep.manual] == ["黑啤卡"] and fx.block(rep, "薪轉戶 A").projected == Decimal("4100")


def test_projection_counts_debits_outside_a_shorter_card_window(fx):
    # 旅遊卡 lead 0: its 11-06 debit is outside its own window on 11-05 but <= D (加油卡's 11-09) → still projected
    rep = build_daily(fx.snapshot(today=date(2026, 11, 5)), fx.cfg(lead_overrides={100: 0}))
    blk = fx.block(rep, "薪轉戶 A"); assert [i.card_name for i in blk.items] == ["加油卡"] and blk.projected == Decimal("-3900")


def test_old_booked_statement_outside_fetch_is_not_an_item(fx):
    # a 05-15 cycle bill booked by the job on 06-05: its payment is older than the today − 120 fetch
    old = fx.statement(account_id=100, total="30000", period_end=date(2026, 5, 15), due_date=date(2026, 6, 5))
    snap = fx.snapshot(enabled_from="2026-01-01", statements_for={"旅遊卡": [fx.statement_default("旅遊卡"), old]})
    rep = build_daily(snap, fx.cfg())
    assert [i for i in [x for b in rep.blocks for x in b.items] + rep.manual if i.statement_id == old["id"]] == []
    assert fx.block(rep, "薪轉戶 A").projected == Decimal("4100")


def test_paid_off_cycle_statement_does_not_suppress_missing_line(fx):
    # last month's off-cycle bill (period_end 09-20, due Sat 10-10) paid 10-09; this cycle's 10-25 bill is missing
    off = fx.statement_default("超市卡", period_end=date(2026, 9, 20), due_date=date(2026, 10, 10))
    pay = [fx.entry(account_id=98, kind="transfer_in", amount="2350", entry_date=date(2026, 10, 9))]
    for today in (date(2026, 11, 5), date(2026, 11, 9)):
        rep = build_daily(fx.snapshot(today=today, statements_for={"超市卡": [off]}, payments={98: pay}), fx.cfg())
        lines = [i for i in rep.manual if i.card_name == "超市卡"]
        assert any(i.from_rule and i.due == date(2026, 11, 7) for i in lines) and all(i.statement_id is None for i in lines)


def test_older_statement_does_not_suppress_missing_line(fx):
    # an unpaid billable statement at the cycle-before-last (period_end 08-25 <= C−2 end + 3) is history
    older = fx.statement_default("超市卡", period_end=date(2026, 8, 25), due_date=date(2026, 9, 7))
    rep = build_daily(fx.snapshot(today=date(2026, 11, 5), statements_for={"超市卡": [older]}), fx.cfg())
    lines = [i for i in rep.manual if i.card_name == "超市卡"]
    assert any(i.from_rule and i.due == date(2026, 11, 7) for i in lines) and all(i.statement_id != older["id"] for i in lines)


def test_split_child_payment_is_ignored(fx):
    # /entries returns split children (parent_entry_id set); find_twins ignores them, and so does paid
    child = fx.entry(account_id=104, kind="transfer_in", amount="48200", entry_date=date(2026, 11, 1), parent_entry_id=999)
    item = fx.find(build_daily(fx.snapshot(payments={100: [child]}), fx.cfg()), "旅遊卡")
    assert item is not None and item.paid == Decimal(0)


def test_enabled_from_null_is_never_auto(fx):
    assert fx.find(build_daily(fx.snapshot(enabled_from=None), fx.cfg()), "旅遊卡").cls == "manual"


def test_due_after_calendar_raises(fx):
    import pytest
    from notify.cycles import CalendarExhausted
    late = fx.statement_default("旅遊卡", due_date=date(2027, 3, 1))
    with pytest.raises(CalendarExhausted):
        build_daily(fx.snapshot(statements_for={"旅遊卡": [late]}), fx.cfg())


def test_past_auto_item_paid_by_hand_is_dropped(fx):
    # after the debit day the job will not debit again: two halves paid by hand clear the 未扣款 line
    pays = [fx.entry(account_id=100, kind="transfer_in", amount="24100", entry_date=d) for d in (date(2026, 11, 7), date(2026, 11, 10))]
    assert fx.find(build_daily(fx.snapshot(today=date(2026, 11, 12), payments={100: pays}), fx.cfg()), "旅遊卡") is None


def _nodue_and_prev(fx):
    """旅遊卡's 10-15 statement without a due date, plus the default 0-total 09-15 one (no 帳單未到 for that cycle)."""
    prev = fx.statement(id=145, account_id=100, total="0", period_end=date(2026, 9, 15), due_date=date(2026, 10, 5))
    return fx.statement_default("旅遊卡", due_date=None), prev


def test_paid_null_due_statement_is_dropped(fx):
    # Ruling 18: an undated matched statement is a manual item; payments in [period_end, min(today, next_closing − 1)]
    nodue, prev = _nodue_and_prev(fx)
    pay = [fx.entry(account_id=104, kind="transfer_in", amount="48200", entry_date=date(2026, 10, 20))]
    assert fx.find(build_daily(fx.snapshot(statements_for={"旅遊卡": [nodue, prev]}, payments={100: pay}), fx.cfg()), "旅遊卡") is None
    # a partial payment keeps it, with 已繳
    half = [fx.entry(account_id=100, kind="transfer_in", amount="10000", entry_date=date(2026, 10, 20))]
    item = fx.find(build_daily(fx.snapshot(statements_for={"旅遊卡": [nodue, prev]}, payments={100: half}), fx.cfg()), "旅遊卡")
    assert item is not None and item.paid == Decimal("10000") and item.note == "到期日未辨識"


def test_unpaid_recent_null_due_statement_stays(fx):
    # Tue 11-03: the 10-15 statement matches the last closed cycle; a payment before period_end does not count
    nodue, prev = _nodue_and_prev(fx)
    early = [fx.entry(account_id=100, kind="transfer_in", amount="48200", entry_date=date(2026, 10, 14))]
    rep = build_daily(fx.snapshot(statements_for={"旅遊卡": [nodue, prev]}, payments={100: early}), fx.cfg())
    assert [i.statement_id for i in rep.manual if i.card_name == "旅遊卡"] == [155]


def test_old_null_due_statement_is_dropped(fx):
    # Fri 11-20: the 11-15 cycle has closed, so the undated 10-15 statement sits in the older cycle's match zone
    # (<= C−1 end + 3) → gone; it never turns into a 帳單未到 line for that cycle either
    nodue = fx.statement_default("旅遊卡", due_date=None)
    rep = build_daily(fx.snapshot(today=date(2026, 11, 20), statements_for={"旅遊卡": [nodue]}), fx.cfg())
    lines = [i for i in [x for b in rep.blocks for x in b.items] + rep.manual if i.card_name == "旅遊卡"]
    assert all(i.statement_id != 155 for i in lines) and all(i.due != date(2026, 11, 5) for i in lines)


def test_past_auto_item_dropped_after_30_days(fx):
    # 旅遊卡 155 due 11-05 (debit 11-06), never debited: 未扣款 through day 30 (12-05), gone on day 31 (12-06)
    audit = [fx.audit_event(id=5, statement_id=155, reason="over_cap", created_at="2026-11-06T06:00:00+08:00")]
    def item_155(today):
        rep = build_daily(fx.snapshot(today=today, audit=audit), fx.cfg())
        return [i for i in [x for b in rep.blocks for x in b.items] + rep.manual if i.statement_id == 155]
    (kept,) = item_155(date(2026, 12, 5))
    assert kept.cls == "auto" and kept.unpaid_reason == "over_cap"
    assert item_155(date(2026, 12, 6)) == []


# --- quiet cycles (§5.1, owner ruling 2026-10-29): nothing owed and nothing on the card → no bill expected ---------

def _supermarket(fx, today=date(2026, 11, 5), **kw):
    """超市卡 (98, closing 25): only the 0-total 09-25 statement (148), so the 10-25 cycle has no statement."""
    zero = fx.statement(id=148, total="0", period_end=date(2026, 9, 25), due_date=date(2026, 10, 7))
    return build_daily(fx.snapshot(today=today, statements_for={"超市卡": [zero]}, **kw), fx.cfg())


def test_quiet_cycle_has_no_missing_line(fx):
    assert fx.find(_supermarket(fx, card_activity={98: []}), "超市卡") is None


def test_unread_activity_keeps_missing_line(fx):
    assert fx.find(_supermarket(fx), "超市卡").note == "帳單未到"


def test_purchase_after_zero_statement_keeps_missing_line(fx):
    spend = fx.entry(account_id=98, kind="expense", amount="-120", entry_date=date(2026, 10, 10))
    assert fx.find(_supermarket(fx, card_activity={98: [spend]}), "超市卡").note == "帳單未到"


def test_payment_after_zero_statement_is_activity(fx):
    pay = fx.entry(account_id=98, kind="transfer_in", amount="300", entry_date=date(2026, 10, 1))
    assert fx.find(_supermarket(fx, card_activity={98: [pay]}), "超市卡").note == "帳單未到"


def test_balance_adjustment_and_split_child_are_not_activity(fx):
    adj = fx.entry(account_id=98, kind="balance_adjustment", amount="-15", entry_date=date(2026, 10, 1))
    child = fx.entry(account_id=98, kind="fee", amount="-2", entry_date=date(2026, 10, 2), parent_entry_id=1)
    assert fx.find(_supermarket(fx, card_activity={98: [adj, child]}), "超市卡") is None


def test_purchase_posted_after_the_zero_statement_counts(fx):
    late = fx.entry(account_id=98, kind="expense", amount="-120", entry_date=date(2026, 9, 24),
                    posted_date=date(2026, 9, 27))
    assert fx.find(_supermarket(fx, card_activity={98: [late]}), "超市卡").note == "帳單未到"
    early = fx.entry(account_id=98, kind="expense", amount="-120", entry_date=date(2026, 9, 24))
    assert fx.find(_supermarket(fx, card_activity={98: [early]}), "超市卡") is None  # on the 09-25 bill


def test_owed_previous_statement_keeps_missing_line(fx):
    owed = fx.statement(id=148, total="500", period_end=date(2026, 9, 25), due_date=date(2026, 10, 7))
    snap = fx.snapshot(today=date(2026, 11, 5), statements_for={"超市卡": [owed]}, card_activity={98: []})
    lines = [i for i in build_daily(snap, fx.cfg()).manual if i.card_name == "超市卡" and i.from_rule]
    assert [i.due for i in lines] == [date(2026, 11, 7)]


def test_two_quiet_cycles_in_a_row_have_no_missing_lines(fx):
    rep = _supermarket(fx, today=date(2026, 12, 5), card_activity={98: []})
    assert [i for i in rep.manual if i.card_name == "超市卡"] == []


def test_zero_statement_older_than_the_read_window_keeps_missing_line(fx):
    old = fx.statement(id=148, total="0", period_end=date(2026, 6, 25), due_date=date(2026, 7, 7))
    snap = fx.snapshot(today=date(2026, 11, 5), statements_for={"超市卡": [old]}, card_activity={98: []})
    assert fx.find(build_daily(snap, fx.cfg()), "超市卡").note == "帳單未到"


def test_secondary_card_activity_counts_for_the_master(fx):
    # 旅遊卡 (100) without its 10-15 statement: only the 0-total 09-15 one (145); 副卡 104 spent in the cycle
    spend = fx.entry(account_id=104, kind="expense", amount="-80", entry_date=date(2026, 10, 1))
    rows = [fx.statement(id=145, account_id=100, total="0", period_end=date(2026, 9, 15), due_date=date(2026, 10, 5))]
    busy = fx.snapshot(statements_for={"旅遊卡": rows}, card_activity={100: [spend]})
    quiet = fx.snapshot(statements_for={"旅遊卡": rows}, card_activity={100: []})
    assert fx.find(build_daily(busy, fx.cfg()), "旅遊卡").note == "帳單未到"
    assert fx.find(build_daily(quiet, fx.cfg()), "旅遊卡") is None
