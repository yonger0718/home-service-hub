"""Splits (design D15): an entry_group of kind split whose members each move one account.

Every member is validated and FX-resolved before the first row is written, so a split is created or
replaced completely or not at all. The cutover lock covers the group row and every member.
"""

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import EntryGroup, LedgerEntry
from ..schemas.writes import MAX_SPLIT_MEMBERS, EntryIn, SplitIn, SplitKeepIn, SplitMemberIn
from . import schedule_entry_hooks
from .edit_lock import assert_editable
from .entry_write_service import (
    EDITABLE_KINDS,
    PreparedEntry,
    attached_rule_ids,
    check_project,
    delete_entries_cascade,
    has_settlements_or_refunds,
    insert_prepared,
    lock_group,
    prepare_entry,
    remember_all_defaults,
)
from .errors import NotFoundError, ValidationError

SHARED_FIELDS = ("entry_date", "entry_time", "posted_date", "project_id", "tags")


def member_payload(payload: SplitIn, member: SplitMemberIn) -> EntryIn:
    """One full member as an EntryIn; fields the member did not send come from the group, except posted_date,
    which defaults to the member's own entry_date when the member sent one. The rework's client sends every
    per-member field; this inheritance stays for callers that omit them (§1.3)."""
    data = member.model_dump(exclude={"id", "client_key"})
    own_date = data["entry_date"]  # captured before the loop fills entry_date from the group
    for field in SHARED_FIELDS:
        if field not in member.model_fields_set or (field == "entry_date" and data[field] is None):
            if field == "posted_date" and own_date is not None:
                data[field] = own_date  # a member with its own entry_date posts on that date, not the group's
            else:
                data[field] = getattr(payload, field)
    return EntryIn(**data)


def member_payloads(payload: SplitIn) -> list[EntryIn]:
    """member_payload for every full member, in request order (keep members carry no entry payload)."""
    return [member_payload(payload, member) for member in payload.members if isinstance(member, SplitMemberIn)]


def _prepare_all(db: Session, payload: SplitIn, http_get) -> list[PreparedEntry]:
    prepared = []
    for index, member in enumerate(member_payloads(payload)):
        try:
            prepared.append(prepare_entry(db, member, http_get=http_get))
        except ValidationError as exc:
            raise ValidationError(f"members.{index}.{exc.field}", exc.message) from exc
    return prepared


@dataclass(frozen=True)
class SplitResult:
    """What every split write answers (§1.6): member ids with their client keys, in request order."""

    group_id: int | None
    members: list[tuple[int, str | None]]

    def out(self) -> dict:
        return {
            "group_id": self.group_id,
            "member_ids": [entry_id for entry_id, _ in self.members],
            "members": [{"id": entry_id, "client_key": key} for entry_id, key in self.members],
        }


@dataclass
class _Member:
    """One payload member through the phases. action: "new" (no id), "full" (SplitMemberIn with an id) or "keep"
    (SplitKeepIn). For "full", `stored` is the split_compare signature the decision was taken on and `change`
    its verdict ("unchanged" / "meta" / "financial"); `prepared` is set for "new" and financially changed
    "full" members."""

    index: int
    item: SplitMemberIn | SplitKeepIn
    entry_id: int | None
    action: str
    payload: EntryIn | None = None
    stored: object | None = None
    change: str | None = None
    prepared: PreparedEntry | None = None


def _prepare(db: Session, members: list[_Member], http_get) -> None:
    """Phase 2: prepare_entry for new members and financially changed full ones (a full member passes its current
    rule links as `attached`, so a rule disabled or expired after import still round-trips); a metadata-only
    member only has its project checked. Never called with a lock held: FX resolution may commit the session.
    Errors are renamed members.{i}.{field}."""
    for member in members:
        try:
            if member.action == "new" or member.change == "financial":
                attached = attached_rule_ids(db, member.entry_id) if member.entry_id is not None else ()
                member.prepared = prepare_entry(db, member.payload, http_get=http_get, attached_rules=attached)
            elif member.action == "full" and member.change == "meta":
                check_project(db, member.payload.project_id)
            elif member.action == "keep" and "project_id" in member.item.model_fields_set:
                check_project(db, member.item.project_id)
        except ValidationError as exc:
            raise ValidationError(f"members.{member.index}.{exc.field}", exc.message) from exc


def _check_create_cardinality(payload: SplitIn) -> None:
    """POST /splits: 2 to MAX_SPLIT_MEMBERS members, none of them existing (§1.3)."""
    if not 2 <= len(payload.members) <= MAX_SPLIT_MEMBERS:
        raise ValidationError("members", f"a new split has 2 to {MAX_SPLIT_MEMBERS} members")
    for index, item in enumerate(payload.members):
        if isinstance(item, SplitKeepIn):
            raise ValidationError(f"members.{index}.keep", "a new split has no existing members")
        if item.id is not None:
            raise ValidationError(f"members.{index}.id", "a new split has no existing members")


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


