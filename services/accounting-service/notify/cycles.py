"""Statement-cycle rules and business-day counting.

Python port of frontend/src/app/components/accounting/cycle.ts (the reference
implementation); business days come only from the API calendar shape.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from app.services.cycles import (
    Period,
    _day_in,
    _last_day,
    _shift_month,
    shift_period,
    statement_period,
)


def last_closed_period(closing_day: int | None, today: date) -> Period:
    return shift_period(closing_day, statement_period(closing_day, today), -1)


def due_date(closing_date: date, rule: str | None, value: int | None) -> date | None:
    """Due date of the statement closing on `closing_date`; None without a rule."""
    if not rule or value is None:
        return None
    if rule == "days_after_closing":
        return closing_date + timedelta(days=value)
    if min(value, _last_day(closing_date.year, closing_date.month)) > closing_date.day:
        return _day_in(closing_date.year, closing_date.month, value)
    next_year, next_month = _shift_month(closing_date.year, closing_date.month, 1)
    return _day_in(next_year, next_month, value)


@dataclass(frozen=True)
class Calendar:
    holidays: frozenset[date]
    workdays: frozenset[date]
    last_known_day: date


class CalendarExhausted(Exception):
    """A day past `Calendar.last_known_day` was consulted."""


def _check_known(day: date, cal: Calendar) -> None:
    if day > cal.last_known_day:
        raise CalendarExhausted(f"{day} is after the calendar's last known day {cal.last_known_day}")


def is_business_day(day: date, cal: Calendar) -> bool:
    if day in cal.workdays:
        return True
    if day in cal.holidays:
        return False
    return day.weekday() < 5


def next_business_day(day: date, cal: Calendar) -> date:
    """First business day on or after `day`."""
    while True:
        _check_known(day, cal)
        if is_business_day(day, cal):
            return day
        day += timedelta(days=1)


def business_days_between(today: date, debit_day: date, cal: Calendar) -> int:
    """Business days in (today, debit_day]; 0 when equal."""
    if debit_day < today:
        raise ValueError(f"debit_day {debit_day} is before today {today}")
    count = 0
    day = today
    while day < debit_day:
        day += timedelta(days=1)
        _check_known(day, cal)
        if is_business_day(day, cal):
            count += 1
    return count
