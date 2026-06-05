from __future__ import annotations

import hashlib
import logging
import os
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import Select, delete, func, select
from sqlalchemy.orm import Session

from ..models.broker_account import BrokerAccount, BrokerEnum
from ..models.cash_transaction import CashTransaction, CashTxnSource, CashTxnType
from ..models.portfolio import Transaction, TransactionType
from ..schemas.cash_account import (
    AccountsListOut,
    BalancePoint,
    BrokerAccountCreate,
    BrokerAccountOut,
    BrokerAccountPatch,
    CashTransactionCreate,
    CashTransactionOut,
)
from . import fx_rate_service

logger = logging.getLogger(__name__)

OUTFLOW_TYPES = {
    CashTxnType.WITHDRAW,
    CashTxnType.BUY_SETTLE,
    CashTxnType.FEE,
    CashTxnType.TAX,
    CashTxnType.MARGIN_INTEREST,
    CashTxnType.WIRE_FEE,
}

AUTO_LEG_NAMES = {"settle", "fee", "tax"}

_ACCOUNT_SORT_FIELDS = {
    "txn_date": CashTransaction.txn_date,
    "created_at": CashTransaction.created_at,
    "amount": CashTransaction.amount,
    "type": CashTransaction.type,
}

_CASH_LEG_SORT_RANK = {
    CashTxnType.BUY_SETTLE: 0,
    CashTxnType.SELL_SETTLE: 0,
    CashTxnType.FEE: 1,
    CashTxnType.TAX: 2,
}


class CashAccountNotFound(ValueError):
    pass


class CashAccountAmbiguous(ValueError):
    pass


def _normalize_currency(value: str) -> str:
    return value.strip().upper()


def _as_decimal(value: Decimal | int | str | None) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _cash_leg_enabled() -> bool:
    return os.getenv("CASH_LEG_ENABLED", "false").lower() in {"true", "1", "yes"}


cash_leg_enabled = _cash_leg_enabled


def compute_manual_fingerprint(
    account_id: int,
    txn_date: date,
    type_: CashTxnType,
    amount: Decimal,
    note: str | None,
) -> str:
    canonical = f"manual|{account_id}|{txn_date.isoformat()}|{type_.value}|{amount}|{note or ''}"
    return _hash(canonical)


def compute_backfill_fingerprint(source_table: str, source_id: int, leg: str) -> str:
    return _hash(f"backfill|{source_table}|{source_id}|{leg}")


def compute_auto_fingerprint(transaction_id: int, leg: str) -> str:
    if leg not in AUTO_LEG_NAMES:
        raise ValueError("unknown auto cash leg")
    return compute_backfill_fingerprint("transactions", transaction_id, leg)


def compute_csv_fingerprint(
    broker: str,
    transaction_fingerprint: str,
    leg: str,
) -> str:
    return _hash(f"csv|{broker}|{transaction_fingerprint}|{leg}")


def normalize_amount(type_: CashTxnType, amount: Decimal) -> Decimal:
    if type_ == CashTxnType.FX_CONVERT:
        return amount

    if amount == 0:
        raise ValueError("amount must be non-zero")

    if type_ not in OUTFLOW_TYPES and amount < 0:
        raise ValueError("amount sign does not match transaction type")

    absolute = abs(amount)
    if type_ in OUTFLOW_TYPES:
        return -absolute
    return absolute


def resolve_default_cathay_twd_account(session: Session) -> BrokerAccount:
    accounts = session.execute(
        select(BrokerAccount).where(
            BrokerAccount.broker == BrokerEnum.CATHAY,
            BrokerAccount.currency == "TWD",
            BrokerAccount.is_active.is_(True),
        )
    ).scalars().all()
    if len(accounts) > 1:
        raise CashAccountAmbiguous("multiple Cathay TWD accounts active — deactivate duplicates first")
    if not accounts:
        raise CashAccountNotFound(
            "Cathay TWD account not found — create one via POST /api/portfolio/accounts "
            "before importing or editing TWD transactions"
        )
    return accounts[0]


