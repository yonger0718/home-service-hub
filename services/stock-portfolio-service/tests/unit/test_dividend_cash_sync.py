from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.models.broker_account import BrokerAccount, BrokerEnum
from app.models.cash_transaction import CashTransaction, CashTxnSource, CashTxnType
from app.models.portfolio import Dividend, PositionSide, Transaction, TransactionType
from app.schemas import portfolio as schemas
from app.services import dividend_auto_record_service, import_service, portfolio_service
from app.services.dividend_history_service import HistoricalDividendEvent

TW = timezone(timedelta(hours=8))


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


def _dividend_payload(
    *,
    symbol: str = "0050.tw",
    amount: str = "4500.00",
    ex_date: datetime | None = None,
    market: str = "TW",
    currency: str = "TWD",
    fx_rate_to_twd: str | None = None,
) -> schemas.DividendCreate:
    return schemas.DividendCreate(
        symbol=symbol,
        market=market,
        amount=Decimal(amount),
        currency=currency,
        fx_rate_to_twd=Decimal(fx_rate_to_twd) if fx_rate_to_twd is not None else None,
        ex_dividend_date=ex_date or datetime(2026, 6, 1, tzinfo=timezone.utc),
        received_date=datetime(2026, 6, 15, tzinfo=timezone.utc),
    )


def _dividend_cash_rows(db_session) -> list[CashTransaction]:
    return db_session.execute(
        select(CashTransaction)
        .where(CashTransaction.type == CashTxnType.DIVIDEND_CASH)
        .order_by(CashTransaction.id.asc())
    ).scalars().all()


def _add_holding(db_session, *, symbol: str = "2330") -> None:
    db_session.add(
        Transaction(
            symbol=symbol,
            type=TransactionType.BUY,
            position_side=PositionSide.LONG,
            quantity=1000,
            price=Decimal("100"),
            trade_date=datetime(2026, 5, 1, tzinfo=TW),
            fee=Decimal("0"),
            tax=Decimal("0"),
            is_day_trade=False,
        )
    )
    db_session.commit()


def test_portfolio_create_dividend_emits_auto_derive_cash_leg(db_session, monkeypatch) -> None:
    monkeypatch.setenv("CASH_LEG_ENABLED", "true")
    account = _add_cathay_account(db_session)

    dividend = portfolio_service.create_dividend(db_session, _dividend_payload())

    rows = _dividend_cash_rows(db_session)
    assert len(rows) == 1
    row = rows[0]
    assert row.account_id == account.id
    assert row.related_dividend_id == dividend.id
    assert row.related_transaction_id is None
    assert row.txn_date == date(2026, 6, 1)
    assert row.amount == Decimal("4500.0000")
    assert row.currency == "TWD"
    assert row.source == CashTxnSource.AUTO_DERIVE


def test_portfolio_create_foreign_dividend_skips_twd_cash_sync(
    db_session, monkeypatch
) -> None:
    monkeypatch.setenv("CASH_LEG_ENABLED", "true")
    _add_cathay_account(db_session)

    portfolio_service.create_dividend(
        db_session,
        _dividend_payload(
            symbol="AAPL",
            market="US",
            amount="10.00",
            currency="USD",
            fx_rate_to_twd="32",
        ),
    )

    assert _dividend_cash_rows(db_session) == []


def test_update_dividend_to_foreign_currency_deletes_stale_twd_cash_leg(
    db_session,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CASH_LEG_ENABLED", "true")
    _add_cathay_account(db_session)
    dividend = portfolio_service.create_dividend(db_session, _dividend_payload())
    assert [row.related_dividend_id for row in _dividend_cash_rows(db_session)] == [dividend.id]

    portfolio_service.update_dividend(
        db_session,
        dividend.id,
        _dividend_payload(
            symbol="AAPL",
            market="US",
            amount="10.00",
            currency="USD",
            fx_rate_to_twd="32",
        ),
    )

    assert _dividend_cash_rows(db_session) == []


def test_generic_csv_dividend_import_emits_auto_derive_cash_leg(db_session, monkeypatch) -> None:
    monkeypatch.setenv("CASH_LEG_ENABLED", "true")
    _add_cathay_account(db_session)
    raw = (
        "symbol,amount,ex_dividend_date,received_date\n"
        "0050,4500.00,2026-06-01T00:00:00Z,2026-06-15T00:00:00Z\n"
    ).encode("utf-8")
    parsed = import_service.parse_dividends_csv(raw)

    result = import_service.commit_dividends(db_session, parsed, dry_run=False)

    assert result.created == 1
    dividend = db_session.query(Dividend).one()
    rows = _dividend_cash_rows(db_session)
    assert len(rows) == 1
    assert rows[0].related_dividend_id == dividend.id
    assert rows[0].amount == Decimal("4500.0000")
    assert rows[0].source == CashTxnSource.AUTO_DERIVE


def test_auto_record_dividend_emits_auto_derive_cash_leg(db_session, monkeypatch) -> None:
    monkeypatch.setenv("CASH_LEG_ENABLED", "true")
    _add_cathay_account(db_session)
    _add_holding(db_session, symbol="2330")
    event = HistoricalDividendEvent(
        symbol="2330",
        ex_date=date(2026, 6, 15),
        cash_dividend_per_share=Decimal("2.00"),
        stock_dividend_per_thousand=None,
        previous_close=None,
        reference_price=None,
        source="TWT49U",
    )

    result = dividend_auto_record_service.auto_record_for_event(db_session, event)

    assert result.cash_inserted is True
    dividend = db_session.query(Dividend).one()
    rows = _dividend_cash_rows(db_session)
    assert len(rows) == 1
    assert rows[0].related_dividend_id == dividend.id
    assert rows[0].amount == Decimal("1990.0000")
    assert rows[0].source == CashTxnSource.AUTO_DERIVE


def test_dividend_paths_with_flag_disabled_emit_no_cash_legs(db_session, monkeypatch) -> None:
    monkeypatch.delenv("CASH_LEG_ENABLED", raising=False)

    portfolio_service.create_dividend(db_session, _dividend_payload(symbol="0050", amount="100.00"))
    raw = (
        "symbol,amount,ex_dividend_date,received_date\n"
        "006208,200.00,2026-06-02T00:00:00Z,\n"
    ).encode("utf-8")
    import_service.commit_dividends(db_session, import_service.parse_dividends_csv(raw), dry_run=False)
    _add_holding(db_session, symbol="2330")
    event = HistoricalDividendEvent(
        symbol="2330",
        ex_date=date(2026, 6, 15),
        cash_dividend_per_share=Decimal("1.00"),
        stock_dividend_per_thousand=None,
        previous_close=None,
        reference_price=None,
        source="TWT49U",
    )
    dividend_auto_record_service.auto_record_for_event(db_session, event)

    assert db_session.query(Dividend).count() == 3
    assert db_session.query(CashTransaction).count() == 0
