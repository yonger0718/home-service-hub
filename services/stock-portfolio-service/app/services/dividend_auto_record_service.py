"""Turn parsed dividend events into persisted Dividend + Transaction rows.

Taiwan-specific math:

- A flat NT$10 handling fee is applied to any cash payout. Set
  ``default_fee=Decimal("0")`` to opt out per call.
- The 二代健保 supplementary premium (NHI surtax) is auto-computed at
  2.11% when the gross cash payout exceeds NT$20,000. Users can edit
  the persisted ``tax`` field afterward (e.g. for overseas ETFs which
  are exempt).
- Stock dividends are converted to a zero-cost BUY transaction whose
  quantity is ``floor(qty * stock_div_per_thousand / 1000)``. Stock
  payouts below one whole share are skipped.

Idempotency: both inserts use deterministic SHA256
``import_fingerprint`` values keyed by source / symbol / ex-date so
repeated runs are no-ops.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import date as dt_date, datetime, time, timezone, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models.portfolio import Dividend, Transaction, TransactionType
from .dividend_history_service import HistoricalDividendEvent

logger = logging.getLogger(__name__)

NHI_SURTAX_RATE = Decimal("0.0211")
NHI_SURTAX_THRESHOLD = Decimal("20000")
DEFAULT_HANDLING_FEE = Decimal("10")
MIN_DIVIDEND_AMOUNT = Decimal("0.01")  # respects ck_dividends_amount_positive

_TW_OFFSET = timezone(timedelta(hours=8))


@dataclass(frozen=True, slots=True)
class AutoRecordResult:
    cash_inserted: bool
    stock_inserted: bool
    skipped_reason: Optional[str]


def _qty_held_on(db: Session, symbol: str, on_date: dt_date) -> Decimal:
    """Sum signed quantity (BUY=+, SELL=-) over trades strictly before on_date.

    Per Taiwan ex-dividend rule the shareholder of record on the day
    *before* the ex-dividend date receives the dividend. So we count
    every trade with ``trade_date < ex_date``.
    """
    cutoff = datetime.combine(on_date, time.min, tzinfo=_TW_OFFSET)
    buy_total = (
        db.execute(
            select(func.coalesce(func.sum(Transaction.quantity), 0)).where(
                Transaction.symbol == symbol,
                Transaction.type == TransactionType.BUY,
                Transaction.trade_date < cutoff,
            )
        ).scalar_one()
    )
    sell_total = (
        db.execute(
            select(func.coalesce(func.sum(Transaction.quantity), 0)).where(
                Transaction.symbol == symbol,
                Transaction.type == TransactionType.SELL,
                Transaction.trade_date < cutoff,
            )
        ).scalar_one()
    )
    return Decimal(str(buy_total)) - Decimal(str(sell_total))


def compute_nhi_surtax(gross: Decimal) -> Decimal:
    """二代健保 supplementary premium for a single dividend payout."""
    if gross <= NHI_SURTAX_THRESHOLD:
        return Decimal("0")
    return (gross * NHI_SURTAX_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _make_fingerprint(prefix: str, source: str, symbol: str, ex_date: dt_date, leg: str) -> str:
    raw = f"{prefix}:{source}:{symbol}:{ex_date.isoformat()}:{leg}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _ex_date_dt(ex_date: dt_date) -> datetime:
    return datetime.combine(ex_date, time(0, 0), tzinfo=_TW_OFFSET)


def auto_record_for_event(
    db: Session,
    event: HistoricalDividendEvent,
    *,
    default_fee: Decimal = DEFAULT_HANDLING_FEE,
    name: Optional[str] = None,
) -> AutoRecordResult:
    """Insert cash dividend + stock-dividend rows for one event.

    Both inserts are idempotent — repeat invocations against the same
    DB are no-ops.
    """
    qty = _qty_held_on(db, event.symbol, event.ex_date)
    if qty <= 0:
        return AutoRecordResult(cash_inserted=False, stock_inserted=False, skipped_reason="no_holding")

    cash_inserted = False
    stock_inserted = False
    cash_per_share = event.cash_dividend_per_share
    if cash_per_share is not None and cash_per_share > 0:
        cash_inserted = _insert_cash(
            db,
            symbol=event.symbol,
            ex_date=event.ex_date,
            qty=qty,
            cash_per_share=cash_per_share,
            source=event.source,
            default_fee=default_fee,
        )

    stock_per_thousand = event.stock_dividend_per_thousand
    if stock_per_thousand is not None and stock_per_thousand > 0:
        extra_shares = int((qty * stock_per_thousand / Decimal(1000)).to_integral_value(rounding=ROUND_DOWN))
        if extra_shares > 0:
            stock_inserted = _insert_stock(
                db,
                symbol=event.symbol,
                ex_date=event.ex_date,
                shares=extra_shares,
                source=event.source,
                name=name,
            )

    return AutoRecordResult(cash_inserted=cash_inserted, stock_inserted=stock_inserted, skipped_reason=None)


def _insert_cash(
    db: Session,
    *,
    symbol: str,
    ex_date: dt_date,
    qty: Decimal,
    cash_per_share: Decimal,
    source: str,
    default_fee: Decimal,
) -> bool:
    gross = (qty * cash_per_share).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    fee = default_fee if gross > 0 else Decimal("0")
    tax = compute_nhi_surtax(gross)
    amount = gross - fee - tax
    if amount < MIN_DIVIDEND_AMOUNT:
        amount = MIN_DIVIDEND_AMOUNT
    fp = _make_fingerprint("auto", source, symbol, ex_date, "cash")
    if db.execute(
        select(Dividend.id).where(Dividend.import_fingerprint == fp)
    ).first() is not None:
        return False
    sp = db.begin_nested()
    try:
        db.add(
            Dividend(
                symbol=symbol,
                amount=amount,
                ex_dividend_date=_ex_date_dt(ex_date),
                fee=fee,
                tax=tax,
                cash_dividend_per_share=cash_per_share,
                stock_dividend_shares=0,
                source=f"auto:{source}",
                quantity_at_record_date=qty,
                import_fingerprint=fp,
            )
        )
        db.flush()
    except IntegrityError:
        sp.rollback()
        return False
    return True


def _insert_stock(
    db: Session,
    *,
    symbol: str,
    ex_date: dt_date,
    shares: int,
    source: str,
    name: Optional[str],
) -> bool:
    fp = _make_fingerprint("auto-stk-div", source, symbol, ex_date, "stk")
    if db.execute(
        select(Transaction.id).where(Transaction.import_fingerprint == fp)
    ).first() is not None:
        return False
    sp = db.begin_nested()
    try:
        db.add(
            Transaction(
                symbol=symbol,
                name=name,
                type=TransactionType.BUY,
                quantity=shares,
                price=Decimal("0"),
                trade_date=_ex_date_dt(ex_date),
                fee=Decimal("0"),
                tax=Decimal("0"),
                is_day_trade=False,
                import_fingerprint=fp,
            )
        )
        db.flush()
    except IntegrityError:
        sp.rollback()
        return False
    return True
