"""add total cash twd to portfolio_snapshot

Revision ID: x1m2n3o4p5q6
Revises: w0l1m2n3o4p5
Create Date: 2026-06-03 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "x1m2n3o4p5q6"
down_revision: Union[str, Sequence[str], None] = "w0l1m2n3o4p5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "portfolio_snapshot",
        sa.Column(
            "total_cash_twd",
            sa.Numeric(20, 4),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("portfolio_snapshot", "total_cash_twd")
