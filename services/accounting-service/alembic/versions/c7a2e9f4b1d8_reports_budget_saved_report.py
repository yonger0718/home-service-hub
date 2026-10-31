"""reports: budget and saved_report tables, project.exclude_from_reports

Additive only. `budget` holds monthly spending limits (total / per top-level category / per project; a total or
category budget is a series of rows ordered by `effective_from`, a project budget has one row). `saved_report`
stores named report configurations. `project.exclude_from_reports` leaves a project out of every /reports figure.

Revision ID: c7a2e9f4b1d8
Revises: d5e1f7a3b9c2
Create Date: 2026-10-31 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "c7a2e9f4b1d8"
down_revision: Union[str, Sequence[str], None] = "d5e1f7a3b9c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "project",
        sa.Column("exclude_from_reports", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    op.create_table(
        "budget",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("category.id", ondelete="CASCADE"), nullable=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=True),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("effective_from", sa.String(7), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("scope IN ('total', 'category', 'project')", name="ck_budget_scope"),
        sa.CheckConstraint("amount > 0", name="ck_budget_amount_positive"),
    )
    op.create_index(
        "ix_budget_identity", "budget", ["scope", "category_id", "project_id", "effective_from"]
    )

    op.create_table(
        "saved_report",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(40), nullable=False, unique=True),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("chart", sa.String(8), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("saved_report")
    op.drop_index("ix_budget_identity", table_name="budget")
    op.drop_table("budget")
    op.drop_column("project", "exclude_from_reports")
