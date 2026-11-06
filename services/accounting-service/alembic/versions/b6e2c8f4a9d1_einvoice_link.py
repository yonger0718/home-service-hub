"""einvoice_link: e-invoice tables, enums and the unlink trigger

Revision ID: b6e2c8f4a9d1
Revises: a3d9e5c1f7b2
Create Date: 2026-11-06 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "b6e2c8f4a9d1"
down_revision: Union[str, Sequence[str], None] = "a3d9e5c1f7b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Copied verbatim from app/models/einvoice.py (migrations do not import app code); parity is unit-tested.
EINVOICE_UNLINK_FUNCTION_SQL = """
CREATE FUNCTION einvoice_link_deleted() RETURNS trigger AS $$
BEGIN
    UPDATE einvoice SET state = 'unmatched', dismiss_reason = NULL, updated_at = now()
    WHERE id = OLD.invoice_id AND state = 'linked';
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""

EINVOICE_UNLINK_TRIGGER_SQL = """
CREATE TRIGGER trg_einvoice_link_deleted AFTER DELETE ON einvoice_link
FOR EACH ROW EXECUTE FUNCTION einvoice_link_deleted();
"""

ENUMS = (
    ("einvoice_state", ("unmatched", "ambiguous", "near", "linked", "dismissed", "void")),
    ("einvoice_dismiss_reason", ("owner", "seller_muted", "runbook")),
    ("einvoice_linked_by", ("auto", "owner")),
)


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(name=name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    for name, values in ENUMS:
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=False)

    op.create_table(
        "einvoice",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("number", sa.String(10), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("period", postgresql.CHAR(5), nullable=False),
        sa.Column("seller_tax_id", sa.String(16), nullable=False, server_default=sa.text("''")),
        sa.Column("seller_name", sa.String(128), nullable=False),
        sa.Column("total_amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("status_raw", sa.String(16), nullable=False),
        sa.Column("is_void", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("state", _enum("einvoice_state"), nullable=False, server_default=sa.text("'unmatched'")),
        sa.Column("dismiss_reason", _enum("einvoice_dismiss_reason"), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("source_drive_id", sa.String(128), nullable=False),
        sa.UniqueConstraint("number", "period", name="ux_einvoice_number_period"),
        sa.CheckConstraint("(state = 'dismissed') = (dismiss_reason IS NOT NULL)", name="ck_einvoice_dismiss_reason"),
        sa.CheckConstraint("NOT is_void OR state = 'void'", name="ck_einvoice_void_state"),
    )
    op.create_index("ix_einvoice_state", "einvoice", ["state"])
    op.create_index("ix_einvoice_seller_tax_id", "einvoice", ["seller_tax_id"])

    op.create_table(
        "einvoice_item",
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("einvoice.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("seq", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("subtotal", sa.Numeric(20, 4), nullable=False),
    )

    op.create_table(
        "einvoice_link",
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("einvoice.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("entry_id", sa.Integer(), sa.ForeignKey("ledger_entry.id", ondelete="CASCADE"), nullable=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("entry_group.id", ondelete="CASCADE"), nullable=True),
        sa.Column("linked_by", _enum("einvoice_linked_by"), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("(entry_id IS NULL) <> (group_id IS NULL)", name="ck_einvoice_link_one_target"),
    )
    op.create_index("ux_einvoice_link_entry", "einvoice_link", ["entry_id"], unique=True,
                    postgresql_where=sa.text("entry_id IS NOT NULL"))
    op.create_index("ux_einvoice_link_group", "einvoice_link", ["group_id"], unique=True,
                    postgresql_where=sa.text("group_id IS NOT NULL"))
    op.execute(EINVOICE_UNLINK_FUNCTION_SQL)
    op.execute(EINVOICE_UNLINK_TRIGGER_SQL)

    op.create_table(
        "einvoice_seller_mute",
        sa.Column("seller_tax_id", sa.String(16), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("seller_tax_id <> ''", name="ck_einvoice_seller_mute_tax_id"),
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_einvoice_link_deleted ON einvoice_link")
    op.execute("DROP FUNCTION IF EXISTS einvoice_link_deleted()")
    op.drop_table("einvoice_seller_mute")
    op.drop_table("einvoice_link")
    op.drop_table("einvoice_item")
    op.drop_table("einvoice")
    bind = op.get_bind()
    for name, _values in reversed(ENUMS):
        postgresql.ENUM(name=name).drop(bind, checkfirst=False)
