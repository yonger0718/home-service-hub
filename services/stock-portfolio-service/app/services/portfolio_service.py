from sqlalchemy.orm import Session
from sqlalchemy import Column, func
from typing import Dict, List, Literal, Optional, Tuple
from datetime import date as date_type, datetime, timedelta, timezone

_ONE_DAY = timedelta(days=1)
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
import logging
import math
import os
from dateutil.relativedelta import relativedelta
from ..models.cash_transaction import CashTxnSource
from ..models import portfolio as models
from ..models.corporate_action import CorporateAction
from ..models.portfolio_snapshot import PortfolioSnapshot
from ..models.price_history import PriceHistory
from ..schemas import portfolio as schemas
from .twse_service import get_stock_quotes
from . import symbol_map_service
from . import cash_account_service
from shared_lib import get_tracer
tracer = get_tracer("stock-portfolio-service")
logger = logging.getLogger(__name__)

_XIRR_WINDOWS: Tuple[Literal["1m", "3m", "1y", "ytd"], ...] = (
    "1m",
    "3m",
    "1y",
    "ytd",
)


class _AdjustedTransaction:
    """Read-only view of a Transaction with a corporate-action factor applied.

    The view duck-types ``models.Transaction`` over the fields the
    aggregation logic touches. ``quantity`` is multiplied by ``factor`` and
    ``price`` is divided by it; cost basis (qty * price) is preserved.
    Fees and taxes stay nominal.
    """

    __slots__ = ("_base", "_factor")

    def __init__(self, base: models.Transaction, factor: Decimal):
        self._base = base
        self._factor = factor

    @property
    def id(self):
        return self._base.id

    @property
    def symbol(self):
        return self._base.symbol

    @property
    def market(self):
        return getattr(self._base, "market", "TW")

    @property
    def name(self):
        return self._base.name

    @property
    def type(self):
        return self._base.type

    @property
    def trade_date(self):
        return self._base.trade_date

    @property
    def quantity(self):
        if self._factor == 1:
            return self._base.quantity
        return Decimal(self._base.quantity) * self._factor

    @property
    def price(self):
        if self._factor == 1:
            return self._base.price
        return self._base.price / self._factor

    @property
    def currency(self):
        return getattr(self._base, "currency", "TWD")

    @property
    def fx_rate_to_twd(self):
        return getattr(self._base, "fx_rate_to_twd", None)

    @property
    def fee(self):
        return self._base.fee

    @property
    def tax(self):
        return self._base.tax

    @property
    def position_side(self):
        return getattr(self._base, "position_side", models.PositionSide.LONG)

    @property
    def is_day_trade(self):
        return getattr(self._base, "is_day_trade", False)


def _factor_for_trade(actions: List[CorporateAction], trade_date) -> Decimal:
    """Cumulative product of every action strictly AFTER trade_date."""
    target = trade_date.date() if hasattr(trade_date, "date") else trade_date
    factor = Decimal(1)
    for action in actions:
        if action.effective_date > target:
            factor *= action.ratio
    return factor


def _row_market(row: object) -> str:
    return str(getattr(row, "market", "TW") or "TW").strip().upper()


def _normalize_symbol_for_market(symbol: str, market: Optional[str] = None) -> str:
    return schemas._normalize_symbol(symbol, market)


def _symbol_market_key(row: object) -> tuple[str, str]:
    market = _row_market(row)
    return (_normalize_symbol_for_market(str(getattr(row, "symbol", "")), market), market)


def _apply_corp_action_factors(
    transactions: List[models.Transaction],
    actions_by_symbol: Optional[Dict[tuple[str, str], List[CorporateAction]]],
) -> List:
    """Return transactions (or adjusted views) with factor applied."""
    if not actions_by_symbol:
        return list(transactions)
    adjusted: list = []
    for txn in transactions:
        sym_actions = actions_by_symbol.get(_symbol_market_key(txn), None)
        if not sym_actions:
            adjusted.append(txn)
            continue
        factor = _factor_for_trade(sym_actions, txn.trade_date)
        if factor == 1:
            adjusted.append(txn)
        else:
            adjusted.append(_AdjustedTransaction(txn, factor))
    return adjusted


def _load_corp_actions_by_symbol(db: Session) -> Dict[tuple[str, str], List[CorporateAction]]:
    rows = (
        db.query(CorporateAction)
        .order_by(CorporateAction.effective_date.asc(), CorporateAction.id.asc())
        .all()
    )
    grouped: Dict[tuple[str, str], List[CorporateAction]] = {}
    for row in rows:
        grouped.setdefault(_symbol_market_key(row), []).append(row)
    return grouped


def _load_adjusted_transactions(db: Session) -> List:
    """Load transactions in the portfolio-summary order with split factors applied."""
    transactions = (
        db.query(models.Transaction)
        .order_by(
            models.Transaction.trade_date,
            models.Transaction.type.asc(),
            models.Transaction.id,
        )
        .all()
    )
    return _apply_corp_action_factors(transactions, _load_corp_actions_by_symbol(db))


def _calculate_xirr(cash_flows: List[Tuple[date_type, Decimal]]) -> Optional[Decimal]:
    """
    Compute XIRR from a list of (date, amount) pairs.
    Returns None if calculation is impossible or fails.
    - amounts < 0: outflows (buy)
    - amounts > 0: inflows (sell, dividend, terminal market value)

    Decimal/float boundary: ``pyxirr`` requires IEEE-754 ``float`` inputs and
    returns a ``float``. We coerce here with ``float(cf[1])`` and convert
    back via ``Decimal(str(round(result, 6)))``. For portfolio values within
    ~15 significant digits this is safe; the round at 6 decimal places is
    the canonical XIRR display precision (annualised return).
    """
    if len(cash_flows) < 2:
        return None

    dates = [cf[0] for cf in cash_flows]
    if len(set(dates)) < 2:
        return None

    if cash_flows[-1][1] <= Decimal("0"):
        return None

    try:
        from pyxirr import xirr as _xirr
        result = _xirr(
            [cf[0] for cf in cash_flows],
            [float(cf[1]) for cf in cash_flows],  # required: pyxirr is float-only
        )
        if result is None or not isinstance(result, float):
            return None
        if math.isnan(result) or math.isinf(result):
            return None
        return Decimal(str(round(result, 6)))
    except Exception:
        return None


