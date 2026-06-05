from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.broker_account import BrokerAccount, BrokerEnum
from app.models.cash_transaction import CashTransaction, CashTxnSource, CashTxnType
from app.models.portfolio import Transaction
from app.schemas import portfolio as schemas
from app.services import cash_account_service, portfolio_service


def _account(
    *,
    broker: BrokerEnum = BrokerEnum.CATHAY,
    nickname: str = "Cathay TWD",
    currency: str = "TWD",
    opening_balance: str = "0",
    is_active: bool = True,
) -> BrokerAccount:
    return BrokerAccount(
        broker=broker,
        nickname=nickname,
        currency=currency,
        opening_balance=Decimal(opening_balance),
        opening_date=date(2026, 1, 1),
        is_active=is_active,
    )


def _payload(
    *,
    tx_type: schemas.TransactionType,
    quantity: int = 1000,
    price: str = "50.00",
    fee: str = "22.00",
    tax: str = "0.00",
    trade_date: datetime | None = None,
    symbol: str = "2330",
    market: str = "TW",
    currency: str = "TWD",
    fx_rate_to_twd: str | None = None,
) -> schemas.TransactionCreate:
    return schemas.TransactionCreate(
        symbol=symbol,
        market=market,
        name="台積電",
        type=tx_type,
        quantity=quantity,
        price=Decimal(price),
        currency=currency,
        fx_rate_to_twd=Decimal(fx_rate_to_twd) if fx_rate_to_twd is not None else None,
        fee=Decimal(fee),
        tax=Decimal(tax),
        trade_date=trade_date or datetime(2026, 5, 1, 1, 30, tzinfo=timezone.utc),
    )


def _add_cathay_account(db_session) -> BrokerAccount:
    account = _account()
    db_session.add(account)
    db_session.commit()
    return account


def _cash_rows(db_session, transaction_id: int) -> list[CashTransaction]:
    return db_session.execute(
        select(CashTransaction)
        .where(CashTransaction.related_transaction_id == transaction_id)
        .order_by(CashTransaction.type.asc())
    ).scalars().all()


def _cash_by_type(db_session, transaction_id: int) -> dict[CashTxnType, CashTransaction]:
    return {row.type: row for row in _cash_rows(db_session, transaction_id)}


def _create_sell_with_inventory(db_session, *, fee: str = "22.00", tax: str = "50.00") -> Transaction:
    portfolio_service.create_transaction(
        db_session,
        _payload(
            tx_type=schemas.TransactionType.BUY,
            fee="0.00",
            tax="0.00",
            trade_date=datetime(2026, 4, 30, 1, 30, tzinfo=timezone.utc),
        ),
    )
    return portfolio_service.create_transaction(
        db_session,
        _payload(tx_type=schemas.TransactionType.SELL, fee=fee, tax=tax),
    )


def test_create_sell_transaction_emits_settlement_fee_and_tax_cash_legs(db_session, monkeypatch) -> None:
    monkeypatch.setenv("CASH_LEG_ENABLED", "true")
    account = _add_cathay_account(db_session)

    transaction = _create_sell_with_inventory(db_session)

    rows = _cash_by_type(db_session, transaction.id)
    assert set(rows) == {CashTxnType.SELL_SETTLE, CashTxnType.FEE, CashTxnType.TAX}
    assert rows[CashTxnType.SELL_SETTLE].amount == Decimal("50000.0000")
    assert rows[CashTxnType.FEE].amount == Decimal("-22.0000")
    assert rows[CashTxnType.TAX].amount == Decimal("-50.0000")
    for row in rows.values():
        assert row.account_id == account.id
        assert row.currency == "TWD"
        assert row.source == CashTxnSource.AUTO_DERIVE
        assert row.related_transaction_id == transaction.id


def test_create_buy_transaction_emits_settlement_and_fee_cash_legs(db_session, monkeypatch) -> None:
    monkeypatch.setenv("CASH_LEG_ENABLED", "true")
    _add_cathay_account(db_session)

    transaction = portfolio_service.create_transaction(
        db_session,
        _payload(tx_type=schemas.TransactionType.BUY),
    )

    rows = _cash_by_type(db_session, transaction.id)
    assert set(rows) == {CashTxnType.BUY_SETTLE, CashTxnType.FEE}
    assert rows[CashTxnType.BUY_SETTLE].amount == Decimal("-50000.0000")
    assert rows[CashTxnType.FEE].amount == Decimal("-22.0000")


def test_create_foreign_transaction_skips_twd_cash_sync(db_session, monkeypatch) -> None:
    monkeypatch.setenv("CASH_LEG_ENABLED", "true")
    _add_cathay_account(db_session)

    transaction = portfolio_service.create_transaction(
        db_session,
        _payload(
            tx_type=schemas.TransactionType.BUY,
            symbol="AAPL",
            market="US",
            currency="USD",
            fx_rate_to_twd="32",
        ),
    )

    assert _cash_rows(db_session, transaction.id) == []


