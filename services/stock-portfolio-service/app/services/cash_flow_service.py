from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256

from sqlalchemy import func
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


def get_broker_balance(db: Session, broker: str, as_of_date: date) -> Decimal:
    value = (
        db.query(func.coalesce(func.sum(models.BrokerCashFlow.amount), 0))
        .filter(models.BrokerCashFlow.broker == broker)
        .filter(models.BrokerCashFlow.date <= as_of_date)
        .scalar()
    )
    return Decimal(value or "0")


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
    return [
        {
            "broker": broker,
            "currency": currency,
            "balance": Decimal(balance or "0"),
            "as_of_date": effective_date,
        }
        for broker, currency, balance in rows
    ]
