"""phase 2a: settings, groups, counterparties, reward rules, preference, posting date

Revision ID: 7b1e4a2c9d05
Revises: 5d2e7c9a1b3f
Create Date: 2026-10-02 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "7b1e4a2c9d05"
down_revision: Union[str, Sequence[str], None] = "5d2e7c9a1b3f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Values this revision adds to enums created by 5d2e7c9a1b3f. ADD VALUE runs inside the
# migration transaction (PostgreSQL 12+ allows it); nothing in upgrade() uses the new values,
# so no autocommit block is needed.
PHASE_1_ENTRY_SOURCES = ("moze_import", "manual", "hermes")
PHASE_1_FX_SOURCES = ("fx_api", "moze_backup")
ADDED_ENTRY_SOURCES = ("moze_backup", "rule")
ADDED_FX_SOURCES = ("manual",)

NEW_ENUMS = {
    "due_rule": ("fixed_day", "days_after_closing"),
    "rounding_mode": ("keep", "round", "floor", "ceil"),
    "entry_group_kind": ("split", "reward_claim", "installment"),
    "reward_method": ("percent", "fixed"),
    "reward_window": ("statement_cycle",),
    "reward_posting": ("after_window", "after_transaction", "manual"),
    "color_convention": ("red_green", "green_red"),
    "keypad_layout": ("calculator", "phone"),
    "moze_schedule_kind": ("period", "installment", "skipped_record"),
    "import_kind": ("moze_csv", "moze_backup"),
}


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*NEW_ENUMS[name], name=name, create_type=False)


POSTED_DATE_FUNCTION_SQL = """
CREATE FUNCTION ledger_entry_default_posted_date() RETURNS trigger AS $$
BEGIN
    IF NEW.posted_date IS NULL THEN
        NEW.posted_date := NEW.entry_date;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""
POSTED_DATE_TRIGGER_SQL = """
CREATE TRIGGER trg_ledger_entry_posted_date
BEFORE INSERT OR UPDATE ON ledger_entry
FOR EACH ROW EXECUTE FUNCTION ledger_entry_default_posted_date()
"""

# Same text as app.models.ledger.SETTLEMENT_SIGN_SQL (migrations do not import app code).
SETTLEMENT_SIGN_SQL = (
    "(is_settlement OR NOT ((kind = 'receivable' AND amount > 0) OR (kind = 'payable' AND amount < 0)))"
    " AND (NOT is_settlement OR kind IN ('receivable', 'payable'))"
)

NEW_ENTRY_COLUMNS = (
    "group_id", "settles_entry_id", "refunds_entry_id", "reward_rule_id", "reward_source_entry_id",
    "invoice_number", "invoice_random", "moze_id",
)

# Each check returns a count; any non-zero count blocks the downgrade because phase 1 cannot hold that data.
# _assert_downgradable runs all of them and raises one RuntimeError listing every non-zero count.
DOWNGRADE_GUARDS = {
    # One label per source phase 1 must not receive, so the message says what kind of rows block it.
    "ledger_entry rows with source = manual": "SELECT count(*) FROM ledger_entry WHERE source = 'manual'",
    "ledger_entry rows with source = hermes": "SELECT count(*) FROM ledger_entry WHERE source = 'hermes'",
    "ledger_entry rows with source = rule": "SELECT count(*) FROM ledger_entry WHERE source = 'rule'",
    "ledger_entry rows with source = moze_backup": "SELECT count(*) FROM ledger_entry WHERE source = 'moze_backup'",
    "ledger_entry rows with fx_source = manual":
        "SELECT count(*) FROM ledger_entry WHERE fx_source = 'manual'",
    "ledger_entry rows whose posted_date differs from entry_date":
        "SELECT count(*) FROM ledger_entry WHERE posted_date <> entry_date",
    "ledger_entry rows using phase 2a link columns":
        "SELECT count(*) FROM ledger_entry WHERE "
        + " OR ".join(f"{column} IS NOT NULL" for column in NEW_ENTRY_COLUMNS),
    "account_group rows": "SELECT count(*) FROM account_group",
    "entry_group rows": "SELECT count(*) FROM entry_group",
    "reward_rule rows": "SELECT count(*) FROM reward_rule",
    "entry_reward_rule rows": "SELECT count(*) FROM entry_reward_rule",
    "moze_schedule rows": "SELECT count(*) FROM moze_schedule",
    "moze_backup import_run rows": "SELECT count(*) FROM import_run WHERE kind = 'moze_backup'",
    "accounts with locally edited settings or a moze_id":
        "SELECT count(*) FROM account WHERE settings_locally_edited OR moze_id IS NOT NULL",
    "categories or projects with a moze_id":
        "SELECT (SELECT count(*) FROM category WHERE moze_id IS NOT NULL)"
        " + (SELECT count(*) FROM project WHERE moze_id IS NOT NULL)",
    "counterparties with a moze_id or without entries":
        "SELECT count(*) FROM counterparty c WHERE c.moze_id IS NOT NULL"
        " OR NOT EXISTS (SELECT 1 FROM ledger_entry e WHERE e.counterparty_id = c.id)",
}


