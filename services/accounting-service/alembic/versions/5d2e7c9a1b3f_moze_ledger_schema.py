"""replace legacy accounting tables with the MOZE ledger schema

Revision ID: 5d2e7c9a1b3f
Revises: 8a4c4f9b2d1b
Create Date: 2026-10-01 18:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "5d2e7c9a1b3f"
down_revision: Union[str, Sequence[str], None] = "8a4c4f9b2d1b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Drop order respects foreign keys (children first).
LEGACY_TABLES = (
    "transactions",
    "subscriptions",
    "installments",
    "credit_cards",
    "payment_methods",
    "categories",
)

ENTRY_KINDS = (
    "expense",
    "income",
    "transfer_out",
    "transfer_in",
    "receivable",
    "payable",
    "balance_adjustment",
    "fee",
    "discount",
    "reward",
    "interest",
    "refund",
)
ENTRY_SOURCES = ("moze_import", "manual", "hermes")
IMPORT_STATUSES = ("running", "succeeded", "failed")
FX_SOURCES = ("fx_api", "moze_backup")

entry_kind = postgresql.ENUM(*ENTRY_KINDS, name="entry_kind", create_type=False)
entry_source = postgresql.ENUM(*ENTRY_SOURCES, name="entry_source", create_type=False)
import_status = postgresql.ENUM(*IMPORT_STATUSES, name="import_status", create_type=False)
fx_source = postgresql.ENUM(*FX_SOURCES, name="fx_source", create_type=False)


def _timestamp_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
    ]


def _lock_legacy_tables(connection: sa.Connection) -> None:
    """Block legacy writers until this transaction ends, so the emptiness check cannot go stale."""
    inspector = sa.inspect(connection)
    for table_name in LEGACY_TABLES:
        if inspector.has_table(table_name):
            connection.execute(sa.text(f'LOCK TABLE "{table_name}" IN ACCESS EXCLUSIVE MODE'))


def _assert_legacy_tables_empty(connection: sa.Connection) -> None:
    inspector = sa.inspect(connection)
    non_empty = [
        table_name
        for table_name in LEGACY_TABLES
        if inspector.has_table(table_name)
        and connection.execute(sa.text(f'SELECT EXISTS (SELECT 1 FROM "{table_name}")')).scalar_one()
    ]
    if non_empty:
        raise RuntimeError(
            "refusing to drop non-empty legacy accounting tables: " + ", ".join(non_empty)
        )


def upgrade() -> None:
    connection = op.get_bind()
    _lock_legacy_tables(connection)
    _assert_legacy_tables_empty(connection)

    inspector = sa.inspect(connection)
    for table_name in LEGACY_TABLES:
        if inspector.has_table(table_name):
            op.drop_table(table_name)

    postgresql.ENUM(*ENTRY_KINDS, name="entry_kind").create(connection)
    postgresql.ENUM(*ENTRY_SOURCES, name="entry_source").create(connection)
    postgresql.ENUM(*IMPORT_STATUSES, name="import_status").create(connection)
    postgresql.ENUM(*FX_SOURCES, name="fx_source").create(connection)
    op.execute("CREATE SEQUENCE ledger_entry_seq_seq AS BIGINT")

    op.create_table(
        "account",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("opening_balance", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        *_timestamp_columns(),
    )
    op.create_table(
        "category",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", entry_kind, nullable=False),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("category.id"), nullable=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "kind",
            "parent_id",
            "name",
            name="uq_category_kind_parent_name",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_table(
        "project",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
    )
    op.create_table(
        "import_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("status", import_status, nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("summary", postgresql.JSONB(), nullable=True),
    )
    op.create_table(
        "ledger_entry",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("account.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("kind", entry_kind, nullable=False),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("original_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("original_currency", sa.String(8), nullable=True),
        sa.Column("fx_rate", sa.Numeric(20, 10), nullable=True),
        sa.Column("fx_source", fx_source, nullable=True),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("entry_time", sa.Time(), nullable=True),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("category.id"), nullable=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id"), nullable=True),
        sa.Column("name", sa.String(128), nullable=True),
        sa.Column("merchant", sa.String(128), nullable=True),
        sa.Column("counterparty", sa.String(128), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'::text[]")),
        sa.Column("parent_entry_id", sa.Integer(), sa.ForeignKey("ledger_entry.id"), nullable=True),
        sa.Column("transfer_group_id", sa.Uuid(), nullable=True),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("source", entry_source, nullable=False),
        sa.Column("import_run_id", sa.Integer(), sa.ForeignKey("import_run.id"), nullable=True),
        sa.Column(
            "seq",
            sa.BigInteger(),
            nullable=False,
            unique=True,
            server_default=sa.text("nextval('ledger_entry_seq_seq')"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.execute("ALTER SEQUENCE ledger_entry_seq_seq OWNED BY ledger_entry.seq")
    op.create_index(
        "ix_ledger_entry_account_order",
        "ledger_entry",
        ["account_id", "entry_date", "entry_time", "seq"],
    )
    op.create_index("ix_ledger_entry_transfer_group_id", "ledger_entry", ["transfer_group_id"])
    op.create_index("ix_ledger_entry_counterparty", "ledger_entry", ["counterparty"])
    op.create_index("ix_ledger_entry_source", "ledger_entry", ["source"])
    op.create_table(
        "fx_rate",
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("base", sa.String(8), primary_key=True),
        sa.Column("quote", sa.String(8), primary_key=True),
        sa.Column("rate", sa.Numeric(20, 10), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("fx_rate")
    op.drop_table("ledger_entry")
    op.drop_table("import_run")
    op.drop_table("project")
    op.drop_table("category")
    op.drop_table("account")
    op.execute("DROP TYPE fx_source")
    op.execute("DROP TYPE import_status")
    op.execute("DROP TYPE entry_source")
    op.execute("DROP TYPE entry_kind")

    # Restore the legacy schema exactly as revision 8a4c4f9b2d1b left it.
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=True, unique=True),
        sa.Column("color", sa.String(), nullable=True),
        *_timestamp_columns(),
    )
    op.create_table(
        "credit_cards",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=True, unique=True),
        sa.Column("billing_day", sa.Integer(), nullable=True),
        sa.Column("reward_cycle_type", sa.String(), nullable=True),
        sa.Column("alert_threshold", sa.Integer(), nullable=True),
        sa.Column("default_payment_method", sa.String(), nullable=True),
        *_timestamp_columns(),
    )
    op.create_table(
        "payment_methods",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=True, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        *_timestamp_columns(),
    )
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("amount", sa.Integer(), nullable=True),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("sub_type", sa.String(), nullable=True),
        sa.Column("payment_method", sa.String(), nullable=True),
        sa.Column("day_of_month", sa.Integer(), nullable=True),
        sa.Column("card_id", sa.Integer(), sa.ForeignKey("credit_cards.id"), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=True),
        *_timestamp_columns(),
    )
    op.create_table(
        "installments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("total_amount", sa.Integer(), nullable=True),
        sa.Column("monthly_amount", sa.Integer(), nullable=True),
        sa.Column("payment_method", sa.String(), nullable=True),
        sa.Column("total_periods", sa.Integer(), nullable=True),
        sa.Column("remaining_periods", sa.Integer(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("card_id", sa.Integer(), sa.ForeignKey("credit_cards.id"), nullable=True),
        *_timestamp_columns(),
    )
    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("date", sa.Date(), server_default=sa.text("CURRENT_DATE"), nullable=True),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("item", sa.String(), nullable=True),
        sa.Column("paid_amount", sa.Integer(), nullable=True),
        sa.Column("transaction_amount", sa.Integer(), nullable=True),
        sa.Column("payment_method", sa.String(), nullable=True),
        sa.Column("card_id", sa.Integer(), sa.ForeignKey("credit_cards.id"), nullable=True),
        sa.Column("transaction_type", sa.String(), nullable=True),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("related_transaction_id", sa.Integer(), sa.ForeignKey("transactions.id"), nullable=True),
        sa.Column("subscription_id", sa.Integer(), sa.ForeignKey("subscriptions.id"), nullable=True),
        sa.Column("installment_id", sa.Integer(), sa.ForeignKey("installments.id"), nullable=True),
        *_timestamp_columns(),
    )
    op.create_index("ix_categories_id", "categories", ["id"])
    op.create_index("ix_categories_name", "categories", ["name"])
    op.create_index("ix_credit_cards_id", "credit_cards", ["id"])
    op.create_index("ix_credit_cards_name", "credit_cards", ["name"])
    op.create_index("ix_payment_methods_id", "payment_methods", ["id"])
    op.create_index("ix_payment_methods_name", "payment_methods", ["name"])
    op.create_index("ix_subscriptions_id", "subscriptions", ["id"])
    op.create_index("ix_installments_id", "installments", ["id"])
    op.create_index("ix_transactions_id", "transactions", ["id"])
