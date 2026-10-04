"""Instance generation (spec "Instance generation", design D30)."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.models import ScheduleInstance
from app.services import schedule_generation as generation
from app.services.moze_import_service import IMPORT_LOCK_KEY
from app.services.schedule_locks import ImportRunningError
from tests.helpers import POSTED_AT


def _instances(db, definition) -> list[ScheduleInstance]:
    db.flush()
    db.expire_all()
    return list(
        db.scalars(select(ScheduleInstance).where(ScheduleInstance.definition_id == definition.id).order_by(ScheduleInstance.seq))
    )


def _dues(db, definition) -> list[date]:
    return [row.due_date for row in _instances(db, definition)]


@pytest.fixture()
def netflix(seed):
    card = seed.account("範例卡")
    return seed.definition([seed.line("expense", card, "390")], anchor=date(2026, 10, 22))


def test_thirteen_month_horizon(db_session, netflix):
    # Spec "13-month horizon".
    assert generation.generate(db_session, netflix, date(2026, 10, 3)) == 13
    rows = _instances(db_session, netflix)
    assert [row.seq for row in rows] == list(range(1, 14))
    assert (rows[0].due_date, rows[-1].due_date) == (date(2026, 10, 22), date(2027, 10, 22))
    assert all(row.rule_date == row.due_date and row.status == "pending" for row in rows)
    assert netflix.generated_until == date(2027, 11, 3)


def test_month_end_anchor_generates_feb_28_then_returns_to_31(db_session, seed):
    # Spec "Month-end anchor clamps and returns"; Review Focus 1.
    wallet = seed.account()
    rent = seed.definition([seed.line("expense", wallet, "100")], anchor=date(2026, 1, 31), auto_post_from=date(2026, 1, 10))
    generation.generate(db_session, rent, date(2026, 1, 10))
    dues = _dues(db_session, rent)
    assert dues[:4] == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]
    assert date(2026, 10, 31) in dues and date(2026, 11, 30) in dues


def test_finite_run(db_session, seed):
    # Spec "Finite run".
    wallet = seed.account()
    finite = seed.definition([seed.line("expense", wallet, "100")], anchor=date(2026, 11, 9), times=3)
    assert generation.generate(db_session, finite, date(2026, 10, 3)) == 3
    assert _dues(db_session, finite) == [date(2026, 11, 9), date(2026, 12, 9), date(2027, 1, 9)]


def test_end_date_stops_the_run(db_session, seed):
    # Spec "End date stops the run".
    wallet = seed.account()
    weekly = seed.definition(
        [seed.line("expense", wallet, "100")], interval_unit="week", anchor=date(2026, 10, 5), end_date=date(2026, 10, 26)
    )
    generation.generate(db_session, weekly, date(2026, 10, 3))
    assert _dues(db_session, weekly) == [date(2026, 10, 5), date(2026, 10, 12), date(2026, 10, 19), date(2026, 10, 26)]


def test_rolling_forward(db_session, netflix):
    # Spec "Rolling forward".
    generation.generate(db_session, netflix, date(2026, 10, 3))
    assert generation.generate(db_session, netflix, date(2026, 10, 21)) == 0
    assert max(row.seq for row in _instances(db_session, netflix)) == 13
    assert generation.generate(db_session, netflix, date(2026, 10, 22)) == 1
    last = _instances(db_session, netflix)[-1]
    assert (last.seq, last.due_date) == (14, date(2027, 11, 22))


def test_a_period_moved_later_does_not_shift_the_series(db_session, netflix):
    # Spec "A period moved later does not shift the series".
    generation.generate(db_session, netflix, date(2026, 10, 3))
    thirteen = _instances(db_session, netflix)[-1]
    thirteen.due_date = date(2027, 10, 25)
    db_session.flush()
    generation.generate(db_session, netflix, date(2026, 10, 22))
    rows = _instances(db_session, netflix)
    assert (rows[-1].seq, rows[-1].rule_date, rows[-1].due_date) == (14, date(2027, 11, 22), date(2027, 11, 22))
    assert (rows[12].rule_date, rows[12].due_date) == (date(2027, 10, 22), date(2027, 10, 25))


def test_a_period_moved_earlier_does_not_repeat_the_series(db_session, netflix):
    # Spec "A period moved earlier does not repeat the series".
    generation.generate(db_session, netflix, date(2026, 10, 3))
    thirteen = _instances(db_session, netflix)[-1]
    thirteen.due_date = date(2027, 10, 15)
    db_session.flush()
    generation.generate(db_session, netflix, date(2026, 10, 22))
    rows = _instances(db_session, netflix)
    assert (rows[-1].seq, rows[-1].due_date) == (14, date(2027, 11, 22))
    assert date(2027, 10, 22) not in [row.due_date for row in rows]


def test_a_posted_day_is_passed_over_without_a_seq(db_session, seed):
    wallet = seed.account()
    entry = seed.entry(wallet, "-390", day=date(2026, 11, 22), source="schedule")
    definition = seed.definition([seed.line("expense", wallet, "390")], anchor=date(2026, 10, 22), times=3)
    seed.instance(definition, 1, date(2026, 11, 22), rule_date=date(2026, 10, 22), status="posted", entries=[entry])
    generation.generate(db_session, definition, date(2026, 10, 3))
    rows = _instances(db_session, definition)
    assert [(row.seq, row.rule_date, row.due_date) for row in rows] == [
        (1, date(2026, 10, 22), date(2026, 11, 22)),
        (2, date(2026, 12, 22), date(2026, 12, 22)),
        (3, date(2027, 1, 22), date(2027, 1, 22)),
    ]


def test_paused_definitions_generate_and_ended_ones_do_not(db_session, seed):
    wallet = seed.account()
    paused = seed.definition([seed.line("expense", wallet, "1")], status="paused")
    ended = seed.definition([seed.line("expense", wallet, "1")], name="舊訂閱", status="ended")
    assert generation.generate(db_session, paused, date(2026, 10, 3)) == 13
    assert generation.generate(db_session, ended, date(2026, 10, 3)) == 0
    assert ended.generated_until is None


def test_installment_last_period_gets_the_remainder(db_session, seed):
    # Spec "Card installment remainder on the last period" (override set at generation, whenever seq == times).
    card, bank = seed.account("範例卡"), seed.account("薪轉")
    phone = seed.definition(
        [seed.line("expense", card, "3333")], kind="installment", anchor=date(2026, 10, 15), times=3,
        total_amount=Decimal("10000"),
    )
    generation.generate(db_session, phone, date(2026, 10, 3))
    assert [row.amount_override for row in _instances(db_session, phone)] == [None, None, ["3334"]]

    loan = seed.definition(
        [seed.line("repayment", bank, "8333"), seed.line("interest", bank, "620")], kind="installment",
        name="信貸 每月還款", anchor=date(2026, 11, 9), times=36, total_amount=Decimal("300000"),
    )
    assert generation.generate(db_session, loan, date(2026, 10, 3)) == 12
    assert all(row.amount_override is None for row in _instances(db_session, loan))
    assert generation.generate(db_session, loan, date(2029, 1, 1)) == 24
    assert _instances(db_session, loan)[-1].amount_override == ["8345", "620"]

    imported = seed.definition(
        [seed.line("expense", card, "3333")], kind="installment", name="MOZE 分期", anchor=date(2026, 10, 15), times=3,
        total_amount=Decimal("10000"), created_locally=False, moze_id="I-1",
    )
    generation.generate(db_session, imported, date(2026, 10, 3))
    assert _instances(db_session, imported)[-1].amount_override is None


def test_definition_ends_when_its_last_period_exists_and_nothing_is_open(db_session, seed):
    wallet = seed.account()
    twice = seed.definition([seed.line("expense", wallet, "1")], times=2)
    generation.generate(db_session, twice, date(2026, 10, 3))
    assert generation.end_if_complete(db_session, twice) is False
    for row in _instances(db_session, twice):
        row.status, row.acted_at, row.acted_by = "skipped", POSTED_AT, "owner"
    db_session.flush()
    assert generation.end_if_complete(db_session, twice) is True
    assert twice.status == "ended"

    once = seed.definition([seed.line("expense", wallet, "1")], name="一次", times=1)
    entry = seed.entry(wallet, "-1", day=date(2026, 10, 22), source="schedule")
    seed.instance(once, 1, date(2026, 10, 22), status="posted", entries=[entry], is_partial=True)
    assert generation.end_if_complete(db_session, once) is False


def test_generate_locked_takes_the_shared_import_key(db_session, seed, pg_engine):
    wallet = seed.account()
    definition = seed.definition([seed.line("expense", wallet, "1")])
    db_session.commit()
    with pg_engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": IMPORT_LOCK_KEY})
        try:
            with pytest.raises(ImportRunningError):
                generation.generate_locked(db_session, definition.id, date(2026, 10, 3))
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": IMPORT_LOCK_KEY})
            holder.commit()
    db_session.rollback()
    assert generation.generate_locked(db_session, definition.id, date(2026, 10, 3)) == 13
