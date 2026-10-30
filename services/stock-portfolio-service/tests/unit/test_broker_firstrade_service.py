from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.models import portfolio as models
from app.models.fx_rate import FXRate
from app.services import broker_firstrade_service, import_service
from app.services.quotes import fx_rate_service as quote_fx_rate_service


FIXTURE = Path(__file__).parent / "fixtures/firstrade_sample.csv"


@pytest.fixture(autouse=True)
def backfill_calls(monkeypatch):
    """Record the missing-FX backfill (conftest already keeps it off the network)."""
    calls: list[str] = []

    def fake_backfill(db, period):
        calls.append(period)
        return quote_fx_rate_service.RefreshResult(ok_count=0, skipped_count=0, errors=[])

    monkeypatch.setattr(import_service, "_fetch_fx_history", fake_backfill)
    return calls


def _seed_usd_fx(db_session, *dates: date) -> None:
    for d in dates:
        db_session.merge(
            FXRate(
                currency="USD",
                date=d,
                rate_to_twd=Decimal("31.42000000"),
                source="test",
            )
        )
    db_session.commit()


def test_parse_firstrade_sample_emits_equity_and_cash_rows() -> None:
    parsed = broker_firstrade_service.parse(FIXTURE.read_bytes())

    assert parsed.errors == []
    assert len([row for row in parsed.rows if row.payload["_kind"] == "transaction"]) == 8
    assert len([row for row in parsed.rows if row.payload["_kind"] == "cash_flow"]) == 3
    buy = parsed.rows[0].payload
    assert buy["broker"] == "FIRSTRADE"
    assert buy["market"] == "US"
    assert buy["currency"] == "USD"
    assert buy["symbol"] == "UUUU"
    assert buy["type"] == "BUY"
    assert buy["quantity"] == Decimal("10")
    assert buy["price"] == Decimal("15.65")
    assert buy["fee"] == Decimal("0")
    assert "account_class" not in buy
    sell = next(
        row.payload
        for row in parsed.rows
        if row.payload["_kind"] == "transaction" and row.payload["type"] == "SELL"
    )
    assert sell["quantity"] == Decimal("27")
    assert sell["price"] == Decimal("18.71")
    deposit = next(row.payload for row in parsed.rows if row.payload.get("cash_flow_type") == "deposit")
    assert deposit["amount"] == Decimal("2500.00")
    interest = next(row.payload for row in parsed.rows if row.payload.get("cash_flow_type") == "interest")
    assert interest["amount"] == Decimal("0.05")


def test_firstrade_commit_populates_fx_and_is_idempotent(db_session) -> None:
    _seed_usd_fx(
        db_session,
        date(2026, 4, 16),
        date(2026, 5, 14),
        date(2026, 5, 15),
        date(2026, 5, 18),
        date(2026, 6, 3),
        date(2026, 6, 4),
        date(2026, 6, 5),
    )
    parsed = broker_firstrade_service.parse(FIXTURE.read_bytes())

    first = import_service.commit_transactions(db_session, parsed, dry_run=False)
    second = import_service.commit_transactions(db_session, parsed, dry_run=False)

    assert first.errors == []
    assert first.created == 11
    assert second.created == 0
    assert second.skipped_duplicates == 11
    tx = db_session.query(models.Transaction).filter_by(symbol="UUUU").first()
    assert tx.broker == "FIRSTRADE"
    assert tx.fx_rate_to_twd == Decimal("31.42000000")
    assert db_session.query(models.BrokerCashFlow).count() == 3


def test_firstrade_missing_fx_rejects_only_that_row(db_session) -> None:
    _seed_usd_fx(db_session, date(2026, 6, 5))
    raw = (
        '"日期","交易類別","數量","說明","代號","賬戶類別","價格","金額"\n'
        '"2026/6/5","買進","10","Energy Fuels Inc","UUUU","融資","15.65","-156.50"\n'
        '"2026/6/4","買進","27","Energy Fuels Inc","UUUU","融資","17.99","-485.73"\n'
    ).encode("utf-8")
    parsed = broker_firstrade_service.parse(raw)

    result = import_service.commit_transactions(db_session, parsed, dry_run=False)

    assert result.created == 1
    assert len(result.errors) == 1
    assert result.errors[0].row_index == 2
    assert "missing FX rate for 2026-06-04 USD" in result.errors[0].message


_HEADER = '"日期","交易類別","數量","說明","代號","賬戶類別","價格","金額"\n'


def _csv(*lines: str) -> bytes:
    return (_HEADER + "\n".join(lines) + "\n").encode("utf-8")


def _cash_rows(parsed):
    return [row.payload for row in parsed.rows if row.payload["_kind"] == "cash_flow"]


