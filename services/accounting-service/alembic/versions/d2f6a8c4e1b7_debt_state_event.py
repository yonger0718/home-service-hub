"""debt_state_event: one row per close / reopen of a debt (#401)

No foreign key to ledger_entry on purpose: the event outlives the entry, and the entry delete path keeps its lock
order and FK actions. No data change.

Revision ID: d2f6a8c4e1b7
Revises: c4e8a1d7b3f9
Create Date: 2026-11-20 04:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d2f6a8c4e1b7"
down_revision: Union[str, Sequence[str], None] = "c4e8a1d7b3f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "debt_state_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entry_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(8), nullable=False),
        sa.Column("actor", sa.String(64), nullable=True),
        sa.Column("open_amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("entry_kind", sa.String(16), nullable=False),
        sa.Column("entry_name", sa.String(128), nullable=True),
        sa.Column("counterparty", sa.String(128), nullable=True),
        sa.Column("entry_amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("action IN ('close', 'reopen')", name="ck_debt_state_event_action"),
    )
    op.create_index("ix_debt_state_event_entry_id", "debt_state_event", ["entry_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_debt_state_event_entry_id", table_name="debt_state_event")
    op.drop_table("debt_state_event")
