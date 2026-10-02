import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ..database import get_db, get_engine
from ..models import ImportRun
from ..schemas.imports import ImportReport
from ..services.moze_csv import MozeImportError
from ..services.moze_import_service import (
    ImportAlreadyRunningError,
    ImportLockedError,
    run_import,
    run_report,
)

router = APIRouter(prefix="/imports", tags=["Imports"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _parse_renames(raw: str) -> dict[str, str]:
    if not raw.strip():
        return {}
    try:
        renames = json.loads(raw)
    except json.JSONDecodeError:
        renames = None
    if not isinstance(renames, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in renames.items()
    ):
        raise HTTPException(status_code=400, detail='renames must be a JSON object {"old name": "new name"}')
    return renames


@router.post("/moze", response_model=ImportReport)
def import_moze(
    file: UploadFile = File(...),
    renames: str = Form(default=""),
    dry_run: bool = Query(default=False),
    engine: Engine = Depends(get_engine),
):
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file exceeds 20 MB")
    rename_map = _parse_renames(renames)
    try:
        return run_import(engine, data, file.filename or "upload.csv", dry_run=dry_run, renames=rename_map)
    except ImportLockedError as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
    except ImportAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MozeImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/latest", response_model=ImportReport)
def latest_import(db: Session = Depends(get_db)):
    run = db.scalar(select(ImportRun).order_by(ImportRun.started_at.desc(), ImportRun.id.desc()).limit(1))
    if run is None:
        raise HTTPException(status_code=404, detail="no import has run yet")
    return run_report(run)
