from datetime import date

from app.services import business_days as bd


def test_weekend_and_make_up_holiday_shift():
    assert bd.next_business_day(date(2026, 10, 24)) == date(2026, 10, 27)  # Sat; Mon 10-26 = 光復節 make-up
    assert bd.next_business_day(date(2026, 10, 9)) == date(2026, 10, 12)  # 國慶 10-10 Sat -> Fri 10-09 holiday
    assert bd.next_business_day(date(2026, 9, 25)) == date(2026, 9, 29)  # 中秋 Fri 09-25, 教師節 Mon 09-28
    assert bd.next_business_day(date(2026, 10, 28)) == date(2026, 10, 28)  # plain Wednesday


def test_spring_festival_2027():
    assert bd.next_business_day(date(2027, 2, 5)) == date(2027, 2, 11)  # 除夕 Fri 02-05 ... make-up Wed 02-10


def test_settings_overrides():
    assert bd.next_business_day(date(2026, 10, 24), extra_workdays=frozenset({date(2026, 10, 26)})) == date(2026, 10, 26)
    assert bd.next_business_day(date(2026, 10, 28), extra_holidays=frozenset({date(2026, 10, 28)})) == date(2026, 10, 29)
    assert bd.is_business_day(date(2026, 10, 31), extra_workdays=frozenset({date(2026, 10, 31)}))  # Saturday make-up workday


def test_calendar_coverage():
    assert bd.calendar_covers(date(2027, 12, 31)) and not bd.calendar_covers(date(2028, 1, 1))
    assert bd.next_business_day(date(2028, 1, 1)) == date(2028, 1, 3)  # beyond LAST_KNOWN_DAY: weekend shift only
    assert all(d.year in (2026, 2027) for d in bd.TW_HOLIDAYS)
