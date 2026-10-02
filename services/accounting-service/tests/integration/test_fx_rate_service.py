from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import FxRate
from app.services.fx_rate_service import FxRateUnavailableError, ensure_rates

DAY = date(2026, 7, 10)
JPY_DAY_URL = "currency-api@2026-07-10/v1/currencies/jpy.json"


def _jpy(rate: float) -> tuple[int, dict]:
    return 200, {"date": DAY.isoformat(), "jpy": {"twd": rate, "usd": 0.0068}}


def test_missing_rate_is_fetched_from_jsdelivr_and_cached(db_session, fake_http):
    http = fake_http({"cdn.jsdelivr.net/npm/@fawazahmed0/" + JPY_DAY_URL: _jpy(0.2)})

    rates = ensure_rates(db_session, [(DAY, "JPY", "TWD")], persist=True, http_get=http)

    assert rates == {(DAY, "JPY", "TWD"): Decimal("0.2")}
    assert len(http.calls) == 1
    cached = db_session.scalar(select(FxRate))
    assert (cached.date, cached.base, cached.quote, cached.rate, cached.source) == (
        DAY, "JPY", "TWD", Decimal("0.2000000000"), "fawazahmed0-jsdelivr",
    )


def test_fallback_source_is_used_when_jsdelivr_fails(db_session, fake_http):
    http = fake_http({
        "cdn.jsdelivr.net": (503, None),
        "2026-07-10.currency-api.pages.dev/v1/currencies/jpy.json": _jpy(0.19876621234),
    })

    rates = ensure_rates(db_session, [(DAY, "JPY", "TWD")], persist=True, http_get=http)

    assert rates[(DAY, "JPY", "TWD")] == Decimal("0.1987662123")
    assert db_session.scalar(select(FxRate.source)) == "fawazahmed0-pages"


def test_cached_rate_makes_no_network_request(db_session, fake_http):
    db_session.add(FxRate(date=DAY, base="JPY", quote="TWD", rate=Decimal("0.2"), source="fawazahmed0-jsdelivr"))
    db_session.commit()
    http = fake_http({})

    rates = ensure_rates(db_session, [(DAY, "JPY", "TWD")], persist=True, http_get=http)

    assert rates == {(DAY, "JPY", "TWD"): Decimal("0.2000000000")}
    assert http.calls == []


def test_unobtainable_rate_names_pair_and_date_and_keeps_fetched_ones(db_session, fake_http):
    http = fake_http({JPY_DAY_URL: _jpy(0.2)})

    with pytest.raises(FxRateUnavailableError, match="no FX rate for JPY→TWD on 2025-06-01"):
        ensure_rates(db_session, [(DAY, "JPY", "TWD"), (date(2025, 6, 1), "JPY", "TWD")], persist=True, http_get=http)

    assert [r.date for r in db_session.scalars(select(FxRate))] == [DAY]


def test_without_persist_nothing_is_written(db_session, fake_http):
    http = fake_http({JPY_DAY_URL: _jpy(0.2)})

    rates = ensure_rates(db_session, [(DAY, "JPY", "TWD")], persist=False, http_get=http)

    assert rates[(DAY, "JPY", "TWD")] == Decimal("0.2")
    db_session.rollback()
    assert db_session.scalar(select(FxRate)) is None


def test_one_request_per_day_and_base(db_session, fake_http):
    http = fake_http({JPY_DAY_URL: _jpy(0.2)})

    ensure_rates(db_session, [(DAY, "JPY", "TWD"), (DAY, "JPY", "USD")], persist=True, http_get=http)

    assert len(http.calls) == 1


@pytest.mark.parametrize("bad_rate", [0, -0.2, "abc"])
def test_unparseable_or_non_positive_rate_is_unavailable_but_others_are_cached(db_session, fake_http, bad_rate):
    day2 = date(2025, 6, 1)
    http = fake_http({
        JPY_DAY_URL: _jpy(0.2),
        "currency-api@2025-06-01/v1/currencies/jpy.json": (200, {"date": "2025-06-01", "jpy": {"twd": bad_rate}}),
    })

    with pytest.raises(FxRateUnavailableError, match="no FX rate for JPY→TWD on 2025-06-01"):
        ensure_rates(db_session, [(DAY, "JPY", "TWD"), (day2, "JPY", "TWD")], persist=True, http_get=http)

    assert [r.date for r in db_session.scalars(select(FxRate))] == [DAY]


