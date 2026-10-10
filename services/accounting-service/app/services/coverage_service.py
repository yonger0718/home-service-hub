"""Statement coverage (spec §4.6): claims, release, conservation and the dirty-event sweep. Never commits;
callers own the transaction.

Sweep barrier (§4.6 erratum): coverage_dirty ids come from a bigserial, assigned at insert, not at commit, so a
writer can hold an uncommitted id N while a later id N+1 commits; a watermark advanced past N+1 would skip N for
good. Every dirty trigger therefore takes `pg_advisory_xact_lock_shared(DIRTY_BARRIER_KEY)` before it inserts and
keeps it until its transaction ends. The sweep takes the same key EXCLUSIVELY at session level, reads
`cap = max(id)` and unlocks at once: once it holds the exclusive lock no writer is between its insert and its
commit, so every id <= cap is committed (or rolled back) and visible, and any later writer gets an id > cap. The
lock is session level and released in a `finally` so it is never held for the rest of the reconcile transaction
(an xact-level lock would block every ledger writer until the reconcile commits). The wait for it is bounded by
BARRIER_LOCK_TIMEOUT: a long writer transaction makes the sweep raise `sweep_barrier_busy` instead of stalling."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from psycopg2.errors import LockNotAvailable, QueryCanceled

from sqlalchemy import Integer, and_, exists, func, or_, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, aliased

from app.models import (
    DIRTY_BARRIER_KEY, Account, AccountStatement, CoverageDirty, LedgerEntry, ReconciliationAction, ReconciliationCase, StatementCoverage,
    StatementLine,
)
from app.services.errors import CodedConflictError, ConflictError
from app.services.statements import matching

_Q = Decimal("0.0001")


def _s(value) -> str | None:
    return None if value is None else str(value)


def _m(value) -> str | None:
    """Money in snapshots: always 4 dp, whether the entry was refreshed from the database or not."""
    return None if value is None else str(Decimal(value).quantize(_Q))


def snapshot_entry(entry: LedgerEntry) -> dict:
    return {
        "id": entry.id, "amount": _m(entry.amount), "flow": _m(entry.amount), "currency": entry.currency,
        "original_amount": _m(entry.original_amount), "original_currency": entry.original_currency,
        "entry_date": _s(entry.entry_date), "posted_date": _s(entry.posted_date),
        "account_id": entry.account_id, "kind": _s(entry.kind), "group_id": entry.group_id,
        "parent_entry_id": entry.parent_entry_id, "transfer_group_id": _s(entry.transfer_group_id),
        "refunds_entry_id": entry.refunds_entry_id, "settles_entry_id": entry.settles_entry_id,
        "is_settlement": entry.is_settlement, "name": entry.name, "merchant": entry.merchant,
    }


def write_claim(db: Session, statement: AccountStatement, line: StatementLine, rows: list[matching.Row], *,
                entries_by_id: dict[int, LedgerEntry], match_kind: str, match_rule: str,
                run_id: int | None) -> list[StatementCoverage]:
    if not matching.conserved(tuple(rows), line.flow_amount):
        raise ConflictError("coverage not conserved")
    out: list[StatementCoverage] = []
    for row in rows:
        snap = snapshot_entry(entries_by_id[row.entry_id])
        if Decimal(row.flow).quantize(_Q) != Decimal(snap["amount"]).quantize(_Q):
            raise ConflictError("coverage snapshot drift")
        out.append(StatementCoverage(
            statement_id=statement.id, event_id=line.event_id, line_id=line.id, entry_id=row.entry_id,
            group_id=row.group_id, role=row.role, snapshot=snap, match_kind=match_kind,
            match_rule=match_rule, status="active", run_id=run_id))
    try:
        with db.begin_nested():
            db.add_all(out)
            db.flush()
    except IntegrityError as exc:
        if "ux_statement_coverage_active_entry" not in str(exc.orig):
            raise
        claimed = claimed_entry_ids(db)
        dup = next((r.entry_id for r in rows if r.entry_id in claimed), rows[0].entry_id)
        raise CodedConflictError("duplicate_claim", f"entry {dup}") from exc
    return out


def _release(db: Session, conds: list, reason: str) -> int:
    rows = db.execute(select(StatementCoverage).where(StatementCoverage.status == "active", *conds)).scalars().all()
    for r in rows:
        r.status = "stale"
        r.stale_reason = reason[:64]
    db.flush()
    return len(rows)


def release_line(db: Session, statement: AccountStatement, line_id: int, *, reason: str) -> int:
    return _release(db, [StatementCoverage.statement_id == statement.id, StatementCoverage.line_id == line_id], reason)


def release_statement(db: Session, statement: AccountStatement, *, reason: str) -> int:
    return _release(db, [StatementCoverage.statement_id == statement.id], reason)


def active_rows(db: Session, statement_id: int) -> dict[int, list[StatementCoverage]]:
    out: dict[int, list[StatementCoverage]] = defaultdict(list)
    q = select(StatementCoverage).where(StatementCoverage.statement_id == statement_id, StatementCoverage.status == "active")
    for r in db.execute(q.order_by(StatementCoverage.id)).scalars():
        out[r.line_id].append(r)
    return dict(out)


def claimed_entry_ids(db: Session, *, exclude_statement_id: int | None = None) -> set[int]:
    q = select(StatementCoverage.entry_id).where(StatementCoverage.status == "active", StatementCoverage.entry_id.is_not(None))
    if exclude_statement_id is not None:
        q = q.where(StatementCoverage.statement_id != exclude_statement_id)
    return set(db.execute(q).scalars())


def assert_conserved(db: Session, statement: AccountStatement) -> None:
    """Every line with active coverage: Σ snapshot flow == line flow (one query; a missing flow is not conserved)."""
    rows = db.execute(
        select(StatementCoverage.line_id, StatementLine.flow_amount, StatementCoverage.snapshot["flow"].astext)
        .join(StatementLine, StatementLine.id == StatementCoverage.line_id)
        .where(StatementCoverage.statement_id == statement.id, StatementCoverage.status == "active")
        .order_by(StatementCoverage.line_id)
    ).all()
    totals: dict[int, Decimal | None] = {}
    targets: dict[int, Decimal | None] = {}
    for line_id, line_flow, flow in rows:
        targets[line_id] = line_flow
        total = totals.get(line_id, Decimal(0))
        totals[line_id] = None if total is None or flow is None else total + Decimal(flow)
    for line_id, total in totals.items():
        target = targets[line_id]
        if total is None or target is None or total.quantize(_Q) != target.quantize(_Q):
            raise ConflictError(f"coverage not conserved for line {line_id}")


# --- dirty sweep (§4.6 "Sweep protocol") -------------------------------------------------------------------------

SWEEP_BATCH = 1000
BARRIER_LOCK_TIMEOUT = "5s"  # the longest the sweep waits for in-flight ledger writers at the barrier
SWEEP_BARRIER_BUSY = "sweep_barrier_busy"
OPEN_CASE_STATUSES = ("open", "proposed")


@dataclass
class SweepResult:
    stale_lines: list[int] = field(default_factory=list)
    reopened_cases: list[int] = field(default_factory=list)
    created_cases: list[int] = field(default_factory=list)
    recheck: bool = False
    swept_through: int = 0


def participating_accounts(db: Session, account_id: int) -> list[int]:
    """The statement's account plus every account combined into it (`combined_account_id == account_id`)."""
    children = db.execute(select(Account.id).where(Account.combined_account_id == account_id).order_by(Account.id))
    return [account_id, *children.scalars()]


