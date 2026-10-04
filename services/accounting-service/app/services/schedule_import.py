"""The backup importer's schedule step (design D32, D37).

Runs inside the importer's ledger transaction while the import advisory key is held exclusively. Owner and job
decisions (periods posted / skipped by auto or owner, owner-edited pending periods, posting_mode, auto_post_from) are
never overwritten; locally created definitions are never written except their loan links (restore_links).
"""

import copy
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Collection

from sqlalchemy import exists, select, update
from sqlalchemy.orm import Session, aliased

from ..models import Category, LedgerEntry, ScheduleDefinition, ScheduleInstance
from . import settlement_service
from .schedule_import_map import TRANSFER_TYPE, MapResult, MappedDefinition, MappedInstance
from .schedule_rules import plain
from .schedule_templates import (
    LEDGER_KINDS, LINE_KEYS, LOAN_LINE_KINDS, aligned_override, loan_line, realign_override, referenced_ids,
    template_amounts,
)

BUCKETS = {"period": "recurring", "installment": "installment", "single": "single"}
NOT_LIVE = "not_live"  # review_reason of a MOZE period the import ended for having no enabled future record
INSTANCE_COUNTERS = (
    "created", "updated", "kept", "kept_owner_edited", "adopted", "deleted", "posted_from_past", "skipped_disabled",
)


@dataclass
class CapturedLinks:
    template_links: list[tuple[int, int, str]] = field(default_factory=list)  # (definition id, line index, moze_id)
    settlement_links: list[tuple[int, str]] = field(default_factory=list)  # (schedule entry id, moze_id of its target)


@dataclass(frozen=True)
class Covered:
    """A past MOZE record HomeHub already decided through the instance `instance_id`: booked it (`posted`), dropped
    it (`skipped`), or holds it as an owner-edited pending period (`owner_pending`, Multica R-F1: the owner's pending
    choice wins, the record is suppressed). None of them is imported as an entry (D37).

    `moze_lines` is MOZE's amount per template line of the period — (|amount|, |in-leg amount| for a transfer line,
    else None) — for the per-line comparison (R-F5); `day` is the record date; `account_amounts` the record's signed
    `total` per MOZE account id, keyed by the record id (the balance comparison of a suppressed `owner_pending` or
    skipped record; suppressed_amounts leaves out the records MOZE does not count, ruling R3)."""

    instance_id: int
    status: str  # posted | skipped | owner_pending
    moze_lines: tuple[tuple[str, str | None], ...]
    day: date
    account_amounts: tuple[tuple[str, str, Decimal], ...] = ()  # (record id, MOZE account id, signed total)


def _jsonable(value):
    def default(item):
        if isinstance(item, Decimal):
            return str(item)
        if isinstance(item, (datetime, date)):
            return item.isoformat()
        raise TypeError(type(item).__name__)

    return json.loads(json.dumps(value, default=default, ensure_ascii=False))


def new_report(mapped: MapResult) -> dict:
    zero = {"created": 0, "updated": 0, "ended": 0, "deleted": 0}
    return {
        "definitions": {bucket: dict(zero) for bucket in ("recurring", "installment", "single")},
        "instances": dict.fromkeys(INSTANCE_COUNTERS, 0),
        "records_mapped": mapped.records_mapped,
        "rewards_ignored": mapped.rewards_ignored,
        "unsupported_types": dict(sorted(mapped.unsupported_types.items())),
        "past_records_already_posted": 0,
        "past_records_already_skipped": 0,
        # per line (R-F5): count / instance_ids of posted periods with a differing line, `lines` the items (owner-facing)
        "past_records_amount_differs": {"count": 0, "instance_ids": [], "lines": []},
        "past_records_owner_pending": [],  # R-F1: {definition_id, seq, date} of suppressed records held by the owner
        "relinked": {"templates": 0, "settlements": 0},
        "review": list(mapped.review),
        # per line (R-F5, R-A2): pending periods whose amounts the import keeps (template_owner_edited definitions,
        # owner-edited periods) and MOZE's differ; owner-facing, never logged
        "amount_differs": [],
        "loan_remainder_check": [],
    }


def lock_schedule_rows(session: Session) -> None:
    """D32: before any entry is deleted, every definition, then every instance, FOR UPDATE by ascending id."""
    session.execute(select(ScheduleDefinition.id).order_by(ScheduleDefinition.id).with_for_update()).all()
    session.execute(select(ScheduleInstance.id).order_by(ScheduleInstance.id).with_for_update()).all()


