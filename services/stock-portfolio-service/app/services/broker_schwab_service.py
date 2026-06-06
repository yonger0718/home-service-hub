from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import structlog

from ..models import portfolio as models
from .cash_flow_service import cash_flow_fingerprint
from .import_service import (
    ParseError,
    ParseResult,
    ParsedRow,
    _broker_transaction_fingerprint,
)

log = structlog.get_logger(__name__)


def _money(value: str) -> Decimal:
    normalized = (
        (value or "0")
        .strip()
        .replace("$", "")
        .replace(",", "")
    )
    if normalized in {"", "-"}:
        return Decimal("0")
    return Decimal(normalized)


def _trade_date(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%m/%d/%Y").replace(tzinfo=timezone.utc)


def parse(raw_bytes: bytes) -> ParseResult:
    reader = csv.DictReader(io.StringIO(raw_bytes.decode("utf-8-sig")))
    rows: list[ParsedRow] = []
    errors: list[ParseError] = []
    for row_index, raw in enumerate(reader, start=1):
        try:
            action = (raw.get("Action") or "").strip()
            trade_date = _trade_date(raw.get("Date") or "")
            date_only = trade_date.date()
            if action in {"Buy", "Sell"}:
                symbol = (raw.get("Symbol") or "").strip().upper()
                quantity = abs(_money(raw.get("Quantity") or "0"))
                price = _money(raw.get("Price") or "0")
                fee = abs(_money(raw.get("Fees & Comm") or "0"))
                type_ = "BUY" if action == "Buy" else "SELL"
                note = (raw.get("Description") or "").strip() or None
                fingerprint = _broker_transaction_fingerprint(
                    broker=models.Broker.SCHWAB.value,
                    symbol=symbol,
                    market="US",
                    type_=type_,
                    quantity=quantity,
                    price=price,
                    trade_date=trade_date,
                    fee=fee,
                    tax=Decimal("0"),
                    currency="USD",
                    note=note,
                )
                rows.append(
                    ParsedRow(
                        row_index=row_index,
                        fingerprint=fingerprint,
                        payload={
                            "_kind": "transaction",
                            "broker": models.Broker.SCHWAB.value,
                            "symbol": symbol,
                            "market": "US",
                            "name": note,
                            "type": type_,
                            "position_side": models.PositionSide.LONG.value,
                            "quantity": quantity,
                            "price": price,
                            "currency": "USD",
                            "trade_date": trade_date,
                            "fee": fee,
                            "tax": Decimal("0"),
                        },
                    )
                )
                continue
            cash_flow_type = None
            amount = _money(raw.get("Amount") or "0")
            if action == "Wire Received":
                cash_flow_type = models.BrokerCashFlowType.DEPOSIT.value
                amount = abs(amount)
            elif action == "Wire Sent":
                cash_flow_type = models.BrokerCashFlowType.WITHDRAWAL.value
                amount = -abs(amount)
            if cash_flow_type is None:
                log.debug("broker.schwab.skip", action=action, row_index=row_index)
                continue
            note = (raw.get("Description") or "").strip() or None
            fingerprint = cash_flow_fingerprint(
                broker=models.Broker.SCHWAB.value,
                date_=date_only,
                type_=cash_flow_type,
                amount=amount,
                currency="USD",
                note=note,
            )
            rows.append(
                ParsedRow(
                    row_index=row_index,
                    fingerprint=fingerprint,
                    payload={
                        "_kind": "cash_flow",
                        "broker": models.Broker.SCHWAB.value,
                        "date": date_only,
                        "cash_flow_type": cash_flow_type,
                        "amount": amount,
                        "currency": "USD",
                        "note": note,
                    },
                )
            )
        except (ValueError, InvalidOperation) as exc:
            errors.append(ParseError(row_index=row_index, message=str(exc)))
    return ParseResult(rows=rows, errors=errors)
