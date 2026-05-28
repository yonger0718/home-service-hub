"""Daily OHLC history table for TWSE / TPEx symbols.

One row per (symbol, trading-date). Backfilled by the market-data service
after market close and queried for charts / range analytics.
"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Index,
    Numeric,
    PrimaryKeyConstraint,
    String,
)
from sqlalchemy.sql import func

from ..database import Base


class PriceHistory(Base):
    __tablename__ = "price_history"
    __table_args__ = (
        PrimaryKeyConstraint("symbol", "date", name="pk_price_history"),
        CheckConstraint("close > 0", name="ck_price_history_close_positive"),
        CheckConstraint("open IS NULL OR open > 0", name="ck_price_history_open_positive"),
        CheckConstraint("high IS NULL OR high > 0", name="ck_price_history_high_positive"),
        CheckConstraint("low IS NULL OR low > 0", name="ck_price_history_low_positive"),
        CheckConstraint(
            "high IS NULL OR low IS NULL OR high >= low",
            name="ck_price_history_high_gte_low",
        ),
        Index("ix_price_history_date", "date"),
    )

    symbol = Column(String(32), nullable=False)
    date = Column(Date, nullable=False)
    open = Column(Numeric(12, 4), nullable=True)
    high = Column(Numeric(12, 4), nullable=True)
    low = Column(Numeric(12, 4), nullable=True)
    close = Column(Numeric(12, 4), nullable=False)
    volume = Column(BigInteger, nullable=True)
    turnover = Column(Numeric(20, 2), nullable=True)
    source = Column(String(16), nullable=False)  # "TWSE" | "TPEx"
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
