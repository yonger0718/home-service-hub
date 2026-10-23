"""Decision 8: once a dividend row exists, its fee and tax belong to the owner.

Re-persisting an entitlement (daily auto-record, reconciliation) must not undo
an edit made through ``update_deductions``.
"""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from app.models.broker_account import BrokerAccount, BrokerEnum
from app.models.portfolio import Dividend, PositionSide, Transaction, TransactionType
from app.services import (
    cash_account_service,
    dividend_auto_record_service,
    dividend_history_service as history,
    post_import_orchestrator as orch,
)
from app.services.dividend_deduction_service import update_deductions
from app.services.dividend_history_service import HistoricalDividendEvent
from app.services.dividend_receipt_service import confirm_receipt, persist_entitlement

TW = timezone(timedelta(hours=8))
EX = date(2026, 6, 15)
QTY = Decimal("20000")
CORRECTION_REASON = "source correction after confirmed receipt; ledger unchanged"


@pytest.fixture(autouse=True)
def _no_snapshot_refresh(monkeypatch):
    monkeypatch.setattr(cash_account_service, "_refresh_snapshot_cash_range", lambda *a, **k: None)


def _event(cash="0.48", stock=None, ex=EX, symbol="2330", source="TWT49U"):
    return HistoricalDividendEvent(symbol, ex, Decimal(cash) if cash is not None else None,
                                   Decimal(stock) if stock is not None else None, None, None, source)


def _holding(db, *, symbol="2330", qty=20000, on=date(2026, 5, 1)):
    db.add(Transaction(symbol=symbol, market="TW", type=TransactionType.BUY, position_side=PositionSide.LONG,
                       quantity=qty, price=Decimal("100"), trade_date=datetime.combine(on, datetime.min.time(), TW),
                       fee=0, tax=0, is_day_trade=False))
    db.commit()


def _persist(db, event, payment_date=None):
    row, reason = persist_entitlement(db, event, QTY, payment_date)
    db.commit()
    return row, reason


def _fresh(db, row_id):
    db.expire_all()
    return db.get(Dividend, row_id)


def _pending_edited_to_fee_zero(db):
    row, _ = _persist(db, _event())
    assert (row.amount, row.fee, row.revision) == (Decimal("9590"), Decimal("10"), 1)
    edited = update_deductions(db, row.id, Decimal("0"), Decimal("0"), row.revision)
    assert (edited.amount, edited.revision) == (Decimal("9600"), 2)
    return edited.id


# --- pending rows -----------------------------------------------------------

def test_repersist_same_event_keeps_edited_pending_row(db_session):
    row_id = _pending_edited_to_fee_zero(db_session)

    row, reason = _persist(db_session, _event())

    assert reason is None
    row = _fresh(db_session, row_id)
    assert (row.amount, row.fee, row.tax, row.revision) == (Decimal("9600"), Decimal("0"), Decimal("0"), 2)
    assert row.review_reason is None and row.source_correction is None
    assert row.receipt_status == "pending"


def test_repersist_with_new_per_share_reamounts_with_owner_deductions(db_session):
    row_id = _pending_edited_to_fee_zero(db_session)

    _persist(db_session, _event(cash="0.50"))

    row = _fresh(db_session, row_id)
    assert row.amount == Decimal("10000.00")
    assert (row.fee, row.tax) == (Decimal("0"), Decimal("0"))
    assert row.cash_dividend_per_share == Decimal("0.50")
    assert row.revision == 3
    assert row.receipt_status == "pending" and row.review_reason is None


def test_auto_record_path_keeps_edited_pending_row(db_session):
    _holding(db_session)
    first = dividend_auto_record_service.auto_record_for_event(db_session, _event())
    db_session.commit()
    assert first.pending_recorded
    row = db_session.query(Dividend).one()
    update_deductions(db_session, row.id, Decimal("0"), Decimal("0"), row.revision)

    again = dividend_auto_record_service.auto_record_for_event(db_session, _event())
    db_session.commit()

    assert again.skipped_reason is None and not again.pending_recorded
    row = _fresh(db_session, row.id)
    assert (row.amount, row.fee, row.revision) == (Decimal("9600"), Decimal("0"), 2)


def test_reconciliation_path_keeps_edited_pending_row(db_session):
    fixture = (Path(__file__).parents[1] / "fixtures/twt49u-2026-09.json").read_bytes()
    _holding(db_session, symbol="9802", qty=1000, on=date(2026, 9, 1))

    @contextmanager
    def factory():
        yield db_session

    def reconcile():
        with patch.object(history, "_http_get", return_value=fixture), \
                patch.object(history, "_http_post_form", return_value={"tables": [{"data": []}]}), \
                patch.object(history, "_fetch_detail_raw", return_value=(None, None)):
            orch._step_dividends(factory, {"9802"}, date(2026, 9, 1), date(2026, 9, 12))
        db_session.commit()

    reconcile()
    row = db_session.query(Dividend).one()
    assert (row.amount, row.fee) == (Decimal("3090"), Decimal("10"))
    update_deductions(db_session, row.id, Decimal("0"), Decimal("0"), row.revision)

    reconcile()

    row = _fresh(db_session, row.id)
    assert (row.amount, row.fee, row.revision) == (Decimal("3100"), Decimal("0"), 2)
    assert row.receipt_status == "pending" and row.review_reason is None


