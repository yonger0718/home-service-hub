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


# --- get_rate: the latest release for today / future dates (final review finding 5) -------------------------

from app.services import fx_rate_service, ledger_service  # noqa: E402

LATEST_URL = "cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/jpy.json"


def _latest(release: str, rate: float) -> tuple[int, dict]:
    return 200, {"date": release, "jpy": {"twd": rate}}


@pytest.fixture()
def today_is(monkeypatch):
    def pin(day: date) -> None:
        monkeypatch.setattr(ledger_service, "_today", lambda: day)

    return pin


@pytest.mark.parametrize("day", [DAY, date(2026, 7, 20)], ids=["today", "future"])
def test_today_or_a_future_date_uses_the_latest_release(db_session, fake_http, today_is, day):
    today_is(DAY)
    http = fake_http({LATEST_URL: _latest("2026-07-09", 0.21)})

    quote = fx_rate_service.get_rate(db_session, day, "JPY", "TWD", http)

    assert (quote.rate, quote.rate_date, quote.source) == (Decimal("0.21"), date(2026, 7, 9), "fawazahmed0-jsdelivr")
    assert http.calls == ["https://" + LATEST_URL]  # the dated slot for today / the future is never asked for
    cached = db_session.scalars(select(FxRate)).all()
    assert [(row.date, row.rate) for row in cached] == [(date(2026, 7, 9), Decimal("0.2100000000"))]  # never under `day`


def test_a_past_date_with_a_dated_release_is_unchanged(db_session, fake_http, today_is):
    today_is(date(2026, 7, 11))
    http = fake_http({"cdn.jsdelivr.net/npm/@fawazahmed0/" + JPY_DAY_URL: _jpy(0.2), LATEST_URL: _latest("2026-07-10", 0.3)})

    quote = fx_rate_service.get_rate(db_session, DAY, "JPY", "TWD", http)

    assert (quote.rate, quote.rate_date) == (Decimal("0.2"), DAY)
    assert len(http.calls) == 1 and "latest" not in http.calls[0]


def test_a_past_date_whose_release_is_missing_uses_the_latest(db_session, fake_http, today_is):
    today_is(date(2026, 7, 11))
    http = fake_http({"cdn.jsdelivr.net/npm/@fawazahmed0/" + JPY_DAY_URL: (404, None),
                      "2026-07-10.currency-api.pages.dev": (404, None), LATEST_URL: _latest("2026-07-11", 0.3)})

    quote = fx_rate_service.get_rate(db_session, DAY, "JPY", "TWD", http)

    assert (quote.rate, quote.rate_date) == (Decimal("0.3"), date(2026, 7, 11))
    assert db_session.get(FxRate, (DAY, "JPY", "TWD")) is None


def test_today_reuses_a_cached_release_of_today_without_a_request(db_session, fake_http, today_is):
    today_is(DAY)
    db_session.add(FxRate(date=DAY, base="JPY", quote="TWD", rate=Decimal("0.2"), source="test"))
    db_session.commit()
    http = fake_http({})

    quote = fx_rate_service.get_rate(db_session, DAY, "JPY", "TWD", http)

    assert (quote.rate, quote.rate_date, http.calls) == (Decimal("0.2"), DAY, [])


def test_latest_unreachable_falls_back_to_the_newest_cached_rate(db_session, fake_http, today_is):
    today_is(DAY)
    db_session.add_all([
        FxRate(date=date(2026, 7, 1), base="JPY", quote="TWD", rate=Decimal("0.19"), source="test"),
        FxRate(date=date(2026, 7, 8), base="JPY", quote="TWD", rate=Decimal("0.2"), source="test"),
    ])
    db_session.commit()

    quote = fx_rate_service.get_rate(db_session, date(2026, 7, 12), "JPY", "TWD", fake_http({}))

    assert (quote.rate, quote.rate_date) == (Decimal("0.2"), date(2026, 7, 8))


def test_no_latest_and_no_cache_is_unavailable(db_session, fake_http, today_is):
    today_is(DAY)
    with pytest.raises(FxRateUnavailableError, match="no FX rate for JPY→TWD on 2026-07-10"):
        fx_rate_service.get_rate(db_session, DAY, "JPY", "TWD", fake_http({}))
