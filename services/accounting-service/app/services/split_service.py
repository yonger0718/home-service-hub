"""Splits (design D15; split rework §1): an entry_group of kind split whose members each move one account.

Every write follows the rework's phases (§1.4): (1) a preliminary read and classification with no lock and no FX;
(2) prepare: validation and FX resolution, which may commit the session through the FX cache
(fx_rate_service.get_rate), so it runs before any lock or ledger write; (3) locks in the accepted order; (4) a
re-read inside the locks, where anything that moved since (1) is a 409 retry (never a re-preparation); (5) the
writes, through helpers that never commit. The router commits once, so a split write lands completely or not at
all (FX cache rows excepted).
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
    apply_prepared_update,
    attached_rule_ids,
    check_project,
    delete_entries_cascade,
    dissolve_group,
    insert_prepared,
    lock_group,
    prepare_entry,
    remember_all_defaults,
)
from .errors import (
    GROUP_SCHEDULED,
    MEMBER_LOCKED,
    MEMBER_NOT_FOUND,
    RETRY,
    CodedConflictError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from .split_compare import MemberSignature, classify, payload_signature, stored_signature
from .split_protection import protected_reasons

SHARED_FIELDS = ("entry_date", "entry_time", "posted_date", "project_id", "tags")
KEEP_FIELDS = ("name", "project_id", "tags", "description")  # what SplitKeepIn may change


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
    (SplitKeepIn). For "full", `stored` is the signature the decision was taken on and `change` its verdict
    ("unchanged" / "meta" / "financial"); `prepared` is set for "new" and financially changed "full" members."""

    index: int
    item: SplitMemberIn | SplitKeepIn
    entry_id: int | None
    action: str
    payload: EntryIn | None = None
    stored: MemberSignature | None = None
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


def member_ids(db: Session, group_id: int) -> list[int]:
    return list(
        db.scalars(
            select(LedgerEntry.id)
            .where(LedgerEntry.group_id == group_id, LedgerEntry.parent_entry_id.is_(None))
            .order_by(LedgerEntry.seq)
        )
    )


# Lock order for every write that locks a group row (shared with entry_write_service.delete_entry and the schedule
# paths, D32): schedule rows (only the delete paths take them) → the entry_group row(s) (lock_group, ascending id) →
# the entries (one SELECT … FOR UPDATE by ascending id; delete_entry adds transfer legs and the members of the split
# groups it may dissolve to that same statement) → no further row lock except the category rows of the defaults,
# written last in ascending id (remember_all_defaults). A split PUT takes no schedule lock: a scheduled group is
# refused before and again inside the locks. Convert (convert_to_split) locks only its ungrouped anchor row.
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
    via locked_entry; the same order locked_with_legs uses). Callers hold the group lock first."""
    return list(
        db.scalars(
            select(LedgerEntry)
            .where(LedgerEntry.group_id == group_id, LedgerEntry.parent_entry_id.is_(None))
            .order_by(LedgerEntry.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )


def _assert_no_transfers_or_system_entries(members: list[LedgerEntry]) -> None:
    """DELETE /splits/{id} (unchanged by the rework): imported split groups can hold a transfer leg whose
    counterpart is outside the group, or reward / interest / balance-adjustment rows; deleting the group would
    orphan the counterpart or delete rows delete_entry refuses, so such groups are deleted one entry at a time."""
    for member in members:
        if member.transfer_group_id is not None or member.kind not in EDITABLE_KINDS:
            raise ValidationError("members", "groups containing transfers, rewards or system entries are edited per entry")


def _assert_not_scheduled(db: Session, group_id: int) -> None:
    """D29: a group a posted period lists is edited one period at a time; 409 group_scheduled naming the instance."""
    try:
        schedule_entry_hooks.assert_group_not_scheduled(db, group_id)
    except ConflictError as exc:
        raise CodedConflictError(GROUP_SCHEDULED, str(exc)) from exc


def _readable_split(db: Session, group_id: int) -> EntryGroup:
    """Phase 1 group checks in precedence order: 404 missing → 409 group_scheduled (before the kind check, so the
    accepted D29 scenario on an installment group still answers 409) → 404 not a split → 409 cutover lock."""
    group = db.get(EntryGroup, group_id)
    if group is None:
        raise NotFoundError(f"split {group_id} not found")
    _assert_not_scheduled(db, group_id)
    if group.kind != "split":
        raise NotFoundError(f"split {group_id} not found")
    assert_editable(group)
    return group


def _current_members(db: Session, group_id: int) -> list[LedgerEntry]:
    """The group's top-level members, unlocked, ascending id."""
    return list(
        db.scalars(
            select(LedgerEntry)
            .where(LedgerEntry.group_id == group_id, LedgerEntry.parent_entry_id.is_(None))
            .order_by(LedgerEntry.id)
        )
    )


