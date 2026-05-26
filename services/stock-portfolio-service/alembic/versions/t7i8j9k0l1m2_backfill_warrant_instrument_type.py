"""backfill warrant instrument_type snapshots

Revision ID: t7i8j9k0l1m2
Revises: s6h7i8j9k0l1
Create Date: 2026-05-25 00:05:00.000000

Backfills ``transactions.instrument_type`` for existing warrant rows using
the current ``symbol_map.type`` value. Revision ``s6h7i8j9k0l1`` adds the
nullable column; this follow-up data migration stamps only rows whose
current type contains the warrant/牛熊 vocabulary and leaves non-warrant
rows NULL so live lookup behavior is preserved.

Idempotent: only rows with ``instrument_type IS NULL`` are updated.
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


revision: str = "t7i8j9k0l1m2"
down_revision: Union[str, Sequence[str], None] = "s6h7i8j9k0l1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    result = op.get_bind().execute(
        text(
            "UPDATE transactions t "
            "SET instrument_type = sm.type "
            "FROM symbol_map sm "
            "WHERE t.symbol = sm.symbol "
            "  AND t.instrument_type IS NULL "
            "  AND ("
            "       sm.type LIKE '%認購%' "
            "    OR sm.type LIKE '%認售%' "
            "    OR sm.type LIKE '%牛證%' "
            "    OR sm.type LIKE '%熊證%' "
            "  )"
        )
    )
    print(f"backfill_warrant_instrument_type: stamped {result.rowcount} rows")


def downgrade() -> None:
    """No-op. The column is removed by the preceding schema downgrade."""
    pass
