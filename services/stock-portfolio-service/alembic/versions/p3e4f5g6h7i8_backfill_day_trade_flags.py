"""backfill is_day_trade — clear false-True flags on warrant/牛熊 rows

Revision ID: p3e4f5g6h7i8
Revises: o2d3e4f5g6h7
Create Date: 2026-05-21 10:30:00.000000

Narrow data migration: for every transaction currently flagged
``is_day_trade=true`` whose symbol resolves to an ineligible instrument
(認購權證, 認售權證, 牛證, 熊證 — i.e. ``symbol_map.type`` contains any of
``{認購, 認售, 牛證, 熊證}`` as substring), set the flag to ``false``.

Intentionally narrow: we do NOT enforce the inverse direction (eligible
buckets with BUY+SELL same-day that happen to be flagged False). The
legacy ``has_buy AND has_sell`` heuristic over-classifies — it cannot
distinguish a true 沖買/沖賣 day-trade pair from an unrelated same-day
open+close. A separate follow-up change (``broker-day-trade-marker``)
will re-derive day-trade flags from the broker's explicit
``沖買/沖賣`` markers rather than from the bucket heuristic.

Predicate inlined (not imported from app service) so the migration stays
self-contained.

Downgrade is a no-op: prior flag values are not preserved.
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


revision: str = "p3e4f5g6h7i8"
down_revision: Union[str, Sequence[str], None] = "o2d3e4f5g6h7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_INELIGIBLE_TYPE_SUBSTRINGS = ("認購", "認售", "牛證", "熊證")


def _is_ineligible(type_value: str | None) -> bool:
    if not type_value:
        return False
    return any(token in type_value for token in _INELIGIBLE_TYPE_SUBSTRINGS)


def clear_warrant_day_trade_flags(conn) -> dict[str, int]:
    """Set ``is_day_trade=false`` on every currently-True row whose symbol is
    a warrant or 牛熊證 per ``symbol_map.type``.

    Returns ``{symbols_inspected, symbols_ineligible, rows_flipped_to_false}``.
    Public so integration tests can exercise the logic against a live
    Postgres connection without invoking the full alembic stack.
    """
    candidate_symbols = [
        row[0]
        for row in conn.execute(
            text(
                "SELECT DISTINCT symbol FROM transactions WHERE is_day_trade = true"
            )
        )
    ]

    rows_flipped = 0
    ineligible_count = 0
    for symbol in candidate_symbols:
        type_row = conn.execute(
            text("SELECT type FROM symbol_map WHERE symbol = :sym"),
            {"sym": symbol},
        ).first()
        if not _is_ineligible(type_row[0] if type_row else None):
            continue
        ineligible_count += 1
        result = conn.execute(
            text(
                "UPDATE transactions "
                "SET is_day_trade = false "
                "WHERE symbol = :sym AND is_day_trade = true"
            ),
            {"sym": symbol},
        )
        rows_flipped += result.rowcount

    return {
        "symbols_inspected": len(candidate_symbols),
        "symbols_ineligible": ineligible_count,
        "rows_flipped_to_false": rows_flipped,
    }


def upgrade() -> None:
    result = clear_warrant_day_trade_flags(op.get_bind())
    print(
        f"backfill_day_trade_flags: inspected {result['symbols_inspected']} symbols, "
        f"{result['symbols_ineligible']} ineligible, "
        f"flipped {result['rows_flipped_to_false']} rows to False"
    )


def downgrade() -> None:
    """No-op. Prior flag values are not preserved; manual fix required if rollback needed."""
    pass
