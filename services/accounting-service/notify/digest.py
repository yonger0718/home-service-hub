"""Weekly digest report types (spec §6.1). The builder lands in a later task; render.py consumes these."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from .debits import Skip


@dataclass(frozen=True)
class CaseLine:
    date: date | None
    amount: Decimal | None
    text: str
    question_zh: str
    statement_id: int


@dataclass(frozen=True)
class AccountCases:
    account_id: int
    name: str
    count: int
    oldest_days: int
    items: list[CaseLine]
    truncated: int


@dataclass(frozen=True)
class MissingStatement:
    account_id: int
    name: str
    last_period_end: date
    expected: date
    days_late: int


@dataclass(frozen=True)
class Gaps:
    unlinked_cards: list[str] = field(default_factory=list)
    cards_without_rule: list[str] = field(default_factory=list)
    statements_without_due: list[tuple[str, int]] = field(default_factory=list)


@dataclass(frozen=True)
class WeeklyReport:
    today: date
    open_cases: list[AccountCases]
    missing: list[MissingStatement]
    skips: list[Skip]
    gaps: Gaps
    last_daily_run: datetime | None
