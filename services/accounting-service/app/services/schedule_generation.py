"""Instance generation (design D30): the next occurrences after the latest instance, up to a 13-month horizon.

Generation never deletes, moves or re-dates an existing instance, so an owner's date edit on one period cannot shift
later ones: it continues strictly after the latest instance's rule_date. Callers hold the definition lock.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import ScheduleDefinition, ScheduleInstance
from . import schedule_rules as rules
from .schedule_locks import lock_definition, take_import_key_shared
from .schedule_templates import template_amounts


def last_period_override(definition: ScheduleDefinition) -> list[str] | None:
    """A local installment with a total: the last period takes the first line's remainder (MOZE 分期餘額納入 末期) and
    the template amounts of the other lines, e.g. ["8345", "620"]. Imported installments carry MOZE's own amounts."""
    if (
        definition.kind != "installment"
        or definition.total_amount is None
        or definition.times is None
        or not definition.created_locally
    ):
        return None
    amounts = template_amounts(definition.template)
    last = rules.last_period_amount(Decimal(definition.total_amount), Decimal(amounts[0]), definition.times)
    return [rules.plain(last), *amounts[1:]]


def _latest(db: Session, definition_id: int) -> ScheduleInstance | None:
    return db.scalar(
        select(ScheduleInstance)
        .where(ScheduleInstance.definition_id == definition_id)
        .order_by(ScheduleInstance.rule_date.desc(), ScheduleInstance.seq.desc())
        .limit(1)
    )


def _max_seq(db: Session, definition_id: int) -> int | None:
    return db.scalar(select(func.max(ScheduleInstance.seq)).where(ScheduleInstance.definition_id == definition_id))


def _rule(definition: ScheduleDefinition) -> tuple:
    return definition.anchor_date, definition.interval_unit, definition.interval_n, definition.day_of_month


def generate(db: Session, definition: ScheduleDefinition, today: date) -> int:
    """Insert the next occurrences up to horizon(today); returns how many instances were created."""
    if definition.status == "ended" or definition.review_reason == "interval_mismatch":
        return 0  # an interval_mismatch import carries an invented rule (seqs by position): never roll it forward
    limit = rules.horizon(today)
    anchor, unit, n, day_of_month = _rule(definition)
    latest = _latest(db, definition.id)
    max_seq = _max_seq(db, definition.id)
    posted_days = set(
        db.scalars(
            select(ScheduleInstance.due_date).where(
                ScheduleInstance.definition_id == definition.id, ScheduleInstance.status == "posted"
            )
        )
    )
    k = 0 if latest is None else rules.first_index_after(anchor, unit, n, day_of_month, latest.rule_date)
    seq = definition.first_seq if max_seq is None else max(max_seq + 1, definition.first_seq)
    last_override = last_period_override(definition)
    created = 0
    while definition.times is None or seq <= definition.times:
        day = rules.occurrence(anchor, unit, n, k, day_of_month)
        if (definition.end_date is not None and day > definition.end_date) or day > limit:
            break
        k += 1
        if day in posted_days:
            continue  # this date is already posted for the definition: no second period, no seq consumed
        instance = ScheduleInstance(definition_id=definition.id, seq=seq, rule_date=day, due_date=day)
        if last_override is not None and seq == definition.times:
            instance.amount_override = last_override
        db.add(instance)
        created += 1
        seq += 1
    definition.generated_until = limit
    db.flush()
    return created


def series_complete(db: Session, definition: ScheduleDefinition) -> bool:
    """The last occurrence (by times or end_date) exists."""
    latest = _latest(db, definition.id)
    if latest is None:
        return False
    if definition.times is not None and (_max_seq(db, definition.id) or 0) >= definition.times:
        return True
    if definition.end_date is not None:
        anchor, unit, n, day_of_month = _rule(definition)
        k = rules.first_index_after(anchor, unit, n, day_of_month, latest.rule_date)
        return rules.occurrence(anchor, unit, n, k, day_of_month) > definition.end_date
    return False


def end_if_complete(db: Session, definition: ScheduleDefinition) -> bool:
    """End a definition whose series is complete and which has nothing pending or partial; True when it ended now."""
    if definition.status == "ended" or not series_complete(db, definition):
        return False
    open_instance = db.scalar(
        select(ScheduleInstance.id)
        .where(
            ScheduleInstance.definition_id == definition.id,
            (ScheduleInstance.status == "pending") | ScheduleInstance.is_partial.is_(True),
        )
        .limit(1)
    )
    if open_instance is not None:
        return False
    definition.status = "ended"
    db.flush()
    return True


def generate_locked(db: Session, definition_id: int, today: date) -> int:
    """The job's per-definition step: shared import key → definition FOR UPDATE → generate → maybe end."""
    take_import_key_shared(db)
    definition = lock_definition(db, definition_id)
    created = generate(db, definition, today)
    end_if_complete(db, definition)
    return created
