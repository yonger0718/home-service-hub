"""account_logo_image: uploaded, server-normalised account logos (bytea)

Revision ID: e9b1c3d7a5f2
Revises: c7a2e9f4b1d8
Create Date: 2026-11-02 09:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e9b1c3d7a5f2"
down_revision: Union[str, Sequence[str], None] = "c7a2e9f4b1d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "account_logo_image",
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("account.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("png", sa.LargeBinary(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("source_media_type", sa.String(32), nullable=False),
        sa.Column("source_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("account_logo_image")
