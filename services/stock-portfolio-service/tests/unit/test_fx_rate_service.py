from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
import requests
from sqlalchemy import select

from app.models.fx_rate import FxRate
from app.services import fx_rate_service


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
