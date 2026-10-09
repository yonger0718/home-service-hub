"""Reconciliation orchestration (spec v3 §6, §7.4; v4 §4.6, §5.7–5.8): one statement per transaction. Never commits:
the batch (`pending_statement_ids` + `reconcile_each`) yields per statement and its caller commits each one.

`reconcile` lock order (§7.4, the sweep barrier of coverage_service): the shared import key → the statement row
FOR UPDATE → flush → `coverage_service.sweep` (briefly takes the dirty barrier; it must run before any entry_group /
ledger_entry lock, because a writer holding the shared barrier may be waiting on a row this transaction would hold)
→ entry_group rows ascending → the candidate entries, their
children and their transfer legs in ONE ordered SELECT … FOR UPDATE → re-read the population under those locks →
match → coverage → cases → counts. Reconcile writes no ledger rows, so it emits no dirty events of its own.

No statement row other than its own is locked or referenced by a statement's reconcile: a deferral case gets its
`deferred_to_statement_id` only from the LATER statement's reconcile (`_link_earlier_deferrals`, case rows only) or
from `fill_deferral_links`.

Re-running is idempotent: a line whose active claim equals the new claim (same rows, roles, rule and snapshots)
keeps its coverage rows; every other active row of the statement (including orphans whose entry was deleted) is
released with reason `reconcile` and the new claim written. Likewise an open/proposed matcher case equal to a newly
derived one (kind, event/entry, candidates, context without the carried `sweep` note) is kept (its line pointer refreshed), every other open matcher
case is superseded (version bumped) and the rest are created. Dismissed cases are never reopened. Cases, including
deferral explanations, are written in live mode only (§5.8)."""
from __future__ import annotations

import json
from calendar import monthrange
from collections import defaultdict
from dataclasses import dataclass, field, fields, replace
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from typing import Iterable, Iterator

from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.orm import Session, aliased

from app.models import (
    Account, AccountStatement, EntryGroup, InstallmentPlanMap, LedgerEntry, ReconciliationCase, ScheduleDefinition,
    ScheduleInstance, StatementCoverage, StatementLine, StatementRevision,
)
from app.services import coverage_service, settings_service
from app.services.entry_write_service import lock_group, proposed_fx_fee
from app.services.errors import NotFoundError
from app.services.ledger_service import account_balance
from app.services.schedule_locks import take_import_key_shared
from app.services.statements import matching

participating_accounts = coverage_service.participating_accounts

OPEN = ("open", "proposed")
# Open cases the matcher owns: superseded when a run no longer derives them (parse_review / statement_conflict are not).
MATCHER_KINDS = ("line_unmatched", "ambiguous", "amount_delta", "entry_unmatched", "duplicate_claim", "balance_gap",
                 "recheck")
DEFERRED = "deferred_next_period"
UNCOVERED_CHILD = "uncovered_child"
CROSS_ACCOUNT_DAYS = 3
_Q = Decimal("0.0001")


@dataclass
class ReconcileResult:
    claims: int = 0  # lines claimed by this run (kept or newly written)
    cases_opened: list[int] = field(default_factory=list)  # open cases created by this run
    explained: int = 0  # entries explained as deferred_next_period by this run
    unmatched_entries: int = 0  # reverse-population entries left unmatched and not deferred
    skipped: str | None = None  # "no_current_revision" | "parse_review"


# --- inputs ------------------------------------------------------------------------------------------------------

def rules_for(raw: dict, period_end: date) -> matching.Rules:
    """matching.Rules from the settings' JSON rules (unknown keys ignored, types from the dataclass defaults)."""
    kwargs = {}
    for f in fields(matching.Rules):
        if f.name == "period_end" or f.name not in raw:
            continue
        value, default = raw[f.name], f.default
        if isinstance(default, bool):  # before int: bool is an int subclass
            value = value is True or str(value).lower() == "true"
        elif isinstance(default, Decimal):
            value = Decimal(str(value))
        elif isinstance(default, tuple):
            value = tuple(value)
        elif isinstance(default, int):
            value = int(value)
        kwargs[f.name] = value
    return matching.Rules(period_end=period_end, **kwargs)


def plan_map(db: Session, account_id: int) -> dict[str, int]:
    rows = db.execute(select(InstallmentPlanMap.plan_key, InstallmentPlanMap.definition_id)
                      .where(InstallmentPlanMap.account_id == account_id))
    return {key: definition_id for key, definition_id in rows}


