"""Persist daily ``PortfolioSummary`` totals into ``portfolio_snapshot``.

One row per TW calendar date keyed by ``date`` primary key. Idempotent on
the same calendar day via ``Session.merge``.
"""

from __future__ import annotations

import logging
from datetime import date as dt_date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..models.portfolio_snapshot import PortfolioSnapshot
from . import cash_account_service, portfolio_service

logger = logging.getLogger(__name__)

_TW_OFFSET = timezone(timedelta(hours=8))


def _today_tw() -> dt_date:
    return datetime.now(_TW_OFFSET).date()


def write_today_snapshot(
    db: Session, *, today: Optional[dt_date] = None
) -> PortfolioSnapshot:
    """Build the live summary and upsert a row for ``today`` (TW calendar)."""
    target = today or _today_tw()
    summary = portfolio_service.get_portfolio_summary(db)
    cash_total_twd, skipped = cash_account_service.get_total_balance_in(
        db, "TWD", asof=target
    )
    if skipped:
        logger.warning("snapshot total_cash_twd skipped currencies: %s", skipped)
    row = PortfolioSnapshot(
        date=target,
        total_market_value=summary.total_market_value,
        total_cost=summary.total_cost,
        total_unrealized_pnl=summary.total_unrealized_pnl,
        total_dividends=summary.total_dividends,
        total_realized_pnl=summary.total_realized_pnl,
        total_cash_twd=cash_total_twd,
        portfolio_xirr=summary.portfolio_xirr,
    )
    merged = db.merge(row)
    db.commit()
    db.refresh(merged)
    return merged


def _is_cash_only_row(row: PortfolioSnapshot) -> bool:
    """Match the shape inserted by ``refresh_snapshot_cash_range`` — all stock
    columns zero and no XIRR. Used to safely prune helper-created rows when
    a later refresh drops cash back to zero."""
    return (
        row.total_market_value == 0
        and row.total_cost == 0
        and row.total_unrealized_pnl == 0
        and row.total_dividends == 0
        and row.total_realized_pnl == 0
        and row.portfolio_xirr is None
    )


def refresh_snapshot_cash_range(
    db: Session,
    start_date: dt_date,
    end_date: dt_date,
) -> None:
    """Refresh only ``total_cash_twd`` across an inclusive date range."""
    if end_date < start_date:
        logger.debug(
            "snapshot total_cash_twd range skipped: end_date before start_date",
            extra={
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        )
        return

    cur = start_date
    while cur <= end_date:
        cash_total_twd, skipped = cash_account_service.get_total_balance_in(
            db, "TWD", asof=cur
        )
        if skipped:
            logger.warning("snapshot total_cash_twd skipped currencies: %s", skipped)

        existing = db.get(PortfolioSnapshot, cur)
        if existing is not None:
            if cash_total_twd == 0 and _is_cash_only_row(existing):
                db.delete(existing)
            else:
                existing.total_cash_twd = cash_total_twd
        elif cash_total_twd != 0:
            db.add(
                PortfolioSnapshot(
                    date=cur,
                    total_market_value=0,
                    total_cost=0,
                    total_unrealized_pnl=0,
                    total_dividends=0,
                    total_realized_pnl=0,
                    total_cash_twd=cash_total_twd,
                    portfolio_xirr=None,
                )
            )

        cur += timedelta(days=1)

    db.commit()


def list_snapshots(
    db: Session,
    *,
    from_date: Optional[dt_date] = None,
    to_date: Optional[dt_date] = None,
    interval: str = "day",
) -> list[PortfolioSnapshot]:
    """Return snapshot rows; optionally downsampled.

    ``interval``:
      - ``day``: every row
      - ``week``: last row in each ISO week
      - ``month``: last row in each calendar month
    """
    if interval not in ("day", "week", "month"):
        raise ValueError(f"unsupported interval: {interval}")

    q = db.query(PortfolioSnapshot)
    if from_date is not None:
        q = q.filter(PortfolioSnapshot.date >= from_date)
    if to_date is not None:
        q = q.filter(PortfolioSnapshot.date <= to_date)
    rows = q.order_by(PortfolioSnapshot.date.asc()).all()

    if interval == "day" or not rows:
        return rows

    bucket_of = (
        (lambda d: d.isocalendar()[:2]) if interval == "week"
        else (lambda d: (d.year, d.month))
    )
    # Keep the last row of each bucket (rows already sorted ascending).
    by_bucket: dict = {}
    for row in rows:
        by_bucket[bucket_of(row.date)] = row
    return [by_bucket[k] for k in sorted(by_bucket.keys())]
