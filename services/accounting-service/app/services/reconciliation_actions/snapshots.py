"""Row snapshots for the action engine (spec §4.9, C6): what an action expects (read unlocked in `prepare`), what the
locked rows are (`before`) and what it left (`after`), as JSON-ready dicts so a stored snapshot compares equal to a
fresh one. Keys of `snapshot_set` are string ids (JSONB object keys are strings), so a set read back from an action
row diffs cleanly against a new read. Reads only; every read refreshes the identity map (`populate_existing`)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time
from decimal import Decimal
from typing import Iterable
from uuid import UUID

from sqlalchemy import inspect, or_, select
from sqlalchemy.orm import Session

from app.models import EntryGroup, LedgerEntry, ScheduleInstance, StatementCoverage

_RELATIONS = (  # entry snapshot key → the column on OTHER entries that points at the entry
    ("child_ids", LedgerEntry.parent_entry_id),
    ("refund_ids", LedgerEntry.refunds_entry_id),
    ("settlement_ids", LedgerEntry.settles_entry_id),
    ("reward_ids", LedgerEntry.reward_source_entry_id),
)


def _json(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    return value


def _columns(row) -> dict:
    return {attr.key: _json(getattr(row, attr.key)) for attr in inspect(type(row)).column_attrs}


def _ids(values: Iterable) -> list[int]:
    return sorted({int(v) for v in values if v is not None})


def _entry_relations(db: Session, ids: list[int]) -> dict[int, dict]:
    """id → {child_ids, refund_ids, settlement_ids, reward_ids, instance_id, coverage_ids} in a fixed number of
    queries (one per relation)."""
    out = {i: {key: [] for key, _ in _RELATIONS} | {"instance_id": None, "coverage_ids": []} for i in ids}
    if not ids:
        return out
    for key, column in _RELATIONS:
        for other_id, target in db.execute(select(LedgerEntry.id, column).where(column.in_(ids))
                                           .order_by(LedgerEntry.id)):
            out[target][key].append(other_id)
    wanted = set(ids)
    for instance_id, posted in db.execute(
            select(ScheduleInstance.id, ScheduleInstance.posted_entry_ids)
            .where(or_(*(ScheduleInstance.posted_entry_ids.contains([i]) for i in ids)))
            .order_by(ScheduleInstance.id)):
        for entry_id in posted or []:
            if isinstance(entry_id, int) and entry_id in wanted and out[entry_id]["instance_id"] is None:
                out[entry_id]["instance_id"] = instance_id
    for coverage_id, entry_id in db.execute(
            select(StatementCoverage.id, StatementCoverage.entry_id)
            .where(StatementCoverage.entry_id.in_(ids), StatementCoverage.status == "active")
            .order_by(StatementCoverage.id)):
        out[entry_id]["coverage_ids"].append(coverage_id)
    return out


def _group_members(db: Session, ids: list[int]) -> dict[int, list[int]]:
    members: dict[int, list[int]] = defaultdict(list)
    if ids:
        for entry_id, group_id in db.execute(select(LedgerEntry.id, LedgerEntry.group_id)
                                             .where(LedgerEntry.group_id.in_(ids)).order_by(LedgerEntry.id)):
            members[group_id].append(entry_id)
    return members


def entry_snapshot(db: Session, entry: LedgerEntry) -> dict:
    """Every LedgerEntry column (Decimal → str, dates/times → ISO, UUID → str) plus `child_ids`, `refund_ids`,
    `settlement_ids`, `reward_ids` (sorted ids of entries pointing at it), `instance_id` (the posted schedule
    instance listing it, or None) and `coverage_ids` (active coverage rows on it)."""
    return _columns(entry) | _entry_relations(db, [entry.id])[entry.id]


def group_snapshot(db: Session, group: EntryGroup) -> dict:
    """Every EntryGroup column plus `member_ids` (sorted ids of every entry carrying the group)."""
    return _columns(group) | {"member_ids": _group_members(db, [group.id]).get(group.id, [])}


def snapshot_set(db: Session, entry_ids: Iterable, group_ids: Iterable) -> dict:
    """{"entries": {"<id>": entry_snapshot}, "groups": {"<id>": group_snapshot}} for the rows that exist (a missing
    id is simply absent, which `diff` reports against a set that had it). Flushes first: the refreshing read must
    never discard a pending change."""
    db.flush()
    e_ids, g_ids = _ids(entry_ids), _ids(group_ids)
    entries = list(db.execute(select(LedgerEntry).where(LedgerEntry.id.in_(e_ids)).order_by(LedgerEntry.id)
                              .execution_options(populate_existing=True)).scalars()) if e_ids else []
    groups = list(db.execute(select(EntryGroup).where(EntryGroup.id.in_(g_ids)).order_by(EntryGroup.id)
                             .execution_options(populate_existing=True)).scalars()) if g_ids else []
    relations = _entry_relations(db, [e.id for e in entries])
    members = _group_members(db, [g.id for g in groups])
    return {
        "entries": {str(e.id): _columns(e) | relations[e.id] for e in entries},
        "groups": {str(g.id): _columns(g) | {"member_ids": members.get(g.id, [])} for g in groups},
    }


def closure(db: Session, entry_ids: Iterable, group_ids: Iterable) -> tuple[list[int], list[int]]:
    """The rows an action on `entry_ids`/`group_ids` depends on: the entries, every member of the groups, and the
    children of all of those (sorted ids; groups unchanged)."""
    entries, groups = set(_ids(entry_ids)), _ids(group_ids)
    if groups:
        entries |= set(db.execute(select(LedgerEntry.id).where(LedgerEntry.group_id.in_(groups))).scalars())
    if entries:
        entries |= set(db.execute(select(LedgerEntry.id)
                                  .where(LedgerEntry.parent_entry_id.in_(sorted(entries)))).scalars())
    return sorted(entries), groups


def expected_set(db: Session, entry_ids: Iterable, group_ids: Iterable) -> dict:
    """`snapshot_set` over `closure`: the C6 expected snapshot `prepare` returns (read without locks)."""
    return snapshot_set(db, *closure(db, entry_ids, group_ids))


def ids_of(snapshots: dict) -> tuple[list[int], list[int]]:
    """The entry and group ids a snapshot set covers (to re-read the same rows)."""
    return _ids((snapshots or {}).get("entries", {})), _ids((snapshots or {}).get("groups", {}))


def diff(before: dict, current: dict) -> list[str]:
    """Dotted paths whose values differ between two snapshot dicts (a key on one side only is a difference);
    [] when equal. Lists compare as whole values."""
    out: list[str] = []

    def walk(a, b, path: str) -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b), key=str):
                child = f"{path}.{key}" if path else str(key)
                if key not in a or key not in b:
                    out.append(child)
                else:
                    walk(a[key], b[key], child)
        elif a != b:
            out.append(path)

    walk(before or {}, current or {}, "")
    return out
