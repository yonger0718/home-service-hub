from __future__ import annotations

import csv
import io
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import structlog

from ..models import portfolio as models
from .cash_flow_service import cash_flow_fingerprint
from sqlalchemy.orm import Session

from .import_service import (
    ParseError,
    ParseResult,
    ParsedRow,
    _as_stored,
    _broker_transaction_fingerprint,
    _row_content_key,
    _trade_content_key,
)

log = structlog.get_logger(__name__)

# Order-ticket suffixes Firstrade appends to the security description
# ("***CAMECO CORP UNSOLICITED EXEC TIME: ... S/D: 10/27/2026").
_NAME_NOISE = re.compile(
    r"\s+(?:UNSOLICITED|PRODUCT DESCRIPTION UNDER SEPARATE COVER|EXEC TIME:|S/D:).*$",
    re.IGNORECASE,
)
# Cash ↔ margin sub-account sweeps: each pair nets to zero inside the broker.
_INTERNAL_TRANSFERS = {"XFER CASH TO MARGIN", "XFER MARGIN TO CASH"}
_WIRE_REBATE_PREFIX = "REBATE FOR WIRE"
_CENT = Decimal("0.01")  # transactions.fee is Numeric(12, 2)


def _decimal(value: str) -> Decimal:
    return Decimal((value or "0").replace(",", "").strip() or "0")


def _trade_date(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%Y/%m/%d").replace(tzinfo=timezone.utc)


def _clean_name(description: str | None) -> str | None:
    if not description:
        return None
    return _NAME_NOISE.sub("", description.lstrip("*")).strip() or None


def _is_clean(description: str | None) -> bool:
    return bool(description) and _clean_name(description) == description


def _sell_fee(quantity: Decimal, price: Decimal, proceeds: Decimal) -> Decimal:
    """SEC fee + FINRA TAF: a sell's 金額 is already net of them (#228).

    A gap under one cent is notional rounding, not a charge, and is left out.
    Buys carry no such fee; their gap is that rounding only.
    """
    fee = (quantity * price - proceeds).quantize(_CENT, rounding=ROUND_HALF_UP)
    return fee if fee >= _CENT else Decimal("0")


def _cash_flow_type(action: str, note: str | None, amount: Decimal) -> tuple[str, Decimal] | None:
    """(type, signed amount) for a cash row, ``None`` for an internal transfer.

    Outflows are stored negative because ``list_balances`` sums amounts as-is
    (same convention as ``routers.cash_flows._normalize_outflow_sign``).
    Raises ``ValueError`` for a row we do not know how to book.
    """
    if action == "存款":
        return models.BrokerCashFlowType.DEPOSIT.value, abs(amount)
    if action == "提款":
        return models.BrokerCashFlowType.WITHDRAWAL.value, -abs(amount)
    if action == "利息收入":
        return models.BrokerCashFlowType.INTEREST.value, amount
    if action == "股息":
        # Amount is already net of the non-resident withholding.
        return models.BrokerCashFlowType.DIVIDEND_CASH.value, amount
    upper_note = (note or "").upper()
    if action == "其他" and upper_note in _INTERNAL_TRANSFERS:
        return None
    if action == "其他" and upper_note.startswith(_WIRE_REBATE_PREFIX):
        # Firstrade refunds the incoming-wire fee; book it as money in.
        return models.BrokerCashFlowType.DEPOSIT.value, abs(amount)
    raise ValueError(f"unsupported Firstrade row: {action} {note or ''}".strip())


def parse(raw_bytes: bytes) -> ParseResult:
    stream = io.StringIO(raw_bytes.decode("utf-8-sig"))
    reader = csv.DictReader(stream)
    rows: list[ParsedRow] = []
    errors: list[ParseError] = []
    clean_names: dict[str, str] = {}
    stripped_names: dict[str, str] = {}
    occurrences: Counter[tuple] = Counter()
    for row_index, raw in enumerate(reader, start=1):
        try:
            action = (raw.get("交易類別") or "").strip()
            date_value = _trade_date((raw.get("日期") or "").strip())
            date_only = date_value.date()
            amount = _decimal(raw.get("金額") or "0")
            note = (raw.get("說明") or "").strip() or None
            if action in {"買進", "賣出"}:
                symbol = (raw.get("代號") or "").strip().upper()
                # Rounded the way Numeric(_, 4) stores them, so the ordinal below,
                # the fingerprint and the stored row all agree (0.47445 -> 0.4745).
                quantity = _as_stored(abs(_decimal(raw.get("數量") or "0")))
                price = _as_stored(_decimal(raw.get("價格") or "0"))
                type_ = "BUY" if action == "買進" else "SELL"
                fee = _sell_fee(quantity, price, amount) if type_ == "SELL" else Decimal("0")
                if _is_clean(note):
                    clean_names.setdefault(symbol, note)
                elif _clean_name(note):
                    stripped_names.setdefault(symbol, _clean_name(note))
                # The description is left out of the fingerprint: the same fill
                # arrives with different wording (manual entry vs CSV ticket text).
                # Identical fills in one file stay distinct via their ordinal.
                occurrences[(symbol, type_, quantity, price, date_only)] += 1
                ordinal = occurrences[(symbol, type_, quantity, price, date_only)]
                fingerprint_note = f"#{ordinal}" if ordinal > 1 else None
                fingerprint = _broker_transaction_fingerprint(
                    broker=models.Broker.FIRSTRADE.value,
                    symbol=symbol,
                    market="US",
                    type_=type_,
                    quantity=quantity,
                    price=price,
                    trade_date=date_value,
                    fee=fee,
                    tax=Decimal("0"),
                    currency="USD",
                    note=fingerprint_note,
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
                            "name": _clean_name(note),
                            "fingerprint_note": fingerprint_note,
                            "type": type_,
                            "position_side": models.PositionSide.LONG.value,
                            "quantity": quantity,
                            "price": price,
                            "currency": "USD",
                            "trade_date": date_value,
                            "fee": fee,
                            "tax": Decimal("0"),
                        },
                    )
                )
                continue
            mapped = _cash_flow_type(action, note, amount)
            if mapped is None:
                log.debug("broker.firstrade.skip", action=action, row_index=row_index)
                continue
            cash_flow_type, amount = mapped
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
    # One name per symbol: a plain description elsewhere in the file beats a
    # stripped ticket line (newest first, the export's order), so holdings do
    # not flip between spellings.
    names = {**stripped_names, **clean_names}
    for row in rows:
        payload = row.payload
        if payload["_kind"] == "transaction" and payload["symbol"] in names:
            payload["name"] = names[payload["symbol"]]
    return ParseResult(rows=rows, errors=errors)


