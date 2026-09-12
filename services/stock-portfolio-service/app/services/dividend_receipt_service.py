"""Persistent TW entitlement and atomic, explicit receipt confirmation.

Financial state and ledger writes share one transaction. No scheduled date or
legacy received_date is evidence of receipt. Confirmed values are immutable;
new source information is retained as a correction requiring human resolution.
"""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
import hashlib

from sqlalchemy import select, update, null
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..models.portfolio import Dividend, Transaction, TransactionType, PositionSide
from ..models.cash_transaction import CashTransaction, CashTxnSource, CashTxnType
from ..models.broker_account import BrokerAccount
from . import cash_account_service

TW = timezone(timedelta(hours=8))


class ReceiptConflict(ValueError):
    pass


def ex_day(value):
    if isinstance(value, datetime):
        return value.astimezone(TW).date() if value.tzinfo else value.date()
    return value


def entitlement_key(symbol, ex_date):
    return hashlib.sha256(f'TW:{symbol}:{ex_date.isoformat()}:dividend'.encode()).hexdigest()


def matching_rows(db, symbol, ex_date):
    start = datetime.combine(ex_date, time.min, TW)
    return list(db.scalars(select(Dividend).where(
        Dividend.market == 'TW', Dividend.symbol == symbol,
        Dividend.ex_dividend_date >= start,
        Dividend.ex_dividend_date < start + timedelta(days=1),
    )))


def legacy_stock_exists(db, symbol, on_date):
    start = datetime.combine(on_date, time.min, TW)
    return db.scalar(select(Transaction.id).where(Transaction.symbol == symbol,
        Transaction.market == 'TW', Transaction.type == TransactionType.BUY,
        Transaction.price == 0, Transaction.trade_date >= start,
        Transaction.trade_date < start + timedelta(days=1)).limit(1)) is not None


def _changed(row, values):
    return any(getattr(row, k) != v for k, v in values.items())


