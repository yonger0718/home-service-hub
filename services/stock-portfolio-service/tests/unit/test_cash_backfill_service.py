from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.broker_account import BrokerAccount, BrokerEnum
from app.models.cash_transaction import CashTransaction, CashTxnSource, CashTxnType
from app.models.portfolio import Broker, Dividend, Transaction, TransactionType
from app.services import cash_backfill_service, cash_account_service


def _account(
    *,
    broker: BrokerEnum = BrokerEnum.CATHAY,
    nickname: str = "Cathay TWD",
    currency: str = "TWD",
    opening_balance: str = "0",
) -> BrokerAccount:
    return BrokerAccount(
        broker=broker,
        nickname=nickname,
        currency=currency,
        opening_balance=Decimal(opening_balance),
        opening_date=date(2025, 1, 1),
        is_active=True,
    )


def _transaction(
    index: int,
    *,
    type_: TransactionType,
    trade_date: datetime,
    symbol: str | None = None,
    market: str = "TW",
    currency: str = "TWD",
    broker: Broker | str | None = None,
    import_fingerprint: str | None = "csv-imported",
) -> Transaction:
    return Transaction(
        symbol=symbol or f"23{index:02d}",
        market=market,
        name="Test",
        type=type_,
        quantity=100,
        price=Decimal("10.00"),
        currency=currency,
        trade_date=trade_date,
        fee=Decimal("1.00"),
        tax=Decimal("2.00") if type_ == TransactionType.SELL else Decimal("0.00"),
        broker=broker.value if isinstance(broker, Broker) else broker,
        import_fingerprint=f"{import_fingerprint}-{index}" if import_fingerprint else None,
    )


def _dividend(
    index: int,
    *,
    ex_dividend_date: datetime,
    symbol: str | None = None,
    market: str = "TW",
    currency: str = "TWD",
) -> Dividend:
    return Dividend(
        symbol=symbol or f"23{index:02d}",
        market=market,
        amount=Decimal("25.00"),
        currency=currency,
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


def test_main_runs_without_all_flag_and_supports_dry_run(db_session, monkeypatch, capsys) -> None:
    account = _account()
    transaction = _transaction(
        1,
        type_=TransactionType.BUY,
        trade_date=datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc),
    )
    db_session.add_all([account, transaction])
    db_session.commit()

    import app.database

    monkeypatch.setattr(app.database, "SessionLocal", lambda: db_session)

    dry_run_exit_code = cash_backfill_service._main(["--dry-run"])
    dry_run_output = capsys.readouterr()

    assert dry_run_exit_code == 0
    assert "mode: dry-run" in dry_run_output.out
    assert _cash_rows(db_session) == []

    commit_exit_code = cash_backfill_service._main([])
    commit_output = capsys.readouterr()

    assert commit_exit_code == 0
    assert "mode: commit" in commit_output.out
    assert {row.account_id for row in _cash_rows(db_session)} == {account.id}


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


def test_replay_routes_transactions_to_matching_broker_currency_account(db_session) -> None:
    cathay = _account()
    firstrade = _account(
        broker=BrokerEnum.FIRSTRADE,
        nickname="Firstrade USD",
        currency="USD",
    )
    start = datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc)
    tw_cathay = _transaction(
        1,
        type_=TransactionType.BUY,
        trade_date=start,
        broker=Broker.TW_CATHAY,
        currency="TWD",
    )
    ft = _transaction(
        2,
        type_=TransactionType.BUY,
        trade_date=start + timedelta(days=1),
        symbol="AAPL",
        market="US",
        broker=Broker.FIRSTRADE,
        currency="USD",
    )
    legacy = _transaction(
        3,
        type_=TransactionType.BUY,
        trade_date=start + timedelta(days=2),
        broker=None,
        currency="TWD",
    )
    db_session.add_all([cathay, firstrade, tw_cathay, ft, legacy])
    db_session.commit()

    result = cash_backfill_service.replay_all(db_session)

    rows = db_session.scalars(
        select(CashTransaction).order_by(CashTransaction.related_transaction_id.asc())
    ).all()
    by_transaction_id = {}
    for row in rows:
        by_transaction_id.setdefault(row.related_transaction_id, set()).add(row.account_id)
    assert by_transaction_id == {
        tw_cathay.id: {cathay.id},
        ft.id: {firstrade.id},
        legacy.id: {cathay.id},
    }
    assert result.per_account_summary[cathay.id] == {"settle": 2, "fee": 2}
    assert result.per_account_summary[firstrade.id] == {"settle": 1, "fee": 1}


def test_replay_skips_transaction_when_broker_currency_account_missing(db_session) -> None:
    cathay = _account()
    schwab = _transaction(
        1,
        type_=TransactionType.BUY,
        trade_date=datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc),
        symbol="MSFT",
        market="US",
        broker=Broker.SCHWAB,
        currency="USD",
    )
    db_session.add_all([cathay, schwab])
    db_session.commit()

    result = cash_backfill_service.replay_all(db_session)

    assert result.transactions_processed == 1
    assert result.cash_rows_inserted == 0
    assert result.cash_rows_skipped == 1
    assert result.per_account_summary == {}
    assert _cash_rows(db_session) == []


def test_replay_routes_dividend_using_prior_matching_transaction_broker(db_session) -> None:
    cathay = _account()
    firstrade = _account(
        broker=BrokerEnum.FIRSTRADE,
        nickname="Firstrade USD",
        currency="USD",
    )
    buy = _transaction(
        1,
        type_=TransactionType.BUY,
        trade_date=datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc),
        symbol="AAPL",
        market="US",
        broker=Broker.FIRSTRADE,
        currency="USD",
    )
    dividend = _dividend(
        1,
        ex_dividend_date=datetime(2025, 2, 1, 9, 0, tzinfo=timezone.utc),
        symbol="AAPL",
        market="US",
        currency="USD",
    )
    db_session.add_all([cathay, firstrade, buy, dividend])
    db_session.commit()

    result = cash_backfill_service.replay_all(db_session)

    row = db_session.scalar(
        select(CashTransaction).where(CashTransaction.related_dividend_id == dividend.id)
    )
    assert row is not None
    assert row.account_id == firstrade.id
    assert result.per_account_summary[firstrade.id]["dividend_cash"] == 1