def merge_known_past_singles(session: Session, mapped: MapResult) -> None:
    """A past record without a definition is an ordinary entry — unless HomeHub holds its `record:<id>` definition
    (a single future record mapped by an earlier import, posted or skipped by HomeHub since). Those join
    mapped.definitions, so covered_records keeps HomeHub's booking (the record is not imported twice) and
    apply_schedules neither ends the definition nor loses the period (spec "Record mapping")."""
    if mapped.past_singles:
        known = set(
            session.scalars(select(ScheduleDefinition.moze_id).where(ScheduleDefinition.moze_id.like("record:%")))
        )
        mapped.definitions.extend(item for item in mapped.past_singles if item.moze_id in known)
    mapped.past_singles = []


def capture_links(session: Session) -> CapturedLinks:
    """Before the full replace deletes MOZE entries: loan links of local templates and the targets of schedule
    settlements, by the target's moze_id (imported templates are rebuilt by apply_schedules)."""
    captured = CapturedLinks()
    for definition in session.scalars(
        select(ScheduleDefinition).where(ScheduleDefinition.created_locally.is_(True)).order_by(ScheduleDefinition.id)
    ):
        for index, line in enumerate(definition.template["lines"]):
            loan_id = line.get("loan_entry_id")
            entry = session.get(LedgerEntry, loan_id) if loan_id is not None else None
            if entry is not None and entry.moze_id is not None:
                captured.template_links.append((definition.id, index, entry.moze_id))
    target = aliased(LedgerEntry)
    captured.settlement_links = [
        (entry_id, moze_id)
        for entry_id, moze_id in session.execute(
            select(LedgerEntry.id, target.moze_id)
            .join(target, target.id == LedgerEntry.settles_entry_id)
            .where(LedgerEntry.source == "schedule", target.moze_id.is_not(None))
            .order_by(LedgerEntry.id)
        )
    ]
    return captured


def _adoptable(rows: list[ScheduleInstance], day: date) -> ScheduleInstance | None:
    free = [row for row in rows if row.moze_id is None]
    return next((row for row in free if row.rule_date == day), None) or next((row for row in free if row.due_date == day), None)


def _find(rows: list[ScheduleInstance], mapped: MappedInstance) -> tuple[ScheduleInstance | None, bool]:
    """(row, adopted): by moze_id over every row first, then by a merged record id (MOZE changed the package's
    primary record: the caller moves the row to the new moze_id), then an adoptable HomeHub row."""
    row = next((row for row in rows if row.moze_id == mapped.moze_id), None) or next(
        (row for row in rows if set(mapped.record_ids) & set(row.moze_record_ids or [])), None
    )
    if row is not None:
        return row, False
    row = _adoptable(rows, mapped.day)
    return row, row is not None


def _moze_lines(template: dict, amounts: list, records: list[dict]) -> tuple[tuple[str, str | None], ...]:
    """MOZE's amount per template line (R-F5): `amounts` is the mapped |total| per line (record → line identity kept by
    the mapping: a packaged repayment and its interest land on their own lines, "0" for a missing member); a transfer
    line also carries its in-leg record's |total|. `records` is empty when the amounts were realigned to a kept
    template (account_missing): the in-leg is then not compared."""
    in_leg = next((record for record in records if record["type"] == TRANSFER_TYPE and record["isTransferIn"]), None)
    lines = []
    for line, amount in zip(template["lines"], amounts):
        in_amount = None
        if line["kind"] == "transfer" and in_leg is not None:
            in_amount = plain(abs(Decimal(str(in_leg["total"]))))
        lines.append((plain(amount), in_amount))
    return tuple(lines)


def _entry_line_kind(entry: LedgerEntry) -> str:
    if entry.kind in ("transfer_out", "transfer_in"):
        return "transfer"
    if entry.is_settlement:
        return "repayment" if entry.kind == "payable" else "collection"
    return entry.kind  # expense, income, receivable, payable, interest


def _posted_lines(session: Session, template: dict, entry_ids: list[int]) -> list[tuple[str, str | None]]:
    """What HomeHub posted per template line, from the period's entries (posted_entry_ids keeps the line order and a
    "0" line wrote nothing): (|amount|, |in-leg amount| for a transfer line, else None); "0" for a missing entry."""
    found = {entry.id: entry for entry in session.scalars(select(LedgerEntry).where(LedgerEntry.id.in_(entry_ids)))}
    pool = [found[entry_id] for entry_id in entry_ids if entry_id in found]
    used: set[int] = set()
    lines: list[tuple[str, str | None]] = []
    for line in template["lines"]:
        out = next(
            (entry for entry in pool
             if entry.id not in used and entry.kind != "transfer_in" and _entry_line_kind(entry) == line["kind"]),
            None,
        )
        if out is not None:
            used.add(out.id)
        in_amount = None
        if line["kind"] == "transfer":
            leg = next((entry for entry in pool if entry.id not in used and entry.kind == "transfer_in"), None)
            if leg is not None:
                used.add(leg.id)
            in_amount = plain(abs(Decimal(leg.amount))) if leg is not None else "0"
        lines.append((plain(abs(Decimal(out.amount))) if out is not None else "0", in_amount))
    return lines


