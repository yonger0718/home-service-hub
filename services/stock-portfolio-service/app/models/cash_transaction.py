"""Signed brokerage cash ledger rows."""

import enum

from sqlalchemy import (
    CHAR,
    Column,
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)

from ..database import Base, TimestampMixin


def _enum_values(enum_type: type[enum.Enum]) -> list[str]:
    return [member.value for member in enum_type]


class CashTxnType(str, enum.Enum):
    DEPOSIT = "deposit"
    WITHDRAW = "withdraw"
    BUY_SETTLE = "buy_settle"
    SELL_SETTLE = "sell_settle"
    FEE = "fee"
    TAX = "tax"
    DIVIDEND_CASH = "dividend_cash"
    INTEREST_IN = "interest_in"
    MARGIN_INTEREST = "margin_interest"
    WIRE_FEE = "wire_fee"
    FX_CONVERT = "fx_convert"


class CashTxnSource(str, enum.Enum):
    MANUAL = "manual"
    CSV_IMPORT = "csv_import"
    AUTO_DERIVE = "auto_derive"


class CashTransaction(Base, TimestampMixin):
    __tablename__ = "cash_transaction"
    __table_args__ = (
        UniqueConstraint("import_fingerprint", name="uq_cash_transaction_import_fingerprint"),
        Index("ix_cash_transaction_account_id", "account_id"),
        Index("ix_cash_transaction_txn_date", "txn_date"),
        Index("ix_cash_transaction_related_transaction_id", "related_transaction_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(
        Integer,
        ForeignKey("broker_account.id", ondelete="RESTRICT"),
        nullable=False,
    )
    txn_date = Column(Date, nullable=False)
    type = Column(
        Enum(
            CashTxnType,
            name="cash_txn_type_enum",
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    amount = Column(Numeric(20, 4), nullable=False)
    currency = Column(CHAR(3), nullable=False)
    related_transaction_id = Column(
        Integer,
        ForeignKey("transactions.id", ondelete="SET NULL"),
        nullable=True,
    )
    related_dividend_id = Column(
        Integer,
        ForeignKey("dividends.id", ondelete="SET NULL"),
        nullable=True,
    )
    note = Column(String(255), nullable=True)
    source = Column(
        Enum(
            CashTxnSource,
            name="cash_txn_source_enum",
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    import_fingerprint = Column(String(128), nullable=False)