def _applied_action_ids(db: Session, statement: AccountStatement) -> set[int]:
    """Actions applied on this statement (their events are self-generated and must not reopen fresh coverage)."""
    return set(db.execute(
        select(ReconciliationAction.id)
        .join(ReconciliationCase, ReconciliationCase.id == ReconciliationAction.case_id)
        .where(ReconciliationCase.statement_id == statement.id, ReconciliationAction.status == "applied")
    ).scalars())


def _recount(db: Session, statement: AccountStatement) -> None:
    statement.open_case_count = db.execute(
        select(func.count()).select_from(ReconciliationCase)
        .where(ReconciliationCase.statement_id == statement.id, ReconciliationCase.status.in_(OPEN_CASE_STATUSES))
    ).scalar_one()
    statement.matched_count = db.execute(
        select(func.count(func.distinct(StatementCoverage.line_id)))
        .where(StatementCoverage.statement_id == statement.id, StatementCoverage.status == "active")
    ).scalar_one()


def _case_for_released_line(db: Session, statement: AccountStatement, line_id: int, event, run_id: int | None,
                            result: SweepResult) -> None:
    """Live mode, by the event's newest case: open/proposed → bump `version` (proposals go stale) and note the event,
    no second case; resolved → reopen with its resolution moved into `context.previous_resolution`; none, dismissed
    or superseded → a new `recheck` case. No row lock: the caller's statement lock serialises case writers."""
    line = db.get(StatementLine, line_id)
    newest = db.execute(
        select(ReconciliationCase)
        .where(ReconciliationCase.statement_id == statement.id, ReconciliationCase.event_id == line.event_id)
        .order_by(ReconciliationCase.id.desc()).limit(1)
    ).scalar_one_or_none()
    note = {"reopened_by_event": event.id, "op": event.op, "row_id": event.row_id}
    if newest is not None and newest.status in ("open", "proposed", "resolved"):
        context = {**(newest.context or {}), **note}
        if newest.status == "resolved":
            context["previous_resolution"] = {
                "resolved_at": newest.resolved_at.isoformat() if newest.resolved_at else None,
                "resolved_by": newest.resolved_by, "resolved_action_id": newest.resolved_action_id,
                "explanation": newest.explanation,
            }
            newest.status = "open"
            newest.resolved_at = newest.resolved_by = newest.resolved_action_id = newest.explanation = None
        newest.context = context
        newest.version = newest.version + 1
        db.flush()
        result.reopened_cases.append(newest.id)
        return
    context = {"event_id": event.id, "op": event.op, "row_id": event.row_id}
    if run_id is not None:
        context["run_id"] = run_id
    item = ReconciliationCase(statement_id=statement.id, revision_id=line.revision_id, kind="recheck",
                              event_id=line.event_id, line_id=line.id, context=context)
    db.add(item)
    db.flush()
    result.created_cases.append(item.id)