def _pending_lines(definition: ScheduleDefinition, row: ScheduleInstance) -> list[tuple[str, str | None]]:
    """What a pending period will post per line: its override, else the template; a transfer's in-leg is the line's
    to_amount (cross-currency), else the same amount."""
    amounts = list(row.amount_override) if row.amount_override is not None else template_amounts(definition.template)
    lines: list[tuple[str, str | None]] = []
    for line, amount in zip(definition.template["lines"], amounts):
        in_amount = None
        if line["kind"] == "transfer":
            in_amount = plain(line["to_amount"]) if line.get("to_amount") is not None else plain(amount)
        lines.append((plain(amount), in_amount))
    return lines


def _line_differences(definition: ScheduleDefinition, row: ScheduleInstance, ours, theirs) -> list[dict]:
    """One owner-facing item per differing line (R-F5): out-leg / entry amount under the line's kind, a transfer's
    in-leg under `transfer_in`. Never logged."""
    items = []

    def item(index: int, kind: str, amount: str, moze_amount: str) -> dict:
        return {
            "definition_id": definition.id, "name": definition.name, "seq": row.seq, "date": row.due_date.isoformat(),
            "line": index, "kind": kind, "amount": amount, "moze_amount": moze_amount,
        }

    for index, (line, (amount, in_amount), (moze_amount, moze_in)) in enumerate(
        zip(definition.template["lines"], ours, theirs)
    ):
        if Decimal(amount) != Decimal(moze_amount):
            items.append(item(index, line["kind"], amount, moze_amount))
        if line["kind"] == "transfer" and in_amount is not None and moze_in is not None:
            if Decimal(in_amount) != Decimal(moze_in):
                items.append(item(index, "transfer_in", in_amount, moze_in))
    return items


def covered_records(session: Session, mapped: MapResult) -> dict[str, Covered]:
    """Past MOZE records HomeHub already covered: their instance (matched or adoptable) is posted by auto / owner,
    skipped by anyone but import, or — Multica R-F1 — pending and owner-edited (the owner's pending choice wins: the
    record is suppressed, the instance stays pending with its override and a later post writes the period once).
    These records are not imported as entries (D37)."""
    covered: dict[str, Covered] = {}
    definitions = {
        row.moze_id: row for row in session.scalars(select(ScheduleDefinition).where(ScheduleDefinition.moze_id.is_not(None)))
    }
    for mapped_definition in mapped.definitions:
        definition = definitions.get(mapped_definition.moze_id)
        if definition is None:
            continue
        rows = list(session.scalars(select(ScheduleInstance).where(ScheduleInstance.definition_id == definition.id)))
        for item in mapped_definition.instances:
            if not item.past:
                continue
            row, _ = _find(rows, item)
            if row is None:
                continue
            kept_posted = row.status == "posted" and row.acted_by in ("auto", "owner")
            kept_skipped = row.status == "skipped" and row.acted_by != "import"
            owner_pending = row.status == "pending" and row.edited_by_owner and item.enabled
            if kept_posted or kept_skipped or owner_pending:
                status = "posted" if kept_posted else "skipped" if kept_skipped else "owner_pending"
                moze_lines = _moze_lines(definition.template, list(item.amounts), item.records)
                account_amounts = tuple(
                    (record["identifier"], record["account"], Decimal(str(record["total"])))
                    for record in item.records
                )
                for record_id in item.record_ids:
                    covered[record_id] = Covered(row.id, status, moze_lines, item.day, account_amounts)
    return covered


def suppressed_amounts(
    covered: dict[str, Covered],
    statuses: Collection[str] = ("owner_pending",),
    uncounted: Collection[str] = frozenset(),
) -> list[tuple[str, date, Decimal]]:
    """(MOZE account id, date, signed total) of every record suppressed for an owner-edited pending period (R-F1):
    MOZE booked them, HomeHub has not yet, so the balance comparison adds them to `moze_part` (Task 18). The full
    replace also passes `skipped`: the owner's skip is a deliberate disagreement with MOZE the comparison explains.
    Ruling R3: only records MOZE counts are compensated — a record in `uncounted` (disabled, the other leg of a
    disabled transfer, or a dependant of one: the importer's disabled rule) adds nothing; the owner's skip stays."""
    seen: set[int] = set()
    amounts = []
    for item in covered.values():
        if item.status not in statuses or item.instance_id in seen:
            continue
        seen.add(item.instance_id)
        amounts.extend(
            (account, item.day, total)
            for record_id, account, total in item.account_amounts
            if record_id not in uncounted
        )
    return amounts


