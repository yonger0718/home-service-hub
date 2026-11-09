"""account_cash_equivalent: 1 點 = 1 元 flag on account

Revision ID: c4e8a1d7b3f9
Revises: b6e2c8f4a9d1
Create Date: 2026-11-09 02:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c4e8a1d7b3f9"
down_revision: Union[str, Sequence[str], None] = "b6e2c8f4a9d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("account", sa.Column("cash_equivalent", sa.Boolean(), nullable=False, server_default=sa.text("false")))


def downgrade() -> None:
    op.drop_column("account", "cash_equivalent")
