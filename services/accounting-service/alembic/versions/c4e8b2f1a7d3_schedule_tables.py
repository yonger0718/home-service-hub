"""accounting schedules: schedule_definition / schedule_instance, entry_source 'schedule'; moze_schedule dropped

Revision ID: c4e8b2f1a7d3
Revises: 7b1e4a2c9d05
Create Date: 2026-10-03 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c4e8b2f1a7d3"
down_revision: Union[str, Sequence[str], None] = "7b1e4a2c9d05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PRE_SCHEDULE_ENTRY_SOURCES = ("moze_import", "moze_backup", "manual", "hermes", "rule")
NEW_ENUMS = {
    "schedule_kind": ("recurring", "installment"),
    "schedule_interval": ("day", "week", "month", "year"),
    "schedule_posting_mode": ("auto", "confirm"),
    "schedule_status": ("active", "paused", "ended"),
    "schedule_instance_status": ("pending", "posted", "skipped"),
    "schedule_actor": ("auto", "owner", "import"),
}
MOZE_SCHEDULE_KINDS = ("period", "installment", "skipped_record")

# Same texts as app.models.schedule (migrations do not import app code).
DEFINITION_CHECKS = {
    "ck_schedule_definition_interval_n": "interval_n >= 1",
    "ck_schedule_definition_day_of_month": (
        "day_of_month IS NULL OR (day_of_month BETWEEN 1 AND 31 AND interval_unit IN ('month', 'year'))"
    ),
    "ck_schedule_definition_first_seq": "first_seq >= 1",
    "ck_schedule_definition_times": "times IS NULL OR times >= first_seq",
    "ck_schedule_definition_end_date": "end_date IS NULL OR end_date >= anchor_date",
    "ck_schedule_definition_total_amount": "total_amount IS NULL OR total_amount > 0",
    "ck_schedule_definition_installment": (
        "kind <> 'installment' OR (interval_unit = 'month' AND times IS NOT NULL AND times >= 2)"
    ),
}
INSTANCE_CHECKS = {
    "ck_schedule_instance_posted_entries": "(status = 'posted') = (jsonb_array_length(posted_entry_ids) > 0)",
    "ck_schedule_instance_acted": "(status = 'pending') = (acted_at IS NULL)",
    "ck_schedule_instance_partial": "is_partial = false OR status = 'posted'",
}
EMPTY_JSON_ARRAY = sa.text("'[]'::jsonb")

# Each count blocks the downgrade: the previous revision cannot hold what HomeHub posted.
DOWNGRADE_GUARDS = {
    "ledger_entry rows with source = schedule": "SELECT count(*) FROM ledger_entry WHERE source = 'schedule'",
    "schedule instances posted or skipped by HomeHub (acted_by auto or owner)":
        "SELECT count(*) FROM schedule_instance WHERE acted_by IN ('auto', 'owner')",
}


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*NEW_ENUMS[name], name=name, create_type=False)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    ]


def upgrade() -> None:
    # D39: the new entry_source value in its own committed step.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE entry_source ADD VALUE IF NOT EXISTS 'schedule'")

    connection = op.get_bind()
    for name, values in NEW_ENUMS.items():
        postgresql.ENUM(*values, name=name).create(connection)

    op.create_table(
        "schedule_definition",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("moze_id", sa.String(64), nullable=True, unique=True),
        sa.Column("kind", _enum("schedule_kind"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("template", postgresql.JSONB(), nullable=False),
        sa.Column("interval_unit", _enum("schedule_interval"), nullable=False),
        sa.Column("interval_n", sa.SmallInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("anchor_date", sa.Date(), nullable=False),
        sa.Column("day_of_month", sa.SmallInteger(), nullable=True),
        sa.Column("first_seq", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("times", sa.Integer(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("total_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("posting_mode", _enum("schedule_posting_mode"), nullable=False, server_default=sa.text("'auto'")),
        sa.Column("status", _enum("schedule_status"), nullable=False, server_default=sa.text("'active'")),
        sa.Column("auto_post_from", sa.Date(), nullable=False),
        sa.Column("generated_until", sa.Date(), nullable=True),
        sa.Column("created_locally", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("template_owner_edited", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("moze_payload", postgresql.JSONB(), nullable=True),
        sa.Column("review_reason", sa.String(64), nullable=True),
        *_timestamps(),
        *(sa.CheckConstraint(sql, name=name) for name, sql in DEFINITION_CHECKS.items()),
    )
    op.create_table(
        "schedule_instance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "definition_id", sa.Integer(), sa.ForeignKey("schedule_definition.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("rule_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("status", _enum("schedule_instance_status"), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("posted_entry_ids", postgresql.JSONB(), nullable=False, server_default=EMPTY_JSON_ARRAY),
        sa.Column("is_partial", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("acted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acted_by", _enum("schedule_actor"), nullable=True),
        sa.Column("amount_override", postgresql.JSONB(), nullable=True),
        sa.Column("edited_by_owner", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.String(128), nullable=True),
        sa.Column("moze_id", sa.String(64), nullable=True, unique=True),
        sa.Column("moze_record_ids", postgresql.JSONB(), nullable=False, server_default=EMPTY_JSON_ARRAY),
        sa.Column("moze_payload", postgresql.JSONB(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("definition_id", "seq", name="uq_schedule_instance_definition_seq"),
        *(sa.CheckConstraint(sql, name=name) for name, sql in INSTANCE_CHECKS.items()),
    )
    op.create_index(
        "ux_schedule_instance_posted_day", "schedule_instance", ["definition_id", "due_date"], unique=True,
        postgresql_where=sa.text("status = 'posted'"),
    )
    op.create_index("ix_schedule_instance_status_due", "schedule_instance", ["status", "due_date"])
    op.create_index("ix_schedule_instance_definition_id", "schedule_instance", ["definition_id"])
    op.create_index(
        "ix_schedule_instance_posted_entries", "schedule_instance", ["posted_entry_ids"], postgresql_using="gin",
        postgresql_ops={"posted_entry_ids": "jsonb_path_ops"},
    )

    # D39: MOZE's rows are reproducible from the backup; the post-upgrade step is a backup import.
    op.drop_table("moze_schedule")
    op.execute("DROP TYPE moze_schedule_kind")


def _assert_downgradable(connection: sa.Connection) -> None:
    blockers = []
    for label, sql in DOWNGRADE_GUARDS.items():
        count = connection.execute(sa.text(sql)).scalar_one()
        if count:
            blockers.append(f"{count} {label}")
    if blockers:
        raise RuntimeError("refusing to downgrade c4e8b2f1a7d3: " + "; ".join(blockers))


def _shrink_enum(table: str, column: str, type_name: str, values: tuple[str, ...]) -> None:
    """PostgreSQL cannot drop enum values: recreate the type without 'schedule'."""
    op.execute(f"ALTER TYPE {type_name} RENAME TO {type_name}_sched")
    op.execute(f"CREATE TYPE {type_name} AS ENUM ({', '.join(repr(v) for v in values)})")
    op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {type_name} USING {column}::text::{type_name}")
    op.execute(f"DROP TYPE {type_name}_sched")


def downgrade() -> None:
    connection = op.get_bind()
    for table in ("ledger_entry", "schedule_definition", "schedule_instance"):
        op.execute(f'LOCK TABLE "{table}" IN ACCESS EXCLUSIVE MODE')
    _assert_downgradable(connection)

    op.drop_index("ix_schedule_instance_posted_entries", table_name="schedule_instance")
    op.drop_index("ix_schedule_instance_definition_id", table_name="schedule_instance")
    op.drop_index("ix_schedule_instance_status_due", table_name="schedule_instance")
    op.drop_index("ux_schedule_instance_posted_day", table_name="schedule_instance")
    op.drop_table("schedule_instance")
    op.drop_table("schedule_definition")
    for name in reversed(list(NEW_ENUMS)):
        op.execute(f"DROP TYPE {name}")

    # Recreated empty: the next phase 2a backup import refills it.
    postgresql.ENUM(*MOZE_SCHEDULE_KINDS, name="moze_schedule_kind").create(connection)
    op.create_table(
        "moze_schedule",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "kind",
            postgresql.ENUM(*MOZE_SCHEDULE_KINDS, name="moze_schedule_kind", create_type=False),
            nullable=False,
        ),
        sa.Column("moze_id", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("import_run_id", sa.Integer(), sa.ForeignKey("import_run.id"), nullable=True),
    )
    _shrink_enum("ledger_entry", "source", "entry_source", PRE_SCHEDULE_ENTRY_SOURCES)
