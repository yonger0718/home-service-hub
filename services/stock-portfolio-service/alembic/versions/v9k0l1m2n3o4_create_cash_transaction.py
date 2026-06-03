"""create cash_transaction table

Revision ID: v9k0l1m2n3o4
Revises: u8j9k0l1m2n3
Create Date: 2026-06-02 00:05:00.000000

Adds the signed cash ledger keyed to broker accounts, with optional links
back to trade and dividend source rows. The import fingerprint constraint
provides idempotency for manual, CSV, and backfill writes.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "v9k0l1m2n3o4"
down_revision: Union[str, Sequence[str], None] = "u8j9k0l1m2n3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CASH_TXN_TYPE_VALUES = (
    "deposit",
    "withdraw",
    "buy_settle",
    "sell_settle",
    "fee",
    "tax",
    "dividend_cash",
    "interest_in",
    "margin_interest",
    "wire_fee",
    "fx_convert",
)
CASH_TXN_SOURCE_VALUES = ("manual", "csv_import", "auto_derive")


def upgrade() -> None:
    cash_txn_type_enum = postgresql.ENUM(
        *CASH_TXN_TYPE_VALUES, name="cash_txn_type_enum"
    )
    cash_txn_source_enum = postgresql.ENUM(
        *CASH_TXN_SOURCE_VALUES, name="cash_txn_source_enum"
    )
    cash_txn_type_enum.create(op.get_bind(), checkfirst=False)
    cash_txn_source_enum.create(op.get_bind(), checkfirst=False)
    op.create_table(
        "cash_transaction",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("txn_date", sa.Date(), nullable=False),
        sa.Column(
            "type",
            postgresql.ENUM(
                *CASH_TXN_TYPE_VALUES,
                name="cash_txn_type_enum",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("related_transaction_id", sa.Integer(), nullable=True),
        sa.Column("related_dividend_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column(
            "source",
            postgresql.ENUM(
                *CASH_TXN_SOURCE_VALUES,
                name="cash_txn_source_enum",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("import_fingerprint", sa.String(length=128), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["broker_account.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["related_transaction_id"],
            ["transactions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["related_dividend_id"],
            ["dividends.id"],
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "import_fingerprint", name="uq_cash_transaction_import_fingerprint"
        ),
    )
    op.create_index("ix_cash_transaction_id", "cash_transaction", ["id"])
    op.create_index("ix_cash_transaction_account_id", "cash_transaction", ["account_id"])
    op.create_index("ix_cash_transaction_txn_date", "cash_transaction", ["txn_date"])
    op.create_index(
        "ix_cash_transaction_related_transaction_id",
        "cash_transaction",
        ["related_transaction_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_cash_transaction_related_transaction_id", table_name="cash_transaction"
    )
    op.drop_index("ix_cash_transaction_txn_date", table_name="cash_transaction")
    op.drop_index("ix_cash_transaction_account_id", table_name="cash_transaction")
    op.drop_index("ix_cash_transaction_id", table_name="cash_transaction")
    op.drop_table("cash_transaction")
    postgresql.ENUM(name="cash_txn_source_enum").drop(op.get_bind(), checkfirst=False)
    postgresql.ENUM(name="cash_txn_type_enum").drop(op.get_bind(), checkfirst=False)
