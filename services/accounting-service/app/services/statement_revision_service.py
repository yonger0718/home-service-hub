"""Statement revisions, events and lineage (design §4.3–4.5, §5.6–5.8).

`submit_revision` derives flow amounts and guardrails server-side, upserts the statement identity
(account, currency, period_end), stores an immutable revision, pairs its lines to the statement's events and
opens cases in live mode. Event state (current line, retirement) follows the CURRENT revision only: a revision that
does not become current (guardrail failure, twin/header change, conflict) records its lineage but moves nothing
(§5.7 "no transfers"). When it does become current, `identical`/`normalised` pairs carry the event's active coverage
to the new line and `changed`/`unpaired` events holding coverage or applied effects are quarantined (§5.7).

Storing and activating are separate steps: `_insert_lines` persists a revision as not current (lines, its own events
retired, lineage rows untransferred) and `_activate` makes a persisted revision current from its stored rows.
`submit_revision` runs both when the revision becomes current; `promote_revision` (a `statement_conflict` resolved
with `keep=new`, ruling 28) runs `_activate` alone on a revision stored earlier. Services never commit; routers do.
"""

from collections import Counter
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from psycopg2.errors import DeadlockDetected
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import (
    Account,
    AccountStatement,
    IngestRun,
    LineLineage,
    ReconciliationAction,
    ReconciliationCase,
    ReconciliationProposal,
    StatementCoverage,
    StatementEvent,
    StatementFile,
    StatementLine,
    StatementRevision,
    StatementSource,
)
from app.schemas.statements import RevisionIn
from app.services import coverage_service
from app.services.reconciliation_actions import audit
from app.services.errors import CodedConflictError, ConflictError, NotFoundError, ValidationError
from app.services.statements import derive, lineage

HEADER_COLUMNS = ("period_start", "period_end", "closing_date", "due_date", "opening_balance", "statement_total",
                  "minimum_payment")
# Header fields compared between revisions; period_end and currency are the identity.
COMPARED_HEADER = ("period_start", "closing_date", "due_date", "opening_balance", "statement_total", "minimum_payment")
OPEN_CASE_STATUSES = ("open", "proposed")
TEXT_CHANGED = "text_changed"  # statement_event.flag after a normalised re-parse (UI: 文字已變更)


def reconciliation_hook(db: Session, statement: AccountStatement, revision: StatementRevision,
                        run: IngestRun) -> list[int]:
    """Called by `submit_revision` after lines, lineage and the current-revision switch are written, under its
    statement lock: when the revision became current and passed its guardrails, reconcile the statement
    (`reconciliation_service.reconcile(locked=True, barrier_wait=False)`) inside a savepoint; returns the ids of the
    cases it opened. The pass never waits for the dirty barrier: this transaction already holds the coverage/case rows
    the lineage transfer moved, and a writer holding the shared barrier may be waiting on them. Any ConflictError of
    the pass (an import holding the import key, `reconcile_busy`, `sweep_barrier_busy`, `duplicate_claim`, coverage
    not conserved) and a residual `DeadlockDetected` roll back only the savepoint and leave the pass to the daily
    batch (`needs_recheck`): the revision itself is stored and the ingest commits. Any other OperationalError
    propagates. Imported late: reconciliation_service depends on the ledger services, the revision service must not
    at import time."""
    if statement.current_revision_id != revision.id or not revision.guardrail_ok:
        return []
    from app.services import reconciliation_service

    db.flush()  # the revision, lines and current-revision switch stay outside the savepoint
    try:
        with db.begin_nested():
            result = reconciliation_service.reconcile(db, statement.id, run_id=run.id, locked=True,
                                                      barrier_wait=False)
    except (ConflictError, OperationalError) as exc:
        if isinstance(exc, OperationalError) and not isinstance(exc.orig, DeadlockDetected):
            raise
        statement.needs_recheck = True
        db.flush()
        return []
    return list(result.cases_opened)


@dataclass
class SubmitResult:
    statement: AccountStatement
    revision: StatementRevision
    lineage_counts: dict[str, int]
    case_ids: list[int]


def _q(value: Decimal | None) -> Decimal | None:
    return None if value is None else Decimal(value).quantize(derive.QUANTUM, rounding=ROUND_HALF_UP)


def _header_columns(payload: RevisionIn) -> dict:
    return {column: getattr(payload, column) for column in HEADER_COLUMNS}


def _jsonable(value):
    return str(value) if isinstance(value, Decimal) else value.isoformat() if hasattr(value, "isoformat") else value


