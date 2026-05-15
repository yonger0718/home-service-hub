"""add corporate_actions table

Revision ID: i6d7e8f9a0b1
Revises: h5c6d7e8f9a0
Create Date: 2026-05-15 12:00:00.000000

TWSE face-value-change events. ``source_event_key`` is unique per
``(symbol, effective_date)`` and acts as the idempotency anchor for
re-running annual backfills.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "i6d7e8f9a0b1"
down_revision: Union[str, Sequence[str], None] = "h5c6d7e8f9a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "corporate_actions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column(
            "action_type",
            sa.String(length=32),
            nullable=False,
            server_default="FACE_VALUE_CHANGE",
        ),
        sa.Column("ratio", sa.Numeric(18, 8), nullable=False),
        sa.Column(
            "source",
            sa.String(length=32),
            nullable=False,
            server_default="TWSE",
        ),
        sa.Column("source_event_key", sa.String(length=128), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("source_event_key", name="uq_corporate_actions_event_key"),
        sa.CheckConstraint("ratio > 0", name="ck_corporate_actions_ratio_positive"),
    )
    op.create_index(
        "ix_corporate_actions_symbol_date",
        "corporate_actions",
        ["symbol", "effective_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_corporate_actions_symbol_date", table_name="corporate_actions")
    op.drop_table("corporate_actions")
