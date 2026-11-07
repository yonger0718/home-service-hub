"""reward_accrual: per-transaction reward accruals, calendar_month window, per_unit method, review_reason

`reward_window` gains `calendar_month` and `reward_method` gains `per_unit`; PostgreSQL cannot drop enum values, so the
downgrade leaves them. `ux_ledger_entry_reward_source_rule` is created only when no duplicate (source, rule) pair
exists; otherwise the duplicates are printed and the index is skipped. The skip leaves the schema behind the models
(which declare the index; a later autogenerate would emit it): a deploy must capture the `alembic upgrade` output and
check `pg_indexes` for the index. After resolving the printed duplicates, create it by hand with the same statement
(`CREATE UNIQUE INDEX ux_ledger_entry_reward_source_rule ON ledger_entry (reward_source_entry_id, reward_rule_id)
WHERE reward_source_entry_id IS NOT NULL AND reward_rule_id IS NOT NULL`).

The downgrade refuses (#297 M8) while it would lose data the old schema cannot hold: a frozen accrual (any state
but `estimated`: dropping `reward_accrual` would turn its reward row into a no-accrual, MOZE-looking row: not
deletable, a matcher candidate, counted as external cap use), a `per_unit` rule (its `unit_spend` is dropped) or a
`calendar_month` rule (the old models do not know the window). `estimated` accruals are re-derivable and are dropped.

Revision ID: a3d9e5c1f7b2
Revises: e9b1c3d7a5f2
Create Date: 2026-11-03 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "a3d9e5c1f7b2"
down_revision: Union[str, Sequence[str], None] = "e9b1c3d7a5f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX = "ux_ledger_entry_reward_source_rule"
PAIR = "reward_source_entry_id IS NOT NULL AND reward_rule_id IS NOT NULL"


def upgrade() -> None:
    # The new enum values in their own committed step (ALTER TYPE ... ADD VALUE cannot be used in the same
    # transaction).
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE reward_window ADD VALUE IF NOT EXISTS 'calendar_month'")
        op.execute("ALTER TYPE reward_method ADD VALUE IF NOT EXISTS 'per_unit'")

    op.add_column("reward_rule", sa.Column("unit_spend", sa.Numeric(20, 4), nullable=True))
    op.add_column("ledger_entry", sa.Column("review_reason", sa.String(32), nullable=True))

    state = postgresql.ENUM("estimated", "posted", "bank_confirmed", "forfeited", name="reward_accrual_state")
    state.create(op.get_bind(), checkfirst=False)
    op.create_table(
        "reward_accrual",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_entry_id", sa.Integer(), sa.ForeignKey("ledger_entry.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("rule_id", sa.Integer(), sa.ForeignKey("reward_rule.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("window_start", sa.Date(), nullable=False),
        sa.Column("window_end", sa.Date(), nullable=False),
        sa.Column("post_on", sa.Date(), nullable=True),
        sa.Column("estimate", sa.Numeric(20, 4), nullable=False),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("state", postgresql.ENUM(name="reward_accrual_state", create_type=False), nullable=False,
                  server_default=sa.text("'estimated'")),
        sa.Column("ledger_entry_id", sa.Integer(), sa.ForeignKey("ledger_entry.id", ondelete="SET NULL"),
                  nullable=True, unique=True),
        sa.Column("confirmed_statement_id", sa.Integer(),
                  sa.ForeignKey("account_statement.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("source_entry_id", "rule_id", name="ux_reward_accrual_source_rule"),
    )
    op.create_index("ix_reward_accrual_rule_window", "reward_accrual", ["rule_id", "window_start"])
    op.create_index("ix_reward_accrual_state_post_on", "reward_accrual", ["state", "post_on"])

    bind = op.get_bind()
    dupes = bind.execute(sa.text(
        "SELECT reward_source_entry_id, reward_rule_id, array_agg(id ORDER BY id) FROM ledger_entry "
        f"WHERE {PAIR} GROUP BY reward_source_entry_id, reward_rule_id HAVING count(*) > 1"
    )).fetchall()
    if not dupes:
        op.create_index(INDEX, "ledger_entry", ["reward_source_entry_id", "reward_rule_id"], unique=True,
                        postgresql_where=sa.text(PAIR))
    else:
        print(f"{INDEX} skipped: {len(dupes)} duplicate pairs")
        for source_id, rule_id, ids in dupes:
            print(f"  source={source_id} rule={rule_id} entry ids={list(ids)}")


DOWNGRADE_GUARDS = {
    "frozen reward accruals": "SELECT count(*) FROM reward_accrual WHERE state <> 'estimated'",
    "per_unit reward rules": "SELECT count(*) FROM reward_rule WHERE method = 'per_unit'",
    "calendar_month reward rules": "SELECT count(*) FROM reward_rule WHERE \"window\" = 'calendar_month'",
}


def _assert_downgradable(connection: sa.Connection) -> None:
    blockers = []
    for label, sql in DOWNGRADE_GUARDS.items():
        count = connection.execute(sa.text(sql)).scalar_one()
        if count:
            blockers.append(f"{count} {label}")
    if blockers:
        raise RuntimeError("refusing to downgrade a3d9e5c1f7b2: " + "; ".join(blockers))


def downgrade() -> None:
    _assert_downgradable(op.get_bind())
    op.execute(f"DROP INDEX IF EXISTS {INDEX}")
    op.drop_table("reward_accrual")
    op.execute("DROP TYPE IF EXISTS reward_accrual_state")
    op.drop_column("ledger_entry", "review_reason")
    op.drop_column("reward_rule", "unit_spend")
