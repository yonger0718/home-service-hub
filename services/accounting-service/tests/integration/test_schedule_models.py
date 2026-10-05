"""The ORM models alone (Base.metadata.create_all) build the schedule tables (design D28).

test_migration.py checks that the Alembic revision produces the same schema.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, delete, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.models import ENTRY_SOURCES, Account, LedgerEntry, ScheduleDefinition, ScheduleInstance

NOW = datetime(2026, 10, 3, 1, 0, tzinfo=timezone.utc)
LINE = {
    "kind": "expense", "account_id": 1, "to_account_id": None, "to_amount": None, "counterparty_id": None,
    "category_id": None, "project_id": None, "amount": "390", "currency": "TWD", "loan_entry_id": None,
    "name": None, "merchant": None,
}
TEMPLATE = {"lines": [LINE], "description": None, "tags": []}


@pytest.fixture()
def model_engine(database_factory):
    engine = create_engine(database_factory())
    with engine.begin() as conn:
        conn.execute(text("CREATE SEQUENCE ledger_entry_seq_seq AS BIGINT"))
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def model_session(model_engine):
    with Session(model_engine, autoflush=False) as session:
        yield session


def _definition(session: Session, **fields) -> ScheduleDefinition:
    values = {
        "kind": "recurring", "name": "Netflix", "template": TEMPLATE, "interval_unit": "month",
        "anchor_date": date(2026, 10, 22), "auto_post_from": date(2026, 10, 3), **fields,
    }
    definition = ScheduleDefinition(**values)
    session.add(definition)
    session.flush()
    return definition


def _instance(session: Session, definition: ScheduleDefinition, seq: int, day: date, **fields) -> ScheduleInstance:
    instance = ScheduleInstance(definition_id=definition.id, seq=seq, rule_date=day, due_date=day, **fields)
    session.add(instance)
    session.flush()
    return instance


def test_schedule_tables_exist_and_entry_source_has_schedule(model_engine, model_session):
    tables = set(inspect(model_engine).get_table_names())
    assert {"schedule_definition", "schedule_instance"} <= tables
    assert "moze_schedule" not in tables
    assert "schedule" in ENTRY_SOURCES
    account = Account(name="錢包", currency="TWD")
    model_session.add(account)
    model_session.flush()
    entry = LedgerEntry(
        account_id=account.id, kind="expense", amount=Decimal("-390"), currency="TWD",
        entry_date=date(2026, 10, 22), source="schedule",
    )
    model_session.add(entry)
    model_session.commit()
    assert entry.source == "schedule"


def test_definition_defaults(model_session):
    # Spec "Local recurring definition": the columns a local monthly definition starts with.
    definition = _definition(model_session)
    model_session.commit()
    assert (
        definition.interval_n, definition.first_seq, definition.times, definition.posting_mode, definition.status,
        definition.created_locally, definition.moze_id, definition.generated_until, definition.review_reason,
        definition.template_owner_edited,
    ) == (1, 1, None, "auto", "active", True, None, None, None, False)


def test_instance_defaults(model_session):
    instance = _instance(model_session, _definition(model_session), 1, date(2026, 10, 22))
    model_session.commit()
    assert (
        instance.status, instance.posted_entry_ids, instance.is_partial, instance.edited_by_owner,
        instance.moze_record_ids, instance.acted_at, instance.acted_by, instance.amount_override,
    ) == ("pending", [], False, False, [], None, None, None)


def test_one_row_per_period(model_session):
    # Spec "One row per period".
    definition = _definition(model_session)
    _instance(model_session, definition, 3, date(2026, 12, 22))
    model_session.commit()
    model_session.add(
        ScheduleInstance(definition_id=definition.id, seq=3, rule_date=date(2027, 1, 22), due_date=date(2027, 1, 22))
    )
    with pytest.raises(IntegrityError, match="uq_schedule_instance_definition_seq"):
        model_session.commit()


def test_instance_status_checks(model_session):
    definition = _definition(model_session)
    model_session.commit()
    cases = [
        ({"status": "posted", "acted_at": NOW, "acted_by": "auto"}, "ck_schedule_instance_posted_entries"),
        ({"acted_at": NOW}, "ck_schedule_instance_acted"),
        ({"is_partial": True}, "ck_schedule_instance_partial"),
    ]
    for index, (fields, constraint) in enumerate(cases):
        day = date(2026, 11, 1 + index)
        model_session.add(ScheduleInstance(definition_id=definition.id, seq=10 + index, rule_date=day, due_date=day, **fields))
        with pytest.raises(IntegrityError, match=constraint):
            model_session.commit()
        model_session.rollback()


def test_a_day_is_posted_once(model_session):
    definition = _definition(model_session)
    day = date(2026, 10, 22)
    posted = {"status": "posted", "acted_at": NOW, "acted_by": "auto"}
    _instance(model_session, definition, 1, day, posted_entry_ids=[1], **posted)
    model_session.commit()
    model_session.add(
        ScheduleInstance(definition_id=definition.id, seq=2, rule_date=day, due_date=day, posted_entry_ids=[2], **posted)
    )
    with pytest.raises(IntegrityError, match="ux_schedule_instance_posted_day"):
        model_session.commit()
    model_session.rollback()
    _instance(model_session, definition, 3, day)  # a pending period on a posted day is allowed
    model_session.commit()


def test_definition_checks(model_session):
    cases = [
        ({"kind": "installment", "interval_unit": "week", "times": 3}, "ck_schedule_definition_installment"),
        ({"kind": "installment", "times": 1}, "ck_schedule_definition_installment"),
        ({"kind": "installment", "times": None}, "ck_schedule_definition_installment"),
        ({"interval_unit": "week", "day_of_month": 5}, "ck_schedule_definition_day_of_month"),
        ({"times": 0}, "ck_schedule_definition_times"),
        ({"end_date": date(2026, 10, 1)}, "ck_schedule_definition_end_date"),
        ({"total_amount": Decimal("0")}, "ck_schedule_definition_total_amount"),
        ({"interval_n": 0}, "ck_schedule_definition_interval_n"),
    ]
    for fields, constraint in cases:
        values = {
            "kind": "recurring", "name": "x", "template": TEMPLATE, "interval_unit": "month",
            "anchor_date": date(2026, 10, 22), "auto_post_from": date(2026, 10, 3), **fields,
        }
        model_session.add(ScheduleDefinition(**values))
        with pytest.raises(IntegrityError, match=constraint):
            model_session.commit()
        model_session.rollback()


def test_instances_cascade_with_their_definition(model_session):
    definition = _definition(model_session)
    _instance(model_session, definition, 1, date(2026, 10, 22))
    _instance(model_session, definition, 2, date(2026, 11, 22))
    model_session.commit()
    model_session.execute(delete(ScheduleDefinition).where(ScheduleDefinition.id == definition.id))
    model_session.commit()
    assert model_session.scalar(select(func.count()).select_from(ScheduleInstance)) == 0
