"""Taiwan business-day calendar (pure; callers pass settings-based overrides).

Source: 行政院人事行政總處 政府行政機關辦公日曆表, 115年 (2026) and 116年 (2027).
Audited 2026-10-28 (issue #195) against the official xlsx files on dgpa.gov.tw:
115年 (https://www.dgpa.gov.tw/information?uid=30&pid=12573, file uploaded
2025-11, 120 days off) and 116年 (https://www.dgpa.gov.tw/information?uid=30&pid=12982,
file uploaded 2026-07, 121 days off). Every weekday shaded 放假日 in those files
is listed below and nothing else; neither year has a make-up workday (補班), so
``extra_workdays`` stays the escape hatch should one be decreed later.
Make-up holidays (補假) are included. Only dates that fall on weekdays are listed
(weekends are never business days anyway). The 117年 (2028) calendar is usually
published around May of the year before; extend the table and LAST_KNOWN_DAY then.
"""

from datetime import date, timedelta

LAST_KNOWN_DAY = date(2027, 12, 31)

TW_HOLIDAYS: frozenset[date] = frozenset(
    {
        # 2026 (115年)
        date(2026, 1, 1),  # 開國紀念日
        date(2026, 2, 16),  # 除夕
        date(2026, 2, 17),  # 春節初一
        date(2026, 2, 18),  # 春節初二
        date(2026, 2, 19),  # 春節初三
        date(2026, 2, 20),  # 小年夜(2/15 Sun)補假
        date(2026, 2, 27),  # 和平紀念日(2/28 Sat)補假
        date(2026, 4, 3),  # 兒童節(4/4 Sat)補假
        date(2026, 4, 6),  # 清明節(4/5 Sun)補假
        date(2026, 5, 1),  # 勞動節
        date(2026, 6, 19),  # 端午節
        date(2026, 9, 25),  # 中秋節
        date(2026, 9, 28),  # 教師節
        date(2026, 10, 9),  # 國慶日(10/10 Sat)補假
        date(2026, 10, 26),  # 光復節(10/25 Sun)補假
        date(2026, 12, 25),  # 行憲紀念日
        # 2027 (116年)
        date(2027, 1, 1),  # 開國紀念日
        date(2027, 2, 4),  # 春節連假(小年夜)
        date(2027, 2, 5),  # 除夕
        date(2027, 2, 8),  # 春節初三
        date(2027, 2, 9),  # 初一(2/6 Sat)補假
        date(2027, 2, 10),  # 初二(2/7 Sun)補假
        date(2027, 3, 1),  # 和平紀念日(2/28 Sun)補假
        date(2027, 4, 5),  # 清明節
        date(2027, 4, 6),  # 兒童節(4/4 Sun)補假
        date(2027, 4, 30),  # 勞動節(5/1 Sat)補假
        date(2027, 6, 9),  # 端午節
        date(2027, 9, 15),  # 中秋節
        date(2027, 9, 28),  # 教師節
        date(2027, 10, 11),  # 國慶日(10/10 Sun)補假
        date(2027, 10, 25),  # 光復節
        date(2027, 12, 24),  # 行憲紀念日(12/25 Sat)補假
        date(2027, 12, 31),  # 開國紀念日(2028-01-01 Sat)補假
    }
)


def is_business_day(
    day: date,
    *,
    extra_holidays: frozenset[date] = frozenset(),
    extra_workdays: frozenset[date] = frozenset(),
) -> bool:
    """True if banks are open; ``extra_workdays`` wins over everything."""
    if day in extra_workdays:
        return True
    return day.weekday() < 5 and day not in TW_HOLIDAYS | extra_holidays


def next_business_day(
    day: date,
    *,
    extra_holidays: frozenset[date] = frozenset(),
    extra_workdays: frozenset[date] = frozenset(),
) -> date:
    """First business day on or after ``day``."""
    while not is_business_day(day, extra_holidays=extra_holidays, extra_workdays=extra_workdays):
        day += timedelta(days=1)
    return day


def calendar_covers(day: date) -> bool:
    """False once ``day`` is past the last date the holiday table knows."""
    return day <= LAST_KNOWN_DAY
