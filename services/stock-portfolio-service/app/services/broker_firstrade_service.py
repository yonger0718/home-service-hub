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


def _decimal(value: str) -> Decimal:
    return Decimal((value or "0").replace(",", "").strip() or "0")


def _trade_date(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%Y/%m/%d").replace(tzinfo=timezone.utc)


def parse(raw_bytes: bytes) -> ParseResult:
    stream = io.StringIO(raw_bytes.decode("utf-8-sig"))
    reader = csv.DictReader(stream)
    rows: list[ParsedRow] = []
    errors: list[ParseError] = []
    for row_index, raw in enumerate(reader, start=1):
        try:
            action = (raw.get("交易類別") or "").strip()
            date_value = _trade_date((raw.get("日期") or "").strip())
            date_only = date_value.date()
            amount = _decimal(raw.get("金額") or "0")
            note = (raw.get("說明") or "").strip() or None
            if action in {"買進", "賣出"}:
                symbol = (raw.get("代號") or "").strip().upper()
                quantity = abs(_decimal(raw.get("數量") or "0"))
                price = _decimal(raw.get("價格") or "0")
                type_ = "BUY" if action == "買進" else "SELL"
                fingerprint = _broker_transaction_fingerprint(
                    broker=models.Broker.FIRSTRADE.value,
                    symbol=symbol,
                    market="US",
                    type_=type_,
                    quantity=quantity,
                    price=price,
                    trade_date=date_value,
                    fee=Decimal("0"),
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
                            "broker": models.Broker.FIRSTRADE.value,
                            "symbol": symbol,
                            "market": "US",
                            "name": note,
                            "type": type_,
                            "position_side": models.PositionSide.LONG.value,
                            "quantity": quantity,
                            "price": price,
                            "currency": "USD",
                            "trade_date": date_value,
                            "fee": Decimal("0"),
                            "tax": Decimal("0"),
                        },
                    )
                )
                continue
            cash_flow_type = None
            if action == "存款":
                cash_flow_type = models.BrokerCashFlowType.DEPOSIT.value
                amount = abs(amount)
            elif action == "利息收入":
                cash_flow_type = models.BrokerCashFlowType.INTEREST.value
            if cash_flow_type is None:
                log.debug("broker.firstrade.skip", action=action, row_index=row_index)
                continue
            fingerprint = cash_flow_fingerprint(
                broker=models.Broker.FIRSTRADE.value,
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
                        "broker": models.Broker.FIRSTRADE.value,
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
