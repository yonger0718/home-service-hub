from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.models import portfolio as models
from app.models.broker_account import BrokerAccount, BrokerEnum
from app.models.cash_transaction import CashTxnSource
from app.models.cathay_statement import CathayStatementDay
from app.models.corporate_action import CorporateAction
from app.models.price_history import PriceHistory
from app.services import cash_account_service, ledger_feed_service

SETTLEMENTS = "/api/portfolio/ledger-feed/settlements"
VALUATION = "/api/portfolio/ledger-feed/valuation"


def Q(settle_from, settle_to, trade_from, trade_to, broker="cathay"):
    return {"broker": broker, "settle_from": settle_from, "settle_to": settle_to, "from": trade_from, "to": trade_to}


def _day(value: str) -> date:
    return date.fromisoformat(value)


class _Cathay:
    def __init__(self, db):
        self.db = db
        self.account = BrokerAccount(
            broker=BrokerEnum.CATHAY, nickname="Cathay TWD", currency="TWD",
            opening_balance=Decimal("0"), opening_date=date(2026, 1, 1), is_active=True,
        )
        db.add(self.account)
        db.commit()
        self._seq = 0

    def trade(self, day, side, *, qty, price, fee="0", tax="0", symbol="9999", broker="TW_CATHAY", points=False):
        self._seq += 1
        d = _day(day)
        tx = models.Transaction(
            symbol=symbol, market="TW", name="範例電子", type=models.TransactionType(side),
            quantity=qty, price=Decimal(price), trade_date=datetime(d.year, d.month, d.day, tzinfo=timezone.utc),
            fee=Decimal(fee), tax=Decimal(tax), broker=broker, paid_with_points=points,
            import_fingerprint=f"ledger-feed-{self._seq}",
        )
        self.db.add(tx)
        self.db.flush()
        cash_account_service.sync_transaction_cash_legs(self.db, tx, self.account.id, CashTxnSource.CSV_IMPORT)
        self.db.commit()
        return tx

    def statement_day(self, day, *, settlement, net, cash_net, held=None):
        self.db.add(CathayStatementDay(
            trade_date=_day(day), settlement_date=_day(settlement), net_amount=Decimal(net),
            cash_net=None if cash_net is None else Decimal(cash_net),
            last_status="held" if held else "imported", last_reason=held,
        ))
        self.db.commit()

    def price(self, symbol, day, close):
        self.db.add(PriceHistory(symbol=symbol, market="TW", date=_day(day), close=Decimal(close), source="test"))
        self.db.commit()

    def split(self, symbol, day, *, ratio):
        self.db.add(CorporateAction(
            symbol=symbol, market="TW", effective_date=_day(day), ratio=Decimal(ratio),
            source_event_key=f"{symbol}-{day}",
        ))
        self.db.commit()


@pytest.fixture()
def cathay(db_session):
    return _Cathay(db_session)


def test_statement_day_uses_the_printed_date_and_cash_net(client, cathay):
    cathay.trade("2026-09-10", "BUY", qty=1000, price="12.3", fee="5")
    cathay.statement_day("2026-09-10", settlement="2026-09-14", net="-12305", cash_net="-12305")
    body = client.get(SETTLEMENTS, params=Q("2026-09-12", "2026-09-16", "2026-09-12", "2026-09-16")).json()  # found by its printed date
    (row,) = body["rows"]
    assert (row["settlement_date"], row["date_source"], row["net_amount"], row["bookable"], row["reason"]) == ("2026-09-14", "statement", "-12305.0000", True, None)
    assert body["bounds"] == {"settle_from": "2026-09-12", "settle_to": "2026-09-16", "from": "2026-09-12", "to": "2026-09-16"} and body["complete"] is True
    assert {i["type"] for i in row["items"]} == {"buy_settle", "fee"} and row["fingerprint"].startswith("cathay:2026-09-10:")
    assert row["currency"] == "TWD" and row["legs_net"] == "-12305.0000"
    assert all(i["source_id"].startswith("ct:") for i in row["items"])


def test_statement_day_is_also_found_by_its_trade_date_alone(client, cathay):
    cathay.trade("2026-09-10", "BUY", qty=1000, price="12.3", fee="5")
    cathay.statement_day("2026-09-10", settlement="2026-09-14", net="-12305", cash_net="-12305")
    (row,) = client.get(SETTLEMENTS, params=Q("2026-09-10", "2026-09-10", "2026-09-10", "2026-09-10")).json()["rows"]
    assert (row["trade_date"], row["settlement_date"], row["date_source"]) == ("2026-09-10", "2026-09-14", "statement")