def template_usage(session: Session) -> dict[str, set[int]]:
    """Rows any definition's template references: they count as used (never deleted or archived by the import)."""
    usage: dict[str, set[int]] = {"account": set(), "category": set(), "counterparty": set(), "project": set()}
    for template in session.scalars(select(ScheduleDefinition.template)):
        for key, ids in referenced_ids(template).items():
            if key in usage:
                usage[key] |= ids
    return usage


def stand_in_entry_ids(session: Session, covered: dict[str, Covered]) -> set[int]:
    """The schedule entries standing in for records skipped as already posted (balance comparison, D37)."""
    instance_ids = {item.instance_id for item in covered.values() if item.status == "posted"}
    if not instance_ids:
        return set()
    rows = session.scalars(select(ScheduleInstance.posted_entry_ids).where(ScheduleInstance.id.in_(instance_ids)))
    return {entry_id for entry_ids in rows for entry_id in entry_ids}


def _resolve_lines(
    mapped: MappedDefinition, settings, entries, category_kinds: dict[int, str]
) -> tuple[list[dict] | None, str | None]:
    lines, reason = [], None
    for source in mapped.lines:
        account = settings.accounts.get(source.account)
        if account is None:
            return None, "account_missing"
        line = dict.fromkeys(LINE_KEYS)
        category_id = settings.categories.get(source.classification)
        line.update(
            kind=source.kind, account_id=account.id, amount=source.amount, currency=account.currency, name=source.name,
            merchant=source.store, project_id=settings.projects.get(source.project),
            category_id=category_id if category_kinds.get(category_id) == LEDGER_KINDS[source.kind] else None,
        )
        if source.kind == "transfer":
            target = settings.accounts.get(source.to_account)
            if target is None:
                return None, "account_missing"
            line["to_account_id"] = target.id
            line["to_amount"] = source.to_amount if target.currency != account.currency else None
        if source.kind in ("receivable", "payable"):
            line["counterparty_id"] = settings.counterparties.get(source.target)
        if source.kind in LOAN_LINE_KINDS:
            loan = entries.entries.get(source.related) if source.related else None
            line["loan_entry_id"] = loan.id if loan is not None else None
            if loan is None:
                reason = "loan_missing"
        lines.append(line)
    return lines, reason


def _clear_pending_marks(row: ScheduleInstance) -> None:
    """A period MOZE booked or dropped no longer shows a failure, a reopen or a delete note from its pending days."""
    row.last_error, row.last_error_at, row.reopened_at, row.note = None, None, None, None


def _recompute_import_row(row: ScheduleInstance, entries, started_at: datetime) -> bool:
    """Spec: an `acted_by = import` instance is recomputed from the newly imported entries; with none left it becomes
    `skipped` (acted_by import). Used for rows whose record left the backup (no mapped item). True when changed."""
    entry_ids = [entries.entries[record_id].id for record_id in row.moze_record_ids or [] if record_id in entries.entries]
    if entry_ids:
        if row.status == "posted" and row.posted_entry_ids == entry_ids:
            return False
        row.status, row.posted_entry_ids, row.is_partial = "posted", entry_ids, False
    else:
        if row.status == "skipped" and not row.posted_entry_ids:
            return False
        row.status, row.posted_entry_ids, row.is_partial = "skipped", [], False
    row.acted_at = started_at
    _clear_pending_marks(row)
    return True


def _moze_status(row: ScheduleInstance, mapped: MappedInstance, entries, started_at: datetime) -> str:
    """Set the status MOZE gives the period: skipped (disabled, or past without an imported entry), posted (past,
    imported), pending (future)."""
    entry_ids = [entries.entries[record_id].id for record_id in mapped.record_ids if record_id in entries.entries]
    if not mapped.enabled or (mapped.past and not entry_ids):
        row.status, row.posted_entry_ids, row.is_partial = "skipped", [], False
        row.acted_at, row.acted_by = started_at, "import"
        _clear_pending_marks(row)
        return "skipped"
    if mapped.past:
        row.status, row.posted_entry_ids, row.is_partial = "posted", entry_ids, False
        row.acted_at, row.acted_by = started_at, "import"
        _clear_pending_marks(row)
        return "posted"
    row.status, row.posted_entry_ids, row.is_partial, row.acted_at, row.acted_by = "pending", [], False, None, None
    return "pending"


def _keep_owner_amounts(old: dict, new: dict) -> dict:
    """template_owner_edited (proposal decision 24): MOZE refreshes the lines, the owner's per-line amounts stay for a
    line at the same index with the same kind."""
    kept = copy.deepcopy(new)
    for index, line in enumerate(kept["lines"]):
        if index < len(old["lines"]) and old["lines"][index]["kind"] == line["kind"]:
            line["amount"] = old["lines"][index]["amount"]
    return kept