def test_parse_maps_withdrawal_dividend_and_wire_rebate() -> None:
    parsed = broker_firstrade_service.parse(
        _csv(
            '"2026/9/23","提款","0","ACH DISBURSEMENT SEN(2026092200991319)","","融資","0.00","-3,000.00"',
            '"2026/9/4","股息","0","ISHARES TRUST CASH DIV ON 40 SHS NON-RES TAX WITHHELD $3.68000","SGOV","融資","0.00","12.28"',
            '"2026/6/8","其他","0","rebate for wire 2026-06-05","","現金","0.00","25.00"',
        )
    )

    assert parsed.errors == []
    withdrawal, dividend, rebate = _cash_rows(parsed)
    assert (withdrawal["cash_flow_type"], withdrawal["amount"]) == ("withdrawal", Decimal("-3000.00"))
    assert (dividend["cash_flow_type"], dividend["amount"]) == ("dividend_cash", Decimal("12.28"))
    assert (rebate["cash_flow_type"], rebate["amount"]) == ("deposit", Decimal("25.00"))
    assert rebate["note"] == "rebate for wire 2026-06-05"


def test_parse_skips_internal_cash_margin_transfers() -> None:
    parsed = broker_firstrade_service.parse(
        _csv(
            '"2026/6/8","其他","0","XFER CASH TO MARGIN","","現金","0.00","-25.00"',
            '"2026/6/8","其他","0","XFER MARGIN TO CASH","","融資","0.00","25.00"',
        )
    )

    assert parsed.rows == []
    assert parsed.errors == []


def test_parse_reports_unknown_rows_instead_of_dropping_them() -> None:
    parsed = broker_firstrade_service.parse(
        _csv('"2026/6/8","其他","0","SOMETHING NEW","","現金","0.00","-3.00"')
    )

    assert parsed.rows == []
    assert len(parsed.errors) == 1
    assert "其他" in parsed.errors[0].message
    assert "SOMETHING NEW" in parsed.errors[0].message


def test_parse_strips_broker_noise_from_names() -> None:
    parsed = broker_firstrade_service.parse(
        _csv(
            '"2026/10/26","買進","1","***CAMECO CORP UNSOLICITED S/D: 10/27/2026","CCJ","融資","90.00","-90.00"',
            '"2026/10/25","買進","1","VANECK ETF TR VANECK SEMICONDUCTOR ETF UNSOLICITED EXEC TIME: 2026-10-25 23:02:17 S/D: 10/27/2026","SMH","融資","300.00","-300.00"',
        )
    )

    names = [row.payload["name"] for row in parsed.rows]
    assert names == ["CAMECO CORP", "VANECK ETF TR VANECK SEMICONDUCTOR ETF"]


def test_parse_prefers_a_clean_description_for_the_whole_symbol() -> None:
    parsed = broker_firstrade_service.parse(
        _csv(
            '"2026/10/26","買進","1","***CAMECO CORP UNSOLICITED S/D: 10/27/2026","CCJ","融資","90.00","-90.00"',
            '"2026/10/20","買進","1","Cameco Corporation","CCJ","融資","80.00","-80.00"',
        )
    )

    assert {row.payload["name"] for row in parsed.rows} == {"Cameco Corporation"}


def test_trade_fingerprint_ignores_the_description() -> None:
    noisy = broker_firstrade_service.parse(
        _csv('"2026/6/5","買進","10","***ENERGY FUELS INC UNSOLICITED S/D: 06/08/2026","UUUU","融資","15.65","-156.50"')
    )
    clean = broker_firstrade_service.parse(
        _csv('"2026/6/5","買進","10","Energy Fuels Inc","UUUU","融資","15.65","-156.50"')
    )

    assert noisy.rows[0].fingerprint == clean.rows[0].fingerprint


def test_identical_fills_in_one_file_are_kept_and_reimport_is_duplicate(db_session) -> None:
    _seed_usd_fx(db_session, date(2026, 6, 5))
    raw = _csv(
        '"2026/6/5","買進","10","Energy Fuels Inc","UUUU","融資","15.65","-156.50"',
        '"2026/6/5","買進","10","Energy Fuels Inc","UUUU","融資","15.65","-156.50"',
    )

    first = import_service.commit_transactions(db_session, broker_firstrade_service.parse(raw), dry_run=False)
    second = import_service.commit_transactions(db_session, broker_firstrade_service.parse(raw), dry_run=False)

    assert first.created == 2
    assert second.created == 0
    assert second.skipped_duplicates == 2


