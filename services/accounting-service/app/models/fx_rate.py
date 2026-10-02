from sqlalchemy import Column, Date, Numeric, String

from ..database import Base


class FxRate(Base):
    """Daily historical rate cache: 1 unit of `base` = `rate` units of `quote` on `date`."""

    __tablename__ = "fx_rate"

    date = Column(Date, primary_key=True)
    base = Column(String(8), primary_key=True)
    quote = Column(String(8), primary_key=True)
    rate = Column(Numeric(20, 10), nullable=False)
    source = Column(String(32), nullable=False)
