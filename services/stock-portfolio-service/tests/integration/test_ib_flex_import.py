from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import pytest

from app.models import portfolio as models
from app.models.fx_rate import FXRate

FIXTURES = Path(__file__).parents[1] / "unit/fixtures"


def _seed_fx(db_session, *dates):
    for d in dates:
        db_session.merge(FXRate(currency="USD", date=d, rate_to_twd=Decimal("31.42000000"), source="test"))
    db_session.commit()


JUNE = [date(2026, 6, d) for d in range(1, 31)]


def _post(client, raw: bytes, **params):
    return client.post("/api/portfolio/imports/ib-flex", params=params, files={"file": ("flex.xml", BytesIO(raw), "application/xml")})


FLEX = (FIXTURES / "ib_flex_sample.xml").read_bytes()
TWIN = FLEX.replace(b"</Trades>", FLEX[FLEX.index(b"<Trade "):FLEX.index(b"/>") + 2].replace(b'tradeID="900001"', b'tradeID="900009"') + b"</Trades>", 1)


def _legacy_row(**over) -> models.Transaction:
    base = dict(symbol="ACWD", market="LSE", name="x", type=models.TransactionType.BUY, quantity=Decimal("1"), price=Decimal("325.05"), currency="USD",
                fx_rate_to_twd=Decimal("31.42"), trade_date=datetime(2026, 6, 2, tzinfo=timezone.utc), fee=Decimal("1.78"), tax=Decimal("0"), broker="IB", import_fingerprint=None)
    return models.Transaction(**{**base, **over})


def _acwd_one(db_session):
    return db_session.query(models.Transaction).filter_by(broker="IB", symbol="ACWD", quantity=Decimal("1"))


def test_ib_flex_import_creates_rows_once_and_dedupes_by_fingerprint_on_the_second_upload(client, db_session):
    _seed_fx(db_session, *JUNE)
    first = _post(client, FLEX).json()
    assert (first["parsed"], first["created"], first["skipped_duplicates"], first["legacy_duplicates"], first["candidates"], first["csv_format"]) == (11, 11, 0, 0, 11, "ib-flex")
    assert first["error_counts"] == {"parse": 3, "ambiguous": 0, "commit": 0}
    assert sorted(e["reason"] for e in first["errors"]) == ["unsupported assetCategory OPT", "unsupported section CorporateActions", "unsupported section Transfers"]
    assert db_session.query(models.Transaction).filter_by(broker="IB").count() == 4
    assert db_session.query(models.BrokerCashFlow).filter_by(broker="IB").count() == 7
    second = _post(client, FLEX).json()  # the daily case: no legacy_until, fingerprints alone
    assert (second["parsed"], second["created"], second["skipped_duplicates"], second["legacy_duplicates"], second["candidates"], second["recalc_scheduled"]) == (11, 0, 11, 0, 0, False)


