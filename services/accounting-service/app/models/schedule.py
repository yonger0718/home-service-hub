"""Schedule definitions (the rule and its template) and instances (one period each); design D28.

Dates are naive Asia/Taipei dates (D41); timestamps are TIMESTAMPTZ. The migration c4e8b2f1a7d3 builds the same
tables, constraints and indexes.
"""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from ..database import Base, TimestampMixin

SCHEDULE_KINDS = ("recurring", "installment")
SCHEDULE_INTERVALS = ("day", "week", "month", "year")
SCHEDULE_POSTING_MODES = ("auto", "confirm")
SCHEDULE_STATUSES = ("active", "paused", "ended")
SCHEDULE_INSTANCE_STATUSES = ("pending", "posted", "skipped")
SCHEDULE_ACTORS = ("auto", "owner", "import")

schedule_kind_enum = Enum(*SCHEDULE_KINDS, name="schedule_kind")
schedule_interval_enum = Enum(*SCHEDULE_INTERVALS, name="schedule_interval")
schedule_posting_mode_enum = Enum(*SCHEDULE_POSTING_MODES, name="schedule_posting_mode")
schedule_status_enum = Enum(*SCHEDULE_STATUSES, name="schedule_status")
schedule_instance_status_enum = Enum(*SCHEDULE_INSTANCE_STATUSES, name="schedule_instance_status")
schedule_actor_enum = Enum(*SCHEDULE_ACTORS, name="schedule_actor")

# The migration uses the same texts (migrations do not import app code).
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
EMPTY_JSON_ARRAY = text("'[]'::jsonb")


class ScheduleDefinition(Base, TimestampMixin):
    __tablename__ = "schedule_definition"
    __table_args__ = tuple(CheckConstraint(sql, name=name) for name, sql in DEFINITION_CHECKS.items())

    id = Column(Integer, primary_key=True)
    # AHPeriod / AHInstallment identifier, or "record:<AHRecord identifier>" for a single imported record.
    moze_id = Column(String(64), nullable=True, unique=True)
    kind = Column(schedule_kind_enum, nullable=False)
    name = Column(String(128), nullable=False)
    template = Column(JSONB, nullable=False)
    interval_unit = Column(schedule_interval_enum, nullable=False)
    interval_n = Column(SmallInteger, nullable=False, server_default=text("1"))
    anchor_date = Column(Date, nullable=False)
    day_of_month = Column(SmallInteger, nullable=True)
    first_seq = Column(Integer, nullable=False, server_default=text("1"))
    times = Column(Integer, nullable=True)
    end_date = Column(Date, nullable=True)
    total_amount = Column(Numeric(20, 4), nullable=True)
    posting_mode = Column(schedule_posting_mode_enum, nullable=False, server_default=text("'auto'"))
    status = Column(schedule_status_enum, nullable=False, server_default=text("'active'"))
    auto_post_from = Column(Date, nullable=False)
    generated_until = Column(Date, nullable=True)
    created_locally = Column(Boolean, nullable=False, server_default=text("true"))
    # Proposal decision 24: the owner changed the template amounts with a 這一期與之後 / 全部週期 period edit;
    # re-imports then keep those amounts and write no MOZE override on pending periods (D37).
    template_owner_edited = Column(Boolean, nullable=False, server_default=text("false"))
    moze_payload = Column(JSONB, nullable=True)
    review_reason = Column(String(64), nullable=True)


class ScheduleInstance(Base, TimestampMixin):
    __tablename__ = "schedule_instance"
    __table_args__ = (
        UniqueConstraint("definition_id", "seq", name="uq_schedule_instance_definition_seq"),
        Index(
            "ux_schedule_instance_posted_day", "definition_id", "due_date", unique=True,
            postgresql_where=text("status = 'posted'"),
        ),
        Index("ix_schedule_instance_status_due", "status", "due_date"),
        Index("ix_schedule_instance_definition_id", "definition_id"),
        Index(
            "ix_schedule_instance_posted_entries", "posted_entry_ids", postgresql_using="gin",
            postgresql_ops={"posted_entry_ids": "jsonb_path_ops"},
        ),
        *(CheckConstraint(sql, name=name) for name, sql in INSTANCE_CHECKS.items()),
    )

    id = Column(Integer, primary_key=True)
    definition_id = Column(Integer, ForeignKey("schedule_definition.id", ondelete="CASCADE"), nullable=False)
    seq = Column(Integer, nullable=False)
    rule_date = Column(Date, nullable=False)  # the rule's occurrence; never changed by an instance edit
    due_date = Column(Date, nullable=False)  # = rule_date unless the owner moved the period
    status = Column(schedule_instance_status_enum, nullable=False, server_default=text("'pending'"))
    posted_entry_ids = Column(JSONB, nullable=False, server_default=EMPTY_JSON_ARRAY)
    is_partial = Column(Boolean, nullable=False, server_default=text("false"))
    acted_at = Column(DateTime(timezone=True), nullable=True)
    acted_by = Column(schedule_actor_enum, nullable=True)
    amount_override = Column(JSONB, nullable=True)  # unsigned decimal strings aligned with template["lines"]
    edited_by_owner = Column(Boolean, nullable=False, server_default=text("false"))
    last_error = Column(Text, nullable=True)
    last_error_at = Column(DateTime(timezone=True), nullable=True)
    reopened_at = Column(DateTime(timezone=True), nullable=True)
    note = Column(String(128), nullable=True)
    moze_id = Column(String(64), nullable=True, unique=True)
    moze_record_ids = Column(JSONB, nullable=False, server_default=EMPTY_JSON_ARRAY)
    moze_payload = Column(JSONB, nullable=True)