def _add_existing_trade(db_session, *, name: str, fingerprint: str | None) -> None:
    from datetime import datetime, timezone

    db_session.add(
        models.Transaction(
            symbol="UUUU",
            market="US",
            name=name,
            type=models.TransactionType.BUY,
            position_side=models.PositionSide.LONG,
            quantity=Decimal("10"),
            price=Decimal("15.65"),
            currency="USD",
            fx_rate_to_twd=Decimal("31.42"),
            trade_date=datetime(2026, 6, 5, tzinfo=timezone.utc),
            fee=Decimal("0"),
            tax=Decimal("0"),
            broker="FIRSTRADE",
            import_fingerprint=fingerprint,
        )
    )
    db_session.commit()


def test_trade_already_stored_under_another_fingerprint_is_duplicate(db_session) -> None:
    _seed_usd_fx(db_session, date(2026, 6, 5))
    _add_existing_trade(db_session, name="Energy Fuels Inc", fingerprint="legacy-name-based-hash")
    raw = _csv(
        '"2026/6/5","買進","10","***ENERGY FUELS INC UNSOLICITED S/D: 06/08/2026","UUUU","融資","15.65","-156.50"',
        '"2026/6/5","買進","20","***ENERGY FUELS INC UNSOLICITED S/D: 06/08/2026","UUUU","融資","15.00","-300.00"',
    )

    result = import_service.commit_transactions(db_session, broker_firstrade_service.parse(raw), dry_run=False)

    assert result.created == 1
    assert result.skipped_duplicates == 1
    names = {tx.name for tx in db_session.query(models.Transaction).filter_by(symbol="UUUU")}
    assert names == {"Energy Fuels Inc"}


def test_cash_flow_already_stored_under_another_fingerprint_is_duplicate(db_session) -> None:
    _seed_usd_fx(db_session, date(2026, 9, 23))
    db_session.add(
        models.BrokerCashFlow(
            broker="FIRSTRADE",
            date=date(2026, 9, 23),
            type="withdrawal",
            amount=Decimal("-3000"),
            currency="USD",
            fx_rate_to_twd=Decimal("31.42"),
            note="ACH DISBURSEMENT SEN(2026092200991319)",
            import_fingerprint="firstrade-cashfix|manual",
        )
    )
    db_session.commit()
    raw = _csv('"2026/9/23","提款","0","ACH DISBURSEMENT SEN(2026092200991319)","","融資","0.00","-3,000.00"')

    result = import_service.commit_transactions(db_session, broker_firstrade_service.parse(raw), dry_run=False)

    assert result.created == 0
    assert result.skipped_duplicates == 1
    assert db_session.query(models.BrokerCashFlow).count() == 1


def test_missing_fx_falls_back_to_the_latest_prior_rate(db_session) -> None:
    _seed_usd_fx(db_session, date(2026, 6, 5))
    raw = _csv('"2026/6/7","其他","0","rebate for wire 2026-06-05","","現金","0.00","25.00"')

    result = import_service.commit_transactions(db_session, broker_firstrade_service.parse(raw), dry_run=False)

    assert result.errors == []
    flow = db_session.query(models.BrokerCashFlow).one()
    assert flow.fx_rate_to_twd == Decimal("31.42000000")


def test_missing_fx_still_fails_when_no_recent_prior_rate(db_session) -> None:
    _seed_usd_fx(db_session, date(2026, 5, 1))
    raw = _csv('"2026/6/7","其他","0","rebate for wire 2026-06-05","","現金","0.00","25.00"')

    result = import_service.commit_transactions(db_session, broker_firstrade_service.parse(raw), dry_run=False)

    assert result.created == 0
    assert "missing FX rate for 2026-06-07 USD" in result.errors[0].message


def test_missing_history_is_backfilled_once_before_commit(db_session, monkeypatch, backfill_calls) -> None:
    def fake_backfill(db, period):
        backfill_calls.append(period)
        _seed_usd_fx(db, date(2024, 9, 30), date(2024, 10, 2))
        return quote_fx_rate_service.RefreshResult(ok_count=2, skipped_count=0, errors=[])

    monkeypatch.setattr(import_service, "_fetch_fx_history", fake_backfill)
    raw = _csv(
        '"2024/10/2","其他","0","REBATE FOR WIRE 2024-09-30","","現金","0.00","25.00"',
        '"2024/9/30","存款","0","Wire Funds Received","","融資","0.00","2,600.00"',
    )

    result = import_service.commit_transactions(db_session, broker_firstrade_service.parse(raw), dry_run=False)

    assert result.errors == []
    assert result.created == 2
    assert len(backfill_calls) == 1
    assert backfill_calls[0] in {"2y", "5y"}


def test_no_backfill_when_every_row_has_a_rate(db_session, backfill_calls) -> None:
    _seed_usd_fx(db_session, date(2026, 6, 5))
    raw = _csv('"2026/6/5","買進","10","Energy Fuels Inc","UUUU","融資","15.65","-156.50"')

    import_service.commit_transactions(db_session, broker_firstrade_service.parse(raw), dry_run=False)

    assert backfill_calls == []


