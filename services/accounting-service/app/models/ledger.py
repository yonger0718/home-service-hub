from sqlalchemy import (
    DDL,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Enum,
    FetchedValue,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.sql import func

from ..database import Base, TimestampMixin

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
SYSTEM_KINDS = ("fee", "discount", "reward", "interest", "balance_adjustment")
ENTRY_SOURCES = ("moze_import", "moze_backup", "manual", "hermes", "rule")
MOZE_SOURCES = ("moze_import", "moze_backup")
FX_SOURCES = ("fx_api", "moze_backup", "manual")
DUE_RULES = ("fixed_day", "days_after_closing")
ROUNDING_MODES = ("keep", "round", "floor", "ceil")
ENTRY_GROUP_KINDS = ("split", "reward_claim", "installment")
REWARD_METHODS = ("percent", "fixed")
REWARD_WINDOWS = ("statement_cycle",)
REWARD_POSTINGS = ("after_window", "after_transaction", "manual")
COLOR_CONVENTIONS = ("red_green", "green_red")
KEYPAD_LAYOUTS = ("calculator", "phone")
SCHEDULE_KINDS = ("period", "installment", "skipped_record")

entry_kind_enum = Enum(*ENTRY_KINDS, name="entry_kind")
entry_source_enum = Enum(*ENTRY_SOURCES, name="entry_source")
fx_source_enum = Enum(*FX_SOURCES, name="fx_source")
due_rule_enum = Enum(*DUE_RULES, name="due_rule")
rounding_mode_enum = Enum(*ROUNDING_MODES, name="rounding_mode")
entry_group_kind_enum = Enum(*ENTRY_GROUP_KINDS, name="entry_group_kind")
reward_method_enum = Enum(*REWARD_METHODS, name="reward_method")
reward_window_enum = Enum(*REWARD_WINDOWS, name="reward_window")
reward_posting_enum = Enum(*REWARD_POSTINGS, name="reward_posting")
color_convention_enum = Enum(*COLOR_CONVENTIONS, name="color_convention")
keypad_layout_enum = Enum(*KEYPAD_LAYOUTS, name="keypad_layout")
schedule_kind_enum = Enum(*SCHEDULE_KINDS, name="moze_schedule_kind")

# posted_date defaults to entry_date (D14). A column default cannot read another column,
# so a BEFORE trigger fills it; the migration installs the same function and trigger.
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

# A collection (receivable, amount > 0) or repayment (payable, amount < 0) must carry
# is_settlement, and only receivable/payable rows may carry it. The migration uses the same text.
SETTLEMENT_SIGN_SQL = (
    "(is_settlement OR NOT ((kind = 'receivable' AND amount > 0) OR (kind = 'payable' AND amount < 0)))"
    " AND (NOT is_settlement OR kind IN ('receivable', 'payable'))"
)


class AccountGroup(Base):
    __tablename__ = "account_group"

    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False, unique=True)
    sort_order = Column(Integer, nullable=False, server_default=text("0"))
    moze_id = Column(String(64), nullable=True, unique=True)


class Account(Base, TimestampMixin):
    __tablename__ = "account"
    __table_args__ = (
        CheckConstraint("closing_day BETWEEN 1 AND 31", name="ck_account_closing_day"),
        CheckConstraint("combined_account_id <> id", name="ck_account_combined_not_self"),
    )

    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False, unique=True)
    currency = Column(String(8), nullable=False)
    opening_balance = Column(Numeric(20, 4), nullable=False, server_default=text("0"))
    is_archived = Column(Boolean, nullable=False, server_default=text("false"))
    group_id = Column(Integer, ForeignKey("account_group.id"), nullable=True)
    icon = Column(String(16), nullable=True)
    color = Column(String(9), nullable=True)
    note = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=False, server_default=text("0"))
    include_in_total = Column(Boolean, nullable=False, server_default=text("true"))
    is_credit = Column(Boolean, nullable=False, server_default=text("false"))
    closing_day = Column(SmallInteger, nullable=True)
    due_rule = Column(due_rule_enum, nullable=True)
    due_value = Column(SmallInteger, nullable=True)
    credit_limit = Column(Numeric(20, 4), nullable=True)
    combined_account_id = Column(Integer, ForeignKey("account.id"), nullable=True)
    credit_sharing_id = Column(Uuid, nullable=True)
    auto_pay_account_id = Column(Integer, ForeignKey("account.id"), nullable=True)
    fx_fee_pct = Column(Numeric(6, 3), nullable=True)
    fx_fee_rounding = Column(rounding_mode_enum, nullable=True)
    fx_fee_refundable = Column(Boolean, nullable=False, server_default=text("false"))
    settings_locally_edited = Column(Boolean, nullable=False, server_default=text("false"))
    moze_id = Column(String(64), nullable=True, unique=True)