@dataclass(frozen=True)
class SellFeeUpdate:
    transaction_id: int
    symbol: str
    trade_date: datetime
    fee: Decimal


def backfill_sell_fees(
    db: Session, parsed_files: list[ParseResult], *, apply: bool
) -> list[SellFeeUpdate]:
    """Give stored Firstrade sells booked with ``fee=0`` the fee their CSV row implies (#228).

    A re-import cannot do this: content matching treats those sells as duplicates.
    All files share one pool, so each CSV sell pairs with one stored sell of the
    same content key (oldest id first) across the whole run. A row whose
    fingerprint an earlier file already had is the same fill from an overlapping
    export and is skipped. Only a stored fee of 0 is changed, so a second run
    changes nothing.
    """
    stored: dict[tuple, list[models.Transaction]] = defaultdict(list)
    for tx in (
        db.query(models.Transaction)
        .filter(models.Transaction.broker == models.Broker.FIRSTRADE.value)
        .filter(models.Transaction.type == models.TransactionType.SELL)
        .order_by(models.Transaction.id)
    ):
        stored[
            _trade_content_key(tx.broker, tx.symbol, tx.type, tx.quantity, tx.price, tx.trade_date)
        ].append(tx)
    updates: list[SellFeeUpdate] = []
    seen: set[str] = set()
    for parsed in parsed_files:
        fingerprints: set[str] = set()
        for row in parsed.rows:
            payload = row.payload
            if payload["_kind"] != "transaction" or payload["type"] != "SELL":
                continue
            if row.fingerprint in seen:
                continue
            fingerprints.add(row.fingerprint)
            candidates = stored.get(_row_content_key(payload))
            if not candidates:
                continue
            tx = candidates.pop(0)
            if payload["fee"] <= 0 or Decimal(tx.fee or 0) != 0:
                continue
            updates.append(
                SellFeeUpdate(transaction_id=tx.id, symbol=tx.symbol, trade_date=tx.trade_date, fee=payload["fee"])
            )
            if apply:
                tx.fee = payload["fee"]
        seen |= fingerprints
    if apply:
        db.commit()
    return updates
