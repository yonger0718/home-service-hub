"""Futures-only history fill (spec §6.5).

``backfill_futures_snapshots`` writes the 永豐期貨 equity series into ``portfolio_snapshot`` without touching any
other column: on every existing row in range it sets ``futures_equity_twd`` only; on a futures source date with no
row it inserts one that copies every other column (``portfolio_xirr`` included) from the latest existing row before
it, or zeros and a NULL XIRR when there is none. It never deletes a row and never runs the networth replay (which
rewrites whole rows and clears ``portfolio_xirr``).

    python -m app.services.sinopac_futures_backfill --from 2024-08-27 [--to YYYY-MM-DD] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from ..models.portfolio_snapshot import PortfolioSnapshot
from . import portfolio_snapshot_service, sinopac_futures_series

_COPIED = (
    "total_market_value",
    "total_cost",
    "total_unrealized_pnl",
    "total_dividends",
    "total_realized_pnl",
    "total_cash_twd",
    "portfolio_xirr",
)
_QUANT = Decimal("0.0001")  # futures_equity_twd is Numeric(20, 4)


def _value_at(values: dict[date, Optional[sinopac_futures_series.FuturesValue]], d: date) -> Decimal:
    value = values[d]
    return Decimal(value.total_equity) if value is not None else Decimal("0")


def backfill_futures_snapshots(db: Session, start: date, end: date, *, dry_run: bool) -> dict:
    """Fill ``futures_equity_twd`` over ``[start, end]``; return the counts.

    ``updated``: existing rows in range (each gets the series value); ``inserted``: rows added on futures source
    dates that had none; ``changed``: rows whose futures value differs from before (an inserted row counts from 0);
    ``delta_sum``: the sum of those differences, a string with 4 decimals. With ``dry_run`` the same writes are made
    and rolled back, so the counts are the real run's; otherwise the session commits once.
    """
    if end < start:
        raise ValueError("end before start")
    sinopac_futures_series.futures_lock(db)  # exclusive; held until the commit / rollback below
    try:
        values = sinopac_futures_series.equity_by_date(db, start, end)
        sources = sorted(d for d in sinopac_futures_series.source_dates(db) if start <= d <= end)
        rows = (
            db.query(PortfolioSnapshot)
            .filter(PortfolioSnapshot.date >= start, PortfolioSnapshot.date <= end)
            .order_by(PortfolioSnapshot.date)
            .all()
        )
        existing = {row.date: row for row in rows}
        # The latest row before ``start`` seeds the forward fill of a source date that precedes every row in range.
        seed = (
            db.query(PortfolioSnapshot)
            .filter(PortfolioSnapshot.date < start)
            .order_by(PortfolioSnapshot.date.desc())
            .first()
        )
        templates = {d: {c: getattr(row, c) for c in _COPIED} for d, row in existing.items()}
        seed_columns = {c: getattr(seed, c) for c in _COPIED} if seed is not None else None

        changed = 0
        delta_sum = Decimal("0")
        for row in rows:
            new = _value_at(values, row.date)
            delta = new - Decimal(row.futures_equity_twd)
            if delta != 0:
                changed += 1
                delta_sum += delta
            row.futures_equity_twd = new

        inserted = 0
        for d in sources:
            if d in existing:
                continue
            before = [k for k in templates if k < d]
            columns = templates[max(before)] if before else seed_columns
            if columns is None:
                columns = {c: Decimal("0") for c in _COPIED}
                columns["portfolio_xirr"] = None
            new = _value_at(values, d)
            db.add(PortfolioSnapshot(date=d, futures_equity_twd=new, **columns))
            inserted += 1
            if new != 0:
                changed += 1
                delta_sum += new
        db.flush()

        counts = {
            "updated": len(rows),
            "inserted": inserted,
            "changed": changed,
            "delta_sum": str(delta_sum.quantize(_QUANT)),
        }
    except Exception:
        db.rollback()
        raise
    if dry_run:
        db.rollback()
    else:
        db.commit()
    return counts


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write the 永豐期貨 equity series into portfolio_snapshot (futures column only)."
    )
    parser.add_argument("--from", dest="from_date", required=True, help="first date, YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", default=None, help="last date, YYYY-MM-DD (default: today, Taipei)")
    parser.add_argument("--dry-run", action="store_true", help="compute the counts and roll back")
    args = parser.parse_args(argv)

    try:
        start = _parse_date(args.from_date)
        end = _parse_date(args.to_date) if args.to_date else portfolio_snapshot_service._today_tw()
    except ValueError as exc:
        print(f"bad date: {exc}", file=sys.stderr)
        return 2
    if end < start:
        print(f"bad range: --to {end} is before --from {start}", file=sys.stderr)
        return 2

    from app.database import SessionLocal

    db = SessionLocal()
    try:
        counts = backfill_futures_snapshots(db, start, end, dry_run=args.dry_run)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    print(json.dumps({**counts, "dry_run": args.dry_run, "from": start.isoformat(), "to": end.isoformat()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
