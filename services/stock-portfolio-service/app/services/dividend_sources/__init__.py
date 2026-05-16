"""Dividend / ex-rights event source parsers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as dt_date
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True, slots=True)
class DividendEventRow:
    """Normalised ex-dividend / ex-rights event from any TW source."""

    symbol: str
    ex_dividend_date: dt_date
    cash_dividend: Optional[Decimal]
    stock_dividend: Optional[Decimal]
    source: str


__all__ = ["DividendEventRow"]
