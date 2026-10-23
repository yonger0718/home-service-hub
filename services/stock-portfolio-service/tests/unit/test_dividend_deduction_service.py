from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models.broker_account import BrokerAccount, BrokerEnum
from app.models.cash_transaction import CashTransaction, CashTxnSource, CashTxnType
from app.models.portfolio import Dividend, PositionSide, Transaction, TransactionType
from app.services import cash_account_service, portfolio_snapshot_service
from app.services.dividend_deduction_service import (
    DeductionError,
    DividendNotFound,
    update_deductions,
)
from app.services.dividend_receipt_service import ReceiptConflict

TW = timezone(timedelta(hours=8))
TODAY = date(2026, 10, 23)
RECEIPT_DATE = date(2026, 8, 20)
LEG_DATE = date(2026, 8, 18)  # deliberately earlier than receipt_date


@pytest.fixture()
def refresh_calls(monkeypatch):
    calls: list[tuple] = []

    def fake_refresh(session, start, end):
        calls.append((start, end, session.in_transaction()))

    monkeypatch.setattr(cash_account_service, "_refresh_snapshot_cash_range", fake_refresh)
    monkeypatch.setattr(portfolio_snapshot_service, "_today_tw", lambda: TODAY)
    return calls


@pytest.fixture()
def writes(db_session):
    """Records every INSERT/UPDATE/DELETE sent to the database once armed."""
    engine = db_session.get_bind()
    state = {"armed": False, "stmts": []}

    def listener(conn, cursor, statement, params, context, executemany):
        if state["armed"] and statement.lstrip().split(" ", 1)[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            state["stmts"].append(statement)

    event.listen(engine, "before_cursor_execute", listener)
    yield state
    event.remove(engine, "before_cursor_execute", listener)


def _account(db, *, nickname="Cathay TWD", currency="TWD") -> BrokerAccount:
    account = BrokerAccount(broker=BrokerEnum.CATHAY, nickname=nickname, currency=currency,
                            opening_balance=Decimal("0"), opening_date=date(2026, 1, 1), is_active=True)
    db.add(account)
    db.commit()
    return account


def _dividend(db, *, status="pending", account=None, amount="9590", fee="10", tax="0",
              qty="20000", per_share="0.48", shares=0, market="TW", currency="TWD",
              revision=1, symbol="2330") -> Dividend:
    confirmed = status == "confirmed"
    row = Dividend(
        symbol=symbol, market=market, currency=currency,
        fx_rate_to_twd=None if currency == "TWD" else Decimal("32"),
        amount=Decimal(amount), fee=Decimal(fee), tax=Decimal(tax),
        quantity_at_record_date=Decimal(qty) if qty is not None else None,
        cash_dividend_per_share=Decimal(per_share) if per_share is not None else None,
        stock_dividend_shares=shares,
        ex_dividend_date=datetime(2026, 7, 1, tzinfo=TW), received_date=None,
        receipt_status=status, revision=revision, source="auto:TWT49U",
        entitlement_key=f"key-{symbol}-{status}",
        receipt_date=RECEIPT_DATE if confirmed else None,
        receipt_account_id=account.id if confirmed else None,
        receipt_confirmed_at=datetime(2026, 8, 20, 9, tzinfo=timezone.utc) if confirmed else None,
    )
    db.add(row)
    db.commit()
    return row


def _leg(db, dividend, account, *, amount="9590", txn_date=LEG_DATE,
         source=CashTxnSource.AUTO_DERIVE, fingerprint=None) -> CashTransaction:
    leg = CashTransaction(
        account_id=account.id, txn_date=txn_date, type=CashTxnType.DIVIDEND_CASH,
        amount=Decimal(amount), currency="TWD", related_dividend_id=dividend.id, source=source,
        import_fingerprint=fingerprint or cash_account_service.compute_backfill_fingerprint(
            "dividends", dividend.id, "dividend_cash"),
    )
    db.add(leg)
    db.commit()
    return leg


def _reload(db, dividend_id):
    """Discard anything uncommitted, then read back what is committed."""
    db.rollback()
    row = db.get(Dividend, dividend_id)
    legs = list(db.scalars(select(CashTransaction).where(
        CashTransaction.related_dividend_id == dividend_id).order_by(CashTransaction.id)))
    return row, legs


def _snapshot(row, legs):
    return ((row.amount, row.fee, row.tax, row.revision),
            [(leg.id, leg.amount, leg.txn_date, leg.source, leg.account_id, leg.import_fingerprint) for leg in legs])


def _assert_nothing_written(db, writes, dividend_id, before):
    assert writes["stmts"] == []
    db.expire_all()
    row = db.get(Dividend, dividend_id)
    legs = list(db.scalars(select(CashTransaction).where(
        CashTransaction.related_dividend_id == dividend_id).order_by(CashTransaction.id)))
    assert _snapshot(row, legs) == before


def _arm(db, writes, dividend_id):
    db.expire_all()
    row = db.get(Dividend, dividend_id)
    legs = list(db.scalars(select(CashTransaction).where(
        CashTransaction.related_dividend_id == dividend_id).order_by(CashTransaction.id)))
    before = _snapshot(row, legs)
    writes["armed"] = True
    return before


# --- pending rows -----------------------------------------------------------

def test_pending_row_fee_to_zero_commits_without_leg_or_refresh(db_session, refresh_calls):
    div = _dividend(db_session)

    result = update_deductions(db_session, div.id, Decimal("0"), Decimal("0"), 1)

    assert result.amount == Decimal("9600.00")
    assert result.fee == Decimal("0")
    assert result.revision == 2
    assert refresh_calls == []
    with Session(bind=db_session.get_bind()) as fresh:
        stored = fresh.get(Dividend, div.id)
        assert (stored.amount, stored.fee, stored.tax, stored.revision) == (
            Decimal("9600.0000"), Decimal("0.00"), Decimal("0.00"), 2)
        assert stored.receipt_status == "pending"
    row, legs = _reload(db_session, div.id)
    assert row.amount == Decimal("9600") and row.revision == 2
    assert legs == []


def test_fee_is_quantized_half_up(db_session, refresh_calls):
    div = _dividend(db_session)

    update_deductions(db_session, div.id, Decimal("1.005"), Decimal("0"), 1)

    row, _ = _reload(db_session, div.id)
    assert row.fee == Decimal("1.01")
    assert row.amount == Decimal("9598.99")


def test_row_without_per_share_data_uses_stored_gross(db_session, refresh_calls):
    div = _dividend(db_session, amount="4485", fee="10", tax="5", qty=None, per_share=None)

    update_deductions(db_session, div.id, Decimal("0"), Decimal("0"), 1)

    row, _ = _reload(db_session, div.id)
    assert row.amount == Decimal("4500.00")
    assert (row.fee, row.tax) == (Decimal("0"), Decimal("0"))


def test_identity_columns_untouched(db_session, refresh_calls):
    account = _account(db_session)
    div = _dividend(db_session, status="confirmed", account=account, shares=7)
    _leg(db_session, div, account)
    identity = lambda r: (r.symbol, r.market, r.currency, r.ex_dividend_date, r.quantity_at_record_date,
                          r.cash_dividend_per_share, r.receipt_date, r.receipt_account_id, r.receipt_status,
                          r.receipt_confirmed_at, r.stock_dividend_shares, r.entitlement_key, r.source)
    before = identity(db_session.get(Dividend, div.id))

    update_deductions(db_session, div.id, Decimal("0"), Decimal("12"), 1)

    row, _ = _reload(db_session, div.id)
    assert identity(row) == before


def test_cash_leg_flag_is_not_read(db_session, refresh_calls, monkeypatch):
    monkeypatch.setenv("CASH_LEG_ENABLED", "false")
    account = _account(db_session)
    div = _dividend(db_session, status="confirmed", account=account)
    _leg(db_session, div, account)

    update_deductions(db_session, div.id, Decimal("0"), Decimal("0"), 1)

    _, legs = _reload(db_session, div.id)
    assert legs[0].amount == Decimal("9600")


# --- confirmed rows ---------------------------------------------------------

def test_confirmed_row_updates_only_the_leg_amount(db_session, refresh_calls):
    account = _account(db_session)
    div = _dividend(db_session, status="confirmed", account=account)
    leg = _leg(db_session, div, account)
    fingerprint = leg.import_fingerprint

    result = update_deductions(db_session, div.id, Decimal("0"), Decimal("0"), 1)

    assert result.amount == Decimal("9600.00")
    assert result.revision == 2
    row, legs = _reload(db_session, div.id)
    assert row.amount == Decimal("9600") and row.revision == 2
    assert len(legs) == 1
    (stored,) = legs
    assert stored.id == leg.id
    assert stored.amount == Decimal("9600.00")
    assert stored.txn_date == LEG_DATE
    assert stored.source == CashTxnSource.AUTO_DERIVE
    assert stored.account_id == account.id
    assert stored.import_fingerprint == fingerprint
    assert refresh_calls == [(LEG_DATE, TODAY, False)]


def test_repeat_call_with_same_values_is_noop_on_amount_and_leg(db_session, refresh_calls):
    account = _account(db_session)
    div = _dividend(db_session, status="confirmed", account=account)
    _leg(db_session, div, account)

    first = update_deductions(db_session, div.id, Decimal("0"), Decimal("0"), 1)
    second = update_deductions(db_session, div.id, Decimal("0"), Decimal("0"), first.revision)

    assert second.amount == Decimal("9600.00")
    row, legs = _reload(db_session, div.id)
    assert row.amount == Decimal("9600")
    assert len(legs) == 1 and legs[0].amount == Decimal("9600")
    assert legs[0].txn_date == LEG_DATE


def test_leg_with_different_fingerprint_is_updated_in_place(db_session, refresh_calls):
    account = _account(db_session)
    div = _dividend(db_session, status="confirmed", account=account)
    leg = _leg(db_session, div, account, fingerprint="legacy-fingerprint-xyz")

    update_deductions(db_session, div.id, Decimal("0"), Decimal("0"), 1)

    _, legs = _reload(db_session, div.id)
    assert len(legs) == 1
    assert legs[0].id == leg.id
    assert legs[0].amount == Decimal("9600")
    assert legs[0].import_fingerprint == "legacy-fingerprint-xyz"


def test_leg_on_another_account_stays_there(db_session, refresh_calls):
    receipt_account = _account(db_session)
    other = _account(db_session, nickname="Other TWD")
    div = _dividend(db_session, status="confirmed", account=receipt_account)
    leg = _leg(db_session, div, other, fingerprint="other-account-fingerprint")

    update_deductions(db_session, div.id, Decimal("0"), Decimal("0"), 1)

    _, legs = _reload(db_session, div.id)
    assert len(legs) == 1
    assert legs[0].id == leg.id
    assert legs[0].account_id == other.id
    assert legs[0].amount == Decimal("9600")


def test_confirmed_row_without_leg_is_rejected(db_session, refresh_calls, writes):
    account = _account(db_session)
    div = _dividend(db_session, status="confirmed", account=account)
    before = _arm(db_session, writes, div.id)

    with pytest.raises(ReceiptConflict, match="^no posted cash leg for this receipt; reload$"):
        update_deductions(db_session, div.id, Decimal("0"), Decimal("0"), 1)

    _assert_nothing_written(db_session, writes, div.id, before)
    assert refresh_calls == []


def test_confirmed_row_with_two_legs_is_rejected(db_session, refresh_calls, writes):
    account = _account(db_session)
    div = _dividend(db_session, status="confirmed", account=account)
    _leg(db_session, div, account)
    _leg(db_session, div, account, fingerprint="second-leg")
    before = _arm(db_session, writes, div.id)

    with pytest.raises(ReceiptConflict, match="^multiple cash legs for this dividend; manual review required$"):
        update_deductions(db_session, div.id, Decimal("0"), Decimal("0"), 1)

    _assert_nothing_written(db_session, writes, div.id, before)
    assert refresh_calls == []


def test_stale_revision_is_rejected(db_session, refresh_calls, writes):
    account = _account(db_session)
    div = _dividend(db_session, status="confirmed", account=account, revision=2)
    _leg(db_session, div, account)
    before = _arm(db_session, writes, div.id)

    with pytest.raises(ReceiptConflict, match="^entitlement changed; reload before editing$"):
        update_deductions(db_session, div.id, Decimal("0"), Decimal("0"), 1)

    row, legs = _reload(db_session, div.id)
    assert _snapshot(row, legs) == before
    assert refresh_calls == []


@pytest.mark.parametrize("status", ["legacy_unknown", "unresolved"])
def test_legacy_or_unresolved_rows_are_rejected(db_session, refresh_calls, writes, status):
    div = _dividend(db_session, status=status)
    before = _arm(db_session, writes, div.id)

    with pytest.raises(ReceiptConflict, match="^legacy or unresolved dividend requires explicit resolution$"):
        update_deductions(db_session, div.id, Decimal("0"), Decimal("0"), 1)

    _assert_nothing_written(db_session, writes, div.id, before)


def test_usd_row_is_rejected(db_session, refresh_calls, writes):
    div = _dividend(db_session, market="US", currency="USD", symbol="AAPL", amount="10", fee="0",
                    qty=None, per_share=None)
    before = _arm(db_session, writes, div.id)

    with pytest.raises(ReceiptConflict, match="^deductions editable for TW/TWD only$"):
        update_deductions(db_session, div.id, Decimal("0"), Decimal("0"), 1)

    _assert_nothing_written(db_session, writes, div.id, before)


def test_deductions_exceeding_gross_are_rejected(db_session, refresh_calls, writes):
    account = _account(db_session)
    div = _dividend(db_session, status="confirmed", account=account)
    _leg(db_session, div, account)
    before = _arm(db_session, writes, div.id)

    with pytest.raises(DeductionError, match="^deductions exceed the gross dividend$"):
        update_deductions(db_session, div.id, Decimal("9000"), Decimal("600.01"), 1)

    _assert_nothing_written(db_session, writes, div.id, before)
    assert refresh_calls == []


def test_deductions_equal_to_gross_without_stock_are_rejected(db_session, refresh_calls, writes):
    account = _account(db_session)
    div = _dividend(db_session, status="confirmed", account=account)
    _leg(db_session, div, account)
    before = _arm(db_session, writes, div.id)

    with pytest.raises(DeductionError, match="^deductions leave no payable amount$"):
        update_deductions(db_session, div.id, Decimal("9000"), Decimal("600"), 1)

    _assert_nothing_written(db_session, writes, div.id, before)
    assert refresh_calls == []


def test_zero_net_with_stock_shares_deletes_auto_leg_and_keeps_stock(db_session, refresh_calls):
    account = _account(db_session)
    div = _dividend(db_session, status="confirmed", account=account, shares=100)
    _leg(db_session, div, account)
    stock = Transaction(symbol="2330", market="TW", type=TransactionType.BUY, position_side=PositionSide.LONG,
                        quantity=100, price=0, trade_date=datetime(2026, 8, 20, tzinfo=TW), fee=0, tax=0,
                        is_day_trade=False, import_fingerprint="receipt-stock-fp")
    db_session.add(stock)
    db_session.commit()

    result = update_deductions(db_session, div.id, Decimal("9000"), Decimal("600"), 1)

    assert result.amount == Decimal("0.00")
    row, legs = _reload(db_session, div.id)
    assert row.amount == Decimal("0") and row.stock_dividend_shares == 100
    assert legs == []
    assert db_session.scalar(select(Transaction).where(Transaction.import_fingerprint == "receipt-stock-fp")) is not None
    assert refresh_calls == [(LEG_DATE, TODAY, False)]


def test_imported_leg_positive_net_is_rejected(db_session, refresh_calls, writes):
    account = _account(db_session)
    div = _dividend(db_session, status="confirmed", account=account)
    _leg(db_session, div, account, source=CashTxnSource.CSV_IMPORT, fingerprint="csv-fp")
    before = _arm(db_session, writes, div.id)

    with pytest.raises(ReceiptConflict, match="^cash leg was imported or entered by hand; edit the cash transaction instead$"):
        update_deductions(db_session, div.id, Decimal("0"), Decimal("0"), 1)

    _assert_nothing_written(db_session, writes, div.id, before)
    assert refresh_calls == []


def test_imported_leg_zero_net_is_rejected_and_leg_kept(db_session, refresh_calls, writes):
    account = _account(db_session)
    div = _dividend(db_session, status="confirmed", account=account, shares=100)
    _leg(db_session, div, account, source=CashTxnSource.CSV_IMPORT, fingerprint="csv-fp")
    before = _arm(db_session, writes, div.id)

    with pytest.raises(ReceiptConflict, match="^cash leg was imported or entered by hand; edit the cash transaction instead$"):
        update_deductions(db_session, div.id, Decimal("9000"), Decimal("600"), 1)

    _assert_nothing_written(db_session, writes, div.id, before)
    _, legs = _reload(db_session, div.id)
    assert len(legs) == 1 and legs[0].amount == Decimal("9590")
    assert refresh_calls == []


def test_snapshot_refresh_failure_keeps_committed_money_write(db_session, monkeypatch):
    """The real helper rolls back and swallows; the edit was committed before it ran."""
    monkeypatch.setattr(portfolio_snapshot_service, "_today_tw", lambda: TODAY)

    def boom(*args, **kwargs):
        raise RuntimeError("snapshot refresh failed")

    monkeypatch.setattr(portfolio_snapshot_service, "refresh_snapshot_cash_range", boom)
    account = _account(db_session)
    div = _dividend(db_session, status="confirmed", account=account)
    _leg(db_session, div, account)

    result = update_deductions(db_session, div.id, Decimal("0"), Decimal("0"), 1)

    assert result.amount == Decimal("9600.00")
    row, legs = _reload(db_session, div.id)
    assert row.amount == Decimal("9600") and row.revision == 2
    assert legs[0].amount == Decimal("9600")


def test_raising_refresh_helper_still_leaves_committed_money_write(db_session, monkeypatch):
    monkeypatch.setattr(portfolio_snapshot_service, "_today_tw", lambda: TODAY)

    def boom(*args, **kwargs):
        raise RuntimeError("snapshot refresh failed")

    monkeypatch.setattr(cash_account_service, "_refresh_snapshot_cash_range", boom)
    account = _account(db_session)
    div = _dividend(db_session, status="confirmed", account=account)
    _leg(db_session, div, account)

    with pytest.raises(RuntimeError):
        update_deductions(db_session, div.id, Decimal("0"), Decimal("0"), 1)

    row, legs = _reload(db_session, div.id)
    assert row.amount == Decimal("9600") and row.revision == 2
    assert legs[0].amount == Decimal("9600")


def test_missing_dividend(db_session, refresh_calls):
    with pytest.raises(DividendNotFound):
        update_deductions(db_session, 999, Decimal("0"), Decimal("0"), 1)