def test_csv_only_day_has_no_date_and_other_brokers_are_left_out(client, cathay):
    cathay.trade("2026-09-11", "SELL", qty=100, price="50", fee="7", tax="15")
    cathay.trade("2026-09-11", "BUY", qty=10, price="10", fee="1", broker="TW_MANUAL")
    (row,) = client.get(SETTLEMENTS, params=Q("2026-09-11", "2026-09-11", "2026-09-11", "2026-09-11")).json()["rows"]
    assert (row["settlement_date"], row["date_source"], row["net_amount"], row["legs_net"]) == (None, "legs", "4978.0000", "4978.0000")
    assert row["bookable"] is True and row["reason"] is None
    assert client.get(SETTLEMENTS, params=Q("2026-09-11", "2026-09-11", "2026-09-12", "2026-09-12")).json()["rows"] == []


def test_margin_day_and_disagreeing_legs_are_not_bookable(client, cathay):
    cathay.statement_day("2026-09-15", settlement="2026-09-17", net="-30000", cash_net=None, held="unsupported_category")
    cathay.trade("2026-09-16", "BUY", qty=1000, price="10", fee="14")
    cathay.statement_day("2026-09-16", settlement="2026-09-18", net="-10014", cash_net="-10015")
    rows = client.get(SETTLEMENTS, params=Q("2026-09-15", "2026-09-18", "2026-09-15", "2026-09-16")).json()["rows"]
    assert [(r["trade_date"], r["bookable"], r["reason"]) for r in rows] == [("2026-09-15", False, "margin_day"), ("2026-09-16", False, "legs_disagree")]
    assert rows[0]["net_amount"] == "-30000.0000" and rows[0]["items"] == []


def test_zero_net_days_are_omitted_with_or_without_items(client, cathay):
    cathay.trade("2026-09-17", "BUY", qty=100, price="10", fee="0", points=True)
    cathay.statement_day("2026-09-17", settlement="2026-09-21", net="-1000", cash_net="0")
    cathay.trade("2026-09-18", "BUY", qty=100, price="10", fee="0")
    cathay.trade("2026-09-18", "SELL", qty=100, price="10", fee="0", tax="0")
    assert client.get(SETTLEMENTS, params=Q("2026-09-17", "2026-09-21", "2026-09-17", "2026-09-18")).json()["rows"] == []


def test_row_cap_reports_incomplete(client, cathay, monkeypatch):
    monkeypatch.setattr(ledger_feed_service, "ROW_CAP", 1)
    cathay.trade("2026-09-22", "BUY", qty=100, price="10", fee="1")
    cathay.trade("2026-09-23", "BUY", qty=100, price="10", fee="1")
    body = client.get(SETTLEMENTS, params=Q("2026-09-22", "2026-09-30", "2026-09-22", "2026-09-23")).json()
    assert body["complete"] is False and len(body["rows"]) == 1


def test_unknown_broker_is_404(client):
    response = client.get(SETTLEMENTS, params=Q("2026-09-01", "2026-09-02", "2026-09-01", "2026-09-02", broker="ib"))
    assert response.status_code == 404 and response.json() == {"detail": "unknown_broker"}
    assert client.get(VALUATION, params={"broker": "ib", "as_of": "2026-09-12"}).status_code == 404


def test_valuation_last_price_on_or_before_as_of(client, cathay):
    cathay.trade("2026-09-01", "BUY", qty=1000, price="90", fee="0", symbol="9999")
    cathay.price("9999", "2026-09-10", "100")
    cathay.price("9999", "2026-09-15", "110")
    body = client.get(VALUATION, params={"broker": "cathay", "as_of": "2026-09-12"}).json()
    assert (body["market_value"], body["priced_through"], body["cash"], body["total"], body["complete"]) == ("100000.0000", "2026-09-10", "0.0000", "100000.0000", True)
    assert (body["broker"], body["currency"], body["as_of"], body["missing"]) == ("cathay", "TWD", "2026-09-12", [])


def test_valuation_reports_unpriced_holdings_and_ignores_later_splits(client, cathay):
    cathay.trade("2026-09-01", "BUY", qty=1000, price="90", fee="0", symbol="9999")
    cathay.price("9999", "2026-09-10", "100")
    cathay.trade("2026-09-02", "BUY", qty=10, price="5", fee="0", symbol="9998")
    cathay.split("9999", "2026-09-20", ratio="2")
    body = client.get(VALUATION, params={"broker": "cathay", "as_of": "2026-09-12"}).json()
    assert (body["market_value"], body["complete"], body["missing"]) == ("100000.0000", False, ["9998"])