# Lock order for every write that locks a group row (shared with entry_write_service.delete_entry): the
# entry_group row(s) (entry_write_service.lock_group, ascending id) → the entry / the group's members (one
# SELECT … FOR UPDATE by ascending id; delete_entry adds the transfer legs to that same statement) → no further
# row lock except the category rows of the defaults, written last in ascending id (remember_all_defaults). The
# group row is the one stable row two writes to a group share (members are deleted and re-inserted), so it is
# what makes a second PUT / DELETE wait for the first and then see the first's members instead of the ones it
# replaced; a single DELETE /entries/{id} of a member takes the same group lock first, so it never deadlocks
# against a split PUT (plan review round 5).
#
# Exception: PUT /entries/{id}, settle and refund on a member do NOT take the group lock. They lock only the
# target entry row (locked_entry) and lock nothing else in the group afterwards, which is why they cannot
# invert the order above. Adding a group-row or second-member write to any of them would break this; such a
# change must take the group lock first like delete_entry.


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


def _assert_no_transfers_or_system_entries(members: list[LedgerEntry]) -> None:
    """Imported split groups (MOZE packages) can hold a transfer leg whose counterpart is outside the group, or
    reward / interest / balance-adjustment rows. Rebuilding or deleting the group would orphan the counterpart or
    delete rows delete_entry refuses, so such groups are edited one entry at a time."""
    for member in members:
        if member.transfer_group_id is not None or member.kind not in EDITABLE_KINDS:
            raise ValidationError("members", "groups containing transfers, rewards or system entries are edited per entry")


def create_split_result(db: Session, payload: SplitIn, *, http_get=None) -> SplitResult:
    """POST /splits: cardinality, then every member prepared (FX may commit), then the group and members written."""
    _check_create_cardinality(payload)
    members = [
        _Member(index, item, None, "new", payload=member_payload(payload, item))
        for index, item in enumerate(payload.members)
    ]
    _prepare(db, members, http_get)
    group = EntryGroup(kind="split", name=payload.name, merchant=payload.merchant, description=payload.description)
    db.add(group)
    db.flush()
    for member in members:
        member.entry_id = insert_prepared(db, member.prepared, group_id=group.id, remember=False)
    remember_all_defaults(db, [member.prepared for member in members])
    return SplitResult(group.id, [(member.entry_id, member.item.client_key) for member in members])


def create_split(db: Session, payload: SplitIn, *, http_get=None) -> int:
    return create_split_result(db, payload, http_get=http_get).group_id


def update_split(db: Session, group_id: int, payload: SplitIn, *, http_get=None) -> None:
    """Replace every member (and their children) and the group fields.

    `_prepare_all` runs first because FX resolution may commit the session, which would release the row
    locks; then the group row and the members are locked FOR UPDATE (group → members) and checked inside
    that lock, so no settle / refund can commit between the check and the delete, and a concurrent PUT /
    DELETE of the same group waits and then sees this one's members. Members are never created with
    is_settlement: SplitMemberIn has no such field and insert_prepared stores False."""
    schedule_entry_hooks.assert_group_not_scheduled(db, group_id)
    assert_editable(_get_split(db, group_id))
    prepared = _prepare_all(db, payload, http_get)
    group = _locked_split(db, group_id)
    assert_editable(group)
    members = _locked_members(db, group_id)
    _assert_no_settlements(db, members)
    _assert_no_transfers_or_system_entries(members)
    delete_entries_cascade(db, [member.id for member in members])
    group.name = payload.name
    group.merchant = payload.merchant
    group.description = payload.description
    db.flush()
    for item in prepared:
        insert_prepared(db, item, group_id=group_id, remember=False)
    remember_all_defaults(db, prepared)


def delete_split(db: Session, group_id: int) -> None:
    """Same lock order as update_split (group → members), after the schedule rows of a posted period that lists the
    members (D32). Groups holding transfer legs or non-editable kinds are refused under the member locks; settled
    members may be deleted (links are cleared)."""
    instance = schedule_entry_hooks.lock_for_entry_delete(db, member_ids(db, group_id))
    group = _locked_split(db, group_id)
    assert_editable(group)
    members = _locked_members(db, group_id)
    _assert_no_transfers_or_system_entries(members)
    doomed = [member.id for member in members]  # read before the cascade expires (and deletes) the rows
    delete_entries_cascade(db, doomed)
    db.execute(delete(EntryGroup).where(EntryGroup.id == group_id))
    if instance is not None:
        schedule_entry_hooks.after_entries_deleted(db, instance, set(doomed))