def next_period_end(period_end: date, closing_day: int | None) -> date:
    """Card (`closing_day`): the next closing date after `period_end`, the day clamped to the month's length.
    Bank (None): the last day of the calendar month after `period_end`."""
    def following(year: int, month: int) -> tuple[int, int]:
        return (year + 1, 1) if month == 12 else (year, month + 1)

    if closing_day is None:
        year, month = following(period_end.year, period_end.month)
        return date(year, month, monthrange(year, month)[1])
    year, month = period_end.year, period_end.month
    while True:
        candidate = date(year, month, min(closing_day, monthrange(year, month)[1]))
        if candidate > period_end:
            return candidate
        year, month = following(year, month)


def _current_lines(db: Session, statement: AccountStatement) -> list[StatementLine]:
    return list(db.execute(select(StatementLine).where(StatementLine.revision_id == statement.current_revision_id)
                           .order_by(StatementLine.id)).scalars())


def _as_line(row: StatementLine) -> matching.Line:
    return matching.Line(
        id=row.id, event_id=row.event_id, posted_date=row.posted_date, txn_date=row.txn_date, flow=row.flow_amount,
        foreign_amount=row.foreign_amount, foreign_currency=row.foreign_currency, line_kind=row.line_kind,
        merchant_norm=row.merchant_norm, installment_seq=row.installment_seq, installment_total=row.installment_total)


def _instances(db: Session, ids: list[int]) -> dict[int, matching.InstanceRef]:
    """Entry id → the posted schedule instance listing it (`posted_entry_ids @> [id]`) with its definition's times."""
    out: dict[int, matching.InstanceRef] = {}
    wanted = set(ids)
    for start in range(0, len(ids), 200):
        chunk = ids[start:start + 200]
        rows = db.execute(
            select(ScheduleInstance.posted_entry_ids, ScheduleInstance.definition_id, ScheduleInstance.seq,
                   ScheduleDefinition.times)
            .join(ScheduleDefinition, ScheduleDefinition.id == ScheduleInstance.definition_id)
            .where(or_(*(ScheduleInstance.posted_entry_ids.contains([i]) for i in chunk)))
            .order_by(ScheduleInstance.id))
        for posted, definition_id, seq, times in rows:
            for entry_id in posted or []:
                if isinstance(entry_id, int) and entry_id in wanted:
                    out.setdefault(entry_id, matching.InstanceRef(definition_id, seq, times))
    return out


