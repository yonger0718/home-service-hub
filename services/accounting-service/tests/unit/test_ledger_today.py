from datetime import date, datetime, timezone

from app.services import ledger_service


def _clock_at(utc: datetime):
    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return utc.astimezone(tz) if tz is not None else utc

    return _Clock


def test_today_is_the_taipei_date_not_the_server_date(monkeypatch):
    # 23:30 UTC on 2 Oct is 07:30 on 3 Oct in Taipei.
    monkeypatch.setattr(ledger_service, "datetime", _clock_at(datetime(2026, 10, 2, 23, 30, tzinfo=timezone.utc)))
    assert ledger_service._today() == date(2026, 10, 3)

    # 15:30 UTC on 2 Oct is 23:30 on 2 Oct in Taipei.
    monkeypatch.setattr(ledger_service, "datetime", _clock_at(datetime(2026, 10, 2, 15, 30, tzinfo=timezone.utc)))
    assert ledger_service._today() == date(2026, 10, 2)
