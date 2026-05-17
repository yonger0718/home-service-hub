"""CSV import endpoints for transactions + dividends.

POST a multipart ``file`` (the CSV) with optional ``?dry_run=true`` to
preview without writing. The response is the structured ``ImportResult``
plus the parsed rows (so the UI can render a preview table).

After a successful (non-dry-run) commit with ``created > 0`` the service
schedules ``post_import_orchestrator.run_chain`` in a ``BackgroundTasks``
slot so derived state (symbol_map, dividend events, networth snapshots)
refreshes without blocking the HTTP response. The chain can also be
triggered manually via ``POST /api/portfolio/imports/recalc``.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date as dt_date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..models import portfolio as portfolio_models
from ..services import import_service, post_import_orchestrator

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
    parsed: import_service.ParseResult,
    result: import_service.ImportResult,
    *,
    recalc_scheduled: bool = False,
) -> dict:
    return {
        "parsed": result.parsed,
        "created": result.created,
        "skipped_duplicates": result.skipped_duplicates,
        "dry_run": result.dry_run,
        "errors": [asdict(error) for error in result.errors],
        "created_ids": result.created_ids,
        "rows": _serialize_parse(parsed),
        "recalc_scheduled": recalc_scheduled,
    }


def _touched_symbols_and_min_trade_date(
    db: Session, created_ids: list[int]
) -> tuple[set[str], Optional[dt_date]]:
    if not created_ids:
        return set(), None
    rows = (
        db.query(portfolio_models.Transaction.symbol, portfolio_models.Transaction.trade_date)
        .filter(portfolio_models.Transaction.id.in_(created_ids))
        .all()
    )
    if not rows:
        return set(), None
    symbols = {symbol for symbol, _ in rows}
    min_trade = min(trade_date for _, trade_date in rows)
    return symbols, min_trade.date() if isinstance(min_trade, datetime) else min_trade


def _touched_symbols_and_min_ex_date(
    db: Session, created_ids: list[int]
) -> tuple[set[str], Optional[dt_date]]:
    if not created_ids:
        return set(), None
    rows = (
        db.query(portfolio_models.Dividend.symbol, portfolio_models.Dividend.ex_dividend_date)
        .filter(portfolio_models.Dividend.id.in_(created_ids))
        .all()
    )
    if not rows:
        return set(), None
    symbols = {symbol for symbol, _ in rows}
    min_ex = min(ex_date for _, ex_date in rows)
    return symbols, min_ex.date() if isinstance(min_ex, datetime) else min_ex


def _maybe_schedule_chain(
    background_tasks: BackgroundTasks,
    *,
    touched_symbols: set[str],
    recalc_from: Optional[dt_date],
) -> bool:
    if recalc_from is None or not touched_symbols:
        return False
    if not post_import_orchestrator.is_enabled():
        return False
    recalc_to = post_import_orchestrator.today_tw()
    if recalc_from > recalc_to:
        recalc_from = recalc_to
    background_tasks.add_task(
        post_import_orchestrator.schedule_chain_sync,
        SessionLocal,
        recalc_from=recalc_from,
        recalc_to=recalc_to,
        touched_symbols=touched_symbols,
    )
    return True


@router.post("/transactions")
def import_transactions(
    background_tasks: BackgroundTasks,
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
    recalc_scheduled = False
    if not result.dry_run and result.created > 0:
        symbols, min_trade = _touched_symbols_and_min_trade_date(db, result.created_ids)
        recalc_scheduled = _maybe_schedule_chain(
            background_tasks, touched_symbols=symbols, recalc_from=min_trade
        )
    return _serialize_result(parsed, result, recalc_scheduled=recalc_scheduled)


@router.post("/dividends")
def import_dividends(
    background_tasks: BackgroundTasks,
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
    recalc_scheduled = False
    if not result.dry_run and result.created > 0:
        symbols, min_ex = _touched_symbols_and_min_ex_date(db, result.created_ids)
        recalc_scheduled = _maybe_schedule_chain(
            background_tasks, touched_symbols=symbols, recalc_from=min_ex
        )
    return _serialize_result(parsed, result, recalc_scheduled=recalc_scheduled)


@router.post("/recalc")
def trigger_recalc(
    background_tasks: BackgroundTasks,
    body: dict = Body(default_factory=dict),
    db: Session = Depends(get_db),
) -> dict:
    raw_start = body.get("start_date")
    raw_end = body.get("end_date")
    try:
        start_date = dt_date.fromisoformat(raw_start) if raw_start else None
        end_date = dt_date.fromisoformat(raw_end) if raw_end else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid date: {exc}") from exc

    if start_date is None:
        min_trade = db.query(func.min(portfolio_models.Transaction.trade_date)).scalar()
        if min_trade is None:
            raise HTTPException(status_code=409, detail="no transactions to recalc")
        start_date = (
            min_trade.date() if isinstance(min_trade, datetime) else min_trade
        )
    if end_date is None:
        end_date = post_import_orchestrator.today_tw()

    if start_date > end_date:
        raise HTTPException(
            status_code=400, detail="start_date must be <= end_date"
        )

    # Touched symbols = all distinct symbols ever traded (manual recalc rebuilds the whole series)
    touched = {
        sym
        for (sym,) in db.query(portfolio_models.Transaction.symbol).distinct().all()
    }
    if not touched:
        raise HTTPException(status_code=409, detail="no transactions to recalc")

    background_tasks.add_task(
        post_import_orchestrator.schedule_chain_sync,
        SessionLocal,
        recalc_from=start_date,
        recalc_to=end_date,
        touched_symbols=touched,
    )
    return {
        "recalc_scheduled": True,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "touched_symbols": sorted(touched),
    }


@router.get("/recalc/status")
def recalc_status() -> dict:
    return post_import_orchestrator.latest_status()
