"""add fee/tax/source columns to dividends

Revision ID: k8f9a0b1c2d3
Revises: j7e8f9a0b1c2
Create Date: 2026-05-15 12:00:00.000000

Adds columns required for auto-recording cash + stock dividends from
TWSE / TPEx events:

- ``fee NUMERIC(12,2) NOT NULL DEFAULT 0`` — handling fee. Default
  NT$10 is applied at the service layer, not as a server default, so
  manual rows still default to 0.
- ``tax NUMERIC(12,2) NOT NULL DEFAULT 0`` — 二代健保 supplementary
  premium (NHI surtax). Auto-computed in the service layer when the
  gross cash amount exceeds NT$20,000.
- ``cash_dividend_per_share NUMERIC(12,4) NULL`` — per-share rate
  carried over from the upstream event for audit / dialog hint.
- ``stock_dividend_shares INTEGER NOT NULL DEFAULT 0`` — whole shares
  awarded for the stock-dividend leg of the same event (``floor(qty *
  stockDividendPerThousand / 1000)``).
- ``source VARCHAR(32) NULL`` — e.g. ``auto:TWT49U``, ``manual``,
  ``csv``.
- ``quantity_at_record_date NUMERIC(18,4) NULL`` — quantity used to
  compute ``amount`` so future fee/tax edits can recompute if needed.

Two new CHECK constraints enforce non-negative fee + tax to match the
shape of the existing ``transactions`` constraints.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "k8f9a0b1c2d3"
down_revision: Union[str, Sequence[str], None] = "j7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "dividends",
        sa.Column(
            "fee",
            sa.Numeric(12, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "dividends",
        sa.Column(
            "tax",
            sa.Numeric(12, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "dividends",
        sa.Column("cash_dividend_per_share", sa.Numeric(12, 4), nullable=True),
    )
    op.add_column(
        "dividends",
        sa.Column(
            "stock_dividend_shares",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "dividends",
        sa.Column("source", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "dividends",
        sa.Column("quantity_at_record_date", sa.Numeric(18, 4), nullable=True),
    )

    op.create_check_constraint(
        "ck_dividends_fee_nonnegative",
        "dividends",
        "coalesce(fee, 0) >= 0",
    )
    op.create_check_constraint(
        "ck_dividends_tax_nonnegative",
        "dividends",
        "coalesce(tax, 0) >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_dividends_tax_nonnegative", "dividends", type_="check")
    op.drop_constraint("ck_dividends_fee_nonnegative", "dividends", type_="check")
    op.drop_column("dividends", "quantity_at_record_date")
    op.drop_column("dividends", "source")
    op.drop_column("dividends", "stock_dividend_shares")
    op.drop_column("dividends", "cash_dividend_per_share")
    op.drop_column("dividends", "tax")
    op.drop_column("dividends", "fee")
