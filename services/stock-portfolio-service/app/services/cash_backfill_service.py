from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date as dt_date
from datetime import datetime
from decimal import Decimal

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.broker_account import BrokerAccount, BrokerEnum
from app.models.cash_transaction import CashTransaction, CashTxnSource, CashTxnType
from app.models.portfolio import Broker, BrokerCashFlow, Dividend, Transaction, TransactionType
from app.services import cash_account_service

logger = structlog.get_logger(__name__)

_BROKER_ENUM_MAP: dict[str, BrokerEnum] = {
    Broker.TW_CATHAY.value: BrokerEnum.CATHAY,
    Broker.TW_SINOPAC.value: BrokerEnum.SINOPAC,
    Broker.TW_MANUAL.value: BrokerEnum.CATHAY,
    Broker.IB.value: BrokerEnum.IB,
    Broker.FIRSTRADE.value: BrokerEnum.FIRSTRADE,
    Broker.SCHWAB.value: BrokerEnum.CS,
    Broker.FOREIGN_MANUAL.value: BrokerEnum.OTHER,
}

_BROKER_CASH_FLOW_TYPE_MAP: dict[str, CashTxnType] = {
    "deposit": CashTxnType.DEPOSIT,
    "withdrawal": CashTxnType.WITHDRAW,
    "interest": CashTxnType.INTEREST_IN,
    "dividend_cash": CashTxnType.DIVIDEND_CASH,
    "fee": CashTxnType.FEE,
}


@dataclass
class BackfillResult:
    transactions_processed: int = 0
    dividends_processed: int = 0
    cash_rows_inserted: int = 0
    cash_rows_skipped: int = 0
    per_account_summary: dict[int, dict[str, int]] = field(default_factory=dict)
    dry_run: bool = False
    errors: list[str] = field(default_factory=list)


def _as_decimal(value: Decimal | int | str | None) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _date_of(value: datetime | dt_date | object) -> dt_date:
    return value.date() if hasattr(value, "date") else value  # type: ignore[return-value]


def _source_for(import_fingerprint: str | None) -> CashTxnSource:
    return CashTxnSource.CSV_IMPORT if import_fingerprint is not None else CashTxnSource.AUTO_DERIVE


def _increment_summary(result: BackfillResult, account_id: int, leg_name: str) -> None:
    account_summary = result.per_account_summary.setdefault(account_id, {})
    account_summary[leg_name] = account_summary.get(leg_name, 0) + 1


def _transaction_legs(transaction: Transaction) -> list[dict[str, object]]:
    gross_amount = _as_decimal(transaction.quantity) * _as_decimal(transaction.price)
    if transaction.type == TransactionType.BUY:
        settle_type = CashTxnType.BUY_SETTLE
        settle_amount = -gross_amount
    else:
        settle_type = CashTxnType.SELL_SETTLE
        settle_amount = gross_amount

    legs: list[dict[str, object]] = [
        {
            "leg_name": "settle",
            "type": settle_type,
            "amount": settle_amount,
        }
    ]
    fee = _as_decimal(transaction.fee)
    if fee > 0:
        legs.append(
            {
                "leg_name": "fee",
                "type": CashTxnType.FEE,
                "amount": -fee,
            }
        )
    tax = _as_decimal(transaction.tax)
    if tax > 0:
        legs.append(
            {
                "leg_name": "tax",
                "type": CashTxnType.TAX,
                "amount": -tax,
            }
        )
    return legs


def _cash_row_exists(session: Session, fingerprint: str) -> bool:
    return (
        session.scalar(
            select(CashTransaction).where(CashTransaction.import_fingerprint == fingerprint)
        )
        is not None
    )


def _normalize_currency(value: str | None) -> str:
    return (value or "TWD").upper()


def _resolve_account_for_broker_currency(
    session: Session,
    *,
    broker: str | None,
    currency: str | None,
) -> BrokerAccount | None:
    if broker is None:
        return cash_account_service.resolve_default_cathay_twd_account(session)

    mapped_broker = _BROKER_ENUM_MAP.get(broker)
    if mapped_broker is None:
        logger.warning("cash_backfill.broker_unmapped", broker=broker)
        return None

    normalized_currency = _normalize_currency(currency)
    account = session.scalar(
        select(BrokerAccount)
        .where(
            BrokerAccount.broker == mapped_broker,
            BrokerAccount.currency == normalized_currency,
            BrokerAccount.is_active.is_(True),
        )
        .order_by(BrokerAccount.id.asc())
    )
    if account is None:
        logger.warning(
            "cash_backfill.account_missing",
            broker=mapped_broker.value,
            currency=normalized_currency,
        )
        return None
    return account


