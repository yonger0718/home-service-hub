from datetime import date

import pytest

from notify.cycles import (
    Calendar,
    CalendarExhausted,
    business_days_between,
    due_date,
    is_business_day,
    last_closed_period,
    next_business_day,
    shift_period,
    statement_period,
)


def test_period_closing_15():
    assert statement_period(15, date(2026, 10, 2)) == (date(2026, 9, 16), date(2026, 10, 15))


def test_moves_to_next_cycle_after_closing_day():
    assert statement_period(15, date(2026, 10, 16)) == (date(2026, 10, 16), date(2026, 11, 15))
    assert statement_period(15, date(2026, 10, 15)) == (date(2026, 9, 16), date(2026, 10, 15))


def test_clamps_closing_day_past_month_end():
    assert statement_period(31, date(2026, 2, 10)) == (date(2026, 2, 1), date(2026, 2, 28))
    assert statement_period(30, date(2026, 3, 5)) == (date(2026, 3, 1), date(2026, 3, 30))


def test_calendar_month_without_closing_day():
    assert statement_period(None, date(2026, 10, 2)) == (date(2026, 10, 1), date(2026, 10, 31))


def test_shift_period():
    current = statement_period(15, date(2026, 10, 2))
    assert shift_period(15, current, -1) == (date(2026, 8, 16), date(2026, 9, 15))
    assert shift_period(15, current, 3) == (date(2026, 12, 16), date(2027, 1, 15))
    assert shift_period(None, (date(2026, 12, 1), date(2026, 12, 31)), 1) == (date(2027, 1, 1), date(2027, 1, 31))


def test_last_closed_period():
    assert last_closed_period(15, date(2026, 10, 2)) == (date(2026, 8, 16), date(2026, 9, 15))
    assert last_closed_period(None, date(2026, 10, 2)) == (date(2026, 9, 1), date(2026, 9, 30))


def test_due_date_rules():
    assert due_date(date(2026, 10, 15), "days_after_closing", 20) == date(2026, 11, 4)
    assert due_date(date(2026, 10, 15), "fixed_day", 5) == date(2026, 11, 5)
    assert due_date(date(2026, 10, 15), "fixed_day", 25) == date(2026, 10, 25)
    assert due_date(date(2026, 10, 15), None, None) is None
    assert due_date(date(2026, 10, 15), "fixed_day", None) is None


def test_due_date_clamps_short_month():
    assert due_date(date(2026, 1, 31), "fixed_day", 31) == date(2026, 2, 28)
    assert statement_period(31, date(2026, 2, 10)) == (date(2026, 2, 1), date(2026, 2, 28))


def _cal(holidays=(), workdays=()):
    return Calendar(holidays=frozenset(holidays), workdays=frozenset(workdays), last_known_day=date(2027, 12, 31))


def test_business_days_between_from_weekend():
    cal = _cal(holidays={date(2026, 10, 26)})
    assert business_days_between(date(2026, 10, 24), date(2026, 10, 27), cal) == 1
    assert business_days_between(date(2026, 10, 23), date(2026, 10, 26), cal) == 0
    assert business_days_between(date(2026, 10, 24), date(2026, 10, 24), cal) == 0
    assert business_days_between(date(2026, 10, 30), date(2026, 11, 3), cal) == 2
    assert business_days_between(date(2026, 10, 29), date(2026, 11, 3), cal) == 3
    assert next_business_day(date(2026, 10, 24), cal) == date(2026, 10, 27)
    with pytest.raises(ValueError):
        business_days_between(date(2026, 10, 27), date(2026, 10, 26), cal)


def test_calendar_exhausted():
    cal = _cal()
    with pytest.raises(CalendarExhausted):
        next_business_day(date(2028, 1, 1), cal)
    with pytest.raises(CalendarExhausted):
        business_days_between(date(2027, 12, 30), date(2028, 1, 4), cal)


def test_workday_wins_over_weekend():
    assert is_business_day(date(2026, 10, 31), _cal(workdays={date(2026, 10, 31)})) is True
    assert is_business_day(date(2026, 10, 31), _cal()) is False


def test_holiday_closed_on_weekday():
    assert is_business_day(date(2026, 10, 26), _cal(holidays={date(2026, 10, 26)})) is False
