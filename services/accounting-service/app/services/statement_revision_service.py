"""Statement revisions, events and lineage (design §4.3–4.5, §5.6–5.8).

`submit_revision` derives flow amounts and guardrails server-side, upserts the statement identity
(account, currency, period_end), stores an immutable revision, pairs its lines to the statement's events and
opens cases in live mode. Event state (current line, retirement) follows the CURRENT revision only: a revision that
does not become current (guardrail failure, twin/header change, conflict) records its lineage but moves nothing
(§5.7 "no transfers"). Services never commit; routers do.
"""

from collections import Counter
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import (
    Account,
    AccountStatement,
    IngestRun,
    LineLineage,
    ReconciliationCase,
    StatementEvent,
    StatementFile,
    StatementLine,
    StatementRevision,
    StatementSource,
)
from app.schemas.statements import RevisionIn
from app.services.errors import NotFoundError, ValidationError
from app.services.statements import derive, lineage

HEADER_COLUMNS = ("period_start", "period_end", "closing_date", "due_date", "opening_balance", "statement_total",
                  "minimum_payment")
# Header fields compared between revisions; period_end and currency are the identity.
COMPARED_HEADER = ("period_start", "closing_date", "due_date", "opening_balance", "statement_total", "minimum_payment")
OPEN_CASE_STATUSES = ("open", "proposed")


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


def _lock_statement(db: Session, run: IngestRun, payload: RevisionIn, mode: str) -> tuple[AccountStatement, bool]:
    """Fetch the statement identity FOR UPDATE, creating it first when absent (concurrent creators do not raise)."""
    db.flush()  # populate_existing below must not discard pending changes
    created_id = db.execute(
        pg_insert(AccountStatement)
        .values(account_id=payload.account_id, kind=payload.kind, currency=payload.currency, mode=mode,
                origin="import" if payload.file_id is not None else "manual", created_run_id=run.id,
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


def _write_lines_and_lineage(db: Session, statement: AccountStatement, revision: StatementRevision,
                             derived: list[derive.DerivedLine], pairings: list[lineage.Pairing],
                             becomes_current: bool) -> dict[str, int]:
    """Events for unpaired new lines first (statement_line.event_id is NOT NULL), then lines, then event pointers,
    retirements and lineage rows. A first revision (nothing to pair with) writes no lineage rows."""
    record_lineage = statement.current_revision_id is not None
    new_events: dict[int, StatementEvent] = {}
    for pairing in pairings:
        if pairing.new is not None and pairing.old is None:
            event = StatementEvent(statement_id=statement.id, first_revision_id=revision.id,
                                   status="live" if becomes_current else "retired")
            db.add(event)
            new_events[pairing.new.index] = event
    db.flush()
    lines: dict[int, StatementLine] = {}
    for pairing in pairings:
        if pairing.new is None:
            continue
        d = derived[pairing.new.index]
        event_id = pairing.old.event_id if pairing.old is not None else new_events[pairing.new.index].id
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
    paired_events = {}
    if becomes_current:
        event_ids = [p.old.event_id for p in pairings if p.old is not None]
        paired_events = {e.id: e for e in db.execute(
            select(StatementEvent).where(StatementEvent.id.in_(event_ids)).with_for_update()).scalars()}
    counts = Counter({"identical": 0, "normalised": 0, "changed": 0, "unpaired_old": 0, "new": 0})
    for pairing in pairings:
        new_line = lines.get(pairing.new.index) if pairing.new is not None else None
        if pairing.old is None:
            event = new_events[pairing.new.index]
            event.first_line_id = new_line.id
            event.current_line_id = new_line.id if becomes_current else None
            counts["new"] += 1
        elif pairing.new is None:
            if becomes_current:
                retired = paired_events[pairing.old.event_id]
                retired.status = "retired"  # R1b: quarantined when effects exist
                retired.current_line_id = None
            counts["unpaired_old"] += 1
        else:
            if becomes_current:
                paired_events[pairing.old.event_id].current_line_id = new_line.id
            counts[pairing.equivalence] += 1
        if not record_lineage:
            continue
        db.add(LineLineage(
            event_id=pairing.old.event_id if pairing.old is not None else new_events[pairing.new.index].id,
            old_line_id=pairing.old.line_id if pairing.old is not None else None,
            new_line_id=new_line.id if new_line is not None else None,
            equivalence=pairing.equivalence, transferred=False))
    db.flush()
    return dict(counts)


def _open_case(db: Session, statement: AccountStatement, revision: StatementRevision, kind: str, *,
               context: dict) -> int:
    item = ReconciliationCase(statement_id=statement.id, revision_id=revision.id, kind=kind, context=context)
    db.add(item)
    db.flush()
    return item.id


def _supersede_parse_reviews(db: Session, statement: AccountStatement) -> None:
    """A corrected revision closes the statement's still-open parse_review cases (version bumped for proposals)."""
    for item in db.execute(
        select(ReconciliationCase)
        .where(ReconciliationCase.statement_id == statement.id, ReconciliationCase.kind == "parse_review",
               ReconciliationCase.status.in_(OPEN_CASE_STATUSES))
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


def submit_revision(db: Session, run: IngestRun, payload: RevisionIn, *, account_map: dict[str, int]) -> SubmitResult:
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
    statement, created = _lock_statement(db, run, payload, "live" if live else "historical")
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
    # 6. lines, events, lineage
    counts = _write_lines_and_lineage(db, statement, revision, derived, pairings, becomes_current)
    # 7. current revision / conflict
    if becomes_current:
        statement.current_revision_id = revision.id
        for column, value in _header_columns(payload).items():
            setattr(statement, column, value)
    if conflict:
        statement.conflict_open = True
    if correction:
        _supersede_parse_reviews(db, statement)
    # 8. cases (live mode only; historical diagnostics stay in revision.guardrail)
    case_ids: list[int] = []
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
