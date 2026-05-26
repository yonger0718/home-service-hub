"""One-shot backfill: correct stale warrant ``name``/``instrument_type`` from stonk.json.

Recovers historical warrant names lost to TWSE/TPEx code recycle. TWSE's
public archive overwrites the title with the currently-existing warrant's
name, so neither broker re-download nor ``per_date_verify`` can recover
the original name. The companion ``stonk.json`` file preserves the
historical mapping at the time the user originally tracked the warrant.

Usage:
    python -m scripts.backfill_warrant_names_from_stonk \\
        --stonk-json /path/to/stonk.json [--commit]

By default runs in dry-run mode (no writes). Pass ``--commit`` to apply.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

# Allow running as a script from the service directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models import portfolio as P


_MARKET_TO_INSTRUMENT_TYPE: dict[str, dict[str, str]] = {
    "上市": {"購": "上市認購(售)權證", "售": "上市認購(售)權證"},
    "櫃": {"購": "上櫃認購(售)權證", "售": "上櫃認購(售)權證"},
}


def _load_stonk_overrides(path: Path) -> dict[str, tuple[str, str]]:
    """Parse stonk.json (skipping its leading version line). Returns symbol -> (name, market)."""
    raw = path.read_text(encoding="utf-8")
    # File starts with a non-JSON version banner (e.g. ``v2.7.0``); skip first line.
    first_newline = raw.find("\n")
    payload = json.loads(raw[first_newline + 1 :])
    out: dict[str, tuple[str, str]] = {}
    for symbol, entry in payload.get("non_public_stock", {}).items():
        if not isinstance(entry, list) or len(entry) < 3:
            continue
        name, _is_active, market = entry[0], entry[1], entry[2]
        if name and market:
            out[symbol] = (name, market)
    return out


def _infer_instrument_type(name: str, market: str) -> Optional[str]:
    if not name or not market:
        return None
    side_token = "購" if "購" in name else ("售" if "售" in name else None)
    if side_token is None:
        return None
    return _MARKET_TO_INSTRUMENT_TYPE.get(market, {}).get(side_token)


def _preview(db: Session, overrides: dict[str, tuple[str, str]]) -> list[dict]:
    """Resolve per-symbol diffs against the DB. Returns one record per affected symbol."""
    diffs: list[dict] = []
    for symbol, (correct_name, market) in sorted(overrides.items()):
        rows = (
            db.query(P.Transaction)
            .filter(P.Transaction.symbol == symbol)
            .all()
        )
        if not rows:
            continue
        new_instrument_type = _infer_instrument_type(correct_name, market)
        per_symbol_changes = {
            "name_updates": sum(1 for r in rows if r.name != correct_name),
            "instrument_type_updates": sum(
                1
                for r in rows
                if new_instrument_type is not None
                and r.instrument_type != new_instrument_type
            ),
        }
        diffs.append(
            {
                "symbol": symbol,
                "row_count": len(rows),
                "current_name": rows[0].name,
                "correct_name": correct_name,
                "current_instrument_type": rows[0].instrument_type,
                "correct_instrument_type": new_instrument_type,
                "changes": per_symbol_changes,
            }
        )
    return diffs


def _apply(db: Session, diffs: list[dict]) -> None:
    for d in diffs:
        symbol = d["symbol"]
        stmt = update(P.Transaction).where(P.Transaction.symbol == symbol).values(
            name=d["correct_name"],
            instrument_type=d["correct_instrument_type"],
        )
        db.execute(stmt)
    db.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stonk-json", required=True, type=Path)
    parser.add_argument("--commit", action="store_true", help="Apply writes (default dry-run)")
    args = parser.parse_args()

    overrides = _load_stonk_overrides(args.stonk_json)
    print(f"Loaded {len(overrides)} non-public-stock overrides from {args.stonk_json}")

    db = SessionLocal()
    try:
        diffs = _preview(db, overrides)
        if not diffs:
            print("No matching warrants in DB. Nothing to do.")
            return 0

        print(f"\n{'Symbol':<10} {'Rows':>5}  {'Field':<18} {'Current':<30} {'Correct':<30}")
        print("-" * 100)
        for d in diffs:
            print(
                f"{d['symbol']:<10} {d['row_count']:>5}  "
                f"{'name':<18} {str(d['current_name']):<30} {str(d['correct_name']):<30}"
            )
            print(
                f"{'':<10} {'':>5}  "
                f"{'instrument_type':<18} {str(d['current_instrument_type']):<30} "
                f"{str(d['correct_instrument_type']):<30}"
            )

        total_name_updates = sum(d["changes"]["name_updates"] for d in diffs)
        total_type_updates = sum(d["changes"]["instrument_type_updates"] for d in diffs)
        print(
            f"\nWould touch {total_name_updates} name updates + "
            f"{total_type_updates} instrument_type updates across {len(diffs)} symbols."
        )

        if not args.commit:
            print("Dry-run only. Pass --commit to apply.")
            return 0

        _apply(db, diffs)
        print("Applied.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