def load_candidates(db: Session, statement: AccountStatement, accounts: list[int], *, window_days: int = 10,
                    include_reward: bool | None = None) -> tuple[list[matching.Entry], dict[int, matching.Group]]:
    """The §6.1 population: entries of `accounts` dated (posted_date, else entry_date) within `window_days` of the
    period, no `balance_adjustment`, `reward` only when the statement prints a reward line (`include_reward` None:
    read from the current revision), none claimed by another statement; children attached to their parent as
    `matching.Child` (never as rows); groups carry ALL their top-level members (a member outside the window leaves
    the group partial, which matching rejects)."""
    if include_reward is None:
        include_reward = any(line.line_kind == "reward" for line in _current_lines(db, statement))
    lo = statement.period_start - timedelta(days=window_days)
    hi = statement.period_end + timedelta(days=window_days)
    day = func.coalesce(LedgerEntry.posted_date, LedgerEntry.entry_date)
    kinds_out = ["balance_adjustment"] + ([] if include_reward else ["reward"])
    claimed = coverage_service.claimed_entry_ids(db, exclude_statement_id=statement.id)
    parents = [e for e in db.execute(
        select(LedgerEntry)
        .where(LedgerEntry.account_id.in_(accounts), LedgerEntry.parent_entry_id.is_(None), day >= lo, day <= hi,
               LedgerEntry.kind.not_in(kinds_out))
        .order_by(LedgerEntry.id)).scalars() if e.id not in claimed]
    ids = [e.id for e in parents]
    children: dict[int, list[matching.Child]] = defaultdict(list)
    if ids:
        for c in db.execute(select(LedgerEntry).where(LedgerEntry.parent_entry_id.in_(ids))
                            .order_by(LedgerEntry.id)).scalars():
            if c.id not in claimed:
                children[c.parent_entry_id].append(matching.Child(c.id, c.kind, Decimal(c.amount)))
    transfer_ids = {e.transfer_group_id for e in parents if e.transfer_group_id is not None}
    peer_is_card: dict[int, bool] = {}
    if transfer_ids:
        legs = db.execute(
            select(LedgerEntry.id, LedgerEntry.transfer_group_id, Account.is_credit)
            .join(Account, Account.id == LedgerEntry.account_id)
            .where(LedgerEntry.transfer_group_id.in_(transfer_ids), LedgerEntry.parent_entry_id.is_(None))).all()
        for e in parents:
            if e.transfer_group_id is not None:
                peer = next((credit for leg_id, group, credit in legs
                             if group == e.transfer_group_id and leg_id != e.id), None)
                peer_is_card[e.id] = None if peer is None else bool(peer)
    instances = _instances(db, ids) if ids else {}
    group_ids = sorted({e.group_id for e in parents if e.group_id is not None})
    groups: dict[int, matching.Group] = {}
    if group_ids:
        members: dict[int, list[int]] = defaultdict(list)
        for member_id, group_id in db.execute(
                select(LedgerEntry.id, LedgerEntry.group_id)
                .where(LedgerEntry.group_id.in_(group_ids), LedgerEntry.parent_entry_id.is_(None))
                .order_by(LedgerEntry.id)):
            members[group_id].append(member_id)
        for g in db.execute(select(EntryGroup).where(EntryGroup.id.in_(group_ids))).scalars():
            groups[g.id] = matching.Group(g.id, g.kind, tuple(members[g.id]))
    entries = []
    for e in parents:
        posted = e.posted_date or e.entry_date
        entries.append(matching.Entry(
            id=e.id, kind=e.kind, flow=Decimal(e.amount), posted_date=posted, entry_date=e.entry_date, name=e.name,
            merchant=e.merchant, original_amount=None if e.original_amount is None else Decimal(e.original_amount),
            original_currency=e.original_currency, account_id=e.account_id, group_id=e.group_id,
            parent_entry_id=None, is_settlement=bool(e.is_settlement), refunds_entry_id=e.refunds_entry_id,
            transfer_group_id=None if e.transfer_group_id is None else str(e.transfer_group_id),
            transfer_peer_is_card=peer_is_card.get(e.id), children=tuple(children.get(e.id, ())),
            instance=instances.get(e.id), in_reverse=statement.period_start <= posted <= statement.period_end))
    return entries, groups


# --- locks -------------------------------------------------------------------------------------------------------

def _lock_statement(db: Session, statement_id: int) -> AccountStatement:
    statement = db.execute(select(AccountStatement).where(AccountStatement.id == statement_id).with_for_update()
                           .execution_options(populate_existing=True)).scalar_one_or_none()
    if statement is None:
        raise NotFoundError(f"statement {statement_id} not found")
    return statement


def _lock_entries(db: Session, entry_ids: set[int], transfer_group_ids: set) -> dict[int, LedgerEntry]:
    """The rows with every top-level leg of their transfers in ONE SELECT … FOR UPDATE ordered by id (the
    `locked_with_legs` shape over an id set)."""
    if not entry_ids:
        return {}
    condition = LedgerEntry.id.in_(entry_ids)
    if transfer_group_ids:
        condition = or_(condition, and_(LedgerEntry.transfer_group_id.in_(transfer_group_ids),
                                        LedgerEntry.parent_entry_id.is_(None)))
    rows = db.execute(select(LedgerEntry).where(condition).order_by(LedgerEntry.id).with_for_update()
                      .execution_options(populate_existing=True)).scalars()
    return {row.id: row for row in rows}


# --- outputs -----------------------------------------------------------------------------------------------------

def _claim_unchanged(rows: list[StatementCoverage], claim: matching.Claim, locked: dict[int, LedgerEntry]) -> bool:
    rep = claim.representation
    if len(rows) != len(rep.rows) or any(r.match_kind != "auto" or r.match_rule != rep.rule for r in rows):
        return False
    have = {r.entry_id: (r.role, r.group_id, r.snapshot) for r in rows}
    for row in rep.rows:
        entry = locked.get(row.entry_id)
        if entry is None or have.get(row.entry_id) != (row.role, row.group_id, coverage_service.snapshot_entry(entry)):
            return False
    return True


