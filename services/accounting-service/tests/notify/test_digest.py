from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from notify.digest import QUESTION_TEXT, build_weekly

TZ8 = timezone(timedelta(hours=8))


def test_missing_statement_card_cadence(fx):
    snap = fx.weekly_snapshot(today=date(2026, 11, 28), statements_for={"旅遊卡": [fx.statement(period_end=date(2026, 10, 15))]})
    m = build_weekly(snap, fx.cfg(), None).missing[0]
    assert (m.name, m.expected, m.days_late) == ("旅遊卡", date(2026, 11, 15), 13)


def test_missing_statement_bank_median_gap(fx):
    snap = fx.weekly_snapshot(today=date(2026, 11, 28), statements_for={"薪轉戶 A": [fx.statement(period_end=d) for d in (date(2026, 7, 31), date(2026, 8, 31), date(2026, 9, 30))]})
    assert build_weekly(snap, fx.cfg(), None).missing[0].expected == date(2026, 10, 30)  # lower of gaps 31, 30


def test_missing_statement_single_bank_statement_plus_one_month(fx):
    snap = fx.weekly_snapshot(today=date(2026, 11, 28), statements_for={"薪轉戶 A": [fx.statement(period_end=date(2026, 9, 30))]})
    assert build_weekly(snap, fx.cfg(), None).missing[0].expected == date(2026, 10, 31)


def test_not_missing_inside_grace(fx):
    snap = fx.weekly_snapshot(today=date(2026, 11, 20), statements_for={"旅遊卡": [fx.statement(period_end=date(2026, 10, 15))]})
    assert build_weekly(snap, fx.cfg(), None).missing == []


def test_open_cases_capped_and_aged(fx):
    snap = fx.weekly_snapshot(inbox_items={"旅遊卡": 12}, oldest_created=date(2026, 11, 1), today=date(2026, 11, 28))
    ac = build_weekly(snap, fx.cfg(), None).open_cases[0]
    assert (ac.count, len(ac.items), ac.truncated, ac.oldest_days) == (12, 10, 2, 27)
    assert {l.question_zh for l in ac.items} <= set(QUESTION_TEXT.values()) and ac.items[0].question_zh == "只有銀行有"
    assert (ac.items[0].date, ac.items[0].amount, ac.items[0].text, ac.items[0].statement_id) == (
        date(2026, 10, 1), Decimal("100"), "商店0", 155)


def test_entry_only_item_and_no_item_fields(fx):
    snap = fx.weekly_snapshot(inbox={"accounts": [{"id": 100, "name": "旅遊卡", "items": [
        {"case_id": 1, "question": "ledger_only", "statement": {"id": 155}, "line": None,
         "entry": {"id": 5, "date": "2026-10-09", "title": "午餐", "amount": "250", "account_id": 100}},
        {"case_id": 2, "question": "balance_gap", "statement": {"id": 155}, "line": None, "entry": None}]}]})
    a, b = build_weekly(snap, fx.cfg(), None).open_cases[0].items
    assert (a.date, a.amount, a.text, a.question_zh) == (date(2026, 10, 9), Decimal("250"), "午餐", "只有帳本有")
    assert (b.date, b.amount, b.question_zh) == (None, None, "餘額差")


def test_quiet_world_is_empty_and_passes_last_daily_run(fx):
    run = datetime(2026, 11, 3, 6, 0, tzinfo=TZ8)
    rep = build_weekly(fx.weekly_snapshot(), fx.cfg(), run)
    assert (rep.open_cases, rep.missing, rep.skips, rep.last_daily_run) == ([], [], [], run)


def test_skips_last_seven_days_deduped_per_statement(fx):
    audit = [fx.audit_event(id=5, statement_id=155, reason="over_cap", created_at="2026-11-02T08:00:00+08:00"),
             fx.audit_event(id=4, statement_id=155, reason="twin", created_at="2026-10-31T08:00:00+08:00"),
             fx.audit_event(id=3, statement_id=156, reason="cutoff", created_at="2026-11-02T08:00:00+08:00"),
             fx.audit_event(id=2, statement_id=157, reason="twin", created_at="2026-10-20T08:00:00+08:00")]
    skips = build_weekly(fx.weekly_snapshot(audit=audit), fx.cfg(), None).skips
    assert [(s.event_id, s.card_name, s.reason) for s in skips] == [(5, "旅遊卡", "over_cap")]


def test_gaps(fx):
    snap = fx.weekly_snapshot(extra_cards=[fx.account(id=11, name="無規則卡", is_credit=True, closing_day=None)],
                              statements_for={"無規則卡": [fx.statement(account_id=11, period_end=date(2026, 10, 31), due_date=None, total="500")]})
    g = build_weekly(snap, fx.cfg(), None).gaps
    assert "無規則卡" in g.cards_without_rule and ("無規則卡", snap.statements[11][0]["id"]) in g.statements_without_due
    assert "無規則卡" in g.unlinked_cards and "旅遊卡" not in g.unlinked_cards


def test_oldest_days_uses_taipei_date(fx):
    snap = fx.weekly_snapshot(today=date(2026, 11, 28), inbox_items={"旅遊卡": 1})
    snap.open_cases[100][0]["created_at"] = "2026-11-01T17:00:00Z"  # Nov 2 01:00 in Taipei
    assert build_weekly(snap, fx.cfg(), None).open_cases[0].oldest_days == 26


def test_folded_items_are_not_counted(fx):
    snap = fx.weekly_snapshot(inbox_items={"旅遊卡": 2})
    snap.inbox["accounts"][0]["items"][1]["folded"] = True
    ac = build_weekly(snap, fx.cfg(), None).open_cases[0]
    assert (ac.count, len(ac.items), ac.truncated) == (1, 1, 0)


def test_cards_without_rule_counts_recent_payment_activity(fx):
    def gaps(entry_date):
        pay = fx.entry(account_id=12, kind="transfer_in", amount="500", entry_date=entry_date)
        snap = fx.weekly_snapshot(extra_cards=[fx.account(id=12, name="閒置卡", closing_day=None)],
                                  payments={12: [pay]})
        return build_weekly(snap, fx.cfg(), None).gaps
    assert "閒置卡" in gaps(date(2026, 11, 3) - timedelta(days=90)).cards_without_rule
    g = gaps(date(2026, 11, 3) - timedelta(days=91))
    assert "閒置卡" not in g.cards_without_rule and g.statements_without_due == []
