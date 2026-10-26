"""statement_auto_pay: provenance anchor for system-booked card-payment transfers

One row per card statement the auto-pay job has considered. A `booked` row anchors the transfer pair (out/in ledger
entries, shared `transfer_group_id`) the system created; the other statuses record why a statement was retired, left
alone after an owner edit/delete, or skipped. The exclusion constraint allows at most one booked payment per card in
any 20-day window (spec §4.2). `btree_gist` is needed to mix `=` on the card with `&&` on the date range.

Revision ID: c4a7e2d9f1b3
Revises: b8d4f2a1c6e3
Create Date: 2026-10-26 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c4a7e2d9f1b3"
down_revision: Union[str, Sequence[str], None] = "b8d4f2a1c6e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.create_table(
        "statement_auto_pay",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("statement_id", sa.Integer(), sa.ForeignKey("account_statement.id", ondelete="RESTRICT"),
                  nullable=False, unique=True),
        sa.Column("card_account_id", sa.Integer(), sa.ForeignKey("account.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("pay_account_id", sa.Integer(), sa.ForeignKey("account.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("pay_date", sa.Date(), nullable=False),
        sa.Column("transfer_group_id", sa.Uuid(), nullable=True),
        sa.Column("out_entry_id", sa.Integer(), sa.ForeignKey("ledger_entry.id", ondelete="SET NULL"), nullable=True),
        sa.Column("in_entry_id", sa.Integer(), sa.ForeignKey("ledger_entry.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(64), nullable=True),
        sa.Column("settings_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('booked', 'retired', 'owner_deleted', 'owner_edited', 'skipped')",
                           name="ck_statement_auto_pay_status"),
    )
    op.create_index("ix_statement_auto_pay_card_account_id", "statement_auto_pay", ["card_account_id"])
    op.create_index("ix_statement_auto_pay_status", "statement_auto_pay", ["status"])
    op.execute(
        "ALTER TABLE statement_auto_pay ADD CONSTRAINT ex_statement_auto_pay_card_window "
        "EXCLUDE USING gist (card_account_id WITH =, daterange(pay_date, pay_date + 20) WITH &&) "
        "WHERE (status = 'booked')"
    )


def downgrade() -> None:
    op.drop_table("statement_auto_pay")