def _write_coverage(db: Session, statement: AccountStatement, result: matching.MatchResult,
                    lines: dict[int, StatementLine], locked: dict[int, LedgerEntry], run_id: int | None) -> None:
    active = coverage_service.active_rows(db, statement.id)
    claims = {c.line_id: c for c in result.claims}
    kept = {line_id for line_id, rows in active.items()
            if line_id in claims and _claim_unchanged(rows, claims[line_id], locked)}
    for line_id in sorted(set(active) - kept):  # releases first: an entry may move between lines
        coverage_service.release_line(db, statement, line_id, reason="reconcile")
    for claim in result.claims:
        if claim.line_id not in kept:
            rep = claim.representation
            coverage_service.write_claim(db, statement, lines[claim.line_id], list(rep.rows), entries_by_id=locked,
                                         match_kind="auto", match_rule=rep.rule, run_id=run_id)


def _cross_account_line(db: Session, statement: AccountStatement, accounts: list[int],
                        entry: matching.Entry) -> tuple[int, int] | None:
    """R10: an uncovered line of another live statement outside the participating set, same currency and flow,
    printed within ±3 days of the entry → (statement id, line id), the nearest date first."""
    line, stmt = StatementLine, AccountStatement
    covered = exists().where(StatementCoverage.line_id == line.id, StatementCoverage.status == "active")
    row = db.execute(
        select(stmt.id, line.id)
        .join(stmt, stmt.current_revision_id == line.revision_id)
        .where(stmt.mode == "live", stmt.id != statement.id, stmt.account_id.not_in(accounts),
               stmt.currency == statement.currency, line.flow_amount == entry.flow,
               line.posted_date >= entry.posted_date - timedelta(days=CROSS_ACCOUNT_DAYS),
               line.posted_date <= entry.posted_date + timedelta(days=CROSS_ACCOUNT_DAYS), ~covered)
        .order_by(func.abs(line.posted_date - entry.posted_date), line.id).limit(1)).first()
    return None if row is None else (row[0], row[1])


def _case(kind: str, *, event_id=None, line_id=None, entry_id=None, candidates=(), context=None) -> dict:
    return {"kind": kind, "event_id": event_id, "line_id": line_id, "entry_id": entry_id,
            "candidates": [dict(c) for c in candidates], "context": dict(context or {})}


SWEEP_NOTE = "sweep"  # context key carrying the dirty event that released the line (from the superseded case)


def _key(kind, event_id, entry_id, candidates, context) -> tuple:
    """A case's identity for the keep-or-supersede diff; the carried sweep note is not part of it."""
    context = {k: v for k, v in (context or {}).items() if k != SWEEP_NOTE}
    return (kind, event_id, entry_id, json.dumps(candidates, sort_keys=True, default=str),
            json.dumps(context, sort_keys=True, default=str))


def _sweep_note(case: ReconciliationCase) -> dict | None:
    """The dirty event a superseded case recorded: a sweep `recheck` case ({event_id, op, row_id}), a case the sweep
    bumped ({reopened_by_event, op, row_id}) or one that already carried a note."""
    context = case.context or {}
    if "reopened_by_event" in context:
        return {"reopened_by_event": context["reopened_by_event"], "op": context.get("op"),
                "row_id": context.get("row_id")}
    if case.kind == "recheck" and "event_id" in context:
        return {"reopened_by_event": context["event_id"], "op": context.get("op"), "row_id": context.get("row_id")}
    return context.get(SWEEP_NOTE)


def _desired_cases(db: Session, statement: AccountStatement, accounts: list[int], result: matching.MatchResult,
                   lines: dict[int, StatementLine], entries: dict[int, matching.Entry]) -> list[dict]:
    out = [_case(c.kind, event_id=lines[c.line_id].event_id, line_id=c.line_id, entry_id=c.entry_id,
                 candidates=c.candidates, context=c.context) for c in result.cases]
    for entry_id in result.unmatched_entry_ids:
        why = result.explained.get(entry_id)
        if why == DEFERRED:
            continue
        if why == UNCOVERED_CHILD:
            out.append(_case("entry_unmatched", entry_id=entry_id, context={"hint": UNCOVERED_CHILD}))
            continue
        entry = entries.get(entry_id)
        found = None if entry is None else _cross_account_line(db, statement, accounts, entry)
        context = {} if found is None else {"hint": "move_account", "statement_id": found[0], "line_id": found[1]}
        out.append(_case("entry_unmatched", entry_id=entry_id, context=context))
    if statement.kind == "bank":
        gap = (Decimal(statement.statement_total)
               - account_balance(db, statement.account_id, as_of=statement.period_end)).quantize(_Q)
        if gap != 0:
            out.append(_case("balance_gap", context={"gap": str(gap)}))
    return out


