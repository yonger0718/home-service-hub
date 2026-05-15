"""CSV import endpoints for transactions + dividends.

POST a multipart ``file`` (the CSV) with optional ``?dry_run=true`` to
preview without writing. The response is the structured ``ImportResult``
plus the parsed rows (so the UI can render a preview table).
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from ..database import get_db
from ..services import import_service

router = APIRouter(prefix="/api/portfolio/imports", tags=["Portfolio Imports"])

_MAX_CSV_BYTES = 5 * 1024 * 1024  # 5 MiB — manual upload, not bulk pipeline


def _read_upload(file: UploadFile) -> bytes:
    raw = file.file.read(_MAX_CSV_BYTES + 1)
    if len(raw) > _MAX_CSV_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"CSV exceeds {_MAX_CSV_BYTES} bytes",
        )
    return raw


def _serialize_parse(parsed: import_service.ParseResult) -> list[dict]:
    return [
        {
            "row_index": row.row_index,
            "fingerprint": row.fingerprint,
            "payload": {
                key: (value.isoformat() if hasattr(value, "isoformat") else str(value))
                if value is not None
                else None
                for key, value in row.payload.items()
            },
        }
        for row in parsed.rows
    ]


def _serialize_result(
    parsed: import_service.ParseResult, result: import_service.ImportResult
) -> dict:
    return {
        "parsed": result.parsed,
        "created": result.created,
        "skipped_duplicates": result.skipped_duplicates,
        "dry_run": result.dry_run,
        "errors": [asdict(error) for error in result.errors],
        "created_ids": result.created_ids,
        "rows": _serialize_parse(parsed),
    }


@router.post("/transactions")
def import_transactions(
    file: UploadFile = File(...),
    dry_run: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> dict:
    raw = _read_upload(file)
    try:
        parsed = import_service.parse_transactions_csv(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result = import_service.commit_transactions(db, parsed, dry_run=dry_run)
    return _serialize_result(parsed, result)


@router.post("/dividends")
def import_dividends(
    file: UploadFile = File(...),
    dry_run: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> dict:
    raw = _read_upload(file)
    try:
        parsed = import_service.parse_dividends_csv(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result = import_service.commit_dividends(db, parsed, dry_run=dry_run)
    return _serialize_result(parsed, result)
