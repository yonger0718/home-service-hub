"""Persist daily ``PortfolioSummary`` totals into ``portfolio_snapshot``.

One row per TW calendar date keyed by ``date`` primary key. Idempotent on
the same calendar day via ``Session.merge``.
"""

from __future__ import annotations

from datetime import date as dt_date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..models.portfolio_snapshot import PortfolioSnapshot
from . import portfolio_service

_TW_OFFSET = timezone(timedelta(hours=8))


def _today_tw() -> dt_date:
    return datetime.now(_TW_OFFSET).date()


def write_today_snapshot(
    db: Session, *, today: Optional[dt_date] = None
) -> PortfolioSnapshot:
    """Build the live summary and upsert a row for ``today`` (TW calendar)."""
    target = today or _today_tw()
    summary = portfolio_service.get_portfolio_summary(db)
    row = PortfolioSnapshot(
        date=target,
        total_market_value=summary.total_market_value,
        total_cost=summary.total_cost,
        total_unrealized_pnl=summary.total_unrealized_pnl,
        total_dividends=summary.total_dividends,
        portfolio_xirr=summary.portfolio_xirr,
    )
    merged = db.merge(row)
    db.commit()
    db.refresh(merged)
    return merged


def list_snapshots(
    db: Session, *, from_date: dt_date, to_date: dt_date
) -> list[PortfolioSnapshot]:
    return (
        db.query(PortfolioSnapshot)
        .filter(
            PortfolioSnapshot.date >= from_date,
            PortfolioSnapshot.date <= to_date,
        )
        .order_by(PortfolioSnapshot.date.asc())
        .all()
    )
