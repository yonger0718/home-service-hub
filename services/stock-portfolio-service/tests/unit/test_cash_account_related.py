"""Related transaction / dividend fields on cash-transaction rows (Decision 7)."""

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import event

from app.models.broker_account import BrokerAccount, BrokerEnum
from app.models.cash_transaction import CashTransaction, CashTxnSource, CashTxnType
from app.models.portfolio import Dividend, Transaction, TransactionType
from app.schemas.cash_account import CashTransactionOut
from app.services import cash_account_service
from app.services.cash_account_service import annotate_related

TPE = timezone(timedelta(hours=8))
RELATED_FIELDS = (
    "related_symbol",
    "related_quantity",
    "related_trade_date",
    "related_per_share",
    "related_fee",
    "related_tax",
)


@contextmanager
def _count_statements(session):
    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", _capture)


def _account(db_session) -> BrokerAccount:
    account = BrokerAccount(
        broker=BrokerEnum.CATHAY,
        nickname="國泰",
        currency="TWD",
        opening_balance=Decimal("0"),
        opening_date=date(2026, 1, 1),
        is_active=True,
    )
    db_session.add(account)
    db_session.commit()
    return account


def _transaction(db_session, *, symbol: str, quantity: str, trade_date: datetime) -> Transaction:
    # Postgres timestamptz stores the instant and returns it tz-aware; the SQLite
    # test engine drops tzinfo on write, so store the UTC wall time (the
    # annotator reads a naive value as UTC, matching ``_resolve_sort_trade_date``).
    row = Transaction(
        symbol=symbol,
        type=TransactionType.BUY,
        quantity=Decimal(quantity),
        price=Decimal("1000"),
        trade_date=trade_date.astimezone(timezone.utc),
        fee=Decimal("0"),
        tax=Decimal("0"),
    )
    db_session.add(row)
    db_session.commit()
    return row


def _dividend(db_session) -> Dividend:
    row = Dividend(
        symbol="00878",
        amount=Decimal("9590"),
        ex_dividend_date=datetime(2026, 8, 18, tzinfo=timezone.utc),
        fee=Decimal("10"),
        tax=Decimal("0"),
        cash_dividend_per_share=Decimal("0.48"),
        quantity_at_record_date=Decimal("20000"),
    )
    db_session.add(row)
    db_session.commit()
    return row


def _cash(
    db_session,
    account_id: int,
    *,
    type_: CashTxnType,
    amount: str,
    txn_date: date = date(2026, 10, 6),
    related_transaction_id: int | None = None,
    related_dividend_id: int | None = None,
    source: CashTxnSource = CashTxnSource.AUTO_DERIVE,
    fingerprint: str,
) -> CashTransaction:
    row = CashTransaction(
        account_id=account_id,
        txn_date=txn_date,
        type=type_,
        amount=Decimal(amount),
        currency="TWD",
        related_transaction_id=related_transaction_id,
        related_dividend_id=related_dividend_id,
        source=source,
        import_fingerprint=fingerprint,
    )
    db_session.add(row)
    db_session.commit()
    return row


def _trade_legs(db_session, account_id: int, txn: Transaction) -> list[CashTransaction]:
    return [
        _cash(
            db_session,
            account_id,
            type_=leg_type,
            amount=amount,
            related_transaction_id=txn.id,
            fingerprint=f"leg-{txn.id}-{leg_type.value}",
        )
        for leg_type, amount in (
            (CashTxnType.BUY_SETTLE, "-1000000"),
            (CashTxnType.FEE, "-1425"),
            (CashTxnType.TAX, "0"),
        )
    ]


def _assert_all_none(row) -> None:
    for field in RELATED_FIELDS:
        value = row[field] if isinstance(row, dict) else getattr(row, field)
        assert value is None, field


def _by_type(items):
    return {item.type: item for item in items}


def test_trade_legs_share_transaction_fields_with_tpe_trade_date(db_session) -> None:
    account = _account(db_session)
    txn = _transaction(
        db_session,
        symbol="2330",
        quantity="1000",
        trade_date=datetime(2026, 10, 2, 23, 30, tzinfo=TPE),
    )
    _trade_legs(db_session, account.id, txn)

    items, total = cash_account_service.list_cash_transactions(db_session, account.id)

    assert total == 3
    assert len(items) == 3
    for item in items:
        assert isinstance(item, CashTransactionOut)
        assert item.related_symbol == "2330"
        assert item.related_quantity == Decimal("1000")
        assert item.related_trade_date == date(2026, 10, 2)
        assert item.related_per_share is None
        assert item.related_fee is None
        assert item.related_tax is None


