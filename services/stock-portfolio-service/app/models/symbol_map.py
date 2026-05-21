"""Cached Chinese-name -> ticker map sourced from twstock."""

from sqlalchemy import Column, DateTime, Index, String
from sqlalchemy.sql import func

from ..database import Base


class SymbolMap(Base):
    __tablename__ = "symbol_map"
    __table_args__ = (Index("ix_symbol_map_symbol", "symbol"),)

    name = Column(String(200), primary_key=True)
    symbol = Column(String(32), nullable=False)
    market = Column(String(8), nullable=False)
    type = Column(String(32), nullable=True)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
