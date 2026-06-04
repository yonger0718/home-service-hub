from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.broker_account import BrokerAccount, BrokerEnum
from app.models.cash_transaction import CashTransaction, CashTxnSource, CashTxnType
from app.models.fx_rate import FxRate
from app.schemas.cash_account import CashTransactionCreate
from app.services import cash_account_service, portfolio_snapshot_service


def _account(
    *,
    broker: BrokerEnum = BrokerEnum.FIRSTRADE,
    nickname: str = "Main",
    currency: str = "USD",
    opening_balance: str = "0",
    opening_date: date = date(2026, 1, 1),
    is_active: bool = True,
) -> BrokerAccount:
    return BrokerAccount(
        broker=broker,
        nickname=nickname,
        currency=currency,
        opening_balance=Decimal(opening_balance),
        opening_date=opening_date,
        is_active=is_active,
    )


def _cash(
    account_id: int,
    txn_date: date,
    type_: CashTxnType,
    amount: str,
    currency: str = "USD",
    fingerprint: str | None = None,
    source: CashTxnSource = CashTxnSource.MANUAL,
) -> CashTransaction:
    return CashTransaction(
        account_id=account_id,
        txn_date=txn_date,
        type=type_,
        amount=Decimal(amount),
        currency=currency,
        source=source,
        import_fingerprint=fingerprint or f"fp-{account_id}-{txn_date}-{type_.value}-{amount}",
    )


def test_get_balance_opening_plus_rows_and_asof_cutoff(db_session) -> None:
    account = _account(opening_balance="1000")
    db_session.add(account)
    db_session.commit()
    db_session.add_all(
        [
            _cash(account.id, date(2026, 2, 1), CashTxnType.DEPOSIT, "500"),
            _cash(account.id, date(2026, 3, 1), CashTxnType.WITHDRAW, "-200"),
            _cash(account.id, date(2026, 4, 1), CashTxnType.DEPOSIT, "100"),
        ]
    )
    db_session.commit()

    assert cash_account_service.get_balance(db_session, account.id, date(2026, 3, 15)) == Decimal("1300.0000")
    assert cash_account_service.get_balance(db_session, account.id, date(2026, 1, 1)) == Decimal("1000.0000")


def test_get_balance_history_step_fills_days(db_session) -> None:
    account = _account(opening_balance="0")
    db_session.add(account)
    db_session.commit()
    db_session.add(_cash(account.id, date(2026, 6, 2), CashTxnType.DEPOSIT, "1000"))
    db_session.commit()

    points = cash_account_service.get_balance_history(
        db_session,
        account.id,
        date(2026, 6, 1),
        date(2026, 6, 3),
    )

    assert [(point.date, point.balance) for point in points] == [
        (date(2026, 6, 1), Decimal("0.0000")),
        (date(2026, 6, 2), Decimal("1000.0000")),
        (date(2026, 6, 3), Decimal("1000.0000")),
    ]


def test_get_total_balance_in_converts_and_skips_missing_rate(db_session) -> None:
    usd = _account(nickname="US", currency="USD", opening_balance="1000")
    twd = _account(broker=BrokerEnum.CATHAY, nickname="TW", currency="TWD", opening_balance="30000")
    jpy = _account(broker=BrokerEnum.OTHER, nickname="JP", currency="JPY", opening_balance="100000")
    db_session.add_all([usd, twd, jpy])
    db_session.add(
        FxRate(
            date=date(2026, 6, 1),
            base_currency="USD",
            quote_currency="TWD",
            rate=Decimal("32.0"),
            source="test",
        )
    )
    db_session.commit()

    total, skipped = cash_account_service.get_total_balance_in(
        db_session,
        "TWD",
        asof=date(2026, 6, 1),
    )

    assert total == Decimal("62000.00000")
    assert skipped == ["JPY"]


def test_create_manual_cash_transaction_normalizes_and_is_idempotent(db_session) -> None:
    account = _account(currency="USD")
    db_session.add(account)
    db_session.commit()
    payload = CashTransactionCreate(
        txn_date=date(2026, 6, 1),
        type=CashTxnType.WITHDRAW,
        amount=Decimal("200"),
        currency="usd",
        note="Wire",
    )

    row = cash_account_service.create_manual_cash_transaction(db_session, account.id, payload)

    assert row.source == CashTxnSource.MANUAL
    assert row.currency == "USD"
    assert row.amount == Decimal("-200.0000")
    with pytest.raises(IntegrityError):
        cash_account_service.create_manual_cash_transaction(db_session, account.id, payload)
    db_session.rollback()
    assert db_session.execute(select(CashTransaction)).scalars().all()[0].amount == Decimal("-200.0000")