def _transaction_trade_date(transaction: Transaction) -> date:
    return transaction.trade_date.date()


def _dividend_ex_date(dividend: object) -> date:
    value = dividend.ex_dividend_date
    return value.date() if hasattr(value, "date") else value


def _transaction_amount(value: Decimal | int | str | None) -> Decimal:
    return _as_decimal(value)


def _desired_transaction_cash_legs(
    transaction: Transaction,
    currency: str,
) -> dict[str, dict[str, object]]:
    txn_date = _transaction_trade_date(transaction)
    gross_amount = _transaction_amount(transaction.quantity) * _transaction_amount(transaction.price)
    if transaction.type == TransactionType.SELL:
        settle_amount = gross_amount
        settle_type = CashTxnType.SELL_SETTLE
    else:
        settle_amount = -gross_amount
        settle_type = CashTxnType.BUY_SETTLE

    desired: dict[str, dict[str, object]] = {
        "settle": {
            "txn_date": txn_date,
            "type": settle_type,
            "amount": settle_amount,
            "currency": currency,
        }
    }
    fee = _transaction_amount(transaction.fee)
    if fee > 0:
        desired["fee"] = {
            "txn_date": txn_date,
            "type": CashTxnType.FEE,
            "amount": -fee,
            "currency": currency,
        }
    tax = _transaction_amount(transaction.tax)
    if tax > 0:
        desired["tax"] = {
            "txn_date": txn_date,
            "type": CashTxnType.TAX,
            "amount": -tax,
            "currency": currency,
        }
    return desired


def _leg_name_for_fingerprint(transaction_id: int, fingerprint: str) -> str | None:
    for leg_name in AUTO_LEG_NAMES:
        if fingerprint == compute_auto_fingerprint(transaction_id, leg_name):
            return leg_name
    return None


def _cash_leg_fingerprint(
    transaction: Transaction,
    source: CashTxnSource,
    leg_name: str,
) -> str:
    if source == CashTxnSource.CSV_IMPORT:
        if not transaction.import_fingerprint:
            raise ValueError("csv cash leg requires transaction import_fingerprint")
        return compute_csv_fingerprint("cathay", transaction.import_fingerprint, leg_name)
    return compute_auto_fingerprint(transaction.id, leg_name)


def _leg_name_for_cash_row(row: CashTransaction, transaction: Transaction) -> str | None:
    leg_name = _leg_name_for_fingerprint(transaction.id, row.import_fingerprint)
    if leg_name is not None:
        return leg_name
    if transaction.import_fingerprint:
        for candidate in AUTO_LEG_NAMES:
            if row.import_fingerprint == compute_csv_fingerprint(
                "cathay",
                transaction.import_fingerprint,
                candidate,
            ):
                return candidate
    type_to_leg = {
        CashTxnType.BUY_SETTLE: "settle",
        CashTxnType.SELL_SETTLE: "settle",
        CashTxnType.FEE: "fee",
        CashTxnType.TAX: "tax",
    }
    if row.source == CashTxnSource.CSV_IMPORT:
        return type_to_leg.get(row.type)
    return None


def _update_cash_leg_if_changed(
    row: CashTransaction,
    *,
    account_id: int,
    source: CashTxnSource,
    fields: dict[str, object],
) -> None:
    desired = {
        "account_id": account_id,
        "txn_date": fields["txn_date"],
        "type": fields["type"],
        "amount": fields["amount"],
        "currency": fields["currency"],
        "source": source,
    }
    for field, value in desired.items():
        if getattr(row, field) != value:
            setattr(row, field, value)


