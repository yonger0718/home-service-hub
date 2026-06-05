"""Daily FX rate snapshots."""

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Numeric,
    PrimaryKeyConstraint,
    String,
)
from sqlalchemy.sql import func

from ..database import Base


class FxRate(Base):
    __tablename__ = "fx_rate"
    __table_args__ = (
        PrimaryKeyConstraint("date", "base_currency", "quote_currency", name="pk_fx_rate"),
        CheckConstraint("rate > 0", name="ck_fx_rate_rate_positive"),
    )

    date = Column(Date, nullable=False)
    base_currency = Column(CHAR(3), nullable=False)
    quote_currency = Column(CHAR(3), nullable=False)
    rate = Column(Numeric(20, 8), nullable=False)
    source = Column(String(32), nullable=False)
    fetched_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FXRate(Base):
    __tablename__ = "fx_rates"
    __table_args__ = (
        PrimaryKeyConstraint("currency", "date", name="pk_fx_rates"),
        CheckConstraint("rate_to_twd > 0", name="ck_fx_rates_rate_to_twd_positive"),
        CheckConstraint("currency IN ('USD', 'GBP')", name="ck_fx_rates_supported_currency"),
    )

    currency = Column(CHAR(3), nullable=False)
    date = Column(Date, nullable=False)
    rate_to_twd = Column(Numeric(20, 8), nullable=False)
    source = Column(
        String(16),
        nullable=False,
        default="yfinance",
        server_default="yfinance",
    )
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
