from datetime import date

from app.services.reward_windows import post_on_for, window_for


def test_window_statement_cycle_uses_closing_day():
    assert window_for("statement_cycle", 15, date(2026, 11, 20)) == (date(2026, 11, 16), date(2026, 12, 15))


def test_window_no_closing_day_is_calendar_month():
    assert window_for("statement_cycle", None, date(2026, 2, 10)) == (date(2026, 2, 1), date(2026, 2, 28))


def test_window_calendar_month():
    assert window_for("calendar_month", 15, date(2026, 11, 20)) == (date(2026, 11, 1), date(2026, 11, 30))


def test_post_after_window_clamps_day():
    window = (date(2026, 12, 16), date(2027, 1, 15))
    assert post_on_for("after_window", None, 1, 31, window, date(2026, 12, 20)) == date(2027, 2, 28)


def test_post_after_window_raised_to_window_end():
    window = (date(2026, 11, 1), date(2026, 11, 30))
    assert post_on_for("after_window", None, 0, 5, window, date(2026, 11, 10)) == date(2026, 11, 30)


def test_post_after_transaction():
    window = (date(2026, 11, 1), date(2026, 11, 30))
    assert post_on_for("after_transaction", 3, None, None, window, date(2026, 11, 29)) == date(2026, 12, 2)


def test_post_manual_is_none():
    window = (date(2026, 11, 1), date(2026, 11, 30))
    assert post_on_for("manual", None, None, None, window, date(2026, 11, 10)) is None