def _note_amount_differs(
    report: dict, definition: ScheduleDefinition, row: ScheduleInstance, moze_lines: tuple[tuple[str, str | None], ...]
) -> None:
    """The owner's amounts stand (a template_owner_edited definition, or an owner-edited pending period — R-A2); each
    line whose MOZE amount differs from what the period will post (its override, else the template) is listed for the
    owner, per line (R-F5; report only — logs carry ids and counts)."""
    report["amount_differs"].extend(_line_differences(definition, row, _pending_lines(definition, row), moze_lines))


def _live(mapped: MappedDefinition) -> bool:
    """Spec maps each *live* AHPeriod: one with at least one enabled future record. Installments and singles are
    always live here (their series is fixed by MOZE's dateInfo / the record itself)."""
    return mapped.source != "period" or any(item.enabled and not item.past for item in mapped.instances)


def _upsert_definition(session: Session, mapped: MappedDefinition, template: dict, review_reason: str | None,
                       existing: ScheduleDefinition | None, today: date, report: dict, *,
                       live: bool = True) -> ScheduleDefinition:
    """Create or refresh one imported definition. A period that is not live (no enabled future record: cancelled or
    finished in MOZE, or not yet pre-generated during the mirror month) is set `ended` with review_reason
    `not_live`, so HomeHub never generates or auto-posts after MOZE stopped; the reason makes the end reversible —
    a later backup with an enabled future record sets it back to `active` (`paused` with a mapping reason) and
    clears the reason. Only an end the import made is undone: an owner's `ended` (any other reason) stays."""
    bucket = report["definitions"][BUCKETS[mapped.source]]
    if existing is not None and existing.template_owner_edited:
        template = _keep_owner_amounts(existing.template, template)  # the owner's price stands (decision 24)
    values = {
        "kind": mapped.kind, "name": mapped.name, "template": template, "interval_unit": mapped.interval_unit,
        "interval_n": mapped.interval_n, "anchor_date": mapped.anchor_date, "day_of_month": mapped.day_of_month,
        "times": mapped.times, "total_amount": mapped.total_amount,
        "moze_payload": _jsonable(mapped.payload) if mapped.payload is not None else None,
    }
    if existing is None:
        status = "ended" if not live else "paused" if review_reason else "active"
        definition = ScheduleDefinition(
            moze_id=mapped.moze_id, **values, posting_mode="auto", status=status, auto_post_from=today,
            created_locally=False, review_reason=NOT_LIVE if not live else review_reason,
        )
        session.add(definition)
        session.flush()
        bucket["created"] += 1
        if not live:
            bucket["ended"] += 1
        return definition
    changed = False
    old_template = existing.template
    for key, value in values.items():
        current = getattr(existing, key)
        if key == "total_amount" and current is not None and value is not None:
            same = Decimal(current) == Decimal(value)
        else:
            same = current == value
        if not same:
            setattr(existing, key, value)
            changed = True
    if existing.template != old_template:
        # Global constraint: overrides stay aligned with the template's lines on every write — owner-edited rows
        # included (their override is kept per line where the kind still matches, D35). A posted row's override is
        # metadata (the amounts it was posted with, pinned by Task 12): never cleared or overwritten here.
        for row in session.scalars(
            select(ScheduleInstance).where(
                ScheduleInstance.definition_id == existing.id, ScheduleInstance.status != "posted"
            )
        ):
            row.amount_override = realign_override(old_template, existing.template, row.amount_override)
    if not live:
        if existing.status != "ended":
            # The import ends it and remembers why; pending rows are left to _apply_instances, which drops the
            # MOZE-sourced ones whose record left the backup and keeps owner-edited and HomeHub-generated ones (D37).
            existing.status, existing.review_reason, changed = "ended", NOT_LIVE, True
            bucket["ended"] += 1
        elif existing.review_reason != NOT_LIVE and existing.review_reason != review_reason:
            existing.review_reason, changed = review_reason, True  # the owner's end stays; the reason follows MOZE
    elif existing.review_reason == NOT_LIVE:
        # MOZE has an enabled future record again: undo the import's own end (a new mapping reason pauses instead).
        existing.status = "paused" if review_reason else "active"
        existing.review_reason, changed = review_reason, True
    else:
        if review_reason is not None and review_reason != existing.review_reason and existing.status != "ended":
            existing.status = "paused"  # a reason new in this import pauses; the owner's own status stays otherwise
        if existing.review_reason != review_reason:
            existing.review_reason = review_reason
            changed = True
    if changed:
        bucket["updated"] += 1
    session.flush()
    return existing


def _day_taken(rows: list[ScheduleInstance], row: ScheduleInstance | None, day: date) -> bool:
    """Another posted or pending (any non-skipped) period of the definition holds `day`: moving or creating a period
    there would put two periods on one day (ux_schedule_instance_posted_day once both post; Task 12 refuses the same
    move with 422 due_date)."""
    return any(other is not row and other.status != "skipped" and other.due_date == day for other in rows)


