"""國泰證券日對帳單 semantics: category mapping, symbol resolution, fingerprint."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from ..models import portfolio as models
from ..schemas.cathay_statement import Holding, StatementPayload
from . import broker_cathay_service
from .broker_cathay_service import CATHAY_SIDE_MAP
from .import_service import ParsedRow, _broker_transaction_fingerprint

STATEMENT_BROKER = "TW_CATHAY_STATEMENT"

CATEGORY_TO_CSV_SIDE: dict[str, str] = {
    "集買": "現買",
    "集賣": "現賣",
    "沖買": "沖買",
    "沖賣": "沖賣",
}


@dataclass
class Mapped:
    rows: list[ParsedRow]
    canonical: list[dict]


@dataclass
class HeldReason:
    code: str
    name: str | None


def resolve_line_symbol(db: Session, name: str, holdings: list[Holding]) -> str | None:
    try:
        return broker_cathay_service.resolve_symbol(name)
    except ValueError:
        pass
    stored = {
        symbol
        for (symbol,) in db.query(models.Transaction.symbol)
        .filter(models.Transaction.name == name, models.Transaction.market == "TW")
        .distinct()
    }
    if len(stored) == 1:
        return stored.pop()
    codes = {h.code for h in holdings if h.name == name}
    if len(codes) == 1:
        return codes.pop()
    return None


def statement_fingerprint(row: ParsedRow, order_no: str) -> str:
    p = row.payload
    return _broker_transaction_fingerprint(
        broker=STATEMENT_BROKER,
        symbol=p["symbol"],
        market="TW",
        type_=p["type"],
        quantity=Decimal(p["quantity"]),
        price=p["price"],
        trade_date=p["trade_date"],
        fee=p["fee"],
        tax=p["tax"],
        currency="TWD",
        note=order_no,
    )


def map_statement(db: Session, payload: StatementPayload) -> Mapped | HeldReason:
    if payload.unsupported:
        return HeldReason("unsupported_category", None)
    trade_date = datetime(
        payload.trade_date.year, payload.trade_date.month, payload.trade_date.day, tzinfo=timezone.utc
    )
    rows: list[ParsedRow] = []
    canonical: list[dict] = []
    for index, line in enumerate(payload.lines, start=1):
        symbol = resolve_line_symbol(db, line.name, payload.holdings)
        if symbol is None:
            return HeldReason("unresolved_symbol", line.name)
        side = CATEGORY_TO_CSV_SIDE[line.category]
        type_, position_side = CATHAY_SIDE_MAP[side]
        marker = side if side in ("沖買", "沖賣") else None
        row = ParsedRow(
            row_index=index,
            fingerprint="",
            payload={
                "symbol": symbol,
                "type": type_,
                "position_side": position_side,
                "quantity": line.quantity,
                "price": line.price,
                "trade_date": trade_date,
                "fee": line.fee,
                "tax": line.tax,
                "name": line.name,
                "order_id": line.order_no[:-2],
                "broker_subtype": side[0],
                "broker_day_trade_marker": marker,
            },
        )
        row.fingerprint = statement_fingerprint(row, line.order_no)
        rows.append(row)
        canonical.append(
            {
                "symbol": symbol,
                "type": type_,
                "position_side": position_side,
                "marker": marker,
                "quantity": line.quantity,
                "price": str(line.price),
                "fee": str(line.fee),
                "tax": str(line.tax),
                "order_no": line.order_no,
            }
        )
    return Mapped(rows=rows, canonical=canonical)
