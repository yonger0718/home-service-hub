"""statement_event.flag: a nullable marker set by lineage transfer (e.g. 'text_changed' for a normalised re-parse)

Design §5.7: a `normalised` pairing transfers the event's coverage and flags the line 文字已變更.

Revision ID: f3b1d2c4a9e7
Revises: e2a9c4d1b7f0
Create Date: 2026-10-09 20:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "f3b1d2c4a9e7"
down_revision: Union[str, Sequence[str], None] = "e2a9c4d1b7f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("statement_event", sa.Column("flag", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("statement_event", "flag")
