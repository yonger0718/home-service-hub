"""create fx_rate table

Revision ID: w0l1m2n3o4p5
Revises: v9k0l1m2n3o4
Create Date: 2026-06-02 00:10:00.000000

Adds daily FX snapshots keyed by date and currency pair so balance
aggregation can use deterministic historical conversion rates.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "w0l1m2n3o4p5"
down_revision: Union[str, Sequence[str], None] = "v9k0l1m2n3o4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fx_rate",
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("base_currency", sa.CHAR(length=3), nullable=False),
        sa.Column("quote_currency", sa.CHAR(length=3), nullable=False),
        sa.Column("rate", sa.Numeric(20, 8), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "date", "base_currency", "quote_currency", name="pk_fx_rate"
        ),
        sa.CheckConstraint("rate > 0", name="ck_fx_rate_rate_positive"),
    )


def downgrade() -> None:
    op.drop_table("fx_rate")