def _follow_moze_date(rows: list[ScheduleInstance], row: ScheduleInstance, day: date) -> bool:
    """A row matched by its moze_id follows the date MOZE gives the period, unless the day is taken. True when moved."""
    if (row.rule_date, row.due_date) == (day, day) or _day_taken(rows, row, day):
        return False
    row.rule_date, row.due_date = day, day
    return True


def _apply_instances(session: Session, definition: ScheduleDefinition, mapped: MappedDefinition, entries,
                     started_at: datetime, report: dict, *, realign_from: dict | None = None) -> None:
    """Upsert the definition's instances from the mapped periods. `realign_from` is the mapped template when the
    definition keeps its stored template (account_missing): MOZE's per-period amounts are then realigned to the
    stored lines with realign_override, so no override goes out of line with the template (Global Constraints).
    On a template_owner_edited definition pending rows get no MOZE override; differences go to amount_differs."""
    counters = report["instances"]
    rows = list(
        session.scalars(select(ScheduleInstance).where(ScheduleInstance.definition_id == definition.id).order_by(ScheduleInstance.id))
    )
    mapped_ids: set[str] = set()
    matched: set[int] = set()  # rows a mapped period matched or created: never swept by the final loop
    for item in mapped.instances:
        mapped_ids.add(item.moze_id)
        if item.seq is None:
            continue
        amounts = (
            list(item.amounts) if realign_from is None
            else realign_override(realign_from, definition.template, list(item.amounts))
        )
        if amounts is None:
            # nothing of MOZE's lines kept its index and kind in the stored template: the period follows the template
            amounts = template_amounts(definition.template)
        moze_lines = _moze_lines(definition.template, amounts, item.records if realign_from is None else [])
        row, adopted = _find(rows, item)
        if row is None:
            if any(other.seq == item.seq for other in rows):
                report["review"].append({"moze_id": item.moze_id, "reason": "seq_conflict"})
                continue
            if definition.status == "ended" and not item.past and item.enabled:
                continue  # no pending period for an ended definition
            row = ScheduleInstance(
                definition_id=definition.id, seq=item.seq, rule_date=item.day, due_date=item.day, moze_id=item.moze_id,
                moze_record_ids=list(item.record_ids), moze_payload=_jsonable(item.records), amount_override=amounts,
            )
            status = _moze_status(row, item, entries, started_at)
            if status != "skipped" and _day_taken(rows, None, item.day):
                report["review"].append({"moze_id": item.moze_id, "reason": "same_date"})
                continue  # another posted or pending period holds the day; the record is not mapped
            if status == "pending" and definition.template_owner_edited:
                row.amount_override = None  # the period follows the owner's template price (decision 24)
                _note_amount_differs(report, definition, row, moze_lines)
            session.add(row)
            session.flush()
            rows.append(row)
            matched.add(row.id)
            counters["created"] += 1
            if status == "posted":
                counters["posted_from_past"] += 1
            elif status == "skipped" and not item.enabled:
                counters["skipped_disabled"] += 1
            continue
        matched.add(row.id)
        if adopted:
            row.moze_id = item.moze_id
            counters["adopted"] += 1
        elif row.moze_id != item.moze_id:
            row.moze_id = item.moze_id  # matched by a record id: MOZE changed the package's primary record
        row.moze_record_ids = list(item.record_ids)
        row.moze_payload = _jsonable(item.records)
        if row.status == "pending":
            if row.edited_by_owner:
                # Kept as the owner left it — also when its record is now past (R-F1: the record is suppressed by
                # covered_records, never imported beside it); its effective amounts are still compared (R-A2).
                counters["kept_owner_edited"] += 1
                if item.enabled:
                    _note_amount_differs(report, definition, row, moze_lines)
            elif item.past or not item.enabled:
                # MOZE booked it (e.g. paid early: the record moved to today) or dropped it: the row takes the
                # record's day first, so the period, its entry and a later repost share one date.
                if not adopted:
                    _follow_moze_date(rows, row, item.day)
                _moze_status(row, item, entries, started_at)
                counters["updated"] += 1
            else:
                # Spec: pending imported instances are refreshed unless owner-edited — amounts, and for a row matched
                # by its moze_id also the dates MOZE moved. A row adopted in this import keeps its dates (spec "Moved
                # period adopted": the HomeHub row's rule_date matched). A new date held by another posted or pending
                # period is refused (two pending periods on one day would collide on ux_schedule_instance_posted_day
                # when both post — Task 12 refuses the same move with 422 due_date).
                owner_price = definition.template_owner_edited  # decision 24: no MOZE override, only a report line
                changed = not owner_price and row.amount_override != amounts
                if not owner_price:
                    row.amount_override = amounts
                if not adopted and _follow_moze_date(rows, row, item.day):
                    changed = True
                if owner_price:
                    _note_amount_differs(report, definition, row, moze_lines)
                counters["updated" if changed else "kept"] += 1
        elif row.acted_by == "import":
            if not adopted:
                _follow_moze_date(rows, row, item.day)  # a past record MOZE moved moves the period with it
            if _moze_status(row, item, entries, started_at) == "pending":
                # Back to pending (its record is enabled and future again): the pending refresh rules apply. An
                # owner-priced definition (decision 24) takes no MOZE override, the period follows the owner's
                # template; an owner-edited row keeps its override, aligned with the template.
                if definition.template_owner_edited:
                    row.amount_override = None
                    _note_amount_differs(report, definition, row, moze_lines)
                elif row.edited_by_owner:
                    row.amount_override = aligned_override(definition.template, row.amount_override)
                else:
                    row.amount_override = amounts
            counters["updated"] += 1
        else:
            counters["kept"] += 1  # posted by auto / owner, or skipped by HomeHub: the owner's decision stays
        session.flush()
    for row in rows:
        if row.id in matched:
            continue
        if row.moze_id is not None and row.moze_id not in mapped_ids and row.status == "pending" and not row.edited_by_owner:
            session.delete(row)
            counters["deleted"] += 1
        elif row.moze_id not in mapped_ids and row.acted_by == "import" and _recompute_import_row(row, entries, started_at):
            counters["updated"] += 1  # its record left the backup: recomputed from the entries now imported
    session.flush()