def _window_start(
    today: date_type,
    window: Literal["1m", "3m", "1y", "ytd"],
) -> date_type:
    if window == "1m":
        return today - relativedelta(months=1)
    if window == "3m":
        return today - relativedelta(months=3)
    if window == "1y":
        return today - relativedelta(years=1)
    return date_type(today.year, 1, 1)


def _calculate_windowed_xirr(
    window_start: date_type,
    today: date_type,
    cashflows: List[Tuple[date_type, Decimal]],
    opening_mv: Optional[Decimal],
    closing_mv: Decimal,
) -> Optional[Decimal]:
    windowed_flows: List[Tuple[date_type, Decimal]] = []
    if opening_mv is not None and opening_mv > Decimal("0"):
        windowed_flows.append((window_start, -opening_mv))

    windowed_flows.extend(
        sorted(
            [
                (cf_date, amount)
                for cf_date, amount in cashflows
                if window_start <= cf_date <= today
            ],
            key=lambda item: item[0],
        )
    )
    windowed_flows.append((today, closing_mv))
    return _calculate_xirr(windowed_flows)


def _quantity_at_window_start(
    transactions: List[models.Transaction],
    symbol: str,
    market: str | date_type,
    window_start: Optional[date_type] = None,
) -> Decimal:
    if window_start is None:
        window_start = market  # type: ignore[assignment]
        market = "TW"
    normalized = _normalize_symbol_for_market(symbol, market)
    normalized_market = (market or "TW").strip().upper()
    quantity = Decimal("0")
    for transaction in transactions:
        t_side = getattr(transaction, "position_side", None) or models.PositionSide.LONG
        if not isinstance(t_side, models.PositionSide):
            t_side = models.PositionSide(t_side)
        if t_side is not models.PositionSide.LONG:
            continue

        if _symbol_market_key(transaction) != (normalized, normalized_market):
            continue

        trade_date = (
            transaction.trade_date.date()
            if hasattr(transaction.trade_date, "date")
            else transaction.trade_date
        )
        if trade_date >= window_start:
            continue

        signed_quantity = Decimal(transaction.quantity)
        if transaction.type == models.TransactionType.BUY:
            quantity += signed_quantity
        elif transaction.type == models.TransactionType.SELL:
            quantity -= signed_quantity
    return quantity


def _lookup_window_open_price(
    db: Session,
    symbol: str,
    market: str | date_type,
    window_start: Optional[date_type] = None,
) -> Optional[Decimal]:
    if window_start is None:
        window_start = market  # type: ignore[assignment]
        market = "TW"
    row = (
        db.query(PriceHistory)
        .filter(PriceHistory.symbol == _normalize_symbol_for_market(symbol, market))
        .filter(PriceHistory.market == (market or "TW").upper())
        .filter(PriceHistory.date <= window_start)
        .filter(PriceHistory.date >= window_start - timedelta(days=7))
        .order_by(PriceHistory.date.desc())
        .first()
    )
    return row.close if row is not None else None


def sanitize_symbol(symbol: str) -> str:
    """
    清理股票代碼：移除 .TW, .TWO (不分大小寫) 並轉為大寫，只保留前面的代碼。
    例如: 0050.TW -> 0050
    """
    if not symbol:
        return ""
    return symbol.split('.')[0].upper().strip()


