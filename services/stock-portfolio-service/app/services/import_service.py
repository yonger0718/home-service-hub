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
from typing import Literal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import portfolio as models
from . import portfolio_service as svc
from .per_date_verify import OverrideValidation

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

# Traditional Chinese column-name synonyms → canonical English keys.
# Lets a Taiwan-localised CSV ship "代號,類別,股數,..." instead of the
# canonical English headers.
#
# `order_id` is intentionally NOT in TRANSACTION_FIELDS — it's an optional
# extension column folded into the fingerprint hash when present. Keeping it
# out of TRANSACTION_FIELDS preserves: (a) byte-for-byte hash compatibility
# with pre-feature CSVs that have no order_id column, and (b) the no-header
# CSV mode (which auto-prepends TRANSACTION_FIELDS as the canonical header).
TRANSACTION_HEADER_SYNONYMS = {
    "代號": "symbol", "代碼": "symbol", "股票代號": "symbol",
    "類別": "type", "買賣別": "type", "交易類別": "type",
    "股數": "quantity", "數量": "quantity",
    "價格": "price", "成交價": "price", "單價": "price",
    "交易日期": "trade_date", "日期": "trade_date", "成交日": "trade_date",
    "手續費": "fee",
    "稅金": "tax", "證交稅": "tax",
    "名稱": "name", "股票名稱": "name",
    "order_id": "order_id",
    "委託書號": "order_id", "訂單編號": "order_id", "委託編號": "order_id",
}
DIVIDEND_HEADER_SYNONYMS = {
    "代號": "symbol", "代碼": "symbol", "股票代號": "symbol",
    "金額": "amount", "股利金額": "amount",
    "除息日": "ex_dividend_date", "除息日期": "ex_dividend_date",
    "入帳日": "received_date", "入帳日期": "received_date",
}

# Type column value synonyms (e.g. "買進" → "BUY").
TYPE_VALUE_SYNONYMS = {
    "買進": "BUY", "買": "BUY", "現買": "BUY",
    "賣出": "SELL", "賣": "SELL", "現賣": "SELL",
}


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
class UnresolvedName:
    name: str
    occurrences: int
    sample_dates: list[str]


@dataclass
class ParseResult:
    rows: list[ParsedRow]
    errors: list[ParseError]
    unresolved_names: list[UnresolvedName] = field(default_factory=list)


@dataclass
class ImportResult:
    parsed: int
    created: int
    skipped_duplicates: int
    errors: list[ParseError]
    dry_run: bool
    created_ids: list[int] = field(default_factory=list)
    rehashed: int = 0
    skipped_unresolved: int = 0
    skipped_unverified: int = 0
    unresolved_names: list[UnresolvedName] = field(default_factory=list)
    override_validations: list[OverrideValidation] = field(default_factory=list)
    would_rehash: int = 0
    would_insert: int = 0
    would_skip_duplicate: int = 0


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


def _normalize_header(
    fieldnames: list[str] | None,
    expected: tuple[str, ...],
    synonyms: dict[str, str],
) -> dict[str, str]:
    """Return mapping {source-column → canonical-column}.

    Accepts canonical English names OR any localised synonym. Raises if a
    required canonical column cannot be mapped from the source header.
    """
    if not fieldnames:
        raise ValueError("CSV is empty")
    mapping: dict[str, str] = {}
    for raw in fieldnames:
        if raw is None:
            continue
        key = raw.strip()
        if not key:
            continue
        canonical = key if key in expected else synonyms.get(key)
        if canonical is None:
            # Unknown column — ignore (lenient) rather than reject, so brokers
            # that include extra metadata columns still import.
            continue
        mapping[raw] = canonical
    missing = [col for col in expected if col not in mapping.values()]
    # Optional columns are tolerated; required columns are not.
    required = {
        TRANSACTION_FIELDS: {"symbol", "type", "quantity", "price", "trade_date"},
        DIVIDEND_FIELDS: {"symbol", "amount", "ex_dividend_date"},
    }[expected]
    missing_required = [col for col in missing if col in required]
    if missing_required:
        raise ValueError(
            "CSV header missing required column(s): "
            + ",".join(missing_required)
            + " (accepted English names: " + ",".join(expected) + ")"
        )
    return mapping


def _remap_row(row: dict, mapping: dict[str, str]) -> dict:
    """Translate one DictReader row from source-columns to canonical-columns."""
    out: dict[str, str] = {}
    for source_key, value in row.items():
        canonical = mapping.get(source_key)
        if canonical is not None:
            out[canonical] = value
    return out


def _prepend_canonical_header(raw: bytes, expected: tuple[str, ...]) -> bytes:
    """Insert a canonical English header at the top of a header-less CSV."""
    header = (",".join(expected) + "\n").encode("utf-8")
    # Strip UTF-8 BOM if present so we don't end up with BOM mid-stream.
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return header + raw


