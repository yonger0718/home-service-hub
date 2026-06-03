from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import portfolio as models
from app.models.broker_account import BrokerAccount, BrokerEnum
from app.models.cash_transaction import CashTransaction, CashTxnSource, CashTxnType
from app.services import broker_cathay_service, cash_account_service, import_service

CATHAY_HEADER = (
    "股名,日期,成交股數,淨收付金額,買賣別,成交價,成本,手續費,交易稅,"
    "融資金額/券擔保品,資自備款/券保證金,利息,稅款,券手續費/標借費,委託書號\n"
)


def _cathay_csv(*rows: str) -> bytes:
    return (
        "根據您篩選的結果，總計有2筆資料\n" + CATHAY_HEADER + "\n".join(rows) + "\n"
    ).encode("utf-8")


def _row(
    *,
    side: str = "現買",
    quantity: str = "1,000",
    price: str = "50",
    fee: str = "22",
    tax: str = "0",
    order_id: str = "aT532",
) -> str:
    net = "50,000" if side == "現賣" else "-50,000"
    return (
        f'晶宏,2026/05/08,"{quantity}","{net}",{side},"{price}","50,000",'
        f"{fee},{tax},0,0,0,0,0,{order_id}"
    )


@pytest.fixture(autouse=True)
def _stable_name_map(monkeypatch) -> None:
    monkeypatch.setitem(broker_cathay_service.NAME_TO_SYMBOL, "晶宏", ["3141"])


def _add_cathay_account(db_session) -> BrokerAccount:
    account = BrokerAccount(
        broker=BrokerEnum.CATHAY,
        nickname="Cathay TWD",
        currency="TWD",
        opening_balance=Decimal("0"),
        opening_date=date(2026, 1, 1),
        is_active=True,
    )
    db_session.add(account)
    db_session.commit()
    return account


def _legacy_fp() -> str:
    return import_service._transaction_fingerprint(
        "3141",
        "BUY",
        1000,
        Decimal("50"),
        datetime(2026, 5, 8, tzinfo=timezone.utc),
        Decimal("22"),
        Decimal("0"),
    )


def _new_fp(order_id: str = "aT532") -> str:
    return import_service._transaction_fingerprint(
        "3141",
        "BUY",
        1000,
        Decimal("50"),
        datetime(2026, 5, 8, tzinfo=timezone.utc),
        Decimal("22"),
        Decimal("0"),
        order_id=order_id,
    )


def _seed_legacy_transaction(db_session) -> models.Transaction:
    tx = models.Transaction(
        symbol="3141",
        name="晶宏",
        type=models.TransactionType.BUY,
        quantity=1000,
        price=Decimal("50"),
        trade_date=datetime(2026, 5, 8, tzinfo=timezone.utc),
        fee=Decimal("22"),
        tax=Decimal("0"),
        import_fingerprint=_legacy_fp(),
    )
    db_session.add(tx)
    db_session.commit()
    db_session.refresh(tx)
    return tx


def _cash_rows(db_session, transaction_id: int) -> list[CashTransaction]:
    return db_session.execute(
        select(CashTransaction)
        .where(CashTransaction.related_transaction_id == transaction_id)
        .order_by(CashTransaction.type.asc())
    ).scalars().all()


def _cash_by_type(db_session, transaction_id: int) -> dict[CashTxnType, CashTransaction]:
    return {row.type: row for row in _cash_rows(db_session, transaction_id)}


def test_cathay_insert_path_emits_csv_cash_legs(db_session, monkeypatch) -> None:
    monkeypatch.setenv("CASH_LEG_ENABLED", "true")
    account = _add_cathay_account(db_session)

    result = broker_cathay_service.parse_cathay_transactions_csv(
        _cathay_csv(_row(side="現賣", tax="150")),
        dry_run=False,
        db=db_session,
    )

    assert result.created == 1
    tx = db_session.query(models.Transaction).one()
    rows = _cash_by_type(db_session, tx.id)
    assert set(rows) == {CashTxnType.SELL_SETTLE, CashTxnType.FEE, CashTxnType.TAX}
    assert rows[CashTxnType.SELL_SETTLE].amount == Decimal("50000.0000")
    assert rows[CashTxnType.FEE].amount == Decimal("-22.0000")
    assert rows[CashTxnType.TAX].amount == Decimal("-150.0000")
    for row in rows.values():
        assert row.account_id == account.id
        assert row.currency == "TWD"
        assert row.source == CashTxnSource.CSV_IMPORT
        assert row.related_transaction_id == tx.id


def test_cathay_legacy_rehash_creates_missing_csv_cash_legs(db_session, monkeypatch) -> None:
    monkeypatch.setenv("CASH_LEG_ENABLED", "true")
    _add_cathay_account(db_session)
    tx = _seed_legacy_transaction(db_session)

    result = broker_cathay_service.parse_cathay_transactions_csv(
        _cathay_csv(_row()),
        dry_run=False,
        db=db_session,
    )

    assert result.rehashed == 1
    db_session.refresh(tx)
    rows = _cash_by_type(db_session, tx.id)
    assert set(rows) == {CashTxnType.BUY_SETTLE, CashTxnType.FEE}
    assert rows[CashTxnType.BUY_SETTLE].amount == Decimal("-50000.0000")
    assert rows[CashTxnType.FEE].amount == Decimal("-22.0000")
    assert rows[CashTxnType.BUY_SETTLE].import_fingerprint == (
        cash_account_service.compute_csv_fingerprint("cathay", _new_fp(), "settle")
    )
    assert rows[CashTxnType.FEE].import_fingerprint == (
        cash_account_service.compute_csv_fingerprint("cathay", _new_fp(), "fee")
    )


def test_cathay_reimport_does_not_duplicate_cash_legs(db_session, monkeypatch) -> None:
    monkeypatch.setenv("CASH_LEG_ENABLED", "true")
    _add_cathay_account(db_session)
    raw = _cathay_csv(_row())

    first = broker_cathay_service.parse_cathay_transactions_csv(raw, dry_run=False, db=db_session)
    second = broker_cathay_service.parse_cathay_transactions_csv(raw, dry_run=False, db=db_session)

    assert first.created == 1
    assert second.skipped_duplicates == 1
    assert db_session.query(CashTransaction).count() == 2


def test_cathay_missing_account_aborts_parent_transaction(db_session, monkeypatch) -> None:
    monkeypatch.setenv("CASH_LEG_ENABLED", "true")

    with pytest.raises(cash_account_service.CashAccountNotFound):
        broker_cathay_service.parse_cathay_transactions_csv(
            _cathay_csv(_row()),
            dry_run=False,
            db=db_session,
        )
    db_session.rollback()

    assert db_session.query(models.Transaction).count() == 0
    assert db_session.query(CashTransaction).count() == 0


def test_cathay_import_with_flag_disabled_emits_no_cash_legs(db_session, monkeypatch) -> None:
    monkeypatch.delenv("CASH_LEG_ENABLED", raising=False)

    result = broker_cathay_service.parse_cathay_transactions_csv(
        _cathay_csv(_row()),
        dry_run=False,
        db=db_session,
    )

    assert result.created == 1
    assert db_session.query(models.Transaction).count() == 1
    assert db_session.query(CashTransaction).count() == 0
