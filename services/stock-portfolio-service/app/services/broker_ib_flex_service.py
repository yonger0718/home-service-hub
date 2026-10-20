from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

import structlog

from ..models import portfolio as models
from . import broker_ib_service
from .cash_flow_service import cash_flow_fingerprint
from .import_service import (
    ParseError,
    ParseResult,
    ParsedRow,
    _broker_transaction_fingerprint,
)

log = structlog.get_logger(__name__)

LSE_EXCHANGES = {"LSE", "LSEETF", "LSEIOB"}

# None: Deposits/Withdrawals, resolved by the sign of the amount.
CASH_TYPES: dict[str, str | None] = {
    "Deposits/Withdrawals": None,
    "Dividends": models.BrokerCashFlowType.DIVIDEND_CASH.value,
    "Payment In Lieu Of Dividends": models.BrokerCashFlowType.DIVIDEND_CASH.value,
    "Withholding Tax": models.BrokerCashFlowType.FEE.value,
    "Other Fees": models.BrokerCashFlowType.FEE.value,
    "Broker Interest Paid": models.BrokerCashFlowType.FEE.value,
    "Commission Adjustments": models.BrokerCashFlowType.FEE.value,
    "Broker Interest Received": models.BrokerCashFlowType.INTEREST.value,
}
UNSUPPORTED_SECTIONS = {"Transfers", "CorporateActions"}


def _decimal(value: str | None) -> Decimal:
    normalized = (value or "").strip()
    value_ = Decimal(normalized) if normalized else Decimal("0")
    if not value_.is_finite():
        raise ValueError(f"non-finite number {normalized}")
    return value_


def _utc_midnight(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _root(raw_bytes: bytes) -> ET.Element:
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("flex: not UTF-8") from None
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise ValueError("flex: DTD refused")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise ValueError("flex: malformed XML") from None
    if root.tag == "FlexStatementResponse":
        code = (root.findtext("ErrorCode") or "").strip()
        raise ValueError(f"flex: {code if re.fullmatch(r'[0-9]{4}', code) else 'error'}")
    if root.tag != "FlexQueryResponse":
        raise ValueError("flex: not a FlexQueryResponse")
    return root


def _cash(row_index: int, el: ET.Element) -> ParsedRow:
    cash_type = el.get("type") or ""
    if cash_type not in CASH_TYPES:
        raise ValueError(f"unsupported cash transaction type {cash_type}")
    transaction_id = (el.get("transactionID") or "").strip()
    if not transaction_id:
        raise ValueError("cash transaction without transactionID")
    amount = _decimal(el.get("amount"))
    flow_type = CASH_TYPES[cash_type]
    if flow_type is None:
        flow_type = (
            models.BrokerCashFlowType.DEPOSIT.value
            if amount > 0
            else models.BrokerCashFlowType.WITHDRAWAL.value
        )
    date_: date = _utc_midnight((el.get("dateTime") or "")[:10]).date()
    currency = (el.get("currency") or "").strip().upper()
    description = (el.get("description") or "").strip()
    fingerprint_note = f"{description} ib:{transaction_id}"
    return ParsedRow(
        row_index=row_index,
        fingerprint=cash_flow_fingerprint(
            broker=models.Broker.IB.value,
            date_=date_,
            type_=flow_type,
            amount=amount,
            currency=currency,
            note=fingerprint_note,
        ),
        payload={
            "_kind": "cash_flow",
            "broker": models.Broker.IB.value,
            "date": date_,
            "cash_flow_type": flow_type,
            "amount": amount,
            "currency": currency,
            "note": description or None,
            "symbol": (el.get("symbol") or "").strip().upper(),
            "fingerprint_note": fingerprint_note,
        },
    )


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
                try:
                    if section.tag in UNSUPPORTED_SECTIONS:
                        raise ValueError(f"unsupported section {section.tag}")
                    if section.tag == "Trades" and el.tag == "Trade":
                        if el.get("levelOfDetail") != "EXECUTION":
                            continue
                        rows.append(_trade(row_index, el))
                    elif section.tag == "CashTransactions" and el.tag == "CashTransaction":
                        rows.append(_cash(row_index, el))
                except (ValueError, InvalidOperation) as exc:
                    errors.append(ParseError(row_index=row_index, message=str(exc)))
                    log.debug("broker.ib_flex.row_error", row_index=row_index, reason=str(exc))
    return ParseResult(rows=rows, errors=errors)
