from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path

from notify import debits
from notify.digest import AccountCases, CaseLine, Gaps, MissingStatement, WeeklyReport
from notify.render import (fmt_amount, render_daily, render_failure, render_weekly, split_blocks, weekday_zh)

GOLDEN = Path(__file__).parent / "golden"
SAMPLE_DAY = date(2026, 11, 5)  # the spec §5.7 sample day (Thursday)
TZ8 = timezone(timedelta(hours=8))


def golden(name: str) -> str:
    return (GOLDEN / name).read_text(encoding="utf-8").rstrip("\n")


def sample_report(fx):
    """The §5.7 world: the default cards plus 外幣戶 B / 海外卡 and a 聯名卡 skip, on Thu 11-05."""
    snap = fx.snapshot(
        today=SAMPLE_DAY,
        statements_for={
            "旅遊卡": [fx.statement_default("旅遊卡", open_case_count=2), fx.statement(
                id=145, account_id=100, total="0", period_end=date(2026, 9, 15), due_date=date(2026, 10, 5))],
            "海外卡": [fx.statement(id=165, account_id=120, total="150", period_end=date(2026, 10, 15),
                                  due_date=date(2026, 11, 6), currency="USD"),
                     fx.statement(id=166, account_id=120, total="0", period_end=date(2026, 9, 15),
                                  due_date=date(2026, 10, 6), currency="USD")]},
        extra_cards=[
            fx.account(id=119, name="外幣戶 B", currency="USD", balance="1200", is_credit=False, closing_day=None,
                       due_rule=None, due_value=None),
            fx.account(id=120, name="海外卡", currency="USD", closing_day=15, due_value=6, auto_pay_account_id=119),
            fx.account(id=110, name="聯名卡", closing_day=None, due_rule=None, due_value=None)],
        audit=[fx.audit_event(id=9, reason="no_link", created_at="2026-11-05T07:00:00+08:00", card_account_id=110,
                              amount="3840")])
    return debits.build_daily(snap, fx.cfg())


def test_daily_matches_spec_sample(fx):
    msgs = render_daily(sample_report(fx), fx.cfg())
    assert msgs == [golden("daily.txt")]


def test_daily_overdue_variants(fx):
    d = date(2026, 11, 12)
    auto = fx.item(155, debit_day=date(2026, 11, 6), unpaid_reason="over_cap", overdue_days=0, in_window=False,
                   paid=Decimal("20000"), open_cases=1)
    note = fx.item(156, card_id=97, card_name="加油卡", amount=Decimal("8.5"), debit_day=date(2026, 11, 12),
                   due=date(2026, 11, 10), note="幣別不同，請自行確認")
    manual = fx.item(157, card_id=93, card_name="黑啤卡", amount=Decimal("4378"), cls="manual", source_id=None,
                     due=date(2026, 11, 2), debit_day=date(2026, 11, 2), overdue_days=10, in_window=False)
    nodue = fx.item(159, card_id=94, card_name="新卡", amount=Decimal("700"), cls="manual", source_id=None,
                    due=None, debit_day=None, in_window=False, note="到期日未辨識")
    rule = fx.item(None, card_id=98, card_name="超市卡", amount=None, cls="manual", source_id=None, from_rule=True,
                   due=date(2026, 11, 2), debit_day=date(2026, 11, 2), overdue_days=10, in_window=False,
                   note="帳單未到")
    rep = fx.report([auto, note, manual, nodue, rule], today=d)
    assert render_daily(rep, fx.cfg()) == [golden("daily_overdue.txt")]


def test_cleared_line_alongside_block(fx):
    rep = sample_report(fx)
    from dataclasses import replace
    rep = replace(rep, cleared=[rep.blocks[0]])
    out = render_daily(rep, fx.cfg())[0]
    assert "餘額 52,300 → 扣款後 −3,900 ⚠ 缺 3,900" in out and "✅ 薪轉戶 A 已補足" in out


def test_weekly_matches_golden(fx):
    cfg = fx.cfg()
    rep = WeeklyReport(
        today=date(2026, 11, 7),
        open_cases=[AccountCases(76, "旅遊卡", 12, 9, [
            CaseLine(date(2026, 11, 2), Decimal("1280"), "全聯", "只有銀行有", 155),
            CaseLine(None, None, "轉帳", "疑似轉帳", 155)], 10)],
        missing=[MissingStatement(93, "黑啤卡", date(2026, 10, 15), date(2026, 11, 15), 12)],
        skips=[fx.skip(7, card_id=100)],
        gaps=Gaps(["聯名卡"], ["海外卡"], [("旅遊卡", 155)]),
        last_daily_run=None)
    assert render_weekly(rep, cfg) == [golden("weekly.txt")]


def _empty(last):
    return WeeklyReport(today=date(2026, 11, 21), open_cases=[], missing=[], skips=[], gaps=Gaps([], [], []),
                        last_daily_run=last)


def test_weekly_empty_line(fx):
    out = render_weekly(_empty(datetime(2026, 11, 20, 7, 0, tzinfo=TZ8)), fx.cfg())
    assert out == [golden("weekly_empty.txt")]


def test_weekly_empty_without_daily_run(fx):
    (out,) = render_weekly(_empty(None), fx.cfg())
    assert out.endswith("最近一次每日檢查 尚無")


def test_helpers():
    assert fmt_amount(Decimal("48200")) == "48,200" and fmt_amount(Decimal("12.5")) == "12.50"
    assert fmt_amount(Decimal("-3900")) == "−3,900"
    assert weekday_zh(date(2026, 11, 5)) == "（四）" and weekday_zh(date(2026, 11, 8)) == "（日）"
    assert render_failure("API 無法連線") == "❌ 帳務通知失敗：API 無法連線"


def test_split_on_block_boundary():
    blocks = ["a" * 1500, "b" * 1500, "c" * 10]
    out = split_blocks(blocks, limit=2000)
    assert out == ["a" * 1500, "b" * 1500 + "\n\n" + "c" * 10] and all(len(m) <= 2000 for m in out)


def test_oversized_block_is_split_on_lines():
    block = "\n".join(["x" * 100] * 30)   # 3029 chars, no block boundary
    out = split_blocks([block], limit=2000)
    assert len(out) == 2 and all(len(m) <= 2000 for m in out) and "\n".join(out) == block
