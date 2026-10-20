from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import pytest

from app.models import portfolio as models
from app.models.fx_rate import FXRate
from app.models.symbol_map import SymbolMap
from app.services import broker_ib_flex_service
from app.services.cash_flow_service import cash_flow_fingerprint
from app.services.import_service import _broker_transaction_fingerprint

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
    assert first["error_counts"] == {"parse": 3, "ambiguous": 0, "commit": 0, "stale_fx": 0}
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
    assert body["legacy_duplicates"] == 0 and body["error_counts"] == {"parse": 3, "ambiguous": 1, "commit": 0, "stale_fx": 0} and body["candidates"] == 10
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
    assert first["error_counts"] == {"parse": 3, "ambiguous": 0, "commit": 1, "stale_fx": 1} and first["candidates"] == 10
    _seed_fx(db_session, date(2026, 6, 5))
    second = _post(client, FLEX).json()
    assert (second["created"], second["skipped_duplicates"], second["candidates"]) == (1, 10, 1)


def test_ib_flex_import_refuses_a_flex_error_document_and_oversize_uploads(client):
    assert _post(client, b"<FlexStatementResponse><Status>Fail</Status><ErrorCode>1020</ErrorCode></FlexStatementResponse>").status_code == 400
    assert _post(client, b"<FlexQueryResponse>" + b" " * (5 * 1024 * 1024) + b"</FlexQueryResponse>").status_code == 413


def _legacy_deposit(**over) -> models.BrokerCashFlow:
    base = dict(broker="IB", date=date(2026, 6, 1), type="deposit", amount=Decimal("3000"), currency="USD", fx_rate_to_twd=Decimal("31.42"), import_fingerprint=None)
    return models.BrokerCashFlow(**{**base, **over})


def test_ib_flex_import_legacy_pass_sees_the_symbol_map_market_override(client, db_session):
    _seed_fx(db_session, *JUNE)
    db_session.add(SymbolMap(name="ACWD UCITS", symbol="ACWD", market="US")); db_session.commit()  # disagrees with listingExchange LSE
    assert _post(client, FLEX).json()["created"] == 11
    again = _post(client, FLEX, legacy_until="2026-06-30").json()
    assert (again["legacy_duplicates"], again["skipped_duplicates"], again["created"]) == (0, 11, 0)
    daily = _post(client, FLEX).json()
    assert (daily["legacy_duplicates"], daily["skipped_duplicates"], daily["created"]) == (0, 11, 0)
    assert db_session.query(models.Transaction).filter_by(broker="IB").count() == 4


def test_ib_flex_import_pairs_a_four_decimal_commission_with_the_two_decimal_stored_fee(client, db_session):
    _seed_fx(db_session, *JUNE)
    db_session.add(_legacy_row()); db_session.commit()  # fee stored 1.78
    raw = FLEX.replace(b'ibCommission="-1.78"', b'ibCommission="-1.7834"', 1)
    body = _post(client, raw, legacy_until="2026-06-30").json()
    assert body["legacy_duplicates"] == 1 and _acwd_one(db_session).count() == 1


def test_ib_flex_import_duplicate_trade_in_one_file_pairs_once_and_does_not_500(client, db_session):
    _seed_fx(db_session, *JUNE)
    db_session.add(_legacy_row()); db_session.commit()
    first_trade = FLEX[FLEX.index(b"<Trade "):FLEX.index(b"/>") + 2]
    dup = FLEX.replace(b"</Trades>", first_trade + b"</Trades>", 1)
    resp = _post(client, dup, legacy_until="2026-06-30")
    assert resp.status_code == 200
    body = resp.json()
    assert (body["legacy_duplicates"], body["created"], body["skipped_duplicates"]) == (1, 10, 1)
    assert _acwd_one(db_session).count() == 1


