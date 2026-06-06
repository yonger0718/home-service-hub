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
    quantity: Decimal
    sell_price: Decimal
    avg_cost_at_sale: Decimal
    fee: Decimal
    tax: Decimal
    proceeds_gross: Decimal
    proceeds_net: Decimal
    cost_out: Decimal
    realized_pnl: Decimal
    is_day_trade: bool
    position_side: models.PositionSide
    broker: Optional[str] = None
    note: Optional[str] = None


_MONEY_QUANT = Decimal("0.01")
_ZERO = Decimal("0")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)


def _trade_date(value: object) -> date_type:
    if hasattr(value, "date"):
        return value.date()
    return value  # type: ignore[return-value]


def _empty_long_pool() -> dict[str, Decimal | int]:
    return {
        "total_quantity": 0,
        "total_cost": Decimal("0.0"),
        "total_cost_ex_fee": Decimal("0.0"),
    }


def _empty_short_pool() -> dict[str, Decimal | int]:
    return {
        "total_quantity": 0,
        "total_proceeds_gross": Decimal("0.0"),
        "total_proceeds_net": Decimal("0.0"),
    }


def _row_ref(row: object) -> str:
    row_id = getattr(row, "id", None)
    symbol = getattr(row, "symbol", None)
    trade_date = getattr(row, "trade_date", None)
    return f"id={row_id!r}, symbol={symbol!r}, trade_date={trade_date!r}"


def _to_twd_per_share(row: object) -> Decimal:
    currency = (getattr(row, "currency", "TWD") or "TWD").upper()
    fx_rate = getattr(row, "fx_rate_to_twd", None)
    if currency != "TWD" and fx_rate is None:
        raise ValueError(f"missing fx_rate_to_twd for transaction ({_row_ref(row)})")
    price = Decimal(getattr(row, "price"))
    if fx_rate is None:
        return price
    return price * Decimal(fx_rate)


def _to_twd_money(row: object, raw_value: object) -> Decimal:
    value = Decimal(raw_value or "0")
    fx_rate = getattr(row, "fx_rate_to_twd", None)
    if fx_rate is None:
        return value
    return value * Decimal(fx_rate)


def _quantity(value: object) -> Decimal:
    quantity = Decimal(value)
    integral = quantity.to_integral_value()
    if quantity == integral:
        return integral
    return quantity


def iter_realized_events(transactions: Iterable[models.Transaction]) -> Iterator[RealizedPnlEvent]:
    pools: dict[tuple[str, str], dict[str, dict[str, Decimal | int]]] = {}

    for transaction in transactions:
        symbol = sanitize_symbol(transaction.symbol)
        market = (getattr(transaction, "market", "TW") or "TW").upper()
        symbol_pools = pools.setdefault(
            (symbol, market), {"LONG": _empty_long_pool(), "SHORT": _empty_short_pool()}
        )
        long_pool = symbol_pools["LONG"]
        short_pool = symbol_pools["SHORT"]

        quantity = _quantity(transaction.quantity)
        price = _to_twd_per_share(transaction)
        fee = _to_twd_money(transaction, transaction.fee)
        tax = _to_twd_money(transaction, transaction.tax)
        side = getattr(transaction, "position_side", None) or models.PositionSide.LONG
        if not isinstance(side, models.PositionSide):
            side = models.PositionSide(side)
        is_day_trade = bool(getattr(transaction, "is_day_trade", False))
        broker = getattr(transaction, "broker", None) or models.Broker.TW_MANUAL.value
        tx_trade_date = _trade_date(transaction.trade_date)

        if side is models.PositionSide.LONG and transaction.type == models.TransactionType.BUY:
            long_pool["total_quantity"] = Decimal(long_pool["total_quantity"]) + quantity
            long_pool["total_cost"] = (
                Decimal(long_pool["total_cost"]) + (quantity * price) + fee
            )
            long_pool["total_cost_ex_fee"] = (
                Decimal(long_pool["total_cost_ex_fee"]) + (quantity * price)
            )
            continue

        if side is models.PositionSide.SHORT and transaction.type == models.TransactionType.SELL:
            proceeds_gross = quantity * price
            proceeds_net = proceeds_gross - fee - tax
            short_pool["total_quantity"] = Decimal(short_pool["total_quantity"]) + quantity
            short_pool["total_proceeds_gross"] = (
                Decimal(short_pool["total_proceeds_gross"]) + proceeds_gross
            )
            short_pool["total_proceeds_net"] = (
                Decimal(short_pool["total_proceeds_net"]) + proceeds_net
            )
            continue

        if side is models.PositionSide.LONG and transaction.type == models.TransactionType.SELL:
            current_qty = Decimal(long_pool["total_quantity"])
            proceeds_gross = quantity * price
            proceeds_net = proceeds_gross - fee - tax
            if current_qty == 0:
                yield RealizedPnlEvent(
                    trade_date=tx_trade_date,
                    symbol=symbol,
                    name=transaction.name,
                    quantity=quantity,
                    sell_price=price,
                    avg_cost_at_sale=_ZERO,
                    fee=fee,
                    tax=tax,
                    proceeds_gross=proceeds_gross,
                    proceeds_net=proceeds_net,
                    cost_out=_ZERO,
                    realized_pnl=proceeds_net,
                    is_day_trade=is_day_trade,
                    position_side=models.PositionSide.LONG,
                    broker=broker,
                    note="no_long_inventory",
                )
                continue

            avg_unit_cost = Decimal(long_pool["total_cost"]) / Decimal(current_qty)
            avg_unit_cost_ex_fee = (
                Decimal(long_pool["total_cost_ex_fee"]) / Decimal(current_qty)
            )
            sold_qty = min(quantity, current_qty)
            proceeds_gross = sold_qty * price
            proceeds_net = proceeds_gross - fee - tax
            cost_out = sold_qty * avg_unit_cost

            yield RealizedPnlEvent(
                trade_date=tx_trade_date,
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
                is_day_trade=is_day_trade,
                position_side=models.PositionSide.LONG,
                broker=broker,
            )

            long_pool["total_quantity"] = current_qty - sold_qty
            long_pool["total_cost"] = (
                Decimal(long_pool["total_cost"]) - (sold_qty * avg_unit_cost)
            )
            long_pool["total_cost_ex_fee"] = (
                Decimal(long_pool["total_cost_ex_fee"])
                - (sold_qty * avg_unit_cost_ex_fee)
            )
            continue

        if side is models.PositionSide.SHORT and transaction.type == models.TransactionType.BUY:
            current_short_qty = Decimal(short_pool["total_quantity"])
            cover_gross = quantity * price
            cover_cost_total = cover_gross + fee + tax
            if current_short_qty == 0:
                yield RealizedPnlEvent(
                    trade_date=tx_trade_date,
                    symbol=symbol,
                    name=transaction.name,
                    quantity=quantity,
                    sell_price=price,
                    avg_cost_at_sale=_ZERO,
                    fee=fee,
                    tax=tax,
                    proceeds_gross=_ZERO,
                    proceeds_net=_ZERO,
                    cost_out=cover_cost_total,
                    realized_pnl=-cover_cost_total,
                    is_day_trade=is_day_trade,
                    position_side=models.PositionSide.SHORT,
                    broker=broker,
                    note="no_short_inventory",
                )
                continue

            avg_open_price = (
                Decimal(short_pool["total_proceeds_gross"]) / Decimal(current_short_qty)
            )
            avg_open_net_per_share = (
                Decimal(short_pool["total_proceeds_net"]) / Decimal(current_short_qty)
            )
            covered_qty = min(quantity, current_short_qty)
            cover_gross_slice = covered_qty * price
            cover_cost_slice = cover_gross_slice + fee + tax
            proceeds_gross_slice = covered_qty * avg_open_price
            proceeds_net_slice = covered_qty * avg_open_net_per_share

            yield RealizedPnlEvent(
                trade_date=tx_trade_date,
                symbol=symbol,
                name=transaction.name,
                quantity=covered_qty,
                sell_price=price,
                avg_cost_at_sale=avg_open_price,
                fee=fee,
                tax=tax,
                proceeds_gross=proceeds_gross_slice,
                proceeds_net=proceeds_net_slice,
                cost_out=cover_cost_slice,
                realized_pnl=proceeds_net_slice - cover_cost_slice,
                is_day_trade=is_day_trade,
                position_side=models.PositionSide.SHORT,
                broker=broker,
            )

            short_pool["total_quantity"] = current_short_qty - covered_qty
            short_pool["total_proceeds_gross"] = (
                Decimal(short_pool["total_proceeds_gross"]) - proceeds_gross_slice
            )
            short_pool["total_proceeds_net"] = (
                Decimal(short_pool["total_proceeds_net"]) - proceeds_net_slice
            )
            continue


