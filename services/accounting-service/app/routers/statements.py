"""Statement ingest (worker), statement reads and reconciliation settings — reconciliation r1a, design §8.

Every route sits behind `require_feature()` (404 while ACCOUNTING_RECONCILIATION_ENABLED is off) and names its own
scope: `enqueue` to queue a run, `ingest` for the worker's run/file/source/revision calls (each fenced by the run's
lease, checked against the caller's label), `read` for statement reads and cases, `write` to reconcile one statement,
`admin` to change the settings and to retire a duplicate statement. `write` also covers the owner's manual statement
and the statement confirm (R1c Task 11).

The reconcile, revision and manual-statement (live statements) and sweep routes run the owner's rules
(`owner_rule_service.run_rules_safe`, inbox spec §6.3) and then the policy (`policy_service.run_policy_safe`, R1c
Task 10) after their own commit; both commit per case/candidate and neither error ever fails the route.
"""

import logging

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ..auth import require, require_feature
from ..database import get_db, get_engine
from ..models import Account, AccountStatement
from ..schemas.statements import (
    AccountMapOut,
    FileOut,
    FileRegisterIn,
    FileStatus,
    FileUpdateIn,
    CaseKind,
    CaseOut,
    CaseStatus,
    ConfirmIn,
    ConfirmOut,
    Lease,
    ManualStatementIn,
    ReconcileOut,
    ReconciliationSettingsIn,
    ReconciliationSettingsOut,
    RetireIn,
    RetireOut,
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
    SettlementRunIn,
    SettlementRunOut,
    SourceRegisterIn,
    SourcesMarkRemovedIn,
    SourcesMarkRemovedOut,
    StatementDetailOut,
    StandInsOut,
    StatementOut,
    SweepOut,
)
from ..services import confirm_service, owner_rule_service, policy_service, statement_retire_service
from ..services import reconciliation_service as recon
from ..services import settings_service, trade_settlement_service
from ..services import statement_ingest_service as ingest
from ..services.errors import ConflictError, NotFoundError, ValidationError
from .errors import service_errors

logger = logging.getLogger(__name__)
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
def _revision_out(result) -> dict:
    return {
        "statement_id": result.statement.id, "revision_id": result.revision.id,
        "revision": result.revision.revision, "guardrail_ok": result.revision.guardrail_ok,
        "mode": result.statement.mode, "conflict": result.revision.conflict,
        "lineage": result.lineage_counts, "case_ids": result.case_ids, "replayed": result.replayed,
    }


@router.post("/statements/revisions", response_model=RevisionOut, status_code=201, dependencies=INGEST)
def submit_revision(body: RevisionIn, request: Request, db: Session = Depends(get_db)):
    with service_errors():
        run = ingest.require_lease(db, body.run_id, body.lease_token, label=_label(request))
        settings = settings_service.get_reconciliation_settings(db)
        result = ingest.submit_revision(db, run, body, account_map=settings.get("account_map", {}),
                                        composite_map=settings.get("composite_map", {}))
        out, recheck = _revision_out(result), result.neighbours_recheck
    db.commit()
    if recheck:  # its own transaction, after the ingest commit (SubmitResult.neighbours_recheck)
        trade_settlement_service.flag_neighbours_safe(db, out["statement_id"])
    if out["mode"] == "live" and not out["replayed"]:
        owner_rule_service.run_rules_safe(db, out["statement_id"], run_id=body.run_id)
        policy_service.run_policy_safe(db, out["statement_id"], run_id=body.run_id)
    return out


# ---- owner statements: manual entry, confirm ----
@router.post("/accounts/{account_id}/statements", response_model=RevisionOut, status_code=201, dependencies=WRITE)
def create_manual_statement(account_id: int, body: ManualStatementIn, request: Request, db: Session = Depends(get_db)):
    """An owner-entered statement: submitted like an import on an owner_cli run of its own (`origin='manual'`)."""
    with service_errors():
        result = ingest.create_manual_statement(db, account_id=account_id, payload=body,
                                                label=_label(request) or "owner")
        out, recheck = _revision_out(result), result.neighbours_recheck
        run_id = result.revision.run_id
    db.commit()
    if recheck:
        trade_settlement_service.flag_neighbours_safe(db, out["statement_id"])
    if out["mode"] == "live" and not out["replayed"]:
        owner_rule_service.run_rules_safe(db, out["statement_id"], run_id=run_id)
        policy_service.run_policy_safe(db, out["statement_id"], run_id=run_id)
    return out


