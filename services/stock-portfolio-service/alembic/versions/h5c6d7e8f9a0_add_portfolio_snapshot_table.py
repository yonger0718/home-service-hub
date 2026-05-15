"""add portfolio_snapshot table

Revision ID: h5c6d7e8f9a0
Revises: g4b5c6d7e8f9
Create Date: 2026-05-15 11:00:00.000000

One row per TW calendar date capturing the durable totals from
``PortfolioSummary``. Written daily by the 15:30 cron in
``scheduler.run_portfolio_snapshot``.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "h5c6d7e8f9a0"
down_revision: Union[str, Sequence[str], None] = "g4b5c6d7e8f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "portfolio_snapshot",
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("total_market_value", sa.Numeric(20, 4), nullable=False),
        sa.Column("total_cost", sa.Numeric(20, 4), nullable=False),
        sa.Column("total_unrealized_pnl", sa.Numeric(20, 4), nullable=False),
        sa.Column("total_dividends", sa.Numeric(20, 4), nullable=False),
        sa.Column("portfolio_xirr", sa.Numeric(10, 6), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("date", name="pk_portfolio_snapshot"),
    )


def downgrade() -> None:
    op.drop_table("portfolio_snapshot")