def test_ib_flex_import_with_legacy_until_pairs_the_manual_era_rows_and_they_adopt_the_flex_fingerprint(client, db_session):
    _seed_fx(db_session, *JUNE)
    csv = (FIXTURES / "ib_sample.csv").read_bytes()
    assert client.post("/api/portfolio/imports/csv", files={"file": ("ib.csv", BytesIO(csv), "text/csv")}).json()["created"] == 4
    before = {t.id: t.import_fingerprint for t in db_session.query(models.Transaction).filter_by(broker="IB")}
    # CSV rows: market US, CSV fingerprint, deposit note = the CSV 說明; the Flex rows differ on all three → business key only
    body = _post(client, FLEX, legacy_until="2026-06-30").json()
    assert (body["parsed"], body["skipped_duplicates"], body["legacy_duplicates"], body["created"], body["candidates"]) == (11, 0, 4, 7, 7)
    assert sorted(p["kind"] for p in body["legacy_pairs"]) == ["cash_flow", "transaction", "transaction", "transaction"]
    assert db_session.query(models.BrokerCashFlow).filter_by(broker="IB", type="deposit").count() == 1
    assert db_session.query(models.Transaction).filter_by(broker="IB", symbol="ACWD").count() == 2  # 1 + 6 from the CSV, nothing new
    after = {t.id: (t.import_fingerprint, t.market) for t in db_session.query(models.Transaction).filter_by(broker="IB")}
    assert set(before) <= set(after) and len(after) == len(before) + 1          # the three CSV rows kept their ids; one new row (the 06-05 sell)
    assert all(after[i][0] != before[i] and after[i][1] == "US" for i in before)  # adoption changes identity only, never market
    assert [after[i][1] for i in after if i not in before] == ["LSE"]              # the new Flex row takes its market from listingExchange
    again = _post(client, FLEX, legacy_until="2026-06-30").json()  # adopted rows are plain fingerprint duplicates now
    assert (again["legacy_duplicates"], again["skipped_duplicates"], again["created"], again["candidates"]) == (0, 11, 0, 0)
    daily = _post(client, FLEX).json()                                # and so is a later run without legacy_until
    assert (daily["legacy_duplicates"], daily["skipped_duplicates"], daily["created"]) == (0, 11, 0)


def test_ib_flex_import_dry_run_reports_pairs_but_adopts_nothing(client, db_session):
    _seed_fx(db_session, *JUNE)
    db_session.add(_legacy_row()); db_session.commit()
    body = _post(client, FLEX, legacy_until="2026-06-30", dry_run="true").json()
    assert body["legacy_duplicates"] == 1 and body["legacy_pairs"][0]["kind"] == "transaction" and body["candidates"] == 10 and body["created"] == 0
    assert _acwd_one(db_session).one().import_fingerprint is None and db_session.query(models.Transaction).count() == 1


def test_ib_flex_import_without_legacy_until_never_pairs(client, db_session):
    _seed_fx(db_session, *JUNE)
    db_session.add(_legacy_row()); db_session.commit()
    body = _post(client, FLEX).json()
    assert body["legacy_duplicates"] == 0 and _acwd_one(db_session).count() == 2


def test_ib_flex_import_legacy_until_scopes_by_date(client, db_session):
    _seed_fx(db_session, *JUNE)
    db_session.add(_legacy_row())                                                                   # 06-02: in scope, paired
    db_session.add(_legacy_row(trade_date=datetime(2026, 6, 5, tzinfo=timezone.utc), type=models.TransactionType.SELL, quantity=Decimal("10"), price=Decimal("28.1"), tax=Decimal("0.5"), symbol="AVGS"))  # the Flex sell's twin, but past `until`
    db_session.commit()
    body = _post(client, FLEX, legacy_until="2026-06-03").json()
    assert body["legacy_duplicates"] == 1 and _acwd_one(db_session).count() == 1
    assert db_session.query(models.Transaction).filter_by(broker="IB", symbol="AVGS", type=models.TransactionType.SELL).count() == 2


@pytest.mark.parametrize("field, value", [("currency", "GBP"), ("fee", Decimal("1.79")), ("tax", Decimal("0.01")), ("price", Decimal("325.06"))])
def test_ib_flex_import_legacy_key_separates_currency_fee_tax_and_price(client, db_session, field, value):
    _seed_fx(db_session, *JUNE)
    db_session.add(_legacy_row(**{field: value})); db_session.commit()                             # same day, one field off: another event
    body = _post(client, FLEX, legacy_until="2026-06-30").json()
    assert body["legacy_duplicates"] == 0 and _acwd_one(db_session).count() == 2


def test_ib_flex_import_legacy_key_ignores_market_and_name(client, db_session):
    _seed_fx(db_session, *JUNE)
    db_session.add(_legacy_row(market="US", name="whatever the CSV said")); db_session.commit()   # the hand-correction case
    body = _post(client, FLEX, legacy_until="2026-06-30").json()
    assert body["legacy_duplicates"] == 1 and _acwd_one(db_session).count() == 1 and _acwd_one(db_session).one().market == "US"


