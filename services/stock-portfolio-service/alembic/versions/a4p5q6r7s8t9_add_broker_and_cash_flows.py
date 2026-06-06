"""add broker and cash flows

Revision ID: a4p5q6r7s8t9
Revises: z3o4p5q6r7s8
Create Date: 2026-06-06 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a4p5q6r7s8t9"
down_revision: Union[str, Sequence[str], None] = "z3o4p5q6r7s8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BROKERS = (
    "TW_CATHAY",
    "TW_SINOPAC",
    "TW_MANUAL",
    "IB",
    "FIRSTRADE",
    "SCHWAB",
    "FOREIGN_MANUAL",
)
CASH_FLOW_TYPES = ("deposit", "withdrawal", "interest", "dividend_cash", "fee")
BROKER_SQL = ", ".join(f"'{broker}'" for broker in BROKERS)
CASH_TYPE_SQL = ", ".join(f"'{type_}'" for type_ in CASH_FLOW_TYPES)


def upgrade() -> None:
    op.add_column("transactions", sa.Column("broker", sa.String(length=32), nullable=True))
    op.create_check_constraint(
        "ck_transactions_broker",
        "transactions",
        f"broker IS NULL OR broker IN ({BROKER_SQL})",
    )
    op.execute("UPDATE transactions SET broker='TW_MANUAL' WHERE broker IS NULL")
    op.create_table(
        "broker_cash_flows",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("broker", sa.String(length=32), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("fx_rate_to_twd", sa.Numeric(20, 8), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("import_fingerprint", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            f"broker IN ({BROKER_SQL})",
            name="ck_broker_cash_flows_broker",
        ),
        sa.CheckConstraint(
            f"type IN ({CASH_TYPE_SQL})",
            name="ck_broker_cash_flows_type",
        ),
        sa.UniqueConstraint(
            "import_fingerprint",
            name="uq_broker_cash_flows_import_fingerprint",
        ),
    )
    op.create_index(
        op.f("ix_broker_cash_flows_id"),
        "broker_cash_flows",
        ["id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_broker_cash_flows_id"), table_name="broker_cash_flows")
    op.drop_table("broker_cash_flows")
    op.drop_constraint("ck_transactions_broker", "transactions", type_="check")
    op.drop_column("transactions", "broker")