def _filtered_events(
    events: list[RealizedPnlEvent],
    *,
    symbol: Optional[str] = None,
    date_from: Optional[date_type] = None,
    date_to: Optional[date_type] = None,
    year: Optional[int] = None,
    day_trade_only: bool = False,
    broker: Optional[str] = None,
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
        # Prefix match so "00" narrows to 00xxx ETFs and "3" matches all 3xxx
        # tickers — mirrors `list_transactions` / `list_dividends` ILIKE
        # behavior so the filter UX is consistent across portfolio pages.
        filtered = [
            event for event in filtered
            if sanitize_symbol(event.symbol).startswith(normalized_symbol)
        ]
    if effective_from is not None:
        filtered = [event for event in filtered if event.trade_date >= effective_from]
    if effective_to is not None:
        filtered = [event for event in filtered if event.trade_date <= effective_to]
    if day_trade_only:
        filtered = [event for event in filtered if event.is_day_trade]
    if broker:
        filtered = [event for event in filtered if event.broker == broker]
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
    broker: Optional[str] = None,
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
        broker=broker,
    )
    return _sort_events(filtered, sort)


def compute_summary(
    session: Session,
    filter_query: Mapping[str, object],
) -> tuple[Decimal, int, Decimal, int]:
    events = list(iter_realized_events(_load_adjusted_transactions(session)))
    filtered = _filtered_events(
        events,
        symbol=filter_query.get("symbol"),  # type: ignore[arg-type]
        date_from=filter_query.get("date_from"),  # type: ignore[arg-type]
        date_to=filter_query.get("date_to"),  # type: ignore[arg-type]
        year=filter_query.get("year"),  # type: ignore[arg-type]
        day_trade_only=bool(filter_query.get("day_trade_only", False)),
        broker=filter_query.get("broker"),  # type: ignore[arg-type]
    )
    current_year = date_type.today().year
    ytd_events = [
        event for event in events
        if (
            date_type(current_year, 1, 1)
            <= event.trade_date
            <= date_type(current_year, 12, 31)
        )
    ]
    filter_scope_total = sum((event.realized_pnl for event in filtered), Decimal("0.0"))
    ytd_total = sum((event.realized_pnl for event in ytd_events), Decimal("0.0"))
    return _money(filter_scope_total), len(filtered), _money(ytd_total), len(ytd_events)
