from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from datetime import date as dt_date
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.broker_account import BrokerAccount
from app.models.cash_transaction import CashTransaction, CashTxnSource, CashTxnType
from app.models.portfolio import Dividend, Transaction, TransactionType
from app.services import cash_account_service

logger = logging.getLogger(__name__)


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


def replay_all(session: Session, *, dry_run: bool = False) -> BackfillResult:
    result = BackfillResult(dry_run=dry_run)
    try:
        default_account = cash_account_service.resolve_default_cathay_twd_account(session)

        transactions = session.scalars(
            select(Transaction).order_by(Transaction.trade_date.asc(), Transaction.id.asc())
        ).all()
        for transaction in transactions:
            result.transactions_processed += 1
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
                _increment_summary(result, default_account.id, leg_name)
                if not dry_run:
                    _add_transaction_cash_row(
                        session,
                        account=default_account,
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
            fingerprint = cash_account_service.compute_backfill_fingerprint(
                "dividends",
                dividend.id,
                "dividend_cash",
            )
            if _cash_row_exists(session, fingerprint):
                result.cash_rows_skipped += 1
                continue

            result.cash_rows_inserted += 1
            _increment_summary(result, default_account.id, "dividend_cash")
            if not dry_run:
                _add_dividend_cash_row(
                    session,
                    account=default_account,
                    dividend=dividend,
                    fingerprint=fingerprint,
                )
                session.flush()

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
        help="replay every transaction and dividend row",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="compute rows that would be inserted without writing them",
    )
    args = parser.parse_args(argv)

    if not args.all:
        parser.print_help(sys.stderr)
        return 2

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
