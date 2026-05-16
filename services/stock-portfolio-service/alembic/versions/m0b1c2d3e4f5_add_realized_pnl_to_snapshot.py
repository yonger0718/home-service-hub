"""add total_realized_pnl to portfolio_snapshot

Revision ID: m0b1c2d3e4f5
Revises: l9a0b1c2d3e4
Create Date: 2026-05-16 09:50:00.000000

Day-trade SELLs realise P&L the same day, so MV - cost in the snapshot
misses that contribution. Add a cumulative realised-P&L column so the
networth chart can reflect day-trade gains/losses.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "m0b1c2d3e4f5"
down_revision: Union[str, Sequence[str], None] = "l9a0b1c2d3e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "portfolio_snapshot",
        sa.Column(
            "total_realized_pnl",
            sa.Numeric(20, 4),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("portfolio_snapshot", "total_realized_pnl")
