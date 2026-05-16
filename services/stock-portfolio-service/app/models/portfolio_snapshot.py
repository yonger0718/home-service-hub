"""Daily aggregate snapshot of ``PortfolioSummary`` totals."""

from sqlalchemy import Column, Date, DateTime, Numeric
from sqlalchemy.sql import func

from ..database import Base


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshot"

    date = Column(Date, primary_key=True)
    total_market_value = Column(Numeric(20, 4), nullable=False)
    total_cost = Column(Numeric(20, 4), nullable=False)
    total_unrealized_pnl = Column(Numeric(20, 4), nullable=False)
    total_dividends = Column(Numeric(20, 4), nullable=False)
    portfolio_xirr = Column(Numeric(10, 6), nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
