from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.sql import expression, func
import enum
from decimal import Decimal
from ..database import Base, TimestampMixin

class TransactionType(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


class PositionSide(str, enum.Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class Transaction(Base, TimestampMixin):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("length(trim(symbol)) > 0", name="ck_transactions_symbol_not_blank"),
        CheckConstraint("quantity > 0", name="ck_transactions_quantity_positive"),
        CheckConstraint("price >= 0", name="ck_transactions_price_nonnegative"),
        CheckConstraint("coalesce(fee, 0) >= 0", name="ck_transactions_fee_nonnegative"),
        CheckConstraint("coalesce(tax, 0) >= 0", name="ck_transactions_tax_nonnegative"),
        UniqueConstraint("import_fingerprint", name="uq_transactions_import_fingerprint"),
        Index("ix_transactions_symbol_trade_date", "symbol", "trade_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, index=True, nullable=False)  # 股票代碼, e.g., 2330
    name = Column(String, nullable=True)               # 股票名稱
    type = Column(Enum(TransactionType), nullable=False)
    position_side = Column(
        Enum(PositionSide, name="position_side_enum"),
        nullable=False,
        default=PositionSide.LONG,
        server_default=PositionSide.LONG.value,
    )
    quantity = Column(Integer, nullable=False)         # 股數
    price = Column(Numeric(12, 2), nullable=False)              # 成交單價
    trade_date = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    fee = Column(Numeric(12, 2), default=0.0)                   # 手續費 (選填)
    tax = Column(Numeric(12, 2), default=0.0)                   # 交易稅 (選填)
    is_day_trade = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=expression.false(),
    )
    import_fingerprint = Column(String(64), nullable=True)

class Dividend(Base, TimestampMixin):
    __tablename__ = "dividends"
    __table_args__ = (
        CheckConstraint("length(trim(symbol)) > 0", name="ck_dividends_symbol_not_blank"),
        CheckConstraint("amount > 0", name="ck_dividends_amount_positive"),
        CheckConstraint("coalesce(fee, 0) >= 0", name="ck_dividends_fee_nonnegative"),
        CheckConstraint("coalesce(tax, 0) >= 0", name="ck_dividends_tax_nonnegative"),
        UniqueConstraint("import_fingerprint", name="uq_dividends_import_fingerprint"),
    )

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, index=True, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)             # 總金額 (扣除 fee + tax 後)
    ex_dividend_date = Column(DateTime(timezone=True), nullable=False, index=True) # 除息日
    received_date = Column(DateTime(timezone=True), server_default=func.now()) # 入帳日
    import_fingerprint = Column(String(64), nullable=True)
    fee = Column(Numeric(12, 2), nullable=False, default=Decimal("0"))               # 匯費 (預設 NT$10)
    tax = Column(Numeric(12, 2), nullable=False, default=Decimal("0"))               # 二代健保補充保費 (NHI)
    cash_dividend_per_share = Column(Numeric(12, 4), nullable=True)                  # 每股現金股利
    stock_dividend_shares = Column(Integer, nullable=False, default=0)               # 配股股數 (floor(qty * per_thousand / 1000))
    source = Column(String(32), nullable=True)                                       # 'auto:TWT49U' / 'manual' / 'csv'
    quantity_at_record_date = Column(Numeric(18, 4), nullable=True)                  # 計算 amount 所用的持股數