def _resolve_account_for_transaction(
    session: Session,
    transaction: Transaction,
) -> BrokerAccount | None:
    return _resolve_account_for_broker_currency(
        session,
        broker=transaction.broker,
        currency=transaction.currency,
    )


def _resolve_account_for_dividend(
    session: Session,
    dividend: Dividend,
) -> BrokerAccount | None:
    query = (
        select(Transaction)
        .where(
            Transaction.symbol == dividend.symbol,
            Transaction.trade_date <= dividend.ex_dividend_date,
        )
        .order_by(Transaction.trade_date.desc(), Transaction.id.desc())
        .limit(1)
    )
    if dividend.market:
        query = query.where(Transaction.market == dividend.market)

    transaction = session.scalar(query)
    if transaction is not None:
        return _resolve_account_for_broker_currency(
            session,
            broker=transaction.broker,
            currency=dividend.currency,
        )

    currency = _normalize_currency(dividend.currency)
    if currency == "TWD":
        return cash_account_service.resolve_default_cathay_twd_account(session)

    logger.warning(
        "cash_backfill.dividend_broker_missing",
        dividend_id=dividend.id,
        symbol=dividend.symbol,
        market=dividend.market,
        currency=currency,
    )
    return None


def _add_transaction_cash_row(
    session: Session,
    *,
    account: BrokerAccount,
    transaction: Transaction,
    leg: dict[str, object],
    fingerprint: str,
) -> None:
    session.add(
        CashTransaction(
            account_id=account.id,
            txn_date=_date_of(transaction.trade_date),
            type=leg["type"],
            amount=leg["amount"],
            currency=account.currency,
            related_transaction_id=transaction.id,
            related_dividend_id=None,
            source=_source_for(transaction.import_fingerprint),
            import_fingerprint=fingerprint,
        )
    )


def _add_dividend_cash_row(
    session: Session,
    *,
    account: BrokerAccount,
    dividend: Dividend,
    fingerprint: str,
) -> None:
    session.add(
        CashTransaction(
            account_id=account.id,
            txn_date=_date_of(dividend.ex_dividend_date),
            type=CashTxnType.DIVIDEND_CASH,
            amount=_as_decimal(dividend.amount),
            currency=account.currency,
            related_transaction_id=None,
            related_dividend_id=dividend.id,
            source=_source_for(dividend.import_fingerprint),
            import_fingerprint=fingerprint,
        )
    )


def _replay_broker_cash_flows(
    session: Session,
    *,
    dry_run: bool,
    result: BackfillResult,
) -> None:
    flows = session.scalars(
        select(BrokerCashFlow).order_by(BrokerCashFlow.date.asc(), BrokerCashFlow.id.asc())
    ).all()
    rows_added = 0
    for flow in flows:
        account = _resolve_account_for_broker_currency(
            session,
            broker=flow.broker,
            currency=flow.currency,
        )
        if account is None:
            result.cash_rows_skipped += 1
            logger.warning(
                "cash_backfill.broker_cash_flow_skipped",
                broker=flow.broker,
                currency=_normalize_currency(flow.currency),
                broker_cash_flow_id=flow.id,
            )
            continue

        mapped_type = _BROKER_CASH_FLOW_TYPE_MAP.get(flow.type)
        if mapped_type is None:
            result.cash_rows_skipped += 1
            logger.warning(
                "cash_backfill.broker_cash_flow_type_unmapped",
                broker_cash_flow_id=flow.id,
                type=flow.type,
            )
            continue

        type_value = mapped_type.value
        fingerprint = cash_account_service.compute_backfill_fingerprint(
            "broker_cash_flows",
            flow.id,
            type_value,
        )
        if _cash_row_exists(session, fingerprint):
            result.cash_rows_skipped += 1
            continue

        result.cash_rows_inserted += 1
        _increment_summary(result, account.id, type_value)
        if not dry_run:
            session.add(
                CashTransaction(
                    account_id=account.id,
                    txn_date=flow.date,
                    type=mapped_type,
                    amount=flow.amount,
                    currency=_normalize_currency(flow.currency),
                    related_transaction_id=None,
                    related_dividend_id=None,
                    note=flow.note,
                    source=CashTxnSource.CSV_IMPORT,
                    import_fingerprint=fingerprint,
                )
            )
            rows_added += 1

    if rows_added:
        session.flush()


