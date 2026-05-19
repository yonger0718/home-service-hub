from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_type
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Iterator, Mapping, Optional

from sqlalchemy.orm import Session

from ..models import portfolio as models
from .portfolio_service import _load_adjusted_transactions, sanitize_symbol


@dataclass(frozen=True)
class RealizedPnlEvent:
    trade_date: date_type
    symbol: str
    name: Optional[str]
    quantity: int
    sell_price: Decimal
    avg_cost_at_sale: Decimal
    fee: Decimal
    tax: Decimal
    proceeds_gross: Decimal
    proceeds_net: Decimal
    cost_out: Decimal
    realized_pnl: Decimal
    is_day_trade: bool
    note: Optional[str] = None


_MONEY_QUANT = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)


def _trade_date(value: object) -> date_type:
    if hasattr(value, "date"):
        return value.date()
    return value  # type: ignore[return-value]


def iter_realized_events(transactions: Iterable[models.Transaction]) -> Iterator[RealizedPnlEvent]:
    pools: dict[str, dict[str, Decimal | int]] = {}

    for transaction in transactions:
        symbol = sanitize_symbol(transaction.symbol)
        pool = pools.setdefault(
            symbol,
            {
                "total_quantity": 0,
                "total_cost": Decimal("0.0"),
                "total_cost_ex_fee": Decimal("0.0"),
            },
        )
        quantity = int(transaction.quantity)
        price = Decimal(transaction.price)
        fee = transaction.fee or Decimal("0.0")
        tax = transaction.tax or Decimal("0.0")

        if transaction.type == models.TransactionType.BUY:
            pool["total_quantity"] = int(pool["total_quantity"]) + quantity
            pool["total_cost"] = (
                Decimal(pool["total_cost"]) + (Decimal(quantity) * price) + fee
            )
            pool["total_cost_ex_fee"] = (
                Decimal(pool["total_cost_ex_fee"]) + (Decimal(quantity) * price)
            )
            continue

        if transaction.type != models.TransactionType.SELL:
            continue

        current_qty = int(pool["total_quantity"])
        proceeds_gross = Decimal(quantity) * price
        proceeds_net = proceeds_gross - fee - tax
        if current_qty == 0:
            yield RealizedPnlEvent(
                trade_date=_trade_date(transaction.trade_date),
                symbol=symbol,
                name=transaction.name,
                quantity=quantity,
                sell_price=price,
                avg_cost_at_sale=Decimal("0"),
                fee=fee,
                tax=tax,
                proceeds_gross=proceeds_gross,
                proceeds_net=proceeds_net,
                cost_out=Decimal("0"),
                realized_pnl=proceeds_net,
                is_day_trade=bool(getattr(transaction, "is_day_trade", False)),
                note="no_inventory",
            )
            continue

        avg_unit_cost = Decimal(pool["total_cost"]) / Decimal(current_qty)
        avg_unit_cost_ex_fee = Decimal(pool["total_cost_ex_fee"]) / Decimal(current_qty)
        sold_qty = min(quantity, current_qty)
        sold_qty_dec = Decimal(sold_qty)
        proceeds_gross = sold_qty_dec * price
        proceeds_net = proceeds_gross - fee - tax
        cost_out = sold_qty_dec * avg_unit_cost

        yield RealizedPnlEvent(
            trade_date=_trade_date(transaction.trade_date),
            symbol=symbol,
            name=transaction.name,
            quantity=sold_qty,
            sell_price=price,
            avg_cost_at_sale=avg_unit_cost,
            fee=fee,
            tax=tax,
            proceeds_gross=proceeds_gross,
            proceeds_net=proceeds_net,
            cost_out=cost_out,
            realized_pnl=proceeds_net - cost_out,
            is_day_trade=bool(getattr(transaction, "is_day_trade", False)),
        )

        pool["total_quantity"] = current_qty - quantity
        pool["total_cost"] = Decimal(pool["total_cost"]) - (Decimal(quantity) * avg_unit_cost)
        pool["total_cost_ex_fee"] = Decimal(pool["total_cost_ex_fee"]) - (
            Decimal(quantity) * avg_unit_cost_ex_fee
        )


def _filtered_events(
    events: list[RealizedPnlEvent],
    *,
    symbol: Optional[str] = None,
    date_from: Optional[date_type] = None,
    date_to: Optional[date_type] = None,
    year: Optional[int] = None,
    day_trade_only: bool = False,
) -> list[RealizedPnlEvent]:
    effective_from = date_from
    effective_to = date_to
    if year is not None:
        if effective_from is None:
            effective_from = date_type(year, 1, 1)
        if effective_to is None:
            effective_to = date_type(year, 12, 31)

    normalized_symbol = sanitize_symbol(symbol) if symbol else None
    filtered = events
    if normalized_symbol:
        filtered = [
            event for event in filtered
            if sanitize_symbol(event.symbol) == normalized_symbol
        ]
    if effective_from is not None:
        filtered = [event for event in filtered if event.trade_date >= effective_from]
    if effective_to is not None:
        filtered = [event for event in filtered if event.trade_date <= effective_to]
    if day_trade_only:
        filtered = [event for event in filtered if event.is_day_trade]
    return filtered


def _sort_events(events: list[RealizedPnlEvent], sort: str) -> list[RealizedPnlEvent]:
    if not sort or ":" not in sort:
        raise ValueError(f"sort must be '<field>:<asc|desc>', got '{sort}'")
    field, _, direction = sort.partition(":")
    field = field.strip()
    direction = direction.strip().lower()
    if field not in {"trade_date", "realized_pnl"}:
        raise ValueError("sort field must be one of ['realized_pnl', 'trade_date']")
    if direction not in {"asc", "desc"}:
        raise ValueError(f"sort direction must be 'asc' or 'desc', got '{direction}'")

    return sorted(
        events,
        key=lambda event: (getattr(event, field), event.symbol),
        reverse=direction == "desc",
    )


def compute_events(
    session: Session,
    *,
    symbol: Optional[str] = None,
    date_from: Optional[date_type] = None,
    date_to: Optional[date_type] = None,
    year: Optional[int] = None,
    day_trade_only: bool = False,
    sort: str = "trade_date:desc",
) -> list[RealizedPnlEvent]:
    events = list(iter_realized_events(_load_adjusted_transactions(session)))
    filtered = _filtered_events(
        events,
        symbol=symbol,
        date_from=date_from,
        date_to=date_to,
        year=year,
        day_trade_only=day_trade_only,
    )
    return _sort_events(filtered, sort)


def compute_summary(
    session: Session,
    filter_query: Mapping[str, object],
) -> tuple[Decimal, Decimal]:
    events = list(iter_realized_events(_load_adjusted_transactions(session)))
    filtered = _filtered_events(
        events,
        symbol=filter_query.get("symbol"),  # type: ignore[arg-type]
        date_from=filter_query.get("date_from"),  # type: ignore[arg-type]
        date_to=filter_query.get("date_to"),  # type: ignore[arg-type]
        year=filter_query.get("year"),  # type: ignore[arg-type]
        day_trade_only=bool(filter_query.get("day_trade_only", False)),
    )
    current_year = date_type.today().year
    filter_scope_total = sum(
        (event.realized_pnl for event in filtered),
        Decimal("0.0"),
    )
    ytd_total = sum(
        (
            event.realized_pnl
            for event in events
            if (
                date_type(current_year, 1, 1)
                <= event.trade_date
                <= date_type(current_year, 12, 31)
            )
        ),
        Decimal("0.0"),
    )
    return _money(filter_scope_total), _money(ytd_total)