def test_create_manual_cash_transaction_refreshes_today_range(
    db_session,
    monkeypatch,
) -> None:
    today = date(2026, 6, 4)
    account = _account(currency="USD")
    db_session.add(account)
    db_session.commit()
    calls: list[tuple[date, date]] = []

    monkeypatch.setattr(portfolio_snapshot_service, "_today_tw", lambda: today)
    monkeypatch.setattr(
        portfolio_snapshot_service,
        "write_today_snapshot",
        lambda _session: (_ for _ in ()).throw(AssertionError("used today writer")),
    )
    monkeypatch.setattr(
        portfolio_snapshot_service,
        "refresh_snapshot_cash_range",
        lambda _session, start, end: calls.append((start, end)),
        raising=False,
    )

    cash_account_service.create_manual_cash_transaction(
        db_session,
        account.id,
        CashTransactionCreate(
            txn_date=today,
            type=CashTxnType.DEPOSIT,
            amount=Decimal("100"),
            currency="USD",
        ),
    )

    assert calls == [(today, today)]


def test_create_manual_cash_transaction_refreshes_backdated_range(
    db_session,
    monkeypatch,
) -> None:
    today = date(2026, 6, 4)
    backdated = date(2026, 6, 1)
    account = _account(currency="USD")
    db_session.add(account)
    db_session.commit()
    calls: list[tuple[date, date]] = []

    monkeypatch.setattr(portfolio_snapshot_service, "_today_tw", lambda: today)
    monkeypatch.setattr(
        portfolio_snapshot_service,
        "write_today_snapshot",
        lambda _session: (_ for _ in ()).throw(AssertionError("used today writer")),
    )
    monkeypatch.setattr(
        portfolio_snapshot_service,
        "refresh_snapshot_cash_range",
        lambda _session, start, end: calls.append((start, end)),
        raising=False,
    )

    cash_account_service.create_manual_cash_transaction(
        db_session,
        account.id,
        CashTransactionCreate(
            txn_date=backdated,
            type=CashTxnType.DEPOSIT,
            amount=Decimal("100"),
            currency="USD",
        ),
    )

    assert calls == [(backdated, today)]


def test_create_manual_cash_transaction_future_date_refreshes_today_only(
    db_session,
    monkeypatch,
) -> None:
    today = date(2026, 6, 4)
    future = date(2026, 7, 1)
    account = _account(currency="USD")
    db_session.add(account)
    db_session.commit()
    calls: list[tuple[date, date]] = []

    monkeypatch.setattr(portfolio_snapshot_service, "_today_tw", lambda: today)
    monkeypatch.setattr(
        portfolio_snapshot_service,
        "write_today_snapshot",
        lambda _session: (_ for _ in ()).throw(AssertionError("used today writer")),
    )
    monkeypatch.setattr(
        portfolio_snapshot_service,
        "refresh_snapshot_cash_range",
        lambda _session, start, end: calls.append((start, end)),
        raising=False,
    )

    cash_account_service.create_manual_cash_transaction(
        db_session,
        account.id,
        CashTransactionCreate(
            txn_date=future,
            type=CashTxnType.DEPOSIT,
            amount=Decimal("100"),
            currency="USD",
        ),
    )

    assert calls == [(today, today)]


def test_delete_manual_cash_transaction_refreshes_captured_txn_date_range(
    db_session,
    monkeypatch,
) -> None:
    today = date(2026, 6, 4)
    backdated = date(2026, 6, 1)
    account = _account()
    db_session.add(account)
    db_session.commit()
    row = _cash(account.id, backdated, CashTxnType.DEPOSIT, "500")
    db_session.add(row)
    db_session.commit()
    txn_id = row.id
    calls: list[tuple[date, date]] = []

    monkeypatch.setattr(portfolio_snapshot_service, "_today_tw", lambda: today)
    monkeypatch.setattr(
        portfolio_snapshot_service,
        "write_today_snapshot",
        lambda _session: (_ for _ in ()).throw(AssertionError("used today writer")),
    )
    monkeypatch.setattr(
        portfolio_snapshot_service,
        "refresh_snapshot_cash_range",
        lambda _session, start, end: calls.append((start, end)),
        raising=False,
    )

    deleted_id = cash_account_service.delete_manual_cash_transaction(
        db_session,
        account.id,
        txn_id,
    )

    assert deleted_id == txn_id
    assert calls == [(backdated, today)]


def test_create_manual_cash_transaction_refresh_failure_preserves_cash_commit(
    db_session,
    monkeypatch,
    caplog,
) -> None:
    today = date(2026, 6, 4)
    account = _account(currency="USD")
    db_session.add(account)
    db_session.commit()

    monkeypatch.setattr(portfolio_snapshot_service, "_today_tw", lambda: today)
    monkeypatch.setattr(portfolio_snapshot_service, "write_today_snapshot", lambda _session: None)

    def fail_refresh(_session, _start, _end):
        raise RuntimeError("snapshot failed")

    monkeypatch.setattr(
        portfolio_snapshot_service,
        "refresh_snapshot_cash_range",
        fail_refresh,
        raising=False,
    )

    cash_account_service.create_manual_cash_transaction(
        db_session,
        account.id,
        CashTransactionCreate(
            txn_date=today,
            type=CashTxnType.DEPOSIT,
            amount=Decimal("100"),
            currency="USD",
        ),
    )

    rows = db_session.execute(select(CashTransaction)).scalars().all()
    assert len(rows) == 1
    assert rows[0].amount == Decimal("100.0000")
    assert "failed to refresh cash snapshot range" in caplog.text