def test_trade_date_just_after_tpe_midnight_uses_tpe_calendar_date(db_session) -> None:
    account = _account(db_session)
    # 2026-10-03 00:30 +08:00 == 2026-10-02 16:30 UTC: the UTC date is the day before.
    txn = _transaction(
        db_session,
        symbol="2330",
        quantity="1000",
        trade_date=datetime(2026, 10, 3, 0, 30, tzinfo=TPE),
    )
    _cash(
        db_session,
        account.id,
        type_=CashTxnType.BUY_SETTLE,
        amount="-1000000",
        related_transaction_id=txn.id,
        fingerprint="early",
    )

    items, _ = cash_account_service.list_cash_transactions(db_session, account.id)

    assert items[0].related_trade_date == date(2026, 10, 3)


def test_tpe_calendar_date_handles_aware_and_naive_datetimes() -> None:
    to_date = cash_account_service._tpe_calendar_date
    assert to_date(datetime(2026, 10, 2, 23, 30, tzinfo=TPE)) == date(2026, 10, 2)
    assert to_date(datetime(2026, 10, 3, 0, 30, tzinfo=TPE)) == date(2026, 10, 3)
    assert to_date(datetime(2026, 10, 2, 16, 30, tzinfo=timezone.utc)) == date(2026, 10, 3)
    # Naive values are UTC (timestamptz instants), as in _resolve_sort_trade_date.
    assert to_date(datetime(2026, 10, 2, 16, 30)) == date(2026, 10, 3)
    assert to_date(datetime(2026, 10, 2, 15, 59)) == date(2026, 10, 2)


def test_dividend_cash_leg_and_manual_deposit_through_router(client, db_session) -> None:
    account = _account(db_session)
    dividend = _dividend(db_session)
    _cash(
        db_session,
        account.id,
        type_=CashTxnType.DIVIDEND_CASH,
        amount="9590",
        txn_date=date(2026, 9, 10),
        related_dividend_id=dividend.id,
        fingerprint="div",
    )
    _cash(
        db_session,
        account.id,
        type_=CashTxnType.DEPOSIT,
        amount="20000",
        txn_date=date(2026, 9, 1),
        source=CashTxnSource.MANUAL,
        fingerprint="deposit",
    )

    response = client.get(f"/api/portfolio/accounts/{account.id}/cash-transactions")

    assert response.status_code == 200
    items = {item["type"]: item for item in response.json()["items"]}
    div = items["dividend_cash"]
    # Decimals serialise as strings with the column scale, exactly like ``amount``.
    assert div["amount"] == "9590.0000"
    assert div["related_symbol"] == "00878"
    assert div["related_per_share"] == "0.4800"
    assert div["related_quantity"] == "20000.0000"
    assert div["related_fee"] == "10.00"
    assert div["related_tax"] == "0.00"
    assert div["related_trade_date"] is None
    assert Decimal(div["related_per_share"]) == Decimal("0.48")
    assert Decimal(div["related_quantity"]) == Decimal("20000")
    assert Decimal(div["related_fee"]) == Decimal("10")
    assert Decimal(div["related_tax"]) == Decimal("0")
    deposit = items["deposit"]
    for field in RELATED_FIELDS:
        assert field in deposit
    _assert_all_none(deposit)


def test_trade_leg_json_shape_through_router(client, db_session) -> None:
    account = _account(db_session)
    txn = _transaction(
        db_session,
        symbol="2330",
        quantity="1000",
        trade_date=datetime(2026, 10, 2, 23, 30, tzinfo=TPE),
    )
    _trade_legs(db_session, account.id, txn)

    response = client.get(f"/api/portfolio/accounts/{account.id}/cash-transactions")

    assert response.status_code == 200
    for item in response.json()["items"]:
        assert item["related_symbol"] == "2330"
        assert item["related_quantity"] == "1000.0000"
        assert item["related_trade_date"] == "2026-10-02"
        assert item["related_per_share"] is None
        assert item["related_fee"] is None
        assert item["related_tax"] is None