def replay_all(session: Session, *, dry_run: bool = False) -> BackfillResult:
    result = BackfillResult(dry_run=dry_run)
    try:
        transactions = session.scalars(
            select(Transaction).order_by(Transaction.trade_date.asc(), Transaction.id.asc())
        ).all()
        for transaction in transactions:
            result.transactions_processed += 1
            account = _resolve_account_for_transaction(session, transaction)
            if account is None:
                result.cash_rows_skipped += 1
                logger.warning(
                    "cash_backfill.transaction_skipped",
                    transaction_id=transaction.id,
                    broker=transaction.broker,
                    currency=_normalize_currency(transaction.currency),
                )
                continue

            rows_added = 0
            for leg in _transaction_legs(transaction):
                leg_name = str(leg["leg_name"])
                fingerprint = cash_account_service.compute_backfill_fingerprint(
                    "transactions",
                    transaction.id,
                    leg_name,
                )
                if _cash_row_exists(session, fingerprint):
                    result.cash_rows_skipped += 1
                    continue

                result.cash_rows_inserted += 1
                _increment_summary(result, account.id, leg_name)
                if not dry_run:
                    _add_transaction_cash_row(
                        session,
                        account=account,
                        transaction=transaction,
                        leg=leg,
                        fingerprint=fingerprint,
                    )
                    rows_added += 1
            if rows_added:
                session.flush()

        dividends = session.scalars(
            select(Dividend).order_by(Dividend.ex_dividend_date.asc(), Dividend.id.asc())
        ).all()
        for dividend in dividends:
            result.dividends_processed += 1
            account = _resolve_account_for_dividend(session, dividend)
            if account is None:
                result.cash_rows_skipped += 1
                logger.warning(
                    "cash_backfill.dividend_skipped",
                    dividend_id=dividend.id,
                    symbol=dividend.symbol,
                    market=dividend.market,
                    currency=_normalize_currency(dividend.currency),
                )
                continue

            fingerprint = cash_account_service.compute_backfill_fingerprint(
                "dividends",
                dividend.id,
                "dividend_cash",
            )
            if _cash_row_exists(session, fingerprint):
                result.cash_rows_skipped += 1
                continue

            result.cash_rows_inserted += 1
            _increment_summary(result, account.id, "dividend_cash")
            if not dry_run:
                _add_dividend_cash_row(
                    session,
                    account=account,
                    dividend=dividend,
                    fingerprint=fingerprint,
                )
                session.flush()

        _replay_broker_cash_flows(session, dry_run=dry_run, result=result)

        if not dry_run:
            session.commit()
        return result
    except Exception:
        session.rollback()
        raise


def _print_summary(result: BackfillResult) -> None:
    mode = "dry-run" if result.dry_run else "commit"
    print(f"mode: {mode}")
    print(f"transactions: {result.transactions_processed}")
    print(f"dividends: {result.dividends_processed}")
    print(f"inserted: {result.cash_rows_inserted}")
    print(f"skipped: {result.cash_rows_skipped}")
    print("per-leg totals:")
    for account_id in sorted(result.per_account_summary):
        for leg_name, count in sorted(result.per_account_summary[account_id].items()):
            print(f"  account {account_id} {leg_name}: {count}")


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay portfolio transactions and dividends into cash_transaction rows."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="deprecated no-op; replay always processes every transaction and dividend row",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="compute rows that would be inserted without writing them",
    )
    args = parser.parse_args(argv)

    from app.database import SessionLocal

    session = SessionLocal()
    try:
        result = replay_all(session, dry_run=args.dry_run)
        _print_summary(result)
        return 0
    except (
        cash_account_service.CashAccountNotFound,
        cash_account_service.CashAccountAmbiguous,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception:
        session.rollback()
        logger.exception("cash backfill failed")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(_main())
