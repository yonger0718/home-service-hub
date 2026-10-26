"""GET /calendar/business-days (#197): the fixed holiday table plus the owner's auto-pay overrides, read scope."""
from app.services import settings_service


def _put_overrides(db_session, holidays: list[str], workdays: list[str]) -> None:
    settings_service.update_reconciliation_settings(
        db_session, {"auto_pay": {"extra_holidays": holidays, "extra_workdays": workdays}})
    db_session.commit()


def test_lists_fixed_holidays_in_range_without_weekends(client):
    response = client.get("/calendar/business-days", params={"from": "2026-10-01", "to": "2026-10-31"})
    assert response.status_code == 200
    body = response.json()
    # 國慶日 make-up (Fri 10-09) and 光復節 make-up (Mon 10-26); Saturdays and Sundays are implied, not listed.
    assert body["holidays"] == ["2026-10-09", "2026-10-26"]
    assert body["workdays"] == []
    assert body["last_known_day"] == "2027-12-31"


def test_merges_owner_overrides_and_keeps_them_in_range(client, db_session):
    _put_overrides(db_session, holidays=["2026-10-27", "2027-03-03"], workdays=["2026-10-26"])
    body = client.get("/calendar/business-days", params={"from": "2026-10-20", "to": "2026-10-31"}).json()
    # A listed workday stays listed as a holiday too: the client applies "workdays win", as the job does.
    assert body["holidays"] == ["2026-10-26", "2026-10-27"]
    assert body["workdays"] == ["2026-10-26"]


def test_range_validation(client):
    assert client.get("/calendar/business-days", params={"from": "2026-10-31", "to": "2026-10-01"}).status_code == 422
    assert client.get("/calendar/business-days", params={"from": "2024-01-01", "to": "2027-12-31"}).status_code == 422
    assert client.get("/calendar/business-days", params={"from": "2026-10-01"}).status_code == 422
    same_day = client.get("/calendar/business-days", params={"from": "2026-10-26", "to": "2026-10-26"}).json()
    assert same_day == {"holidays": ["2026-10-26"], "workdays": [], "last_known_day": "2027-12-31"}
