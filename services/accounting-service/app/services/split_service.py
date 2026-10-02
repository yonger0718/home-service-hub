"""Splits (design D15): an entry_group of kind split whose members each move one account.

Every member is validated and FX-resolved before the first row is written, so a split is created or
replaced completely or not at all. The cutover lock covers the group row and every member.
"""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import EntryGroup, LedgerEntry
from ..schemas.writes import EntryIn, SplitIn
from .edit_lock import assert_editable
from .entry_write_service import (
    PreparedEntry,
    delete_entries_cascade,
    has_settlements_or_refunds,
    insert_prepared,
    lock_group,
    prepare_entry,
)
from .errors import NotFoundError, ValidationError

SHARED_FIELDS = ("entry_date", "entry_time", "posted_date", "project_id", "tags")


def member_payloads(payload: SplitIn) -> list[EntryIn]:
    """Each member as a full EntryIn; fields the member did not send come from the group."""
    members = []
    for member in payload.members:
        data = member.model_dump()
        for field in SHARED_FIELDS:
            if field not in member.model_fields_set or (field == "entry_date" and data[field] is None):
                data[field] = getattr(payload, field)
        members.append(EntryIn(**data))
    return members


def _prepare_all(db: Session, payload: SplitIn, http_get) -> list[PreparedEntry]:
    prepared = []
    for index, member in enumerate(member_payloads(payload)):
        try:
            prepared.append(prepare_entry(db, member, http_get=http_get))
        except ValidationError as exc:
            raise ValidationError(f"members.{index}.{exc.field}", exc.message) from exc
    return prepared


def _get_split(db: Session, group_id: int) -> EntryGroup:
    group = db.get(EntryGroup, group_id)
    if group is None or group.kind != "split":
        raise NotFoundError(f"split {group_id} not found")
    return group


def member_ids(db: Session, group_id: int) -> list[int]:
    return list(
        db.scalars(
            select(LedgerEntry.id)
            .where(LedgerEntry.group_id == group_id, LedgerEntry.parent_entry_id.is_(None))
            .order_by(LedgerEntry.seq)
        )
    )


# Lock order for every write that touches a group member (shared with entry_write_service.delete_entry): the
# entry_group row (entry_write_service.lock_group) → the entry / the group's members → their transfer legs →
# nothing else. The group row is the one stable row two writes to a group share (members are deleted and
# re-inserted), so it is what makes a second PUT / DELETE wait for the first and then see the first's members
# instead of the ones it replaced; a single DELETE /entries/{id} of a member takes the same group lock first, so
# it never deadlocks against a split PUT (plan review round 5).


def _locked_split(db: Session, group_id: int) -> EntryGroup:
    """The split's entry_group row, SELECT … FOR UPDATE via lock_group; NotFoundError if a concurrent delete
    removed it."""
    group = lock_group(db, group_id)
    if group is None or group.kind != "split":
        raise NotFoundError(f"split {group_id} not found")
    return group


def _locked_members(db: Session, group_id: int) -> list[LedgerEntry]:
    """The group's members, one SELECT … FOR UPDATE ordered by ascending id (the row lock settle / refund take
    via locked_entry; the same order locked_with_legs uses). Callers hold the group lock from _locked_split first."""
    return list(
        db.scalars(
            select(LedgerEntry)
            .where(LedgerEntry.group_id == group_id, LedgerEntry.parent_entry_id.is_(None))
            .order_by(LedgerEntry.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )


def _assert_no_settlements(db: Session, members: list[LedgerEntry]) -> None:
    """Rebuilding members would re-sign a settlement (a +200 collection becomes a −200 receivable) and orphan
    the entries that settle or refund a member, so such groups are never rebuilt."""
    for member in members:
        if member.is_settlement or member.kind == "refund" or has_settlements_or_refunds(db, member.id):
            raise ValidationError(
                "members", "groups containing settlements or refunds are edited by deleting and re-settling"
            )


def create_split(db: Session, payload: SplitIn, *, http_get=None) -> int:
    prepared = _prepare_all(db, payload, http_get)
    group = EntryGroup(kind="split", name=payload.name, merchant=payload.merchant, description=payload.description)
    db.add(group)
    db.flush()
    for item in prepared:
        insert_prepared(db, item, group_id=group.id)
    return group.id


def update_split(db: Session, group_id: int, payload: SplitIn, *, http_get=None) -> None:
    """Replace every member (and their children) and the group fields.

    `_prepare_all` runs first because FX resolution may commit the session, which would release the row
    locks; then the group row and the members are locked FOR UPDATE (group → members) and checked inside
    that lock, so no settle / refund can commit between the check and the delete, and a concurrent PUT /
    DELETE of the same group waits and then sees this one's members. Members are never created with
    is_settlement: SplitMemberIn has no such field and insert_prepared stores False."""
    assert_editable(_get_split(db, group_id))
    prepared = _prepare_all(db, payload, http_get)
    group = _locked_split(db, group_id)
    assert_editable(group)
    members = _locked_members(db, group_id)
    _assert_no_settlements(db, members)
    delete_entries_cascade(db, [member.id for member in members])
    group.name = payload.name
    group.merchant = payload.merchant
    group.description = payload.description
    db.flush()
    for item in prepared:
        insert_prepared(db, item, group_id=group_id)


def delete_split(db: Session, group_id: int) -> None:
    """Same lock order as update_split (group → members), so it waits for a concurrent PUT and then deletes
    that PUT's members rather than the ones the PUT already replaced."""
    group = _locked_split(db, group_id)
    assert_editable(group)
    members = _locked_members(db, group_id)
    delete_entries_cascade(db, [member.id for member in members])
    db.execute(delete(EntryGroup).where(EntryGroup.id == group_id))
