"""Reconciliation case detail, owner actions, revert, action and audit listings (R1c, spec §4.9).

Behind `require_feature()` like the statements router. Owner actions need `write`; reads need `read`. The engine
never commits and these handlers do; policy does not run after owner actions.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from sqlalchemy.orm import Session

from ..auth import require, require_feature
from ..database import get_db
from ..schemas.reconciliation import (
    ActionIn, ActionOut, AuditOut, DismissIn, ProposalIn, ProposalOut, ProposalRejectIn,
)
from ..schemas.inbox import InboxApplyIn, InboxApplyOut, InboxCandidate, InboxOut
from ..schemas.statements import CaseDetailOut
from ..services import inbox_service, proposal_service
from ..services import statement_ingest_service as ingest
from ..services.reconciliation_actions import engine
from .errors import service_errors
from .statements import READ, WRITE, _label

PROPOSE = [Depends(require("propose"))]
INBOX_STATUSES = ("open", "proposed")

router = APIRouter(tags=["Reconciliation"], dependencies=[Depends(require_feature())])


def _action_out(row, *, replayed: bool = False) -> dict:
    fields = {name: getattr(row, name) for name in ActionOut.model_fields if name != "replayed"}
    return {**fields, "replayed": replayed}


def _apply(db: Session, request: Request, response: Response, case_id: int, action: str, params: dict) -> dict:
    with service_errors():
        row, replayed = engine.apply_action(
            db, case_id=case_id, action=action, params=params, actor="owner", label=_label(request),
            request_id=request.headers.get("x-request-id"))
        out = _action_out(row, replayed=replayed)
    db.commit()
    response.status_code = 200 if replayed else 201
    return out


@router.get("/reconciliation/cases/{case_id}", response_model=CaseDetailOut, dependencies=READ)
def get_case(case_id: int, db: Session = Depends(get_db)):
    with service_errors():
        out = ingest.get_case(db, case_id)
    db.commit()  # stale proposals superseded on read stay superseded
    return out


# --- inbox (spec 2026-10-21 §4) -------------------------------------------------------------------------------------

def _inbox_statuses(raw: str) -> tuple[str, ...]:
    statuses = tuple(dict.fromkeys(part.strip() for part in raw.split(",") if part.strip()))
    if not statuses or any(s not in INBOX_STATUSES for s in statuses):
        raise RequestValidationError([{"loc": ["query", "status"], "type": "value_error",
                                       "msg": f"status must be a comma list of {', '.join(INBOX_STATUSES)}"}])
    return statuses


@router.get("/reconciliation/inbox", response_model=InboxOut, dependencies=READ)
def get_inbox(status: str = "open,proposed", account_id: int | None = None,
              limit: int = Query(500, ge=1, le=1000), db: Session = Depends(get_db)):
    out = inbox_service.build_inbox(db, statuses=_inbox_statuses(status), account_id=account_id, limit=limit)
    db.commit()  # stale proposals superseded on read stay superseded
    return out


@router.get("/reconciliation/inbox/cases/{case_id}/search", response_model=list[InboxCandidate], dependencies=READ)
def search_inbox_candidates(case_id: int, days: int = Query(14, ge=1, le=60), db: Session = Depends(get_db)):
    with service_errors():
        return inbox_service.search_candidates(db, case_id, days=days)


@router.post("/reconciliation/inbox/apply", response_model=InboxApplyOut, dependencies=WRITE)
def apply_inbox(body: InboxApplyIn, request: Request, db: Session = Depends(get_db)):
    """Each member commits on its own (spec §5); only the structural checks fail the whole batch."""
    with service_errors():
        return inbox_service.apply_batch(db, body=body, label=_label(request) or "owner",
                                         request_id=request.headers.get("x-request-id"))


@router.post("/reconciliation/cases/{case_id}/actions", response_model=ActionOut, status_code=201,
             dependencies=WRITE)
def apply_action(case_id: int, body: ActionIn, request: Request, response: Response, db: Session = Depends(get_db)):
    return _apply(db, request, response, case_id, body.action, body.params)


@router.post("/reconciliation/cases/{case_id}/dismiss", response_model=ActionOut, status_code=201,
             dependencies=WRITE)
def dismiss_case(case_id: int, body: DismissIn, request: Request, response: Response,
                 db: Session = Depends(get_db)):
    return _apply(db, request, response, case_id, "dismiss", {"note": body.note})


@router.get("/reconciliation/actions", response_model=list[ActionOut], dependencies=READ)
def list_actions(statement_id: int | None = None, case_id: int | None = None,
                 limit: int = Query(200, ge=1, le=1000), db: Session = Depends(get_db)):
    rows = ingest.list_actions(db, statement_id=statement_id, case_id=case_id, limit=limit)
    return [_action_out(row) for row in rows]


@router.post("/reconciliation/actions/{action_id}/revert", response_model=ActionOut, status_code=201,
             dependencies=WRITE)
def revert_action(action_id: int, request: Request, db: Session = Depends(get_db)):
    with service_errors():
        row = engine.revert_action(db, action_id=action_id, actor="owner", label=_label(request),
                                   request_id=request.headers.get("x-request-id"))
        out = _action_out(row)
    db.commit()
    return out


@router.get("/reconciliation/audit", response_model=list[AuditOut], dependencies=READ)
def list_audit(statement_id: int | None = None, limit: int = Query(200, ge=1, le=1000),
               db: Session = Depends(get_db)):
    return ingest.list_audit(db, statement_id=statement_id, limit=limit)


# --- agent proposals (Task 9) ---------------------------------------------------------------------------------------

@router.post("/reconciliation/cases/{case_id}/proposals", response_model=ProposalOut, status_code=201,
             dependencies=PROPOSE)
def create_proposal(case_id: int, body: ProposalIn, request: Request, db: Session = Depends(get_db)):
    with service_errors():
        row = proposal_service.create_proposal(db, case_id=case_id, payload=body, author=_label(request) or "unknown")
        out = ProposalOut.model_validate(row)
    db.commit()
    return out


@router.get("/reconciliation/proposals", response_model=list[ProposalOut], dependencies=READ)
def list_proposals(status: Literal["pending", "applied", "rejected", "superseded"] | None = None,
                   case_id: int | None = None, limit: int = Query(200, ge=1, le=1000),
                   db: Session = Depends(get_db)):
    proposal_service.supersede_stale(db)
    db.commit()  # durable even if the rest of the request fails
    rows = proposal_service.list_proposals(db, status=status, case_id=case_id, limit=limit)
    out = [ProposalOut.model_validate(row) for row in rows]  # before the commit: no per-row reload afterwards
    db.commit()  # list_proposals supersedes again and takes SKIP LOCKED locks; persist what the response shows
    return out


@router.post("/reconciliation/proposals/{proposal_id}/apply", response_model=ActionOut, status_code=201,
             dependencies=WRITE)
def apply_proposal(proposal_id: int, request: Request, response: Response, db: Session = Depends(get_db)):
    proposal_service.supersede_stale(db)
    db.commit()  # a stale proposal reads back superseded even though the apply below is refused
    try:
        with service_errors():
            row, replayed = proposal_service.apply_proposal(
                db, proposal_id=proposal_id, label=_label(request) or "unknown",
                request_id=request.headers.get("x-request-id"))
            out = _action_out(row, replayed=replayed)
    except HTTPException as exc:
        db.rollback()
        proposal_service.supersede_stale(db)  # the engine saw a newer case version under its locks
        db.commit()
        raise exc
    db.commit()
    response.status_code = 200 if replayed else 201
    return out


@router.post("/reconciliation/proposals/{proposal_id}/reject", response_model=ProposalOut, dependencies=WRITE)
def reject_proposal(proposal_id: int, request: Request, body: ProposalRejectIn | None = None,
                    db: Session = Depends(get_db)):
    with service_errors():
        row = proposal_service.reject_proposal(
            db, proposal_id=proposal_id, label=_label(request) or "unknown", note=body.note if body else None,
            request_id=request.headers.get("x-request-id"))
        out = ProposalOut.model_validate(row)
    db.commit()
    return out
