from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
import requests
from sqlalchemy import select

from app.models.fx_rate import FxRate
from app.models.fx_rate import FXRate
from app.services import fx_rate_service
from app.services.quotes import fx_rate_service as quote_fx_rate_service


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


def _rows(db_session) -> list[FxRate]:
    return db_session.execute(select(FxRate).order_by(FxRate.base_currency, FxRate.quote_currency)).scalars().all()


def _payload(base: str, rates: dict[str, Any], payload_date: str = "2026-06-01") -> dict[str, Any]:
    return {"date": payload_date, base.lower(): {k.lower(): v for k, v in rates.items()}}


def test_primary_cdn_succeeds_and_upserts_requested_rows(db_session) -> None:
    def http_get(url: str, *, timeout: int):
        assert timeout == 10
        assert "cdn.jsdelivr.net" in url
        return FakeResponse(200, _payload("USD", {"TWD": 32.05, "GBP": 0.79, "JPY": 156.3}))

    result = fx_rate_service.fetch_and_store(
        db_session,
        base_currencies=["USD"],
        quote_currencies=["TWD", "GBP", "JPY"],
        http_get=http_get,
    )

    assert result.success is True
    assert result.upserted_count == 3
    rows = _rows(db_session)
    assert [(r.date, r.base_currency, r.quote_currency, r.rate, r.source) for r in rows] == [
        (date(2026, 6, 1), "USD", "GBP", Decimal("0.79"), "fawazahmed0-jsdelivr"),
        (date(2026, 6, 1), "USD", "JPY", Decimal("156.3"), "fawazahmed0-jsdelivr"),
        (date(2026, 6, 1), "USD", "TWD", Decimal("32.05"), "fawazahmed0-jsdelivr"),
    ]


def test_primary_failure_fallback_succeeds(db_session) -> None:
    seen: list[str] = []

    def http_get(url: str, *, timeout: int):
        seen.append(url)
        if "cdn.jsdelivr.net" in url:
            return FakeResponse(503)
        return FakeResponse(200, _payload("USD", {"TWD": "32.05"}))

    result = fx_rate_service.fetch_and_store(
        db_session,
        base_currencies=["USD"],
        quote_currencies=["TWD"],
        http_get=http_get,
    )

    assert result.success is True
    assert result.per_base["USD"].source_url == seen[1]
    [row] = _rows(db_session)
    assert row.rate == Decimal("32.05")
    assert row.source == "fawazahmed0-pages"


def test_both_urls_fail_per_base_and_other_bases_continue(db_session) -> None:
    def http_get(url: str, *, timeout: int):
        if url.endswith("/usd.json"):
            return FakeResponse(503)
        return FakeResponse(200, _payload("TWD", {"USD": "0.031", "GBP": "0.025"}))

    result = fx_rate_service.fetch_and_store(
        db_session,
        base_currencies=["USD", "TWD"],
        quote_currencies=["TWD", "USD", "GBP", "JPY"],
        http_get=http_get,
    )

    assert result.success is False
    assert result.per_base["USD"].success is False
    assert "503" in (result.per_base["USD"].error or "")
    assert result.per_base["TWD"].success is True
    rows = _rows(db_session)
    assert [(row.base_currency, row.quote_currency) for row in rows] == [("TWD", "GBP"), ("TWD", "USD")]


def test_historical_slot_url_and_asof_date_win(db_session) -> None:
    seen: list[str] = []

    def http_get(url: str, *, timeout: int):
        seen.append(url)
        return FakeResponse(200, _payload("USD", {"TWD": "32.05"}, payload_date="2026-06-01"))

    fx_rate_service.fetch_and_store(
        db_session,
        base_currencies=["USD"],
        quote_currencies=["TWD"],
        asof=date(2025, 12, 31),
        http_get=http_get,
    )

    assert "@2025-12-31" in seen[0]
    [row] = _rows(db_session)
    assert row.date == date(2025, 12, 31)


