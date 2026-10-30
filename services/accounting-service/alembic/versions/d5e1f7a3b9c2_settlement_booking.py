"""settlement_booking: owner table for brokerage settlement / dividend transfers, account feed sources, case kinds

Additive only. `account.settlement_source` / `account.valuation_source` name the portfolio feeds an account follows.
`settlement_booking` (+ `_item`) is the auto-pay-shaped provenance of the transfers booked from those feeds; its
partial unique indexes reserve a fingerprint (and, for settlements, an account-day) only while a row is `booked`.
`case_kind` gains `settlement` and `dividend`; PostgreSQL cannot drop enum values, so the downgrade leaves them.

Revision ID: d5e1f7a3b9c2
Revises: c4a7e2d9f1b3
Create Date: 2026-10-30 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "d5e1f7a3b9c2"
down_revision: Union[str, Sequence[str], None] = "c4a7e2d9f1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The new case_kind values in their own committed step (ALTER TYPE ... ADD VALUE cannot be used in the same
    # transaction).
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE case_kind ADD VALUE IF NOT EXISTS 'settlement'")
        op.execute("ALTER TYPE case_kind ADD VALUE IF NOT EXISTS 'dividend'")

    op.add_column("account", sa.Column("settlement_source", sa.String(64), nullable=True))
    op.add_column("account", sa.Column("valuation_source", sa.String(64), nullable=True))

    op.create_table(
        "settlement_booking",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("account.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("target_account_id", sa.Integer(), sa.ForeignKey("account.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("statement_event.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=True),
        sa.Column("settlement_date", sa.Date(), nullable=False),
        sa.Column("net_amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("facts", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("transfer_group_id", sa.Uuid(), nullable=True),
        sa.Column("out_entry_id", sa.Integer(), sa.ForeignKey("ledger_entry.id", ondelete="SET NULL"), nullable=True),
        sa.Column("in_entry_id", sa.Integer(), sa.ForeignKey("ledger_entry.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(64), nullable=True),
        sa.Column("action_id", sa.Integer(), sa.ForeignKey("reconciliation_action.id", ondelete="RESTRICT"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('booked', 'retired', 'replaced', 'owner_deleted', 'owner_edited', 'skipped')",
                           name="ck_settlement_booking_status"),
    )
    op.create_index("ix_settlement_booking_event_id", "settlement_booking", ["event_id"])
    op.create_index("ux_settlement_booking_fingerprint_booked", "settlement_booking", ["fingerprint"], unique=True,
                    postgresql_where=sa.text("status = 'booked'"))
    op.create_index("ux_settlement_booking_day_booked", "settlement_booking", ["account_id", "settlement_date"],
                    unique=True, postgresql_where=sa.text("status = 'booked' AND kind = 'settlement'"))

    op.create_table(
        "settlement_booking_item",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("booking_id", sa.Integer(), sa.ForeignKey("settlement_booking.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),
        sa.UniqueConstraint("source", "source_id", name="ux_settlement_booking_item_source"),
    )


def downgrade() -> None:
    op.drop_table("settlement_booking_item")
    op.drop_table("settlement_booking")
    op.drop_column("account", "valuation_source")
    op.drop_column("account", "settlement_source")
