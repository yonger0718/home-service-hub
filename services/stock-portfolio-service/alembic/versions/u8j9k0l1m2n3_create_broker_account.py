"""create broker_account table

Revision ID: u8j9k0l1m2n3
Revises: t7i8j9k0l1m2
Create Date: 2026-06-02 00:00:00.000000

Adds brokerage cash account metadata for the cash ledger feature. Account
rows are unique per broker nickname and retain opening balances so later
cash ledger rows can compute balances from a stable starting point.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "u8j9k0l1m2n3"
down_revision: Union[str, Sequence[str], None] = "t7i8j9k0l1m2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


BROKER_VALUES = ("cathay", "sinopac", "firstrade", "ib", "cs", "other")


def upgrade() -> None:
    broker_enum = postgresql.ENUM(*BROKER_VALUES, name="broker_enum")
    broker_enum.create(op.get_bind(), checkfirst=False)
    op.create_table(
        "broker_account",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "broker",
            postgresql.ENUM(*BROKER_VALUES, name="broker_enum", create_type=False),
            nullable=False,
        ),
        sa.Column("nickname", sa.String(length=64), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column(
            "opening_balance",
            sa.Numeric(20, 4),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "opening_date",
            sa.Date(),
            server_default=sa.text("CURRENT_DATE"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "broker", "nickname", name="uq_broker_account_broker_nickname"
        ),
    )
    op.create_index("ix_broker_account_id", "broker_account", ["id"])


def downgrade() -> None:
    op.drop_index("ix_broker_account_id", table_name="broker_account")
    op.drop_table("broker_account")
    postgresql.ENUM(name="broker_enum").drop(op.get_bind(), checkfirst=False)