def test_quote_not_in_payload_is_skipped_silently(db_session) -> None:
    def http_get(url: str, *, timeout: int):
        return FakeResponse(200, _payload("USD", {"TWD": "32.05", "GBP": "0.79"}))

    result = fx_rate_service.fetch_and_store(
        db_session,
        base_currencies=["USD"],
        quote_currencies=["TWD", "GBP", "XYZ"],
        http_get=http_get,
    )

    assert result.success is True
    assert result.upserted_count == 2
    assert [(row.quote_currency, row.rate) for row in _rows(db_session)] == [
        ("GBP", Decimal("0.79")),
        ("TWD", Decimal("32.05")),
    ]


@pytest.mark.parametrize("currency", ["../foo", "1$A", "U", "USDX"])
def test_fetch_and_store_rejects_invalid_base_currency(db_session, currency: str) -> None:
    def http_get(url: str, *, timeout: int):
        raise AssertionError("invalid currency should not be fetched")

    with pytest.raises(ValueError, match="invalid currency code"):
        fx_rate_service.fetch_and_store(
            db_session,
            base_currencies=[currency],
            quote_currencies=["USD"],
            http_get=http_get,
        )


def test_fetch_and_store_rejects_invalid_quote_currency(db_session) -> None:
    def http_get(url: str, *, timeout: int):
        raise AssertionError("invalid currency should not be fetched")

    with pytest.raises(ValueError, match="invalid currency code"):
        fx_rate_service.fetch_and_store(
            db_session,
            base_currencies=["USD"],
            quote_currencies=["1$A"],
            http_get=http_get,
        )


def test_upsert_overwrites_stale_row(db_session) -> None:
    db_session.add(
        FxRate(
            date=date(2026, 6, 1),
            base_currency="USD",
            quote_currency="TWD",
            rate=Decimal("31.50"),
            source="manual",
        )
    )
    db_session.commit()

    def http_get(url: str, *, timeout: int):
        return FakeResponse(200, _payload("USD", {"TWD": "32.05"}))

    result = fx_rate_service.fetch_and_store(
        db_session,
        base_currencies=["USD"],
        quote_currencies=["TWD"],
        http_get=http_get,
    )

    assert result.success is True
    [row] = _rows(db_session)
    assert row.rate == Decimal("32.05")
    assert row.source == "fawazahmed0-jsdelivr"


def test_get_rate_exact_asof_missing_and_usd_pivot(db_session) -> None:
    db_session.add_all(
        [
            FxRate(date=date(2026, 5, 30), base_currency="USD", quote_currency="TWD", rate=Decimal("31.80"), source="test"),
            FxRate(date=date(2026, 6, 1), base_currency="USD", quote_currency="TWD", rate=Decimal("32.05"), source="test"),
            FxRate(date=date(2026, 6, 1), base_currency="GBP", quote_currency="USD", rate=Decimal("1.27"), source="test"),
        ]
    )
    db_session.commit()

    assert fx_rate_service.get_rate(db_session, date(2026, 6, 1), "USD", "TWD") == Decimal("32.05")
    assert fx_rate_service.get_rate(db_session, date(2026, 5, 31), "USD", "TWD") == Decimal("31.80")
    assert fx_rate_service.get_rate(db_session, date(2026, 6, 1), "JPY", "TWD") is None
    assert fx_rate_service.get_rate(db_session, date(2026, 6, 1), "GBP", "TWD") == Decimal("40.7035")


class _FakeTicker:
    prices: dict[str, object] = {}

    def __init__(self, yf_symbol: str) -> None:
        self.yf_symbol = yf_symbol

    @property
    def fast_info(self) -> dict[str, object]:
        value = self.prices[self.yf_symbol]
        if isinstance(value, Exception):
            raise value
        return {"regularMarketPrice": value}


def _phase2_rows(db_session) -> list[FXRate]:
    return (
        db_session.execute(select(FXRate).order_by(FXRate.currency, FXRate.date))
        .scalars()
        .all()
    )


