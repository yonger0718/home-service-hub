"""Posting an instance (spec "Posting an instance", design D31)."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.models import EntryGroup, LedgerEntry, ScheduleInstance
from app.services import entry_write_service as ews
from app.services import ledger_service, settlement_service
from app.services import schedule_locks as locks
from app.services import schedule_posting as posting
from app.services.edit_lock import EditLockedError
from app.services.errors import ConflictError, ValidationError


@pytest.fixture()
def loan(seed):
    bank = seed.account("薪轉", opening="50000")
    lender = seed.counterparty("範例銀行")
    payable = seed.entry(bank, "300000", kind="payable", counterparty_id=lender.id, name="信貸", day=date(2026, 10, 3))
    definition = seed.definition(
        [seed.line("repayment", bank, "8333", loan_entry_id=payable.id), seed.line("interest", bank, "620")],
        kind="installment", name="信貸 每月還款", anchor=date(2026, 11, 9), times=36, total_amount=Decimal("300000"),
    )
    return SimpleNamespace(bank=bank, lender=lender, payable=payable, definition=definition)


def _balance(db, account) -> Decimal:
    db.expire_all()
    return ledger_service.account_balance(db, account.id)


def _open(db, entry) -> Decimal:
    db.expire_all()
    return settlement_service.open_amount(db, db.get(LedgerEntry, entry.id))


def _schedule_entries(db) -> int:
    return db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.source == "schedule"))


def test_repayment_with_interest(db_session, seed, loan, today):
    # Spec "Repayment with interest" and the ledger scenario "Loan interest names the lender".
    today(date(2026, 11, 9))
    instance = seed.instance(loan.definition, 1, date(2026, 11, 9))
    before = _balance(db_session, loan.bank)

    result = posting.post_instance(db_session, instance.id, actor="owner")
    db_session.commit()

    assert result.outcome == "posted"
    assert _balance(db_session, loan.bank) - before == Decimal("-8953")
    repayment, interest = (db_session.get(LedgerEntry, entry_id) for entry_id in result.entry_ids)
    assert (repayment.kind, repayment.amount, repayment.settles_entry_id, repayment.is_settlement) == (
        "payable", Decimal("-8333"), loan.payable.id, True,
    )
    assert (repayment.entry_date, repayment.posted_date, repayment.entry_time) == (date(2026, 11, 9), date(2026, 11, 9), None)
    assert (interest.kind, interest.amount, interest.counterparty_id) == ("interest", Decimal("-620"), loan.lender.id)
    assert interest.category_id == ews.system_category_id(db_session, "interest")
    assert (repayment.source, interest.source) == ("schedule", "schedule")
    group = db_session.get(EntryGroup, repayment.group_id)
    assert (group.kind, group.name, interest.group_id) == ("installment", "信貸 每月還款 #1/36", group.id)
    assert _open(db_session, loan.payable) == Decimal("291667")
    db_session.refresh(instance)
    assert (instance.status, instance.acted_by, instance.posted_entry_ids, instance.is_partial) == (
        "posted", "owner", list(result.entry_ids), False,
    )


def test_last_repayment_clamped_to_the_open_amount(db_session, seed, loan, today):
    # Spec "Last repayment clamped to the open amount".
    for amount in ("-291655", "-100"):
        seed.entry(
            loan.bank, amount, kind="payable", counterparty_id=loan.lender.id, settles_entry_id=loan.payable.id,
            is_settlement=True,
        )
    assert _open(db_session, loan.payable) == Decimal("8245")
    today(date(2029, 10, 9))
    instance = seed.instance(loan.definition, 36, date(2029, 10, 9), amount_override=["8345", "620"])

    result = posting.post_instance(db_session, instance.id, actor="auto")

    repayment, interest = (db_session.get(LedgerEntry, entry_id) for entry_id in result.entry_ids)
    assert (repayment.amount, interest.amount) == (Decimal("-8245"), Decimal("-620"))
    assert _open(db_session, loan.payable) == 0


def test_posting_twice_is_refused(db_session, seed, loan, today):
    # Spec "Posting twice is refused".
    today(date(2026, 11, 9))
    instance = seed.instance(loan.definition, 1, date(2026, 11, 9))
    posting.post_instance(db_session, instance.id, actor="owner")
    db_session.commit()
    written = _schedule_entries(db_session)
    with pytest.raises(ConflictError, match="already_posted"):
        posting.post_instance(db_session, instance.id, actor="owner")
    db_session.rollback()
    assert _schedule_entries(db_session) == written


def test_closed_loan_ends_the_schedule(db_session, seed, loan, today):
    # Spec "Closed loan ends the schedule".
    loan.payable.is_closed = True
    days = [date(2026, 12, 9)] + [date(2027, month, 9) for month in range(1, 11)]
    instances = [seed.instance(loan.definition, seq, day) for seq, day in enumerate(days, start=2)]
    today(date(2026, 12, 9))

    result = posting.post_instance(db_session, instances[0].id, actor="auto", job=True, today=date(2026, 12, 9))
    db_session.commit()

    assert result.outcome == "loan_closed"
    assert _schedule_entries(db_session) == 0
    db_session.expire_all()
    for instance in instances:
        row = db_session.get(ScheduleInstance, instance.id)
        assert (row.status, row.acted_by, row.note) == ("skipped", "auto", "貸款已結清")
    assert loan.definition.status == "ended"


def test_loan_settled_by_hand_while_pending_skips_the_rest_and_ends(db_session, seed, loan, today):
    # Review Focus 4: the owner repaid the rest by hand while the next period waited.
    seed.entry(
        loan.bank, "-300000", kind="payable", counterparty_id=loan.lender.id, settles_entry_id=loan.payable.id,
        is_settlement=True,
    )
    first = seed.instance(loan.definition, 1, date(2026, 11, 9))
    second = seed.instance(loan.definition, 2, date(2026, 12, 9))
    today(date(2026, 11, 9))

    result = posting.post_instance(db_session, first.id, actor="owner")
    db_session.commit()

    assert result.outcome == "loan_closed"
    db_session.expire_all()
    assert [db_session.get(ScheduleInstance, row.id).status for row in (first, second)] == ["skipped", "skipped"]
    assert db_session.get(ScheduleInstance, first.id).acted_by == "owner"
    assert loan.definition.status == "ended"
    assert _schedule_entries(db_session) == 0


def test_archived_account_found_at_posting_time_names_the_line(db_session, seed, today):
    # Spec "Archived account found at posting time" (the reopen half is in Task 12).
    card, old = seed.account("範例卡"), seed.account("舊帳戶")
    definition = seed.definition([seed.line("expense", card, "390"), seed.line("expense", old, "620")])
    instance = seed.instance(definition, 1, date(2026, 10, 22))
    old.is_archived = True
    db_session.commit()
    today(date(2026, 10, 22))

    with pytest.raises(ValidationError) as exc:
        posting.post_instance(db_session, instance.id, actor="owner")
    assert exc.value.field == "lines[1].account_id"
    posting.record_failure(db_session, instance.id, posting.failure_message(exc.value))

    db_session.expire_all()
    row = db_session.get(ScheduleInstance, instance.id)
    assert row.status == "pending"
    assert row.last_error.startswith("lines[1].account_id")
    assert "620" not in row.last_error and row.last_error_at is not None
    assert _schedule_entries(db_session) == 0


def test_zero_override_leaves_a_line_out(db_session, seed, loan, today):
    # Spec "Zero override leaves a line out".
    today(date(2026, 11, 9))
    instance = seed.instance(loan.definition, 1, date(2026, 11, 9), amount_override=["8333", "0"])
    result = posting.post_instance(db_session, instance.id, actor="owner")
    assert len(result.entry_ids) == 1
    repayment = db_session.get(LedgerEntry, result.entry_ids[0])
    assert (repayment.amount, repayment.group_id) == (Decimal("-8333"), None)


def test_recurring_transfer(db_session, seed, today):
    # Spec "Recurring transfer".
    pay, broker = seed.account("薪轉"), seed.account("交割")
    definition = seed.definition(
        [seed.line("transfer", pay, "15000", to_account_id=broker.id)], name="定期轉交割", anchor=date(2026, 10, 5)
    )
    instance = seed.instance(definition, 1, date(2026, 10, 5))
    today(date(2026, 10, 5))

    result = posting.post_instance(db_session, instance.id, actor="auto")

    out_leg, in_leg = (db_session.get(LedgerEntry, entry_id) for entry_id in result.entry_ids)
    assert (out_leg.kind, out_leg.amount, in_leg.kind, in_leg.amount) == (
        "transfer_out", Decimal("-15000"), "transfer_in", Decimal("15000"),
    )
    assert out_leg.transfer_group_id == in_leg.transfer_group_id is not None
    assert out_leg.entry_date == in_leg.entry_date == date(2026, 10, 5)
    assert out_leg.group_id is None


def test_expense_line_takes_the_definition_name_and_never_moves_category_defaults(db_session, seed, today):
    card = seed.account("範例卡")
    streaming = seed.category("串流")
    definition = seed.definition(
        [seed.line("expense", card, "390", category_id=streaming.id)], description="家庭方案", tags=["訂閱"]
    )
    instance = seed.instance(definition, 1, date(2026, 10, 22))
    today(date(2026, 10, 22))

    result = posting.post_instance(db_session, instance.id, actor="auto")

    entry = db_session.get(LedgerEntry, result.entry_ids[0])
    assert (entry.name, entry.description, entry.tags, entry.amount, entry.source) == (
        "Netflix", "家庭方案", ["訂閱"], Decimal("-390"), "schedule",
    )
    db_session.refresh(streaming)
    assert streaming.default_account_id is None


def test_imported_period_posts_against_an_imported_loan(db_session, seed, today, monkeypatch):
    # Spec "Imported period posts against an imported loan".
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "false")
    bank = seed.account("薪轉")
    lender = seed.counterparty("範例銀行")
    payable = seed.entry(bank, "300000", kind="payable", counterparty_id=lender.id, source="moze_backup", moze_id="R-LOAN")
    definition = seed.definition(
        [seed.line("repayment", bank, "8333", loan_entry_id=payable.id)], kind="installment", name="信貸",
        anchor=date(2026, 2, 9), times=36, created_locally=False, moze_id="I-1",
    )
    instance = seed.instance(definition, 9, date(2026, 10, 9), moze_id="R-9")
    today(date(2026, 10, 9))

    result = posting.post_instance(db_session, instance.id, actor="auto", job=True, today=date(2026, 10, 9))

    settlement = db_session.get(LedgerEntry, result.entry_ids[0])
    assert (settlement.source, settlement.settles_entry_id, settlement.amount) == ("schedule", payable.id, Decimal("-8333"))


def test_the_job_leaves_an_instance_the_owner_is_acting_on(db_session, seed, pg_engine):
    card = seed.account("範例卡")
    definition = seed.definition([seed.line("expense", card, "390")])
    instance = seed.instance(definition, 1, date(2026, 10, 22))
    db_session.commit()
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    owner, job = factory(), factory()
    try:
        assert locks.lock_instance(owner, instance.id) is not None
        result = posting.post_instance(job, instance.id, actor="auto", job=True, today=date(2026, 10, 22))
        assert result.outcome == "skipped_locked"
    finally:
        owner.rollback()
        job.rollback()
        owner.close()
        job.close()


def test_the_job_rechecks_eligibility_under_the_lock(db_session, seed):
    card = seed.account("範例卡")
    auto = seed.definition([seed.line("expense", card, "390")], auto_post_from=date(2026, 10, 3))
    confirm = seed.definition([seed.line("expense", card, "390")], name="房租", posting_mode="confirm")
    backlog = seed.instance(auto, 1, date(2026, 10, 1))
    reopened = seed.instance(auto, 2, date(2026, 10, 3), reopened_at=locks_now())
    confirmed = seed.instance(confirm, 1, date(2026, 10, 3))
    for instance in (backlog, reopened, confirmed):
        result = posting.post_instance(db_session, instance.id, actor="auto", job=True, today=date(2026, 10, 3))
        assert result.outcome == "not_due"
    assert _schedule_entries(db_session) == 0


def test_delete_period_entries_takes_both_legs_empties_the_group_and_respects_the_cutover_lock(db_session, seed, loan, today):
    today(date(2026, 11, 9))
    instance = seed.instance(loan.definition, 1, date(2026, 11, 9))
    result = posting.post_instance(db_session, instance.id, actor="owner")
    group_id = db_session.get(LedgerEntry, result.entry_ids[0]).group_id
    posting.delete_period_entries(db_session, list(result.entry_ids))
    assert _schedule_entries(db_session) == 0
    assert db_session.get(EntryGroup, group_id) is None

    imported = seed.entry(loan.bank, "-390", source="moze_backup", moze_id="R-X")
    with pytest.raises(EditLockedError):
        posting.delete_period_entries(db_session, [imported.id])


def test_delete_period_entries_locks_the_loan_in_the_same_statement_and_keeps_it(db_session, seed, loan, today):
    # Repost: the loan joins the period's single entry-lock statement and is handed back, never deleted.
    today(date(2026, 11, 9))
    instance = seed.instance(loan.definition, 1, date(2026, 11, 9))
    result = posting.post_instance(db_session, instance.id, actor="owner")

    kept = posting.delete_period_entries(db_session, list(result.entry_ids), also_lock=loan.payable.id)

    assert kept is not None and kept.id == loan.payable.id
    assert _schedule_entries(db_session) == 0
    assert db_session.get(LedgerEntry, loan.payable.id) is not None
    assert _open(db_session, loan.payable) == Decimal("300000")


def test_posting_a_loan_period_holds_the_definition_for_update_and_locks_later_periods(db_session, seed, loan, pg_engine):
    # D32: a loan post may end the definition, so it takes FOR UPDATE (not FOR SHARE), and it locks the later
    # pending periods before the loan entry.
    first = seed.instance(loan.definition, 1, date(2026, 11, 9))
    second = seed.instance(loan.definition, 2, date(2026, 12, 9))
    db_session.commit()
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    owner, probe = factory(), factory()
    try:
        definition = posting.lock_definition_for_post(owner, loan.definition.id)
        instance = locks.lock_instance(owner, first.id)
        posting.post_locked(owner, definition, instance, "owner")
        probe.execute(text("SET LOCAL lock_timeout = '200ms'"))
        with pytest.raises(OperationalError):
            probe.execute(
                text("SELECT id FROM schedule_definition WHERE id = :id FOR SHARE"), {"id": loan.definition.id}
            )
        probe.rollback()
        probe.execute(text("SET LOCAL lock_timeout = '200ms'"))
        with pytest.raises(OperationalError):
            probe.execute(text("SELECT id FROM schedule_instance WHERE id = :id FOR UPDATE"), {"id": second.id})
    finally:
        owner.rollback()
        probe.rollback()
        owner.close()
        probe.close()


def locks_now():
    from datetime import datetime, timezone

    return datetime(2026, 10, 2, tzinfo=timezone.utc)
