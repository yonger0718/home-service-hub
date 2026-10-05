"""Response shapes (spec "Instance endpoints" queue, "Loan summary", "Schedule links on entries")."""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.services import schedule_read as read
from tests.helpers import POSTED_AT

REOPENED = datetime(2026, 10, 2, tzinfo=timezone.utc)


def _ids(items) -> list[int]:
    return [item["id"] for item in items]


@pytest.fixture()
def card(seed):
    return seed.account("範例卡")


def test_queue_lists_confirm_failing_and_overdue_items(db_session, seed, card, today):
    # Spec "待完成交易 queue".
    today(date(2026, 10, 3))
    rent = seed.definition([seed.line("expense", card, "18000")], name="房租", posting_mode="confirm", times=12)
    netflix = seed.definition([seed.line("expense", card, "390")], auto_post_from=date(2026, 9, 1))
    paused = seed.definition([seed.line("expense", card, "100")], name="健身房", posting_mode="confirm", status="paused")
    overdue = seed.instance(rent, 4, date(2026, 9, 30))
    later = seed.instance(rent, 5, date(2026, 10, 20))
    seed.instance(netflix, 1, date(2026, 10, 5))
    failing = seed.instance(netflix, 2, date(2026, 10, 1), last_error="lines[0].account_id: 帳戶已封存")
    seed.instance(paused, 1, date(2026, 10, 2))

    items = read.list_instances(db_session, queue=True)

    assert _ids(items) == [overdue.id, failing.id, later.id]
    assert [item["overdue_days"] for item in items] == [3, 2, 0]
    first = items[0]
    assert (first["definition_name"], first["seq"], first["times"], first["posting_mode"]) == ("房租", 4, 12, "confirm")
    assert [(line["account_name"], line["amount"]) for line in first["lines"]] == [("範例卡", Decimal("-18000"))]
    assert first["totals"] == [{"currency": "TWD", "amount": Decimal("-18000")}]


def test_queue_adds_backlog_reopened_and_partial_items(db_session, seed, card, today):
    today(date(2026, 10, 3))
    netflix = seed.definition([seed.line("expense", card, "390")], auto_post_from=date(2026, 10, 3))
    ended = seed.definition([seed.line("expense", card, "1")], name="舊", status="ended")
    backlog = seed.instance(netflix, 1, date(2026, 9, 22))
    reopened = seed.instance(netflix, 2, date(2026, 10, 3), reopened_at=REOPENED)
    entry = seed.entry(card, "-390", day=date(2026, 12, 22), source="schedule")
    partial = seed.instance(netflix, 4, date(2026, 12, 22), status="posted", entries=[entry], is_partial=True)
    old_entry = seed.entry(card, "-1", day=date(2026, 9, 1), source="schedule")
    seed.instance(ended, 1, date(2026, 9, 1), status="posted", entries=[old_entry], is_partial=True)
    seed.instance(netflix, 3, date(2026, 11, 22))  # auto, after auto_post_from: the job's, not the queue's

    items = read.list_instances(db_session, queue=True)

    assert _ids(items) == [backlog.id, reopened.id, partial.id]
    assert items[1]["reopened"] is True
    assert (items[2]["status"], items[2]["is_partial"], items[2]["overdue_days"]) == ("posted", True, 0)


def test_instance_lines_are_signed_with_totals_per_currency(db_session, seed, card, today):
    today(date(2026, 10, 3))
    pay, broker, yen = seed.account("薪轉"), seed.account("交割"), seed.account("日幣", currency="JPY")
    definition = seed.definition(
        [
            seed.line("expense", card, "390"),
            seed.line("transfer", pay, "15000", to_account_id=broker.id),
            seed.line("income", yen, "5000"),
            seed.line("expense", card, "50"),
        ]
    )
    instance = seed.instance(definition, 1, date(2026, 10, 5), amount_override=["390", "15000", "5000", "0"])

    out = read.instance_out(db_session, instance)

    assert [(line["kind"], line["amount"], line["currency"]) for line in out["lines"]] == [
        ("expense", Decimal("-390"), "TWD"), ("transfer", Decimal("-15000"), "TWD"), ("income", Decimal("5000"), "JPY"),
    ]
    assert out["lines"][1]["to_account_name"] == "交割"
    assert out["totals"] == [{"currency": "JPY", "amount": Decimal("5000")}, {"currency": "TWD", "amount": Decimal("-15390")}]


def test_loan_detail_after_three_periods(db_session, seed, today):
    # Spec "Loan detail after three periods" (shape; the entry endpoint is wired in Task 14).
    today(date(2027, 1, 20))
    bank = seed.account("薪轉")
    lender = seed.counterparty("範例銀行")
    payable = seed.entry(bank, "300000", kind="payable", counterparty_id=lender.id)
    loan = seed.definition(
        [seed.line("repayment", bank, "8333", loan_entry_id=payable.id), seed.line("interest", bank, "620")],
        kind="installment", name="信貸 每月還款", anchor=date(2026, 11, 9), times=36, total_amount=Decimal("300000"),
    )
    for seq, day in enumerate((date(2026, 11, 9), date(2026, 12, 9), date(2027, 1, 9)), start=1):
        repayment = seed.entry(
            bank, "-8333", kind="payable", counterparty_id=lender.id, settles_entry_id=payable.id, is_settlement=True,
            day=day, source="schedule",
        )
        seed.instance(loan, seq, day, status="posted", entries=[repayment])
    seed.instance(loan, 4, date(2027, 2, 9))

    out = read.definition_out(db_session, loan)
    summary = read.loan_schedule_for(db_session, payable.id)

    assert (out["remaining"], out["repaid"], out["posted_count"], out["times"]) == (
        Decimal("-275001"), Decimal("24999"), 3, 36,
    )
    assert (out["next_due_date"], out["loan_entry_id"], out["needs_check"]) == (date(2027, 2, 9), payable.id, False)
    assert out["next_amount"] == [{"currency": "TWD", "amount": Decimal("-8953")}]
    assert (summary["remaining"], summary["repaid"], summary["posted_count"], summary["next_due_date"]) == (
        Decimal("-275001"), Decimal("24999"), 3, date(2027, 2, 9),
    )
    assert read.loan_schedule_for(db_session, payable.id + 1000) is None


