"""The ledger's side of schedules: deleting an entry of a posted period (design D33) and the rows a definition's
template references (D29). Imported by entry_write_service, split_service and settings_service; it imports none of
them, so there is no import cycle."""

from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import LedgerEntry, ScheduleDefinition, ScheduleInstance
from . import ledger_service
from .errors import ConflictError
from .schedule_locks import lock_definition, lock_instance, take_import_key_shared
from .schedule_templates import aligned_override


def _instance_listing(db: Session, entry_ids: list[int]) -> ScheduleInstance | None:
    """The posted instance whose posted_entry_ids contains one of the entries (unlocked; GIN @> lookups)."""
    if not entry_ids:
        return None
    return db.scalar(
        select(ScheduleInstance)
        .where(
            ScheduleInstance.status == "posted",
            or_(*[ScheduleInstance.posted_entry_ids.contains([entry_id]) for entry_id in entry_ids]),
        )
        .order_by(ScheduleInstance.id)
        .limit(1)
    )


def posted_instance_for(db: Session, entry_ids: list[int]) -> ScheduleInstance | None:
    """The posted instance listing one of the entries, or None (unlocked read). Whatever the entry's source: a
    MOZE-booked period lists moze-source rows."""
    return _instance_listing(db, entry_ids)


DEFINITION_ENDED_RETRY = "排程剛結束，請重試刪除"


def lock_for_entry_delete(db: Session, entry_ids: list[int]) -> ScheduleInstance | None:
    """D32 for the entry-delete path: read without a lock, then the shared import key → definition FOR SHARE
    (FOR UPDATE when it is ended: after_entries_deleted may revive it, and two FOR SHARE holders that both upgrade
    deadlock) → instance FOR UPDATE, and re-check that it still lists one of the entries. Call before any group /
    entry lock."""
    ids = sorted(set(entry_ids))
    found = _instance_listing(db, ids)
    if found is None:
        return None
    take_import_key_shared(db)
    peek = db.get(ScheduleDefinition, found.definition_id)
    share = peek is None or peek.status != "ended"
    definition = lock_definition(db, found.definition_id, share=share)
    if share and definition.status == "ended":
        # Ended meanwhile (a loan close-out committed first). The SPA shows the message as is, so it is the owner's
        # text, not a code: retrying takes the definition FOR UPDATE and may revive it.
        raise ConflictError(DEFINITION_ENDED_RETRY)
    instance = lock_instance(db, found.id)
    if instance is None or instance.status != "posted" or not set(ids) & set(instance.posted_entry_ids):
        return None
    return instance


def after_entries_deleted(db: Session, instance: ScheduleInstance, deleted_ids: set[int]) -> None:
    """D33: the period becomes partial, or pending (reopened) / skipped (MOZE-booked) when nothing is left."""
    today = ledger_service._today().isoformat()
    definition = db.get(ScheduleDefinition, instance.definition_id)
    remaining = [entry_id for entry_id in instance.posted_entry_ids if entry_id not in deleted_ids]
    if remaining:
        instance.posted_entry_ids, instance.is_partial = remaining, True
        instance.note = f"部分入帳記錄已於 {today} 刪除"
    elif instance.acted_by == "import":
        instance.status, instance.posted_entry_ids, instance.is_partial = "skipped", [], False
        instance.note = "入帳記錄已刪除"  # MOZE booked it: never re-posted
    else:
        instance.status, instance.posted_entry_ids, instance.is_partial = "pending", [], False
        instance.acted_at, instance.acted_by = None, None
        instance.amount_override = aligned_override(definition.template, instance.amount_override)
        instance.reopened_at = datetime.now(timezone.utc)  # never auto-posted again; waits in 待完成交易
        instance.note = f"入帳記錄已於 {today} 刪除"
    if (instance.status == "pending" or instance.is_partial) and definition.status == "ended":
        definition.status = "active"
    db.flush()


def definitions_referencing(
    db: Session,
    *,
    account_id: int | None = None,
    category_id: int | None = None,
    counterparty_id: int | None = None,
    project_id: int | None = None,
    loan_entry_id: int | None = None,
) -> list[ScheduleDefinition]:
    """Definitions that are not ended and whose template names the row (JSONB containment on template.lines)."""
    lines = ScheduleDefinition.template["lines"]
    keys = {
        "account_id": account_id, "to_account_id": account_id, "category_id": category_id,
        "counterparty_id": counterparty_id, "project_id": project_id, "loan_entry_id": loan_entry_id,
    }
    conditions = [lines.contains([{key: value}]) for key, value in keys.items() if value is not None]
    if not conditions:
        return []
    return list(
        db.scalars(
            select(ScheduleDefinition)
            .where(ScheduleDefinition.status != "ended", or_(*conditions))
            .order_by(ScheduleDefinition.id)
        )
    )


def assert_not_referenced(db: Session, **ids) -> None:
    found = definitions_referencing(db, **ids)
    if found:
        raise ConflictError(f"排程「{found[0].name}」仍在使用，請先結束排程")


def assert_group_not_scheduled(db: Session, group_id: int) -> None:
    """A split PUT would replace the members a posted period lists; the owner edits one period instead (D29)."""
    member_ids = list(db.scalars(select(LedgerEntry.id).where(LedgerEntry.group_id == group_id)))
    instance = _instance_listing(db, member_ids)
    if instance is not None:
        definition = db.get(ScheduleDefinition, instance.definition_id)
        raise ConflictError(f"排程期別 {instance.id}（{definition.name} #{instance.seq}）使用這組記錄，請用編輯這一筆")
