"""永豐期貨 equity series (spec §6.3) and the futures advisory lock (§6.2).

Every row in ``futures_statement_day`` / ``futures_statement_month`` is an accepted source. A daily row is dated by
``statement_date``, a monthly row by ``month_end``; on the same date the monthly row wins. A source's value applies
from its own date up to, not including, the next source's date.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..models.sinopac_futures_statement import FuturesStatementDay, FuturesStatementMonth

FUTURES_LOCK_KEY = 0x46555455 << 32  # "FUTU"


def futures_lock(db: Session, *, shared: bool = False) -> None:
    """Transaction-scoped futures advisory lock; a no-op off Postgres (SQLite tests are single-writer)."""
    if db.get_bind().dialect.name != "postgresql":
        return
    fn = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
    db.execute(text(f"SELECT {fn}(:k)"), {"k": FUTURES_LOCK_KEY})


@dataclass(frozen=True)
class FuturesValue:
    total_equity: Decimal
    balance: Decimal
    source_date: date
    source_kind: Literal["daily", "monthly"]
    positions_open: bool

    def frozen(self, d: date) -> bool:
        return self.positions_open and self.source_date < d


def _day_value(row: FuturesStatementDay) -> FuturesValue:
    return FuturesValue(
        row.total_equity, row.balance, row.statement_date, "daily", (row.open_positions or 0) > 0
    )


def _month_value(row: FuturesStatementMonth) -> FuturesValue:
    return FuturesValue(
        row.total_equity, row.m, row.month_end, "monthly", (row.open_positions or 0) > 0
    )


def _sources_until(db: Session, end: date) -> list[FuturesValue]:
    """All sources dated on or before ``end``, ascending; a monthly row sorts after a daily one on a tie."""
    days = db.scalars(select(FuturesStatementDay).where(FuturesStatementDay.statement_date <= end))
    months = db.scalars(select(FuturesStatementMonth).where(FuturesStatementMonth.month_end <= end))
    values = [_day_value(r) for r in days] + [_month_value(r) for r in months]
    values.sort(key=lambda v: (v.source_date, v.source_kind == "monthly"))
    return values


def equity_at(db: Session, d: date) -> FuturesValue | None:
    values = _sources_until(db, d)
    return values[-1] if values else None


def equity_by_date(db: Session, start: date, end: date) -> dict[date, FuturesValue | None]:
    """Every calendar day in [start, end]: one query per table (sources on or before ``end``)."""
    values = _sources_until(db, end)
    keys = [v.source_date for v in values]
    out: dict[date, FuturesValue | None] = {}
    d = start
    while d <= end:
        i = bisect_right(keys, d)
        out[d] = values[i - 1] if i else None
        d += timedelta(days=1)
    return out


def source_dates(db: Session) -> set[date]:
    days = db.scalars(select(FuturesStatementDay.statement_date)).all()
    months = db.scalars(select(FuturesStatementMonth.month_end)).all()
    return set(days) | set(months)