def test_update_transaction_to_foreign_currency_deletes_stale_twd_cash_legs(
    db_session,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CASH_LEG_ENABLED", "true")
    _add_cathay_account(db_session)
    transaction = portfolio_service.create_transaction(
        db_session,
        _payload(tx_type=schemas.TransactionType.BUY, fee="22.00", tax="0.00"),
    )
    assert set(_cash_by_type(db_session, transaction.id)) == {
        CashTxnType.BUY_SETTLE,
        CashTxnType.FEE,
    }

    portfolio_service.update_transaction(
        db_session,
        transaction.id,
        _payload(
            tx_type=schemas.TransactionType.BUY,
            symbol="AAPL",
            market="US",
            currency="USD",
            fx_rate_to_twd="32",
            fee="22.00",
            tax="0.00",
        ),
    )

    assert _cash_rows(db_session, transaction.id) == []


def test_update_transaction_updates_existing_fee_cash_leg_without_inserting(db_session, monkeypatch) -> None:
    monkeypatch.setenv("CASH_LEG_ENABLED", "true")
    _add_cathay_account(db_session)
    transaction = _create_sell_with_inventory(db_session)
    original_rows = _cash_by_type(db_session, transaction.id)
    original_fee_id = original_rows[CashTxnType.FEE].id

    portfolio_service.update_transaction(
        db_session,
        transaction.id,
        _payload(tx_type=schemas.TransactionType.SELL, fee="33.00", tax="50.00"),
    )

    rows = _cash_by_type(db_session, transaction.id)
    assert set(rows) == {CashTxnType.SELL_SETTLE, CashTxnType.FEE, CashTxnType.TAX}
    assert rows[CashTxnType.FEE].id == original_fee_id
    assert rows[CashTxnType.FEE].amount == Decimal("-33.0000")
    assert rows[CashTxnType.SELL_SETTLE].id == original_rows[CashTxnType.SELL_SETTLE].id
    assert rows[CashTxnType.TAX].id == original_rows[CashTxnType.TAX].id


def test_update_transaction_removing_fee_deletes_fee_cash_leg(db_session, monkeypatch) -> None:
    monkeypatch.setenv("CASH_LEG_ENABLED", "true")
    _add_cathay_account(db_session)
    transaction = _create_sell_with_inventory(db_session)

    portfolio_service.update_transaction(
        db_session,
        transaction.id,
        _payload(tx_type=schemas.TransactionType.SELL, fee="0.00", tax="50.00"),
    )

    rows = _cash_by_type(db_session, transaction.id)
    assert set(rows) == {CashTxnType.SELL_SETTLE, CashTxnType.TAX}


def test_delete_transaction_removes_linked_cash_legs_only(db_session, monkeypatch) -> None:
    monkeypatch.setenv("CASH_LEG_ENABLED", "true")
    account = _add_cathay_account(db_session)
    transaction = _create_sell_with_inventory(db_session)
    other_cash = CashTransaction(
        account_id=account.id,
        txn_date=date(2026, 5, 1),
        type=CashTxnType.DEPOSIT,
        amount=Decimal("100.00"),
        currency="TWD",
        source=CashTxnSource.MANUAL,
        related_transaction_id=9999,
        import_fingerprint="manual-other-transaction",
    )
    db_session.add(other_cash)
    db_session.commit()
    unaffected_ids = {
        row.id
        for row in db_session.execute(
            select(CashTransaction).where(CashTransaction.related_transaction_id != transaction.id)
        ).scalars()
    }

    assert portfolio_service.delete_transaction(db_session, transaction.id) is True

    rows = db_session.execute(select(CashTransaction)).scalars().all()
    assert {row.id for row in rows} == unaffected_ids
    assert all(row.related_transaction_id != transaction.id for row in rows)


def test_legacy_transaction_gains_cash_legs_on_first_update(db_session, monkeypatch) -> None:
    monkeypatch.delenv("CASH_LEG_ENABLED", raising=False)
    _add_cathay_account(db_session)
    transaction = portfolio_service.create_transaction(
        db_session,
        _payload(tx_type=schemas.TransactionType.BUY, fee="22.00", tax="0.00"),
    )
    assert _cash_rows(db_session, transaction.id) == []

    monkeypatch.setenv("CASH_LEG_ENABLED", "true")
    portfolio_service.update_transaction(
        db_session,
        transaction.id,
        _payload(tx_type=schemas.TransactionType.BUY, fee="22.00", tax="0.00"),
    )

    rows = _cash_by_type(db_session, transaction.id)
    assert set(rows) == {CashTxnType.BUY_SETTLE, CashTxnType.FEE}


def test_create_transaction_with_flag_disabled_does_not_emit_cash_rows(db_session, monkeypatch) -> None:
    monkeypatch.delenv("CASH_LEG_ENABLED", raising=False)
    _add_cathay_account(db_session)

    transaction = portfolio_service.create_transaction(
        db_session,
        _payload(tx_type=schemas.TransactionType.BUY),
    )

    assert _cash_rows(db_session, transaction.id) == []


def test_create_transaction_missing_cathay_account_rolls_back_parent_transaction(
    db_session,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CASH_LEG_ENABLED", "true")

    with pytest.raises(cash_account_service.CashAccountNotFound):
        portfolio_service.create_transaction(
            db_session,
            _payload(tx_type=schemas.TransactionType.BUY),
        )
    db_session.rollback()

    assert db_session.execute(select(Transaction)).scalars().all() == []
    assert db_session.execute(select(CashTransaction)).scalars().all() == []
