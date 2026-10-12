"""Reconciliation case detail, owner actions, revert, action and audit listings (R1c, spec §4.9).

Behind `require_feature()` like the statements router. Owner actions need `write`; reads need `read`. The engine
never commits and these handlers do; policy does not run after owner actions.
"""

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from ..auth import require, require_feature
from ..database import get_db
from ..schemas.reconciliation import ActionIn, ActionOut, AuditOut, DismissIn
from ..schemas.statements import CaseDetailOut
from ..services import statement_ingest_service as ingest
from ..services.reconciliation_actions import engine
from .errors import service_errors
from .statements import READ, WRITE, _label

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
        return ingest.get_case(db, case_id)


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
