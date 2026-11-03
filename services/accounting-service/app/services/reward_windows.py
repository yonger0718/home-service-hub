"""Reward windows and posting dates (pure; spec §4.4)."""

from calendar import monthrange
from datetime import date, timedelta

from app.services.cycles import Period, calendar_month, statement_period


def window_for(window: str, closing_day: int | None, entry_date: date) -> Period:
    """The reward window containing `entry_date`."""
    if window == "calendar_month":
        return calendar_month(entry_date)
    return statement_period(closing_day, entry_date)


def post_on_for(
    posting: str,
    delay_days: int | None,
    post_month_offset: int | None,
    post_day: int | None,
    window: Period,
    entry_date: date,
) -> date | None:
    """The date a reward posts; None for manual posting."""
    if posting == "after_transaction":
        return entry_date + timedelta(days=delay_days or 0)
    if posting == "after_window":
        index = window[1].year * 12 + (window[1].month - 1) + (post_month_offset or 0)
        year, month = index // 12, index % 12 + 1
        day = min(post_day or 1, monthrange(year, month)[1])
        return max(date(year, month, day), window[1])
    return None