class Category(Base):
    __tablename__ = "category"
    __table_args__ = (
        UniqueConstraint(
            "kind",
            "parent_id",
            "name",
            name="uq_category_kind_parent_name",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id = Column(Integer, primary_key=True)
    kind = Column(entry_kind_enum, nullable=False)
    parent_id = Column(Integer, ForeignKey("category.id"), nullable=True)
    name = Column(String(64), nullable=False)
    icon = Column(String(16), nullable=True)
    color = Column(String(9), nullable=True)
    sort_order = Column(Integer, nullable=False, server_default=text("0"))
    is_hidden = Column(Boolean, nullable=False, server_default=text("false"))
    default_account_id = Column(Integer, ForeignKey("account.id"), nullable=True)
    default_project_id = Column(Integer, ForeignKey("project.id"), nullable=True)
    moze_id = Column(String(64), nullable=True, unique=True)


class Project(Base):
    __tablename__ = "project"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False, unique=True)
    is_archived = Column(Boolean, nullable=False, server_default=text("false"))
    sort_order = Column(Integer, nullable=False, server_default=text("0"))
    moze_id = Column(String(64), nullable=True, unique=True)


class Counterparty(Base):
    __tablename__ = "counterparty"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False, unique=True)
    moze_id = Column(String(64), nullable=True, unique=True)


class EntryGroup(Base):
    __tablename__ = "entry_group"

    id = Column(Integer, primary_key=True)
    kind = Column(entry_group_kind_enum, nullable=False)
    name = Column(String(128), nullable=True)
    merchant = Column(String(128), nullable=True)
    description = Column(Text, nullable=True)
    moze_id = Column(String(64), nullable=True, unique=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RewardRule(Base):
    __tablename__ = "reward_rule"
    __table_args__ = (
        CheckConstraint("post_month_offset BETWEEN 0 AND 2", name="ck_reward_rule_post_month_offset"),
        CheckConstraint("post_day BETWEEN 1 AND 31", name="ck_reward_rule_post_day"),
        Index("ix_reward_rule_account_id", "account_id"),
    )

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("account.id"), nullable=False)
    name = Column(String(128), nullable=False)
    method = Column(reward_method_enum, nullable=False)
    rate = Column(Numeric(8, 4), nullable=True)
    fixed_amount = Column(Numeric(20, 4), nullable=True)
    window = Column(reward_window_enum, nullable=False, server_default=text("'statement_cycle'"))
    posting = Column(reward_posting_enum, nullable=False)
    delay_days = Column(SmallInteger, nullable=False, server_default=text("0"))
    post_month_offset = Column(SmallInteger, nullable=False, server_default=text("0"))
    post_day = Column(SmallInteger, nullable=False, server_default=text("1"))
    txn_rounding = Column(rounding_mode_enum, nullable=False, server_default=text("'keep'"))
    total_rounding = Column(rounding_mode_enum, nullable=False, server_default=text("'keep'"))
    total_cap = Column(Numeric(20, 4), nullable=True)
    shared_cap_id = Column(Uuid, nullable=True)
    is_basic = Column(Boolean, nullable=False, server_default=text("false"))
    reward_account_id = Column(Integer, ForeignKey("account.id"), nullable=True)
    reward_project_id = Column(Integer, ForeignKey("project.id"), nullable=True)
    starts_on = Column(Date, nullable=True)
    ends_on = Column(Date, nullable=True)
    is_enabled = Column(Boolean, nullable=False, server_default=text("true"))
    description = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=False, server_default=text("0"))
    moze_id = Column(String(64), nullable=True, unique=True)


