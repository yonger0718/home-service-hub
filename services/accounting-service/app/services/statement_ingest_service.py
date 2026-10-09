"""Statement ingest: runs with leases, files, sources, statement reads (design §4.1, §4.2).

Revisions, events and lineage live in statement_revision_service and are re-exported here, so routers import one
module. Services never commit; routers do.
"""

import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, exists, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import (
    Account,
    AccountStatement,
    CoverageDirty,
    IngestRun,
    ReconciliationCase,
    StatementFile,
    StatementLine,
    StatementRevision,
    StatementSource,
)
from app.schemas.statements import FileRegisterIn, FileUpdateIn, SourceRegisterIn
from app.services.errors import ConflictError, NotFoundError
from app.services.statement_revision_service import SubmitResult, submit_revision

__all__ = [
    "LEASE", "TERMINAL", "SubmitResult", "enqueue_run", "create_worker_run", "list_runs", "claim_run", "renew_lease",
    "finish_run", "require_lease", "register_file", "register_source", "update_file", "mark_removed_sources",
    "submit_revision", "list_statements", "get_statement",
]

LEASE = timedelta(minutes=30)
TERMINAL = ("done", "failed")
LEASED = ("claimed", "running")
ENQUEUE_LOCK_KEY = 0x53544D51  # "STMQ": serialises enqueue so two requests coalesce instead of both inserting
LEASE_FIELDS = {"run_id", "lease_token"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---- runs ----
def enqueue_run(db: Session, *, principal: str) -> IngestRun:
    """Insert a queued run, or return the existing non-terminal one with this request appended to summary.coalesced.

    The caller learns whether it coalesced from `bool(run.summary.get("coalesced"))` (the creating call has none).
    """
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": ENQUEUE_LOCK_KEY})
    run = db.execute(
        select(IngestRun).where(IngestRun.status.not_in(TERMINAL)).order_by(IngestRun.id).limit(1).with_for_update()
    ).scalar_one_or_none()
    if run is None:
        run = IngestRun(trigger="enqueue", principal=principal, mode="live", status="queued", summary={})
        db.add(run)
    else:
        summary = dict(run.summary or {})
        summary["coalesced"] = [*summary.get("coalesced", []), {"principal": principal, "at": _now().isoformat()}]
        run.summary = summary
    db.flush()
    return run


def create_worker_run(db: Session, *, trigger: str, initiator_hint: str | None, mode: str) -> IngestRun:
    """Timer/CLI runs are created by the worker itself (principal 'worker', unauthenticated initiator hint)."""
    run = IngestRun(trigger=trigger, principal="worker", initiator_hint=initiator_hint, mode=mode, status="queued",
                    summary={})
    db.add(run)
    db.flush()
    return run


def list_runs(db: Session, *, status: str | None) -> list[IngestRun]:
    query = select(IngestRun).order_by(IngestRun.id)
    if status is not None:
        query = query.where(IngestRun.status == status)
    return list(db.execute(query).scalars())


def _get_run(db: Session, run_id: int) -> IngestRun:
    run = db.get(IngestRun, run_id)
    if run is None:
        raise NotFoundError(f"run {run_id} not found")
    return run


def claim_run(db: Session, run_id: int, *, label: str) -> tuple[IngestRun, str]:
    """Atomically move queued/expired → claimed with a fresh token and a 30-minute lease; a stale claimed/running
    run is lazily expired and the claim retried once."""
    db.flush()
    claimed = _try_claim(db, run_id, label)
    if claimed is None:
        _get_run(db, run_id)
        # Conditional, so a concurrent fresh claim is never overwritten and one expiry bumps attempt only once.
        expired = db.execute(
            update(IngestRun)
            .where(IngestRun.id == run_id, IngestRun.status.in_(LEASED), IngestRun.lease_expires_at < _now())
            .values(status="expired")
            .returning(IngestRun.id)
            .execution_options(synchronize_session=False)
        ).scalar_one_or_none()
        if expired is not None:
            claimed = _try_claim(db, run_id, label)
    if claimed is None:
        raise ConflictError("run not claimable")
    return claimed


def _try_claim(db: Session, run_id: int, label: str) -> tuple[IngestRun, str] | None:
    token = secrets.token_hex(24)
    now = _now()
    updated = db.execute(
        update(IngestRun)
        .where(IngestRun.id == run_id, IngestRun.status.in_(("queued", "expired")))
        .values(status="claimed", claimed_by=label, lease_token=token, lease_expires_at=now + LEASE,
                attempt=IngestRun.attempt + 1, started_at=func.coalesce(IngestRun.started_at, now))
        .returning(IngestRun.id)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    if updated is None:
        return None
    return db.get(IngestRun, run_id, populate_existing=True), token


def _check_token(run: IngestRun, token: str) -> None:
    if (run.status not in LEASED or not hmac.compare_digest((run.lease_token or "").encode(), token.encode())
            or run.lease_expires_at is None or run.lease_expires_at < _now()):
        raise ConflictError("lease")


def require_lease(db: Session, run_id: int, token: str, *, label: str) -> IngestRun:
    """Fence a submission: the run is claimed/running by `label`, the token matches and the lease is live."""
    run = _get_run(db, run_id)
    _check_token(run, token)
    if run.claimed_by != label:
        raise ConflictError("lease")
    if run.status == "claimed":
        run.status = "running"
        db.flush()
    return run


def renew_lease(db: Session, run_id: int, token: str) -> IngestRun:
    run = _get_run(db, run_id)
    _check_token(run, token)
    run.lease_expires_at = _now() + LEASE
    db.flush()
    return run


def finish_run(db: Session, run_id: int, token: str, *, status: str, summary: dict) -> IngestRun:
    """Close a leased run as done/failed; the worker's summary is merged over the stored one (keeps coalesced)."""
    if status not in TERMINAL:
        raise ConflictError(f"cannot finish a run as {status}")
    run = _get_run(db, run_id)
    _check_token(run, token)
    run.status = status
    run.finished_at = _now()
    run.summary = {**(run.summary or {}), **summary}
    db.flush()
    return run


# ---- files and sources ----
def register_file(db: Session, run: IngestRun, payload: FileRegisterIn) -> StatementFile:
    """Idempotent by sha256: concurrent duplicates insert nothing and read the existing row."""
    sha = payload.sha256.lower()
    db.execute(
        pg_insert(StatementFile)
        .values(sha256=sha, size=payload.size, kind=payload.kind, object_key=payload.object_key, run_id=run.id)
        .on_conflict_do_nothing(index_elements=["sha256"])
    )
    return db.execute(select(StatementFile).where(StatementFile.sha256 == sha)).scalar_one()


def list_files(db: Session, *, status: str | None) -> list[StatementFile]:
    query = select(StatementFile).order_by(StatementFile.id)
    if status is not None:
        query = query.where(StatementFile.status == status)
    return list(db.execute(query).scalars())


def update_file(db: Session, run: IngestRun, file_id: int, payload: FileUpdateIn) -> StatementFile:
    """Record a processing outcome. `failure` always follows the new status; other fields only when sent."""
    file = db.get(StatementFile, file_id, with_for_update=True)
    if file is None:
        raise NotFoundError(f"statement file {file_id} not found")
    changes = payload.model_dump(exclude=LEASE_FIELDS, exclude_unset=True)
    changes["status"] = payload.status
    changes["failure"] = payload.failure
    for column, value in changes.items():
        setattr(file, column, value)
    if payload.status == "parsed":
        file.parsed_at = _now()
    if payload.status == "failed":
        file.attempts = (file.attempts or 0) + 1
    db.flush()
    return file


def register_source(db: Session, run: IngestRun, payload: SourceRegisterIn) -> StatementSource:
    """Upsert by (drive_file_id, drive_md5); a path change appends the old path to path_history."""
    if db.get(StatementFile, payload.file_id) is None:
        raise NotFoundError(f"statement file {payload.file_id} not found")
    md5 = payload.drive_md5.lower()
    now = _now()
    inserted = db.execute(
        pg_insert(StatementSource)
        .values(file_id=payload.file_id, root=payload.root, drive_file_id=payload.drive_file_id,
                drive_path=payload.drive_path, drive_md5=md5, drive_size=payload.drive_size, last_seen_at=now)
        .on_conflict_do_nothing(index_elements=["drive_file_id", "drive_md5"])
        .returning(StatementSource.id)
    ).scalar_one_or_none()
    source = db.execute(
        select(StatementSource)
        .where(StatementSource.drive_file_id == payload.drive_file_id, StatementSource.drive_md5 == md5)
        .with_for_update()
    ).scalar_one()
    if inserted is not None:
        # Same Drive file, new bytes: older versions are superseded by this one.
        db.execute(
            update(StatementSource)
            .where(StatementSource.drive_file_id == payload.drive_file_id, StatementSource.id != source.id,
                   StatementSource.superseded_by_source_id.is_(None))
            .values(superseded_by_source_id=source.id)
            .execution_options(synchronize_session=False)
        )
        return source
    if source.file_id != payload.file_id:
        raise ConflictError("source belongs to another file")
    if source.drive_path != payload.drive_path:
        source.path_history = [*(source.path_history or []), {"path": source.drive_path, "until": now.isoformat()}]
        source.drive_path = payload.drive_path
    source.last_seen_at = now
    source.removed_at = None
    db.flush()
    return source


def mark_removed_sources(db: Session, run: IngestRun, seen_ids: set[str]) -> int:
    """After a COMPLETE listing only: sources whose drive_file_id was not listed get removed_at."""
    query = update(StatementSource).where(StatementSource.removed_at.is_(None))
    if seen_ids:
        query = query.where(StatementSource.drive_file_id.not_in(seen_ids))
    result = db.execute(query.values(removed_at=_now()).execution_options(synchronize_session="fetch"))
    db.flush()
    return result.rowcount


# ---- statements ----
HEADER_FIELDS = ("id", "account_id", "kind", "currency", "period_start", "period_end", "closing_date", "due_date",
                 "opening_balance", "statement_total", "minimum_payment", "origin", "mode", "status", "conflict_open",
                 "needs_recheck", "current_revision_id", "matched_count", "explained_count", "open_case_count")
LINE_FIELDS = ("id", "event_id", "seq", "txn_date", "posted_date", "merchant_raw", "merchant_norm", "printed_amount",
               "flow_amount", "foreign_amount", "foreign_currency", "line_kind", "installment_seq", "installment_total")
CASE_FIELDS = ("id", "kind", "status", "event_id", "line_id", "entry_id", "explanation", "candidates", "context",
               "version", "created_at")
REVISION_FIELDS = ("id", "revision", "file_id", "parser", "parser_version", "guardrail_ok", "conflict", "rejected",
                   "created_at")


def _row(obj, fields) -> dict:
    return {name: getattr(obj, name) for name in fields}


def list_statements(db: Session, account_id: int) -> list[AccountStatement]:
    if db.get(Account, account_id) is None:
        raise NotFoundError(f"account {account_id} not found")
    return list(db.execute(
        select(AccountStatement).where(AccountStatement.account_id == account_id)
        .order_by(AccountStatement.period_end.desc(), AccountStatement.id.desc())
    ).scalars())


def get_statement(db: Session, statement_id: int) -> dict:
    """Header, current-revision lines (with event ids), cases (open/proposed first, newest first), revisions and
    `stale_events_pending` (ledger changes on the account past the statement's sweep watermark)."""
    statement = db.get(AccountStatement, statement_id)
    if statement is None:
        raise NotFoundError(f"statement {statement_id} not found")
    lines = []
    if statement.current_revision_id is not None:
        lines = db.execute(
            select(StatementLine).where(StatementLine.revision_id == statement.current_revision_id)
            .order_by(StatementLine.seq)
        ).scalars()
    cases = db.execute(
        select(ReconciliationCase).where(ReconciliationCase.statement_id == statement.id)
        .order_by(case((ReconciliationCase.status.in_(("open", "proposed")), 0), else_=1),
                  ReconciliationCase.created_at.desc(), ReconciliationCase.id.desc())
    ).scalars()
    revisions = db.execute(
        select(StatementRevision).where(StatementRevision.statement_id == statement.id)
        .order_by(StatementRevision.revision.desc())
    ).scalars()
    stale = db.execute(select(exists().where(
        CoverageDirty.id > statement.swept_through_event_id,
        or_(CoverageDirty.old_account_id == statement.account_id, CoverageDirty.new_account_id == statement.account_id),
    ))).scalar_one()
    return {
        **_row(statement, HEADER_FIELDS),
        "lines": [_row(line, LINE_FIELDS) for line in lines],
        "cases": [_row(item, CASE_FIELDS) for item in cases],
        "revisions": [_row(item, REVISION_FIELDS) for item in revisions],
        "stale_events_pending": bool(stale),
    }