def _check_put_cardinality(payload: SplitIn, current_count: int) -> None:
    """PUT /splits/{id}: at most max(50, current) members (an imported group over 50 stays editable but never
    grows); a one-member PUT is a dissolve and must name an existing member."""
    limit = max(MAX_SPLIT_MEMBERS, current_count)
    if len(payload.members) > limit:
        raise ValidationError("members", f"at most {limit} members")
    if len(payload.members) == 1 and payload.members[0].id is None:
        raise ValidationError("members.0.id", "a split edited down to one member keeps one of its members")


def _plan_members(payload: SplitIn, current: list[LedgerEntry]) -> list[_Member]:
    """keep_full / keep_meta / new per payload member; 404 member_not_found for an id outside the group."""
    known = {entry.id for entry in current}
    members = []
    for index, item in enumerate(payload.members):
        if item.id is not None and item.id not in known:
            raise NotFoundError(f"{MEMBER_NOT_FOUND}: entry {item.id} is not a member of this split")
        if isinstance(item, SplitKeepIn):
            members.append(_Member(index, item, item.id, "keep"))
        else:
            action = "new" if item.id is None else "full"
            members.append(_Member(index, item, item.id, action, payload=member_payload(payload, item)))
    return members


def _refuse_locked(members: list[_Member], drop_ids: list[int], protected: dict[int, str | None]) -> None:
    """§1.5: a protected member is only accepted in the keep form and is never dropped (409 member_locked)."""
    for member in members:
        reason = protected.get(member.entry_id) if member.action == "full" else None
        if reason is not None:
            raise CodedConflictError(
                MEMBER_LOCKED, f"member {member.entry_id} is protected ({reason}); send it as {{id, keep: true}}"
            )
    for entry_id in drop_ids:
        if protected[entry_id] is not None:
            raise CodedConflictError(MEMBER_LOCKED, f"member {entry_id} is protected ({protected[entry_id]}) and cannot be removed")


def _classify_full(db: Session, members: list[_Member], by_id: dict[int, LedgerEntry]) -> None:
    """The canonical comparison for every keep_full member (split_compare), on the unlocked read."""
    for member in members:
        if member.action == "full":
            entry = by_id[member.entry_id]
            member.stored = stored_signature(db, entry)
            member.change = classify(member.stored, payload_signature(member.payload, entry.currency))


def _revalidate(
    db: Session,
    group: EntryGroup | None,
    group_id: int,
    locked: list[LedgerEntry],
    before_ids: list[int],
    protected: dict[int, str | None],
    members: list[_Member],
) -> None:
    """Phase 4, inside the group and member locks: the group still a split, still editable and unscheduled; the
    same members, the same protected set and, for every keep_full member, the same stored signature as in phase 1.
    Any drift is a 409 retry: the client re-reads and re-submits."""
    if group is None or group.kind != "split":
        raise CodedConflictError(RETRY, f"split {group_id} changed concurrently")
    assert_editable(group)
    _assert_not_scheduled(db, group_id)
    by_id = {entry.id: entry for entry in locked}
    if sorted(by_id) != before_ids:
        raise CodedConflictError(RETRY, f"the members of split {group_id} changed concurrently")
    if protected_reasons(db, locked) != protected:
        raise CodedConflictError(RETRY, f"a member of split {group_id} was settled, refunded or scheduled concurrently")
    for member in members:
        if member.action == "full" and stored_signature(db, by_id[member.entry_id]) != member.stored:
            raise CodedConflictError(RETRY, f"member {member.entry_id} changed concurrently")