class LedgerEntry(Base):
    __tablename__ = "ledger_entry"
    __table_args__ = (
        Index("ix_ledger_entry_account_order", "account_id", "entry_date", "entry_time", "seq"),
        Index("ix_ledger_entry_account_posted", "account_id", "posted_date"),
        Index("ix_ledger_entry_entry_date", "entry_date"),
        Index("ix_ledger_entry_transfer_group_id", "transfer_group_id"),
        Index("ix_ledger_entry_counterparty_id", "counterparty_id"),
        Index("ix_ledger_entry_group_id", "group_id"),
        Index("ix_ledger_entry_settles_entry_id", "settles_entry_id"),
        Index("ix_ledger_entry_reward_source_entry_id", "reward_source_entry_id"),
        Index("ix_ledger_entry_refunds_entry_id", "refunds_entry_id"),
        Index("ix_ledger_entry_parent_entry_id", "parent_entry_id"),
        Index("ix_ledger_entry_source", "source"),
        CheckConstraint(SETTLEMENT_SIGN_SQL, name="ck_ledger_entry_settlement_sign"),
    )

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("account.id", ondelete="RESTRICT"), nullable=False)
    kind = Column(entry_kind_enum, nullable=False)
    amount = Column(Numeric(20, 4), nullable=False)
    currency = Column(String(8), nullable=False)
    original_amount = Column(Numeric(20, 4), nullable=True)
    original_currency = Column(String(8), nullable=True)
    fx_rate = Column(Numeric(20, 10), nullable=True)
    fx_source = Column(fx_source_enum, nullable=True)
    entry_date = Column(Date, nullable=False)
    entry_time = Column(Time, nullable=True)
    # Filled from entry_date by trg_ledger_entry_posted_date when NULL; FetchedValue makes the ORM reload it.
    posted_date = Column(Date, nullable=False, server_default=FetchedValue(), server_onupdate=FetchedValue())
    category_id = Column(Integer, ForeignKey("category.id"), nullable=True)
    project_id = Column(Integer, ForeignKey("project.id"), nullable=True)
    name = Column(String(128), nullable=True)
    merchant = Column(String(128), nullable=True)
    counterparty_id = Column(Integer, ForeignKey("counterparty.id"), nullable=True)
    description = Column(Text, nullable=True)
    tags = Column(ARRAY(Text), nullable=False, server_default=text("'{}'::text[]"))
    parent_entry_id = Column(Integer, ForeignKey("ledger_entry.id"), nullable=True)
    group_id = Column(Integer, ForeignKey("entry_group.id"), nullable=True)
    transfer_group_id = Column(Uuid, nullable=True)
    settles_entry_id = Column(Integer, ForeignKey("ledger_entry.id", ondelete="SET NULL"), nullable=True)
    refunds_entry_id = Column(Integer, ForeignKey("ledger_entry.id", ondelete="SET NULL"), nullable=True)
    # True for collections/repayments (set by settle, the backup importer and the CSV importer by sign).
    is_settlement = Column(Boolean, nullable=False, server_default=text("false"))
    reward_rule_id = Column(Integer, ForeignKey("reward_rule.id"), nullable=True)
    reward_source_entry_id = Column(Integer, ForeignKey("ledger_entry.id", ondelete="SET NULL"), nullable=True)
    invoice_number = Column(String(16), nullable=True)
    invoice_random = Column(String(8), nullable=True)
    needs_review = Column(Boolean, nullable=False, server_default=text("false"))
    source = Column(entry_source_enum, nullable=False)
    import_run_id = Column(Integer, ForeignKey("import_run.id"), nullable=True)
    moze_id = Column(String(64), nullable=True, unique=True)
    seq = Column(
        BigInteger,
        nullable=False,
        unique=True,
        server_default=text("nextval('ledger_entry_seq_seq')"),
    )
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


event.listen(LedgerEntry.__table__, "after_create", DDL(POSTED_DATE_FUNCTION_SQL))
event.listen(LedgerEntry.__table__, "after_create", DDL(POSTED_DATE_TRIGGER_SQL))


class EntryRewardRule(Base):
    __tablename__ = "entry_reward_rule"

    entry_id = Column(Integer, ForeignKey("ledger_entry.id", ondelete="CASCADE"), primary_key=True)
    rule_id = Column(Integer, ForeignKey("reward_rule.id", ondelete="RESTRICT"), primary_key=True)


class Preference(Base):
    __tablename__ = "preference"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_preference_single_row"),
        CheckConstraint("week_start BETWEEN 0 AND 6", name="ck_preference_week_start"),
    )

    id = Column(Integer, primary_key=True, autoincrement=False, server_default=text("1"))
    expense_income_colors = Column(color_convention_enum, nullable=False, server_default=text("'red_green'"))
    keypad_layout = Column(keypad_layout_enum, nullable=False, server_default=text("'calculator'"))
    week_start = Column(SmallInteger, nullable=False, server_default=text("0"))
    main_currency = Column(String(8), nullable=False, server_default=text("'TWD'"))
    hide_rewards_on_timeline = Column(Boolean, nullable=False, server_default=text("false"))
    abbreviate_totals = Column(Boolean, nullable=False, server_default=text("true"))


class MozeSchedule(Base):
    __tablename__ = "moze_schedule"

    id = Column(Integer, primary_key=True)
    kind = Column(schedule_kind_enum, nullable=False)
    moze_id = Column(String(64), nullable=False)
    payload = Column(JSONB, nullable=False)
    import_run_id = Column(Integer, ForeignKey("import_run.id"), nullable=True)