def test_ib_flex_import_reports_an_ambiguous_legacy_match_instead_of_guessing(client, db_session):
    _seed_fx(db_session, *JUNE)
    db_session.add(_legacy_row()); db_session.add(_legacy_row(name="y")); db_session.commit()  # two old rows, one Flex row
    body = _post(client, FLEX, legacy_until="2026-06-30").json()
    assert body["legacy_duplicates"] == 0 and body["error_counts"] == {"parse": 3, "ambiguous": 1, "commit": 0} and body["candidates"] == 10
    assert sum(e["reason"] == "ambiguous legacy match" for e in body["errors"]) == 1
    assert _acwd_one(db_session).count() == 2 and all(t.import_fingerprint is None for t in _acwd_one(db_session))  # untouched
    assert body["created"] == 10


def test_ib_flex_import_one_old_row_and_two_new_fills_is_ambiguous_not_a_silent_merge(client, db_session):
    _seed_fx(db_session, *JUNE)
    db_session.add(_legacy_row()); db_session.commit()
    body = _post(client, TWIN, legacy_until="2026-06-30").json()
    assert sum(e["reason"] == "ambiguous legacy match" for e in body["errors"]) == 2 and body["legacy_duplicates"] == 0
    assert _acwd_one(db_session).count() == 1


def test_ib_flex_import_keeps_two_same_day_fills_apart_by_trade_id(client, db_session):
    _seed_fx(db_session, *JUNE)
    body = _post(client, TWIN).json()
    assert body["created"] == 12 and _acwd_one(db_session).count() == 2


def test_ib_flex_import_pairs_the_identified_rows_first(client, db_session):
    """One old CSV-era row and one Flex row already imported for the same key: the Flex row is identified by its
    fingerprint, the old row pairs with nothing new → nothing ambiguous, nothing adopted, nothing created."""
    _seed_fx(db_session, *JUNE)
    assert _post(client, FLEX).json()["created"] == 11                       # Flex rows exist with Flex fingerprints
    db_session.add(_legacy_row()); db_session.commit()                        # plus an unidentified old twin
    body = _post(client, FLEX, legacy_until="2026-06-30").json()
    assert (body["legacy_duplicates"], body["skipped_duplicates"], body["created"], body["error_counts"]["ambiguous"]) == (0, 11, 0, 0)
    assert _acwd_one(db_session).count() == 2


def test_ib_flex_import_dry_run_writes_nothing_and_reports_candidates(client, db_session):
    _seed_fx(db_session, *JUNE)
    body = _post(client, FLEX, dry_run="true").json()
    assert body["dry_run"] is True and body["created"] == 0 and body["recalc_scheduled"] is False and body["candidates"] == 11  # parse errors are not rows
    assert db_session.query(models.Transaction).count() == 0 and db_session.query(models.BrokerCashFlow).count() == 0


def test_ib_flex_import_retries_a_row_once_its_fx_rate_exists(client, db_session):
    _seed_fx(db_session, *[d for d in JUNE if d != date(2026, 6, 5)])  # the sell's date has no rate yet
    first = _post(client, FLEX).json()
    assert any(e["reason"] == "missing FX rate for 2026-06-05 USD" for e in first["errors"]) and first["created"] == 10
    assert first["error_counts"] == {"parse": 3, "ambiguous": 0, "commit": 1} and first["candidates"] == 10
    _seed_fx(db_session, date(2026, 6, 5))
    second = _post(client, FLEX).json()
    assert (second["created"], second["skipped_duplicates"], second["candidates"]) == (1, 10, 1)


def test_ib_flex_import_refuses_a_flex_error_document_and_oversize_uploads(client):
    assert _post(client, b"<FlexStatementResponse><Status>Fail</Status><ErrorCode>1020</ErrorCode></FlexStatementResponse>").status_code == 400
    assert _post(client, b"<FlexQueryResponse>" + b" " * (5 * 1024 * 1024) + b"</FlexQueryResponse>").status_code == 413
