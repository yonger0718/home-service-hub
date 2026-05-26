"""add instrument_type snapshot column to transactions

Revision ID: s6h7i8j9k0l1
Revises: r5g6h7i8j9k0
Create Date: 2026-05-25 00:00:00.000000

Adds nullable ``transactions.instrument_type`` so warrant rows can snapshot
the live ``symbol_map.type`` at insert time. This closes the warrant-code
recycle risk where historical rows could later resolve eligibility from a
new instrument using the same symbol.

Follow-up revision ``t7i8j9k0l1m2`` backfills existing warrant rows using
the current ``symbol_map`` type as the best available historical proxy.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "s6h7i8j9k0l1"
down_revision: Union[str, Sequence[str], None] = "r5g6h7i8j9k0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("instrument_type", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transactions", "instrument_type")