def detect_csv_format(raw: bytes) -> Literal["generic", "cathay"]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        # Surface as ValueError so the router translates to 400 instead of 500.
        raise ValueError("CSV must be UTF-8 encoded") from exc
    first_non_empty = next((line for line in text.splitlines() if line.strip()), "")
    return "cathay" if first_non_empty.startswith("根據您篩選的結果") else "generic"


def _transaction_fingerprint(
    symbol: str,
    type_: str,
    quantity: int,
    price: Decimal,
    trade_date: datetime,
    fee: Decimal,
    tax: Decimal,
    order_id: str | None = None,
    position_side: str | None = None,
) -> str:
    """SHA256 over the canonical row; identical CSV rows produce identical hashes.

    When `order_id` is supplied (typically from a Taiwan broker export's
    ``委託書號`` column), it is appended as ``|order_id=<value>`` so that
    otherwise-identical same-day fills produce distinct fingerprints.

    When `position_side` is supplied AND not ``"LONG"`` (the default), it is
    appended as ``|side=SHORT``. LONG is omitted so legacy long-only rows
    keep the original hash and rehash cleanly against pre-position_side DB
    state. SHORT differentiates 券買/券賣 rows from same-key 現買/現賣.
    """

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
    if order_id:
        canonical += f"|order_id={order_id}"
    if position_side and position_side != "LONG":
        canonical += f"|side={position_side}"
    return sha256(canonical.encode("utf-8")).hexdigest()


def _dividend_fingerprint(
    symbol: str,
    amount: Decimal,
    ex_dividend_date: datetime,
    received_date: datetime | None,
) -> str:
    canonical = "|".join(
        (
            SOURCE_DIVIDENDS,
            symbol,
            f"{amount:.4f}",
            ex_dividend_date.astimezone(timezone.utc).isoformat(),
            received_date.astimezone(timezone.utc).isoformat()
            if received_date is not None
            else "",
        )
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def parse_transactions_csv(raw: bytes, *, has_header: bool = True) -> ParseResult:
    if not has_header:
        raw = _prepend_canonical_header(raw, TRANSACTION_FIELDS)
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    header_map = _normalize_header(
        reader.fieldnames, TRANSACTION_FIELDS, TRANSACTION_HEADER_SYNONYMS
    )
    rows: list[ParsedRow] = []
    errors: list[ParseError] = []
    for row_index, source_row in enumerate(reader, start=1):
        raw_row = _remap_row(source_row, header_map)
        try:
            symbol = svc.sanitize_symbol(_required(raw_row, "symbol", row_index))
            if not symbol:
                raise ValueError(f"row {row_index}: 'symbol' is blank after normalization")
            type_raw = _required(raw_row, "type", row_index).strip()
            type_ = TYPE_VALUE_SYNONYMS.get(type_raw, type_raw.upper())
            if type_ not in {"BUY", "SELL"}:
                raise ValueError(
                    f"row {row_index}: 'type' must be BUY/SELL or 買進/賣出 (got {type_raw!r})"
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
            order_id = (raw_row.get("order_id") or "").strip() or None

            fingerprint = _transaction_fingerprint(
                symbol, type_, quantity, price, trade_date, fee, tax, order_id
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
                        "order_id": order_id,
                    },
                )
            )
        except ValueError as exc:
            errors.append(ParseError(row_index=row_index, message=str(exc)))
    return ParseResult(rows=rows, errors=errors)


def parse_dividends_csv(raw: bytes, *, has_header: bool = True) -> ParseResult:
    if not has_header:
        raw = _prepend_canonical_header(raw, DIVIDEND_FIELDS)
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    header_map = _normalize_header(
        reader.fieldnames, DIVIDEND_FIELDS, DIVIDEND_HEADER_SYNONYMS
    )
    rows: list[ParsedRow] = []
    errors: list[ParseError] = []
    for row_index, source_row in enumerate(reader, start=1):
        raw_row = _remap_row(source_row, header_map)
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

            fingerprint = _dividend_fingerprint(
                symbol, amount, ex_dividend_date, received_date
            )
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
        except IntegrityError:
            # Another request inserted the same fingerprint between snapshot
            # and our flush — treat as duplicate, keep session usable.
            db.rollback()
            skipped += 1
            continue
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
        rehashed=0,
        skipped_unresolved=0,
        skipped_unverified=0,
        override_validations=[],
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
        try:
            db_div = _persist_dividend(db, row)
        except IntegrityError:
            db.rollback()
            skipped += 1
            continue
        created_ids.append(db_div.id)
    return ImportResult(
        parsed=len(parsed.rows),
        created=len(created_ids),
        skipped_duplicates=skipped,
        errors=errors,
        dry_run=dry_run,
        created_ids=created_ids,
        rehashed=0,
        skipped_unresolved=0,
        skipped_unverified=0,
        override_validations=[],
    )
