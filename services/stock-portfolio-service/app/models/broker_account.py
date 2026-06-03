"""Brokerage cash account metadata."""

import enum

from sqlalchemy import (
    Boolean,
    CHAR,
    Column,
    Date,
    Enum,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.sql import expression, func

from ..database import Base, TimestampMixin


def _enum_values(enum_type: type[enum.Enum]) -> list[str]:
    return [member.value for member in enum_type]


class BrokerEnum(str, enum.Enum):
    CATHAY = "cathay"
    SINOPAC = "sinopac"
    FIRSTRADE = "firstrade"
    IB = "ib"
    CS = "cs"
    OTHER = "other"


class BrokerAccount(Base, TimestampMixin):
    __tablename__ = "broker_account"
    __table_args__ = (
        UniqueConstraint("broker", "nickname", name="uq_broker_account_broker_nickname"),
    )

    id = Column(Integer, primary_key=True, index=True)
    broker = Column(
        Enum(
            BrokerEnum,
            name="broker_enum",
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    nickname = Column(String(64), nullable=False)
    currency = Column(CHAR(3), nullable=False)
    opening_balance = Column(
        Numeric(20, 4),
        nullable=False,
        default=0,
        server_default="0",
    )
    opening_date = Column(
        Date,
        nullable=False,
        server_default=func.current_date(),
    )
    is_active = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default=expression.true(),
    )
