from datetime import date

from app.services.report_rules import compare_window, period_bounds, week_start


def test_previous_whole_month_shifts_by_calendar_month():
    assert compare_window(date(2026, 10, 1), date(2026, 10, 31), "previous") == (date(2026, 9, 1), date(2026, 9, 30))


def test_previous_six_whole_months():
    assert compare_window(date(2026, 5, 1), date(2026, 10, 31), "previous") == (date(2025, 11, 1), date(2026, 4, 30))


def test_previous_partial_range_uses_day_length():
    assert compare_window(date(2026, 10, 5), date(2026, 10, 14), "previous") == (date(2026, 9, 25), date(2026, 10, 4))


def test_last_year_whole_year():
    assert compare_window(date(2024, 1, 1), date(2024, 12, 31), "last_year") == (date(2023, 1, 1), date(2023, 12, 31))


def test_last_year_feb_29_becomes_feb_28():
    assert compare_window(date(2024, 2, 29), date(2024, 2, 29), "last_year") == (date(2023, 2, 28), date(2023, 2, 28))


def test_week_start_is_monday():
    assert week_start(date(2026, 10, 8)) == date(2026, 10, 5)
    assert week_start(date(2026, 10, 5)) == date(2026, 10, 5)
    assert week_start(date(2026, 10, 11)) == date(2026, 10, 5)


def test_period_bounds_is_half_open():
    assert period_bounds("2026-12") == (date(2026, 12, 1), date(2027, 1, 1))
    assert period_bounds("2026-10") == (date(2026, 10, 1), date(2026, 11, 1))


def test_previous_whole_month_ending_on_the_30th_ends_on_the_31st():
    assert compare_window(date(2026, 9, 1), date(2026, 9, 30), "previous") == (date(2026, 8, 1), date(2026, 8, 31))


def test_previous_february_ends_on_january_31st():
    assert compare_window(date(2026, 2, 1), date(2026, 2, 28), "previous") == (date(2026, 1, 1), date(2026, 1, 31))


def test_previous_multi_month_range_ending_on_the_30th():
    assert compare_window(date(2026, 4, 1), date(2026, 9, 30), "previous") == (date(2025, 10, 1), date(2026, 3, 31))


# polish-accounting-reports D2: a range starting on the 1st that ends mid-month compares the same days of the earlier
# month(s), the end day clipped to the target month's length.
def test_previous_running_month_compares_the_same_days():
    assert compare_window(date(2026, 11, 1), date(2026, 11, 7), "previous") == (date(2026, 10, 1), date(2026, 10, 7))


def test_previous_first_anchored_partial_range_clips_to_the_shorter_month():
    assert compare_window(date(2026, 3, 1), date(2026, 3, 30), "previous") == (date(2026, 2, 1), date(2026, 2, 28))


def test_previous_first_anchored_partial_range_clips_to_february_29th_in_a_leap_year():
    assert compare_window(date(2028, 3, 1), date(2028, 3, 30), "previous") == (date(2028, 2, 1), date(2028, 2, 29))


def test_previous_multi_month_running_range_moves_back_by_the_months_it_touches():
    assert compare_window(date(2026, 6, 1), date(2026, 11, 7), "previous") == (date(2025, 12, 1), date(2026, 5, 7))


def test_previous_range_not_starting_on_the_1st_keeps_the_day_count_rule():
    assert compare_window(date(2026, 11, 2), date(2026, 11, 7), "previous") == (date(2026, 10, 27), date(2026, 11, 1))