def _escape_like_prefix(value: str) -> str:
    """Escape SQL LIKE wildcards so user input is matched literally.

    Without this, a ``%`` or ``_`` in the input would silently turn into
    wildcards in the ILIKE pattern. Backslash is escaped first so it
    cannot consume the escapes added afterwards.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _resolve_sort_trade_date(
    trade_date: datetime,
) -> datetime:
    if trade_date.tzinfo is None:
        return trade_date
    return trade_date.astimezone(timezone.utc).replace(tzinfo=None)


def _trade_calendar_date(trade_date: datetime) -> date_type:
    """Calendar date used for day-trade bucketing.

    Normalises to UTC date (matches ``_resolve_sort_trade_date``). TW trading
    hours run 01:00-05:30 UTC, so the UTC date matches the TW market date.
    """

    return _resolve_sort_trade_date(trade_date).date()


def _is_odd_lot(quantity: object) -> bool:
    quantity_dec = Decimal(quantity)
    return quantity_dec < Decimal("1000") or quantity_dec % Decimal("1000") != 0


def _recompute_day_trade_flags(
    db: Session, symbol: str, calendar_date: date_type
) -> None:
    """Flip ``is_day_trade`` for every transaction in the (symbol, date) bucket.

    A transaction is a day-trade when the same TW-market symbol has BOTH a
    BUY and a SELL on the same calendar trade date. Foreign-market rows in
    the same symbol/date bucket are always cleared. Caller commits.
    """

    normalized = sanitize_symbol(symbol)
    day_start = datetime.combine(calendar_date, datetime.min.time(), tzinfo=timezone.utc)
    day_end = day_start + _ONE_DAY
    rows = (
        db.query(models.Transaction)
        .filter(models.Transaction.symbol == normalized)
        .filter(models.Transaction.trade_date >= day_start)
        .filter(models.Transaction.trade_date < day_end)
        .all()
    )
    # _trade_calendar_date normalises to UTC date; rows already bounded above.
    bucket = [
        row for row in rows
        if _trade_calendar_date(row.trade_date) == calendar_date
    ]
    tw_bucket = [
        row for row in bucket
        if (getattr(row, "market", "TW") or "TW").upper() == "TW"
    ]
    board_lot = [row for row in tw_bucket if not _is_odd_lot(row.quantity)]
    has_buy = any(row.type == models.TransactionType.BUY for row in board_lot)
    has_sell = any(row.type == models.TransactionType.SELL for row in board_lot)
    marker_present = any(
        getattr(row, "broker_day_trade_marker", None) in {"沖買", "沖賣"}
        for row in board_lot
    )
    if marker_present:
        board_flag = all(
            symbol_map_service.is_day_trade_eligible(
                db, normalized, getattr(row, "instrument_type", None)
            )
            for row in board_lot
        )
    elif has_buy and has_sell:
        board_flag = all(
            symbol_map_service.is_day_trade_eligible(
                db, normalized, getattr(row, "instrument_type", None)
            )
            for row in board_lot
        )
    else:
        board_flag = False
    for row in bucket:
        is_tw = (getattr(row, "market", "TW") or "TW").upper() == "TW"
        new_flag = is_tw and not _is_odd_lot(row.quantity) and board_flag
        if row.is_day_trade != new_flag:
            row.is_day_trade = new_flag


def _validate_symbol_ledger(symbol: str, ledger_entries: List[Dict[str, object]]) -> None:
    long_qty = Decimal("0")
    short_qty = Decimal("0")

    for entry in sorted(
        ledger_entries,
        key=lambda item: (item["sort_trade_date"], item["sort_id"]),
    ):
        quantity = Decimal(entry["quantity"])
        side = entry.get("position_side", models.PositionSide.LONG)
        if not isinstance(side, models.PositionSide):
            side = models.PositionSide(side)
        is_buy = entry["type"] == models.TransactionType.BUY

        if side is models.PositionSide.LONG and is_buy:
            long_qty += quantity
            continue
        if side is models.PositionSide.SHORT and not is_buy:
            short_qty += quantity
            continue

        if side is models.PositionSide.LONG:
            available = long_qty
            long_qty -= quantity
            if long_qty >= 0:
                continue
            if available <= 0:
                raise ValueError(
                    f"Cannot sell {_display_quantity(quantity)} shares of {symbol} without holdings"
                )
            raise ValueError(
                f"Cannot sell {_display_quantity(quantity)} shares of {symbol}; "
                f"only {_display_quantity(available)} available"
            )

        available = short_qty
        short_qty -= quantity
        if short_qty >= 0:
            continue
        if available <= 0:
            raise ValueError(
                f"Cannot cover {_display_quantity(quantity)} shares of {symbol} without open short"
            )
        raise ValueError(
            f"Cannot cover {_display_quantity(quantity)} shares of {symbol}; "
            f"only {_display_quantity(available)} open short"
        )


def _display_quantity(value: Decimal) -> str:
    if value == value.to_integral_value():
        return str(value.to_integral_value())
    return format(value.normalize(), "f")


def _validate_transaction_ledger(
    db: Session,
    transaction_data: Dict[str, object],
    existing_transaction: Optional[models.Transaction] = None,
) -> None:
    proposed_market = str(transaction_data.get("market", "TW") or "TW").upper()
    proposed_symbol = _normalize_symbol_for_market(
        str(transaction_data["symbol"]), proposed_market
    )
    keys_to_validate = {(proposed_symbol, proposed_market)}
    if existing_transaction is not None:
        keys_to_validate.add(
            (
                _normalize_symbol_for_market(
                    existing_transaction.symbol,
                    getattr(existing_transaction, "market", "TW"),
                ),
                _row_market(existing_transaction),
            )
        )

    ledger_map: Dict[tuple[str, str], List[Dict[str, object]]] = {
        key: [] for key in keys_to_validate
    }
    persisted_transactions = (
        db.query(models.Transaction)
        .order_by(models.Transaction.trade_date, models.Transaction.id)
        .all()
    )

    for transaction in persisted_transactions:
        if existing_transaction is not None and transaction.id == existing_transaction.id:
            continue

        key = _symbol_market_key(transaction)
        if key not in keys_to_validate:
            continue

        ledger_map[key].append(
            {
                "sort_trade_date": _resolve_sort_trade_date(transaction.trade_date),
                "sort_id": transaction.id,
                "type": transaction.type,
                "position_side": getattr(
                    transaction, "position_side", models.PositionSide.LONG
                ),
                "quantity": transaction.quantity,
            }
        )

    proposed_side_raw = transaction_data.get("position_side", models.PositionSide.LONG)
    proposed_side = (
        proposed_side_raw
        if isinstance(proposed_side_raw, models.PositionSide)
        else models.PositionSide(
            getattr(proposed_side_raw, "value", proposed_side_raw)
        )
    )
    ledger_map[(proposed_symbol, proposed_market)].append(
        {
            "sort_trade_date": _resolve_sort_trade_date(transaction_data["trade_date"]),
            "sort_id": existing_transaction.id if existing_transaction is not None else float("inf"),
            "type": models.TransactionType(
                getattr(transaction_data["type"], "value", transaction_data["type"])
            ),
            "position_side": proposed_side,
            "quantity": transaction_data["quantity"],
        }
    )

    for (symbol, _market), entries in ledger_map.items():
        _validate_symbol_ledger(symbol, entries)


def _aggregate_active_holdings(
    transactions: List[models.Transaction],
    actions_by_symbol: Optional[Dict[tuple[str, str], List[CorporateAction]]] = None,
) -> Dict[tuple[str, str], Dict[str, object]]:
    holdings: Dict[tuple[str, str], Dict[str, object]] = {}

    adjusted = _apply_corp_action_factors(transactions, actions_by_symbol)

    for transaction in sorted(
        adjusted,
        key=lambda item: (_resolve_sort_trade_date(item.trade_date), item.id or float("inf")),
    ):
        t_side = getattr(transaction, "position_side", None) or models.PositionSide.LONG
        if not isinstance(t_side, models.PositionSide):
            t_side = models.PositionSide(t_side)
        if t_side is not models.PositionSide.LONG:
            continue

        key = _symbol_market_key(transaction)
        symbol, market = key
        if key not in holdings:
            holdings[key] = {
                "symbol": symbol,
                "market": market,
                "name": transaction.name,
                "total_quantity": Decimal("0"),
            }

        holdings[key]["total_quantity"] += (
            Decimal(transaction.quantity)
            if transaction.type == models.TransactionType.BUY
            else -Decimal(transaction.quantity)
        )
        if transaction.name and not holdings[key]["name"]:
            holdings[key]["name"] = transaction.name

    return {
        key: holding
        for key, holding in holdings.items()
        if Decimal(holding["total_quantity"]) > 0
    }


def get_active_holdings(db: Session) -> Dict[str, Dict[str, object]]:
    transactions = (
        db.query(models.Transaction)
        .order_by(models.Transaction.trade_date, models.Transaction.id)
        .all()
    )
    active = _aggregate_active_holdings(transactions, _load_corp_actions_by_symbol(db))
    return {
        symbol: holding
        for (symbol, market), holding in active.items()
        if market == "TW"
    }


def _get_quote_status(active_symbols: List[str], quotes: Dict[str, Dict]) -> str:
    if not active_symbols:
        return "ok"
    if not quotes:
        return "unavailable"
    if len(quotes) < len(active_symbols):
        return "partial"
    return "ok"


def _env_decimal(name: str, default: str) -> Decimal:
    val = os.getenv(name)
    if not val:
        return Decimal(default)
    try:
        return Decimal(str(val))
    except Exception:
        return Decimal(default)


def _estimate_sell_costs(gross_market_value: Decimal) -> Decimal:
    """
    估算券商賣出成本（手續費 + 證交稅），預設口徑:
    - 手續費: 0.1425% * 2.8折 = 0.0399%
    - 證交稅: 0.1% (ETF 常見口徑)
    - 成本採整數元無條件捨去
    - 最低手續費: 1 元（國泰證券 2.8 折期間電子下單低消 1 元；
      若實際扣費為 0 元的小額部位，估算仍保留 1 元偏保守）
    可用環境變數覆蓋:
    PORTFOLIO_SELL_FEE_RATE_BASE, PORTFOLIO_SELL_FEE_DISCOUNT,
    PORTFOLIO_SELL_TAX_RATE, PORTFOLIO_SELL_MIN_FEE
    """
    fee_rate_base = _env_decimal("PORTFOLIO_SELL_FEE_RATE_BASE", "0.001425")
    fee_discount = _env_decimal("PORTFOLIO_SELL_FEE_DISCOUNT", "0.28")
    tax_rate = _env_decimal("PORTFOLIO_SELL_TAX_RATE", "0.001")
    min_fee = _env_decimal("PORTFOLIO_SELL_MIN_FEE", "1")
    fee = (gross_market_value * fee_rate_base * fee_discount).quantize(Decimal("1"), rounding=ROUND_DOWN)
    if gross_market_value > 0:
        fee = max(fee, min_fee)
    tax = (gross_market_value * tax_rate).quantize(Decimal("1"), rounding=ROUND_DOWN)
    return fee + tax


def _dividend_amount_twd(row: models.Dividend) -> Decimal:
    currency = (getattr(row, "currency", "TWD") or "TWD").strip().upper()
    fx_rate = getattr(row, "fx_rate_to_twd", None)
    if currency != "TWD" and fx_rate is None:
        raise ValueError(
            f"missing fx_rate_to_twd for dividend "
            f"(id={getattr(row, 'id', None)!r}, symbol={row.symbol!r}, "
            f"ex_dividend_date={row.ex_dividend_date!r})"
        )
    amount = Decimal(row.amount)
    if fx_rate is None:
        return amount
    return amount * Decimal(fx_rate)


def _sync_transaction_cash_legs_if_twd(
    db: Session,
    transaction: models.Transaction,
) -> None:
    if not cash_account_service.cash_leg_enabled():
        return
    currency = (getattr(transaction, "currency", "TWD") or "TWD").strip().upper()
    if currency != "TWD":
        deleted_count = cash_account_service.delete_auto_derived_transaction_cash_legs(
            db,
            transaction.id,
        )
        # TODO Phase 2: route multi-currency cash legs to matching currency accounts.
        logger.info(
            "portfolio.cash_sync.skipped_non_twd_transaction",
            extra={
                "transaction_id": transaction.id,
                "currency": currency,
                "deleted_auto_derived_cash_legs": deleted_count,
            },
        )
        return
    try:
        account = cash_account_service.resolve_default_cathay_twd_account(db)
        cash_account_service.sync_transaction_cash_legs(
            db,
            transaction,
            account.id,
            CashTxnSource.AUTO_DERIVE,
        )
    except (
        cash_account_service.CashAccountNotFound,
        cash_account_service.CashAccountAmbiguous,
    ):
        raise


def _sync_dividend_cash_leg_if_twd(
    db: Session,
    dividend: models.Dividend,
) -> None:
    if not cash_account_service.cash_leg_enabled():
        return
    currency = (getattr(dividend, "currency", "TWD") or "TWD").strip().upper()
    if currency != "TWD":
        deleted_count = cash_account_service.delete_auto_derived_dividend_cash_leg(
            db,
            dividend.id,
        )
        # TODO Phase 2: route multi-currency cash legs to matching currency accounts.
        logger.info(
            "portfolio.cash_sync.skipped_non_twd_dividend",
            extra={
                "dividend_id": dividend.id,
                "currency": currency,
                "deleted_auto_derived_cash_legs": deleted_count,
            },
        )
        return
    account = cash_account_service.resolve_default_cathay_twd_account(db)
    cash_account_service.sync_dividend_cash_leg(
        db,
        dividend,
        account.id,
        CashTxnSource.AUTO_DERIVE,
    )


def get_portfolio_summary(db: Session) -> schemas.PortfolioSummary:
    """
    計算投資組合總覽，包含未實現損益與單日損益
    """
    with tracer.start_as_current_span("calculate_portfolio_summary") as span:
        # 1. 取得所有交易紀錄
        # Within the same trade_date, force BUY before SELL so a day-trade
        # whose SELL row has a lower id than its BUY cannot drop the SELL
        # silently against qty=0 and leave phantom holdings.
        transactions = (
            db.query(models.Transaction)
            .order_by(
                models.Transaction.trade_date,
                models.Transaction.type.asc(),  # BUY < SELL alphabetically
                models.Transaction.id,
            )
            .all()
        )
        # 2. 取得所有股利紀錄
        dividends = db.query(models.Dividend).all()
        actions_by_symbol = _load_corp_actions_by_symbol(db)
        adjusted_transactions = _apply_corp_action_factors(transactions, actions_by_symbol)
        from .realized_pnl_service import iter_realized_events
        realized_events = list(iter_realized_events(adjusted_transactions))
        realized_pnl_by_symbol: Dict[str, Decimal] = {}
        for event in realized_events:
            realized_pnl_by_symbol[event.symbol] = (
                realized_pnl_by_symbol.get(event.symbol, Decimal("0.0"))
                + event.realized_pnl
            )
        active_holdings = _aggregate_active_holdings(transactions, actions_by_symbol)
        active_keys = [
            key
            for key in active_holdings.keys()
            if key[1] == "TW"
        ]
        active_key_set = set(active_keys)
        active_symbols = [symbol for symbol, _market in active_keys]
        today = date_type.today()
        window_starts = {
            window: _window_start(today, window)
            for window in _XIRR_WINDOWS
        }

        span.set_attribute("portfolio.transaction_count", len(transactions))
        span.set_attribute("portfolio.dividend_count", len(dividends))
        span.set_attribute("portfolio.active_symbol_count", len(active_symbols))
        span.set_attribute("portfolio.corporate_action_symbol_count", len(actions_by_symbol))

        # 整理每檔股票的狀態
        holdings_map = {}
        cashflows_map: Dict[tuple[str, str], List[Tuple[date_type, Decimal]]] = {}

        # 股利統計
        dividend_map = {}
        for d in dividends:
            key = _symbol_market_key(d)
            if key[1] != "TW":
                continue
            amount_twd = _dividend_amount_twd(d)
            dividend_map[key] = dividend_map.get(key, Decimal("0.0")) + amount_twd
            # XIRR: dividend inflow
            cf_date = d.ex_dividend_date.date() if hasattr(d.ex_dividend_date, 'date') else d.ex_dividend_date
            cashflows_map.setdefault(key, []).append((cf_date, amount_twd))

        # 交易統計 (計算平均成本與持股數，採用 corporate-action 調整後的視圖)
        # SHORT rows are intentionally skipped here — long-side holdings_map only
        # tracks long inventory. Realized P&L for short closes is aggregated via
        # `realized_pnl_by_symbol` above (sourced from iter_realized_events).
        for t in adjusted_transactions:
            t_side = getattr(t, "position_side", None) or models.PositionSide.LONG
            if not isinstance(t_side, models.PositionSide):
                t_side = models.PositionSide(t_side)
            if t_side is not models.PositionSide.LONG:
                continue

            key = _symbol_market_key(t)
            if key[1] != "TW":
                continue
            symbol, market = key
            if key not in holdings_map:
                holdings_map[key] = {
                    "symbol": symbol,
                    "market": market,
                    "name": t.name,
                    "total_quantity": Decimal("0"),
                    "total_cost": Decimal("0.0"),
                    "total_cost_ex_fee": Decimal("0.0"),
                }

            h = holdings_map[key]
            if t.type == models.TransactionType.BUY:
                h["total_quantity"] += Decimal(t.quantity)
                # 買入總成本 = (單價 * 股數) + 手續費
                h["total_cost"] += (Decimal(t.quantity) * t.price) + (t.fee or Decimal("0.0"))
                # 成交均價口徑(不含手續費)
                h["total_cost_ex_fee"] += (Decimal(t.quantity) * t.price)
                # XIRR: buy outflow
                cf_date = t.trade_date.date() if hasattr(t.trade_date, 'date') else t.trade_date
                outflow = -((Decimal(t.quantity) * t.price) + (t.fee or Decimal("0.0")) + (t.tax or Decimal("0.0")))
                cashflows_map.setdefault(key, []).append((cf_date, outflow))
            elif t.type == models.TransactionType.SELL:
                if h["total_quantity"] > 0:
                    avg_unit_cost = h["total_cost"] / Decimal(h["total_quantity"])
                    avg_unit_cost_ex_fee = h["total_cost_ex_fee"] / Decimal(h["total_quantity"])
                    h["total_quantity"] -= Decimal(t.quantity)
                    # 賣出時減少庫存成本 (簡易已實現計算方式)
                    h["total_cost"] -= (Decimal(t.quantity) * avg_unit_cost)
                    h["total_cost_ex_fee"] -= (Decimal(t.quantity) * avg_unit_cost_ex_fee)
                    # XIRR: sell inflow
                    cf_date = t.trade_date.date() if hasattr(t.trade_date, 'date') else t.trade_date
                    inflow = (Decimal(t.quantity) * t.price) - (t.fee or Decimal("0.0")) - (t.tax or Decimal("0.0"))
                    cashflows_map.setdefault(key, []).append((cf_date, inflow))
                else:
                    pass

        # 3. 取得即時報價
        quotes = get_stock_quotes(active_symbols)
        quote_status = _get_quote_status(active_symbols, quotes)
        span.set_attribute("portfolio.quote_count", len(quotes))
        span.set_attribute("portfolio.quote_status", quote_status)

        holdings_list = []
        total_market_value = Decimal("0.0")
        total_cost = Decimal("0.0")
        total_unrealized_pnl = Decimal("0.0")
        total_day_pnl = Decimal("0.0")
        total_dividends = sum(dividend_map.values(), Decimal("0.0"))

        for key in active_keys:
            symbol, market = key
            h = holdings_map[key]
            quote = quotes.get(symbol, {})
            # 如果抓不到即時價格，暫以 0 處理，但在計算損益時應避免顯示全賠
            current_price = quote.get("current_price", Decimal("0.0"))
            yesterday_close = quote.get("yesterday_close", Decimal("0.0"))
            
            total_qty_dec = Decimal(h["total_quantity"])
            
            # 平均成本計算（含手續費 / 交易稅口徑，與損益計算一致）
            avg_cost = (h["total_cost"] / total_qty_dec).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            
            # 市值與未實現損益（以券商口徑估算賣出後淨額）
            gross_market_value = (total_qty_dec * current_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            estimated_sell_costs = _estimate_sell_costs(gross_market_value)
            market_value = (gross_market_value - estimated_sell_costs).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            
            if current_price > 0:
                unrealized_pnl = (market_value - h["total_cost"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                pnl_percent = ((unrealized_pnl / h["total_cost"]) * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if h["total_cost"] > 0 else Decimal("0.0")
                
                day_change_amount = (current_price - yesterday_close).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                day_change_percent = ((day_change_amount / yesterday_close) * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if yesterday_close > 0 else Decimal("0.0")
                day_pnl = (day_change_amount * total_qty_dec).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            else:
                # 沒股價時損益先歸零，避免誤導
                unrealized_pnl = Decimal("0.0")
                pnl_percent = Decimal("0.0")
                day_change_amount = Decimal("0.0")
                day_change_percent = Decimal("0.0")
                day_pnl = Decimal("0.0")
            
            stock_div = dividend_map.get(key, Decimal("0.0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            # Per-stock XIRR: append terminal market value at today
            stock_xirr: Optional[Decimal] = None
            if current_price > 0 and key in cashflows_map:
                stock_flows = sorted(cashflows_map.get(key, []), key=lambda x: x[0])
                stock_flows_with_terminal = stock_flows + [(today, market_value)]
                stock_xirr = _calculate_xirr(stock_flows_with_terminal)

            stock_windowed_xirr: Dict[str, Optional[Decimal]] = {
                window: None for window in _XIRR_WINDOWS
            }
            if current_price > 0 and key in cashflows_map:
                for window, window_start in window_starts.items():
                    qty_at_window_start = _quantity_at_window_start(
                        adjusted_transactions,
                        symbol,
                        market,
                        window_start,
                    )
                    opening_mv: Optional[Decimal] = None
                    if qty_at_window_start > Decimal("0"):
                        opening_price = _lookup_window_open_price(
                            db,
                            symbol,
                            market,
                            window_start,
                        )
                        if opening_price is None:
                            continue
                        opening_mv = qty_at_window_start * opening_price
                    stock_windowed_xirr[window] = _calculate_windowed_xirr(
                        window_start,
                        today,
                        cashflows_map.get(key, []),
                        opening_mv,
                        market_value,
                    )

            holdings_list.append(schemas.StockHolding(
                symbol=symbol,
                name=quote.get("name") or h["name"] or symbol,
                total_quantity=h["total_quantity"],
                avg_cost=avg_cost,
                current_price=current_price,
                market_value=market_value,
                unrealized_pnl=unrealized_pnl,
                unrealized_pnl_percent=pnl_percent,
                day_change_amount=day_change_amount,
                day_change_percent=day_change_percent,
                day_pnl=day_pnl,
                total_dividends=stock_div,
                total_pnl_with_dividend=(unrealized_pnl + stock_div).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                xirr=stock_xirr,
                xirr_1m=stock_windowed_xirr["1m"],
                xirr_3m=stock_windowed_xirr["3m"],
                xirr_1y=stock_windowed_xirr["1y"],
                xirr_ytd=stock_windowed_xirr["ytd"],
            ))

            if current_price > 0:
                total_market_value += market_value
                total_unrealized_pnl += unrealized_pnl
                total_day_pnl += day_pnl
            
            total_cost += h["total_cost"]

        total_pnl_percent = ((total_unrealized_pnl / total_cost) * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if total_cost > 0 else Decimal("0.0")

        # Portfolio XIRR: aggregate all cash flows, including closed positions.
        all_cashflows: List[Tuple[date_type, Decimal]] = [
            flow for flows in cashflows_map.values() for flow in flows
        ]
        all_cashflows.sort(key=lambda x: x[0])
        portfolio_xirr: Optional[Decimal] = None
        if total_market_value > 0 and all_cashflows:
            all_cashflows_with_terminal = all_cashflows + [(today, total_market_value)]
            portfolio_xirr = _calculate_xirr(all_cashflows_with_terminal)

        portfolio_windowed_xirr: Dict[str, Optional[Decimal]] = {
            window: None for window in _XIRR_WINDOWS
        }
        if total_market_value > 0:
            for window, window_start in window_starts.items():
                snapshot = (
                    db.query(PortfolioSnapshot)
                    .filter(PortfolioSnapshot.date <= window_start)
                    .filter(PortfolioSnapshot.date >= window_start - timedelta(days=7))
                    .order_by(PortfolioSnapshot.date.desc())
                    .first()
                )
                if snapshot is None:
                    continue
                portfolio_windowed_xirr[window] = _calculate_windowed_xirr(
                    window_start,
                    today,
                    all_cashflows,
                    snapshot.total_market_value,
                    total_market_value,
                )

        # Sum realised P&L across every symbol with realized events (long close +
        # short cover + no-inventory anomalies). Sourced from iter_realized_events
        # so the per-event sum invariant holds vs the realized-pnl endpoint.
        total_realized_pnl = sum(
            realized_pnl_by_symbol.values(),
            Decimal("0.0"),
        )
        total_market_value_twd = total_market_value.quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        total_cash_twd, _skipped = cash_account_service.get_total_balance_in(db, "TWD", asof=today)

        return schemas.PortfolioSummary(
            total_market_value=total_market_value_twd,
            total_cost=total_cost.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            total_unrealized_pnl=total_unrealized_pnl.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            total_unrealized_pnl_percent=total_pnl_percent,
            total_day_pnl=total_day_pnl.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            total_dividends=total_dividends.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            total_realized_pnl=total_realized_pnl.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            total_cash_twd=total_cash_twd,
            total_assets_twd=total_market_value_twd + total_cash_twd,
            holdings=holdings_list,
            portfolio_xirr=portfolio_xirr,
            portfolio_xirr_1m=portfolio_windowed_xirr["1m"],
            portfolio_xirr_3m=portfolio_windowed_xirr["3m"],
            portfolio_xirr_1y=portfolio_windowed_xirr["1y"],
            portfolio_xirr_ytd=portfolio_windowed_xirr["ytd"],
            quotes_status=quote_status,
        )

def create_transaction(db: Session, transaction: schemas.TransactionCreate):
    # Task 2: 清理 symbol
    transaction_data = transaction.model_dump()
    transaction_data["symbol"] = _normalize_symbol_for_market(
        transaction_data["symbol"],
        transaction_data.get("market", "TW"),
    )
    transaction_data["trade_date"] = transaction_data.get("trade_date") or datetime.now(timezone.utc)
    transaction_data["instrument_type"] = symbol_map_service.lookup_warrant_type(
        db, transaction_data["symbol"]
    )

    _validate_transaction_ledger(db, transaction_data)

    db_transaction = models.Transaction(**transaction_data)
    db.add(db_transaction)
    db.flush()
    _sync_transaction_cash_legs_if_twd(db, db_transaction)
    _recompute_day_trade_flags(
        db,
        db_transaction.symbol,
        _trade_calendar_date(db_transaction.trade_date),
    )
    db.commit()
    db.refresh(db_transaction)
    return db_transaction

def create_dividend(db: Session, dividend: schemas.DividendCreate):
    # Task 2: 清理 symbol
    dividend_data = dividend.model_dump()
    dividend_data["symbol"] = _normalize_symbol_for_market(
        dividend_data["symbol"],
        dividend_data.get("market", "TW"),
    )

    db_dividend = models.Dividend(**dividend_data)
    db.add(db_dividend)
    db.flush()
    _sync_dividend_cash_leg_if_twd(db, db_dividend)
    db.commit()
    db.refresh(db_dividend)
    return db_dividend


_TRANSACTION_SORT_FIELDS: Dict[str, Column] = {
    "trade_date": models.Transaction.trade_date,
    "symbol": models.Transaction.symbol,
    "type": models.Transaction.type,
    "price": models.Transaction.price,
    "quantity": models.Transaction.quantity,
}

_DIVIDEND_SORT_FIELDS: Dict[str, Column] = {
    "ex_dividend_date": models.Dividend.ex_dividend_date,
    "symbol": models.Dividend.symbol,
    "amount": models.Dividend.amount,
    "source": models.Dividend.source,
}


def _parse_sort(value: str, allowlist: Dict[str, Column]) -> Tuple[str, str]:
    """Split ``"field:direction"`` and validate against ``allowlist``.

    Raises ``ValueError`` on bad syntax or unknown field. Caller maps that
    to HTTP 422.
    """
    if not value or ":" not in value:
        raise ValueError(f"sort must be '<field>:<asc|desc>', got '{value}'")
    field, _, direction = value.partition(":")
    field = field.strip()
    direction = direction.strip().lower()
    if direction not in ("asc", "desc"):
        raise ValueError(f"sort direction must be 'asc' or 'desc', got '{direction}'")
    if field not in allowlist:
        raise ValueError(f"sort field '{field}' not allowed; choose one of {sorted(allowlist)}")
    return field, direction


def list_transactions(
    db: Session,
    *,
    symbol: Optional[str] = None,
    date_from: Optional[date_type] = None,
    date_to: Optional[date_type] = None,
    side: Optional[str] = None,
    sort_field: str = "trade_date",
    sort_dir: str = "desc",
    offset: int = 0,
    limit: int = 25,
) -> Tuple[List[models.Transaction], int]:
    """Return ``(items, total)`` paged + filtered transactions.

    ``date_from`` / ``date_to`` are inclusive bounds on ``trade_date``.
    ``id desc`` is always appended as tie-breaker so pages stay stable.
    """
    if sort_field not in _TRANSACTION_SORT_FIELDS:
        raise ValueError(f"sort field '{sort_field}' not allowed")

    base = db.query(models.Transaction)
    if symbol:
        # Prefix match so typing "0" shows every 0xxx ETF, "00" narrows to
        # 00xxx, etc. Sanitize first to keep casing/whitespace consistent;
        # only apply the filter if a non-empty stem remains so a
        # whitespace-only input does not degenerate into ILIKE '%' and
        # silently bypass filtering.
        stem = sanitize_symbol(symbol)
        if stem:
            base = base.filter(
                models.Transaction.symbol.ilike(
                    f"{_escape_like_prefix(stem)}%", escape="\\"
                )
            )
    if date_from is not None:
        base = base.filter(
            models.Transaction.trade_date
            >= datetime.combine(date_from, datetime.min.time(), tzinfo=timezone.utc)
        )
    if date_to is not None:
        # date_to inclusive — use < (next day midnight)
        end_exclusive = (
            datetime.combine(date_to, datetime.min.time(), tzinfo=timezone.utc) + _ONE_DAY
        )
        base = base.filter(models.Transaction.trade_date < end_exclusive)
    if side:
        base = base.filter(models.Transaction.type == side)

    total = base.with_entities(func.count(models.Transaction.id)).scalar() or 0

    sort_col = _TRANSACTION_SORT_FIELDS[sort_field]
    order = sort_col.desc() if sort_dir == "desc" else sort_col.asc()
    rows = (
        base.order_by(order, models.Transaction.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return rows, int(total)

def update_transaction(db: Session, transaction_id: int, transaction_update: schemas.TransactionCreate):
    db_transaction = db.query(models.Transaction).filter(models.Transaction.id == transaction_id).first()
    if not db_transaction:
        return None

    old_symbol = _normalize_symbol_for_market(
        db_transaction.symbol,
        getattr(db_transaction, "market", "TW"),
    )
    old_calendar = _trade_calendar_date(db_transaction.trade_date)

    update_data = transaction_update.model_dump(exclude_unset=True)
    update_data["symbol"] = _normalize_symbol_for_market(
        update_data["symbol"],
        update_data.get("market", getattr(db_transaction, "market", "TW")),
    )
    if "trade_date" in update_data:
        update_data["trade_date"] = update_data["trade_date"] or db_transaction.trade_date
    else:
        update_data["trade_date"] = db_transaction.trade_date

    if update_data["symbol"] != old_symbol or db_transaction.instrument_type is None:
        update_data["instrument_type"] = symbol_map_service.lookup_warrant_type(
            db, update_data["symbol"]
        )

    _validate_transaction_ledger(db, update_data, existing_transaction=db_transaction)

    for key, value in update_data.items():
        setattr(db_transaction, key, value)

    db.flush()
    _sync_transaction_cash_legs_if_twd(db, db_transaction)

    new_symbol = _normalize_symbol_for_market(
        db_transaction.symbol,
        getattr(db_transaction, "market", "TW"),
    )
    new_calendar = _trade_calendar_date(db_transaction.trade_date)
    _recompute_day_trade_flags(db, old_symbol, old_calendar)
    if (new_symbol, new_calendar) != (old_symbol, old_calendar):
        _recompute_day_trade_flags(db, new_symbol, new_calendar)

    db.commit()
    db.refresh(db_transaction)
    return db_transaction

def delete_transaction(db: Session, transaction_id: int):
    db_transaction = db.query(models.Transaction).filter(models.Transaction.id == transaction_id).first()
    if not db_transaction:
        return False

    symbol = _normalize_symbol_for_market(
        db_transaction.symbol,
        getattr(db_transaction, "market", "TW"),
    )
    calendar = _trade_calendar_date(db_transaction.trade_date)

    db.delete(db_transaction)
    db.flush()
    if cash_account_service.cash_leg_enabled():
        cash_account_service.delete_transaction_cash_legs(db, transaction_id)
    _recompute_day_trade_flags(db, symbol, calendar)
    db.commit()
    return True


def list_dividends(
    db: Session,
    *,
    symbol: Optional[str] = None,
    date_from: Optional[date_type] = None,
    date_to: Optional[date_type] = None,
    source: Optional[str] = None,
    sort_field: str = "ex_dividend_date",
    sort_dir: str = "desc",
    offset: int = 0,
    limit: int = 25,
) -> Tuple[List[models.Dividend], int]:
    """Return ``(items, total)`` paged + filtered dividends."""
    if sort_field not in _DIVIDEND_SORT_FIELDS:
        raise ValueError(f"sort field '{sort_field}' not allowed")

    base = db.query(models.Dividend)
    if symbol:
        # Prefix match — same UX as transactions list.
        stem = sanitize_symbol(symbol)
        if stem:
            base = base.filter(
                models.Dividend.symbol.ilike(
                    f"{_escape_like_prefix(stem)}%", escape="\\"
                )
            )
    if date_from is not None:
        base = base.filter(
            models.Dividend.ex_dividend_date
            >= datetime.combine(date_from, datetime.min.time(), tzinfo=timezone.utc)
        )
    if date_to is not None:
        end_exclusive = (
            datetime.combine(date_to, datetime.min.time(), tzinfo=timezone.utc) + _ONE_DAY
        )
        base = base.filter(models.Dividend.ex_dividend_date < end_exclusive)
    if source is not None:
        base = base.filter(models.Dividend.source == source)

    total = base.with_entities(func.count(models.Dividend.id)).scalar() or 0

    sort_col = _DIVIDEND_SORT_FIELDS[sort_field]
    order = sort_col.desc() if sort_dir == "desc" else sort_col.asc()
    rows = (
        base.order_by(order, models.Dividend.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return rows, int(total)

def update_dividend(db: Session, dividend_id: int, dividend_update: schemas.DividendCreate):
    db_dividend = db.query(models.Dividend).filter(models.Dividend.id == dividend_id).first()
    if not db_dividend:
        return None
    
    update_data = dividend_update.model_dump(exclude_unset=True)
    update_data["symbol"] = _normalize_symbol_for_market(
        update_data["symbol"],
        update_data.get("market", getattr(db_dividend, "market", "TW")),
    )
    
    for key, value in update_data.items():
        setattr(db_dividend, key, value)
    
    db.flush()
    _sync_dividend_cash_leg_if_twd(db, db_dividend)
    db.commit()
    db.refresh(db_dividend)
    return db_dividend

def delete_dividend(db: Session, dividend_id: int):
    db_dividend = db.query(models.Dividend).filter(models.Dividend.id == dividend_id).first()
    if not db_dividend:
        return False
    if cash_account_service.cash_leg_enabled():
        cash_account_service.delete_dividend_cash_leg(db, dividend_id)
    db.delete(db_dividend)
    db.commit()
    return True