def sync_transaction_cash_legs(
    session: Session,
    transaction: Transaction,
    account_id: int,
    source: CashTxnSource,
) -> list[CashTransaction]:
    account = session.get(BrokerAccount, account_id)
    if account is None:
        raise ValueError("account not found")

    currency = _normalize_currency(account.currency)
    desired = _desired_transaction_cash_legs(transaction, currency)
    existing_rows = session.execute(
        select(CashTransaction).where(CashTransaction.related_transaction_id == transaction.id)
    ).scalars().all()

    existing_by_leg: dict[str, CashTransaction] = {}
    rows_to_delete: list[CashTransaction] = []
    for row in existing_rows:
        leg_name = _leg_name_for_cash_row(row, transaction)
        if leg_name is None:
            rows_to_delete.append(row)
            continue
        existing_by_leg[leg_name] = row

    synced_rows: list[CashTransaction] = []
    for leg_name, fields in desired.items():
        row = existing_by_leg.get(leg_name)
        if row is None:
            row = CashTransaction(
                account_id=account_id,
                txn_date=fields["txn_date"],
                type=fields["type"],
                amount=fields["amount"],
                currency=fields["currency"],
                related_transaction_id=transaction.id,
                source=source,
                import_fingerprint=_cash_leg_fingerprint(transaction, source, leg_name),
            )
            session.add(row)
        else:
            _update_cash_leg_if_changed(
                row,
                account_id=account_id,
                source=source,
                fields=fields,
            )
            desired_fingerprint = _cash_leg_fingerprint(transaction, source, leg_name)
            if row.import_fingerprint != desired_fingerprint:
                row.import_fingerprint = desired_fingerprint
        synced_rows.append(row)

    for leg_name, row in existing_by_leg.items():
        if leg_name not in desired:
            rows_to_delete.append(row)
    for row in rows_to_delete:
        session.delete(row)

    return synced_rows


def delete_transaction_cash_legs(session: Session, transaction_id: int) -> int:
    result = session.execute(
        delete(CashTransaction).where(CashTransaction.related_transaction_id == transaction_id)
    )
    return int(result.rowcount or 0)


def delete_auto_derived_transaction_cash_legs(session: Session, transaction_id: int) -> int:
    result = session.execute(
        delete(CashTransaction).where(
            CashTransaction.related_transaction_id == transaction_id,
            CashTransaction.source == CashTxnSource.AUTO_DERIVE,
        )
    )
    return int(result.rowcount or 0)


def sync_dividend_cash_leg(
    session: Session,
    dividend: object,
    account_id: int,
    source: CashTxnSource,
) -> CashTransaction | None:
    account = session.get(BrokerAccount, account_id)
    if account is None:
        raise ValueError("account not found")

    leg_fp = compute_backfill_fingerprint("dividends", dividend.id, "dividend_cash")
    txn_date = _dividend_ex_date(dividend)
    amount = _as_decimal(dividend.amount)
    currency = _normalize_currency(account.currency)
    row = session.execute(
        select(CashTransaction).where(
            CashTransaction.related_dividend_id == dividend.id,
            CashTransaction.import_fingerprint == leg_fp,
        )
    ).scalar_one_or_none()
    if row is None:
        row = CashTransaction(
            account_id=account_id,
            txn_date=txn_date,
            type=CashTxnType.DIVIDEND_CASH,
            amount=amount,
            currency=currency,
            related_transaction_id=None,
            related_dividend_id=dividend.id,
            source=source,
            import_fingerprint=leg_fp,
        )
        session.add(row)
        return row

    desired = {
        "amount": amount,
        "txn_date": txn_date,
        "currency": currency,
        "source": source,
    }
    for field, value in desired.items():
        if getattr(row, field) != value:
            setattr(row, field, value)
    return row


def delete_dividend_cash_leg(session: Session, dividend_id: int) -> int:
    result = session.execute(
        delete(CashTransaction).where(CashTransaction.related_dividend_id == dividend_id)
    )
    return int(result.rowcount or 0)


def delete_auto_derived_dividend_cash_leg(session: Session, dividend_id: int) -> int:
    result = session.execute(
        delete(CashTransaction).where(
            CashTransaction.related_dividend_id == dividend_id,
            CashTransaction.source == CashTxnSource.AUTO_DERIVE,
        )
    )
    return int(result.rowcount or 0)


