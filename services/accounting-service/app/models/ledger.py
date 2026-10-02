from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
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
ENTRY_SOURCES = ("moze_import", "manual", "hermes")
FX_SOURCES = ("fx_api", "moze_backup")

entry_kind_enum = Enum(*ENTRY_KINDS, name="entry_kind")
entry_source_enum = Enum(*ENTRY_SOURCES, name="entry_source")
fx_source_enum = Enum(*FX_SOURCES, name="fx_source")


class Account(Base, TimestampMixin):
    __tablename__ = "account"

    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False, unique=True)
    currency = Column(String(8), nullable=False)
    opening_balance = Column(Numeric(20, 4), nullable=False, server_default=text("0"))
    is_archived = Column(Boolean, nullable=False, server_default=text("false"))


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


class Project(Base):
    __tablename__ = "project"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False, unique=True)


class LedgerEntry(Base):
    __tablename__ = "ledger_entry"
    __table_args__ = (
        Index("ix_ledger_entry_account_order", "account_id", "entry_date", "entry_time", "seq"),
        Index("ix_ledger_entry_transfer_group_id", "transfer_group_id"),
        Index("ix_ledger_entry_counterparty", "counterparty"),
        Index("ix_ledger_entry_source", "source"),
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
    category_id = Column(Integer, ForeignKey("category.id"), nullable=True)
    project_id = Column(Integer, ForeignKey("project.id"), nullable=True)
    name = Column(String(128), nullable=True)
    merchant = Column(String(128), nullable=True)
    counterparty = Column(String(128), nullable=True)
    description = Column(Text, nullable=True)
    tags = Column(ARRAY(Text), nullable=False, server_default=text("'{}'::text[]"))
    parent_entry_id = Column(Integer, ForeignKey("ledger_entry.id"), nullable=True)
    transfer_group_id = Column(Uuid, nullable=True)
    needs_review = Column(Boolean, nullable=False, server_default=text("false"))
    source = Column(entry_source_enum, nullable=False)
    import_run_id = Column(Integer, ForeignKey("import_run.id"), nullable=True)
    seq = Column(
        BigInteger,
        nullable=False,
        unique=True,
        server_default=text("nextval('ledger_entry_seq_seq')"),
    )
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
