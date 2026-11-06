"""E-invoice link schema (spec §5). A link row ties one invoice to one ledger entry or one entry group."""
from sqlalchemy import (
    Boolean, CheckConstraint, Column, DDL, Date, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String,
    UniqueConstraint, event, func, text,
)
from sqlalchemy.dialects.postgresql import CHAR

from app.database import Base

EINVOICE_STATES = ("unmatched", "ambiguous", "near", "linked", "dismissed", "void")
EINVOICE_DISMISS_REASONS = ("owner", "seller_muted", "runbook")
EINVOICE_LINKED_BY = ("auto", "owner")

einvoice_state_enum = Enum(*EINVOICE_STATES, name="einvoice_state")
einvoice_dismiss_reason_enum = Enum(*EINVOICE_DISMISS_REASONS, name="einvoice_dismiss_reason")
einvoice_linked_by_enum = Enum(*EINVOICE_LINKED_BY, name="einvoice_linked_by")

# Deleting a link (directly or through the entry/group cascade) hands a linked invoice back to the matcher.
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


class Einvoice(Base):
    __tablename__ = "einvoice"
    __table_args__ = (
        UniqueConstraint("number", "period", name="ux_einvoice_number_period"),
        Index("ix_einvoice_state", "state"),
        Index("ix_einvoice_seller_tax_id", "seller_tax_id"),
        CheckConstraint("(state = 'dismissed') = (dismiss_reason IS NOT NULL)", name="ck_einvoice_dismiss_reason"),
        CheckConstraint("NOT is_void OR state = 'void'", name="ck_einvoice_void_state"),
    )

    id = Column(Integer, primary_key=True)
    number = Column(String(10), nullable=False)
    issue_date = Column(Date, nullable=False)
    period = Column(CHAR(5), nullable=False)
    seller_tax_id = Column(String(16), nullable=False, server_default=text("''"))
    seller_name = Column(String(128), nullable=False)
    total_amount = Column(Numeric(20, 4), nullable=False)
    status_raw = Column(String(16), nullable=False)
    is_void = Column(Boolean, nullable=False, server_default=text("false"))
    state = Column(einvoice_state_enum, nullable=False, server_default=text("'unmatched'"))
    dismiss_reason = Column(einvoice_dismiss_reason_enum, nullable=True)
    first_seen_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    source_drive_id = Column(String(128), nullable=False)


class EinvoiceItem(Base):
    __tablename__ = "einvoice_item"

    invoice_id = Column(Integer, ForeignKey("einvoice.id", ondelete="CASCADE"), primary_key=True)
    seq = Column(Integer, primary_key=True)
    name = Column(String(256), nullable=False)
    subtotal = Column(Numeric(20, 4), nullable=False)


class EinvoiceLink(Base):
    __tablename__ = "einvoice_link"
    __table_args__ = (
        CheckConstraint("(entry_id IS NULL) <> (group_id IS NULL)", name="ck_einvoice_link_one_target"),
        Index("ux_einvoice_link_entry", "entry_id", unique=True, postgresql_where=text("entry_id IS NOT NULL")),
        Index("ux_einvoice_link_group", "group_id", unique=True, postgresql_where=text("group_id IS NOT NULL")),
    )

    invoice_id = Column(Integer, ForeignKey("einvoice.id", ondelete="CASCADE"), primary_key=True)
    entry_id = Column(Integer, ForeignKey("ledger_entry.id", ondelete="CASCADE"), nullable=True)
    group_id = Column(Integer, ForeignKey("entry_group.id", ondelete="CASCADE"), nullable=True)
    linked_by = Column(einvoice_linked_by_enum, nullable=False)
    linked_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


event.listen(EinvoiceLink.__table__, "after_create", DDL(EINVOICE_UNLINK_FUNCTION_SQL))
event.listen(EinvoiceLink.__table__, "after_create", DDL(EINVOICE_UNLINK_TRIGGER_SQL))


class EinvoiceSellerMute(Base):
    __tablename__ = "einvoice_seller_mute"
    __table_args__ = (CheckConstraint("seller_tax_id <> ''", name="ck_einvoice_seller_mute_tax_id"),)

    seller_tax_id = Column(String(16), primary_key=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