def get_balance(
    session: Session,
    account_id: int,
    asof: date | None = None,
) -> Decimal:
    account = session.get(BrokerAccount, account_id)
    if account is None:
        raise ValueError("account not found")

    asof_date = asof or date.today()
    txn_sum = session.execute(
        select(func.sum(CashTransaction.amount)).where(
            CashTransaction.account_id == account_id,
            CashTransaction.txn_date <= asof_date,
        )
    ).scalar_one()
    return _as_decimal(account.opening_balance) + _as_decimal(txn_sum)


def get_balance_history(
    session: Session,
    account_id: int,
    date_from: date,
    date_to: date,
) -> list[BalancePoint]:
    if date_from > date_to:
        raise ValueError("date_from must be <= date_to")

    account = session.get(BrokerAccount, account_id)
    if account is None:
        raise ValueError("account not found")

    rows = session.execute(
        select(CashTransaction.txn_date, CashTransaction.amount)
        .where(
            CashTransaction.account_id == account_id,
            CashTransaction.txn_date <= date_to,
        )
        .order_by(CashTransaction.txn_date.asc())
    ).all()

    balance = _as_decimal(account.opening_balance)
    daily_delta: dict[date, Decimal] = {}
    for txn_date, amount in rows:
        delta = _as_decimal(amount)
        if txn_date < date_from:
            balance += delta
            continue
        daily_delta[txn_date] = daily_delta.get(txn_date, Decimal("0")) + delta

    points: list[BalancePoint] = []
    current = date_from
    while current <= date_to:
        balance += daily_delta.get(current, Decimal("0"))
        points.append(BalancePoint(date=current, balance=balance))
        current += timedelta(days=1)
    return points


def get_total_balance_in(
    session: Session,
    target_currency: str,
    asof: date | None = None,
    include_inactive: bool = False,
) -> tuple[Decimal, list[str]]:
    target = _normalize_currency(target_currency)
    asof_date = asof or date.today()
    stmt = select(BrokerAccount)
    if not include_inactive:
        stmt = stmt.where(BrokerAccount.is_active.is_(True))

    total = Decimal("0")
    skipped_currencies: list[str] = []
    for account in session.execute(stmt.order_by(BrokerAccount.id.asc())).scalars():
        native_balance = get_balance(session, account.id, asof_date)
        source_currency = _normalize_currency(account.currency)
        if source_currency == target:
            total += native_balance
            continue

        rate = fx_rate_service.get_rate(session, asof_date, source_currency, target)
        if rate is None:
            if source_currency not in skipped_currencies:
                skipped_currencies.append(source_currency)
            continue
        total += native_balance * rate
    return total, skipped_currencies


