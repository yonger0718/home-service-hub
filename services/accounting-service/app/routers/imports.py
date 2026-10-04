import json
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ..database import get_db, get_engine
from ..models import ImportRun
from ..schemas.imports import ImportReport
from ..services.moze_backup_import_service import run_backup_import
from ..services.moze_csv import MozeImportError
from ..services.moze_import_service import (
    ImportAlreadyRunningError,
    ImportLockedError,
    run_import,
    run_report,
)

router = APIRouter(prefix="/imports", tags=["Imports"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_BACKUP_UPLOAD_BYTES = 200 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024


def get_backup_exporter() -> list[str] | None:
    """Converter command for backup uploads; None means MOZE_REALM_EXPORTER or the repo tool (tests override)."""
    return None


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


def _stored_name(filename: str | None, default: str) -> str:
    """The upload's base name, cut to import_run.file_name's 255 characters."""
    return (Path(filename or default).name or default)[:255]


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
        return run_import(engine, data, _stored_name(file.filename, "upload.csv"), dry_run=dry_run, renames=rename_map)
    except ImportLockedError as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
    except ImportAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MozeImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/moze-backup", response_model=ImportReport)
def import_moze_backup(
    file: UploadFile = File(...),
    renames: str = Form(default=""),
    dry_run: bool = Query(default=False),
    strict: bool = Query(default=True),
    allow_fx_outliers: bool = Query(default=False),
    engine: Engine = Depends(get_engine),
    exporter: list[str] | None = Depends(get_backup_exporter),
):
    rename_map = _parse_renames(renames)
    file_name = _stored_name(file.filename, "backup.zip")
    with tempfile.TemporaryDirectory(prefix="moze-upload-") as work:
        zip_path = Path(work) / "upload.zip"
        size = 0
        with zip_path.open("wb") as target:
            while chunk := file.file.read(UPLOAD_CHUNK_BYTES):
                size += len(chunk)
                if size > MAX_BACKUP_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="file exceeds 200 MB")
                target.write(chunk)
        try:
            return run_backup_import(
                engine, zip_path, file_name, dry_run=dry_run, renames=rename_map, strict=strict,
                allow_fx_outliers=allow_fx_outliers, exporter=exporter,
            )
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
