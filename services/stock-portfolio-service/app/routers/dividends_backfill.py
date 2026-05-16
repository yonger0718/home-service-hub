"""POST /api/portfolio/dividends/backfill — one-shot historical pull."""
from __future__ import annotations

import logging
from datetime import date as dt_date
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models.portfolio import Transaction
from ..services import dividend_auto_record_service, dividend_history_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/portfolio", tags=["Portfolio"])


class BackfillResult(BaseModel):
    symbols_scanned: int
    events_seen: int
    cash_inserted: int
    stock_inserted: int
    skipped_no_holding: int


def _symbols_with_first_trade(db: Session) -> list[tuple[str, dt_date, Optional[str]]]:
    """Every symbol that has at least one transaction, plus its first trade_date + latest name.

    Backfill must cover symbols the user has since fully sold — they
    received dividends while they held the shares. ``name`` is the value
    on the most recent transaction (by ``trade_date``), not the lexical max.
    """
    aggregates = (
        select(
            Transaction.symbol.label("symbol"),
            func.min(Transaction.trade_date).label("first_dt"),
            func.max(Transaction.trade_date).label("last_dt"),
        )
        .group_by(Transaction.symbol)
        .subquery()
    )
    rows = db.execute(
        select(aggregates.c.symbol, aggregates.c.first_dt, Transaction.name)
        .select_from(aggregates)
        .outerjoin(
            Transaction,
            (Transaction.symbol == aggregates.c.symbol)
            & (Transaction.trade_date == aggregates.c.last_dt),
        )
    ).all()
    out: list[tuple[str, dt_date, Optional[str]]] = []
    seen: set[str] = set()
    for symbol, first_ts, name in rows:
        if first_ts is None or symbol in seen:
            continue
        seen.add(symbol)
        first = first_ts.date() if hasattr(first_ts, "date") else first_ts
        out.append((symbol, first, name))
    return out


@router.post("/dividends/backfill", response_model=BackfillResult)
def backfill_dividends(db: Session = Depends(get_db)) -> BackfillResult:
    symbols = _symbols_with_first_trade(db)
    symbols_scanned = 0
    events_seen = 0
    cash_inserted = 0
    stock_inserted = 0
    skipped_no_holding = 0

    for symbol, first, name in symbols:
        symbols_scanned += 1
        try:
            events = dividend_history_service.fetch_for_symbol_all_years(symbol, first)
        except Exception as exc:  # noqa: BLE001 — per-symbol failure must not abort run
            logger.exception(
                "dividends.backfill.symbol_failed",
                extra={"symbol": symbol, "error": str(exc)},
            )
            continue
        for event in events:
            events_seen += 1
            try:
                result = dividend_auto_record_service.auto_record_for_event(db, event, name=name)
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                logger.exception(
                    "dividends.backfill.event_failed",
                    extra={"symbol": symbol, "ex_date": event.ex_date.isoformat(), "error": str(exc)},
                )
                continue
            if result.cash_inserted:
                cash_inserted += 1
            if result.stock_inserted:
                stock_inserted += 1
            if result.skipped_reason == "no_holding":
                skipped_no_holding += 1
        try:
            db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.exception(
                "dividends.backfill.commit_failed",
                extra={"symbol": symbol, "error": str(exc)},
            )
            continue

    return BackfillResult(
        symbols_scanned=symbols_scanned,
        events_seen=events_seen,
        cash_inserted=cash_inserted,
        stock_inserted=stock_inserted,
        skipped_no_holding=skipped_no_holding,
    )
