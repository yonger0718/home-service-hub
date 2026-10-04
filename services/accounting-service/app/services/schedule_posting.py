"""Posting one schedule instance (design D31): one transaction, the existing ledger services, source = 'schedule'.

Lock order (D32): shared import key → definition via lock_definition_for_post (FOR SHARE; FOR UPDATE with a loan
line) → instance FOR UPDATE (SKIP LOCKED for the job) → later pending instances (loan) → the ledger's order (the loan
entry via locked_entry is the "target entries" step; new rows take no further locks).
Callers own the transaction; record_failure is the "second short transaction" that stores last_error.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.orm import Session

from ..models import Account, EntryGroup, LedgerEntry, ScheduleDefinition, ScheduleInstance
from ..schemas.writes import EntryIn, SettleIn, TransferIn
from . import entry_write_service, ledger_service, settlement_service, transfer_service
from .errors import ConflictError, NotFoundError, ValidationError
from .schedule_locks import ImportRunningError, get_instance, lock_definition, lock_instance, take_import_key_shared
from .schedule_templates import LOAN_LINE_KINDS, loan_line, resolved_amounts, validate_template

SOURCE = "schedule"
LOAN_CLOSED_NOTE = "貸款已結清"
ENTRY_LINE_KINDS = ("expense", "income", "receivable", "payable")
MAX_ERROR_LENGTH = 500
GROUP_NAME_LENGTH = 128


@dataclass(frozen=True)
class PostResult:
    instance_id: int
    outcome: str  # posted | loan_closed | skipped_locked | not_due
    entry_ids: tuple[int, ...] = ()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def auto_eligible(definition: ScheduleDefinition, instance: ScheduleInstance, today: date) -> bool:
    """D34: what the job may post. Reopened, confirm, paused, ended and pre-auto_post_from periods wait for the owner."""
    return (
        instance.status == "pending"
        and instance.reopened_at is None
        and definition.auto_post_from <= instance.due_date <= today
        and definition.status == "active"
        and definition.posting_mode == "auto"
    )


def lock_definition_for_post(db: Session, definition_id: int) -> ScheduleDefinition:
    """FOR SHARE, or FOR UPDATE when the template has a loan line: such a post may end the definition
    (_close_out), and two FOR SHARE holders that both upgrade deadlock (D32)."""
    peek = db.get(ScheduleDefinition, definition_id)
    if peek is None:
        raise NotFoundError(f"schedule definition {definition_id} not found")
    share = loan_line(peek.template) is None
    definition = lock_definition(db, definition_id, share=share)
    if share and loan_line(definition.template) is not None:
        raise ConflictError("definition_changed")  # the template gained a loan line meanwhile; the caller retries
    return definition


def lock_later_pending(
    db: Session, definition: ScheduleDefinition, instance: ScheduleInstance
) -> list[ScheduleInstance]:
    """Lock the definition's pending instances after this one (ascending id). Called before any entry lock, so a
    loan close-out never locks a schedule row while it holds the loan entry (D32)."""
    return list(
        db.scalars(
            select(ScheduleInstance)
            .where(
                ScheduleInstance.definition_id == definition.id,
                ScheduleInstance.status == "pending",
                ScheduleInstance.seq > instance.seq,
            )
            .order_by(ScheduleInstance.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )


def post_instance(
    db: Session, instance_id: int, *, actor: str, job: bool = False, today: date | None = None
) -> PostResult:
    take_import_key_shared(db)
    definition = lock_definition_for_post(db, get_instance(db, instance_id).definition_id)
    instance = lock_instance(db, instance_id, skip_locked=job)
    if instance is None:
        if job:
            return PostResult(instance_id, "skipped_locked")
        raise NotFoundError(f"schedule instance {instance_id} not found")
    if instance.status != "pending":
        raise ConflictError("already_posted" if instance.status == "posted" else "skipped")
    if job and not auto_eligible(definition, instance, today or ledger_service._today()):
        return PostResult(instance_id, "not_due")
    return post_locked(db, definition, instance, actor)


def _close_out(
    db: Session,
    definition: ScheduleDefinition,
    instance: ScheduleInstance,
    later: list[ScheduleInstance],
    actor: str,
) -> None:
    """The loan is closed or fully repaid: this and every later pending period are skipped; the definition ends.
    `later` was locked by lock_later_pending before the loan entry; the definition is held FOR UPDATE
    (lock_definition_for_post). No schedule row is locked here."""
    now = _now()
    for item in [instance, *later]:
        item.status, item.acted_at, item.acted_by = "skipped", now, actor
        item.note, item.last_error, item.last_error_at = LOAN_CLOSED_NOTE, None, None
    definition.status = "ended"
    db.flush()


def _write_line(
    db: Session,
    definition: ScheduleDefinition,
    instance: ScheduleInstance,
    line: dict,
    amount: Decimal,
    loan: LedgerEntry | None,
    open_amount: Decimal,
) -> tuple[list[int], bool]:
    """Write one line; returns (top-level entry ids, whether they join the period's group)."""
    template = definition.template
    due = instance.due_date
    name = line["name"] or definition.name
    description, tags = template.get("description"), list(template.get("tags") or [])
    kind = line["kind"]
    if kind in ENTRY_LINE_KINDS:
        payload = EntryIn(
            account_id=line["account_id"], kind=kind, amount=amount, entry_date=due, entry_time=None, posted_date=due,
            category_id=line["category_id"], project_id=line["project_id"], name=name, merchant=line["merchant"],
            counterparty_id=line["counterparty_id"] if kind in ("receivable", "payable") else None,
            description=description, tags=tags,
        )
        prepared = entry_write_service.prepare_entry(db, payload)
        return [entry_write_service.insert_prepared(db, prepared, remember=False, source=SOURCE)], True
    if kind == "transfer":
        payload = TransferIn(
            from_account_id=line["account_id"], to_account_id=line["to_account_id"], out_amount=amount,
            in_amount=Decimal(line["to_amount"]) if line["to_amount"] is not None else None, entry_date=due,
            entry_time=None, posted_date=due, category_id=line["category_id"], name=name, merchant=line["merchant"],
            description=description, project_id=line["project_id"], tags=tags,
        )
        group = transfer_service.create_transfer(db, payload, source=SOURCE, remember=False)
        out_leg, in_leg = transfer_service.transfer_legs(db, group)
        return [out_leg.id, in_leg.id], False
    if kind in LOAN_LINE_KINDS:
        value = min(amount, open_amount)  # the last period absorbs rounding and hand repayments (D35, D38)
        entry_id = settlement_service.settle(
            db, loan.id, SettleIn(account_id=line["account_id"], amount=value, entry_date=due, description=description),
            source=SOURCE, check_cutover_lock=False, name=name,
        )
        entry = db.get(LedgerEntry, entry_id)
        entry.category_id, entry.project_id = line["category_id"], line["project_id"]
        entry.merchant, entry.tags = line["merchant"], tags
        db.flush()
        return [entry_id], True
    account = db.get(Account, line["account_id"])
    entry = LedgerEntry(
        account_id=account.id, currency=account.currency, kind="interest", amount=-amount, entry_date=due,
        entry_time=None, posted_date=due,
        category_id=line["category_id"] or entry_write_service.system_category_id(db, "interest"),
        project_id=line["project_id"], name=name, merchant=line["merchant"],
        counterparty_id=loan.counterparty_id if loan is not None else None,  # interest names the lender (D29)
        description=description, tags=tags, source=SOURCE,
    )
    db.add(entry)
    db.flush()
    return [entry.id], True


def _group(db: Session, definition: ScheduleDefinition, instance: ScheduleInstance, entry_ids: list[int]) -> None:
    label = f"#{instance.seq}/{definition.times}" if definition.times is not None else f"#{instance.seq}"
    group = EntryGroup(
        kind="installment" if definition.kind == "installment" else "split",
        name=f"{definition.name} {label}"[:GROUP_NAME_LENGTH],
    )
    db.add(group)
    db.flush()
    db.execute(
        update(LedgerEntry)
        .where(LedgerEntry.id.in_(entry_ids))
        .values(group_id=group.id)
        .execution_options(synchronize_session=False)
    )
    db.flush()


def post_locked(
    db: Session,
    definition: ScheduleDefinition,
    instance: ScheduleInstance,
    actor: str,
    *,
    locked_loan: LedgerEntry | None = None,
) -> PostResult:
    """Write the period; the caller holds the definition (lock_definition_for_post) and instance locks and owns the
    transaction. `locked_loan`: the loan entry the caller already locked in its single entry statement (repost);
    such a caller has called lock_later_pending before taking any entry lock."""
    template = definition.template
    validate_template(db, template)
    amounts = resolved_amounts(definition, instance)
    loan, open_amount = None, Decimal(0)
    reference = loan_line(template)
    if reference is not None:
        index, line = reference
        if locked_loan is not None and locked_loan.id == line["loan_entry_id"]:
            later = list(
                db.scalars(  # already locked by the caller (lock_later_pending); plain read, no schedule lock here
                    select(ScheduleInstance)
                    .where(
                        ScheduleInstance.definition_id == definition.id,
                        ScheduleInstance.status == "pending",
                        ScheduleInstance.seq > instance.seq,
                    )
                    .order_by(ScheduleInstance.id)
                )
            )
            loan = locked_loan
        else:
            later = lock_later_pending(db, definition, instance)  # schedule rows first, then the loan entry (D32)
            try:
                loan = entry_write_service.locked_entry(db, line["loan_entry_id"])
            except NotFoundError as exc:
                raise ValidationError(f"lines[{index}].loan_entry_id", "貸款記錄不存在") from exc
        open_amount = Decimal(0) if loan.is_closed else settlement_service.open_amount(db, loan)
        if open_amount == 0:
            _close_out(db, definition, instance, later, actor)
            return PostResult(instance.id, "loan_closed")

    written: list[int] = []
    grouped: list[int] = []
    for index, (line, amount) in enumerate(zip(template["lines"], amounts)):
        if amount == 0:
            continue
        try:
            ids, groupable = _write_line(db, definition, instance, line, amount, loan, open_amount)
        except ValidationError as exc:
            if exc.field.startswith("lines["):
                raise
            raise ValidationError(f"lines[{index}].{exc.field}", "無法入帳") from exc
        written += ids
        if groupable:
            grouped += ids
    if not written:
        raise ValidationError("amounts", "這一期沒有要入帳的金額")
    if len(grouped) >= 2:
        _group(db, definition, instance, grouped)

    instance.status, instance.posted_entry_ids = "posted", written
    instance.acted_at, instance.acted_by, instance.is_partial = _now(), actor, False
    instance.last_error, instance.last_error_at, instance.reopened_at = None, None, None
    db.flush()
    return PostResult(instance.id, "posted", tuple(written))


def failure_message(exc: Exception) -> str:
    """last_error text: the field and the amount-free message; other errors by class name only."""
    if isinstance(exc, ValidationError):
        return f"{exc.field}: {exc.message}"[:MAX_ERROR_LENGTH]
    return exc.__class__.__name__


def record_failure(db: Session, instance_id: int, message: str) -> None:
    """Roll the failed posting back, then store last_error on the still-pending instance in its own transaction.

    Like every schedule write it shares the import key first (D32); while an import holds the key the write is
    skipped (the instance stays pending and the import refreshes it) and nothing is raised.
    """
    db.rollback()
    try:
        take_import_key_shared(db)
    except ImportRunningError:
        db.rollback()
        return
    db.execute(
        update(ScheduleInstance)
        .where(ScheduleInstance.id == instance_id, ScheduleInstance.status == "pending")
        .values(last_error=message[:MAX_ERROR_LENGTH], last_error_at=_now())
        .execution_options(synchronize_session=False)
    )
    db.commit()


def delete_period_entries(
    db: Session, entry_ids: list[int], *, also_lock: int | None = None
) -> LedgerEntry | None:
    """Delete a period's entries (reopen, repost) in the ledger's lock order: their entry_group rows ascending →
    the entries with their transfer legs in one statement. The cutover lock applies to every row (a MOZE-booked
    period's entries are MOZE rows until cutover). `also_lock`: an entry (the loan of a repost) locked in that same
    single statement and not deleted; it is returned so post_locked reuses the row instead of a second entry-lock
    statement."""
    ids = sorted(set(entry_ids))
    if not ids and also_lock is None:
        return None
    transfer_groups = {
        row for row in db.scalars(select(LedgerEntry.transfer_group_id).where(LedgerEntry.id.in_(ids))) if row is not None
    }
    condition = LedgerEntry.id.in_(ids)
    if transfer_groups:
        condition = or_(
            condition,
            and_(LedgerEntry.transfer_group_id.in_(transfer_groups), LedgerEntry.parent_entry_id.is_(None)),
        )
    group_ids = sorted(
        {row for row in db.scalars(select(LedgerEntry.group_id).where(condition)) if row is not None}
    )
    for group_id in group_ids:
        entry_write_service.lock_group(db, group_id)
    lock_condition = condition if also_lock is None else or_(condition, LedgerEntry.id == also_lock)
    locked = list(
        db.scalars(
            select(LedgerEntry)
            .where(lock_condition)
            .order_by(LedgerEntry.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    extra = next((entry for entry in locked if entry.id == also_lock), None)
    doomed = [entry for entry in locked if entry.id != also_lock]
    for entry in doomed:
        entry_write_service.assert_entry_editable(db, entry)
    if doomed:
        entry_write_service.delete_entries_cascade(db, [entry.id for entry in doomed])
    for group_id in group_ids:
        remaining = db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.group_id == group_id))
        if remaining == 0:
            db.execute(delete(EntryGroup).where(EntryGroup.id == group_id))
    db.flush()
    return extra
