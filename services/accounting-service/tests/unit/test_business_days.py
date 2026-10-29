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


# #195: the weekday 放假日 of the official 115年/116年 辦公日曆表 xlsx (dgpa.gov.tw), audited 2026-10-28.
OFFICIAL_2026 = {"01-01", "02-16", "02-17", "02-18", "02-19", "02-20", "02-27", "04-03", "04-06", "05-01", "06-19",
                 "09-25", "09-28", "10-09", "10-26", "12-25"}
OFFICIAL_2027 = {"01-01", "02-04", "02-05", "02-08", "02-09", "02-10", "03-01", "04-05", "04-06", "04-30", "06-09",
                 "09-15", "09-28", "10-11", "10-25", "12-24", "12-31"}


def test_holiday_table_equals_the_official_calendars():
    for year, official in ((2026, OFFICIAL_2026), (2027, OFFICIAL_2027)):
        assert {d.strftime("%m-%d") for d in bd.TW_HOLIDAYS if d.year == year} == official, year
    assert all(d.weekday() < 5 for d in bd.TW_HOLIDAYS)


def test_every_2027_due_date_is_inside_the_calendar():
    """No auto_pay_calendar_stale for a 2027 due date: the shift stays inside the table through 12-31."""
    day = date(2027, 1, 1)
    while day.year == 2027:
        assert bd.calendar_covers(day)
        day = date.fromordinal(day.toordinal() + 1)
    assert bd.next_business_day(date(2027, 12, 31)) == date(2028, 1, 3)  # 12-31 補假, then the 2028 weekend
