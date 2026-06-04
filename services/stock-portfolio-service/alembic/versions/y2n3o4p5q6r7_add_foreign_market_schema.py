"""add foreign market schema

Revision ID: y2n3o4p5q6r7
Revises: x1m2n3o4p5q6
Create Date: 2026-06-04 00:00:00.000000

Phase 1 foreign-market schema foundation. This revision is intended to run
atomically in PostgreSQL under Alembic's transaction. Existing TW rows are
backfilled by NOT NULL defaults. Widening transactions.price and
dividends.amount is metadata-only in PostgreSQL, while transactions.quantity
Integer -> Numeric(18, 4) rewrites the transactions table; this is acceptable
for the current ~50k-row production size.

Downgrade reverses the schema while Phase 1 contains only TW rows. Once
foreign or fractional-share rows exist, downgrade becomes risky because
quantity narrows back to Integer and foreign market/currency fields are
dropped.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "y2n3o4p5q6r7"
down_revision: Union[str, Sequence[str], None] = "x1m2n3o4p5q6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("market", sa.String(length=8), nullable=False, server_default="TW"),
    )
    op.add_column(
        "transactions",
        sa.Column("currency", sa.CHAR(length=3), nullable=False, server_default="TWD"),
    )
    op.add_column(
        "transactions",
        sa.Column("fx_rate_to_twd", sa.Numeric(20, 8), nullable=True),
    )
    op.add_column(
        "dividends",
        sa.Column("market", sa.String(length=8), nullable=False, server_default="TW"),
    )
    op.add_column(
        "dividends",
        sa.Column("currency", sa.CHAR(length=3), nullable=False, server_default="TWD"),
    )
    op.add_column(
        "dividends",
        sa.Column("fx_rate_to_twd", sa.Numeric(20, 8), nullable=True),
    )
    op.add_column(
        "price_history",
        sa.Column("market", sa.String(length=8), nullable=False, server_default="TW"),
    )
    op.add_column(
        "corporate_actions",
        sa.Column("market", sa.String(length=8), nullable=False, server_default="TW"),
    )

    op.alter_column("symbol_map", "market", new_column_name="exchange")
    op.alter_column("symbol_map", "exchange", nullable=True, existing_type=sa.String(length=8))
    op.add_column(
        "symbol_map",
        sa.Column("market", sa.String(length=8), nullable=False, server_default="TW"),
    )

    op.alter_column(
        "transactions",
        "price",
        existing_type=sa.Numeric(12, 2),
        type_=sa.Numeric(18, 4),
        existing_nullable=False,
    )
    op.alter_column(
        "dividends",
        "amount",
        existing_type=sa.Numeric(12, 2),
        type_=sa.Numeric(18, 4),
        existing_nullable=False,
    )
    op.alter_column(
        "transactions",
        "quantity",
        existing_type=sa.Integer(),
        type_=sa.Numeric(18, 4),
        existing_nullable=False,
        postgresql_using="quantity::numeric(18,4)",
    )

    op.drop_constraint("pk_price_history", "price_history", type_="primary")
    op.create_primary_key(
        "pk_price_history", "price_history", ["symbol", "market", "date"]
    )

    op.drop_index("ix_transactions_symbol_trade_date", table_name="transactions")
    op.create_index(
        "ix_transactions_symbol_market_trade_date",
        "transactions",
        ["symbol", "market", "trade_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_transactions_symbol_market_trade_date", table_name="transactions")
    op.create_index(
        "ix_transactions_symbol_trade_date",
        "transactions",
        ["symbol", "trade_date"],
    )

    op.drop_constraint("pk_price_history", "price_history", type_="primary")
    op.create_primary_key("pk_price_history", "price_history", ["symbol", "date"])

    op.alter_column(
        "transactions",
        "quantity",
        existing_type=sa.Numeric(18, 4),
        type_=sa.Integer(),
        existing_nullable=False,
        postgresql_using="quantity::integer",
    )
    op.alter_column(
        "dividends",
        "amount",
        existing_type=sa.Numeric(18, 4),
        type_=sa.Numeric(12, 2),
        existing_nullable=False,
    )
    op.alter_column(
        "transactions",
        "price",
        existing_type=sa.Numeric(18, 4),
        type_=sa.Numeric(12, 2),
        existing_nullable=False,
    )

    op.drop_column("symbol_map", "market")
    op.alter_column("symbol_map", "exchange", nullable=False, existing_type=sa.String(length=8))
    op.alter_column("symbol_map", "exchange", new_column_name="market")

    op.drop_column("corporate_actions", "market")
    op.drop_column("price_history", "market")
    op.drop_column("dividends", "fx_rate_to_twd")
    op.drop_column("dividends", "currency")
    op.drop_column("dividends", "market")
    op.drop_column("transactions", "fx_rate_to_twd")
    op.drop_column("transactions", "currency")
    op.drop_column("transactions", "market")
