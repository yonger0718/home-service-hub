"""Transactions paid entirely with 小樹點數 (#219)."""
from alembic import op
import sqlalchemy as sa

revision = "c7points"
down_revision = "b6cathay"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "transactions",
        sa.Column("paid_with_points", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column("transactions", "paid_with_points")