def _end_absent(session: Session, seen: set[int], entries, started_at: datetime, report: dict) -> None:
    absent = session.scalars(
        select(ScheduleDefinition)
        .where(ScheduleDefinition.moze_id.is_not(None), ScheduleDefinition.created_locally.is_(False))
        .order_by(ScheduleDefinition.id)
    )
    for definition in absent:
        if definition.id in seen:
            continue
        bucket = report["definitions"][
            "single" if definition.moze_id.startswith("record:") else
            "installment" if definition.kind == "installment" else "recurring"
        ]
        for row in session.scalars(
            select(ScheduleInstance).where(
                ScheduleInstance.definition_id == definition.id, ScheduleInstance.acted_by == "import"
            )
        ):
            if _recompute_import_row(row, entries, started_at):
                report["instances"]["updated"] += 1  # its records left the backup with the definition
        history = session.scalar(
            select(
                exists().where(
                    ScheduleInstance.definition_id == definition.id, ScheduleInstance.status.in_(("posted", "skipped"))
                )
            )
        )
        if history:
            for row in session.scalars(
                select(ScheduleInstance).where(ScheduleInstance.definition_id == definition.id, ScheduleInstance.status == "pending")
            ):
                session.delete(row)
            if definition.status != "ended":
                definition.status = "ended"
                bucket["ended"] += 1
        else:
            session.delete(definition)
            bucket["deleted"] += 1
    session.flush()


def _amount_differs(session: Session, covered: dict[str, Covered], report: dict) -> None:
    """past_records_amount_differs, per line (R-F5): each line HomeHub posted (from the period's entries) against the
    matching MOZE record's amount — never period totals, so 8333 + 620 against 8400 + 553 is reported."""
    by_instance: dict[int, Covered] = {}
    for item in covered.values():
        if item.status == "posted":
            by_instance[item.instance_id] = item
    differs, lines = [], []
    for instance_id, item in sorted(by_instance.items()):
        instance = session.get(ScheduleInstance, instance_id)
        if instance is None or not instance.posted_entry_ids:
            continue
        definition = session.get(ScheduleDefinition, instance.definition_id)
        found = _line_differences(
            definition, instance, _posted_lines(session, definition.template, instance.posted_entry_ids), item.moze_lines
        )
        if found:
            differs.append(instance_id)
            lines.extend(found)
    report["past_records_amount_differs"] = {"count": len(differs), "instance_ids": differs, "lines": lines}


def loan_check(session: Session, mapped: MapResult, report: dict) -> None:
    """loan_remainder_check. Runs after restore_links: until then the schedule settlements of the loans point
    nowhere (ON DELETE SET NULL), and open_amount would ignore every repayment HomeHub posted."""
    for item in mapped.definitions:
        if item.source != "installment" or item.remainder is None:
            continue
        definition = session.scalar(select(ScheduleDefinition).where(ScheduleDefinition.moze_id == item.moze_id))
        reference = loan_line(definition.template) if definition is not None else None
        loan_id = reference[1].get("loan_entry_id") if reference is not None else None
        loan = session.get(LedgerEntry, loan_id) if loan_id is not None else None
        if loan is None:
            continue
        open_amount = Decimal(0) if loan.is_closed else settlement_service.open_amount(session, loan)
        report["loan_remainder_check"].append(
            {
                "definition": definition.name, "moze_remainder": plain(item.remainder), "open_amount": plain(open_amount),
                "difference": plain(open_amount - item.remainder),
            }
        )


