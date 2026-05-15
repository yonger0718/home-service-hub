"""TWSE face-value-change events keyed by symbol + effective date."""

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from ..database import Base


class CorporateAction(Base):
    __tablename__ = "corporate_actions"
    __table_args__ = (
        UniqueConstraint("source_event_key", name="uq_corporate_actions_event_key"),
        CheckConstraint("ratio > 0", name="ck_corporate_actions_ratio_positive"),
        Index("ix_corporate_actions_symbol_date", "symbol", "effective_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(32), nullable=False)
    effective_date = Column(Date, nullable=False)
    action_type = Column(String(32), nullable=False, server_default="FACE_VALUE_CHANGE")
    ratio = Column(Numeric(18, 8), nullable=False)
    source = Column(String(32), nullable=False, server_default="TWSE")
    source_event_key = Column(String(128), nullable=False)
    raw_payload = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
