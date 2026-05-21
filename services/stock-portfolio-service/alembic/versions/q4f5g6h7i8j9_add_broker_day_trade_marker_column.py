"""add broker day-trade marker column to transactions

Revision ID: q4f5g6h7i8j9
Revises: p3e4f5g6h7i8
Create Date: 2026-05-21 11:00:00.000000

Adds nullable ``broker_day_trade_marker`` to carry Cathay CSV ``買賣別``
markers ``沖買``/``沖賣``. ``_recompute_day_trade_flags`` consumes this
column in its priority chain before falling back to the same-day BUY+SELL
heuristic.

Dev-DB cleanup note: legacy rows installed before this migration carry
``broker_day_trade_marker IS NULL`` and the heuristic fallback keeps any
wrongly-True equity rows flagged True. To converge, operator should
re-import the most recent 30-day Cathay CSV after upgrade — the rehash
path propagates markers onto pre-existing rows and the live recompute
then clears the wrong flags. No auto-script is shipped; this is
intentional per ``openspec/changes/broker-day-trade-marker/design.md``
decision D3 (do not flip True→False without marker evidence).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "q4f5g6h7i8j9"
down_revision: Union[str, Sequence[str], None] = "p3e4f5g6h7i8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("broker_day_trade_marker", sa.String(length=8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transactions", "broker_day_trade_marker")