# --- confirmed rows ---------------------------------------------------------

def _confirmed_edited_to_fee_zero(db):
    _holding(db)
    account = BrokerAccount(broker=BrokerEnum.CATHAY, nickname="Cathay TWD", currency="TWD",
                            opening_balance=Decimal("0"), opening_date=date(2026, 1, 1), is_active=True)
    db.add(account)
    db.commit()
    row, _ = _persist(db, _event())
    row = confirm_receipt(db, row.id, date(2026, 7, 10), account.id, row.revision)
    edited = update_deductions(db, row.id, Decimal("0"), Decimal("0"), row.revision)
    assert edited.amount == Decimal("9600")
    return edited.id, edited.revision


def test_repersist_same_event_does_not_flag_edited_confirmed_row(db_session):
    row_id, revision = _confirmed_edited_to_fee_zero(db_session)

    _, reason = _persist(db_session, _event())

    assert reason is None
    row = _fresh(db_session, row_id)
    assert row.source_correction is None and row.review_reason is None
    assert (row.amount, row.fee, row.revision) == (Decimal("9600"), Decimal("0"), revision)
    assert row.receipt_status == "confirmed"


def test_repersist_changed_per_share_still_flags_confirmed_row(db_session):
    row_id, revision = _confirmed_edited_to_fee_zero(db_session)

    _, reason = _persist(db_session, _event(cash="0.50"))

    assert reason == CORRECTION_REASON
    row = _fresh(db_session, row_id)
    assert row.review_reason == CORRECTION_REASON
    assert row.source_correction["cash_dividend_per_share"] == "0.50"
    assert row.source_correction["fee"] == "0.00"
    assert row.source_correction["amount"] == "10000.00"
    # ledger values stay frozen
    assert (row.amount, row.fee, row.cash_dividend_per_share) == (Decimal("9600"), Decimal("0"), Decimal("0.48"))


# --- new rows and the amount floor --------------------------------------------

def test_brand_new_event_uses_default_fee_and_computed_tax(db_session):
    # 20,000 x 1.5 = 30,000 > NHI threshold -> tax 633.00
    row, reason = _persist(db_session, _event(cash="1.5"))

    assert reason is None
    assert row.fee == dividend_auto_record_service.DEFAULT_HANDLING_FEE
    assert row.tax == Decimal("633.00")
    assert row.amount == Decimal("29357.00")
    assert row.revision == 1


def test_stock_share_row_with_deductions_equal_to_gross_has_no_floor(db_session):
    # 20,000 x 0.48 = 9,600 cash; 50 per thousand -> 1,000 shares
    row, _ = _persist(db_session, _event(stock="50"))
    assert row.stock_dividend_shares == 1000
    update_deductions(db_session, row.id, Decimal("9600"), Decimal("0"), row.revision)

    # a payment date is a source field change, so the row is rewritten from the owner's deductions
    _persist(db_session, _event(stock="50"), payment_date=date(2026, 7, 10))

    row = _fresh(db_session, row.id)
    assert row.payment_date == date(2026, 7, 10)
    assert row.amount == Decimal("0")
    assert (row.fee, row.tax) == (Decimal("9600"), Decimal("0"))
    assert row.revision == 3


def test_cash_only_row_keeps_minimum_amount_floor(db_session):
    row, _ = _persist(db_session, _event())
    update_deductions(db_session, row.id, Decimal("9599.99"), Decimal("0"), row.revision)

    # gross drops to 9,400, below the owner's deductions -> floor as today
    _persist(db_session, _event(cash="0.47"))

    row = _fresh(db_session, row.id)
    assert row.amount == dividend_auto_record_service.MIN_DIVIDEND_AMOUNT
    assert row.fee == Decimal("9599.99")
    assert row.revision == 3


# --- only upstream facts count as a change ----------------------------------

def test_stored_amount_alone_is_not_a_source_change_for_pending_row(db_session):
    """_changed compares upstream facts only, not amount/fee/tax."""
    row, _ = _persist(db_session, _event())
    row.amount = Decimal("9000")  # inconsistent with 9,600 - 10 on purpose
    db_session.commit()

    _, reason = _persist(db_session, _event())

    assert reason is None
    row = _fresh(db_session, row.id)
    assert (row.amount, row.fee, row.revision) == (Decimal("9000"), Decimal("10"), 1)