def _unlock_barrier(db: Session) -> None:
    """Release the session-level barrier lock. A failed or false unlock would leave it on a pooled connection (blocking
    every later writer), so the connection is invalidated: the pool discards it and the server drops the lock."""
    try:
        released = db.execute(select(func.pg_advisory_unlock(DIRTY_BARRIER_KEY))).scalar_one()
    except Exception:
        db.connection().invalidate()
        raise
    if not released:
        db.connection().invalidate()
        raise RuntimeError("the dirty barrier was not held at unlock; connection invalidated")


def _restore_lock_timeout(db: Session, previous: str) -> None:
    db.execute(select(func.set_config("lock_timeout", previous, True)))


def _drop_barrier_after_failure(db: Session) -> None:
    """The barrier is held but its owner failed before handing it on: unlock it, or (when the transaction can no
    longer run the unlock) invalidate the connection so the server drops the session lock with the backend."""
    try:
        _unlock_barrier(db)  # invalidates the connection itself when the unlock fails or returns false
    except Exception:  # noqa: BLE001  (the original error is re-raised by the caller)
        try:
            db.connection().invalidate()
        except Exception:  # noqa: BLE001
            pass


def _take_barrier(db: Session, *, wait: bool = True) -> None:
    """The exclusive session-level barrier. `wait=True`: wait at most BARRIER_LOCK_TIMEOUT (a transaction-local
    lock_timeout set in a savepoint, so a timeout leaves the session usable, and restored once the lock is held).
    `wait=False` (the revision hook, whose transaction already holds coverage rows a writer may wait on): one
    `pg_try_advisory_lock`, never a wait. Either way a busy barrier raises `CodedConflictError("sweep_barrier_busy")`.
    Once acquired, any failure before returning releases the lock (or invalidates the connection) before re-raising,
    so the lock never stays on a pooled connection."""
    if not wait:
        if not db.execute(select(func.pg_try_advisory_lock(DIRTY_BARRIER_KEY))).scalar_one():
            raise CodedConflictError(SWEEP_BARRIER_BUSY, "a ledger write holds the dirty barrier")
        return
    previous = db.execute(select(func.current_setting("lock_timeout"))).scalar_one()
    acquired = False
    try:
        try:
            with db.begin_nested():  # rolled back on a timeout, which also reverts the SET LOCAL
                db.execute(select(func.set_config("lock_timeout", BARRIER_LOCK_TIMEOUT, True)))
                db.execute(select(func.pg_advisory_lock(DIRTY_BARRIER_KEY)))
                acquired = True
        except OperationalError as exc:
            if not acquired and isinstance(exc.orig, (LockNotAvailable, QueryCanceled)):
                raise CodedConflictError(SWEEP_BARRIER_BUSY, "a ledger write held the dirty barrier too long") from exc
            raise
        _restore_lock_timeout(db, previous)
    except BaseException:
        if acquired:
            _drop_barrier_after_failure(db)
        raise


