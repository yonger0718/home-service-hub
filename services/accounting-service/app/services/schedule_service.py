"""Definition and instance actions of /schedules (design D35, D36; spec "Definition endpoints", "Definition state
endpoints", "Instance endpoints").

Every write first shares the import advisory key (409 import_running while an import runs), then locks the definition
and its instances (D32). Services never commit, except post_sequence, which commits one transaction per instance.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ScheduleDefinition, ScheduleInstance
from ..schemas.schedules import DefinitionIn, DefinitionUpdateIn, InstanceUpdateIn
from ..schemas.writes import EntryIn
from . import entry_write_service, ledger_service
from . import schedule_generation as generation
from . import schedule_posting as posting
from . import schedule_rules as rules
from .edit_lock import EditLockedError, import_locked
from .errors import ConflictError, NotFoundError, ValidationError
from .schedule_locks import (
    ImportRunningError,
    get_instance,
    lock_definition,
    lock_instance,
    lock_instances,
    take_import_key_shared,
)
from .schedule_templates import check_amounts, loan_line, normalize_template, realign_override, validate_template


def _today() -> date:
    return ledger_service._today()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _check_rule(kind: str, payload: DefinitionUpdateIn) -> None:
    if kind == "installment":
        if payload.interval_unit != "month":
            raise ValidationError("interval_unit", "分期只能每月一期")
        if payload.times is None or payload.times < 2:
            raise ValidationError("times", "分期至少 2 期")
    if payload.day_of_month is not None and payload.interval_unit not in ("month", "year"):
        raise ValidationError("day_of_month", "只有每月或每年可以指定日期")
    if payload.end_date is not None and payload.end_date < payload.anchor_date:
        raise ValidationError("end_date", "結束日不可早於起始日")
    if payload.total_amount is not None and kind != "installment":
        raise ValidationError("total_amount", "只有分期有總額")


def _check_total(kind: str, template: dict, times: int | None, total: Decimal | None) -> None:
    if kind == "installment" and total is not None and times is not None:
        per_period = Decimal(template["lines"][0]["amount"])
        if per_period * (times - 1) >= Decimal(total):
            raise ValidationError("total_amount", "每期金額乘以期數已達總額")


def _set_mode(definition: ScheduleDefinition, mode: str, today: date) -> None:
    """D35: a switch to auto never posts a backlog, so auto_post_from moves to today."""
    if mode == "auto" and definition.posting_mode != "auto":
        definition.auto_post_from = today
    definition.posting_mode = mode


def create_definition(db: Session, payload: DefinitionIn) -> int:
    take_import_key_shared(db)
    today = _today()
    _check_rule(payload.kind, payload)
    template = normalize_template(payload.template.model_dump())
    total_amount = payload.total_amount
    if payload.loan is not None:
        if payload.kind != "installment":
            raise ValidationError("loan", "只有分期可以同時建立貸款")
        loan = payload.loan
        try:
            prepared = entry_write_service.prepare_entry(
                db,
                EntryIn(
                    account_id=loan.account_id, kind="payable", amount=loan.amount, entry_date=loan.entry_date,
                    category_id=loan.category_id, counterparty_id=loan.counterparty_id, name=loan.name,
                ),
            )
        except ValidationError as exc:
            raise ValidationError(f"loan.{exc.field}", exc.message) from exc
        loan_id = entry_write_service.insert_prepared(db, prepared, remember=False)
        for line in template["lines"]:
            if line["kind"] == "repayment" and line["loan_entry_id"] is None:
                line["loan_entry_id"] = loan_id
        total_amount = loan.amount
    validate_template(db, template)
    _check_total(payload.kind, template, payload.times, total_amount)
    # anchor_date is occurrence 0: a day_of_month earlier than the sent day starts next month (rules.normalize_anchor)
    anchor = rules.normalize_anchor(payload.anchor_date, payload.interval_unit, payload.interval_n, payload.day_of_month)
    if payload.end_date is not None and payload.end_date < anchor:
        raise ValidationError("end_date", "結束日期不可早於第一期")
    definition = ScheduleDefinition(
        kind=payload.kind, name=payload.name, template=template, interval_unit=payload.interval_unit,
        interval_n=payload.interval_n, anchor_date=anchor, day_of_month=payload.day_of_month,
        times=payload.times, end_date=payload.end_date, total_amount=total_amount, posting_mode=payload.posting_mode,
        status="active", auto_post_from=today, created_locally=True,
    )
    db.add(definition)
    db.flush()
    generation.generate(db, definition, today)
    generation.end_if_complete(db, definition)
    return definition.id


def update_definition(db: Session, definition_id: int, payload: DefinitionUpdateIn) -> None:
    take_import_key_shared(db)
    definition = lock_definition(db, definition_id)
    if definition.moze_id is not None and not import_locked():
        raise EditLockedError()  # D36: the next import would overwrite a template edit
    if definition.status == "ended":
        raise ConflictError("definition_ended")  # spec: no pending instance is ever created for an ended definition
    instances = lock_instances(db, definition_id)
    today = _today()
    tomorrow = today + timedelta(days=1)
    _check_rule(definition.kind, payload)
    template = normalize_template(payload.template.model_dump())
    validate_template(db, template)
    total = payload.total_amount if payload.total_amount is not None else definition.total_amount
    if definition.kind != "installment":
        total = None
    _check_total(definition.kind, template, payload.times, total)

    doomed = [row for row in instances if row.status == "pending" and row.due_date >= tomorrow]
    remaining = [row for row in instances if row not in doomed]
    max_seq = max((row.seq for row in remaining), default=0)
    if payload.times is not None and payload.times < max_seq:
        raise ValidationError("times", "期數不可少於已有的期別")
    for row in doomed:
        db.delete(row)
    db.flush()
    old_template = definition.template
    for row in remaining:
        if row.status == "pending":
            row.amount_override = realign_override(old_template, template, row.amount_override)

    definition.name, definition.template = payload.name, template
    definition.interval_unit, definition.interval_n = payload.interval_unit, payload.interval_n
    definition.day_of_month, definition.times, definition.end_date = payload.day_of_month, payload.times, payload.end_date
    definition.total_amount = total
    if definition.review_reason == "interval_mismatch":
        definition.review_reason = None  # the owner's edit replaces the import's invented rule, so it may roll forward
    if payload.posting_mode is not None:
        _set_mode(definition, payload.posting_mode, today)

    # R-F3: occurrences are always computed from the rule anchor, which an edit never rebases implicitly. Only a
    # different anchor_date moves it; a month / year rule sent without day_of_month then takes the new anchor's day.
    unit, n, day_of_month = payload.interval_unit, payload.interval_n, payload.day_of_month
    anchor_changed = payload.anchor_date != definition.anchor_date
    if anchor_changed and day_of_month is None and unit in ("month", "year"):
        day_of_month = payload.anchor_date.day
    base = payload.anchor_date if anchor_changed else definition.anchor_date
    anchor = rules.normalize_anchor(base, unit, n, day_of_month)  # occurrence 0; keeps a 31st / 02-29 anchor as is
    definition.anchor_date, definition.day_of_month = anchor, day_of_month  # first_seq is kept
    k = rules.first_index_on_or_after(anchor, unit, n, day_of_month, tomorrow)
    # The same bound generation uses: generate() continues strictly after the latest rule_date, and an owner may
    # have moved a period (due_date ≠ rule_date) either way — start after the later of the two.
    latest = max((max(row.rule_date, row.due_date) for row in remaining), default=None)
    if latest is not None:
        k = max(k, rules.first_index_after(anchor, unit, n, day_of_month, latest))
    first = rules.occurrence(anchor, unit, n, k, day_of_month)
    next_seq = max(max_seq + 1, definition.first_seq)
    fits = (
        (payload.times is None or next_seq <= payload.times)
        and (payload.end_date is None or first <= payload.end_date)
        and first <= rules.horizon(today)  # beyond the horizon generate() inserts it later, once it is within reach
    )
    db.flush()
    if fits:
        # The first regenerated period is inserted here, so generate() (strictly after the latest rule_date) continues
        # from it without the anchor being moved; it carries the last-period override when it is the last seq.
        last_override = generation.last_period_override(definition)
        db.add(ScheduleInstance(
            definition_id=definition.id, seq=next_seq, rule_date=first, due_date=first,
            amount_override=last_override if last_override is not None and next_seq == definition.times else None,
        ))
        db.flush()
        generation.generate(db, definition, today)
    generation.end_if_complete(db, definition)


def delete_definition(db: Session, definition_id: int) -> None:
    take_import_key_shared(db)
    definition = lock_definition(db, definition_id)
    if definition.moze_id is not None and not import_locked():
        raise EditLockedError()
    posted = db.scalar(
        select(ScheduleInstance.id)
        .where(ScheduleInstance.definition_id == definition_id, ScheduleInstance.status == "posted")
        .limit(1)
    )
    if posted is not None:
        raise ConflictError("排程已有入帳的期別，不能刪除；請改用結束（end）")
    db.delete(definition)  # instances go by ON DELETE CASCADE
    db.flush()


def pause(db: Session, definition_id: int) -> None:
    take_import_key_shared(db)
    definition = lock_definition(db, definition_id)
    if definition.status != "active":
        raise ConflictError(f"只有進行中的排程可以暫停（目前 {definition.status}）")
    definition.status = "paused"
    db.flush()


def _due_pending(db: Session, definition_id: int, today: date):
    return select(ScheduleInstance).where(
        ScheduleInstance.definition_id == definition_id,
        ScheduleInstance.status == "pending",
        ScheduleInstance.due_date <= today,
    )


def resume(db: Session, definition_id: int, backlog: str) -> list[int]:
    """Active again; the paused months are skipped (default) or returned to be posted after this commits."""
    take_import_key_shared(db)
    definition = lock_definition(db, definition_id)
    if definition.status != "paused":
        raise ConflictError(f"只有暫停中的排程可以繼續（目前 {definition.status}）")
    lock_instances(db, definition_id)
    definition.status = "active"
    due = list(db.scalars(_due_pending(db, definition_id, _today()).order_by(ScheduleInstance.seq)))
    if backlog == "post":
        db.flush()
        return [row.id for row in due]
    now = _now()
    for row in due:
        row.status, row.acted_at, row.acted_by = "skipped", now, "owner"
    db.flush()
    return []


def end(db: Session, definition_id: int) -> None:
    take_import_key_shared(db)
    definition = lock_definition(db, definition_id)
    if definition.status == "ended":
        raise ConflictError("排程已結束")
    for row in lock_instances(db, definition_id):
        if row.status == "pending":
            db.delete(row)
    definition.status = "ended"
    if definition.review_reason == "not_live":
        definition.review_reason = None  # the owner's end: a later backup import must not revive it (Task 17)
    db.flush()


def set_mode(db: Session, definition_id: int, posting_mode: str) -> None:
    take_import_key_shared(db)
    definition = lock_definition(db, definition_id)
    if definition.status == "ended":
        raise ConflictError("definition_ended")  # consistent with PUT of an ended definition (Task 10)
    _set_mode(definition, posting_mode, _today())
    db.flush()


def catch_up_ids(db: Session, definition_id: int) -> list[int]:
    """補入帳至今天: the pending periods due today or earlier, in seq order; refused while paused."""
    take_import_key_shared(db)
    definition = lock_definition(db, definition_id, share=True)
    if definition.status == "paused":
        raise ConflictError("排程已暫停，請先繼續")
    due = _due_pending(db, definition_id, _today()).with_only_columns(ScheduleInstance.id)
    return list(db.scalars(due.order_by(ScheduleInstance.seq)))


_NOT_MINE = ("already_posted", "skipped")  # someone else posted or skipped the period meanwhile


def _post_with_retry(db: Session, instance_id: int) -> posting.PostResult:
    """post_instance, retried once when the template gained a loan line under it (definition_changed)."""
    try:
        return posting.post_instance(db, instance_id, actor="owner")
    except ConflictError as exc:
        if str(exc) != "definition_changed":
            raise
        db.rollback()
        return posting.post_instance(db, instance_id, actor="owner")


def post_sequence(db: Session, instance_ids: list[int]) -> tuple[list[int], dict | None]:
    """Post each instance in its own transaction (acted_by owner) in seq order, stopping at the first failure (D31,
    R-F6): only a period someone else posted, skipped or deleted meanwhile is passed over."""
    posted: list[int] = []
    for instance_id in instance_ids:
        try:
            result = _post_with_retry(db, instance_id)
            db.commit()
        except ImportRunningError:
            db.rollback()
            return posted, {"instance_id": instance_id, "error": "import_running"}
        except NotFoundError:
            db.rollback()
            continue
        except Exception as exc:  # noqa: BLE001 — ValidationError, ConflictError, IntegrityError, OperationalError …
            if isinstance(exc, ConflictError) and str(exc) in _NOT_MINE:
                db.rollback()
                continue
            # Spec: on any error a second transaction stores last_error; failure_message keeps only the class name
            # for anything but a ValidationError.
            message = posting.failure_message(exc)
            posting.record_failure(db, instance_id, message)
            return posted, {"instance_id": instance_id, "error": message}
        if result.outcome == "posted":
            posted.append(instance_id)
        elif result.outcome == "loan_closed":
            break  # the later periods were skipped with it
    return posted, None
