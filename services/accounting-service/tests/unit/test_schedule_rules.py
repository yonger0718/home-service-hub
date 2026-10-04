from datetime import date
from decimal import Decimal

import pytest

from app.services import schedule_rules as rules


def test_month_end_and_leap_day_clamp_from_the_anchor():
    # Review Focus 1: always from the anchor, so February's 28th never sticks.
    anchor = date(2026, 1, 31)
    assert [rules.occurrence(anchor, "month", 1, k) for k in range(4)] == [
        date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30),
    ]
    leap = date(2024, 2, 29)
    assert [rules.occurrence(leap, "year", 1, k) for k in range(5)] == [
        date(2024, 2, 29), date(2025, 2, 28), date(2026, 2, 28), date(2027, 2, 28), date(2028, 2, 29),
    ]


def test_day_and_week_steps():
    anchor = date(2026, 10, 1)
    assert rules.occurrence(anchor, "day", 3, 2) == date(2026, 10, 7)
    assert rules.occurrence(anchor, "week", 2, 1) == date(2026, 10, 15)
    assert rules.occurrence(anchor, "month", 2, 3) == date(2027, 4, 1)


def test_day_of_month_overrides_the_anchor_day():
    anchor = date(2026, 9, 1)
    assert rules.occurrence(anchor, "month", 1, 0, 15) == date(2026, 9, 15)
    assert rules.occurrence(anchor, "month", 1, 1, 15) == date(2026, 10, 15)
    assert rules.occurrence(date(2027, 1, 31), "month", 1, 1, 31) == date(2027, 2, 28)


def test_occurrence_zero_before_the_anchor_day_is_indexed_and_normalized():
    # day_of_month earlier than the anchor's day: occurrence 0 falls before the anchor. occurrence_index still finds
    # it, and normalize_anchor (used on create / edit) moves the stored anchor to the first occurrence on or after it.
    anchor = date(2026, 9, 20)
    assert rules.occurrence(anchor, "month", 1, 0, 15) == date(2026, 9, 15)
    assert rules.occurrence_index(anchor, "month", 1, 15, date(2026, 9, 15)) == 0
    assert rules.occurrence_index(anchor, "month", 1, 15, date(2026, 9, 14)) is None
    assert rules.normalize_anchor(anchor, "month", 1, 15) == date(2026, 10, 15)
    assert rules.normalize_anchor(date(2026, 9, 1), "month", 1, 15) == date(2026, 9, 15)
    assert rules.normalize_anchor(date(2026, 10, 22), "month", 1, None) == date(2026, 10, 22)
    assert rules.normalize_anchor(date(2026, 9, 21), "week", 1, None) == date(2026, 9, 21)


def test_occurrence_index_and_first_indexes():
    anchor = date(2026, 10, 22)
    assert rules.occurrence_index(anchor, "month", 1, None, date(2027, 10, 22)) == 12
    assert rules.occurrence_index(anchor, "month", 1, None, date(2027, 10, 25)) is None
    assert rules.occurrence_index(anchor, "month", 1, None, date(2026, 10, 21)) is None
    assert rules.first_index_after(anchor, "month", 1, None, date(2027, 10, 25)) == 13
    assert rules.first_index_after(anchor, "month", 1, None, date(2027, 10, 15)) == 12
    assert rules.first_index_after(anchor, "month", 1, None, date(2026, 9, 1)) == 0
    assert rules.first_index_on_or_after(anchor, "month", 1, None, date(2026, 10, 22)) == 0
    assert rules.first_index_on_or_after(anchor, "month", 1, None, date(2026, 10, 23)) == 1


def test_week_index_follows_the_weekday():
    monday = date(2026, 9, 21)
    assert rules.occurrence_index(monday, "week", 1, None, date(2026, 10, 12)) == 3
    assert rules.occurrence_index(monday, "week", 1, None, date(2026, 10, 13)) is None


def test_horizon_is_thirteen_months_on_the_same_day_clamped():
    assert rules.horizon(date(2026, 10, 3)) == date(2027, 11, 3)
    assert rules.horizon(date(2026, 1, 31)) == date(2027, 2, 28)


def test_last_period_amount_carries_the_remainder():
    assert rules.last_period_amount(Decimal("300000"), Decimal("8333"), 36) == Decimal("8345")
    assert rules.last_period_amount(Decimal("10000"), Decimal("3333"), 3) == Decimal("3334")
    with pytest.raises(ValueError):
        rules.last_period_amount(Decimal("10000"), Decimal("5000"), 3)


def test_plain_decimal_strings():
    assert rules.plain("8333.0000") == "8333"
    assert rules.plain(Decimal("0.5000")) == "0.5"
    assert rules.plain(10) == "10"
    assert rules.plain("0.0000") == "0"
