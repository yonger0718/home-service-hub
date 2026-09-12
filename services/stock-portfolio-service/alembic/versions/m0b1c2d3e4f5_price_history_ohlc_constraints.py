"""add full OHLC integrity constraints to price_history

Revision ID: m0b1c2d3e4f6
Revises: m0b1c2d3e4f5
Create Date: 2026-05-27 10:00:00.000000

Mirrors the model-level CheckConstraints added to PriceHistory so DB and
ORM stay aligned: open/high/low must be positive when present, and high
must be >= low when both are set.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "m0b1c2d3e4f6"
down_revision: Union[str, Sequence[str], None] = "m0b1c2d3e4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_price_history_open_positive",
        "price_history",
        "open IS NULL OR open > 0",
    )
    op.create_check_constraint(
        "ck_price_history_high_positive",
        "price_history",
        "high IS NULL OR high > 0",
    )
    op.create_check_constraint(
        "ck_price_history_low_positive",
        "price_history",
        "low IS NULL OR low > 0",
    )
    op.create_check_constraint(
        "ck_price_history_high_gte_low",
        "price_history",
        "high IS NULL OR low IS NULL OR high >= low",
    )


def downgrade() -> None:
    op.drop_constraint("ck_price_history_high_gte_low", "price_history", type_="check")
    op.drop_constraint("ck_price_history_low_positive", "price_history", type_="check")
    op.drop_constraint("ck_price_history_high_positive", "price_history", type_="check")
    op.drop_constraint("ck_price_history_open_positive", "price_history", type_="check")