PRIMARY = "cdn.jsdelivr.net/npm/@fawazahmed0/" + JPY_DAY_URL
FALLBACK = "2026-07-10.currency-api.pages.dev/v1/currencies/jpy.json"


def _payload(day: str = DAY.isoformat(), **jpy) -> tuple[int, dict | list]:
    return 200, {"date": day, "jpy": jpy}


def _sources(db_session) -> dict[str, str]:
    return {r.quote: r.source for r in db_session.scalars(select(FxRate))}


@pytest.mark.parametrize(
    "primary",
    [
        _payload(usd=0.0068),  # requested quote missing
        _payload(twd=0),  # zero quote
        _payload(twd=-1),  # negative quote
        _payload(twd="abc"),  # unparseable quote
        (200, [{"jpy": {"twd": 0.2}}]),  # JSON array, not an object
        (200, {"date": DAY.isoformat(), "jpy": [0.2]}),  # base key is not an object
        (200, {"date": DAY.isoformat()}),  # base key absent
    ],
)
def test_fallback_used_when_primary_payload_is_unusable(db_session, fake_http, primary):
    http = fake_http({PRIMARY: primary, FALLBACK: _payload(twd=0.21)})

    rates = ensure_rates(db_session, [(DAY, "JPY", "TWD")], persist=True, http_get=http)

    assert rates == {(DAY, "JPY", "TWD"): Decimal("0.21")}
    assert _sources(db_session) == {"TWD": "fawazahmed0-pages"}


def test_both_sources_unusable_raises_naming_pair_and_date(db_session, fake_http):
    http = fake_http({PRIMARY: (200, []), FALLBACK: _payload(twd=0)})

    with pytest.raises(FxRateUnavailableError, match="no FX rate for JPY→TWD on 2026-07-10"):
        ensure_rates(db_session, [(DAY, "JPY", "TWD")], persist=True, http_get=http)

    assert db_session.scalar(select(FxRate)) is None


def test_mixed_quotes_keep_primary_rate_and_fill_the_rest_from_fallback(db_session, fake_http):
    http = fake_http({PRIMARY: _payload(twd=0.2), FALLBACK: _payload(twd=0.5, usd=0.0068)})

    rates = ensure_rates(db_session, [(DAY, "JPY", "TWD"), (DAY, "JPY", "USD")], persist=True, http_get=http)

    assert rates == {(DAY, "JPY", "TWD"): Decimal("0.2"), (DAY, "JPY", "USD"): Decimal("0.0068")}
    assert _sources(db_session) == {"TWD": "fawazahmed0-jsdelivr", "USD": "fawazahmed0-pages"}


def test_payload_dated_for_another_day_falls_back_to_correct_source(db_session, fake_http):
    http = fake_http({PRIMARY: _payload("2026-07-09", twd=0.2), FALLBACK: _payload(twd=0.21)})

    rates = ensure_rates(db_session, [(DAY, "JPY", "TWD")], persist=True, http_get=http)

    assert rates == {(DAY, "JPY", "TWD"): Decimal("0.21")}
    assert _sources(db_session) == {"TWD": "fawazahmed0-pages"}


@pytest.mark.parametrize("bad_date", ["2026-07-09", None, 20260710])
def test_payload_date_mismatch_on_both_sources_caches_nothing(db_session, fake_http, bad_date):
    bad = (200, {"date": bad_date, "jpy": {"twd": 0.2}})
    http = fake_http({PRIMARY: bad, FALLBACK: bad})

    with pytest.raises(FxRateUnavailableError, match="no FX rate for JPY→TWD on 2026-07-10"):
        ensure_rates(db_session, [(DAY, "JPY", "TWD")], persist=True, http_get=http)

    assert db_session.scalar(select(FxRate)) is None
