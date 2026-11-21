"""#401: the date an event shows is its `created_at` in the ledger time zone, not in UTC."""

from datetime import UTC, date, datetime

from app.models import DebtStateEvent
from app.services import debt_events


def test_an_event_late_in_the_utc_day_shows_the_next_ledger_day():
    event = DebtStateEvent(created_at=datetime(2026, 11, 19, 17, 30, tzinfo=UTC))

    assert debt_events._event_date(event) == date(2026, 11, 20)


def test_an_event_early_in_the_utc_day_shows_the_same_ledger_day():
    event = DebtStateEvent(created_at=datetime(2026, 11, 19, 3, 0, tzinfo=UTC))

    assert debt_events._event_date(event) == date(2026, 11, 19)