def test_create_manual_cash_transaction_rejects_currency_mismatch(db_session) -> None:
    account = _account(currency="TWD")
    db_session.add(account)
    db_session.commit()

    with pytest.raises(ValueError, match="currency must match account"):
        cash_account_service.create_manual_cash_transaction(
            db_session,
            account.id,
            CashTransactionCreate(
                txn_date=date(2026, 6, 1),
                type=CashTxnType.DEPOSIT,
                amount=Decimal("100"),
                currency="USD",
            ),
        )


def test_cash_transaction_create_rejects_non_alpha_currency() -> None:
    with pytest.raises(ValidationError):
        CashTransactionCreate(
            txn_date=date(2026, 6, 1),
            type=CashTxnType.DEPOSIT,
            amount=Decimal("100"),
            currency="1$A",
        )


def test_delete_manual_cash_transaction_returns_id_and_removes_row(db_session) -> None:
    account = _account()
    db_session.add(account)
    db_session.commit()
    row = _cash(account.id, date(2026, 6, 1), CashTxnType.DEPOSIT, "500")
    db_session.add(row)
    db_session.commit()

    deleted_id = cash_account_service.delete_manual_cash_transaction(db_session, account.id, row.id)

    assert deleted_id == row.id
    assert db_session.get(CashTransaction, row.id) is None


def test_delete_manual_cash_transaction_rejects_non_manual(db_session) -> None:
    account = _account()
    db_session.add(account)
    db_session.commit()
    row = _cash(
        account.id,
        date(2026, 6, 1),
        CashTxnType.BUY_SETTLE,
        "-500",
        source=CashTxnSource.AUTO_DERIVE,
    )
    db_session.add(row)
    db_session.commit()

    with pytest.raises(ValueError, match="not_manual"):
        cash_account_service.delete_manual_cash_transaction(db_session, account.id, row.id)

    assert db_session.get(CashTransaction, row.id) is not None


def test_delete_manual_cash_transaction_missing_raises_lookup_error(db_session) -> None:
    with pytest.raises(LookupError):
        cash_account_service.delete_manual_cash_transaction(db_session, 1, 99999)


def test_delete_manual_cash_transaction_wrong_account_raises_lookup_error(db_session) -> None:
    account = _account(nickname="Main")
    other = _account(nickname="Other")
    db_session.add_all([account, other])
    db_session.commit()
    row = _cash(other.id, date(2026, 6, 1), CashTxnType.DEPOSIT, "500")
    db_session.add(row)
    db_session.commit()

    with pytest.raises(LookupError):
        cash_account_service.delete_manual_cash_transaction(db_session, account.id, row.id)

    assert db_session.get(CashTransaction, row.id) is not None


def test_normalize_amount_sign_rules() -> None:
    assert cash_account_service.normalize_amount(CashTxnType.WITHDRAW, Decimal("200")) == Decimal("-200")
    assert cash_account_service.normalize_amount(CashTxnType.WITHDRAW, Decimal("-200")) == Decimal("-200")
    assert cash_account_service.normalize_amount(CashTxnType.DEPOSIT, Decimal("200")) == Decimal("200")
    assert cash_account_service.normalize_amount(CashTxnType.FX_CONVERT, Decimal("-20")) == Decimal("-20")
    with pytest.raises(ValueError):
        cash_account_service.normalize_amount(CashTxnType.DEPOSIT, Decimal("-200"))
    with pytest.raises(ValueError):
        cash_account_service.normalize_amount(CashTxnType.FEE, Decimal("0"))


def test_fingerprint_helpers_are_deterministic_and_distinct() -> None:
    manual = cash_account_service.compute_manual_fingerprint(
        1,
        date(2026, 6, 1),
        CashTxnType.DEPOSIT,
        Decimal("5000"),
        "Wire",
    )
    assert manual == cash_account_service.compute_manual_fingerprint(
        1,
        date(2026, 6, 1),
        CashTxnType.DEPOSIT,
        Decimal("5000"),
        "Wire",
    )
    assert len(manual) == 64
    assert manual != cash_account_service.compute_manual_fingerprint(
        1,
        date(2026, 6, 1),
        CashTxnType.DEPOSIT,
        Decimal("5001"),
        "Wire",
    )
    assert cash_account_service.compute_backfill_fingerprint("transactions", 1, "fee") != (
        cash_account_service.compute_csv_fingerprint("cathay", "abc", "fee")
    )