def test_parse_gives_one_name_per_symbol_without_a_clean_description() -> None:
    parsed = broker_firstrade_service.parse(
        _csv(
            '"2024/10/22","買進","1","VANGUARD STAR FD VANGUARD TOTAL INTL STOCK INDEX FD ETF SHS UNSOLICITED PRODUCT DESCRIPTION UNDER SEPARATE COVER S/D: 10/23/2024","VXUS","融資","62.87","-62.87"',
            '"2024/10/21","買進","0.47445","VANGUARD STAR FD VANGUARD TOTAL INTL STOCK INDEX FD ETF SHS PRODUCT DESCRIPTION UNDER SEPARATE COVER S/D: 10/22/2024","VXUS","融資","63.2299","-30.00"',
        )
    )

    assert {row.payload["name"] for row in parsed.rows} == {
        "VANGUARD STAR FD VANGUARD TOTAL INTL STOCK INDEX FD ETF SHS"
    }


def test_five_decimal_fractional_share_matches_its_stored_four_decimal_row(db_session) -> None:
    from datetime import datetime, timezone

    _seed_usd_fx(db_session, date(2024, 10, 21))
    db_session.add(
        models.Transaction(
            symbol="VXUS",
            market="US",
            name="Vanguard Total International Stock ETF",
            type=models.TransactionType.BUY,
            position_side=models.PositionSide.LONG,
            quantity=Decimal("0.4745"),  # Numeric(_, 4) rounds 0.47445 half-up
            price=Decimal("63.2299"),
            currency="USD",
            fx_rate_to_twd=Decimal("31.42"),
            trade_date=datetime(2024, 10, 21, tzinfo=timezone.utc),
            fee=Decimal("0"),
            tax=Decimal("0"),
            broker="FIRSTRADE",
            import_fingerprint="legacy",
        )
    )
    db_session.commit()
    raw = _csv('"2024/10/21","買進","0.47445","VANGUARD STAR FD ETF SHS S/D: 10/22/2024","VXUS","融資","63.2299","-30.00"')

    result = import_service.commit_transactions(db_session, broker_firstrade_service.parse(raw), dry_run=False)

    assert (result.created, result.skipped_duplicates) == (0, 1)


def test_a_fingerprint_match_owns_its_stored_row_before_content_matching(db_session) -> None:
    _seed_usd_fx(db_session, date(2026, 6, 5))
    wire_a = '"2026/6/5","存款","0","Wire A","","融資","0.00","100.00"'
    import_service.commit_transactions(db_session, broker_firstrade_service.parse(_csv(wire_a)), dry_run=False)
    raw = _csv('"2026/6/5","存款","0","Wire B","","融資","0.00","100.00"', wire_a)

    result = import_service.commit_transactions(db_session, broker_firstrade_service.parse(raw), dry_run=False)

    assert (result.created, result.skipped_duplicates) == (1, 1)
    notes = {flow.note for flow in db_session.query(models.BrokerCashFlow)}
    assert notes == {"Wire A", "Wire B"}


def test_a_trade_fingerprint_match_owns_its_stored_row_before_content_matching(db_session) -> None:
    _seed_usd_fx(db_session, date(2026, 6, 5))
    fill = '"2026/6/5","買進","10","Energy Fuels Inc","UUUU","融資","15.65","-156.50"'
    raw = _csv(fill, fill)
    parsed = broker_firstrade_service.parse(raw)
    import_service.commit_transactions(db_session, parsed, dry_run=False)
    # Only the file's second fill (#2) stays stored; the first must be re-booked.
    db_session.query(models.Transaction).filter_by(import_fingerprint=parsed.rows[0].fingerprint).delete()
    db_session.commit()

    result = import_service.commit_transactions(db_session, broker_firstrade_service.parse(raw), dry_run=False)

    assert (result.created, result.skipped_duplicates) == (1, 1)
    assert db_session.query(models.Transaction).count() == 2


def test_five_decimal_fills_that_store_alike_get_distinct_fingerprints(db_session) -> None:
    _seed_usd_fx(db_session, date(2026, 6, 5))
    raw = _csv(
        '"2026/6/5","買進","0.47441","ABC CORP","ABC","融資","10.00","-4.74"',
        '"2026/6/5","買進","0.47442","ABC CORP","ABC","融資","10.00","-4.74"',
    )
    parsed = broker_firstrade_service.parse(raw)

    assert parsed.rows[0].fingerprint != parsed.rows[1].fingerprint
    first = import_service.commit_transactions(db_session, parsed, dry_run=False)
    second = import_service.commit_transactions(db_session, broker_firstrade_service.parse(raw), dry_run=False)
    assert (first.created, second.created, second.skipped_duplicates) == (2, 0, 2)
