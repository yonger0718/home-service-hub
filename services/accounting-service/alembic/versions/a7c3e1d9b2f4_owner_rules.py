"""reconciliation_owner_rule: owner-taught rules the inbox rule runner applies to recurring lines

Inbox spec §6: one row per (account, line kind, merchant pattern, flow sign, amount); a NULL amount means "any
amount" and collides with another NULL (NULLS NOT DISTINCT, Postgres 15+).

Revision ID: a7c3e1d9b2f4
Revises: f3b1d2c4a9e7
Create Date: 2026-10-21 09:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "a7c3e1d9b2f4"
down_revision: Union[str, Sequence[str], None] = "f3b1d2c4a9e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reconciliation_owner_rule",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("account_id", sa.Integer, sa.ForeignKey("account.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("line_kind", sa.String(32), nullable=False),
        sa.Column("merchant_pattern", sa.String(256), nullable=False),
        sa.Column("flow_sign", sa.String(1), nullable=False),
        sa.Column("amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("params", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_from_case_id", sa.Integer, sa.ForeignKey("reconciliation_case.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("note", sa.Text, nullable=True),
    )
    op.create_index(
        "uq_reconciliation_owner_rule_key", "reconciliation_owner_rule",
        ["account_id", "line_kind", "merchant_pattern", "flow_sign", "amount"],
        unique=True, postgresql_nulls_not_distinct=True,
    )
    op.create_index("ix_reconciliation_owner_rule_account", "reconciliation_owner_rule", ["account_id"])


def downgrade() -> None:
    op.drop_table("reconciliation_owner_rule")
