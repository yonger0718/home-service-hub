from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.broker_account import BrokerAccount, BrokerEnum
from app.models.cash_transaction import CashTransaction, CashTxnSource, CashTxnType
from app.models.portfolio import Dividend, Transaction, TransactionType
from app.services import cash_backfill_service, cash_account_service


def _account(*, opening_balance: str = "0") -> BrokerAccount:
    return BrokerAccount(
        broker=BrokerEnum.CATHAY,
        nickname="Cathay TWD",
        currency="TWD",
        opening_balance=Decimal(opening_balance),
        opening_date=date(2025, 1, 1),
        is_active=True,
    )


def _transaction(
    index: int,
    *,
    type_: TransactionType,
    trade_date: datetime,
    import_fingerprint: str | None = "csv-imported",
) -> Transaction:
    return Transaction(
        symbol=f"23{index:02d}",
        name="Test",
        type=type_,
        quantity=100,
        price=Decimal("10.00"),
        trade_date=trade_date,
        fee=Decimal("1.00"),
        tax=Decimal("2.00") if type_ == TransactionType.SELL else Decimal("0.00"),
        import_fingerprint=f"{import_fingerprint}-{index}" if import_fingerprint else None,
    )


def _dividend(index: int, *, ex_dividend_date: datetime) -> Dividend:
    return Dividend(
        symbol=f"23{index:02d}",
        amount=Decimal("25.00"),
        ex_dividend_date=ex_dividend_date,
        import_fingerprint=f"dividend-csv-{index}",
    )


def _seed_large_fixture(db_session) -> BrokerAccount:
    account = _account()
    start = datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc)
    rows: list[Transaction | Dividend | BrokerAccount] = [account]
    for index in range(50):
        rows.append(
            _transaction(
                index,
                type_=TransactionType.BUY,
                trade_date=start + timedelta(days=index),
            )
        )
        rows.append(
            _transaction(
                index + 50,
                type_=TransactionType.SELL,
                trade_date=start + timedelta(days=index, hours=1),
            )
        )
    for index in range(10):
        rows.append(_dividend(index, ex_dividend_date=start + timedelta(days=100 + index)))
    db_session.add_all(rows)
    db_session.commit()
    return account


def _cash_rows(db_session) -> list[CashTransaction]:
    return db_session.scalars(select(CashTransaction).order_by(CashTransaction.id.asc())).all()


def test_first_run_replays_transactions_and_dividends_into_cash_rows(db_session) -> None:
    account = _seed_large_fixture(db_session)

    result = cash_backfill_service.replay_all(db_session)

    assert result.transactions_processed == 100
    assert result.dividends_processed == 10
    assert result.cash_rows_inserted == 260
    assert result.cash_rows_skipped == 0
    assert result.per_account_summary == {
        account.id: {
            "settle": 100,
            "fee": 100,
            "tax": 50,
            "dividend_cash": 10,
        }
    }

    rows = _cash_rows(db_session)
    assert len(rows) == 260
    assert {row.account_id for row in rows} == {account.id}


def test_replay_is_idempotent_by_backfill_fingerprint(db_session) -> None:
    _seed_large_fixture(db_session)
    first = cash_backfill_service.replay_all(db_session)

    second = cash_backfill_service.replay_all(db_session)

    assert first.cash_rows_inserted == 260
    assert second.cash_rows_inserted == 0
    assert second.cash_rows_skipped == 260
    assert len(_cash_rows(db_session)) == 260


def test_dry_run_counts_expected_rows_without_writing(db_session) -> None:
    account = _seed_large_fixture(db_session)

    result = cash_backfill_service.replay_all(db_session, dry_run=True)

    assert result.dry_run is True
    assert result.transactions_processed == 100
    assert result.dividends_processed == 10
    assert result.cash_rows_inserted == 260
    assert result.cash_rows_skipped == 0
    assert result.per_account_summary[account.id]["settle"] == 100
    assert _cash_rows(db_session) == []


def test_missing_cathay_account_raises_and_main_returns_2(db_session, monkeypatch, capsys) -> None:
    db_session.add(
        _transaction(
            1,
            type_=TransactionType.BUY,
            trade_date=datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc),
        )
    )
    db_session.commit()

    with pytest.raises(cash_account_service.CashAccountNotFound):
        cash_backfill_service.replay_all(db_session)

    import app.database

    monkeypatch.setattr(app.database, "SessionLocal", lambda: db_session)

    exit_code = cash_backfill_service._main(["--all"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Cathay TWD account not found" in captured.err


def test_source_assignment_uses_originating_import_fingerprint(db_session) -> None:
    account = _account()
    imported = _transaction(
        1,
        type_=TransactionType.BUY,
        trade_date=datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc),
        import_fingerprint="csv",
    )
    manual = _transaction(
        2,
        type_=TransactionType.BUY,
        trade_date=datetime(2025, 1, 2, 9, 0, tzinfo=timezone.utc),
        import_fingerprint=None,
    )
    db_session.add_all([account, imported, manual])
    db_session.commit()

    cash_backfill_service.replay_all(db_session)

    rows = db_session.scalars(
        select(CashTransaction).where(CashTransaction.type == CashTxnType.BUY_SETTLE)
    ).all()
    by_transaction_id = {row.related_transaction_id: row for row in rows}
    assert by_transaction_id[imported.id].source == CashTxnSource.CSV_IMPORT
    assert by_transaction_id[manual.id].source == CashTxnSource.AUTO_DERIVE
