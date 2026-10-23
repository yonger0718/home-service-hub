"""Edit the 匯費 (fee) and 補充保費 (tax) of a TW/TWD dividend after the fact.

The net amount is recomputed from the gross; a confirmed receipt's single
AUTO_DERIVE cash leg follows with an amount-only update (or is deleted when
the net becomes zero). The leg's date, source, account and fingerprint are
never moved, so `sync_dividend_cash_leg` is deliberately not used here.
"""
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models.cash_transaction import CashTransaction, CashTxnSource
from ..models.portfolio import Dividend
from . import cash_account_service, portfolio_snapshot_service
from .dividend_receipt_service import ReceiptConflict

_CENT = Decimal("0.01")


class DividendNotFound(LookupError):
    pass


class DeductionError(ValueError):
    pass


def _cents(value) -> Decimal:
    if isinstance(value, float):
        raise TypeError('money must be Decimal, not float')
    return Decimal(value).quantize(_CENT, rounding=ROUND_HALF_UP)


def update_deductions(db: Session, dividend_id: int, fee: Decimal, tax: Decimal, revision: int) -> Dividend:
    try:
        # (a) lock the row and guard its kind and state
        row = db.scalar(select(Dividend).where(Dividend.id == dividend_id)
                        .with_for_update().execution_options(populate_existing=True))
        if row is None:
            raise DividendNotFound('dividend not found')
        if row.market != 'TW' or row.currency != 'TWD':
            raise ReceiptConflict('deductions editable for TW/TWD only')
        if row.receipt_status not in ('pending', 'confirmed'):
            raise ReceiptConflict('legacy or unresolved dividend requires explicit resolution')
        confirmed = row.receipt_status == 'confirmed'

        # (b) recompute the net from the gross; no minimum floor
        if fee < 0 or tax < 0:
            raise DeductionError('fee and tax must be non-negative')
        fee, tax = _cents(fee), _cents(tax)
        per_share = row.cash_dividend_per_share
        if row.quantity_at_record_date is not None and per_share is not None and per_share > 0:
            gross = _cents(row.quantity_at_record_date * per_share)
        else:
            gross = Decimal(row.amount) + Decimal(row.fee or 0) + Decimal(row.tax or 0)
        new_amount = _cents(gross - fee - tax)
        if new_amount < 0:
            raise DeductionError('deductions exceed the gross dividend')
        if new_amount == 0 and row.stock_dividend_shares == 0:
            raise DeductionError('deductions leave no payable amount')

        # (c) leg gate before any write (confirmed rows, both nets)
        leg = None
        if confirmed:
            legs = list(db.scalars(select(CashTransaction)
                                   .where(CashTransaction.related_dividend_id == row.id)
                                   .with_for_update()))
            if not legs:
                raise ReceiptConflict('no posted cash leg for this receipt; reload')
            if len(legs) > 1:
                raise ReceiptConflict('multiple cash legs for this dividend; manual review required')
            leg = legs[0]
            if leg.source != CashTxnSource.AUTO_DERIVE:
                raise ReceiptConflict('cash leg was imported or entered by hand; edit the cash transaction instead')

        # (d) compare-and-set on the client's revision
        claimed = db.execute(update(Dividend)
                             .where(Dividend.id == row.id, Dividend.revision == revision,
                                    Dividend.receipt_status == row.receipt_status)
                             .values(fee=fee, tax=tax, amount=new_amount, revision=Dividend.revision + 1)
                             .execution_options(synchronize_session=False))
        if claimed.rowcount != 1:
            raise ReceiptConflict('entitlement changed; reload before editing')

        # (e) amount-only leg update, or delete the AUTO_DERIVE leg at zero net
        leg_date = None
        if leg is not None:
            leg_date = leg.txn_date
            if new_amount > 0:
                leg.amount = new_amount
            else:
                db.delete(leg)

        # (f) one commit for dividend + leg
        db.commit()
    except Exception:
        db.rollback()
        raise

    # (g) after the commit: a failing refresh rolls back and is swallowed in the helper
    if leg_date is not None:
        today = portfolio_snapshot_service._today_tw()
        cash_account_service._refresh_snapshot_cash_range(db, min(leg_date, today), today)

    # (h)
    db.refresh(row)
    return row
