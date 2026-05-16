"""add price_history table

Revision ID: g4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-05-15 10:00:00.000000

One row per (symbol, trading-date) sourced from TWSE MI_INDEX or TPEx daily
quotes. Composite primary key prevents duplicate inserts; the upsert path in
``market_data_service`` relies on it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "g4b5c6d7e8f9"
down_revision: Union[str, Sequence[str], None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "price_history",
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(12, 4), nullable=True),
        sa.Column("high", sa.Numeric(12, 4), nullable=True),
        sa.Column("low", sa.Numeric(12, 4), nullable=True),
        sa.Column("close", sa.Numeric(12, 4), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("turnover", sa.Numeric(20, 2), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("symbol", "date", name="pk_price_history"),
        sa.CheckConstraint("close > 0", name="ck_price_history_close_positive"),
    )
    op.create_index("ix_price_history_date", "price_history", ["date"])


def downgrade() -> None:
    op.drop_index("ix_price_history_date", table_name="price_history")
    op.drop_table("price_history")
