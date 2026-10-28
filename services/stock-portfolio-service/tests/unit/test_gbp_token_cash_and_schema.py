"""#124 / #125: the exact ``GBp`` (pence) token survives the transaction schema, and a pence dividend's cash leg is
posted in pounds. Fictional amounts only."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.schemas.portfolio import TransactionCreate
from app.services.cash_account_service import dividend_cash_amount


def _create(currency: str) -> TransactionCreate:
    return TransactionCreate(symbol="vod", market="lse", type="BUY", quantity=Decimal("10"), price=Decimal("72.5"),
                             currency=currency, fx_rate_to_twd=Decimal("0.41"))


@pytest.mark.parametrize("raw", ["GBp", " GBp "])
def test_transaction_schema_keeps_the_pence_token(raw: str) -> None:
    assert _create(raw).currency == "GBp"


@pytest.mark.parametrize(("raw", "expected"), [("gbp", "GBP"), ("GBP", "GBP"), ("usd", "USD"), ("Gbp", "GBP")])
def test_transaction_schema_uppercases_every_other_code(raw: str, expected: str) -> None:
    assert _create(raw).currency == expected


@pytest.mark.parametrize(("currency", "amount", "expected"), [
    ("GBp", "1234.5650", "12.3457"),  # pence → pounds, half-up to the cash column's 4 places
    (" GBp ", "50", "0.5000"),
    ("GBP", "12.34", "12.34"),  # pounds are never divided
    ("USD", "7.5", "7.5"),
    ("TWD", "100", "100"),
])
def test_dividend_cash_amount(currency: str, amount: str, expected: str) -> None:
    assert dividend_cash_amount(SimpleNamespace(currency=currency, amount=Decimal(amount))) == Decimal(expected)


# --- AGENT-327: the broker import path and the transaction cash legs -----------------------------------------------

from datetime import date, datetime, timezone  # noqa: E402

from app.models import portfolio as models  # noqa: E402
from app.models.broker_account import BrokerAccount, BrokerEnum  # noqa: E402
from app.models.cash_transaction import CashTransaction  # noqa: E402
from app.models.fx_rate import FXRate  # noqa: E402
from app.services import cash_backfill_service, import_service  # noqa: E402

TRADE_DAY = date(2026, 5, 14)


def _seed_gbp(db_session) -> None:
    db_session.add(FXRate(currency="GBP", date=TRADE_DAY, rate_to_twd=Decimal("41.23456789"), source="test"))
    db_session.commit()


def test_exact_fx_rate_reads_gbp_per_penny_for_the_pence_token(db_session) -> None:
    _seed_gbp(db_session)
    assert import_service._exact_fx_rate(db_session, "GBp", TRADE_DAY) == Decimal("0.41234568")
    assert import_service._exact_fx_rate(db_session, "GBP", TRADE_DAY) == Decimal("41.23456789")
    assert import_service._exact_fx_rate(db_session, "TWD", TRADE_DAY) is None


def _broker_row(currency: str) -> import_service.ParsedRow:
    return import_service.ParsedRow(row_index=1, fingerprint=f"fictional-{currency}", payload={
        "_kind": "transaction", "broker": models.Broker.IB.value, "symbol": "VOD", "market": "LSE", "name": None,
        "type": "BUY", "position_side": models.PositionSide.LONG.value, "quantity": Decimal("100"),
        "price": Decimal("72.5"), "currency": currency,
        "trade_date": datetime(2026, 5, 14, 9, 0, tzinfo=timezone.utc), "fee": Decimal("300"), "tax": Decimal("0")})


def test_broker_import_keeps_the_pence_token_and_a_per_penny_rate(db_session) -> None:
    _seed_gbp(db_session)
    result = import_service.commit_transactions(
        db_session, import_service.ParseResult(rows=[_broker_row("GBp")], errors=[]), dry_run=False)
    assert result.created == 1 and result.errors == []
    tx = db_session.query(models.Transaction).one()
    assert (tx.currency, tx.fx_rate_to_twd) == ("GBp", Decimal("0.41234568"))


def test_backfill_posts_a_gbp_pence_trade_in_pounds(db_session) -> None:
    account = BrokerAccount(broker=BrokerEnum.IB, nickname="IB GBP", currency="GBP", opening_balance=Decimal("0"),
                            opening_date=date(2025, 1, 1), is_active=True)
    cathay = BrokerAccount(broker=BrokerEnum.CATHAY, nickname="Cathay TWD", currency="TWD",
                           opening_balance=Decimal("0"), opening_date=date(2025, 1, 1), is_active=True)
    pence = models.Transaction(symbol="VOD", market="LSE", name="Test", type=models.TransactionType.BUY, quantity=100,
                               price=Decimal("72.5"), currency="GBp", trade_date=datetime(2026, 5, 14, 9, 0,
                               tzinfo=timezone.utc), fee=Decimal("300"), tax=Decimal("0"),
                               broker=models.Broker.IB.value, import_fingerprint="fictional-gbp-1")
    db_session.add_all([account, cathay, pence])
    db_session.commit()
    cash_backfill_service.replay_all(db_session)
    legs = {row.type.value: row.amount for row in db_session.query(CashTransaction).filter(
        CashTransaction.related_transaction_id == pence.id)}
    assert all(row.account_id == account.id for row in db_session.query(CashTransaction))
    assert legs == {"buy_settle": Decimal("-72.5000"), "fee": Decimal("-3.0000")}  # 7250p, 300p
