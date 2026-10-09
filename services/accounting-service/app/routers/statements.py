"""Statement ingest (worker), statement reads and reconciliation settings — reconciliation r1a, design §8.

Every route sits behind `require_feature()` (404 while ACCOUNTING_RECONCILIATION_ENABLED is off) and names its own
scope: `enqueue` to queue a run, `ingest` for the worker's run/file/source/revision calls (each fenced by the run's
lease, checked against the caller's label), `read` for statement reads, `admin` to change the settings.
"""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import require, require_feature
from ..database import get_db
from ..models import AccountStatement
from ..schemas.statements import (
    FileOut,
    FileRegisterIn,
    FileStatus,
    FileUpdateIn,
    CaseKind,
    CaseOut,
    CaseStatus,
    Lease,
    ReconcileOut,
    ReconciliationSettingsIn,
    ReconciliationSettingsOut,
    RevisionIn,
    RevisionOut,
    RunClaimOut,
    RunCreateIn,
    RunEnqueueOut,
    RunFinishIn,
    RunLeaseIn,
    RunOut,
    RunStatus,
    SourceOut,
    SourceRegisterIn,
    SourcesMarkRemovedIn,
    SourcesMarkRemovedOut,
    StatementDetailOut,
    StatementOut,
    SweepOut,
)
from ..services import reconciliation_service as recon
from ..services import settings_service
from ..services import statement_ingest_service as ingest
from ..services.errors import NotFoundError, ValidationError
from .errors import service_errors

router = APIRouter(tags=["Statements"], dependencies=[Depends(require_feature())])

ENQUEUE = [Depends(require("enqueue"))]
INGEST = [Depends(require("ingest"))]
READ = [Depends(require("read"))]
WRITE = [Depends(require("write"))]
ADMIN = [Depends(require("admin"))]


def _label(request: Request) -> str | None:
    return getattr(request.state, "client_label", None)


def _out(model: type[BaseModel], obj) -> dict:
    """The ORM row as a dict with exactly the response model's fields (the models forbid extras)."""
    return {name: getattr(obj, name) for name in model.model_fields}


# ---- runs ----
@router.post("/statements/ingest/run", response_model=RunEnqueueOut, status_code=202, dependencies=ENQUEUE)
def enqueue_run(request: Request, db: Session = Depends(get_db)):
    with service_errors():
        run = ingest.enqueue_run(db, principal=_label(request) or "anonymous")
        out = {"run_id": run.id, "status": run.status, "coalesced": bool((run.summary or {}).get("coalesced"))}
    db.commit()
    return out


@router.post("/statements/ingest-runs", response_model=RunClaimOut, status_code=201, dependencies=INGEST)
def create_run(body: RunCreateIn, request: Request, db: Session = Depends(get_db)):
    """The worker creates its own timer/CLI run and claims it in the same transaction."""
    with service_errors():
        created = ingest.create_worker_run(db, trigger=body.trigger, initiator_hint=body.initiator_hint, mode=body.mode)
        run, token = ingest.claim_run(db, created.id, label=_label(request))
        out = {"run_id": run.id, "lease_token": token, "lease_expires_at": run.lease_expires_at, "attempt": run.attempt}
    db.commit()
    return out


@router.get("/statements/ingest-runs", response_model=list[RunOut], dependencies=INGEST)
def list_runs(status: RunStatus | None = None, db: Session = Depends(get_db)):
    return [_out(RunOut, run) for run in ingest.list_runs(db, status=status)]


@router.post("/statements/ingest-runs/{run_id}/claim", response_model=RunClaimOut, dependencies=INGEST)
def claim_run(run_id: int, request: Request, db: Session = Depends(get_db)):
    with service_errors():
        run, token = ingest.claim_run(db, run_id, label=_label(request))
        out = {"run_id": run.id, "lease_token": token, "lease_expires_at": run.lease_expires_at, "attempt": run.attempt}
    db.commit()
    return out


@router.post("/statements/ingest-runs/{run_id}/renew", response_model=RunClaimOut, dependencies=INGEST)
def renew_lease(run_id: int, body: RunLeaseIn, request: Request, db: Session = Depends(get_db)):
    with service_errors():
        ingest.require_lease(db, run_id, body.lease_token, label=_label(request))
        run = ingest.renew_lease(db, run_id, body.lease_token)
        out = {"run_id": run.id, "lease_token": body.lease_token, "lease_expires_at": run.lease_expires_at,
               "attempt": run.attempt}
    db.commit()
    return out


@router.post("/statements/ingest-runs/{run_id}/finish", response_model=RunOut, dependencies=INGEST)
def finish_run(run_id: int, body: RunFinishIn, request: Request, db: Session = Depends(get_db)):
    with service_errors():
        if body.run_id != run_id:
            raise ValidationError("run_id", "does not match the path")
        ingest.require_lease(db, body.run_id, body.lease_token, label=_label(request))
        run = ingest.finish_run(db, run_id, body.lease_token, status=body.status, summary=body.summary)
        out = _out(RunOut, run)
    db.commit()
    return out


# ---- files ----
@router.post("/statements/files", response_model=FileOut, status_code=201, dependencies=INGEST)
def register_file(body: FileRegisterIn, request: Request, db: Session = Depends(get_db)):
    with service_errors():
        run = ingest.require_lease(db, body.run_id, body.lease_token, label=_label(request))
        out = _out(FileOut, ingest.register_file(db, run, body))
    db.commit()
    return out


@router.patch("/statements/files/{file_id}", response_model=FileOut, dependencies=INGEST)
def update_file(file_id: int, body: FileUpdateIn, request: Request, db: Session = Depends(get_db)):
    with service_errors():
        run = ingest.require_lease(db, body.run_id, body.lease_token, label=_label(request))
        out = _out(FileOut, ingest.update_file(db, run, file_id, body))
    db.commit()
    return out