def _committed_cap(db: Session, *, barrier_wait: bool = True) -> int | None:
    """max(coverage_dirty.id) read under the exclusive barrier (module docstring); None when there are no events."""
    _take_barrier(db, wait=barrier_wait)
    try:
        with db.begin_nested():  # a failing read must not leave the transaction unable to unlock
            return db.execute(select(func.max(CoverageDirty.id))).scalar_one()
    finally:
        _unlock_barrier(db)


def sweep(db: Session, statement: AccountStatement, *, run_id: int | None = None,
          barrier_wait: bool = True) -> SweepResult:
    """Apply every coverage_dirty event past the statement's watermark that touches it, then advance the watermark
    to the highest id scanned (relevant or not). Precondition: the caller holds the statement row FOR UPDATE (§7.4:
    writers never lock statements, so a writer committing after this sweep gets a higher id the next sweep sees).
    Effects: a changed/deleted covered entry or group releases its line(s) as a whole (live: reopen/create a case;
    historical: release only); population changes (insert/move/edit/delete of an uncovered in-period row, account
    config, a combined child relinked to or away from the account) set `needs_recheck` in live mode. Events of
    actions applied on this statement are skipped. Only ids <= the committed cap read under the writer barrier are
    scanned (module docstring). That argument needs READ COMMITTED isolation and `coverage_dirty_id_seq` with CACHE 1
    (a cached block would let a later-allocated lower id commit after the cap). `barrier_wait=False` try-locks the
    barrier instead of waiting (`_take_barrier`). Never deletes events and never commits."""
    live = statement.mode == "live"
    cap = _committed_cap(db, barrier_wait=barrier_wait)
    accounts = set(participating_accounts(db, statement.account_id))
    active = active_rows(db, statement.id)
    lines_by_entry: dict[int, set[int]] = defaultdict(set)
    lines_by_group: dict[int, set[int]] = defaultdict(set)
    for line_id, rows in active.items():
        for r in rows:
            # the snapshot keeps the ids after a delete SET NULLs the columns
            for entry_id in {r.entry_id, (r.snapshot or {}).get("id")} - {None}:
                lines_by_entry[entry_id].add(line_id)
            for group_id in {r.group_id, (r.snapshot or {}).get("group_id")} - {None}:
                lines_by_group[group_id].add(line_id)
    skipped_actions = _applied_action_ids(db, statement)
    start, end = statement.period_start, statement.period_end
    result = SweepResult(swept_through=statement.swept_through_event_id)
    released: set[int] = set()

    def in_population(account_id, day) -> bool:
        return account_id in accounts and day is not None and start <= day <= end

    def release(line_ids, event, reason: str) -> None:
        for line_id in sorted(line_ids - released):
            released.add(line_id)
            if release_line(db, statement, line_id, reason=reason) == 0:
                continue
            result.stale_lines.append(line_id)
            if live:
                _case_for_released_line(db, statement, line_id, event, run_id, result)

    last = statement.swept_through_event_id
    while True:
        batch = db.execute(
            select(CoverageDirty.id, CoverageDirty.kind, CoverageDirty.row_id, CoverageDirty.op,
                   CoverageDirty.old_account_id, CoverageDirty.new_account_id, CoverageDirty.old_date,
                   CoverageDirty.new_date, CoverageDirty.action_id,
                   CoverageDirty.old_row["combined_account_id"].astext.cast(Integer).label("old_parent"),
                   CoverageDirty.new_row["combined_account_id"].astext.cast(Integer).label("new_parent"))
            .where(CoverageDirty.id > last, CoverageDirty.id <= cap).order_by(CoverageDirty.id).limit(SWEEP_BATCH)
        ).all() if cap is not None else []
        if not batch:
            break
        last = batch[-1].id
        for ev in batch:
            if ev.action_id is not None and ev.action_id in skipped_actions:
                continue
            if ev.kind == "entry":
                if ev.row_id in lines_by_entry:
                    if ev.op in ("update", "delete"):
                        release(lines_by_entry[ev.row_id], ev, f"dirty:{ev.op}:{ev.id}")
                elif in_population(ev.new_account_id, ev.new_date) or in_population(ev.old_account_id, ev.old_date):
                    result.recheck = result.recheck or live
            elif ev.kind == "group":
                if ev.row_id in lines_by_group and ev.op in ("update", "delete"):
                    release(lines_by_group[ev.row_id], ev, f"dirty:group:{ev.id}")
            elif ev.kind == "account" and (
                    ev.row_id in accounts or statement.account_id in (ev.old_parent, ev.new_parent)):  # relinks
                result.recheck = result.recheck or live
        if len(batch) < SWEEP_BATCH:
            break
    if last > statement.swept_through_event_id:
        statement.swept_through_event_id = last
    if result.recheck:
        statement.needs_recheck = True
    result.swept_through = statement.swept_through_event_id
    _recount(db, statement)
    db.flush()
    return result