def _header_changes(statement: AccountStatement, payload: RevisionIn) -> dict[str, list]:
    changes = {}
    for column in COMPARED_HEADER:
        stored, sent = getattr(statement, column), getattr(payload, column)
        if stored != sent:  # Decimal equality is numeric, so 580.0000 == 580
            changes[column] = [_jsonable(stored), _jsonable(sent)]
    return changes


def _assert_folder_maps(db: Session, file_id: int, account_id: int, account_map: dict[str, int]) -> None:
    """The file's newest source (non-removed preferred) folder `root/<first two folders>` must map to the account."""
    if db.get(StatementFile, file_id) is None:
        raise NotFoundError(f"statement file {file_id} not found")
    source = db.execute(
        select(StatementSource).where(StatementSource.file_id == file_id)
        .order_by(StatementSource.removed_at.is_not(None), StatementSource.id.desc()).limit(1)
    ).scalar_one_or_none()
    if source is None:
        raise ValidationError("file_id", "file has no source")
    folders = [part for part in source.drive_path.strip("/").split("/") if part][:-1]
    folder = "/".join([source.root, *folders[:2]])
    if account_map.get(folder) != account_id:
        raise ValidationError("account_id", "folder maps elsewhere")


def _lock_statement(db: Session, run: IngestRun, payload: RevisionIn, mode: str,
                    origin: str) -> tuple[AccountStatement, bool]:
    """Fetch the statement identity FOR UPDATE, creating it first when absent (concurrent creators do not raise);
    `origin` and `mode` apply only to a statement created here."""
    db.flush()  # populate_existing below must not discard pending changes
    created_id = db.execute(
        pg_insert(AccountStatement)
        .values(account_id=payload.account_id, kind=payload.kind, currency=payload.currency, mode=mode,
                origin=origin, created_run_id=run.id,
                **_header_columns(payload))
        .on_conflict_do_nothing(constraint="uq_account_statement_identity")
        .returning(AccountStatement.id)
    ).scalar_one_or_none()
    statement = db.execute(
        select(AccountStatement)
        .where(AccountStatement.account_id == payload.account_id, AccountStatement.currency == payload.currency,
               AccountStatement.period_end == payload.period_end)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    return statement, created_id is not None


def _old_lines(db: Session, statement: AccountStatement) -> list[lineage.Old]:
    if statement.current_revision_id is None:
        return []
    rows = db.execute(
        select(StatementLine).where(StatementLine.revision_id == statement.current_revision_id)
        .order_by(StatementLine.seq)
    ).scalars()
    return [lineage.Old(line_id=row.id, event_id=row.event_id, logical_key=row.logical_key,
                        canonical_key=row.canonical_key, merchant_norm=row.merchant_norm,
                        kind_fields=(row.posted_date, row.txn_date, row.flow_amount, row.foreign_amount,
                                     row.foreign_currency, row.line_kind, row.installment_seq, row.installment_total))
            for row in rows]


def _new_lines(derived: list[derive.DerivedLine]) -> list[lineage.New]:
    return [lineage.New(index=i, logical_key=d.logical_key, canonical_key=d.canonical_key, merchant_norm=d.merchant_norm,
                        kind_fields=(d.line.posted_date, d.line.txn_date, d.flow_amount, _q(d.line.foreign_amount),
                                     d.line.foreign_currency, d.line.line_kind, d.line.installment_seq,
                                     d.line.installment_total))
            for i, d in enumerate(derived)]


def _events_with_applied_effects(db: Session, event_ids: list[int]) -> set[int]:
    if not event_ids:
        return set()
    return set(db.execute(
        select(ReconciliationAction.event_id)
        .where(ReconciliationAction.event_id.in_(event_ids), ReconciliationAction.status == "applied")
    ).scalars())


def _supersede_pending_proposals(db: Session, event_id: int) -> None:
    rows = db.execute(update(ReconciliationProposal)
                      .where(ReconciliationProposal.event_id == event_id, ReconciliationProposal.status == "pending")
                      .values(status="superseded", decided_at=func.now())
                      .returning(ReconciliationProposal.id, ReconciliationProposal.case_id)
                      .execution_options(synchronize_session="fetch")).all()
    for proposal_id, case_id in sorted(rows):
        statement_id = db.execute(select(ReconciliationCase.statement_id)
                                  .where(ReconciliationCase.id == case_id)).scalar_one()
        audit.record(db, event="proposal_superseded", actor="system", statement_id=statement_id, case_id=case_id,
                     detail={"proposal_id": proposal_id, "by": "lineage", "event_id": event_id})
    if rows:
        from app.services import proposal_service  # local: proposal_service imports the action engine
        proposal_service.reopen_idle(db, [case_id for _, case_id in rows])


def _open_quarantine_case(db: Session, statement: AccountStatement, revision: StatementRevision,
                          event: StatementEvent, old_line_id: int, *, context: dict) -> int:
    """One open parse_review per quarantined event: an open/proposed one is reused (context merged, version bumped
    so its proposals go stale), otherwise a new case keyed by the event and its old line."""
    existing = db.execute(
        select(ReconciliationCase)
        .where(ReconciliationCase.statement_id == statement.id, ReconciliationCase.kind == "parse_review",
               ReconciliationCase.event_id == event.id, ReconciliationCase.status.in_(OPEN_CASE_STATUSES))
        .order_by(ReconciliationCase.id.desc()).limit(1).with_for_update()
    ).scalar_one_or_none()
    if existing is not None:
        existing.context = {**(existing.context or {}), **context}
        existing.version = existing.version + 1
        db.flush()
        return existing.id
    item = ReconciliationCase(statement_id=statement.id, revision_id=revision.id, kind="parse_review",
                              event_id=event.id, line_id=old_line_id, context=context)
    db.add(item)
    db.flush()
    return item.id


def _stored_new_lines(rows: list[StatementLine]) -> list[lineage.New]:
    """A stored revision's lines (in seq order, the order `derive.derive_lines` produced them in) as pairing input."""
    return [lineage.New(index=i, logical_key=row.logical_key, canonical_key=row.canonical_key,
                        merchant_norm=row.merchant_norm,
                        kind_fields=(row.posted_date, row.txn_date, row.flow_amount, row.foreign_amount,
                                     row.foreign_currency, row.line_kind, row.installment_seq, row.installment_total))
            for i, row in enumerate(rows)]


def _revision_lines(db: Session, revision: StatementRevision) -> list[StatementLine]:
    return list(db.execute(select(StatementLine).where(StatementLine.revision_id == revision.id)
                           .order_by(StatementLine.seq)).scalars())


def _moves_event(pairing: lineage.Pairing) -> bool:
    """`identical`/`normalised`: the old event moves to the new line; any other new line gets an event of its own."""
    return pairing.old is not None and pairing.new is not None and pairing.equivalence != "changed"


def _insert_lines(db: Session, statement: AccountStatement, revision: StatementRevision,
                  derived: list[derive.DerivedLine], pairings: list[lineage.Pairing]) -> dict[str, int]:
    """Persist a revision as NOT current: events for unpaired and `changed` new lines first (statement_line.event_id
    is NOT NULL; `retired`, no current line, `first_line_id` set), then the lines (an `identical`/`normalised` line
    rides its old event), then one `line_lineage` row per pairing with `transferred=False` — none for a first revision
    (nothing to pair with). Moves nothing: `_activate` makes the revision current. Returns the lineage counts."""
    record_lineage = statement.current_revision_id is not None
    new_events: dict[int, StatementEvent] = {}
    for pairing in pairings:
        if pairing.new is not None and not _moves_event(pairing):
            event = StatementEvent(statement_id=statement.id, first_revision_id=revision.id, status="retired")
            db.add(event)
            new_events[pairing.new.index] = event
    db.flush()
    lines: dict[int, StatementLine] = {}
    for pairing in pairings:
        if pairing.new is None:
            continue
        d = derived[pairing.new.index]
        event = new_events.get(pairing.new.index)
        event_id = event.id if event is not None else pairing.old.event_id
        line = StatementLine(
            revision_id=revision.id, event_id=event_id, seq=d.line.seq, canonical_key=d.canonical_key,
            logical_key=d.logical_key, txn_date=d.line.txn_date, posted_date=d.line.posted_date,
            merchant_raw=d.line.merchant_raw, merchant_norm=d.merchant_norm, printed_amount=_q(d.line.printed_amount),
            flow_amount=d.flow_amount, foreign_amount=_q(d.line.foreign_amount),
            foreign_currency=d.line.foreign_currency, line_kind=d.line.line_kind,
            installment_seq=d.line.installment_seq, installment_total=d.line.installment_total)
        db.add(line)
        lines[pairing.new.index] = line
    db.flush()
    counts = Counter({"identical": 0, "normalised": 0, "changed": 0, "unpaired_old": 0, "new": 0})
    for pairing in pairings:
        new_line = lines.get(pairing.new.index) if pairing.new is not None else None
        new_event = new_events.get(pairing.new.index) if pairing.new is not None else None
        if new_event is not None:
            new_event.first_line_id = new_line.id
        if pairing.old is None:
            counts["new"] += 1
        elif pairing.new is None:
            counts["unpaired_old"] += 1
        else:
            counts[pairing.equivalence] += 1
        if record_lineage:
            db.add(LineLineage(
                event_id=pairing.old.event_id if pairing.old is not None else new_event.id,
                old_line_id=pairing.old.line_id if pairing.old is not None else None,
                new_line_id=new_line.id if new_line is not None else None,
                equivalence=pairing.equivalence, transferred=False))
    db.flush()
    return dict(counts)


def _lineage_rows(db: Session, new_line_ids: list[int], unpaired_old_ids: list[int]) -> dict[tuple, LineLineage]:
    """The recorded lineage rows of a revision's pairings, by (old_line_id, new_line_id): each of its lines' rows,
    and the `new_line_id IS NULL` rows of its unpaired old lines (the newest when several revisions recorded one)."""
    conds = []
    if new_line_ids:
        conds.append(LineLineage.new_line_id.in_(new_line_ids))
    if unpaired_old_ids:
        conds.append(and_(LineLineage.old_line_id.in_(unpaired_old_ids), LineLineage.new_line_id.is_(None)))
    if not conds:
        return {}
    return {(row.old_line_id, row.new_line_id): row for row in db.execute(
        select(LineLineage).where(or_(*conds)).order_by(LineLineage.id)).scalars()}


def _activate(db: Session, statement: AccountStatement, revision: StatementRevision,
              old_lines: list[lineage.Old]) -> list[int]:
    """Make a persisted revision current, from its stored rows only (no statement_line is written): re-pair the
    current revision's lines (`old_lines`) with the revision's lines through `lineage.pair`, then apply the transfer
    rules (§5.7), flip the event pointers, settle the lineage rows, copy the revision's header onto the statement and
    point `current_revision_id` at it. `submit_revision` calls it right after `_insert_lines` (when the revision
    becomes current), `promote_revision` on a revision stored earlier as non-current (ruling 28).

    Transfer rules: `identical` → the old event's active coverage moves to the new line (lineage `transferred`);
    `normalised` → the same, plus the event is flagged `text_changed` and its pending proposals are superseded;
    `changed`/unpaired old → when the event holds active coverage or an applied action, its coverage is released
    (`lineage:<equivalence>`), it becomes `quarantined` and, in live mode, gets one open `parse_review` case;
    otherwise it is `retired`. The revision's own events (unpaired/`changed` new lines) become `live` on their line;
    events retired earlier stay retired. Lineage rows: the recorded row of each pairing gets `transferred` set to the
    outcome; a pairing without one gets a new row (none for a first revision). Raises `CodedConflictError`
    (`conflict_stale`) when a stored line's event no longer fits the re-pairing (the current revision moved since the
    revision was stored). Returns the ids of the quarantine cases opened or reused."""
    record_lineage = statement.current_revision_id is not None
    rows = _revision_lines(db, revision)
    pairings = lineage.pair(old_lines, _stored_new_lines(rows))
    own_events = {e.id: e for e in db.execute(
        select(StatementEvent).where(StatementEvent.statement_id == statement.id,
                                     StatementEvent.first_revision_id == revision.id)).scalars()}
    paired_events: dict[int, StatementEvent] = {}
    coverage_by_event: dict[int, list] = {}
    with_effects: set[int] = set()
    if record_lineage:
        event_ids = [p.old.event_id for p in pairings if p.old is not None]
        paired_events = {e.id: e for e in db.execute(
            select(StatementEvent).where(StatementEvent.id.in_(event_ids)).with_for_update()).scalars()}
        for active in coverage_service.active_rows(db, statement.id).values():
            for row in active:
                coverage_by_event.setdefault(row.event_id, []).append(row)
        with_effects = _events_with_applied_effects(db, event_ids)
    recorded = _lineage_rows(db, [row.id for row in rows],
                             [p.old.line_id for p in pairings if p.old is not None and p.new is None]) \
        if record_lineage else {}
    case_ids: list[int] = []
    for pairing in pairings:
        new_line = rows[pairing.new.index] if pairing.new is not None else None
        moves = _moves_event(pairing)
        if new_line is not None:
            fits = new_line.event_id == pairing.old.event_id if moves else new_line.event_id in own_events
            if not fits:
                raise CodedConflictError("conflict_stale", f"line {new_line.id} no longer pairs as stored")
            if not moves:
                own = own_events[new_line.event_id]
                own.status, own.current_line_id = "live", new_line.id
        transferred = False
        if pairing.old is not None:
            old_event = paired_events[pairing.old.event_id]
            covered = coverage_by_event.get(old_event.id, [])
            if moves:  # identical / normalised: the event moves
                old_event.current_line_id = new_line.id
                for row in covered:
                    row.line_id = new_line.id
                transferred = True
                if pairing.equivalence == "normalised":
                    old_event.flag = TEXT_CHANGED
                    _supersede_pending_proposals(db, old_event.id)
            else:  # changed / unpaired: the event leaves the current revision
                old_event.current_line_id = None
                _supersede_pending_proposals(db, old_event.id)  # §4.8: line changed beyond normalised
                if covered or old_event.id in with_effects:
                    for line_id in sorted({row.line_id for row in covered}):
                        coverage_service.release_line(db, statement, line_id,
                                                      reason=f"lineage:{pairing.equivalence}")
                    old_event.status = "quarantined"
                    if statement.mode == "live":
                        case_ids.append(_open_quarantine_case(db, statement, revision, old_event,
                                                              pairing.old.line_id, context={
                            "quarantined_event_id": old_event.id, "equivalence": pairing.equivalence,
                            "old_line_id": pairing.old.line_id,
                            "new_line_id": new_line.id if new_line is not None else None}))
                else:
                    old_event.status = "retired"
        if not record_lineage:
            continue
        key = (pairing.old.line_id if pairing.old is not None else None, new_line.id if new_line is not None else None)
        row = recorded.get(key)
        if row is not None:
            row.transferred = transferred
        else:
            db.add(LineLineage(event_id=pairing.old.event_id if pairing.old is not None else new_line.event_id,
                               old_line_id=key[0], new_line_id=key[1], equivalence=pairing.equivalence,
                               transferred=transferred))
    statement.current_revision_id = revision.id
    for column in HEADER_COLUMNS:
        setattr(statement, column, getattr(revision, column))
    db.flush()
    return case_ids


def promote_revision(db: Session, statement: AccountStatement, revision: StatementRevision) -> list[int]:
    """Ruling 28: make a revision stored as non-current (a `statement_conflict` submission) the current one, from the
    persisted rows only — `_activate` against the current revision's lines (it inserts no `statement_line`), then
    the statement-level parse_reviews are superseded and the counts recomputed. The caller holds the statement lock
    and reconciles afterwards. Returns the quarantine case ids."""
    if revision.statement_id != statement.id:
        raise ValidationError("revision_id", "not a revision of this statement")
    if revision.id == statement.current_revision_id:
        raise ValidationError("revision_id", "already the current revision")
    case_ids = _activate(db, statement, revision, _old_lines(db, statement))
    _supersede_parse_reviews(db, statement)
    _recount(db, statement)
    db.flush()
    return case_ids


def _open_case(db: Session, statement: AccountStatement, revision: StatementRevision, kind: str, *,
               context: dict) -> int:
    item = ReconciliationCase(statement_id=statement.id, revision_id=revision.id, kind=kind, context=context)
    db.add(item)
    db.flush()
    return item.id


def _supersede_parse_reviews(db: Session, statement: AccountStatement) -> None:
    """A corrected revision closes the statement's still-open statement-level parse_review cases (version bumped for
    proposals). Event-level ones (quarantines, `event_id` set) stay open: only the owner resolves a quarantine."""
    for item in db.execute(
        select(ReconciliationCase)
        .where(ReconciliationCase.statement_id == statement.id, ReconciliationCase.kind == "parse_review",
               ReconciliationCase.event_id.is_(None), ReconciliationCase.status.in_(OPEN_CASE_STATUSES))
        .with_for_update()
    ).scalars():
        item.status = "superseded"
        item.version = item.version + 1
    db.flush()


def _recount(db: Session, statement: AccountStatement) -> None:
    statement.open_case_count = db.execute(
        select(func.count()).select_from(ReconciliationCase)
        .where(ReconciliationCase.statement_id == statement.id, ReconciliationCase.status.in_(OPEN_CASE_STATUSES))
    ).scalar_one()
    statement.matched_count = db.execute(
        select(func.count(func.distinct(StatementCoverage.line_id)))
        .where(StatementCoverage.statement_id == statement.id, StatementCoverage.status == "active")
    ).scalar_one()


def submit_revision(db: Session, run: IngestRun, payload: RevisionIn, *, account_map: dict[str, int],
                    origin: str | None = None) -> SubmitResult:
    """Store one revision (module docstring). `origin` names a statement created here: default `import` with a file,
    `manual` without one (R1a); the manual-statement route passes `manual`."""
    # 1. account, kind, folder mapping
    account = db.get(Account, payload.account_id)
    if account is None:
        raise NotFoundError(f"account {payload.account_id} not found")
    if (payload.kind == "card") != bool(account.is_credit):
        raise ValidationError("kind", "statement kind does not match the account")
    if payload.period_start > payload.period_end:
        raise ValidationError("period_start", "period_start is after period_end")
    if payload.file_id is not None:
        _assert_folder_maps(db, payload.file_id, payload.account_id, account_map)
        db.get(StatementFile, payload.file_id).account_id = payload.account_id
    # 2. server-side derivation and guardrails
    try:
        derived = derive.derive_lines(payload.kind, [derive.LineIn(**line.model_dump()) for line in payload.lines])
    except derive.SignError as exc:
        raise ValidationError("lines", str(exc)) from exc
    header = derive.Header(**_header_columns(payload), currency=payload.currency)
    check = derive.guardrails(payload.kind, header, account.currency, derived)
    # 3. mode (only used when the statement is created; an existing statement keeps its mode)
    live = account.statement_live_from is not None and payload.period_end >= account.statement_live_from
    # 4. statement identity, locked
    origin = origin or ("import" if payload.file_id is not None else "manual")
    statement, created = _lock_statement(db, run, payload, "live" if live else "historical", origin)
    # 5. revision
    old, new = _old_lines(db, statement), _new_lines(derived)
    pairings = lineage.pair(old, new)
    twins_changed = (not created) and lineage.twin_count_changed(old, new)
    header_changes = {} if created else _header_changes(statement, payload)
    header_changed = bool(header_changes)
    conflict = statement.status == "reconciled"
    # A passing revision replaces a failed current one: the failed header is no trustworthy comparison base.
    current_failed = not created and not db.get(StatementRevision, statement.current_revision_id).guardrail_ok
    correction = current_failed and check.ok and not conflict
    becomes_current = created or correction or (check.ok and not conflict and not twins_changed and not header_changed)
    number = (db.execute(select(func.max(StatementRevision.revision))
                         .where(StatementRevision.statement_id == statement.id)).scalar() or 0) + 1
    revision = StatementRevision(
        statement_id=statement.id, revision=number, file_id=payload.file_id, parser=payload.parser,
        parser_version=payload.parser_version, raw=payload.raw, guardrail_ok=check.ok, conflict=conflict,
        run_id=run.id, **_header_columns(payload),
        guardrail={"checks": check.checks, "detail": check.detail, "twins_changed": twins_changed,
                   "header_changes": header_changes})
    db.add(revision)
    db.flush()
    # 6. lines, events, lineage (stored as not current)
    counts = _insert_lines(db, statement, revision, derived, pairings)
    # 7. current revision (transfers, quarantines, event pointers, header) / conflict
    quarantine_case_ids = _activate(db, statement, revision, old) if becomes_current else []
    if conflict:
        statement.conflict_open = True
    if correction:
        _supersede_parse_reviews(db, statement)
    matcher_case_ids = reconciliation_hook(db, statement, revision, run)
    # 8. cases (live mode only; historical diagnostics stay in revision.guardrail); quarantines were opened in step 6,
    # the matcher's by the hook
    case_ids: list[int] = list(quarantine_case_ids) + matcher_case_ids
    # A reconciled statement gets only the conflict case: it already reviews the whole revision.
    if statement.mode == "live":
        if conflict:
            case_ids.append(_open_case(db, statement, revision, "statement_conflict", context={
                "revision": number, "guardrail_ok": check.ok, "twins_changed": twins_changed,
                "header_changes": header_changes}))
        elif not correction and (not check.ok or twins_changed or header_changed):
            case_ids.append(_open_case(db, statement, revision, "parse_review", context={
                "checks": check.checks, "detail": check.detail, "twins_changed": twins_changed,
                "header_changes": header_changes}))
    _recount(db, statement)
    db.flush()
    # 9. lineage counts
    return SubmitResult(statement, revision, counts, case_ids)
