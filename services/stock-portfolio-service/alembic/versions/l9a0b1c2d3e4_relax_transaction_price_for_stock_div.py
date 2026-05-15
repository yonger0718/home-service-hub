"""relax transactions.price >= 0 for stock-dividend zero-cost rows

Revision ID: l9a0b1c2d3e4
Revises: k8f9a0b1c2d3
Create Date: 2026-05-15 12:15:00.000000

The auto-record service writes stock-dividend awards as a
zero-cost BUY transaction. The previous ``price > 0`` check rejected
that. Relax to ``price >= 0`` to support gifted shares while keeping
quantity > 0 to prevent empty rows.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "l9a0b1c2d3e4"
down_revision: Union[str, Sequence[str], None] = "k8f9a0b1c2d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_transactions_price_positive", "transactions", type_="check")
    op.create_check_constraint(
        "ck_transactions_price_nonnegative",
        "transactions",
        "price >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_transactions_price_nonnegative", "transactions", type_="check")
    op.create_check_constraint(
        "ck_transactions_price_positive",
        "transactions",
        "price > 0",
    )