@router.post("/accounts/{account_id}/statements/{statement_id}/confirm", response_model=ConfirmOut,
             dependencies=WRITE)
def confirm_statement(account_id: int, statement_id: int, body: ConfirmIn, request: Request,
                      db: Session = Depends(get_db)):
    """409 `confirm_refused: <checks JSON>` when a check fails without `force`; 409 `confirm_retry` when a deferral
    was linked during the call; 422 `note_required` for `force` without a note."""
    with service_errors():
        result = confirm_service.confirm_statement(
            db, account_id=account_id, statement_id=statement_id, force=body.force, note=body.note,
            label=_label(request) or "owner", request_id=request.headers.get("x-request-id"))
        out = {"status": result.status, "checks": result.checks, "reopened_cases": result.reopened_cases,
               "forced": result.forced}
    db.commit()
    return out


@router.post("/accounts/{account_id}/statements/{statement_id}/retire", response_model=RetireOut, dependencies=ADMIN)
def retire_statement(account_id: int, statement_id: int, body: RetireIn, request: Request,
                     db: Session = Depends(get_db)):
    """Retire a duplicate statement (statement_retire_service): 409 `anchor_depends` while an applied anchor_opening
    rests on it, 409 `actions_applied` while an action on its cases is applied, 409 `sweep_barrier_busy` when the
    sweep it runs under the lock meets a busy writer barrier (retry); a
    retired statement answers 200 with `already_retired` and writes nothing."""
    with service_errors():
        result = statement_retire_service.retire_statement(
            db, account_id=account_id, statement_id=statement_id, note=body.note, label=_label(request) or "admin",
            request_id=request.headers.get("x-request-id"))
        out = {"statement_id": result.statement.id, "mode": result.statement.mode,
               "already_retired": result.already_retired, "released_coverage": result.released_coverage,
               "retired_events": result.retired_events, "superseded_cases": result.superseded_cases}
    db.commit()
    return out


# ---- statement reads ----
@router.get("/accounts/{account_id}/statements", response_model=list[StatementOut], dependencies=READ)
def list_statements(account_id: int, db: Session = Depends(get_db)):
    with service_errors():
        return ingest.list_statements(db, account_id)


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
               "unmatched_entries": result.unmatched_entries, "skipped": result.skipped,
               "settlement_source_unavailable": result.settlement_source_unavailable,
               "settlements": dict(result.settlements)}
    db.commit()
    owner_rule_service.run_rules_safe(db, statement_id)
    policy = policy_service.run_policy_safe(db, statement_id)
    return {**out, "policy": {"applied": len(policy.applied), "proposed": len(policy.proposed)}}


@router.post("/reconciliation/settlements/run", response_model=SettlementRunOut, dependencies=INGEST)
def run_settlements(body: SettlementRunIn, db: Session = Depends(get_db), engine: Engine = Depends(get_engine)):
    """Spec §4.5 item 1: run the daily stand-in job now (the 國泰 worker calls it after each commit run) and flag the
    statements it touched, plus those with open settlement cases, for re-check. Counts and codes only."""
    report = trade_settlement_service.run_stand_ins(engine)  # its own transactions: `db` has none open yet
    with service_errors():
        flagged = trade_settlement_service.flag_for_recheck(db, report)
    db.commit()
    return {"status": report["status"], "booked": len(report["booked"]), "skipped": len(report["skipped"]),
            "housekeeping": report["housekeeping"], "flagged": flagged}


