"""Cathay daily statement ownership and run tables."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b6cathay"
down_revision = "a5receipt"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade():
    op.create_table(
        "cathay_statement_day",
        sa.Column("trade_date", sa.Date(), primary_key=True),
        sa.Column("accepted_kind", sa.String(16), nullable=True),
        sa.Column("accepted_sha256", sa.String(64), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canonical_lines", _JSON, nullable=True),
        sa.Column("last_sha256", sa.String(64), nullable=True),
        sa.Column("last_status", sa.String(16), nullable=True),
        sa.Column("last_reason", sa.String(80), nullable=True),
        sa.Column("last_detail", _JSON, nullable=True),
        sa.Column("last_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ack_sha256", sa.String(64), nullable=True),
        sa.Column("ack_status", sa.String(16), nullable=True),
        sa.Column("ack_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("drive_file_id", sa.String(128), nullable=True),
        sa.CheckConstraint(
            "accepted_kind IS NULL OR accepted_kind IN ('imported', 'verified')",
            name="ck_cathay_statement_day_accepted_kind",
        ),
        sa.CheckConstraint(
            "last_status IS NULL OR last_status IN ('imported', 'verified', 'duplicate', 'mismatch', 'held', 'conflict')",
            name="ck_cathay_statement_day_last_status",
        ),
    )
    op.create_table(
        "cathay_statement_run",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(8), nullable=False, unique=True),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", sa.String(8), nullable=False),
        sa.Column("code", sa.String(64), nullable=True),
        sa.Column("files_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("posted", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("kind IN ('latest', 'ok')", name="ck_cathay_statement_run_kind"),
        sa.CheckConstraint("result IN ('ok', 'latched', 'failed')", name="ck_cathay_statement_run_result"),
    )


def downgrade():
    op.drop_table("cathay_statement_run")
    op.drop_table("cathay_statement_day")