def test_leg_whose_transaction_was_deleted_returns_nulls_with_200(client, db_session) -> None:
    account = _account(db_session)
    txn = _transaction(
        db_session,
        symbol="2330",
        quantity="1000",
        trade_date=datetime(2026, 10, 2, 23, 30, tzinfo=TPE),
    )
    _cash(
        db_session,
        account.id,
        type_=CashTxnType.BUY_SETTLE,
        amount="-1000000",
        related_transaction_id=txn.id,
        fingerprint="orphan",
    )
    _cash(
        db_session,
        account.id,
        type_=CashTxnType.DIVIDEND_CASH,
        amount="100",
        related_dividend_id=987654,
        fingerprint="orphan-div",
    )
    db_session.delete(txn)  # SQLite FKs are off: the leg keeps a dangling id
    db_session.commit()

    response = client.get(f"/api/portfolio/accounts/{account.id}/cash-transactions")
    merged = client.get(
        f"/api/portfolio/accounts/{account.id}/cash-transactions",
        params={"merge_related": "true"},
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 2
    assert {item["related_transaction_id"] for item in items} == {txn.id, None}
    for item in items:
        _assert_all_none(item)
    assert merged.status_code == 200
    for item in merged.json()["items"]:
        _assert_all_none(item)
        for leg in item["child_legs"] or []:
            _assert_all_none(leg)


def test_merged_group_parent_and_children_carry_transaction_fields(db_session) -> None:
    account = _account(db_session)
    txn = _transaction(
        db_session,
        symbol="2330",
        quantity="1000",
        trade_date=datetime(2026, 10, 2, 23, 30, tzinfo=TPE),
    )
    _trade_legs(db_session, account.id, txn)
    dividend = _dividend(db_session)
    _cash(
        db_session,
        account.id,
        type_=CashTxnType.DIVIDEND_CASH,
        amount="9590",
        txn_date=date(2026, 9, 10),
        related_dividend_id=dividend.id,
        fingerprint="div",
    )

    items, total = cash_account_service.list_cash_transactions(
        db_session, account.id, merge_related=True
    )

    assert total == 2
    rows = _by_type(items)
    parent = rows["trade"]
    assert parent.id == -txn.id
    assert parent.amount == Decimal("-1001425")
    assert parent.related_symbol == "2330"
    assert parent.related_quantity == Decimal("1000")
    assert parent.related_trade_date == date(2026, 10, 2)
    assert parent.related_per_share is None
    assert parent.child_legs is not None and len(parent.child_legs) == 3
    for leg in parent.child_legs:
        assert leg.related_symbol == "2330"
        assert leg.related_quantity == Decimal("1000")
        assert leg.related_trade_date == date(2026, 10, 2)
    div = rows[CashTxnType.DIVIDEND_CASH]
    assert div.related_symbol == "00878"
    assert div.related_per_share == Decimal("0.48")
    assert div.related_quantity == Decimal("20000")
    assert div.related_fee == Decimal("10")
    assert div.related_tax == Decimal("0")


def test_annotator_issues_at_most_two_statements_for_a_full_page(db_session) -> None:
    account = _account(db_session)
    dividend = _dividend(db_session)
    for n in range(10):
        txn = _transaction(
            db_session,
            symbol=f"{2300 + n}",
            quantity="1000",
            trade_date=datetime(2026, 10, 2, 10, 0, tzinfo=TPE),
        )
        for leg_type in (CashTxnType.BUY_SETTLE, CashTxnType.FEE):
            _cash(
                db_session,
                account.id,
                type_=leg_type,
                amount="-1",
                related_transaction_id=txn.id,
                fingerprint=f"q-{n}-{leg_type.value}",
            )
    for n in range(5):
        _cash(
            db_session,
            account.id,
            type_=CashTxnType.DIVIDEND_CASH,
            amount="1",
            related_dividend_id=dividend.id,
            fingerprint=f"qd-{n}",
        )
    for n in range(5):
        _cash(
            db_session,
            account.id,
            type_=CashTxnType.DEPOSIT,
            amount="1",
            source=CashTxnSource.MANUAL,
            fingerprint=f"qm-{n}",
        )
    account_id = account.id
    db_session.expire_all()
    all_rows = [
        CashTransactionOut.model_validate(row)
        for row in db_session.query(CashTransaction).order_by(CashTransaction.id).all()
    ]
    assert len(all_rows) == 30
    trade_only = [row for row in all_rows if row.related_transaction_id is not None]
    manual_only = [row for row in all_rows if row.type == CashTxnType.DEPOSIT]

    with _count_statements(db_session) as both:
        annotated = annotate_related(db_session, all_rows)
    with _count_statements(db_session) as one:
        annotate_related(db_session, trade_only)
    with _count_statements(db_session) as none:
        annotate_related(db_session, manual_only)

    assert len(both) == 2
    assert len(one) == 1
    assert len(none) == 0
    assert sum(1 for row in annotated if row.related_symbol is not None) == 25

    # The unmerged list path: count + page + the two annotator queries.
    with _count_statements(db_session) as listed:
        page, total = cash_account_service.list_cash_transactions(
            db_session, account_id, limit=30
        )
    assert total == 30 and len(page) == 30
    assert len(listed) == 4
