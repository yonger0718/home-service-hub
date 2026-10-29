"""Payload of the worker's 國泰證券日對帳單 post (spec §4) with the §4.2 self-checks."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

UNSUPPORTED_WARNING = "unsupported_category"
_BUY_CATEGORIES = ("集買", "沖買")


def _non_negative_integer(value: Decimal, label: str) -> Decimal:
    if value < 0 or value != value.to_integral_value():
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _integer(value: Decimal, label: str) -> Decimal:
    if value != value.to_integral_value():
        raise ValueError(f"{label} must be an integer")
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StatementSource(_Strict):
    drive_file_id: str = Field(max_length=128)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    file_name: str


class StatementLine(_Strict):
    name: str
    category: Literal["集買", "集賣", "沖買", "沖賣"]
    quantity: int = Field(gt=0)
    price: Decimal = Field(gt=0)
    amount: Decimal
    fee: Decimal
    tax: Decimal
    net: Decimal
    order_no: str = Field(pattern=r"^[A-Za-z0-9]{7}$")
    points: bool

    @field_validator("amount", "fee", "tax")
    @classmethod
    def _non_negative(cls, value: Decimal, info) -> Decimal:
        return _non_negative_integer(value, info.field_name)

    @field_validator("net")
    @classmethod
    def _net_integer(cls, value: Decimal) -> Decimal:
        return _integer(value, "net")

    @model_validator(mode="after")
    def _check_line(self) -> "StatementLine":
        if abs(self.quantity * self.price - self.amount) >= 1:
            raise ValueError("quantity x price does not match amount")
        if self.category in _BUY_CATEGORIES:
            if self.tax != 0:
                raise ValueError("buy line must carry tax 0")
            expected = -(self.amount + self.fee)
        else:
            expected = self.amount - self.fee - self.tax
        if self.net != expected:
            raise ValueError("net does not match the line identity")
        return self


class StatementTotals(_Strict):
    amount: Decimal
    fee: Decimal
    tax: Decimal
    net: Decimal


class Holding(_Strict):
    code: str = Field(pattern=r"^[0-9A-Z]{4,6}$")
    name: str
    shares: int = Field(ge=0)


class StatementPayload(_Strict):
    source: StatementSource
    trade_date: date
    settlement_date: date
    net_amount: Decimal
    lines: list[StatementLine]
    totals: StatementTotals
    holdings: list[Holding]
    warnings: list[str]

    @property
    def unsupported(self) -> bool:
        return not self.lines and UNSUPPORTED_WARNING in self.warnings

    @model_validator(mode="after")
    def _check_sums(self) -> "StatementPayload":
        if self.unsupported:
            return self
        if not self.lines:
            raise ValueError("statement has no lines")
        for label in ("amount", "fee", "tax", "net"):
            if sum(getattr(line, label) for line in self.lines) != getattr(self.totals, label):
                raise ValueError(f"sum of line {label} does not match totals")
        if self.net_amount != self.totals.net:
            raise ValueError("net_amount does not match totals net")
        return self
