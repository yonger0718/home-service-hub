"""add day-trade flag and import fingerprint columns

Revision ID: f3a4b5c6d7e8
Revises: e2f3g4h5i6j7
Create Date: 2026-05-15 09:00:00.000000

Adds two new columns each on ``transactions`` and ``dividends`` to support
upcoming CSV import + day-trade detection features:

- ``transactions.is_day_trade`` BOOLEAN NOT NULL DEFAULT FALSE — auto-set when
  a BUY and SELL of the same symbol occur on the same calendar trade date in
  the same account. Used for TW half-rate transaction-tax cost estimates
  (沖賣 half-tax).
- ``transactions.import_fingerprint`` VARCHAR(64) NULL UNIQUE — SHA256 hex of
  the canonical CSV row. NULL for manually-entered rows; populated by the
  importer. UNIQUE prevents duplicate re-imports of the same statement.
- ``dividends.import_fingerprint`` VARCHAR(64) NULL UNIQUE — same idempotency
  pattern for dividend CSV imports.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, Sequence[str], None] = "e2f3g4h5i6j7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column(
            "is_day_trade",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "transactions",
        sa.Column("import_fingerprint", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_transactions_import_fingerprint",
        "transactions",
        ["import_fingerprint"],
    )

    op.add_column(
        "dividends",
        sa.Column("import_fingerprint", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_dividends_import_fingerprint",
        "dividends",
        ["import_fingerprint"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_dividends_import_fingerprint", "dividends", type_="unique"
    )
    op.drop_column("dividends", "import_fingerprint")

    op.drop_constraint(
        "uq_transactions_import_fingerprint", "transactions", type_="unique"
    )
    op.drop_column("transactions", "import_fingerprint")
    op.drop_column("transactions", "is_day_trade")
