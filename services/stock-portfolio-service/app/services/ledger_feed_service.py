"""Read-only 國泰 ledger feed (spec §4.1): settlements per trade date and a point-in-time valuation."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import portfolio as models
from ..models.cash_transaction import CashTransaction, CashTxnType
from ..models.cathay_statement import CathayStatementDay
from ..models.price_history import PriceHistory
from . import cash_account_service, portfolio_service
from .broker_cathay_statement_service import TW_CATHAY_FILTER

ROW_CAP = 2000
BROKER = "cathay"
LEG_TYPES = (CashTxnType.BUY_SETTLE, CashTxnType.SELL_SETTLE, CashTxnType.FEE, CashTxnType.TAX)
ZERO = Decimal("0")


class UnknownBroker(ValueError):
    pass


def _check_broker(broker: str) -> None:
    if broker != BROKER:
        raise UnknownBroker(broker)


def _amount(value: Decimal) -> str:
    return f"{value:.4f}"


def _fingerprint(trade_date: date, ids: list[int]) -> str:
    digest = hashlib.sha256(",".join(str(i) for i in sorted(ids)).encode()).hexdigest()[:16]
    return f"cathay:{trade_date.isoformat()}:{digest}"


def _legs_by_date(db: Session, window) -> dict[date, list[CashTransaction]]:
    try:
        account = cash_account_service.resolve_default_cathay_twd_account(db)
    except cash_account_service.CashAccountNotFound:
        return {}
    query = (
        select(CashTransaction)
        .join(models.Transaction, models.Transaction.id == CashTransaction.related_transaction_id)
        .where(CashTransaction.account_id == account.id, CashTransaction.type.in_(LEG_TYPES), TW_CATHAY_FILTER)
        .where(window)
    )
    grouped: dict[date, list[CashTransaction]] = defaultdict(list)
    for row in db.execute(query).scalars():
        grouped[row.txn_date].append(row)
    return grouped


def _row(trade_date: date, legs: list[CashTransaction], day: CathayStatementDay | None) -> dict | None:
    legs_net = sum((Decimal(leg.amount) for leg in legs), ZERO)
    has_facts = day is not None and (day.settlement_date is not None or day.net_amount is not None)
    reason = None
    if has_facts:
        date_source = "statement"
        settlement = day.settlement_date.isoformat() if day.settlement_date else None
        if day.last_status == "held" and day.last_reason == "unsupported_category":
            net = Decimal(day.net_amount) if day.net_amount is not None else ZERO
            bookable, reason = False, "margin_day"
        elif day.cash_net is None:
            net, bookable = legs_net, True
        else:
            net = Decimal(day.cash_net)
            bookable = net == 0 or net == legs_net
            if not bookable:
                reason = "legs_disagree"
    else:
        date_source, settlement, net, bookable = "legs", None, legs_net, True
    if net == 0:
        return None
    ids = [leg.id for leg in legs]
    return {
        "trade_date": trade_date.isoformat(),
        "settlement_date": settlement,
        "date_source": date_source,
        "net_amount": _amount(net),
        "legs_net": _amount(legs_net),
        "currency": "TWD",
        "bookable": bookable,
        "reason": reason,
        "items": [
            {"source_id": f"ct:{leg.id}", "type": leg.type.value, "amount": _amount(Decimal(leg.amount))}
            for leg in sorted(legs, key=lambda leg: leg.id)
        ],
        "fingerprint": _fingerprint(trade_date, ids),
    }


def settlements(
    db: Session, broker: str, *, settle_from: date, settle_to: date, trade_from: date, trade_to: date
) -> dict:
    _check_broker(broker)
    days = {
        day.trade_date: day
        for day in db.execute(
            select(CathayStatementDay).where(
                or_(
                    CathayStatementDay.trade_date.between(trade_from, trade_to),
                    # the only clause that reads the settlement date
                    CathayStatementDay.settlement_date.between(settle_from, settle_to),
                )
            )
        ).scalars()
    }
    legs = _legs_by_date(db, CashTransaction.txn_date.between(trade_from, trade_to))
    matched = sorted(set(days) | set(legs))
    complete = len(matched) <= ROW_CAP
    matched = matched[:ROW_CAP]
    # a day found through the statement's settlement date may lie outside the trade window: fetch its legs
    outside = [d for d in matched if d < trade_from or d > trade_to]
    if outside:
        legs.update(_legs_by_date(db, CashTransaction.txn_date.in_(outside)))
    rows = [row for d in matched if (row := _row(d, legs.get(d, []), days.get(d))) is not None]
    return {
        "rows": rows,
        "bounds": {
            "settle_from": settle_from.isoformat(), "settle_to": settle_to.isoformat(),
            "from": trade_from.isoformat(), "to": trade_to.isoformat(),
        },
        "complete": complete,
    }


def valuation(db: Session, broker: str, as_of: date) -> dict:
    _check_broker(broker)
    end = datetime(as_of.year, as_of.month, as_of.day, tzinfo=timezone.utc) + timedelta(days=1)
    transactions = (
        db.query(models.Transaction)
        .filter(TW_CATHAY_FILTER)
        .filter(models.Transaction.position_side == models.PositionSide.LONG)
        .filter(models.Transaction.trade_date < end)
        .all()
    )
    actions = {
        key: [a for a in group if a.effective_date <= as_of]
        for key, group in portfolio_service._load_corp_actions_by_symbol(db).items()
    }
    held: dict[str, Decimal] = defaultdict(Decimal)
    for txn in portfolio_service._apply_corp_action_factors(transactions, actions):
        sign = 1 if txn.type == models.TransactionType.BUY else -1
        held[txn.symbol] += sign * Decimal(txn.quantity)
    market_value, missing, price_dates = ZERO, [], []
    for symbol, quantity in sorted(held.items()):
        if quantity <= 0:
            continue
        price = (
            db.query(PriceHistory)
            .filter(PriceHistory.symbol == symbol, PriceHistory.market == "TW", PriceHistory.date <= as_of)
            .order_by(PriceHistory.date.desc())
            .first()
        )
        if price is None:
            missing.append(symbol)
            continue
        market_value += quantity * Decimal(price.close)
        price_dates.append(price.date)
    return {
        "broker": BROKER,
        "currency": "TWD",
        "as_of": as_of.isoformat(),
        "priced_through": min(price_dates).isoformat() if price_dates else None,
        "market_value": _amount(market_value),
        "cash": _amount(ZERO),
        "total": _amount(market_value),
        "complete": not missing,
        "missing": missing,
    }
