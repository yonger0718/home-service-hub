"""add position_side column to transactions

Revision ID: n1c2d3e4f5g6
Revises: m0b1c2d3e4f5
Create Date: 2026-05-20 12:00:00.000000

Adds a ``position_side`` discriminator (LONG / SHORT) on ``transactions``
so realized-P&L compute can route closing transactions into the correct
inventory pool. Cathay CSV import populates this from 買賣別:

- 現買 / 現賣 / 資買 / 資賣 / 沖買 / 沖賣 → LONG
- 券買 / 券賣                              → SHORT

Existing rows backfill to LONG (99.8% of historical data is long-side;
operators re-import broker CSVs to recover the rare SHORT classification).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "n1c2d3e4f5g6"
down_revision: Union[str, Sequence[str], None] = "m0b1c2d3e4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    position_side_enum = sa.Enum(
        "LONG", "SHORT", name="position_side_enum"
    )
    position_side_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "transactions",
        sa.Column(
            "position_side",
            position_side_enum,
            nullable=False,
            server_default="LONG",
        ),
    )


def downgrade() -> None:
    op.drop_column("transactions", "position_side")
    sa.Enum(name="position_side_enum").drop(op.get_bind(), checkfirst=True)
