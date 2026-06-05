"""add yfinance quote source

Revision ID: z3o4p5q6r7s8
Revises: y2n3o4p5q6r7
Create Date: 2026-06-05 00:00:00.000000

Adds the Phase 2 yfinance schema: ``fx_rates`` daily TWD conversion rows
and native ``price_history.currency`` values. Downgrade DROPS ``fx_rates``
and the ``price_history.currency`` column, which loses existing yfinance
FX and native-currency data.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "z3o4p5q6r7s8"
down_revision: Union[str, Sequence[str], None] = "y2n3o4p5q6r7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fx_rates",
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("rate_to_twd", sa.Numeric(20, 8), nullable=False),
        sa.Column(
            "source",
            sa.String(length=16),
            server_default="yfinance",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("currency", "date", name="pk_fx_rates"),
        sa.CheckConstraint(
            "rate_to_twd > 0", name="ck_fx_rates_rate_to_twd_positive"
        ),
        sa.CheckConstraint(
            "currency IN ('USD', 'GBP')",
            name="ck_fx_rates_supported_currency",
        ),
    )
    op.add_column(
        "price_history",
        sa.Column(
            "currency",
            sa.String(length=8),
            nullable=False,
            server_default="TWD",
        ),
    )


def downgrade() -> None:
    op.drop_column("price_history", "currency")
    op.drop_table("fx_rates")
