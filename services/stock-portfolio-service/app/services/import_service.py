"""CSV importers for transactions + dividends.

Mirrors stonk's importer pattern (apps/api/src/finapp/importers/manual_csv.py)
but maps to home-hub's flat transactions / dividends schema instead of the
unified-ledger StagedEntry model. Idempotency comes from a SHA256 fingerprint
over the canonical row, stored in ``transactions.import_fingerprint`` /
``dividends.import_fingerprint`` (UNIQUE) — re-uploading the same CSV is a
no-op.

CSV schemas:

- transactions.csv: ``symbol,type,quantity,price,trade_date,fee,tax,name``
- dividends.csv:    ``symbol,amount,ex_dividend_date,received_date``

Datetime columns accept any ISO 8601 string; naive values are interpreted as
UTC. Empty optional cells become NULL (``name``, ``received_date``) or zero
(``fee``, ``tax``).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256

from sqlalchemy.orm import Session

from ..models import portfolio as models
from . import portfolio_service as svc

TRANSACTION_FIELDS = (
    "symbol",
    "type",
    "quantity",
    "price",
    "trade_date",
    "fee",
    "tax",
    "name",
)
DIVIDEND_FIELDS = ("symbol", "amount", "ex_dividend_date", "received_date")
SOURCE_TRANSACTIONS = "manual-csv-v1:transactions"
SOURCE_DIVIDENDS = "manual-csv-v1:dividends"


@dataclass
class ParsedRow:
    row_index: int  # 1-based, for human-readable error messages
    fingerprint: str
    payload: dict


@dataclass
class ParseError:
    row_index: int
    message: str


@dataclass
class ParseResult:
    rows: list[ParsedRow]
    errors: list[ParseError]


@dataclass
class ImportResult:
    parsed: int
    created: int
    skipped_duplicates: int
    errors: list[ParseError]
    dry_run: bool
    created_ids: list[int] = field(default_factory=list)


def _required(row: dict, key: str, row_index: int) -> str:
    value = (row.get(key) or "").strip()
    if not value:
        raise ValueError(f"row {row_index}: column '{key}' is required")
    return value


def _decimal(value: str, key: str, row_index: int) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(
            f"row {row_index}: column '{key}' is not a decimal: {value!r}"
        ) from exc


def _parse_datetime(value: str, key: str, row_index: int) -> datetime:
    """Parse an ISO 8601 timestamp; naive values default to UTC."""

    try:
        normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(
            f"row {row_index}: column '{key}' is not ISO 8601: {value!r}"
        ) from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _validate_header(fieldnames: list[str] | None, expected: tuple[str, ...]) -> None:
    if fieldnames is None:
        raise ValueError("CSV is empty")
    actual = tuple(name.strip() for name in fieldnames)
    if actual != expected:
        raise ValueError(
            "CSV header must be: "
            + ",".join(expected)
            + " (got: "
            + ",".join(actual)
            + ")"
        )


def _transaction_fingerprint(
    symbol: str,
    type_: str,
    quantity: int,
    price: Decimal,
    trade_date: datetime,
    fee: Decimal,
    tax: Decimal,
) -> str:
    """SHA256 over the canonical row; identical CSV rows produce identical hashes."""

    canonical = "|".join(
        (
            SOURCE_TRANSACTIONS,
            symbol,
            type_,
            str(quantity),
            f"{price:.4f}",
            trade_date.astimezone(timezone.utc).isoformat(),
            f"{fee:.4f}",
            f"{tax:.4f}",
        )
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _dividend_fingerprint(
    symbol: str, amount: Decimal, ex_dividend_date: datetime
) -> str:
    canonical = "|".join(
        (
            SOURCE_DIVIDENDS,
            symbol,
            f"{amount:.4f}",
            ex_dividend_date.astimezone(timezone.utc).isoformat(),
        )
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def parse_transactions_csv(raw: bytes) -> ParseResult:
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    _validate_header(reader.fieldnames, TRANSACTION_FIELDS)
    rows: list[ParsedRow] = []
    errors: list[ParseError] = []
    for row_index, raw_row in enumerate(reader, start=1):
        try:
            symbol = svc.sanitize_symbol(_required(raw_row, "symbol", row_index))
            if not symbol:
                raise ValueError(f"row {row_index}: 'symbol' is blank after normalization")
            type_ = _required(raw_row, "type", row_index).upper()
            if type_ not in {"BUY", "SELL"}:
                raise ValueError(
                    f"row {row_index}: 'type' must be BUY or SELL (got {type_!r})"
                )
            quantity_raw = _required(raw_row, "quantity", row_index)
            try:
                quantity = int(quantity_raw)
            except ValueError as exc:
                raise ValueError(
                    f"row {row_index}: 'quantity' must be an integer (got {quantity_raw!r})"
                ) from exc
            if quantity <= 0:
                raise ValueError(f"row {row_index}: 'quantity' must be positive")
            price = _decimal(
                _required(raw_row, "price", row_index), "price", row_index
            )
            if price <= 0:
                raise ValueError(f"row {row_index}: 'price' must be positive")
            trade_date = _parse_datetime(
                _required(raw_row, "trade_date", row_index),
                "trade_date",
                row_index,
            )
            fee = _decimal((raw_row.get("fee") or "0").strip() or "0", "fee", row_index)
            tax = _decimal((raw_row.get("tax") or "0").strip() or "0", "tax", row_index)
            if fee < 0:
                raise ValueError(f"row {row_index}: 'fee' must be non-negative")
            if tax < 0:
                raise ValueError(f"row {row_index}: 'tax' must be non-negative")
            name = (raw_row.get("name") or "").strip() or None

            fingerprint = _transaction_fingerprint(
                symbol, type_, quantity, price, trade_date, fee, tax
            )
            rows.append(
                ParsedRow(
                    row_index=row_index,
                    fingerprint=fingerprint,
                    payload={
                        "symbol": symbol,
                        "type": type_,
                        "quantity": quantity,
                        "price": price,
                        "trade_date": trade_date,
                        "fee": fee,
                        "tax": tax,
                        "name": name,
                    },
                )
            )
        except ValueError as exc:
            errors.append(ParseError(row_index=row_index, message=str(exc)))
    return ParseResult(rows=rows, errors=errors)


def parse_dividends_csv(raw: bytes) -> ParseResult:
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    _validate_header(reader.fieldnames, DIVIDEND_FIELDS)
    rows: list[ParsedRow] = []
    errors: list[ParseError] = []
    for row_index, raw_row in enumerate(reader, start=1):
        try:
            symbol = svc.sanitize_symbol(_required(raw_row, "symbol", row_index))
            if not symbol:
                raise ValueError(f"row {row_index}: 'symbol' is blank after normalization")
            amount = _decimal(
                _required(raw_row, "amount", row_index), "amount", row_index
            )
            if amount <= 0:
                raise ValueError(f"row {row_index}: 'amount' must be positive")
            ex_dividend_date = _parse_datetime(
                _required(raw_row, "ex_dividend_date", row_index),
                "ex_dividend_date",
                row_index,
            )
            received_raw = (raw_row.get("received_date") or "").strip()
            received_date = (
                _parse_datetime(received_raw, "received_date", row_index)
                if received_raw
                else None
            )

            fingerprint = _dividend_fingerprint(symbol, amount, ex_dividend_date)
            rows.append(
                ParsedRow(
                    row_index=row_index,
                    fingerprint=fingerprint,
                    payload={
                        "symbol": symbol,
                        "amount": amount,
                        "ex_dividend_date": ex_dividend_date,
                        "received_date": received_date,
                    },
                )
            )
        except ValueError as exc:
            errors.append(ParseError(row_index=row_index, message=str(exc)))
    return ParseResult(rows=rows, errors=errors)


def _persist_transaction(db: Session, row: ParsedRow) -> models.Transaction:
    """Insert one parsed transaction.

    Reuses the private validation + day-trade recompute helpers from
    ``portfolio_service`` so imported rows behave identically to API-created
    rows. The fingerprint is set during INSERT (single atomic commit) so a
    concurrent re-upload of the same CSV always loses the UNIQUE race
    cleanly.
    """

    payload = dict(row.payload)
    payload["type"] = models.TransactionType(payload["type"])
    svc._validate_transaction_ledger(db, payload)
    db_tx = models.Transaction(
        symbol=payload["symbol"],
        name=payload["name"],
        type=payload["type"],
        quantity=payload["quantity"],
        price=payload["price"],
        trade_date=payload["trade_date"],
        fee=payload["fee"],
        tax=payload["tax"],
        import_fingerprint=row.fingerprint,
    )
    db.add(db_tx)
    db.flush()
    svc._recompute_day_trade_flags(
        db, db_tx.symbol, svc._trade_calendar_date(db_tx.trade_date)
    )
    db.commit()
    db.refresh(db_tx)
    return db_tx


def _persist_dividend(db: Session, row: ParsedRow) -> models.Dividend:
    payload = row.payload
    db_div = models.Dividend(
        symbol=payload["symbol"],
        amount=payload["amount"],
        ex_dividend_date=payload["ex_dividend_date"],
        received_date=payload.get("received_date"),
        import_fingerprint=row.fingerprint,
    )
    db.add(db_div)
    db.commit()
    db.refresh(db_div)
    return db_div


def commit_transactions(
    db: Session, parsed: ParseResult, *, dry_run: bool
) -> ImportResult:
    existing: set[str] = {
        fingerprint
        for (fingerprint,) in db.query(models.Transaction.import_fingerprint)
        .filter(models.Transaction.import_fingerprint.is_not(None))
        .all()
    }
    seen_in_batch: set[str] = set()
    created_ids: list[int] = []
    errors = list(parsed.errors)
    skipped = 0
    for row in parsed.rows:
        if row.fingerprint in existing or row.fingerprint in seen_in_batch:
            skipped += 1
            continue
        seen_in_batch.add(row.fingerprint)
        if dry_run:
            continue
        try:
            db_tx = _persist_transaction(db, row)
        except ValueError as exc:
            errors.append(ParseError(row_index=row.row_index, message=str(exc)))
            continue
        created_ids.append(db_tx.id)
    return ImportResult(
        parsed=len(parsed.rows),
        created=len(created_ids),
        skipped_duplicates=skipped,
        errors=errors,
        dry_run=dry_run,
        created_ids=created_ids,
    )


def commit_dividends(
    db: Session, parsed: ParseResult, *, dry_run: bool
) -> ImportResult:
    existing: set[str] = {
        fingerprint
        for (fingerprint,) in db.query(models.Dividend.import_fingerprint)
        .filter(models.Dividend.import_fingerprint.is_not(None))
        .all()
    }
    seen_in_batch: set[str] = set()
    created_ids: list[int] = []
    errors = list(parsed.errors)
    skipped = 0
    for row in parsed.rows:
        if row.fingerprint in existing or row.fingerprint in seen_in_batch:
            skipped += 1
            continue
        seen_in_batch.add(row.fingerprint)
        if dry_run:
            continue
        db_div = _persist_dividend(db, row)
        created_ids.append(db_div.id)
    return ImportResult(
        parsed=len(parsed.rows),
        created=len(created_ids),
        skipped_duplicates=skipped,
        errors=errors,
        dry_run=dry_run,
        created_ids=created_ids,
    )