def test_card_installment_remaining_from_posted_entries(db_session, seed, card, today):
    # Spec "Card installment remaining from posted entries".
    today(date(2026, 10, 20))
    phone = seed.definition(
        [seed.line("expense", card, "3333")], kind="installment", name="iPhone", anchor=date(2026, 10, 15), times=3,
        total_amount=Decimal("10000"),
    )
    edited = seed.entry(card, "-3300", day=date(2026, 10, 15), source="schedule")
    seed.instance(phone, 1, date(2026, 10, 15), status="posted", entries=[edited])
    seed.instance(phone, 2, date(2026, 11, 15))
    assert read.loan_summary(db_session, phone) == (Decimal("6700"), None)
    assert read.definition_out(db_session, phone)["remaining"] == Decimal("6700")


def test_needs_check_and_failing(db_session, seed, today):
    today(date(2026, 10, 3))
    bank = seed.account("薪轉")
    lender = seed.counterparty("範例銀行")
    payable = seed.entry(bank, "8333", kind="payable", counterparty_id=lender.id)
    ended = seed.definition(
        [seed.line("repayment", bank, "8333", loan_entry_id=payable.id)], kind="installment", name="舊貸款",
        times=2, status="ended",
    )
    reviewed = seed.definition([seed.line("expense", bank, "1")], name="待檢查", review_reason="interval_mismatch")
    failing = seed.definition([seed.line("expense", bank, "1")], name="失敗")
    first = seed.instance(failing, 1, date(2026, 10, 1), last_error="lines[0].account_id: 帳戶已封存")
    seed.instance(failing, 2, date(2026, 11, 1), last_error="lines[0].account_id: 帳戶已封存")
    assert read.definition_out(db_session, ended)["needs_check"] is True
    assert read.definition_out(db_session, reviewed)["needs_check"] is True
    out = read.definition_out(db_session, failing)
    assert (out["needs_check"], out["failing"]) == (
        False, {"instance_id": first.id, "due_date": date(2026, 10, 1), "last_error": "lines[0].account_id: 帳戶已封存"},
    )


def test_definitions_are_ordered_by_next_due_date_and_filtered(db_session, seed, card, today, monkeypatch):
    today(date(2026, 10, 3))
    monkeypatch.delenv("ACCOUNTING_IMPORT_LOCKED", raising=False)
    late = seed.definition([seed.line("expense", card, "1")], name="晚")
    early = seed.definition([seed.line("expense", card, "1")], name="早", kind="installment", times=2, created_locally=False, moze_id="I-9")
    none = seed.definition([seed.line("expense", card, "1")], name="沒有下期", status="ended")
    seed.instance(late, 1, date(2026, 11, 1))
    seed.instance(early, 1, date(2026, 10, 10))
    assert [item["name"] for item in read.list_definitions(db_session)] == ["早", "晚", "沒有下期"]
    assert [item["name"] for item in read.list_definitions(db_session, status="ended")] == ["沒有下期"]
    assert [item["name"] for item in read.list_definitions(db_session, kind="installment")] == ["早"]
    imported = read.list_definitions(db_session, kind="installment")[0]
    assert (imported["imported"], imported["locked"], imported["created_locally"]) == (True, True, False)
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")
    assert read.list_definitions(db_session, kind="installment")[0]["locked"] is False


def test_schedule_links_map_every_posted_entry(db_session, seed, card):
    # Spec "Pill data on a posted entry".
    phone = seed.definition(
        [seed.line("expense", card, "1000"), seed.line("interest", card, "10")], kind="installment", name="分期",
        times=36,
    )
    expense = seed.entry(card, "-1000", source="schedule")
    interest = seed.entry(card, "-10", kind="interest", source="schedule")
    other = seed.entry(card, "-5")
    fifth = seed.instance(phone, 5, date(2027, 2, 22), status="posted", entries=[expense, interest])

    links = read.schedule_links(db_session, [expense.id, interest.id, other.id])

    assert set(links) == {expense.id, interest.id}
    assert links[expense.id] == {
        "definition_id": phone.id, "instance_id": fifth.id, "kind": "installment", "seq": 5, "times": 36,
        "name": "分期", "is_partial": False, "acted_by": "auto", "posted_entry_ids": [expense.id, interest.id],
    }
    assert read.schedule_links(db_session, []) == {}


def test_plain_listing_defaults_to_pending_within_thirty_days(db_session, seed, card, today):
    today(date(2026, 10, 3))
    netflix = seed.definition([seed.line("expense", card, "390")])
    soon = seed.instance(netflix, 1, date(2026, 10, 22))
    seed.instance(netflix, 2, date(2026, 11, 22))
    entry = seed.entry(card, "-390", day=date(2026, 9, 22), source="schedule")
    posted = seed.instance(netflix, 3, date(2026, 9, 22), status="posted", entries=[entry])
    assert _ids(read.list_instances(db_session)) == [soon.id]
    assert _ids(read.list_instances(db_session, status="posted", date_from=date(2026, 9, 1))) == [posted.id]
    assert len(read.list_instances(db_session, status=None, until=date(2026, 12, 31), definition_id=netflix.id)) == 3
    assert POSTED_AT.tzinfo is not None
