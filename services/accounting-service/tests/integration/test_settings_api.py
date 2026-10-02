from datetime import date
from decimal import Decimal

import pytest

from app.models import FxRate, Preference
from app.services import fx_rate_service

DEFAULT_PREFERENCE = {
    "expense_income_colors": "red_green",
    "keypad_layout": "calculator",
    "week_start": 0,
    "main_currency": "TWD",
    "hide_rewards_on_timeline": False,
    "abbreviate_totals": True,
}


def test_preference_defaults_created_on_first_read(client, db_session):
    assert db_session.get(Preference, 1) is None

    response = client.get("/preference")

    assert response.status_code == 200
    assert response.json() == DEFAULT_PREFERENCE
    db_session.expire_all()
    assert db_session.get(Preference, 1).main_currency == "TWD"
    assert client.get("/preference").json() == DEFAULT_PREFERENCE


def test_preference_update_replaces_every_field(client, db_session):
    changed = {
        "expense_income_colors": "green_red",
        "keypad_layout": "phone",
        "week_start": 1,
        "main_currency": "JPY",
        "hide_rewards_on_timeline": True,
        "abbreviate_totals": False,
    }

    response = client.put("/preference", json=changed)

    assert response.status_code == 200
    assert response.json() == changed
    assert client.get("/preference").json() == changed


@pytest.mark.parametrize(
    "field, value",
    [("week_start", 7), ("keypad_layout", "abacus"), ("main_currency", "twd"), ("expense_income_colors", None)],
)
def test_preference_update_validates_fields(client, db_session, field, value):
    response = client.put("/preference", json={**DEFAULT_PREFERENCE, field: value})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][-1] == field


def test_fx_rate_returns_the_cached_rate_without_a_network_request(client, db_session):
    db_session.add(FxRate(date=date(2026, 10, 2), base="JPY", quote="TWD", rate=Decimal("0.2163"), source="fawazahmed0-jsdelivr"))
    db_session.commit()

    response = client.get("/fx-rate", params={"date": "2026-10-02", "base": "JPY", "quote": "TWD"})

    assert response.status_code == 200
    assert response.json() == {
        "date": "2026-10-02", "base": "JPY", "quote": "TWD", "rate": "0.2163000000", "source": "fawazahmed0-jsdelivr",
    }


def test_fx_rate_fetches_and_caches_a_missing_rate(client, db_session, fake_http, monkeypatch):
    http = fake_http(
        {"currency-api@2026-10-02/v1/currencies/usd.json": (200, {"date": "2026-10-02", "usd": {"twd": 32.1}})}
    )
    monkeypatch.setattr(fx_rate_service.requests, "get", http)

    response = client.get("/fx-rate", params={"date": "2026-10-02", "base": "usd", "quote": "twd"})

    assert response.status_code == 200
    assert (response.json()["rate"], response.json()["source"]) == ("32.1000000000", "fawazahmed0-jsdelivr")
    db_session.expire_all()
    assert db_session.get(FxRate, (date(2026, 10, 2), "USD", "TWD")).rate == Decimal("32.1")
    assert len(http.calls) == 1


def test_fx_rate_same_currency_is_one(client, db_session):
    body = client.get("/fx-rate", params={"date": "2026-10-02", "base": "TWD", "quote": "TWD"}).json()

    assert (body["rate"], body["source"]) == ("1", "identity")


def test_fx_rate_unavailable_is_503(client, db_session, fake_http, monkeypatch):
    monkeypatch.setattr(fx_rate_service.requests, "get", fake_http({}))

    response = client.get("/fx-rate", params={"date": "2026-10-02", "base": "JPY", "quote": "TWD"})

    assert response.status_code == 503
    assert "no FX rate for JPY→TWD on 2026-10-02" in response.json()["message"]


def test_get_rate_service_reads_the_cache(db_session):
    db_session.add(FxRate(date=date(2026, 10, 2), base="JPY", quote="TWD", rate=Decimal("0.2163"), source="test"))
    db_session.commit()

    assert fx_rate_service.get_rate(db_session, date(2026, 10, 2), "jpy", "twd") == Decimal("0.2163")
    assert fx_rate_service.get_rate(db_session, date(2026, 10, 2), "TWD", "TWD") == Decimal(1)
