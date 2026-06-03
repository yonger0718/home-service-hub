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
