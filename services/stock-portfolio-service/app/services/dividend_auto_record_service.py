"""Turn parsed dividend events into pending Dividend entitlements.

Taiwan-specific math:

- A flat NT$10 handling fee is applied to any cash payout. Set
  ``default_fee=Decimal("0")`` to opt out per call.
- The 二代健保 supplementary premium (NHI surtax) is auto-computed at
  2.11% when the gross cash payout exceeds NT$20,000. Users can edit
  the persisted ``tax`` field afterward (e.g. for overseas ETFs which
  are exempt).
- Stock quantities use ``floor(qty * stock_div_per_thousand / 1000)``.
  Cash and zero-cost stock transactions are posted only by explicit receipt
  confirmation, never by this fetch/record adapter.

Idempotency uses a source-independent symbol/ex-date entitlement identity.
The legacy result fields cash_inserted/stock_inserted remain false;
pending_recorded reports a newly persisted entitlement.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date as dt_date, datetime, time, timezone, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models.portfolio import Dividend, PositionSide, Transaction, TransactionType
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
    pending_recorded: bool = False


def _qty_held_on(db: Session, symbol: str, on_date: dt_date) -> Decimal:
    """Sum signed quantity (BUY=+, SELL=-) over trades strictly before on_date.

    Per Taiwan ex-dividend rule the shareholder of record on the day
    *before* the ex-dividend date receives the dividend. So we count
    every trade with ``trade_date < ex_date``.
    Only LONG-side transactions contribute to dividend-eligible quantity.
    """
    cutoff = datetime.combine(on_date, time.min, tzinfo=_TW_OFFSET)
    buy_total = (
        db.execute(
            select(func.coalesce(func.sum(Transaction.quantity), 0)).where(
                Transaction.symbol == symbol,
                Transaction.type == TransactionType.BUY,
                Transaction.position_side == PositionSide.LONG,
                Transaction.trade_date < cutoff,
            )
        ).scalar_one()
    )
    sell_total = (
        db.execute(
            select(func.coalesce(func.sum(Transaction.quantity), 0)).where(
                Transaction.symbol == symbol,
                Transaction.type == TransactionType.SELL,
                Transaction.position_side == PositionSide.LONG,
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


def auto_record_for_event(
    db: Session,
    event: HistoricalDividendEvent,
    *,
    default_fee: Decimal = DEFAULT_HANDLING_FEE,
    name: Optional[str] = None,
) -> AutoRecordResult:
    """Persist a pending entitlement; receipt confirmation owns all cash/stock posting."""
    from .dividend_reconciliation_service import _eligible_qty
    from ..models.symbol_map import SymbolMap
    trades = list(db.scalars(select(Transaction).where(Transaction.symbol == event.symbol, Transaction.market == 'TW')))
    types = list(db.scalars(select(SymbolMap.type).where(SymbolMap.symbol == event.symbol, SymbolMap.market == 'TW')))
    qty = _eligible_qty(trades, event.ex_date, types)
    if qty <= 0:
        return AutoRecordResult(cash_inserted=False, stock_inserted=False, skipped_reason="no_holding")

    from .dividend_receipt_service import persist_entitlement, entitlement_key
    previous = db.scalar(select(Dividend.id).where(Dividend.entitlement_key == entitlement_key(event.symbol, event.ex_date)))
    row, reason = persist_entitlement(db, event, qty, default_fee=default_fee)
    db.flush()
    return AutoRecordResult(cash_inserted=False, stock_inserted=False, skipped_reason=reason, pending_recorded=bool(row and previous is None))