def upgrade() -> None:
    connection = op.get_bind()
    for value in ADDED_ENTRY_SOURCES:
        op.execute(f"ALTER TYPE entry_source ADD VALUE IF NOT EXISTS '{value}'")
    for value in ADDED_FX_SOURCES:
        op.execute(f"ALTER TYPE fx_source ADD VALUE IF NOT EXISTS '{value}'")
    for name, values in NEW_ENUMS.items():
        postgresql.ENUM(*values, name=name).create(connection)

    op.create_table(
        "account_group",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("moze_id", sa.String(64), nullable=True, unique=True),
    )
    op.create_table(
        "counterparty",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("moze_id", sa.String(64), nullable=True, unique=True),
    )
    op.create_table(
        "entry_group",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", _enum("entry_group_kind"), nullable=False),
        sa.Column("name", sa.String(128), nullable=True),
        sa.Column("merchant", sa.String(128), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("moze_id", sa.String(64), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_table(
        "preference",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False, server_default=sa.text("1")),
        sa.Column("expense_income_colors", _enum("color_convention"), nullable=False, server_default=sa.text("'red_green'")),
        sa.Column("keypad_layout", _enum("keypad_layout"), nullable=False, server_default=sa.text("'calculator'")),
        sa.Column("week_start", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("main_currency", sa.String(8), nullable=False, server_default=sa.text("'TWD'")),
        sa.Column("hide_rewards_on_timeline", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("abbreviate_totals", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.CheckConstraint("id = 1", name="ck_preference_single_row"),
        sa.CheckConstraint("week_start BETWEEN 0 AND 6", name="ck_preference_week_start"),
    )

    op.add_column("account", sa.Column("group_id", sa.Integer(), sa.ForeignKey("account_group.id"), nullable=True))
    op.add_column("account", sa.Column("icon", sa.String(16), nullable=True))
    op.add_column("account", sa.Column("color", sa.String(9), nullable=True))
    op.add_column("account", sa.Column("note", sa.Text(), nullable=True))
    op.add_column("account", sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.add_column("account", sa.Column("include_in_total", sa.Boolean(), nullable=False, server_default=sa.text("true")))
    op.add_column("account", sa.Column("is_credit", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("account", sa.Column("closing_day", sa.SmallInteger(), nullable=True))
    op.add_column("account", sa.Column("due_rule", _enum("due_rule"), nullable=True))
    op.add_column("account", sa.Column("due_value", sa.SmallInteger(), nullable=True))
    op.add_column("account", sa.Column("credit_limit", sa.Numeric(20, 4), nullable=True))
    op.add_column("account", sa.Column("combined_account_id", sa.Integer(), sa.ForeignKey("account.id"), nullable=True))
    op.add_column("account", sa.Column("credit_sharing_id", sa.Uuid(), nullable=True))
    op.add_column("account", sa.Column("auto_pay_account_id", sa.Integer(), sa.ForeignKey("account.id"), nullable=True))
    op.add_column("account", sa.Column("fx_fee_pct", sa.Numeric(6, 3), nullable=True))
    op.add_column("account", sa.Column("fx_fee_rounding", _enum("rounding_mode"), nullable=True))
    op.add_column("account", sa.Column("fx_fee_refundable", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("account", sa.Column("settings_locally_edited", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("account", sa.Column("moze_id", sa.String(64), nullable=True))
    op.create_unique_constraint("account_moze_id_key", "account", ["moze_id"])
    op.create_check_constraint("ck_account_closing_day", "account", "closing_day BETWEEN 1 AND 31")
    op.create_check_constraint("ck_account_combined_not_self", "account", "combined_account_id <> id")

    op.add_column("category", sa.Column("icon", sa.String(16), nullable=True))
    op.add_column("category", sa.Column("color", sa.String(9), nullable=True))
    op.add_column("category", sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.add_column("category", sa.Column("is_hidden", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("category", sa.Column("default_account_id", sa.Integer(), sa.ForeignKey("account.id"), nullable=True))
    op.add_column("category", sa.Column("default_project_id", sa.Integer(), sa.ForeignKey("project.id"), nullable=True))
    op.add_column("category", sa.Column("moze_id", sa.String(64), nullable=True))
    op.create_unique_constraint("category_moze_id_key", "category", ["moze_id"])

    op.add_column("project", sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("project", sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.add_column("project", sa.Column("moze_id", sa.String(64), nullable=True))
    op.create_unique_constraint("project_moze_id_key", "project", ["moze_id"])

    op.add_column("import_run", sa.Column("kind", _enum("import_kind"), nullable=False, server_default=sa.text("'moze_csv'")))
    op.add_column("import_run", sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "reward_rule",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("method", _enum("reward_method"), nullable=False),
        sa.Column("rate", sa.Numeric(8, 4), nullable=True),
        sa.Column("fixed_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("window", _enum("reward_window"), nullable=False, server_default=sa.text("'statement_cycle'")),
        sa.Column("posting", _enum("reward_posting"), nullable=False),
        sa.Column("delay_days", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("post_month_offset", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("post_day", sa.SmallInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("txn_rounding", _enum("rounding_mode"), nullable=False, server_default=sa.text("'keep'")),
        sa.Column("total_rounding", _enum("rounding_mode"), nullable=False, server_default=sa.text("'keep'")),
        sa.Column("total_cap", sa.Numeric(20, 4), nullable=True),
        sa.Column("shared_cap_id", sa.Uuid(), nullable=True),
        sa.Column("is_basic", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("reward_account_id", sa.Integer(), sa.ForeignKey("account.id"), nullable=True),
        sa.Column("reward_project_id", sa.Integer(), sa.ForeignKey("project.id"), nullable=True),
        sa.Column("starts_on", sa.Date(), nullable=True),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("moze_id", sa.String(64), nullable=True, unique=True),
        sa.CheckConstraint("post_month_offset BETWEEN 0 AND 2", name="ck_reward_rule_post_month_offset"),
        sa.CheckConstraint("post_day BETWEEN 1 AND 31", name="ck_reward_rule_post_day"),
    )
    op.create_index("ix_reward_rule_account_id", "reward_rule", ["account_id"])

    # ledger_entry: posting date (D14), back-filled from entry_date, then kept filled by a trigger.
    op.add_column("ledger_entry", sa.Column("posted_date", sa.Date(), nullable=True))
    op.execute("UPDATE ledger_entry SET posted_date = entry_date")
    op.alter_column("ledger_entry", "posted_date", nullable=False)
    op.execute(POSTED_DATE_FUNCTION_SQL)
    op.execute(POSTED_DATE_TRIGGER_SQL)

    op.add_column("ledger_entry", sa.Column("counterparty_id", sa.Integer(), sa.ForeignKey("counterparty.id"), nullable=True))
    op.add_column("ledger_entry", sa.Column("group_id", sa.Integer(), sa.ForeignKey("entry_group.id"), nullable=True))
    op.add_column(
        "ledger_entry",
        sa.Column("settles_entry_id", sa.Integer(), sa.ForeignKey("ledger_entry.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "ledger_entry",
        sa.Column("refunds_entry_id", sa.Integer(), sa.ForeignKey("ledger_entry.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column("ledger_entry", sa.Column("reward_rule_id", sa.Integer(), sa.ForeignKey("reward_rule.id"), nullable=True))
    op.add_column(
        "ledger_entry",
        sa.Column("reward_source_entry_id", sa.Integer(), sa.ForeignKey("ledger_entry.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column("ledger_entry", sa.Column("invoice_number", sa.String(16), nullable=True))
    op.add_column("ledger_entry", sa.Column("invoice_random", sa.String(8), nullable=True))
    op.add_column("ledger_entry", sa.Column("moze_id", sa.String(64), nullable=True))
    op.create_unique_constraint("ledger_entry_moze_id_key", "ledger_entry", ["moze_id"])
    op.add_column(
        "ledger_entry",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # Settlement identity: phase 1 CSV collections (receivable > 0) and repayments (payable < 0) are
    # settlements. Back-fill before the check constraint, which would otherwise reject those rows.
    op.add_column(
        "ledger_entry", sa.Column("is_settlement", sa.Boolean(), nullable=False, server_default=sa.text("false"))
    )
    op.execute(
        "UPDATE ledger_entry SET is_settlement = true "
        "WHERE (kind = 'receivable' AND amount > 0) OR (kind = 'payable' AND amount < 0)"
    )
    op.create_check_constraint("ck_ledger_entry_settlement_sign", "ledger_entry", SETTLEMENT_SIGN_SQL)

    # Free-text counterparty → one counterparty row per distinct value (D16).
    op.execute(
        "INSERT INTO counterparty (name) "
        "SELECT DISTINCT counterparty FROM ledger_entry WHERE counterparty IS NOT NULL ORDER BY counterparty"
    )
    op.execute(
        "UPDATE ledger_entry e SET counterparty_id = c.id FROM counterparty c WHERE c.name = e.counterparty"
    )
    op.drop_index("ix_ledger_entry_counterparty", table_name="ledger_entry")
    op.drop_column("ledger_entry", "counterparty")

    op.create_index("ix_ledger_entry_account_posted", "ledger_entry", ["account_id", "posted_date"])
    op.create_index("ix_ledger_entry_entry_date", "ledger_entry", ["entry_date"])
    op.create_index("ix_ledger_entry_counterparty_id", "ledger_entry", ["counterparty_id"])
    op.create_index("ix_ledger_entry_group_id", "ledger_entry", ["group_id"])
    op.create_index("ix_ledger_entry_settles_entry_id", "ledger_entry", ["settles_entry_id"])
    op.create_index("ix_ledger_entry_reward_source_entry_id", "ledger_entry", ["reward_source_entry_id"])

    op.create_table(
        "entry_reward_rule",
        sa.Column("entry_id", sa.Integer(), sa.ForeignKey("ledger_entry.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("rule_id", sa.Integer(), sa.ForeignKey("reward_rule.id", ondelete="RESTRICT"), primary_key=True),
    )
    op.create_table(
        "moze_schedule",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", _enum("moze_schedule_kind"), nullable=False),
        sa.Column("moze_id", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("import_run_id", sa.Integer(), sa.ForeignKey("import_run.id"), nullable=True),
    )


def _assert_downgradable(connection: sa.Connection) -> None:
    blockers = []
    for label, sql in DOWNGRADE_GUARDS.items():
        count = connection.execute(sa.text(sql)).scalar_one()
        if count:
            blockers.append(f"{count} {label}")
    if blockers:
        raise RuntimeError("refusing to downgrade 7b1e4a2c9d05: phase 1 cannot hold " + "; ".join(blockers))


def _shrink_enum(table: str, column: str, type_name: str, values: tuple[str, ...]) -> None:
    """PostgreSQL cannot drop enum values: recreate the type with the phase 1 values."""
    op.execute(f"ALTER TYPE {type_name} RENAME TO {type_name}_2a")
    op.execute(f"CREATE TYPE {type_name} AS ENUM ({', '.join(repr(v) for v in values)})")
    op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {type_name} USING {column}::text::{type_name}")
    op.execute(f"DROP TYPE {type_name}_2a")


def downgrade() -> None:
    connection = op.get_bind()
    for table in ("ledger_entry", "account", "category", "project", "counterparty", "import_run"):
        op.execute(f'LOCK TABLE "{table}" IN ACCESS EXCLUSIVE MODE')
    _assert_downgradable(connection)

    op.drop_table("moze_schedule")
    op.drop_table("entry_reward_rule")

    op.add_column("ledger_entry", sa.Column("counterparty", sa.String(128), nullable=True))
    op.execute("UPDATE ledger_entry e SET counterparty = c.name FROM counterparty c WHERE c.id = e.counterparty_id")
    op.create_index("ix_ledger_entry_counterparty", "ledger_entry", ["counterparty"])

    # is_settlement is derivable from kind and sign for every row phase 1 can hold, so no guard.
    op.drop_constraint("ck_ledger_entry_settlement_sign", "ledger_entry", type_="check")
    op.drop_column("ledger_entry", "is_settlement")

    op.execute("DROP TRIGGER trg_ledger_entry_posted_date ON ledger_entry")
    op.execute("DROP FUNCTION ledger_entry_default_posted_date()")
    for index in (
        "ix_ledger_entry_reward_source_entry_id",
        "ix_ledger_entry_settles_entry_id",
        "ix_ledger_entry_group_id",
        "ix_ledger_entry_counterparty_id",
        "ix_ledger_entry_entry_date",
        "ix_ledger_entry_account_posted",
    ):
        op.drop_index(index, table_name="ledger_entry")
    for column in (
        "updated_at", "moze_id", "invoice_random", "invoice_number", "reward_source_entry_id", "reward_rule_id",
        "refunds_entry_id", "settles_entry_id", "group_id", "counterparty_id", "posted_date",
    ):
        op.drop_column("ledger_entry", column)

    op.drop_index("ix_reward_rule_account_id", table_name="reward_rule")
    op.drop_table("reward_rule")

    op.drop_column("import_run", "exported_at")
    op.drop_column("import_run", "kind")
    for column in ("moze_id", "sort_order", "is_archived"):
        op.drop_column("project", column)
    for column in ("moze_id", "default_project_id", "default_account_id", "is_hidden", "sort_order", "color", "icon"):
        op.drop_column("category", column)
    op.drop_constraint("ck_account_combined_not_self", "account", type_="check")
    op.drop_constraint("ck_account_closing_day", "account", type_="check")
    for column in (
        "moze_id", "settings_locally_edited", "fx_fee_refundable", "fx_fee_rounding", "fx_fee_pct",
        "auto_pay_account_id", "credit_sharing_id", "combined_account_id", "credit_limit", "due_value", "due_rule",
        "closing_day", "is_credit", "include_in_total", "sort_order", "note", "color", "icon", "group_id",
    ):
        op.drop_column("account", column)

    op.drop_table("preference")
    op.drop_table("entry_group")
    op.drop_table("counterparty")
    op.drop_table("account_group")
    for name in reversed(list(NEW_ENUMS)):
        op.execute(f"DROP TYPE {name}")

    _shrink_enum("ledger_entry", "source", "entry_source", PHASE_1_ENTRY_SOURCES)
    _shrink_enum("ledger_entry", "fx_source", "fx_source", PHASE_1_FX_SOURCES)
