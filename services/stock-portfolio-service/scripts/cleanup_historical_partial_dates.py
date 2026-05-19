"""Clean up historical partial-date sentinel rows from detect-partial-phase1-fetch.

This one-shot script removes the known poisoned rows left by the historical
partial-fetch incident after the detect-partial-phase1-fetch gate shipped.
Do not repurpose this script for any other dates or sources; create a new,
reviewed cleanup instead.
"""

import argparse
from datetime import date
from decimal import Decimal
from typing import List, NamedTuple

from app.database import SessionLocal
from app.models.price_history import PriceHistory

TARGET_DATES = (date(2026, 4, 3), date(2026, 4, 6), date(2026, 5, 1))
TARGET_SOURCES = ("TWSE", "TPEx")


class PriceHistoryRow(NamedTuple):
    date: date
    source: str
    symbol: str
    close: Decimal


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delete the known historical partial-date sentinel rows."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="delete the matching rows instead of running a dry-run",
    )
    return parser.parse_args()


def select_rows(session) -> List[PriceHistoryRow]:
    rows = (
        session.query(
            PriceHistory.date,
            PriceHistory.source,
            PriceHistory.symbol,
            PriceHistory.close,
        )
        .filter(
            PriceHistory.date.in_(TARGET_DATES),
            PriceHistory.source.in_(TARGET_SOURCES),
        )
        .order_by(PriceHistory.date, PriceHistory.source)
        .all()
    )
    return [PriceHistoryRow(*row) for row in rows]


def print_rows(rows: List[PriceHistoryRow]) -> None:
    for row in rows:
        print(f"{row.date.isoformat()} {row.source} {row.symbol} close={row.close}")


def format_rows(rows: List[PriceHistoryRow]) -> str:
    return ", ".join(
        f"{row.date.isoformat()} {row.source} {row.symbol} close={row.close}"
        for row in rows
    )


def delete_rows(session) -> int:
    return (
        session.query(PriceHistory)
        .filter(
            PriceHistory.date.in_(TARGET_DATES),
            PriceHistory.source.in_(TARGET_SOURCES),
        )
        .delete(synchronize_session=False)
    )


def main() -> int:
    args = parse_args()
    session = SessionLocal()
    try:
        rows = select_rows(session)
        if not rows:
            print("nothing to delete")
            return 0

        print_rows(rows)

        if not args.apply:
            print(f"DRY RUN: would delete {len(rows)} rows")
            return 0

        deleted_count = delete_rows(session)
        session.commit()
        print(f"deleted {deleted_count} rows")

        remaining_rows = select_rows(session)
        if remaining_rows:
            session.rollback()
            raise RuntimeError(
                "post-delete verification failed; remaining rows: "
                f"{format_rows(remaining_rows)}"
            )

        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
