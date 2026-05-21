"""backfill odd-lot day-trade flags

Revision ID: r5g6h7i8j9k0
Revises: q4f5g6h7i8j9
Create Date: 2026-05-21 12:00:00.000000

D5: odd-lot rows never carry ``is_day_trade=true`` for this user's trading
pattern. A row is odd-lot when ``quantity < 1000 OR quantity % 1000 != 0``.
This migration clears legacy heuristic over-classification on 零股 rows and
is safe to re-run because it only updates currently-True matching rows.

Downgrade is a no-op: prior flag values are not preserved.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "r5g6h7i8j9k0"
down_revision: Union[str, Sequence[str], None] = "q4f5g6h7i8j9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    result = op.get_bind().execute(
        sa.text(
            "UPDATE transactions SET is_day_trade = false "
            "WHERE is_day_trade = true AND (quantity < 1000 OR quantity % 1000 != 0)"
        )
    )
    print(f"backfill_odd_lot_day_trade_flags: cleared {result.rowcount} rows")


def downgrade() -> None:
    """No-op. Prior flag values are not preserved; manual fix required if rollback needed."""
    pass