def apply_schedules(
    session: Session,
    data,
    mapped: MapResult,
    settings,
    entries,
    covered: dict[str, Covered],
    *,
    started_at: datetime,
    today: date,
) -> dict:
    """Upsert imported definitions and instances (after the entries are inserted); returns the report block."""
    report = new_report(mapped)
    category_kinds = dict(session.execute(select(Category.id, Category.kind)).all())
    existing = {
        row.moze_id: row
        for row in session.scalars(select(ScheduleDefinition).where(ScheduleDefinition.moze_id.is_not(None)))
    }
    seen: set[int] = set()
    for item in mapped.definitions:
        lines, reason = _resolve_lines(item, settings, entries, category_kinds)
        current = existing.get(item.moze_id)
        if lines is None:
            report["review"].append({"moze_id": item.moze_id, "reason": reason})
            if current is not None and not current.created_locally:
                seen.add(current.id)  # keep its definition and template rather than ending it …
                # … but its instances still follow the new entries (import-posted rows, records now past); MOZE's
                # amounts are realigned from the mapped lines to the kept template's lines
                mapped_template = {"lines": [{"kind": line.kind, "amount": line.amount} for line in item.lines]}
                _apply_instances(session, current, item, entries, started_at, report, realign_from=mapped_template)
            continue
        if current is not None and current.created_locally:
            continue
        review_reason = item.review_reason or reason
        if reason == "loan_missing" and item.review_reason is None:
            report["review"].append({"moze_id": item.moze_id, "reason": "loan_missing"})
        template = {"lines": lines, "description": None, "tags": []}
        definition = _upsert_definition(session, item, template, review_reason, current, today, report, live=_live(item))
        seen.add(definition.id)
        _apply_instances(session, definition, item, entries, started_at, report)
    _end_absent(session, seen, entries, started_at, report)
    report["past_records_already_posted"] = sum(1 for item in covered.values() if item.status == "posted")
    report["past_records_already_skipped"] = sum(1 for item in covered.values() if item.status == "skipped")
    held = sorted({item.instance_id for item in covered.values() if item.status == "owner_pending"})
    for instance_id in held:  # R-F1: the owner's pending choice won over a MOZE record now past
        row = session.get(ScheduleInstance, instance_id)
        if row is not None:
            report["past_records_owner_pending"].append(
                {"definition_id": row.definition_id, "seq": row.seq, "date": row.due_date.isoformat()}
            )
    _amount_differs(session, covered, report)
    return report  # loan_check runs after restore_links (Task 18 wiring)


def _pause_loan_missing(definition: ScheduleDefinition, report: dict) -> None:
    if definition.status != "ended":
        definition.status = "paused"
    definition.review_reason = "loan_missing"
    report["review"].append({"moze_id": definition.moze_id or f"definition:{definition.id}", "reason": "loan_missing"})


def restore_links(session: Session, captured: CapturedLinks, report: dict) -> None:
    """After the entries are re-inserted: re-point local loan links and schedule settlements to the entry now
    carrying the captured moze_id; a vanished target clears the link and pauses the definition (loan_missing)."""
    needed = {moze_id for _, _, moze_id in captured.template_links} | {moze_id for _, moze_id in captured.settlement_links}
    new_ids = (
        dict(session.execute(select(LedgerEntry.moze_id, LedgerEntry.id).where(LedgerEntry.moze_id.in_(needed))).all())
        if needed else {}
    )
    for definition_id, index, moze_id in captured.template_links:
        definition = session.get(ScheduleDefinition, definition_id)
        template = copy.deepcopy(definition.template)
        new_id = new_ids.get(moze_id)
        template["lines"][index]["loan_entry_id"] = new_id
        definition.template = template
        if new_id is None:
            _pause_loan_missing(definition, report)
        else:
            report["relinked"]["templates"] += 1
    for entry_id, moze_id in captured.settlement_links:
        new_id = new_ids.get(moze_id)
        session.execute(
            update(LedgerEntry).where(LedgerEntry.id == entry_id).values(settles_entry_id=new_id)
            .execution_options(synchronize_session=False)
        )
        if new_id is not None:
            report["relinked"]["settlements"] += 1
    for definition in session.scalars(select(ScheduleDefinition).where(ScheduleDefinition.created_locally.is_(True))):
        template, dangling = copy.deepcopy(definition.template), False
        for line in template["lines"]:
            if line.get("loan_entry_id") is not None and session.get(LedgerEntry, line["loan_entry_id"]) is None:
                line["loan_entry_id"], dangling = None, True
        if dangling:
            definition.template = template
            _pause_loan_missing(definition, report)
    session.flush()