def _touches(s):
    """EXISTS: an event past the statement `s`'s watermark that may touch it: the account or a combined child on
    either side, an account event whose old or new `combined_account_id` is the account, or a group/entry its active
    coverage holds (by column or, after a SET NULL delete, by snapshot id). A superset; `sweep` decides relevance.
    `s` is the AccountStatement entity (or alias) the clause is correlated to."""
    child = aliased(Account)
    participating = select(child.id).where(child.combined_account_id == s.account_id)
    covered = select(StatementCoverage).where(StatementCoverage.statement_id == s.id,
                                              StatementCoverage.status == "active")
    return exists().where(
        CoverageDirty.id > s.swept_through_event_id,
        or_(
            CoverageDirty.old_account_id == s.account_id, CoverageDirty.new_account_id == s.account_id,
            and_(CoverageDirty.kind == "account",
                 or_(CoverageDirty.old_row["combined_account_id"].astext.cast(Integer) == s.account_id,
                     CoverageDirty.new_row["combined_account_id"].astext.cast(Integer) == s.account_id)),
            CoverageDirty.old_account_id.in_(participating), CoverageDirty.new_account_id.in_(participating),
            and_(CoverageDirty.kind == "group", exists(covered.where(or_(
                StatementCoverage.group_id == CoverageDirty.row_id,
                StatementCoverage.snapshot["group_id"].astext.cast(Integer) == CoverageDirty.row_id)))),
            and_(CoverageDirty.kind == "entry", exists(covered.where(or_(
                StatementCoverage.entry_id == CoverageDirty.row_id,
                StatementCoverage.snapshot["id"].astext.cast(Integer) == CoverageDirty.row_id)))),
        ),
    )


def sweep_pending(db: Session) -> list[int]:
    """Statements (any mode) with an event past their watermark that may touch them (`_touches`). Read without the
    writer barrier: an uncommitted event only shows up on a later call."""
    s = AccountStatement
    return list(db.execute(select(s.id).where(_touches(s)).order_by(s.id)).scalars())


def has_pending_events(db: Session, statement: AccountStatement) -> bool:
    """The same relevance as `sweep_pending`, for one statement (the read model's `stale_events_pending`)."""
    s = AccountStatement
    return db.execute(select(exists().where(s.id == statement.id, _touches(s)))).scalar_one()