def test_ib_flex_import_cash_flow_pairing_adopts_the_parsed_fingerprint(client, db_session):
    _seed_fx(db_session, *JUNE)
    db_session.add(_legacy_deposit()); db_session.commit()
    body = _post(client, FLEX, legacy_until="2026-06-30").json()
    assert body["legacy_duplicates"] == 1 and body["legacy_pairs"][0]["kind"] == "cash_flow"
    expected = next(r.fingerprint for r in broker_ib_flex_service.parse(FLEX).rows if r.payload.get("_kind") == "cash_flow" and r.payload["cash_flow_type"] == "deposit")
    assert db_session.query(models.BrokerCashFlow).filter_by(broker="IB", type="deposit").one().import_fingerprint == expected


def test_ib_flex_import_two_old_deposits_and_one_flex_deposit_is_ambiguous(client, db_session):
    _seed_fx(db_session, *JUNE)
    db_session.add(_legacy_deposit()); db_session.add(_legacy_deposit()); db_session.commit()
    body = _post(client, FLEX, legacy_until="2026-06-30").json()
    assert body["legacy_duplicates"] == 0 and body["error_counts"]["ambiguous"] == 1
    assert db_session.query(models.BrokerCashFlow).filter_by(broker="IB", type="deposit").count() == 2
    assert all(c.import_fingerprint is None for c in db_session.query(models.BrokerCashFlow).filter_by(broker="IB", type="deposit"))


def test_stale_fx_count_only_counts_missing_rates_older_than_three_days():
    from app.routers.imports import _stale_fx_count
    from app.services.import_service import ParseError
    errors = [
        ParseError(row_index=1, message="missing FX rate for 2026-06-16 USD"),
        ParseError(row_index=2, message="missing FX rate for 2026-06-18 USD"),
        ParseError(row_index=3, message="unsupported section Transfers"),
    ]
    assert _stale_fx_count(errors, date(2026, 6, 20)) == 1


# Provenance: an existing row with a fingerprint is manual-era only if that fingerprint is the CSV-style one
# recomputed from its stored fields; a Flex-origin row never pairs, even when a later file lacks its event.
B_ONLY = FLEX.replace(b'tradeID="900001"', b'tradeID="900009"', 1)          # same key as A (900001), another execution
B_ONLY_CASH = FLEX.replace(b'transactionID="800010"', b'transactionID="800019"', 1)
TWIN_CASH = FLEX.replace(b"</CashTransactions>", FLEX[FLEX.index(b"<CashTransaction "):FLEX.index(b"/>", FLEX.index(b"<CashTransaction ")) + 2].replace(b'transactionID="800010"', b'transactionID="800019"') + b"</CashTransactions>", 1)


def _deposits(db_session):
    return db_session.query(models.BrokerCashFlow).filter_by(broker="IB", type="deposit").order_by(models.BrokerCashFlow.id)


def test_ib_flex_import_a_flex_row_missing_from_a_subset_file_is_never_re_paired(client, db_session):
    _seed_fx(db_session, *JUNE)
    assert _post(client, FLEX).json()["created"] == 11                                    # event A inserted with its Flex fingerprint
    a_id, a_fp = _acwd_one(db_session).with_entities(models.Transaction.id, models.Transaction.import_fingerprint).one()
    body = _post(client, B_ONLY, legacy_until="2026-06-30").json()                         # B only: A is absent from the file
    assert (body["legacy_duplicates"], body["created"]) == (0, 1)
    assert _acwd_one(db_session).count() == 2
    assert db_session.query(models.Transaction).filter_by(id=a_id).one().import_fingerprint == a_fp
    daily = _post(client, TWIN).json()                                                     # A + B: both identified
    assert (daily["created"], daily["skipped_duplicates"]) == (0, 12)


