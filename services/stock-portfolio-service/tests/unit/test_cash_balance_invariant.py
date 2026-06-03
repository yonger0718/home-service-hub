from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.models.broker_account import BrokerAccount, BrokerEnum
from app.models.cash_transaction import CashTransaction
from app.models.portfolio import Dividend, Transaction, TransactionType
from app.services import cash_account_service, cash_backfill_service


def test_compute_on_read_balance_equals_opening_balance_plus_ledger_sum(db_session) -> None:
    account = BrokerAccount(
        broker=BrokerEnum.CATHAY,
        nickname="Cathay TWD",
        currency="TWD",
        opening_balance=Decimal("100.00"),
        opening_date=date(2025, 1, 1),
        is_active=True,
    )
    start = datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc)
    transactions = [
        Transaction(
            symbol=f"23{index:02d}",
            name="Test",
            type=TransactionType.BUY if index % 2 == 0 else TransactionType.SELL,
            quantity=100 + index,
            price=Decimal("10.00"),
            trade_date=start + timedelta(days=index),
            fee=Decimal("1.00"),
            tax=Decimal("2.00") if index % 2 == 1 else Decimal("0.00"),
            import_fingerprint=f"tx-{index}",
        )
        for index in range(6)
    ]
    dividend = Dividend(
        symbol="2300",
        amount=Decimal("12.34"),
        ex_dividend_date=start + timedelta(days=10),
        import_fingerprint="dividend-1",
    )
    db_session.add_all([account, *transactions, dividend])
    db_session.commit()

    cash_backfill_service.replay_all(db_session)

    row_amounts = db_session.scalars(
        select(CashTransaction.amount).where(CashTransaction.account_id == account.id)
    ).all()
    expected = account.opening_balance + sum(row_amounts, Decimal("0"))

    assert cash_account_service.get_balance(db_session, account.id) == expected
