"""D32 lock order under two overlapping transactions (tests.helpers.race)."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import LedgerEntry, ScheduleDefinition, ScheduleInstance
from app.schemas.schedules import DefinitionUpdateIn
from app.services import entry_write_service as ews
from app.services import schedule_posting as posting
from app.services import schedule_service
from app.services.errors import ConflictError
from app.services.moze_import_service import import_lock
from tests.helpers import race


@pytest.fixture()
def period(seed, db_session, today):
    """A posted loan period (repayment + interest) on 2026-11-09; today is 2026-11-10."""
    today(date(2026, 11, 10))
    bank = seed.account("薪轉")
    lender = seed.counterparty("範例銀行")
    payable = seed.entry(bank, "300000", kind="payable", counterparty_id=lender.id)
    definition = seed.definition(
        [seed.line("repayment", bank, "8333", loan_entry_id=payable.id), seed.line("interest", bank, "620")],
        kind="installment", name="信貸 每月還款", anchor=date(2026, 11, 9), times=36,
    )
    instance = seed.instance(definition, 1, date(2026, 11, 9))
    repayment_id, interest_id = posting.post_instance(db_session, instance.id, actor="auto").entry_ids
    db_session.commit()
    return {
        "bank": bank, "definition": definition, "instance": instance, "payable": payable, "repayment": repayment_id,
        "interest": interest_id,
    }


def _row(db, instance_id) -> ScheduleInstance:
    db.expire_all()
    return db.get(ScheduleInstance, instance_id)


def test_repost_waits_for_a_delete_of_its_entry(pg_engine, db_session, period):
    outcome = race(
        pg_engine,
        lambda db: ews.delete_entry(db, period["interest"]),
        lambda db: schedule_service.repost_instance(db, period["instance"].id, ["8333", "598"]),
    )
    assert outcome == "committed"
    row = _row(db_session, period["instance"].id)
    assert (row.status, row.is_partial) == ("posted", False)
    amounts = sorted(db_session.get(LedgerEntry, entry_id).amount for entry_id in row.posted_entry_ids)
    assert amounts == [Decimal("-8333"), Decimal("-598")]


def test_definition_edit_waits_for_a_post(pg_engine, db_session, seed, today):
    today(date(2026, 10, 22))
    card = seed.account("範例卡")
    definition = seed.definition([seed.line("expense", card, "390")])
    instance = seed.instance(definition, 1, date(2026, 10, 22))
    db_session.commit()
    body = DefinitionUpdateIn(
        name="Netflix", template={"lines": [{"kind": "expense", "account_id": card.id, "amount": "420", "currency": "TWD"}]},
        interval_unit="month", anchor_date=date(2026, 10, 22),
    )
    outcome = race(
        pg_engine,
        lambda db: posting.post_instance(db, instance.id, actor="owner"),
        lambda db: schedule_service.update_definition(db, definition.id, body),
    )
    assert outcome == "committed"
    row = _row(db_session, instance.id)
    assert row.status == "posted"
    assert db_session.get(LedgerEntry, row.posted_entry_ids[0]).amount == Decimal("-390")


def test_import_waits_for_a_post(pg_engine, db_session, seed, today):
    today(date(2026, 10, 22))
    card = seed.account("範例卡")
    definition = seed.definition([seed.line("expense", card, "390")])
    instance = seed.instance(definition, 1, date(2026, 10, 22))
    db_session.commit()

    def take_the_import_lock(_db):
        with import_lock(pg_engine):
            pass

    outcome = race(pg_engine, lambda db: posting.post_instance(db, instance.id, actor="owner"), take_the_import_lock)
    assert outcome == "committed"
    assert _row(db_session, instance.id).status == "posted"


def test_two_deletes_of_one_period_queue(pg_engine, db_session, period):
    outcome = race(
        pg_engine,
        lambda db: ews.delete_entry(db, period["interest"]),
        lambda db: ews.delete_entry(db, period["repayment"]),
    )
    assert outcome == "committed"
    row = _row(db_session, period["instance"].id)
    assert (row.status, row.posted_entry_ids, row.reopened_at is not None) == ("pending", [], True)


def _definition(db, definition_id) -> ScheduleDefinition:
    db.expire_all()
    return db.get(ScheduleDefinition, definition_id)


def test_loan_posts_of_neighbouring_periods_queue_without_deadlock(pg_engine, db_session, seed, period):
    # Loan post of k locks k+1… before the loan; a post of k+1 waits on the definition (FOR UPDATE) instead of
    # holding k+1 while it waits for the loan.
    second = seed.instance(period["definition"], 2, date(2026, 12, 9))
    third = seed.instance(period["definition"], 3, date(2027, 1, 9))
    db_session.commit()
    outcome = race(
        pg_engine,
        lambda db: posting.post_instance(db, second.id, actor="owner"),
        lambda db: posting.post_instance(db, third.id, actor="owner"),
    )
    assert outcome == "committed"
    assert [_row(db_session, row.id).status for row in (second, third)] == ["posted", "posted"]


def test_two_deletes_reviving_an_ended_definition_queue(pg_engine, db_session, period):
    # Both deletes may revive the definition, so both take it FOR UPDATE; two FOR SHARE upgrades would deadlock.
    definition = db_session.get(ScheduleDefinition, period["definition"].id)
    definition.status = "ended"
    db_session.commit()
    outcome = race(
        pg_engine,
        lambda db: ews.delete_entry(db, period["interest"]),
        lambda db: ews.delete_entry(db, period["repayment"]),
    )
    assert outcome == "committed"
    assert _row(db_session, period["instance"].id).status == "pending"
    assert _definition(db_session, period["definition"].id).status == "active"


def test_loan_close_out_waits_for_a_delete_that_revives_the_definition(pg_engine, db_session, seed, period):
    # A loan post that closes out (open amount 0) and an entry delete that revives an ended definition both write
    # the definition; each holds it FOR UPDATE, so one waits for the other.
    seed.entry(
        period["bank"], "-291667", kind="payable", counterparty_id=period["payable"].counterparty_id,
        settles_entry_id=period["payable"].id, is_settlement=True,
    )
    pending = seed.instance(period["definition"], 2, date(2026, 12, 9))
    definition = db_session.get(ScheduleDefinition, period["definition"].id)
    definition.status = "ended"
    db_session.commit()
    outcome = race(
        pg_engine,
        lambda db: ews.delete_entry(db, period["interest"]),
        lambda db: posting.post_instance(db, pending.id, actor="owner"),
    )
    assert outcome == "committed"
    assert (_row(db_session, pending.id).status, _row(db_session, pending.id).note) == ("skipped", "貸款已結清")
    assert _definition(db_session, period["definition"].id).status == "ended"


def test_delete_after_a_loan_close_out_is_refused_with_a_retry_message(pg_engine, db_session, seed, period):
    # The reverse order: the close-out holds the definition FOR UPDATE and ends it; the delete read the definition
    # active, waits on FOR SHARE, then finds it ended and answers 409 with the owner's retry text (no lost revive).
    seed.entry(
        period["bank"], "-291667", kind="payable", counterparty_id=period["payable"].counterparty_id,
        settles_entry_id=period["payable"].id, is_settlement=True,
    )
    pending = seed.instance(period["definition"], 2, date(2026, 12, 9))
    db_session.commit()
    outcome = race(
        pg_engine,
        lambda db: posting.post_instance(db, pending.id, actor="owner"),
        lambda db: ews.delete_entry(db, period["interest"]),
    )
    assert isinstance(outcome, ConflictError) and str(outcome) == "排程剛結束，請重試刪除"
    assert _definition(db_session, period["definition"].id).status == "ended"
    assert sorted(_row(db_session, period["instance"].id).posted_entry_ids) == sorted(
        [period["repayment"], period["interest"]]
    )

    ews.delete_entry(db_session, period["interest"])  # the retry takes the definition FOR UPDATE and revives it
    db_session.commit()
    row = _row(db_session, period["instance"].id)
    assert (row.status, row.is_partial, row.posted_entry_ids) == ("posted", True, [period["repayment"]])
    assert _definition(db_session, period["definition"].id).status == "active"


def test_loan_delete_waits_for_a_repost_on_an_ended_definition(pg_engine, db_session, period):
    # The repost locks the loan in the same statement as the period's entries, so a DELETE of the loan (allowed once
    # the definition ended) waits for it instead of holding the loan while the repost waits for the repayment.
    definition = db_session.get(ScheduleDefinition, period["definition"].id)
    definition.status = "ended"
    db_session.commit()
    payable_id = period["payable"].id  # read before the race: the row is gone afterwards, so a refresh would fail
    outcome = race(
        pg_engine,
        lambda db: schedule_service.repost_instance(db, period["instance"].id, ["8000", "620"]),
        lambda db: ews.delete_entry(db, payable_id),
    )
    assert outcome == "committed"
    db_session.expire_all()
    assert db_session.get(LedgerEntry, payable_id) is None
    row = _row(db_session, period["instance"].id)
    repayment = next(
        db_session.get(LedgerEntry, entry_id) for entry_id in row.posted_entry_ids
        if db_session.get(LedgerEntry, entry_id).kind == "payable"
    )
    assert (repayment.amount, repayment.settles_entry_id) == (Decimal("-8000"), None)
