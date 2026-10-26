"""account_logo: optional brand-logo slug beside the emoji icon

`account.logo` holds a slug of the SPA's built-in brand-logo set (shape `^[a-z0-9-]{1,32}$`, validated by the API).

Revision ID: b8d4f2a1c6e3
Revises: a7c3e1d9b2f4
Create Date: 2026-10-26 09:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b8d4f2a1c6e3"
down_revision: Union[str, Sequence[str], None] = "a7c3e1d9b2f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("account", sa.Column("logo", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("account", "logo")