@router.get("/statements/files", response_model=list[FileOut], dependencies=INGEST)
def list_files(status: FileStatus | None = None, db: Session = Depends(get_db)):
    return [_out(FileOut, file) for file in ingest.list_files(db, status=status)]


# ---- sources ----
@router.post("/statements/sources", response_model=SourceOut, status_code=201, dependencies=INGEST)
def register_source(body: SourceRegisterIn, request: Request, db: Session = Depends(get_db)):
    with service_errors():
        run = ingest.require_lease(db, body.run_id, body.lease_token, label=_label(request))
        out = _out(SourceOut, ingest.register_source(db, run, body))
    db.commit()
    return out


@router.post("/statements/sources/mark-removed", response_model=SourcesMarkRemovedOut, dependencies=INGEST)
def mark_removed_sources(body: SourcesMarkRemovedIn, request: Request, db: Session = Depends(get_db)):
    with service_errors():
        if not body.seen_drive_file_ids and not body.allow_empty:
            raise ValidationError("seen_drive_file_ids", "empty set needs allow_empty")
        run = ingest.require_lease(db, body.run_id, body.lease_token, label=_label(request))
        removed = ingest.mark_removed_sources(db, run, set(body.seen_drive_file_ids))
    db.commit()
    return {"removed": removed}


# ---- revisions ----
@router.post("/statements/revisions", response_model=RevisionOut, status_code=201, dependencies=INGEST)
def submit_revision(body: RevisionIn, request: Request, db: Session = Depends(get_db)):
    with service_errors():
        run = ingest.require_lease(db, body.run_id, body.lease_token, label=_label(request))
        account_map = settings_service.get_reconciliation_settings(db).get("account_map", {})
        result = ingest.submit_revision(db, run, body, account_map=account_map)
        out = {
            "statement_id": result.statement.id, "revision_id": result.revision.id,
            "revision": result.revision.revision, "guardrail_ok": result.revision.guardrail_ok,
            "mode": result.statement.mode, "conflict": result.revision.conflict,
            "lineage": result.lineage_counts, "case_ids": result.case_ids,
        }
    db.commit()
    return out


# ---- statement reads ----
@router.get("/accounts/{account_id}/statements", response_model=list[StatementOut], dependencies=READ)
def list_statements(account_id: int, db: Session = Depends(get_db)):
    with service_errors():
        statements = ingest.list_statements(db, account_id)
    return [_out(StatementOut, statement) for statement in statements]


@router.get("/accounts/{account_id}/statements/{statement_id}", response_model=StatementDetailOut, dependencies=READ)
def get_statement(account_id: int, statement_id: int, db: Session = Depends(get_db)):
    with service_errors():
        statement = ingest.get_statement(db, statement_id)
        if statement["account_id"] != account_id:
            raise NotFoundError(f"statement {statement_id} not found")
    return statement


# ---- reconcile, sweep, cases ----
@router.post("/accounts/{account_id}/statements/{statement_id}/reconcile", response_model=ReconcileOut,
             dependencies=WRITE)
def reconcile_statement(account_id: int, statement_id: int, db: Session = Depends(get_db)):
    with service_errors():
        statement = db.get(AccountStatement, statement_id)
        if statement is None or statement.account_id != account_id:
            raise NotFoundError(f"statement {statement_id} not found")
        result = recon.reconcile(db, statement_id)
        out = {"claims": result.claims, "cases_opened": result.cases_opened, "explained": result.explained,
               "unmatched_entries": result.unmatched_entries, "skipped": result.skipped}
    db.commit()
    return out


@router.post("/reconciliation/sweep", response_model=SweepOut, dependencies=INGEST)
def sweep(body: Lease, request: Request, db: Session = Depends(get_db)):
    """The worker's daily pass over every pending statement. One transaction per statement: the next statement's
    sweep must not run while this one still holds the previous statement's entry/group locks."""
    totals = {"statements": 0, "claims": 0, "cases_opened": 0}
    errors: list[dict] = []
    with service_errors():
        ingest.require_lease(db, body.run_id, body.lease_token, label=_label(request))
        db.commit()  # release the lease row lock; each statement then commits on its own
        for item in recon.reconcile_each(db, recon.pending_statement_ids(db), run_id=body.run_id):
            if "error" in item:
                errors.append({"statement_id": item["statement_id"], "error": item["error"]})
            else:
                totals["statements"] += 1
                totals["claims"] += item["result"].claims
                totals["cases_opened"] += len(item["result"].cases_opened)
            db.commit()
        links = recon.fill_deferral_links(db)
    db.commit()
    return {**totals, "errors": errors, "links_filled": links}


@router.get("/reconciliation/cases", response_model=list[CaseOut], dependencies=READ)
def list_cases(status: CaseStatus | None = None, account_id: int | None = None, kind: CaseKind | None = None,
               db: Session = Depends(get_db)):
    return ingest.list_cases(db, status=status, account_id=account_id, kind=kind)


# ---- settings ----
@router.get("/settings/reconciliation", response_model=ReconciliationSettingsOut, dependencies=READ)
def get_reconciliation_settings(db: Session = Depends(get_db)):
    settings = settings_service.get_reconciliation_settings(db)
    db.commit()
    return settings


@router.put("/settings/reconciliation", response_model=ReconciliationSettingsOut, dependencies=ADMIN)
def update_reconciliation_settings(body: ReconciliationSettingsIn, db: Session = Depends(get_db)):
    with service_errors():
        settings = settings_service.update_reconciliation_settings(db, body.model_dump())
    db.commit()
    return settings
