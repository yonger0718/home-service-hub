"""CSV import endpoints for transactions + dividends.

POST a multipart ``file`` (the CSV) with optional ``?dry_run=true`` to
preview without writing. 國泰 broker CSVs (detected by preamble row) are
auto-routed to the rehash-capable parser — same upload UX, no extra
parameter. The response is the structured ``ImportResult`` plus the
parsed rows (so the UI can render a preview table).

After a successful (non-dry-run) commit with ``created > 0`` the service
schedules ``post_import_orchestrator.run_chain`` in a ``BackgroundTasks``
slot so derived state (symbol_map, dividend events, networth snapshots)
refreshes without blocking the HTTP response. The chain can also be
triggered manually via ``POST /api/portfolio/imports/recalc``.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date as dt_date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..models import portfolio as portfolio_models
from ..services import broker_cathay_service, import_service, per_date_verify, post_import_orchestrator

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
        "rehashed": result.rehashed,
        "skipped_unresolved": result.skipped_unresolved,
        "skipped_unverified": result.skipped_unverified,
        "unresolved_names": [asdict(name) for name in result.unresolved_names],
        "override_validations": [
            asdict(validation) for validation in result.override_validations
        ],
        "would_rehash": result.would_rehash,
        "would_insert": result.would_insert,
        "would_skip_duplicate": result.would_skip_duplicate,
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
    name_overrides: str = Form(default=""),
    confirmed_overrides: str = Form(default=""),
    dry_run: bool = Query(default=False),
    has_header: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> dict:
    raw = _read_upload(file)
    csv_format = import_service.detect_csv_format(raw)
    try:
        overrides_dict = json.loads(name_overrides) if name_overrides.strip() else None
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail="name_overrides must be a JSON object",
        ) from exc
    if overrides_dict is not None and not isinstance(overrides_dict, dict):
        raise HTTPException(
            status_code=400,
            detail="name_overrides must be a JSON object",
        )
    try:
        confirmed_list = (
            json.loads(confirmed_overrides) if confirmed_overrides.strip() else []
        )
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail="confirmed_overrides must be a JSON array of strings",
        ) from exc
    if not isinstance(confirmed_list, list) or not all(
        isinstance(name, str) for name in confirmed_list
    ):
        raise HTTPException(
            status_code=400,
            detail="confirmed_overrides must be a JSON array of strings",
        )
    confirmed_set = set(confirmed_list)
    try:
        if csv_format == "cathay":
            parsed = broker_cathay_service.parse_cathay_rows(
                raw,
                name_overrides=overrides_dict,
            )
            result = broker_cathay_service.parse_cathay_transactions_csv(
                raw,
                dry_run=dry_run,
                db=db,
                name_overrides=overrides_dict,
                confirmed_overrides=confirmed_set,
            )
        else:
            parsed = import_service.parse_transactions_csv(raw, has_header=has_header)
            result = import_service.commit_transactions(db, parsed, dry_run=dry_run)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    has_header: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> dict:
    raw = _read_upload(file)
    try:
        parsed = import_service.parse_dividends_csv(raw, has_header=has_header)
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


@router.post("/verify-symbol")
def verify_symbol(body: dict = Body(...)) -> dict:
    name = (body.get("name") or "").strip()
    code = (body.get("code") or "").strip()
    raw_date = (body.get("trade_date") or "").strip()
    if not name or not code or not raw_date:
        raise HTTPException(
            status_code=400,
            detail="name, code, and trade_date are required",
        )
    try:
        trade_date = dt_date.fromisoformat(raw_date)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="trade_date must be YYYY-MM-DD",
        ) from exc
    validation = per_date_verify.verify_single(name, code, trade_date)
    return asdict(validation)


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


@router.post("/refresh-quotes", status_code=202, response_model=None)
def refresh_quotes(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict | Response:
    today = post_import_orchestrator.today_tw()
    today_utc_eod = datetime.combine(
        today,
        datetime.max.time(),
        tzinfo=timezone(timedelta(hours=8)),
    ).astimezone(timezone.utc)
    signed_qty = case(
        (
            portfolio_models.Transaction.type
            == portfolio_models.TransactionType.BUY,
            portfolio_models.Transaction.quantity,
        ),
        else_=-portfolio_models.Transaction.quantity,
    )
    net_qty = func.sum(signed_qty)
    touched = {
        symbol
        for symbol, _qty in (
            db.query(
                portfolio_models.Transaction.symbol,
                net_qty.label("qty"),
            )
            .filter(portfolio_models.Transaction.trade_date <= today_utc_eod)
            .group_by(portfolio_models.Transaction.symbol)
            .having(net_qty > 0)
            .all()
        )
    }
    if not touched:
        return Response(status_code=204)

    if not post_import_orchestrator._RECALC_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="recalc in progress")
    # The background task re-acquires the lock; this short gap avoids queueing behind a long recalc.
    post_import_orchestrator._RECALC_LOCK.release()

    background_tasks.add_task(
        post_import_orchestrator.schedule_quotes_refresh_sync,
        SessionLocal,
        touched_symbols=touched,
    )
    return {
        "refresh_scheduled": True,
        "date": today.isoformat(),
        "touched_symbols": sorted(touched),
    }


@router.get("/recalc/status")
def recalc_status() -> dict:
    return post_import_orchestrator.latest_status()
