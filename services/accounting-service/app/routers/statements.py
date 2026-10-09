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
from ..schemas.statements import (
    FileOut,
    FileRegisterIn,
    FileStatus,
    FileUpdateIn,
    ReconciliationSettingsIn,
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
)
from ..services import settings_service
from ..services import statement_ingest_service as ingest
from ..services.errors import NotFoundError, ValidationError
from .errors import service_errors

router = APIRouter(tags=["Statements"], dependencies=[Depends(require_feature())])

ENQUEUE = [Depends(require("enqueue"))]
INGEST = [Depends(require("ingest"))]
READ = [Depends(require("read"))]
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


# ---- settings ----
@router.get("/settings/reconciliation", dependencies=READ)
def get_reconciliation_settings(db: Session = Depends(get_db)):
    settings = settings_service.get_reconciliation_settings(db)
    db.commit()
    return settings


@router.put("/settings/reconciliation", dependencies=ADMIN)
def update_reconciliation_settings(body: ReconciliationSettingsIn, db: Session = Depends(get_db)):
    with service_errors():
        settings = settings_service.update_reconciliation_settings(db, body.model_dump())
    db.commit()
    return settings