def _dismissed(dismissed: list[ReconciliationCase], want: dict) -> bool:
    """A dismissed case of the same kind on the same event (line cases), entry (entry cases) or gap (balance_gap)."""
    for case in dismissed:
        if case.kind != want["kind"]:
            continue
        if want["event_id"] is not None:
            if case.event_id == want["event_id"]:
                return True
        elif want["entry_id"] is not None:
            if case.entry_id == want["entry_id"]:
                return True
        elif (case.context or {}).get("gap") == want["context"].get("gap"):
            return True
    return False


def _write_cases(db: Session, statement: AccountStatement, desired: list[dict]) -> list[int]:
    existing = list(db.execute(
        select(ReconciliationCase)
        .where(ReconciliationCase.statement_id == statement.id, ReconciliationCase.kind.in_(MATCHER_KINDS),
               ReconciliationCase.status.in_(OPEN + ("dismissed",)))
        .order_by(ReconciliationCase.id)).scalars())
    dismissed = [c for c in existing if c.status == "dismissed"]
    open_by_key: dict[tuple, list[ReconciliationCase]] = defaultdict(list)
    for case in existing:
        if case.status in OPEN:
            open_by_key[_key(case.kind, case.event_id, case.entry_id, case.candidates, case.context)].append(case)
    kept: set[int] = set()
    fresh: list[dict] = []
    for want in desired:
        same = open_by_key.get(_key(want["kind"], want["event_id"], want["entry_id"], want["candidates"],
                                    want["context"]))
        if same:
            case = same.pop(0)
            kept.add(case.id)
            if case.line_id != want["line_id"]:  # an identical re-parse moved the event to a new line
                case.line_id, case.revision_id = want["line_id"], statement.current_revision_id
        elif not _dismissed(dismissed, want):
            fresh.append(want)
    leaving = [c for c in existing if c.status in OPEN and c.id not in kept]
    notes: dict[int, dict] = {}  # event id → the newest sweep note among its superseded cases
    for case in leaving:
        note = _sweep_note(case)
        if case.event_id is not None and note:
            notes[case.event_id] = note
    created: list[int] = []
    for want in fresh:
        note = notes.get(want["event_id"]) if want["event_id"] is not None else None
        if note:
            want = {**want, "context": {**want["context"], SWEEP_NOTE: note}}
        case = ReconciliationCase(statement_id=statement.id, revision_id=statement.current_revision_id, **want)
        db.add(case)
        db.flush()
        created.append(case.id)
    for case in leaving:
        case.status = "superseded"
        case.version = case.version + 1
    db.flush()
    return created


def _write_deferrals(db: Session, statement: AccountStatement, account: Account, deferred: list[int],
                     evaluated: set[int]) -> None:
    """Resolved `entry_unmatched` cases explaining `deferred_next_period` (resolved_by NULL: the matcher's own) with
    `deferred_to_period_end` from the account's cycle. `deferred_to_statement_id` is never set here (the later
    statement links it); an existing link is kept, and cleared only when the period end itself moved (a cycle
    change). Refreshed in place; one whose entry was evaluated this run and is no longer deferred is superseded; one
    whose entry left the population (e.g. covered by the next statement) keeps its explanation."""
    closing_day = (account.closing_day or statement.period_end.day) if statement.kind == "card" else None
    end = next_period_end(statement.period_end, closing_day)
    current = {c.entry_id: c for c in db.execute(
        select(ReconciliationCase)
        .where(ReconciliationCase.statement_id == statement.id, ReconciliationCase.kind == "entry_unmatched",
               ReconciliationCase.status == "resolved", ReconciliationCase.explanation == DEFERRED,
               ReconciliationCase.resolved_by.is_(None))
        .order_by(ReconciliationCase.id)).scalars()}
    for entry_id in deferred:
        case = current.get(entry_id)
        if case is None:
            db.add(ReconciliationCase(
                statement_id=statement.id, revision_id=statement.current_revision_id, kind="entry_unmatched",
                entry_id=entry_id, status="resolved", explanation=DEFERRED, deferred_to_period_end=end,
                resolved_at=func.now(), context={}))
        elif case.deferred_to_period_end != end:
            case.deferred_to_period_end, case.deferred_to_statement_id = end, None
    for entry_id, case in current.items():
        if entry_id in evaluated and entry_id not in deferred:
            case.status = "superseded"
            case.version = case.version + 1
    db.flush()


