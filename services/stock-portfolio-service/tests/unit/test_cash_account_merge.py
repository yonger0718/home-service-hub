from datetime import date, datetime
from decimal import Decimal

from app.models.cash_transaction import CashTransaction, CashTxnSource, CashTxnType
from app.services.cash_account_service import _merge_legs_into_groups


def _cash(
    *,
    id: int,
    type_: CashTxnType,
    amount: str,
    txn_date: date,
    related_transaction_id: int | None = 42,
) -> CashTransaction:
    return CashTransaction(
        id=id,
        account_id=1,
        txn_date=txn_date,
        type=type_,
        amount=Decimal(amount),
        currency="USD",
        related_transaction_id=related_transaction_id,
        source=CashTxnSource.AUTO_DERIVE,
        import_fingerprint=f"merge-{id}",
        created_at=datetime(2026, 6, id, 9, 0, 0),
    )


def test_merge_legs_builds_synthetic_group_with_ordered_children() -> None:
    tax = _cash(id=3, type_=CashTxnType.TAX, amount="-300", txn_date=date(2026, 6, 4))
    fee = _cash(id=2, type_=CashTxnType.FEE, amount="-285", txn_date=date(2026, 6, 3))
    settle = _cash(id=1, type_=CashTxnType.BUY_SETTLE, amount="-100000", txn_date=date(2026, 6, 2))

    rows = _merge_legs_into_groups([tax, fee, settle])

    assert len(rows) == 1
    group = rows[0]
    assert group.id == -42
    assert group.type == "trade"
    assert group.amount == Decimal("-100585")
    assert group.txn_date == date(2026, 6, 2)
    assert group.related_transaction_id == 42
    assert group.child_legs is not None
    assert [leg.type for leg in group.child_legs] == [
        CashTxnType.BUY_SETTLE,
        CashTxnType.FEE,
        CashTxnType.TAX,
    ]
    assert [leg.id for leg in group.child_legs] == [1, 2, 3]


def test_merge_legs_keeps_standalone_rows_individual() -> None:
    settle = _cash(id=1, type_=CashTxnType.SELL_SETTLE, amount="1000", txn_date=date(2026, 6, 2))
    dividend = _cash(
        id=4,
        type_=CashTxnType.DIVIDEND_CASH,
        amount="10",
        txn_date=date(2026, 6, 5),
        related_transaction_id=None,
    )
    dividend.related_dividend_id = 9

    rows = _merge_legs_into_groups([settle, dividend])

    assert len(rows) == 2
    assert rows[0].type == "trade"
    assert rows[1].id == 4
    assert rows[1].type == CashTxnType.DIVIDEND_CASH
    assert rows[1].child_legs is None
