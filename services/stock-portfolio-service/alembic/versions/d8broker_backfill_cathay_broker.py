"""國泰 rows imported with a NULL broker become TW_CATHAY (#222)."""
from alembic import op

revision = "d8broker"
down_revision = "c7points"
branch_labels = None
depends_on = None


def upgrade():
    # Same rule as a4p5q6r7s8t9; stock-dividend receipt rows (price 0) have no known broker and stay NULL.
    op.execute(
        "UPDATE transactions SET broker='TW_CATHAY' WHERE broker IS NULL AND market='TW' AND price > 0"
    )


def downgrade():
    # The pre-upgrade NULLs are not recorded; TW_CATHAY_FILTER reads both values the same way.
    pass
