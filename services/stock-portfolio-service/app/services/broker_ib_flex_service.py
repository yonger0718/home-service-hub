from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import structlog

from ..models import portfolio as models
from . import broker_ib_service
from .import_service import (
    ParseError,
    ParseResult,
    ParsedRow,
    _broker_transaction_fingerprint,
)

log = structlog.get_logger(__name__)

LSE_EXCHANGES = {"LSE", "LSEETF", "LSEIOB"}


def _decimal(value: str | None) -> Decimal:
    normalized = (value or "").strip()
    return Decimal(normalized) if normalized else Decimal("0")


def _utc_midnight(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _root(raw_bytes: bytes) -> ET.Element:
    return ET.fromstring(raw_bytes)


def _trade(row_index: int, el: ET.Element) -> ParsedRow:
    category = el.get("assetCategory") or ""
    if category != "STK":
        raise ValueError(f"unsupported assetCategory {category}")
    currency = (el.get("currency") or "").strip().upper()
    commission_currency = (el.get("ibCommissionCurrency") or "").strip().upper()
    if commission_currency and commission_currency != currency:
        raise ValueError(f"ibCommissionCurrency {commission_currency} differs from currency {currency}")
    trade_id = (el.get("tradeID") or "").strip()
    if not trade_id:
        raise ValueError("trade without tradeID")
    symbol = (el.get("symbol") or "").strip().upper()
    market = (
        "LSE"
        if el.get("listingExchange") in LSE_EXCHANGES
        else broker_ib_service._market_for(symbol, currency)
    )
    type_ = "BUY" if el.get("buySell") == "BUY" else "SELL"
    quantity = abs(_decimal(el.get("quantity")))
    price = _decimal(el.get("tradePrice"))
    fee = abs(_decimal(el.get("ibCommission")))
    tax = abs(_decimal(el.get("taxes")))
    trade_date = _utc_midnight(el.get("tradeDate") or "")
    name = (el.get("description") or "").strip() or None
    fingerprint_note = f"{name or ''} ib:{trade_id}".strip()
    fingerprint = _broker_transaction_fingerprint(
        broker=models.Broker.IB.value,
        symbol=symbol,
        market=market,
        type_=type_,
        quantity=quantity,
        price=price,
        trade_date=trade_date,
        fee=fee,
        tax=tax,
        currency=currency,
        note=fingerprint_note,
    )
    return ParsedRow(
        row_index=row_index,
        fingerprint=fingerprint,
        payload={
            "_kind": "transaction",
            "broker": models.Broker.IB.value,
            "symbol": symbol,
            "market": market,
            "name": name,
            "type": type_,
            "position_side": models.PositionSide.LONG.value,
            "quantity": quantity,
            "price": price,
            "currency": currency,
            "trade_date": trade_date,
            "fee": fee,
            "tax": tax,
            "fingerprint_note": fingerprint_note,
        },
    )


def parse(raw_bytes: bytes) -> ParseResult:
    root = _root(raw_bytes)
    rows: list[ParsedRow] = []
    errors: list[ParseError] = []
    row_index = 0
    for statement in root.iter("FlexStatement"):
        for section in statement:
            for el in section:
                row_index += 1
                if section.tag != "Trades" or el.tag != "Trade":
                    continue
                if el.get("levelOfDetail") != "EXECUTION":
                    continue
                try:
                    rows.append(_trade(row_index, el))
                except (ValueError, InvalidOperation) as exc:
                    errors.append(ParseError(row_index=row_index, message=str(exc)))
                    log.debug("broker.ib_flex.row_error", row_index=row_index, reason=str(exc))
    return ParseResult(rows=rows, errors=errors)