def _deferral_conditions() -> list:
    case = ReconciliationCase
    return [case.kind == "entry_unmatched", case.status == "resolved", case.explanation == DEFERRED,
            case.deferred_to_statement_id.is_(None)]


def _link_earlier_deferrals(db: Session, statement: AccountStatement) -> int:
    """Point earlier statements' unlinked deferrals to this period at this statement: one UPDATE of case rows only
    (no statement row of theirs is locked; the foreign key's KEY SHARE lands on this statement, which we hold).
    No SKIP LOCKED: an earlier statement's reconcile writes its deferral cases only after taking all its entry locks
    and then waits on nothing of ours, so waiting for its commit here cannot close a cycle."""
    earlier = select(AccountStatement.id).where(
        AccountStatement.account_id == statement.account_id, AccountStatement.currency == statement.currency,
        AccountStatement.id != statement.id)
    count = db.execute(
        update(ReconciliationCase)
        .where(ReconciliationCase.statement_id.in_(earlier),
               ReconciliationCase.deferred_to_period_end == statement.period_end, *_deferral_conditions())
        .values(deferred_to_statement_id=statement.id)
        .execution_options(synchronize_session=False)).rowcount
    db.flush()
    return count


def fill_deferral_links(db: Session) -> int:
    """Link every unlinked deferral case to the statement of the same account and currency whose period_end is its
    `deferred_to_period_end`; returns the number linked. Case rows only, no statement lock taken (the foreign key's
    KEY SHARE may wait for a running reconcile of the target: call it in its own transaction, e.g. after the batch)."""
    own, target = aliased(AccountStatement), aliased(AccountStatement)
    target_id = (select(target.id)
                 .join(own, and_(own.account_id == target.account_id, own.currency == target.currency))
                 .where(own.id == ReconciliationCase.statement_id,
                        target.period_end == ReconciliationCase.deferred_to_period_end)
                 .limit(1).scalar_subquery())
    count = db.execute(
        update(ReconciliationCase)
        .where(*_deferral_conditions(), ReconciliationCase.deferred_to_period_end.is_not(None), target_id.is_not(None))
        .values(deferred_to_statement_id=target_id)
        .execution_options(synchronize_session=False)).rowcount
    db.flush()
    return count


def _recount(db: Session, statement: AccountStatement) -> None:
    def count(*conds):
        return db.execute(select(func.count()).select_from(ReconciliationCase)
                          .where(ReconciliationCase.statement_id == statement.id, *conds)).scalar_one()

    statement.matched_count = db.execute(
        select(func.count(func.distinct(StatementCoverage.line_id)))
        .where(StatementCoverage.statement_id == statement.id, StatementCoverage.status == "active")).scalar_one()
    statement.explained_count = count(or_(
        and_(ReconciliationCase.status == "resolved", ReconciliationCase.explanation.is_not(None)),
        ReconciliationCase.status == "dismissed"))
    statement.open_case_count = count(ReconciliationCase.status.in_(OPEN))
    statement.needs_recheck = False
    db.flush()


def _skip_reason(db: Session, statement: AccountStatement) -> str | None:
    if statement.current_revision_id is None or not db.get(StatementRevision, statement.current_revision_id).guardrail_ok:
        return "no_current_revision"
    review = db.execute(select(ReconciliationCase.id).where(
        ReconciliationCase.statement_id == statement.id, ReconciliationCase.kind == "parse_review",
        ReconciliationCase.status.in_(OPEN)).limit(1)).scalar_one_or_none()
    return "parse_review" if review is not None else None


# --- entry points ------------------------------------------------------------------------------------------------

