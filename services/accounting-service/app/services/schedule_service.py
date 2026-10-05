"""Definition and instance actions of /schedules (design D35, D36; spec "Definition endpoints", "Definition state
endpoints", "Instance endpoints").

Every write first shares the import advisory key (409 import_running while an import runs), then locks the definition
and its instances (D32). Services never commit, except post_sequence, which commits one transaction per instance.
"""

import copy
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
from .schedule_generation import check_installment_closed, installment_residual
from .schedule_templates import (
    aligned_override,
    check_amounts,
    loan_line,
    normalize_template,
    realign_override,
    template_amounts,
    validate_template,
)


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
    # R-F3: occurrences are always computed from the rule anchor, which an edit never rebases implicitly. Only a
    # different anchor_date moves it; a month / year rule sent without day_of_month then takes the new anchor's day.
    unit, n, day_of_month = payload.interval_unit, payload.interval_n, payload.day_of_month
    anchor_changed = payload.anchor_date != definition.anchor_date
    if anchor_changed and day_of_month is None and unit in ("month", "year"):
        day_of_month = payload.anchor_date.day
    base = payload.anchor_date if anchor_changed else definition.anchor_date
    anchor = rules.normalize_anchor(base, unit, n, day_of_month)  # occurrence 0; keeps a 31st / 02-29 anchor as is
    if payload.end_date is not None and payload.end_date < anchor:
        raise ValidationError("end_date", "結束日期不可早於第一期")  # against the normalised anchor, as create does

    doomed = [row for row in instances if row.status == "pending" and row.due_date >= tomorrow]
    remaining = [row for row in instances if row not in doomed]
    max_seq = max((row.seq for row in remaining), default=0)
    if payload.times is not None and payload.times < max_seq:
        raise ValidationError("times", "期數不可少於已有的期別")
    for row in doomed:
        db.delete(row)
    db.flush()
    old_template = definition.template
    old_amounts = template_amounts(old_template)
    amounts_changed = old_amounts != template_amounts(template)
    for row in remaining:
        if row.status == "pending":
            row.amount_override = realign_override(old_template, template, row.amount_override)
        elif row.status == "posted" and row.amount_override is None and amounts_changed:
            # R-A1 review: pin the amounts it was posted with before the template changes (metadata only; its
            # entries stay), so an installment's last period keeps Σ = total_amount
            row.amount_override = list(old_amounts)

    definition.name, definition.template = payload.name, template
    definition.interval_unit, definition.interval_n = payload.interval_unit, payload.interval_n
    definition.day_of_month, definition.times, definition.end_date = payload.day_of_month, payload.times, payload.end_date
    definition.total_amount = total
    if definition.review_reason == "interval_mismatch":
        definition.review_reason = None  # the owner's edit replaces the import's invented rule, so it may roll forward
    if payload.posting_mode is not None:
        _set_mode(definition, payload.posting_mode, today)

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
        db.add(ScheduleInstance(
            definition_id=definition.id, seq=next_seq, rule_date=first, due_date=first,
            amount_override=generation.generated_last_override(db, definition) if next_seq == definition.times else None,
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


def _instance_locked(db: Session, instance_id: int, *, share: bool = True) -> tuple[ScheduleDefinition, ScheduleInstance]:
    """Shared import key → definition (FOR SHARE, or FOR UPDATE when the action may change it) → instance FOR UPDATE."""
    take_import_key_shared(db)
    definition = lock_definition(db, get_instance(db, instance_id).definition_id, share=share)
    instance = lock_instance(db, instance_id)
    if instance is None:
        raise NotFoundError(f"schedule instance {instance_id} not found")
    return definition, instance


def update_instance(db: Session, instance_id: int, payload: InstanceUpdateIn) -> None:
    """編輯這一筆 on a pending period: date and / or amounts; rule_date never changes. A scope other than `this`
    (這一期與之後 / 全部週期, proposal decision 24) is the amounts-only path of _apply_amount_scope."""
    if payload.scope != "this":
        _apply_amount_scope(db, instance_id, payload)
        return
    if payload.amounts is None and payload.due_date is None:
        raise ValidationError("scope", "沒有要修改的欄位")  # an empty edit never marks the period owner-edited
    definition, instance = _instance_locked(db, instance_id)
    if instance.status != "pending":
        raise ConflictError("只有待入帳的期別可以修改")
    if payload.amounts is not None:
        instance.amount_override = check_amounts(definition.template, payload.amounts)
    if payload.due_date is not None:
        taken = db.scalar(
            select(ScheduleInstance.status)
            .where(
                ScheduleInstance.definition_id == definition.id,
                ScheduleInstance.id != instance.id,
                ScheduleInstance.status != "skipped",
                ScheduleInstance.due_date == payload.due_date,
            )
            .order_by(ScheduleInstance.status.desc())  # 'posted' first
            .limit(1)
        )
        if taken == "posted":
            raise ValidationError("due_date", "這一天已有入帳的期別")
        if taken is not None:
            # two pending periods on one day would collide on ux_schedule_instance_posted_day when both post
            raise ValidationError("due_date", "這一天已有另一期待入帳")
        instance.due_date = payload.due_date
    instance.edited_by_owner = True
    db.flush()


def _apply_amount_scope(db: Session, instance_id: int, payload: InstanceUpdateIn) -> None:
    """這一期與之後 (`following`) / 全部週期 (`all`), spec "Instance amount edit scope".

    The new amounts become the template's (template_owner_edited, so re-imports keep them, D37); every pending period
    from this one (following) or every pending period (all) follows the template, except owner-edited ones other
    than this; with `following` the earlier pending periods keep the old price as an override. Posted and skipped
    periods never change. An installment with a total gives its pending last period the residual of
    installment_residual (R-A1); when its last period is already posted or skipped the edit must leave Σ exactly
    total_amount (check_installment_closed, ruling R1). Lock order (D32): key → definition FOR UPDATE → every
    instance FOR UPDATE, ascending id. No cutover check: amounts are not rule fields (update_definition / delete_definition keep their
    lock)."""
    if payload.amounts is None or payload.due_date is not None:
        raise ValidationError("scope", "套用到其他期別時只能修改金額")
    take_import_key_shared(db)
    definition = lock_definition(db, get_instance(db, instance_id).definition_id)
    rows = lock_instances(db, definition.id)
    instance = next((row for row in rows if row.id == instance_id), None)
    if instance is None:
        raise NotFoundError(f"schedule instance {instance_id} not found")
    if instance.status != "pending":
        raise ConflictError("只有待入帳的期別可以修改")
    amounts = check_amounts(definition.template, payload.amounts)
    if any(Decimal(value) == 0 for value in amounts):
        raise ValidationError("amounts", "套用到其他期別時每一行金額須大於 0")
    pending = [row for row in rows if row.status == "pending"]
    old_amounts = template_amounts(definition.template)
    planned: dict[int, list[str] | None] = {}  # the overrides this edit writes; computed before anything changes
    for row in rows:
        if row.status == "posted" and row.amount_override is None:
            planned[row.id] = list(old_amounts)  # pin the amounts it was posted with (metadata only, R-A1 review)
    for row in pending:
        if payload.scope == "following" and row.seq < instance.seq:
            if row.amount_override is None:
                planned[row.id] = list(old_amounts)  # the earlier periods keep the old price
        elif row.id == instance.id:
            planned[row.id] = list(amounts) if row.edited_by_owner else None
        elif not row.edited_by_owner:
            planned[row.id] = None
    closed = next(
        (row for row in rows if definition.times is not None and row.seq == definition.times and row.status != "pending"),
        None,
    )
    if closed is not None:  # R1: a posted / skipped last period absorbs nothing, so Σ must already be the total
        check_installment_closed(definition, rows, old_amounts=old_amounts, new_amounts=amounts, overrides=planned)
        residual = None
    else:
        residual = installment_residual(  # 422 `amounts` when the others reach the total; nothing written yet
            definition, rows, old_amounts=old_amounts, new_amounts=amounts, overrides=planned
        )
    for row in rows:
        if row.id in planned:
            row.amount_override = planned[row.id]
    template = copy.deepcopy(definition.template)
    for line, amount in zip(template["lines"], amounts):
        line["amount"] = amount
    definition.template, definition.template_owner_edited = template, True
    last = next((row for row in pending if definition.times is not None and row.seq == definition.times), None)
    if residual is not None and last is not None:
        # The last pending period always closes the total (local or imported); its other lines keep a preserved
        # owner override, else take the new amounts. An ungenerated last period is written later by generation.
        keep = last.edited_by_owner and last.id != instance.id and last.amount_override is not None
        rest = last.amount_override[1:] if keep else amounts[1:]
        last.amount_override = [rules.plain(residual), *rest]
    db.flush()


def post_one(db: Session, instance_id: int) -> None:
    """[入帳]: acted_by owner. Any failure is stored as last_error (own transaction): a validation failure is
    re-raised (422); conflicts, a missing row and the cutover lock pass through unrecorded; any other error
    (IntegrityError, a deadlock's OperationalError) is recorded by class name and answered 409."""
    try:
        posting.post_instance(db, instance_id, actor="owner")
    except ValidationError as exc:
        posting.record_failure(db, instance_id, posting.failure_message(exc))
        raise
    except (ConflictError, NotFoundError, EditLockedError):
        db.rollback()
        raise
    except Exception as exc:  # noqa: BLE001 — spec: on any error a separate transaction stores last_error
        message = posting.failure_message(exc)
        posting.record_failure(db, instance_id, message)
        raise ConflictError(message) from exc


def skip_instance(db: Session, instance_id: int) -> None:
    """[略過]: no entry is written, so a loan's open amount stays (略過這一期？剩餘不變)."""
    _, instance = _instance_locked(db, instance_id)
    if instance.status != "pending":
        raise ConflictError("只有待入帳的期別可以略過")
    instance.status, instance.acted_at, instance.acted_by = "skipped", _now(), "owner"
    db.flush()


def reopen_instance(db: Session, instance_id: int) -> None:
    definition, instance = _instance_locked(db, instance_id, share=False)
    if instance.status == "pending":
        raise ConflictError("這一期已是待入帳")
    if instance.status == "posted":
        posting.delete_period_entries(db, list(instance.posted_entry_ids))
        instance.note = f"入帳記錄已於 {_today().isoformat()} 刪除"
    instance.status, instance.posted_entry_ids, instance.is_partial = "pending", [], False
    instance.acted_at, instance.acted_by, instance.reopened_at = None, None, _now()
    instance.amount_override = aligned_override(definition.template, instance.amount_override)
    if definition.status == "ended":
        definition.status = "active"
    db.flush()


def repost_instance(db: Session, instance_id: int, amounts: list) -> None:
    """編輯這一筆 on a posted (or partial) period: delete its entries and post again with new amounts, atomically.

    Lock order (D32): key → definition (lock_definition_for_post: FOR UPDATE with a loan line, since the repost may
    end it) → instance → the later pending instances (loan templates) → groups → the period's entries **and the
    loan** in one statement (delete_period_entries(also_lock=…)); post_locked reuses that loan row, so no second
    entry-lock statement runs."""
    take_import_key_shared(db)
    definition = posting.lock_definition_for_post(db, get_instance(db, instance_id).definition_id)
    instance = lock_instance(db, instance_id)
    if instance is None:
        raise NotFoundError(f"schedule instance {instance_id} not found")
    if instance.status != "posted":
        raise ConflictError("只有已入帳的期別可以重新入帳")
    if instance.acted_by == "import" and not import_locked():
        raise EditLockedError()
    override = check_amounts(definition.template, amounts)
    reference = loan_line(definition.template)
    loan_id = None
    if reference is not None:
        posting.lock_later_pending(db, definition, instance)  # schedule rows before any entry lock
        loan_id = reference[1]["loan_entry_id"]
    loan = posting.delete_period_entries(db, list(instance.posted_entry_ids), also_lock=loan_id)
    instance.status, instance.posted_entry_ids, instance.is_partial = "pending", [], False
    instance.acted_at, instance.acted_by = None, None
    instance.amount_override, instance.edited_by_owner = override, True
    db.flush()
    posting.post_locked(db, definition, instance, "owner", locked_loan=loan)


def accept_partial(db: Session, instance_id: int) -> None:
    """保留部分: keep what is left of the period; the note stays."""
    _, instance = _instance_locked(db, instance_id)
    if instance.status != "posted" or not instance.is_partial:
        raise ConflictError("只有部分入帳的期別可以保留部分")
    instance.is_partial = False
    db.flush()