def test_phase2_refresh_today_writes_usd_and_gbp_rows(db_session, monkeypatch) -> None:
    monkeypatch.setattr(quote_fx_rate_service, "_today_taipei", lambda: date(2026, 6, 5))
    monkeypatch.setattr(quote_fx_rate_service.yf, "Ticker", _FakeTicker)
    _FakeTicker.prices = {"USDTWD=X": "32.10", "GBPTWD=X": "40.25"}

    result = quote_fx_rate_service.refresh_today(db_session)

    assert result.ok_count == 2
    assert result.skipped_count == 0
    assert result.errors == []
    assert [(r.currency, r.date, r.rate_to_twd, r.source) for r in _phase2_rows(db_session)] == [
        ("GBP", date(2026, 6, 5), Decimal("40.25000000"), "yfinance"),
        ("USD", date(2026, 6, 5), Decimal("32.10000000"), "yfinance"),
    ]


def test_phase2_refresh_today_is_idempotent_and_overwrites(db_session, monkeypatch) -> None:
    monkeypatch.setattr(quote_fx_rate_service, "_today_taipei", lambda: date(2026, 6, 5))
    monkeypatch.setattr(quote_fx_rate_service.yf, "Ticker", _FakeTicker)
    _FakeTicker.prices = {"USDTWD=X": "32.10", "GBPTWD=X": "40.25"}
    quote_fx_rate_service.refresh_today(db_session)
    _FakeTicker.prices = {"USDTWD=X": "33.00", "GBPTWD=X": "41.00"}

    result = quote_fx_rate_service.refresh_today(db_session)

    assert result.ok_count == 2
    assert db_session.query(FXRate).count() == 2
    assert {
        row.currency: row.rate_to_twd for row in _phase2_rows(db_session)
    } == {"GBP": Decimal("41.00000000"), "USD": Decimal("33.00000000")}


def test_phase2_refresh_today_partial_failure_keeps_ok_ticker(db_session, monkeypatch) -> None:
    monkeypatch.setattr(quote_fx_rate_service, "_today_taipei", lambda: date(2026, 6, 5))
    monkeypatch.setattr(quote_fx_rate_service.yf, "Ticker", _FakeTicker)
    _FakeTicker.prices = {
        "USDTWD=X": "32.10",
        "GBPTWD=X": RuntimeError("transport down"),
    }

    result = quote_fx_rate_service.refresh_today(db_session)

    assert result.ok_count == 1
    assert result.skipped_count == 1
    assert "GBP" in result.errors[0]
    assert [(r.currency, r.rate_to_twd) for r in _phase2_rows(db_session)] == [
        ("USD", Decimal("32.10000000"))
    ]


def test_phase2_get_rate_gbp_minor_unit_divides_by_100(db_session) -> None:
    db_session.add(
        FXRate(
            currency="GBP",
            date=date(2026, 6, 5),
            rate_to_twd=Decimal("40.0"),
            source="test",
        )
    )
    db_session.commit()

    assert quote_fx_rate_service.get_rate(db_session, "GBp", date(2026, 6, 5)) == Decimal("0.40000000")


def test_phase2_get_rate_returns_latest_on_or_before(db_session) -> None:
    db_session.add_all(
        [
            FXRate(currency="USD", date=date(2026, 6, 3), rate_to_twd=Decimal("32.0"), source="test"),
            FXRate(currency="USD", date=date(2026, 6, 5), rate_to_twd=Decimal("33.0"), source="test"),
        ]
    )
    db_session.commit()

    assert quote_fx_rate_service.get_rate(db_session, "USD", date(2026, 6, 4)) == Decimal("32.00000000")


def test_phase2_get_rate_returns_none_without_prior_row(db_session) -> None:
    db_session.add(
        FXRate(currency="USD", date=date(2026, 6, 3), rate_to_twd=Decimal("32.0"), source="test")
    )
    db_session.commit()

    assert quote_fx_rate_service.get_rate(db_session, "USD", date(2026, 6, 2)) is None


def test_phase2_upsert_rejects_gbp_minor_unit(db_session) -> None:
    with pytest.raises(ValueError, match="GBp"):
        quote_fx_rate_service._upsert_rate(
            db_session,
            currency="GBp",
            date_=date(2026, 6, 5),
            rate_to_twd=Decimal("0.4"),
        )
