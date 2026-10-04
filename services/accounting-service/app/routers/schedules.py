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
from ..services import schedule_job, schedule_locks, schedule_read, schedule_service
from ..services.errors import ConflictError
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


@router.post("/definitions/{definition_id}/pause", response_model=DefinitionOut)
def pause_definition(definition_id: int, db: Session = Depends(get_db)):
    with service_errors():
        schedule_service.pause(db, definition_id)
    db.commit()
    return _definition_out(db, definition_id)


@router.post("/definitions/{definition_id}/resume", response_model=DefinitionOut)
def resume_definition(definition_id: int, payload: ResumeIn | None = None, db: Session = Depends(get_db)):
    with service_errors():
        to_post = schedule_service.resume(db, definition_id, payload.backlog if payload else "skip")
    db.commit()
    if to_post:
        schedule_service.post_sequence(db, to_post)
    return _definition_out(db, definition_id)


@router.post("/definitions/{definition_id}/end", response_model=DefinitionOut)
def end_definition(definition_id: int, db: Session = Depends(get_db)):
    with service_errors():
        schedule_service.end(db, definition_id)
    db.commit()
    return _definition_out(db, definition_id)


@router.put("/definitions/{definition_id}/mode", response_model=DefinitionOut)
def put_mode(definition_id: int, payload: ModeIn, db: Session = Depends(get_db)):
    with service_errors():
        schedule_service.set_mode(db, definition_id, payload.posting_mode)
    db.commit()
    return _definition_out(db, definition_id)


@router.post("/definitions/{definition_id}/catch-up", response_model=CatchUpOut)
def catch_up(definition_id: int, db: Session = Depends(get_db)):
    with service_errors():
        instance_ids = schedule_service.catch_up_ids(db, definition_id)
    db.commit()
    posted, failed = schedule_service.post_sequence(db, instance_ids)
    return {"posted": posted, "failed": failed, "definition": _definition_out(db, definition_id)}


@router.get("/instances", response_model=list[InstanceOut])
def list_instances(
    date_from: date | None = Query(default=None, alias="from"),
    until: date | None = None,
    status: Literal["pending", "posted", "skipped", "all"] = "pending",
    definition_id: int | None = None,
    queue: bool = False,
    db: Session = Depends(get_db),
):
    return schedule_read.list_instances(
        db, date_from=date_from, until=until, status=None if status == "all" else status,
        definition_id=definition_id, queue=queue,
    )


@router.put("/instances/{instance_id}", response_model=InstanceOut)
def put_instance(instance_id: int, payload: InstanceUpdateIn, db: Session = Depends(get_db)):
    with service_errors():
        schedule_service.update_instance(db, instance_id, payload)
    db.commit()
    return _instance_out(db, instance_id)


@router.post("/instances/{instance_id}/post", response_model=InstanceOut)
def post_instance(instance_id: int, db: Session = Depends(get_db)):
    with service_errors():
        schedule_service.post_one(db, instance_id)
    db.commit()
    return _instance_out(db, instance_id)


@router.post("/instances/{instance_id}/skip", response_model=InstanceOut)
def skip_instance(instance_id: int, db: Session = Depends(get_db)):
    with service_errors():
        schedule_service.skip_instance(db, instance_id)
    db.commit()
    return _instance_out(db, instance_id)


@router.post("/instances/{instance_id}/reopen", response_model=InstanceOut)
def reopen_instance(instance_id: int, db: Session = Depends(get_db)):
    with service_errors():
        schedule_service.reopen_instance(db, instance_id)
    db.commit()
    return _instance_out(db, instance_id)


@router.post("/instances/{instance_id}/repost", response_model=InstanceOut)
def repost_instance(instance_id: int, payload: RepostIn, db: Session = Depends(get_db)):
    with service_errors():
        schedule_service.repost_instance(db, instance_id, payload.amounts)
    db.commit()
    return _instance_out(db, instance_id)


@router.post("/instances/{instance_id}/accept-partial", response_model=InstanceOut)
def accept_partial(instance_id: int, db: Session = Depends(get_db)):
    with service_errors():
        schedule_service.accept_partial(db, instance_id)
    db.commit()
    return _instance_out(db, instance_id)


@router.post("/run-now", response_model=RunReportOut)
def run_now(engine: Engine = Depends(get_engine)):
    """立即執行: the daily job, now (D40, D42: same perimeter as every endpoint; the advisory locks make a repeat safe).
    409 import_running while an import holds the key, 409 busy while another run holds the job lock."""
    with service_errors():
        if not schedule_locks.import_key_free(engine):
            raise schedule_locks.ImportRunningError()
        report = schedule_job.run(engine, "manual")
        if report["status"] == "busy":
            raise ConflictError("busy")
    return report
