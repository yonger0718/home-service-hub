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

_TICKER_PATTERN = re.compile(r"^[0-9A-Za-z]+$")  # full token must be ASCII alnum


def _looks_like_ticker(symbol: str) -> bool:
    """Returns True if the symbol is entirely ASCII alphanumeric (TWSE/TPEx ticker shape)."""
    cleaned = (symbol or "").strip()
    if not cleaned:
        return False
    return bool(_TICKER_PATTERN.fullmatch(cleaned))


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
        instrument_type = getattr(entry, "type", None)
        if not name or not code:
            continue
        db.merge(
            SymbolMap(
                name=name,
                symbol=code,
                market=market[:8],
                type=(instrument_type[:32] if instrument_type else None),
            )
        )
        count += 1
    db.commit()
    logger.info("symbol_map.refreshed", extra={"count": count})
    return {"refreshed_count": count}


_INELIGIBLE_TYPE_SUBSTRINGS: tuple[str, ...] = ("認購", "認售", "牛證", "熊證")


def lookup_warrant_type(db: Session, symbol: str) -> Optional[str]:
    """Return the live ``symbol_map.type`` only when it identifies a warrant."""
    if not symbol:
        return None
    row = (
        db.query(SymbolMap.type)
        .filter(SymbolMap.symbol == symbol)
        .first()
    )
    if row is None or not row[0]:
        return None
    type_value = row[0]
    if any(token in type_value for token in _INELIGIBLE_TYPE_SUBSTRINGS):
        return type_value
    return None


def is_day_trade_eligible(
    db: Session, symbol: str, instrument_type: Optional[str] = None
) -> bool:
    """Return whether ``symbol`` is eligible for TW 現股當沖 classification.

    Fail-open: unmapped symbols and rows with NULL or empty ``type``
    resolve as eligible. Resolvable rows whose ``type`` CONTAINS any of
    ``{認購, 認售, 牛證, 熊證}`` are ineligible (warrants + 牛熊證). The
    substring check covers twstock's actual format ``上市認購(售)權證`` /
    ``上櫃認購(售)權證`` rather than an exact-prefix match.

    When ``instrument_type`` is non-None (including empty string), the
    stamped value is authoritative and the live ``symbol_map`` lookup is
    skipped — this preserves the snapshot-first contract for warrant rows
    even if the caller explicitly stamped ``''``.
    """
    if instrument_type is not None:
        return not any(
            token in instrument_type for token in _INELIGIBLE_TYPE_SUBSTRINGS
        )
    if not symbol:
        return True
    row = (
        db.query(SymbolMap.type)
        .filter(SymbolMap.symbol == symbol)
        .first()
    )
    if row is None or not row[0]:
        return True
    type_value = row[0]
    return not any(token in type_value for token in _INELIGIBLE_TYPE_SUBSTRINGS)


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
