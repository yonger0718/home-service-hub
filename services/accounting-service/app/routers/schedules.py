"""/schedules endpoints (design D40). In-service paths: Caddy and the dev proxy strip /api/accounting.

Writes commit after the service call; service_errors maps ValidationError → 422 naming the field, EditLockedError →
409 locked_until_cutover, ConflictError (ImportRunningError included) → 409, NotFoundError → 404.
"""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ..database import get_db, get_engine
from ..models import ScheduleDefinition, ScheduleInstance
from ..schemas.schedules import (
    CatchUpOut,
    DefinitionDetailOut,
    DefinitionIn,
    DefinitionKind,
    DefinitionOut,
    DefinitionStatus,
    DefinitionUpdateIn,
    InstanceOut,
    InstanceUpdateIn,
    ModeIn,
    RepostIn,
    ResumeIn,
    RunReportOut,
)
from ..services import schedule_read, schedule_service
from .errors import service_errors

router = APIRouter(prefix="/schedules", tags=["Schedules"])


def _definition_out(db: Session, definition_id: int) -> dict:
    db.expire_all()
    definition = db.get(ScheduleDefinition, definition_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="schedule definition not found")
    return schedule_read.definition_out(db, definition)


def _instance_out(db: Session, instance_id: int) -> dict:
    db.expire_all()
    instance = db.get(ScheduleInstance, instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="schedule instance not found")
    return schedule_read.instance_out(db, instance)


@router.get("/definitions", response_model=list[DefinitionOut])
def list_definitions(
    status: DefinitionStatus | None = None, kind: DefinitionKind | None = None, db: Session = Depends(get_db)
):
    return schedule_read.list_definitions(db, status=status, kind=kind)


@router.get("/definitions/{definition_id}", response_model=DefinitionDetailOut)
def get_definition(definition_id: int, db: Session = Depends(get_db)):
    with service_errors():
        return schedule_read.get_definition(db, definition_id)


@router.post("/definitions", response_model=DefinitionOut, status_code=201)
def post_definition(payload: DefinitionIn, db: Session = Depends(get_db)):
    with service_errors():
        definition_id = schedule_service.create_definition(db, payload)
    db.commit()
    return _definition_out(db, definition_id)


@router.put("/definitions/{definition_id}", response_model=DefinitionOut)
def put_definition(definition_id: int, payload: DefinitionUpdateIn, db: Session = Depends(get_db)):
    with service_errors():
        schedule_service.update_definition(db, definition_id, payload)
    db.commit()
    return _definition_out(db, definition_id)


@router.delete("/definitions/{definition_id}", status_code=204)
def delete_definition(definition_id: int, db: Session = Depends(get_db)):
    with service_errors():
        schedule_service.delete_definition(db, definition_id)
    db.commit()
    return Response(status_code=204)
