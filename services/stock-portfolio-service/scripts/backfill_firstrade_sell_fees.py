"""Book the SEC/TAF fee on Firstrade sells that were imported with fee=0 (#228).

One-shot, owner-run. Pass the Firstrade CSV exports that cover the stored sells;
each CSV sell is paired one-to-one with a stored sell of the same symbol, qty,
price and day, and only a stored fee of 0 is changed. Dry run by default.

After --apply, rebuild snapshots and realized P&L from the printed date:
    curl -X POST http://127.0.0.1:8001/api/portfolio/imports/recalc \\
         -H 'Content-Type: application/json' -d '{"start_date": "<date>"}'
"""

import argparse
from pathlib import Path

from app.database import SessionLocal
from app.services import broker_firstrade_service


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Book SEC/TAF fees on Firstrade sells stored with fee=0.")
    parser.add_argument("csv", nargs="+", type=Path, help="Firstrade CSV export(s)")
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="write the fees instead of running a dry-run",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session = SessionLocal()
    try:
        updates = []
        for path in args.csv:
            parsed = broker_firstrade_service.parse(path.read_bytes())
            for error in parsed.errors:
                print(f"{path.name}: row {error.row_index}: {error.message}")
            updates += broker_firstrade_service.backfill_sell_fees(session, parsed, apply=args.apply)
        if not updates:
            print("nothing to update")
            return 0
        for update in updates:
            print(f"{update.trade_date.date().isoformat()} {update.symbol} id={update.transaction_id} fee={update.fee}")
        total = sum(update.fee for update in updates)
        earliest = min(update.trade_date for update in updates).date().isoformat()
        verb = "updated" if args.apply else "DRY RUN: would update"
        print(f"{verb} {len(updates)} sells, fee total {total} USD; recalc from {earliest}")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
