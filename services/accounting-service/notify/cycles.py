"""Statement-cycle rules and business-day counting.

Python port of frontend/src/app/components/accounting/cycle.ts (the reference
implementation); business days come only from the API calendar shape.
"""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta

Period = tuple[date, date]


def _last_day(year: int, month: int) -> int:
    return monthrange(year, month)[1]


def _day_in(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, _last_day(year, month)))


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _calendar_month(year: int, month: int) -> Period:
    return date(year, month, 1), _day_in(year, month, 31)


def _cycle_ending_in(closing_day: int, year: int, month: int) -> Period:
    prev_year, prev_month = _shift_month(year, month, -1)
    return _day_in(prev_year, prev_month, closing_day) + timedelta(days=1), _day_in(year, month, closing_day)


def statement_period(closing_day: int | None, today: date) -> Period:
    """The statement cycle containing `today`."""
    if closing_day is None:
        return _calendar_month(today.year, today.month)
    close = min(closing_day, _last_day(today.year, today.month))
    if today.day <= close:
        end_year, end_month = today.year, today.month
    else:
        end_year, end_month = _shift_month(today.year, today.month, 1)
    return _cycle_ending_in(closing_day, end_year, end_month)


def shift_period(closing_day: int | None, period: Period, delta: int) -> Period:
    """The cycle `delta` cycles before (negative) or after (positive) `period`."""
    year, month = _shift_month(period[1].year, period[1].month, delta)
    if closing_day is None:
        return _calendar_month(year, month)
    return _cycle_ending_in(closing_day, year, month)


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