def _apply_meta(entry: LedgerEntry, payload: EntryIn) -> None:
    """A financially unchanged keep_full member: metadata only (no FX, no child or rule-link rebuild)."""
    entry.name, entry.merchant, entry.project_id = payload.name, payload.merchant, payload.project_id
    entry.tags, entry.description = list(payload.tags), payload.description


def _apply_keep(entry: LedgerEntry, item: SplitKeepIn) -> None:
    """A keep_meta member: only the fields the request sent (null and [] included)."""
    for field in KEEP_FIELDS:
        if field in item.model_fields_set:
            value = getattr(item, field)
            setattr(entry, field, list(value or []) if field == "tags" else value)


def update_split(db: Session, group_id: int, payload: SplitIn, *, http_get=None) -> SplitResult:
    """PUT /splits/{group_id}: upsert by member id (§1.5). keep_full members are rewritten in place (financially
    changed), get a metadata-only update (financially unchanged) or are skipped (fully unchanged); keep_meta
    members get the sent metadata; new members are inserted; current members absent from the payload are deleted.
    Protected members (split_protection) only take the keep form and are never dropped. Reward ledger rows are
    never touched. Errors in phase order: see _readable_split, _check_put_cardinality, _plan_members,
    _refuse_locked, _prepare, _revalidate."""
    # 1. preliminary read and classification: no lock, no FX
    _readable_split(db, group_id)
    current = _current_members(db, group_id)
    _check_put_cardinality(payload, len(current))
    members = _plan_members(payload, current)
    kept = {member.entry_id for member in members if member.entry_id is not None}
    drop_ids = sorted(entry.id for entry in current if entry.id not in kept)
    protected = protected_reasons(db, current)
    _refuse_locked(members, drop_ids, protected)
    _classify_full(db, members, {entry.id: entry for entry in current})
    before_ids = sorted(entry.id for entry in current)
    # 2. prepare: may commit through the FX cache, so it runs before any lock or ledger write
    _prepare(db, members, http_get)
    # 3. lock: the group row, then its members in one statement by ascending id
    group = lock_group(db, group_id)
    locked = _locked_members(db, group_id) if group is not None else []
    # 4. re-read and re-validate inside the locks
    _revalidate(db, group, group_id, locked, before_ids, protected, members)
    # 5. write: no helper below commits
    for entry_id in drop_ids:  # §1.5: the same refusal as a single delete; not kind-gated like SCHEDULED_LOAN
        schedule_entry_hooks.assert_not_referenced(db, loan_entry_id=entry_id)
    delete_entries_cascade(db, drop_ids)  # expires the session; rows are re-read below under the held locks
    for member in members:
        if member.action == "new":
            member.entry_id = insert_prepared(db, member.prepared, group_id=group_id, remember=False)
        elif member.action == "keep":
            _apply_keep(db.get(LedgerEntry, member.entry_id), member.item)
        elif member.change == "financial":
            apply_prepared_update(db, db.get(LedgerEntry, member.entry_id), member.prepared)
        elif member.change == "meta":
            _apply_meta(db.get(LedgerEntry, member.entry_id), member.payload)
    db.flush()
    result = [(member.entry_id, member.item.client_key) for member in members]
    if len(members) == 1:
        # Dissolve (§1.6): the payload's parent values fill the survivor's blank fields, then the group goes.
        survivor = db.get(LedgerEntry, members[0].entry_id)
        dissolve_group(db, group_id, survivor, name=payload.name, merchant=payload.merchant, description=payload.description)
        group_out = None
    else:
        group = db.get(EntryGroup, group_id)
        group.name, group.merchant, group.description = payload.name, payload.merchant, payload.description
        db.flush()
        group_out = group_id
    remember_all_defaults(db, [member.prepared for member in members if member.prepared is not None])
    return SplitResult(group_out, result)


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