def create_manual_cash_transaction(
    session: Session,
    account_id: int,
    payload: CashTransactionCreate,
) -> CashTransaction:
    account = session.get(BrokerAccount, account_id)
    if account is None:
        raise ValueError("account not found")

    account_currency = _normalize_currency(account.currency)
    payload_currency = _normalize_currency(payload.currency)
    if payload.type != CashTxnType.FX_CONVERT and payload_currency != account_currency:
        raise ValueError("currency must match account")

    amount = normalize_amount(payload.type, payload.amount)
    row = CashTransaction(
        account_id=account.id,
        txn_date=payload.txn_date,
        type=payload.type,
        amount=amount,
        currency=account_currency,
        note=payload.note,
        source=CashTxnSource.MANUAL,
        import_fingerprint=compute_manual_fingerprint(
            account.id,
            payload.txn_date,
            payload.type,
            amount,
            payload.note,
        ),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    from . import portfolio_snapshot_service

    today = portfolio_snapshot_service._today_tw()
    _refresh_snapshot_cash_range(session, min(row.txn_date, today), today)
    return row


def _refresh_snapshot_cash_range(
    session: Session,
    start_date: date,
    end_date: date,
) -> None:
    from . import portfolio_snapshot_service

    try:
        portfolio_snapshot_service.refresh_snapshot_cash_range(
            session,
            start_date,
            end_date,
        )
    except Exception:
        session.rollback()
        logger.warning(
            "cash_account_service: failed to refresh cash snapshot range",
            extra={
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
            exc_info=True,
        )


def delete_manual_cash_transaction(session: Session, account_id: int, txn_id: int) -> int:
    row = session.execute(
        select(CashTransaction).where(CashTransaction.id == txn_id)
    ).scalar_one_or_none()
    if row is None or row.account_id != account_id:
        raise LookupError
    if row.source != CashTxnSource.MANUAL:
        raise ValueError("not_manual")

    deleted_id = int(row.id)
    deleted_txn_date = row.txn_date
    session.delete(row)
    session.commit()
    from . import portfolio_snapshot_service

    today = portfolio_snapshot_service._today_tw()
    _refresh_snapshot_cash_range(session, min(deleted_txn_date, today), today)
    return deleted_id


def create_account(session: Session, payload: BrokerAccountCreate) -> BrokerAccount:
    account = BrokerAccount(
        broker=payload.broker,
        nickname=payload.nickname,
        currency=_normalize_currency(payload.currency),
        opening_balance=payload.opening_balance,
        opening_date=payload.opening_date,
        is_active=payload.is_active,
    )
    session.add(account)
    session.commit()
    session.refresh(account)
    return account


def patch_account(
    session: Session,
    account_id: int,
    payload: BrokerAccountPatch,
) -> BrokerAccount:
    account = session.get(BrokerAccount, account_id)
    if account is None:
        raise ValueError("account not found")

    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(account, field, value)
    session.commit()
    session.refresh(account)
    return account


def _parse_sort(sort: str) -> tuple[str, str]:
    parts = sort.split(":", 1)
    field = parts[0]
    direction = parts[1].lower() if len(parts) == 2 else "asc"
    if field not in _ACCOUNT_SORT_FIELDS or direction not in {"asc", "desc"}:
        raise ValueError("invalid sort")
    return field, direction


def _cash_transaction_filters(
    account_id: int,
    date_from: date | None,
    date_to: date | None,
    type_: CashTxnType | None,
) -> list[object]:
    filters: list[object] = [CashTransaction.account_id == account_id]
    if date_from is not None:
        filters.append(CashTransaction.txn_date >= date_from)
    if date_to is not None:
        filters.append(CashTransaction.txn_date <= date_to)
    if type_ is not None:
        filters.append(CashTransaction.type == type_)
    return filters


def _cash_transaction_out(row: CashTransaction) -> CashTransactionOut:
    return CashTransactionOut.model_validate(row)


def _ordered_cash_legs(rows: list[CashTransaction]) -> list[CashTransaction]:
    return sorted(
        rows,
        key=lambda row: (
            _CASH_LEG_SORT_RANK.get(row.type, 99),
            row.txn_date,
            row.id or 0,
        ),
    )


def _merge_legs_into_groups(rows: list[CashTransaction]) -> list[CashTransactionOut]:
    """Collapse rows sharing related_transaction_id into synthetic trade groups."""
    grouped: dict[int, list[CashTransaction]] = {}
    standalone: list[CashTransactionOut] = []
    groups: list[CashTransactionOut] = []

    for row in rows:
        if row.related_transaction_id is None:
            standalone.append(_cash_transaction_out(row))
        else:
            grouped.setdefault(row.related_transaction_id, []).append(row)

    for related_transaction_id, legs in grouped.items():
        ordered_legs = _ordered_cash_legs(legs)
        settle_leg = next(
            (
                leg
                for leg in ordered_legs
                if leg.type in {CashTxnType.BUY_SETTLE, CashTxnType.SELL_SETTLE}
            ),
            ordered_legs[0],
        )
        first_leg = legs[0]
        child_legs = [_cash_transaction_out(leg) for leg in ordered_legs]
        groups.append(
            CashTransactionOut.model_validate(
                {
                    "id": -related_transaction_id,
                    "account_id": first_leg.account_id,
                    "txn_date": settle_leg.txn_date,
                    "type": "trade",
                    "amount": sum((leg.amount for leg in legs), Decimal("0")),
                    "currency": first_leg.currency,
                    "note": None,
                    "related_transaction_id": related_transaction_id,
                    "related_dividend_id": None,
                    "source": first_leg.source,
                    "import_fingerprint": f"merged:{related_transaction_id}",
                    "created_at": settle_leg.created_at,
                    "child_legs": child_legs,
                }
            )
        )

    return groups + standalone


def _cash_virtual_sort_value(row: CashTransactionOut, field: str) -> object:
    if field == "type":
        return row.type.value if isinstance(row.type, CashTxnType) else row.type
    return getattr(row, field)


def list_cash_transactions(
    session: Session,
    account_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    type_: CashTxnType | None = None,
    sort: str = "txn_date:desc",
    offset: int = 0,
    limit: int = 25,
    merge_related: bool = False,
) -> tuple[list[CashTransaction | CashTransactionOut], int]:
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ValueError("date_from must be <= date_to")

    field, direction = _parse_sort(sort)
    filters = _cash_transaction_filters(account_id, date_from, date_to, type_)

    if merge_related:
        rows = session.execute(
            select(CashTransaction).where(*filters).order_by(CashTransaction.id.asc())
        ).scalars().all()
        virtual_rows = _merge_legs_into_groups(rows)
        reverse = direction == "desc"
        virtual_rows.sort(
            key=lambda row: (_cash_virtual_sort_value(row, field), row.id),
            reverse=reverse,
        )
        total = len(virtual_rows)
        return virtual_rows[offset : offset + limit], total

    total = session.execute(
        select(func.count()).select_from(CashTransaction).where(*filters)
    ).scalar_one()

    sort_column = _ACCOUNT_SORT_FIELDS[field]
    order_by = sort_column.desc() if direction == "desc" else sort_column.asc()
    rows = session.execute(
        select(CashTransaction)
        .where(*filters)
        .order_by(order_by, CashTransaction.id.desc())
        .offset(offset)
        .limit(limit)
    ).scalars().all()
    return rows, total


def list_accounts(
    session: Session,
    include_inactive: bool = False,
    in_currency: str | None = None,
    asof: date | None = None,
) -> AccountsListOut:
    asof_date = asof or date.today()
    target_currency = _normalize_currency(in_currency) if in_currency else None
    stmt: Select[tuple[BrokerAccount]] = select(BrokerAccount)
    if not include_inactive:
        stmt = stmt.where(BrokerAccount.is_active.is_(True))

    items: list[BrokerAccountOut] = []
    total_target_balance = Decimal("0") if target_currency else None
    skipped_currencies: list[str] = []

    for account in session.execute(stmt.order_by(BrokerAccount.id.asc())).scalars():
        native_balance = get_balance(session, account.id, asof_date)
        target_balance: Decimal | None = None
        if target_currency is not None:
            account_currency = _normalize_currency(account.currency)
            if account_currency == target_currency:
                target_balance = native_balance
            else:
                rate = fx_rate_service.get_rate(
                    session,
                    asof_date,
                    account_currency,
                    target_currency,
                )
                if rate is None:
                    if account_currency not in skipped_currencies:
                        skipped_currencies.append(account_currency)
                else:
                    target_balance = native_balance * rate
            if target_balance is not None:
                total_target_balance = (total_target_balance or Decimal("0")) + target_balance

        items.append(
            BrokerAccountOut.model_validate(
                {
                    "id": account.id,
                    "broker": account.broker,
                    "nickname": account.nickname,
                    "currency": _normalize_currency(account.currency),
                    "opening_balance": account.opening_balance,
                    "opening_date": account.opening_date,
                    "is_active": account.is_active,
                    "created_at": account.created_at,
                    "native_balance": native_balance,
                    "target_balance": target_balance,
                    "target_currency": target_currency,
                }
            )
        )

    return AccountsListOut(
        items=items,
        target_currency=target_currency,
        total_target_balance=total_target_balance,
        skipped_currencies=skipped_currencies,
    )
