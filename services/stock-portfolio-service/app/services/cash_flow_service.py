from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256

from sqlalchemy import Date, cast, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import portfolio as models


@dataclass(frozen=True)
class CashFlowRow:
    broker: str
    date: date
    type: str
    amount: Decimal
    currency: str
    fx_rate_to_twd: Decimal | None
    note: str | None
    import_fingerprint: str | None = None


@dataclass(frozen=True)
class CashFlowWriteResult:
    created: int
    skipped_duplicates: int
    created_ids: list[int]


def cash_flow_fingerprint(
    *,
    broker: str,
    date_: date,
    type_: str,
    amount: Decimal,
    currency: str,
    note: str | None,
) -> str:
    canonical = "|".join(
        (
            broker,
            date_.isoformat(),
            type_,
            f"{amount:.4f}",
            currency.upper(),
            note or "",
        )
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def write_cash_flows(
    db: Session,
    rows: list[CashFlowRow],
    *,
    dry_run: bool = False,
) -> CashFlowWriteResult:
    existing = {
        fingerprint
        for (fingerprint,) in db.query(models.BrokerCashFlow.import_fingerprint)
        .filter(models.BrokerCashFlow.import_fingerprint.is_not(None))
        .all()
    }
    seen: set[str] = set()
    created_ids: list[int] = []
    skipped = 0
    for row in rows:
        fingerprint = row.import_fingerprint or cash_flow_fingerprint(
            broker=row.broker,
            date_=row.date,
            type_=row.type,
            amount=row.amount,
            currency=row.currency,
            note=row.note,
        )
        if fingerprint in existing or fingerprint in seen:
            skipped += 1
            continue
        seen.add(fingerprint)
        if dry_run:
            continue
        db_row = models.BrokerCashFlow(
            broker=row.broker,
            date=row.date,
            type=row.type,
            amount=row.amount,
            currency=row.currency.upper(),
            fx_rate_to_twd=row.fx_rate_to_twd,
            note=row.note,
            import_fingerprint=fingerprint,
        )
        db.add(db_row)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            skipped += 1
            continue
        created_ids.append(db_row.id)
    if not dry_run:
        db.commit()
    return CashFlowWriteResult(
        created=len(created_ids),
        skipped_duplicates=skipped,
        created_ids=created_ids,
    )


def _trade_cash_deltas(
    db: Session, *, as_of_date: date
) -> dict[tuple[str, str], Decimal]:
    """Per (broker, currency) net cash impact from BUY/SELL transactions.

    BUY → outflow (qty*price + fee + tax); SELL → inflow (qty*price - fee - tax).
    Stored in the transaction's native currency. TW_MANUAL and TW_CATHAY excluded:
    the legacy cash_transaction ledger (account_id=1) already materializes their
    buy_settle/sell_settle/fee/tax rows and is folded in by ``list_balances``.
    """
    rows = (
        db.query(
            models.Transaction.broker,
            models.Transaction.currency,
            models.Transaction.type,
            func.coalesce(
                func.sum(
                    models.Transaction.quantity * models.Transaction.price
                ),
                0,
            ),
            func.coalesce(func.sum(models.Transaction.fee), 0),
            func.coalesce(func.sum(models.Transaction.tax), 0),
        )
        .filter(models.Transaction.broker.isnot(None))
        .filter(
            models.Transaction.broker.notin_(
                [models.Broker.TW_MANUAL.value, models.Broker.TW_CATHAY.value]
            )
        )
        .filter(cast(models.Transaction.trade_date, Date) <= as_of_date)
        .group_by(
            models.Transaction.broker,
            models.Transaction.currency,
            models.Transaction.type,
        )
        .all()
    )
    deltas: dict[tuple[str, str], Decimal] = {}
    for broker, currency, tx_type, gross, fee, tax in rows:
        gross_d = Decimal(gross or "0")
        fee_d = Decimal(fee or "0")
        tax_d = Decimal(tax or "0")
        cur = (currency or "TWD").upper()
        if tx_type == models.TransactionType.BUY:
            delta = -(gross_d + fee_d + tax_d)
        else:
            delta = gross_d - fee_d - tax_d
        key = (broker, cur)
        deltas[key] = deltas.get(key, Decimal("0")) + delta
    return deltas


def get_broker_balance(db: Session, broker: str, as_of_date: date) -> Decimal:
    """Sum of explicit cash flows MINUS trade outflows PLUS trade inflows.

    Returned in the broker's primary currency only — multi-currency brokers
    should use :func:`list_balances` to see the per-currency split.
    """
    value = (
        db.query(func.coalesce(func.sum(models.BrokerCashFlow.amount), 0))
        .filter(models.BrokerCashFlow.broker == broker)
        .filter(models.BrokerCashFlow.date <= as_of_date)
        .scalar()
    )
    balance = Decimal(value or "0")
    trade_deltas = _trade_cash_deltas(db, as_of_date=as_of_date)
    for (b, _cur), delta in trade_deltas.items():
        if b == broker:
            balance += delta
    return balance


def _legacy_tw_cathay_balance(db: Session, *, as_of_date: date) -> Decimal | None:
    """Sum of cash_transaction rows on the legacy Cathay TWD account.

    The legacy ledger pre-dates Phase 4 broker_cash_flows and is still the
    source of truth for TW deposits/withdrawals/dividend_cash plus the
    pre-materialized buy_settle/sell_settle/fee/tax legs. We virtualize a
    single (TW_CATHAY, TWD) row in ``list_balances`` so the dashboard tile
    and the accounts page render the same figure.
    """
    from ..models.broker_account import BrokerAccount, BrokerEnum
    from ..models.cash_transaction import CashTransaction

    account_id = (
        db.query(BrokerAccount.id)
        .filter(
            BrokerAccount.broker == BrokerEnum.CATHAY,
            BrokerAccount.currency == "TWD",
            BrokerAccount.is_active.is_(True),
        )
        .order_by(BrokerAccount.id.asc())
        .scalar()
    )
    if account_id is None:
        return None
    total = (
        db.query(func.coalesce(func.sum(CashTransaction.amount), 0))
        .filter(CashTransaction.account_id == account_id)
        .filter(CashTransaction.txn_date <= as_of_date)
        .scalar()
    )
    return Decimal(total or "0")


def list_balances(db: Session, *, as_of_date: date | None = None) -> list[dict[str, object]]:
    effective_date = as_of_date or date.today()
    rows = (
        db.query(
            models.BrokerCashFlow.broker,
            models.BrokerCashFlow.currency,
            func.coalesce(func.sum(models.BrokerCashFlow.amount), 0),
        )
        .filter(models.BrokerCashFlow.date <= effective_date)
        .group_by(models.BrokerCashFlow.broker, models.BrokerCashFlow.currency)
        .order_by(models.BrokerCashFlow.broker, models.BrokerCashFlow.currency)
        .all()
    )
    per_key: dict[tuple[str, str], Decimal] = {
        (broker, currency): Decimal(balance or "0")
        for broker, currency, balance in rows
    }
    trade_deltas = _trade_cash_deltas(db, as_of_date=effective_date)
    for key, delta in trade_deltas.items():
        per_key[key] = per_key.get(key, Decimal("0")) + delta
    tw_balance = _legacy_tw_cathay_balance(db, as_of_date=effective_date)
    if tw_balance is not None:
        key = (models.Broker.TW_CATHAY.value, "TWD")
        per_key[key] = per_key.get(key, Decimal("0")) + tw_balance
    return [
        {
            "broker": broker,
            "currency": currency,
            "balance": balance,
            "as_of_date": effective_date,
        }
        for (broker, currency), balance in sorted(per_key.items())
    ]
