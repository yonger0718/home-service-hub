"""add symbol_map table

Revision ID: j7e8f9a0b1c2
Revises: i6d7e8f9a0b1
Create Date: 2026-05-15 16:30:00.000000

Caches Chinese-name -> ticker mappings sourced from the ``twstock`` library
so imported broker CSVs that ship names instead of tickers can be rewritten
to canonical TWSE/TPEx symbols.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "j7e8f9a0b1c2"
down_revision: Union[str, Sequence[str], None] = "i6d7e8f9a0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "symbol_map",
        sa.Column("name", sa.String(length=200), primary_key=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_symbol_map_symbol", "symbol_map", ["symbol"])


def downgrade() -> None:
    op.drop_index("ix_symbol_map_symbol", table_name="symbol_map")
    op.drop_table("symbol_map")