def test_ib_flex_import_a_flex_cash_flow_missing_from_a_subset_file_is_never_re_paired(client, db_session):
    _seed_fx(db_session, *JUNE)
    assert _post(client, FLEX).json()["created"] == 11
    a_id, a_fp = _deposits(db_session).with_entities(models.BrokerCashFlow.id, models.BrokerCashFlow.import_fingerprint).one()
    body = _post(client, B_ONLY_CASH, legacy_until="2026-06-30").json()
    assert (body["legacy_duplicates"], body["created"]) == (0, 1)
    assert _deposits(db_session).count() == 2
    assert db_session.query(models.BrokerCashFlow).filter_by(id=a_id).one().import_fingerprint == a_fp
    daily = _post(client, TWIN_CASH).json()
    assert (daily["created"], daily["skipped_duplicates"]) == (0, 12)


def test_ib_flex_import_an_adopted_row_is_never_re_paired(client, db_session):
    _seed_fx(db_session, *JUNE)
    csv = (FIXTURES / "ib_sample.csv").read_bytes()
    assert client.post("/api/portfolio/imports/csv", files={"file": ("ib.csv", BytesIO(csv), "text/csv")}).json()["created"] == 4
    assert _post(client, FLEX, legacy_until="2026-06-30").json()["legacy_duplicates"] == 4  # three trades + the deposit adopt
    adopted_tx = _acwd_one(db_session).one().import_fingerprint
    adopted_cf = _deposits(db_session).one().import_fingerprint
    body = _post(client, B_ONLY, legacy_until="2026-06-30").json()
    assert (body["legacy_duplicates"], body["created"]) == (0, 1)
    assert _acwd_one(db_session).count() == 2
    assert _acwd_one(db_session).order_by(models.Transaction.id).first().import_fingerprint == adopted_tx
    cash = _post(client, B_ONLY_CASH, legacy_until="2026-06-30").json()
    assert (cash["legacy_duplicates"], cash["created"]) == (0, 1)
    assert _deposits(db_session).count() == 2 and _deposits(db_session).first().import_fingerprint == adopted_cf


def test_ib_flex_import_a_manual_era_row_with_a_hand_corrected_market_still_pairs(client, db_session):
    _seed_fx(db_session, *JUNE)
    csv_fp = _broker_transaction_fingerprint(
        broker="IB", symbol="ACWD", market="US", type_="BUY", quantity=Decimal("1"), price=Decimal("325.05"),
        trade_date=datetime(2026, 6, 2, tzinfo=timezone.utc), fee=Decimal("1.78"), tax=Decimal("0"), currency="USD", note="x",
    )
    db_session.add(_legacy_row(market="LSE", import_fingerprint=csv_fp)); db_session.commit()  # CSV guessed US, corrected to LSE since
    body = _post(client, FLEX, legacy_until="2026-06-30").json()
    assert body["legacy_duplicates"] == 1 and _acwd_one(db_session).count() == 1


def test_ib_flex_import_a_manual_era_cash_flow_with_its_csv_fingerprint_still_pairs(client, db_session):
    _seed_fx(db_session, *JUNE)
    csv_fp = cash_flow_fingerprint(broker="IB", date_=date(2026, 6, 1), type_="deposit", amount=Decimal("3000"), currency="USD", note="電子資金轉帳")
    db_session.add(_legacy_deposit(note="電子資金轉帳", import_fingerprint=csv_fp)); db_session.commit()
    body = _post(client, FLEX, legacy_until="2026-06-30").json()
    assert body["legacy_duplicates"] == 1 and _deposits(db_session).count() == 1


def test_ib_flex_import_a_row_with_an_unrecognised_fingerprint_never_pairs(client, db_session):
    _seed_fx(db_session, *JUNE)
    db_session.add(_legacy_row(import_fingerprint="deadbeef" * 8))
    db_session.add(_legacy_deposit(import_fingerprint="feedface" * 8)); db_session.commit()
    body = _post(client, FLEX, legacy_until="2026-06-30").json()
    assert body["legacy_duplicates"] == 0 and _acwd_one(db_session).count() == 2 and _deposits(db_session).count() == 2
