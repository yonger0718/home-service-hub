from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable

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
    normalized = (value or "0").replace(",", "").strip()
    if normalized in {"", "-"}:
        return Decimal("0")
    return Decimal(normalized)


def _trade_date(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _market_for(symbol: str, currency: str) -> str:
    return "LSE" if currency.upper() == "GBP" else "US"


def parse(
    raw_bytes: bytes,
    *,
    market_resolver: "Callable[[str, str], str | None] | None" = None,
) -> ParseResult:
    reader = csv.reader(io.StringIO(raw_bytes.decode("utf-8-sig")))
    rows: list[ParsedRow] = []
    errors: list[ParseError] = []
    base_currency = "USD"
    transfer_header: list[str] | None = None
    for csv_index, raw in enumerate(reader, start=1):
        if not raw:
            continue
        if raw[:3] == ["總結", "Data", "基礎貨幣"] and len(raw) >= 4:
            base_currency = raw[3].strip().upper()
            continue
        if raw[:2] == ["轉賬歷史", "Header"]:
            transfer_header = raw[2:]
            continue
        if raw[:2] != ["轉賬歷史", "Data"] or transfer_header is None:
            continue
        row_index = csv_index
        data = dict(zip(transfer_header, raw[2:]))
        try:
            action = (data.get("交易類型") or "").strip()
            trade_date = _trade_date(data.get("日期") or "")
            date_only = trade_date.date()
            if action in {"買", "賣"}:
                symbol = (data.get("代碼") or "").strip().upper()
                quantity = abs(_decimal(data.get("交易量") or "0"))
                price = _decimal(data.get("價格") or "0")
                currency = (data.get("Price Currency") or base_currency).strip().upper()
                market = (market_resolver(symbol, currency) if market_resolver else None) or _market_for(symbol, currency)
                fee = abs(_decimal(data.get("佣金") or "0"))
                type_ = "BUY" if action == "買" else "SELL"
                note = (data.get("說明") or "").strip() or None
                fingerprint = _broker_transaction_fingerprint(
                    broker=models.Broker.IB.value,
                    symbol=symbol,
                    market=market,
                    type_=type_,
                    quantity=quantity,
                    price=price,
                    trade_date=trade_date,
                    fee=fee,
                    tax=Decimal("0"),
                    currency=currency,
                    note=note,
                )
                rows.append(
                    ParsedRow(
                        row_index=row_index,
                        fingerprint=fingerprint,
                        payload={
                            "_kind": "transaction",
                            "broker": models.Broker.IB.value,
                            "symbol": symbol,
                            "market": market,
                            "name": note,
                            "type": type_,
                            "position_side": models.PositionSide.LONG.value,
                            "quantity": quantity,
                            "price": price,
                            "currency": currency,
                            "trade_date": trade_date,
                            "fee": fee,
                            "tax": Decimal("0"),
                        },
                    )
                )
                continue
            if action == "存款":
                amount = abs(_decimal(data.get("淨金額") or data.get("總額") or "0"))
                note = (data.get("說明") or "").strip() or None
                fingerprint = cash_flow_fingerprint(
                    broker=models.Broker.IB.value,
                    date_=date_only,
                    type_=models.BrokerCashFlowType.DEPOSIT.value,
                    amount=amount,
                    currency=base_currency,
                    note=note,
                )
                rows.append(
                    ParsedRow(
                        row_index=row_index,
                        fingerprint=fingerprint,
                        payload={
                            "_kind": "cash_flow",
                            "broker": models.Broker.IB.value,
                            "date": date_only,
                            "cash_flow_type": models.BrokerCashFlowType.DEPOSIT.value,
                            "amount": amount,
                            "currency": base_currency,
                            "note": note,
                        },
                    )
                )
        except (ValueError, InvalidOperation) as exc:
            errors.append(ParseError(row_index=row_index, message=str(exc)))
            log.debug("broker.ib.row_error", row_index=row_index, reason=str(exc))
    return ParseResult(rows=rows, errors=errors)