def reconcile(db: Session, statement_id: int, *, run_id: int | None = None, locked: bool = False) -> ReconcileResult:
    """Sweep, match and record one statement (module docstring for the lock order). `locked=True`: the caller
    already holds the statement row FOR UPDATE (the revision hook); the import key is then attempted AFTER that
    statement lock, which is safe only because the attempt never waits (pg_try_advisory_xact_lock_shared raises
    ImportRunningError at once while an import holds the key exclusively). Skips (after the sweep): no current revision or a failed
    current one → `no_current_revision`; an open/proposed parse_review → `parse_review`. Never commits."""
    take_import_key_shared(db)
    statement = db.get(AccountStatement, statement_id) if locked else _lock_statement(db, statement_id)
    if statement is None:
        raise NotFoundError(f"statement {statement_id} not found")
    db.flush()  # the sweep runs on a clean session
    coverage_service.sweep(db, statement, run_id=run_id)
    skipped = _skip_reason(db, statement)
    if skipped is not None:
        return ReconcileResult(skipped=skipped)
    live = statement.mode == "live"
    accounts = participating_accounts(db, statement.account_id)
    accounts_by_id = {a.id: a for a in db.execute(select(Account).where(Account.id.in_(accounts))).scalars()}
    rules = rules_for(settings_service.reconciliation_rules(db), statement.period_end)
    line_rows = _current_lines(db, statement)
    include_reward = any(line.line_kind == "reward" for line in line_rows)
    # population before the locks, to know what to lock; re-read under the locks
    entries, groups = load_candidates(db, statement, accounts, window_days=rules.candidate_window_days,
                                      include_reward=include_reward)
    for group_id in sorted(groups):
        lock_group(db, group_id)
    wanted = {e.id for e in entries} | {c.id for e in entries for c in e.children}
    transfers = {e.transfer_group_id for e in entries if e.transfer_group_id is not None}
    locked_rows = _lock_entries(db, wanted, {UUID(t) for t in transfers})
    first_groups = {e.id: e.group_id for e in entries}
    entries, groups = load_candidates(db, statement, accounts, window_days=rules.candidate_window_days,
                                      include_reward=include_reward)
    # rows that joined the population after the first read are not locked, and a row whose group changed in between
    # belongs to a group we did not lock: both are left to the next sweep/recheck
    entries = [_with_children(e, locked_rows) for e in entries
               if e.id in locked_rows and e.id in first_groups and first_groups[e.id] == e.group_id]
    result = matching.match(
        statement.kind, [_as_line(row) for row in line_rows], entries, groups, plan_map(db, statement.account_id),
        lambda e: proposed_fx_fee(accounts_by_id[e.account_id], e.flow), rules, statement.currency)
    lines = {row.id: row for row in line_rows}
    _write_coverage(db, statement, result, lines, locked_rows, run_id)
    deferred = [entry_id for entry_id in result.unmatched_entry_ids if result.explained.get(entry_id) == DEFERRED]
    created: list[int] = []
    if live:
        by_id = {e.id: e for e in entries}
        created = _write_cases(db, statement, _desired_cases(db, statement, accounts, result, lines, by_id))
        evaluated = set(by_id) | {c.id for e in entries for c in e.children}
        _write_deferrals(db, statement, accounts_by_id[statement.account_id], deferred, evaluated)
    _link_earlier_deferrals(db, statement)
    coverage_service.assert_conserved(db, statement)
    _recount(db, statement)
    return ReconcileResult(claims=len(result.claims), cases_opened=created, explained=len(deferred),
                           unmatched_entries=len(result.unmatched_entry_ids) - len(deferred))


def _with_children(entry: matching.Entry, locked: dict[int, LedgerEntry]) -> matching.Entry:
    children = tuple(c for c in entry.children if c.id in locked)
    return entry if children == entry.children else replace(entry, children=children)


def pending_statement_ids(db: Session) -> list[int]:
    """Statements (any mode) with a dirty event past their watermark that may touch them (`sweep_pending`) or with
    `needs_recheck`, deduplicated, ascending."""
    flagged = db.execute(select(AccountStatement.id).where(AccountStatement.needs_recheck.is_(True))).scalars()
    return sorted(set(coverage_service.sweep_pending(db)) | set(flagged))


def reconcile_each(db: Session, statement_ids: Iterable[int], *, run_id: int | None = None) -> Iterator[dict]:
    """`reconcile` each statement inside its own savepoint and yield `{"statement_id", "result"}` or
    `{"statement_id", "error": <exception class>}` (the class only: driver messages can carry amounts). Never commits.
    The caller MUST commit after each yielded item: the next statement's sweep must not run while this transaction
    still holds the previous statement's entry/group row locks (module docstring)."""
    for statement_id in statement_ids:
        try:
            with db.begin_nested():
                result = reconcile(db, statement_id, run_id=run_id)
        except Exception as exc:  # noqa: BLE001  (reported per statement; the batch goes on)
            yield {"statement_id": statement_id, "error": exc.__class__.__name__}
        else:
            yield {"statement_id": statement_id, "result": result}