def persist_entitlement(db: Session, event, qty: Decimal, payment_date=None, *, default_fee=None):
    from .dividend_auto_record_service import compute_nhi_surtax, DEFAULT_HANDLING_FEE, MIN_DIVIDEND_AMOUNT
    cash = event.cash_dividend_per_share
    gross = (qty * cash).quantize(Decimal('.01'), rounding=ROUND_HALF_UP) if cash and cash > 0 else Decimal(0)
    fee = (DEFAULT_HANDLING_FEE if default_fee is None else default_fee) if gross > 0 else Decimal(0)
    tax = compute_nhi_surtax(gross)
    amount = max(MIN_DIVIDEND_AMOUNT, gross - fee - tax) if gross > 0 else Decimal(0)
    shares = int((qty * (event.stock_dividend_per_thousand or 0) / 1000).to_integral_value(rounding=ROUND_DOWN))
    if amount <= 0 and shares <= 0:
        return None, 'no payable amount or whole stock share'
    key = entitlement_key(event.symbol, event.ex_date)
    values = dict(amount=amount, fee=fee, tax=tax, cash_dividend_per_share=cash,
                  stock_dividend_shares=shares, quantity_at_record_date=qty,
                  payment_date=payment_date, payment_date_source=event.source if payment_date else None)
    existing_receipt = db.scalar(select(Dividend).where(Dividend.entitlement_key == key))
    if shares > 0 and (existing_receipt is None or existing_receipt.receipt_status != 'confirmed') and legacy_stock_exists(db, event.symbol, event.ex_date):
        return None, 'pre-existing stock transaction requires explicit resolution'
    matches = matching_rows(db, event.symbol, event.ex_date)
    if any(row.entitlement_key != key or row.receipt_status == 'legacy_unknown' for row in matches):
        return None, 'legacy or duplicate dividend requires explicit resolution; receipt not inferred'
    insert = sqlite_insert if db.get_bind().dialect.name == 'sqlite' else pg_insert
    db.execute(insert(Dividend).values(
        symbol=event.symbol, market='TW', currency='TWD', ex_dividend_date=datetime.combine(event.ex_date, time.min, TW),
        received_date=None, receipt_status='pending', entitlement_key=key,
        source=f'auto:{event.source}', revision=1, **values,
    ).on_conflict_do_nothing(index_elements=['entitlement_key']))
    row = db.scalar(select(Dividend).where(Dividend.entitlement_key == key).with_for_update().execution_options(populate_existing=True))
    if payment_date is None and row.payment_date is not None:
        values.update(payment_date=row.payment_date, payment_date_source=row.payment_date_source)
    if row.receipt_status == 'confirmed':
        if _changed(row, values):
            # Keep original receipt, fees, amounts and cash immutable.
            row.source_correction = {k: str(v) if v is not None else None for k, v in values.items()}
            row.review_reason = 'source correction after confirmed receipt; ledger unchanged'
        return row, row.review_reason
    if not (row.source or '').startswith('auto:') and _changed(row, values):
        correction = {k: str(v) if v is not None else None for k, v in values.items()}
        if row.source_correction != correction or row.receipt_status != 'unresolved':
            row.source_correction = correction
            row.review_reason = 'source differs from manual/CSV entitlement; explicit resolution required'
            row.receipt_status = 'unresolved'
            row.revision += 1
        return row, row.review_reason
    if row.receipt_status == 'unresolved' and row.review_reason != 'eligible position no longer present; review required':
        return row, row.review_reason
    if row.receipt_status not in {'pending', 'unresolved'}:
        return row, 'receipt state requires explicit resolution'
    if _changed(row, values) or row.receipt_status == 'unresolved':
        result = db.execute(update(Dividend).where(Dividend.id == row.id, Dividend.revision == row.revision,
            Dividend.receipt_status.in_(['pending', 'unresolved'])).values(**values, receipt_status='pending',
                revision=row.revision + 1, review_reason=None, source_correction=None).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            raise ReceiptConflict('entitlement changed concurrently; retry reconciliation')
        db.refresh(row)
    return row, None


def invalidate_entitlement(db, symbol, ex_date):
    row = db.scalar(select(Dividend).where(Dividend.entitlement_key == entitlement_key(symbol, ex_date)).with_for_update().execution_options(populate_existing=True))
    if row is None:
        return False
    if row.receipt_status == 'confirmed':
        row.source_correction = {'quantity_at_record_date': '0'}
        row.review_reason = 'position correction after confirmed receipt; ledger unchanged'
    elif row.receipt_status == 'pending':
        db.execute(update(Dividend).where(Dividend.id == row.id, Dividend.receipt_status == 'pending', Dividend.revision == row.revision)
            .values(receipt_status='unresolved', review_reason='eligible position no longer present; review required', revision=Dividend.revision + 1))
    return True


def prepare_manual(db, values):
    """Manual/CSV input is an entitlement declaration, never receipt evidence."""
    if values.get('market', 'TW') != 'TW':
        return values  # unrelated foreign-market contracts retained
    day = ex_day(values['ex_dividend_date'])
    if matching_rows(db, values['symbol'], day):
        raise ReceiptConflict('dividend already exists for symbol/ex-date; edit pending row or resolve legacy record')
    values.update(receipt_status='pending', entitlement_key=entitlement_key(values['symbol'], day), received_date=null())
    return values


def _unresolved(db, row, reason):
    # No receipt claim or posting has happened. Persist the review state so the
    # ambiguity survives reload/restart, without modifying any existing cash.
    changed = db.execute(update(Dividend).where(Dividend.id == row.id,
        Dividend.receipt_status == 'pending', Dividend.revision == row.revision)
        .values(receipt_status='unresolved', review_reason=reason, revision=Dividend.revision + 1)
        .execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        db.rollback()
        raise ReceiptConflict('entitlement changed concurrently; reload')
    db.commit()
    raise ReceiptConflict(reason)


def confirm_receipt(db: Session, dividend_id: int, receipt_date: date, account_id: int, revision: int):
    try:
        row = db.scalar(select(Dividend).where(Dividend.id == dividend_id).with_for_update().execution_options(populate_existing=True))
        if row is None:
            raise ReceiptConflict('dividend not found')
        if row.market != 'TW' or row.currency != 'TWD':
            raise ReceiptConflict('receipt confirmation supports TW/TWD only')
        if row.receipt_status == 'confirmed':
            if row.receipt_date != receipt_date or row.receipt_account_id != account_id:
                raise ReceiptConflict('receipt already confirmed with different date or account')
            db.commit()
            return row
        if row.receipt_status != 'pending':
            raise ReceiptConflict('legacy or unresolved receipt requires explicit resolution')
        if row.review_reason:
            raise ReceiptConflict(row.review_reason)
        if (row.source or '').startswith('auto:'):
            from .dividend_reconciliation_service import _eligible_qty
            from ..models.symbol_map import SymbolMap
            trades = list(db.scalars(select(Transaction).where(Transaction.symbol == row.symbol, Transaction.market == 'TW')))
            types = list(db.scalars(select(SymbolMap.type).where(SymbolMap.symbol == row.symbol, SymbolMap.market == 'TW')))
            if _eligible_qty(trades, ex_day(row.ex_dividend_date), types) != row.quantity_at_record_date:
                raise ReceiptConflict('eligible position changed; reconcile before confirmation')
        if row.revision != revision:
            raise ReceiptConflict('entitlement changed; reload before confirmation')
        if receipt_date < ex_day(row.ex_dividend_date) or receipt_date > datetime.now(TW).date():
            raise ReceiptConflict('receipt date must be between ex-date and today')
        peers = matching_rows(db, row.symbol, ex_day(row.ex_dividend_date))
        if len(peers) != 1:
            _unresolved(db, row, 'duplicate or legacy event requires explicit resolution')
        if db.scalar(select(CashTransaction.id).where(CashTransaction.related_dividend_id == row.id).limit(1)):
            _unresolved(db, row, 'pre-existing cash leg requires explicit resolution; cannot credit again')
        if row.stock_dividend_shares > 0 and (legacy_stock_exists(db, row.symbol, ex_day(row.ex_dividend_date)) or legacy_stock_exists(db, row.symbol, receipt_date)):
            _unresolved(db, row, 'pre-existing stock transaction requires explicit resolution')
        if row.amount > 0 and db.scalar(select(CashTransaction.id).where(CashTransaction.account_id == account_id,
            CashTransaction.type == CashTxnType.DIVIDEND_CASH, CashTransaction.txn_date == receipt_date,
            CashTransaction.amount == row.amount, CashTransaction.related_dividend_id.is_(None)).limit(1)):
            _unresolved(db, row, 'unlinked matching cash dividend requires explicit resolution; cannot credit again')
        account = db.get(BrokerAccount, account_id)
        if account is None or not account.is_active or account.currency != 'TWD':
            raise ReceiptConflict('an active TWD account is required')
        claimed = db.execute(update(Dividend).where(Dividend.id == row.id, Dividend.receipt_status == 'pending',
            Dividend.revision == revision).values(receipt_status='confirmed', receipt_date=receipt_date,
                receipt_account_id=account_id, receipt_confirmed_at=datetime.now(timezone.utc),
                received_date=datetime.combine(receipt_date, time.min, TW), revision=revision + 1)
            .execution_options(synchronize_session=False))
        if claimed.rowcount != 1:
            raise ReceiptConflict('receipt changed concurrently; reload and retry')
        db.refresh(row)
        if row.amount > 0:
            cash_account_service.sync_dividend_cash_leg(db, row, account_id, CashTxnSource.AUTO_DERIVE)
        if row.stock_dividend_shares > 0:
            db.add(Transaction(symbol=row.symbol, market='TW', type=TransactionType.BUY,
                position_side=PositionSide.LONG, quantity=row.stock_dividend_shares, price=0,
                trade_date=datetime.combine(receipt_date, time.min, TW), fee=0, tax=0,
                is_day_trade=False, import_fingerprint=hashlib.sha256(f'receipt-stock:{row.entitlement_key}'.encode()).hexdigest()))
        db.flush()
        db.commit()
        db.refresh(row)
        return row
    except Exception:
        db.rollback()
        raise
