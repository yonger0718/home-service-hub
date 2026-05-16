"""Maintain + apply the Chinese-name -> ticker map sourced from twstock."""

from __future__ import annotations

import logging
import re
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import portfolio as portfolio_models
from ..models.symbol_map import SymbolMap

logger = logging.getLogger(__name__)

_TICKER_PATTERN = re.compile(r"^[0-9A-Za-z]")  # numeric or alphanumeric (covers 00XX ETFs)


def _looks_like_ticker(symbol: str) -> bool:
    """Returns True if the symbol starts with a digit or ASCII letter (TWSE/TPEx ticker shape)."""
    if not symbol:
        return False
    return bool(_TICKER_PATTERN.match(symbol))


def refresh_all_from_twstock(db: Session) -> dict:
    """Pull latest codes from twstock and upsert into symbol_map.

    Triggers twstock's own code-database refresh once per call. Each row is
    upserted via ``Session.merge`` so the operation is idempotent across re-runs.
    """
    import twstock  # imported lazily so test fixtures can patch the attribute

    try:
        twstock.__update_codes()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — refresh failure should not poison the upsert pass
        logger.exception("symbol_map.update_codes.failed")

    count = 0
    for code, entry in twstock.codes.items():
        name = getattr(entry, "name", None)
        market = getattr(entry, "market", "") or ""
        if not name or not code:
            continue
        db.merge(SymbolMap(name=name, symbol=code, market=market[:8]))
        count += 1
    db.commit()
    logger.info("symbol_map.refreshed", extra={"count": count})
    return {"refreshed_count": count}


def resolve_name(db: Session, name: str) -> Optional[str]:
    """Return the ticker for a Chinese name, or None if unmapped."""
    row = (
        db.query(SymbolMap)
        .filter(func.lower(SymbolMap.name) == name.strip().lower())
        .one_or_none()
    )
    return row.symbol if row is not None else None


def backfill_transactions(db: Session, *, dry_run: bool = False) -> dict:
    """Rewrite transactions.symbol from Chinese name -> ticker where resolvable.

    ``import_fingerprint`` is preserved on rewrite so future re-imports of the
    original Chinese-named CSV continue to dedupe against the rewritten row.
    ``collisions`` is reserved for future use (always empty under the current
    preserve-fingerprint contract).
    """
    updated = 0
    unresolved: list[str] = []
    collisions: list[int] = []
    unresolved_set: set[str] = set()

    rows = db.query(portfolio_models.Transaction).all()
    for tx in rows:
        if _looks_like_ticker(tx.symbol):
            continue

        ticker = resolve_name(db, tx.symbol)
        if ticker is None:
            if tx.symbol not in unresolved_set:
                unresolved.append(tx.symbol)
                unresolved_set.add(tx.symbol)
            continue

        if not dry_run:
            tx.symbol = ticker
        updated += 1

    if not dry_run:
        db.commit()
    else:
        db.rollback()

    logger.info(
        "symbol_map.backfill.complete",
        extra={
            "updated": updated,
            "unresolved": len(unresolved),
            "collisions": len(collisions),
            "dry_run": dry_run,
        },
    )
    return {
        "updated": updated,
        "unresolved": unresolved,
        "collisions": collisions,
        "dry_run": dry_run,
    }