@router.post("/reconciliation/sweep", response_model=SweepOut, dependencies=INGEST)
def sweep(body: Lease, request: Request, db: Session = Depends(get_db), engine: Engine = Depends(get_engine)):
    """The worker's daily pass over every pending statement. One transaction per statement: the next statement's
    sweep must not run while this one still holds the previous statement's entry/group locks."""
    totals = {"statements": 0, "claims": 0, "cases_opened": 0, "settlement_source_unavailable": 0}
    errors: list[dict] = []
    with service_errors():
        ingest.require_lease(db, body.run_id, body.lease_token, label=_label(request))
        db.commit()  # release the lease row lock; each statement then commits on its own
        label = _label(request)
        stand_ins = StandInsOut(status="failed", booked=0, skipped=0, flagged=0)
        extra_ids: list[int] = []
        try:  # the stand-in job must never cost the day's reconciliation (class name only: a message may carry ids)
            report = trade_settlement_service.run_stand_ins(engine)  # the job's own transactions; `db` holds none
            flagged = trade_settlement_service.flag_for_recheck(db, report)
            db.commit()
            stand_ins = StandInsOut(status=report["status"], booked=len(report["booked"]),
                                    skipped=len(report["skipped"]), flagged=flagged)
        except Exception as exc:  # noqa: BLE001
            logger.error("sweep.stand_ins_failed: %s", type(exc).__name__)
            db.rollback()
        try:
            extra_ids = trade_settlement_service.recheck_statement_ids(db)
        except Exception as exc:  # noqa: BLE001
            logger.error("sweep.recheck_ids_failed: %s", type(exc).__name__)
            db.rollback()
        pending = sorted(set(recon.pending_statement_ids(db)) | set(extra_ids))
        for index, item in enumerate(recon.reconcile_each(db, pending, run_id=body.run_id)):
            if "error" in item:
                errors.append({"statement_id": item["statement_id"], "error": item["error"]})
            else:
                totals["statements"] += 1
                totals["claims"] += item["result"].claims
                totals["cases_opened"] += len(item["result"].cases_opened)
                totals["settlement_source_unavailable"] += int(item["result"].settlement_source_unavailable)
            db.commit()  # the items done so far stay committed
            if "error" not in item:
                owner_rule_service.run_rules_safe(db, item["statement_id"], run_id=body.run_id)
                policy_service.run_policy_safe(db, item["statement_id"], run_id=body.run_id)
            if index < len(pending) - 1 and not ingest.lease_is_live(db, body.run_id, body.lease_token, label):
                db.rollback()  # release the read transaction before refusing
                raise ConflictError("lease")
        links = recon.fill_deferral_links(db)
    db.commit()
    return {**totals, "errors": errors, "links_filled": links, "stand_ins": stand_ins}


@router.get("/statements/account-map", response_model=AccountMapOut, dependencies=INGEST)
def read_account_map(db: Session = Depends(get_db)):
    data = settings_service.read_reconciliation_settings(db)
    db.commit()
    account_map = data.get("account_map") or {}
    closing_days = {str(account_id): day for account_id, day in db.execute(
        select(Account.id, Account.closing_day).where(Account.closing_day.is_not(None))).all()}
    composite_map = data.get("composite_map") or {}
    card_accounts = list(db.execute(select(Account.id).where(Account.is_credit).order_by(Account.id)).scalars())
    return AccountMapOut(account_map=account_map, composite_map=composite_map,
                         mapping_version=settings_service.mapping_version(account_map, composite_map),
                         closing_days=closing_days, card_accounts=card_accounts)


@router.get("/reconciliation/cases", response_model=list[CaseOut], dependencies=READ)
def list_cases(status: CaseStatus | None = None, account_id: int | None = None, kind: CaseKind | None = None,
               limit: int = Query(200, ge=1, le=1000), db: Session = Depends(get_db)):
    return ingest.list_cases(db, status=status, account_id=account_id, kind=kind, limit=limit)


# ---- settings ----
@router.get("/settings/reconciliation", response_model=ReconciliationSettingsOut, dependencies=READ)
def get_reconciliation_settings(db: Session = Depends(get_db)):
    settings = settings_service.read_reconciliation_settings(db)
    db.commit()
    return settings


@router.put("/settings/reconciliation", response_model=ReconciliationSettingsOut, dependencies=ADMIN)
def update_reconciliation_settings(body: ReconciliationSettingsIn, db: Session = Depends(get_db)):
    with service_errors():
        settings = settings_service.update_reconciliation_settings(db, body.model_dump())
    db.commit()
    return settings
