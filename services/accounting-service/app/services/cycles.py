"""Statement-cycle period arithmetic shared by notify and the reward engine (stdlib only)."""

from calendar import monthrange
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


def calendar_month(day: date) -> Period:
    """The calendar month containing `day`."""
    return _calendar_month(day.year, day.month)
