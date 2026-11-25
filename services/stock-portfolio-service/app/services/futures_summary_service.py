"""永豐期貨 card data (spec §9): the series value, the latest daily margins and the month's P&L."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.sinopac_futures_statement import FuturesStatementDay, FuturesStatementMonth
from . import sinopac_futures_series as series


def _amount(value: Decimal) -> str:
    return f"{Decimal(value):.4f}"


def _month_block(db: Session, as_of: date, value: series.FuturesValue) -> dict:
    first = as_of.replace(day=1)
    days = list(db.scalars(
        select(FuturesStatementDay)
        .where(FuturesStatementDay.statement_date >= first, FuturesStatementDay.statement_date <= as_of)
        .order_by(FuturesStatementDay.statement_date)
    ))
    month_row = db.get(FuturesStatementMonth, first)
    if month_row is not None and month_row.month_end > as_of:
        month_row = None  # not yet a source at as_of
    baseline = series.equity_at(db, first - timedelta(days=1))

    if month_row is not None:
        deposits, realized = month_row.b, month_row.k
    else:
        deposits = sum((d.deposits_net for d in days), Decimal(0))
        realized = sum((d.futures_realized for d in days), Decimal(0))

    pnl: str | None = None
    complete, reason = True, None
    if baseline is None:
        complete, reason = False, "no_baseline"
    else:
        pnl = _amount(value.total_equity - baseline.total_equity - deposits)
        if month_row is None and (any(d.findings for d in days) or _month_findings(db, first)):
            complete, reason = False, "findings"
    return {
        "month": first.strftime("%Y-%m"), "pnl": pnl, "pnl_complete": complete, "pnl_reason": reason,
        "futures_realized": _amount(realized),
    }


def _month_findings(db: Session, first: date) -> bool:
    row = db.get(FuturesStatementMonth, first)
    return bool(row is not None and row.findings)


def summary(db: Session, as_of: date) -> dict:
    value = series.equity_at(db, as_of)
    if value is None:
        return {"as_of": as_of.isoformat(), "value": None, "latest_daily": None, "month": None}
    latest = db.scalars(
        select(FuturesStatementDay)
        .where(FuturesStatementDay.statement_date <= as_of)
        .order_by(FuturesStatementDay.statement_date.desc())
        .limit(1)
    ).one_or_none()
    return {
        "as_of": as_of.isoformat(),
        "value": {
            "total_equity": _amount(value.total_equity), "balance": _amount(value.balance),
            "source_date": value.source_date.isoformat(), "source_kind": value.source_kind,
            "positions_open": value.positions_open, "frozen": value.frozen(as_of),
        },
        "latest_daily": None if latest is None else {
            "statement_date": latest.statement_date.isoformat(),
            **{k: _amount(getattr(latest, k)) for k in
               ("withdrawable", "initial_margin", "maintenance_margin", "excess_margin", "margin_call")},
        },
        "month": _month_block(db, as_of, value),
    }
