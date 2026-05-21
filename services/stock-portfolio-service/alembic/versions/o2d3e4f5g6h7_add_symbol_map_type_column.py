"""add type column to symbol_map

Revision ID: o2d3e4f5g6h7
Revises: n1c2d3e4f5g6
Create Date: 2026-05-21 10:00:00.000000

Adds a nullable ``type`` column (e.g., ``股票``, ``ETF``, ``認購權證``,
``認售權證``, ``牛證``, ``熊證``) sourced from ``twstock.codes[code].type``
during the next ``refresh_all_from_twstock`` run. Used by
``symbol_map_service.is_day_trade_eligible`` to gate the day-trade flag —
warrants and 牛熊證 cannot be 現股當沖 per TW FSC rules.

Column is nullable: existing rows resolve as eligible (fail-open) until
the next scheduled refresh populates them.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "o2d3e4f5g6h7"
down_revision: Union[str, Sequence[str], None] = "n1c2d3e4f5g6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "symbol_map",
        sa.Column("type", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("symbol_map", "type")
