"""Unit tests for the auto-record service."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.portfolio import Dividend, Transaction, TransactionType
from app.services import dividend_auto_record_service as svc
from app.services.dividend_history_service import HistoricalDividendEvent

TW = timezone(timedelta(hours=8))


def _event(symbol="2330", ex=date(2026, 6, 15), cash=None, stock_per_thousand=None):
    return HistoricalDividendEvent(
        symbol=symbol,
        ex_date=ex,
        cash_dividend_per_share=cash,
        stock_dividend_per_thousand=stock_per_thousand,
        previous_close=None,
        reference_price=None,
        source="TWT49U",
    )


def _add_trade(db, *, symbol, qty, trade_date, type_=TransactionType.BUY, price=Decimal("100.00")):
    tx = Transaction(
        symbol=symbol,
        type=type_,
        quantity=qty,
        price=price,
        trade_date=datetime.combine(trade_date, datetime.min.time(), tzinfo=TW),
        fee=Decimal("0"),
        tax=Decimal("0"),
        is_day_trade=False,
    )
    db.add(tx)
    db.flush()
    return tx


def test_qty_held_on_excludes_trades_on_ex_date(db_session):
    sym, ex = "2330", date(2026, 6, 15)
    _add_trade(db_session, symbol=sym, qty=1000, trade_date=date(2026, 5, 1))
    _add_trade(db_session, symbol=sym, qty=500, trade_date=ex, type_=TransactionType.BUY)
    qty = svc._qty_held_on(db_session, sym, ex)
    assert qty == Decimal("1000")


def test_qty_held_on_signed_with_sell(db_session):
    sym, ex = "2330", date(2026, 6, 15)
    _add_trade(db_session, symbol=sym, qty=1000, trade_date=date(2026, 5, 1))
    _add_trade(db_session, symbol=sym, qty=300, trade_date=date(2026, 5, 10), type_=TransactionType.SELL)
    assert svc._qty_held_on(db_session, sym, ex) == Decimal("700")


def test_compute_nhi_surtax_below_threshold_returns_zero():
    assert svc.compute_nhi_surtax(Decimal("20000")) == Decimal("0")
    assert svc.compute_nhi_surtax(Decimal("19999.99")) == Decimal("0")


def test_compute_nhi_surtax_above_threshold_is_2_11_percent():
    assert svc.compute_nhi_surtax(Decimal("21000")) == Decimal("443.10")
    assert svc.compute_nhi_surtax(Decimal("25000")) == Decimal("527.50")


def test_cash_branch_inserts_with_fee_and_zero_tax_below_threshold(db_session):
    _add_trade(db_session, symbol="2330", qty=1000, trade_date=date(2026, 5, 1))
    event = _event(cash=Decimal("2.00"))
    result = svc.auto_record_for_event(db_session, event)
    assert result.cash_inserted is True
    assert result.stock_inserted is False
    row = db_session.query(Dividend).one()
    assert row.amount == Decimal("1990.00")
    assert row.fee == Decimal("10")
    assert row.tax == Decimal("0")
    assert row.quantity_at_record_date == Decimal("1000")
    assert row.cash_dividend_per_share == Decimal("2.00")
    assert row.source == "auto:TWT49U"


def test_cash_branch_applies_nhi_above_threshold(db_session):
    _add_trade(db_session, symbol="2330", qty=10000, trade_date=date(2026, 5, 1))
    event = _event(cash=Decimal("2.50"))  # gross = 25,000
    svc.auto_record_for_event(db_session, event)
    row = db_session.query(Dividend).one()
    assert row.fee == Decimal("10")
    assert row.tax == Decimal("527.50")
    assert row.amount == Decimal("24462.50")


def test_amount_is_clamped_to_minimum_positive(db_session):
    _add_trade(db_session, symbol="2330", qty=1, trade_date=date(2026, 5, 1))
    event = _event(cash=Decimal("0.01"))  # gross = 0.01, fee = 10 → would be negative
    svc.auto_record_for_event(db_session, event)
    row = db_session.query(Dividend).one()
    assert row.amount == Decimal("0.01")


def test_stock_branch_inserts_zero_cost_transaction(db_session):
    _add_trade(db_session, symbol="2330", qty=1500, trade_date=date(2026, 5, 1))
    event = _event(cash=None, stock_per_thousand=Decimal("100"))
    result = svc.auto_record_for_event(db_session, event)
    assert result.stock_inserted is True
    assert result.cash_inserted is False
    txns = db_session.query(Transaction).filter(Transaction.price == Decimal("0")).all()
    assert len(txns) == 1
    tx = txns[0]
    assert tx.symbol == "2330"
    assert tx.type == TransactionType.BUY
    assert tx.quantity == 150
    assert tx.import_fingerprint and tx.import_fingerprint.startswith("") and "auto-stk-div" not in tx.import_fingerprint  # fingerprint is sha256 hex


def test_stock_branch_skipped_when_floored_to_zero(db_session):
    _add_trade(db_session, symbol="2330", qty=5, trade_date=date(2026, 5, 1))
    event = _event(cash=None, stock_per_thousand=Decimal("100"))  # 5 * 100 / 1000 = 0.5 → floor 0
    result = svc.auto_record_for_event(db_session, event)
    assert result.stock_inserted is False
    assert db_session.query(Transaction).filter(Transaction.price == Decimal("0")).count() == 0


def test_no_holding_returns_skipped(db_session):
    event = _event(cash=Decimal("2.00"))
    result = svc.auto_record_for_event(db_session, event)
    assert result == svc.AutoRecordResult(False, False, "no_holding")
    assert db_session.query(Dividend).count() == 0


def test_idempotent_on_repeat(db_session):
    _add_trade(db_session, symbol="2330", qty=1000, trade_date=date(2026, 5, 1))
    event = _event(cash=Decimal("2.00"), stock_per_thousand=Decimal("100"))
    r1 = svc.auto_record_for_event(db_session, event)
    db_session.commit()
    r2 = svc.auto_record_for_event(db_session, event)
    assert r1 == svc.AutoRecordResult(True, True, None)
    assert r2 == svc.AutoRecordResult(False, False, None)
    assert db_session.query(Dividend).count() == 1
    assert db_session.query(Transaction).filter(Transaction.price == Decimal("0")).count() == 1