def test_stored_amount_alone_is_not_a_source_change_for_confirmed_row(db_session):
    row_id, revision = _confirmed_edited_to_fee_zero(db_session)
    row = db_session.get(Dividend, row_id)
    row.amount = Decimal("9500")
    db_session.commit()

    _, reason = _persist(db_session, _event())

    assert reason is None
    row = _fresh(db_session, row_id)
    assert row.source_correction is None and row.review_reason is None
    assert (row.amount, row.revision) == (Decimal("9500"), revision)


# --- Decision 8 amendment: untouched default deductions follow the gross -----

NHI = dividend_auto_record_service.compute_nhi_surtax


def test_untouched_defaults_recomputed_when_gross_crosses_nhi_threshold(db_session):
    # 20,000 x 0.98 = 19,600 (no NHI) -> 20,000 x 1.02 = 20,400 (NHI 430.44)
    row, _ = _persist(db_session, _event(cash="0.98"))
    assert (row.fee, row.tax, row.amount, row.revision) == (Decimal("10"), Decimal("0"), Decimal("19590"), 1)

    _persist(db_session, _event(cash="1.02"))

    row = _fresh(db_session, row.id)
    assert NHI(Decimal("20400")) == Decimal("430.44")
    assert (row.fee, row.tax) == (Decimal("10"), Decimal("430.44"))
    assert row.amount == Decimal("19959.56")
    assert row.revision == 2
    assert row.receipt_status == "pending" and row.review_reason is None


def test_owner_edited_fee_kept_when_gross_crosses_nhi_threshold(db_session):
    row, _ = _persist(db_session, _event(cash="0.98"))
    stored_tax = row.tax
    update_deductions(db_session, row.id, Decimal("0"), stored_tax, row.revision)

    _persist(db_session, _event(cash="1.02"))

    row = _fresh(db_session, row.id)
    assert (row.fee, row.tax) == (Decimal("0"), stored_tax)
    assert row.amount == Decimal("20400") - Decimal("0") - stored_tax
    assert row.cash_dividend_per_share == Decimal("1.02")


def test_owner_edit_equal_to_defaults_is_treated_as_untouched(db_session):
    row, _ = _persist(db_session, _event(cash="0.98"))
    update_deductions(db_session, row.id, Decimal("10"), Decimal("0"), row.revision)
    revision = _fresh(db_session, row.id).revision

    _persist(db_session, _event(cash="1.02"))

    row = _fresh(db_session, row.id)
    assert (row.fee, row.tax, row.amount) == (Decimal("10"), Decimal("430.44"), Decimal("19959.56"))
    assert row.revision == revision + 1


def test_stock_only_row_that_gains_cash_gets_default_deductions(db_session):
    # stock only: 50 per thousand -> 1,000 shares, no cash
    row, _ = _persist(db_session, _event(cash=None, stock="50"))
    assert (row.fee, row.tax, row.amount, row.stock_dividend_shares) == (Decimal("0"), Decimal("0"), Decimal("0"), 1000)

    # 20,000 x 1.5 = 30,000 -> fee 10, NHI 633.00
    _persist(db_session, _event(cash="1.5", stock="50"))

    row = _fresh(db_session, row.id)
    assert row.fee == Decimal("10")
    assert row.tax == NHI(Decimal("30000")) == Decimal("633.00")
    assert row.amount == Decimal("29357.00")
    assert row.revision == 2


def test_row_whose_cash_disappears_drops_default_deductions(db_session):
    row, _ = _persist(db_session, _event(cash="0.48", stock="50"))
    assert (row.fee, row.tax, row.amount) == (Decimal("10"), Decimal("0"), Decimal("9590"))

    _persist(db_session, _event(cash=None, stock="50"))

    row = _fresh(db_session, row.id)
    assert (row.fee, row.tax, row.amount) == (Decimal("0"), Decimal("0"), Decimal("0"))
    assert row.stock_dividend_shares == 1000
    assert row.revision == 2


# --- an edit before confirmation is what gets posted ---------------------------

def test_pending_edit_is_the_amount_posted_on_confirmation(db_session):
    from sqlalchemy import select
    from app.models.cash_transaction import CashTransaction, CashTxnSource

    _holding(db_session)
    account = BrokerAccount(broker=BrokerEnum.CATHAY, nickname="Cathay TWD", currency="TWD",
                            opening_balance=Decimal("0"), opening_date=date(2026, 1, 1), is_active=True)
    db_session.add(account)
    db_session.commit()
    row, _ = _persist(db_session, _event())
    edited = update_deductions(db_session, row.id, Decimal("0"), Decimal("0"), row.revision)
    assert edited.amount == Decimal("9600")

    confirmed = confirm_receipt(db_session, row.id, date(2026, 7, 10), account.id, edited.revision)

    legs = list(db_session.scalars(select(CashTransaction).where(CashTransaction.related_dividend_id == row.id)))
    assert len(legs) == 1
    assert legs[0].amount == Decimal("9600") == confirmed.amount
    assert legs[0].source == CashTxnSource.AUTO_DERIVE
    assert (confirmed.fee, confirmed.tax) == (Decimal("0"), Decimal("0"))
