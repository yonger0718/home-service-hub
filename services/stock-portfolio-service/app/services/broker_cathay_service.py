from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import portfolio as models
from . import per_date_verify, portfolio_service as svc
from .import_service import (
    ImportResult,
    ParseError,
    ParseResult,
    ParsedRow,
    UnresolvedName,
    _transaction_fingerprint,
)

CATHAY_SIDE_MAP = {
    "現買": "BUY",
    "資買": "BUY",
    "券買": "BUY",
    "沖買": "BUY",
    "現賣": "SELL",
    "資賣": "SELL",
    "券賣": "SELL",
    "沖賣": "SELL",
}

_DATA_PATH = Path(__file__).resolve().parents[1] / "data/name_to_symbol.json"


def _load_name_to_symbol() -> dict[str, list[str]]:
    symbol_to_name = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    reverse: defaultdict[str, list[str]] = defaultdict(list)
    for symbol, name in symbol_to_name.items():
        reverse[name].append(symbol)
    return dict(reverse)


NAME_TO_SYMBOL: dict[str, list[str]] = _load_name_to_symbol()


def resolve_symbol(name: str, overrides: dict[str, str] | None = None) -> str:
    if overrides and name in overrides:
        return overrides[name]
    candidates = NAME_TO_SYMBOL.get(name, [])
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) >= 2:
        raise ValueError(f"ambiguous symbol for 股名='{name}': {candidates}")
    raise ValueError(f"cannot resolve symbol for 股名='{name}'")


def _required(row: dict[str, str | None], key: str, row_index: int) -> str:
    value = (row.get(key) or "").strip()
    if not value:
        raise ValueError(f"row {row_index}: column '{key}' is required")
    return value


def _parse_decimal(value: str, key: str, row_index: int) -> Decimal:
    try:
        return Decimal(value.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(
            f"row {row_index}: column '{key}' is not a decimal: {value!r}"
        ) from exc


def _parse_quantity(value: str, row_index: int) -> int:
    normalized = value.replace(",", "")
    try:
        quantity = int(normalized)
    except ValueError as exc:
        raise ValueError(
            f"row {row_index}: column '成交股數' must be an integer (got {value!r})"
        ) from exc
    if quantity <= 0:
        raise ValueError(f"row {row_index}: column '成交股數' must be positive")
    return quantity


def _parse_trade_date(value: str, row_index: int) -> datetime:
    try:
        parsed = datetime.strptime(value, "%Y/%m/%d")
    except ValueError as exc:
        raise ValueError(
            f"row {row_index}: column '日期' is not YYYY/MM/DD: {value!r}"
        ) from exc
    return parsed.replace(tzinfo=timezone.utc)


def parse_cathay_rows(
    raw_bytes: bytes,
    *,
    name_overrides: dict[str, str] | None = None,
) -> ParseResult:
    stream = io.StringIO(raw_bytes.decode("utf-8-sig"))
    stream.readline()
    reader = csv.DictReader(stream)
    rows: list[ParsedRow] = []
    errors: list[ParseError] = []
    unresolved_occurrences: defaultdict[str, int] = defaultdict(int)
    unresolved_dates: defaultdict[str, set[str]] = defaultdict(set)
    for row_index, raw_row in enumerate(reader, start=1):
        try:
            name = _required(raw_row, "股名", row_index)
            trade_date = _parse_trade_date(_required(raw_row, "日期", row_index), row_index)
            try:
                symbol = resolve_symbol(name, name_overrides)
            except ValueError as exc:
                if "cannot resolve" in str(exc):
                    unresolved_occurrences[name] += 1
                    unresolved_dates[name].add(trade_date.date().isoformat())
                    continue
                raise
            side = _required(raw_row, "買賣別", row_index)
            type_ = CATHAY_SIDE_MAP.get(side)
            if type_ is None:
                raise ValueError(f"row {row_index}: unsupported 買賣別 {side!r}")
            quantity = _parse_quantity(_required(raw_row, "成交股數", row_index), row_index)
            price = _parse_decimal(_required(raw_row, "成交價", row_index), "成交價", row_index)
            if price <= 0:
                raise ValueError(f"row {row_index}: column '成交價' must be positive")
            fee = _parse_decimal((raw_row.get("手續費") or "0").strip() or "0", "手續費", row_index)
            tax = _parse_decimal((raw_row.get("交易稅") or "0").strip() or "0", "交易稅", row_index)
            if fee < 0:
                raise ValueError(f"row {row_index}: column '手續費' must be non-negative")
            if tax < 0:
                raise ValueError(f"row {row_index}: column '交易稅' must be non-negative")
            order_id = (raw_row.get("委託書號") or "").strip() or None
            fingerprint = _transaction_fingerprint(
                symbol,
                type_,
                quantity,
                price,
                trade_date,
                fee,
                tax,
                order_id=order_id,
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
                        "broker_subtype": side[0],
                    },
                )
            )
        except ValueError as exc:
            errors.append(ParseError(row_index=row_index, message=str(exc)))
    unresolved_names = [
        UnresolvedName(
            name=name,
            occurrences=occurrences,
            sample_dates=sorted(unresolved_dates[name], reverse=True)[:3],
        )
        for name, occurrences in unresolved_occurrences.items()
    ]
    return ParseResult(rows=rows, errors=errors, unresolved_names=unresolved_names)


def _business_key_match(
    db: Session,
    row: ParsedRow,
    claimed_ids: set[int],
    batch_fingerprints: set[str],
) -> models.Transaction | None:
    """Fallback rehash key: match an existing row by symbol+type+qty+price+fee+tax on
    the same calendar date, ignoring the stored time-of-day. Pre-cathay rows were
    inserted via manual entry / older importers that set trade_date to the moment of
    entry (e.g. 13:30 TPE), so the legacy fingerprint — which hashes the full
    datetime — would never match a cathay parse (which uses 00:00 UTC). Pop the first
    unclaimed candidate; the caller is responsible for tracking ids it has already
    paired so repeated CSV rows of an identical business key pair 1:1 with DB rows."""
    payload = row.payload
    date_only = payload["trade_date"].date()
    candidates = (
        db.query(models.Transaction)
        .filter(
            models.Transaction.symbol == payload["symbol"],
            models.Transaction.type == models.TransactionType(payload["type"]),
            models.Transaction.quantity == payload["quantity"],
            models.Transaction.price == payload["price"],
            models.Transaction.fee == payload["fee"],
            models.Transaction.tax == payload["tax"],
            func.date(models.Transaction.trade_date) == date_only,
        )
        .order_by(models.Transaction.id)
        .all()
    )
    for cand in candidates:
        if cand.id in claimed_ids:
            continue
        if cand.import_fingerprint == row.fingerprint:
            # Already on the new scheme — let the duplicate-check branch handle it
            # so the row is counted as a skip, not a rehash.
            return None
        if cand.import_fingerprint in batch_fingerprints:
            # Candidate's fingerprint is one this batch will produce — meaning it was
            # inserted (or rehashed) earlier in this loop, not a stale legacy row.
            # Skipping prevents same-day "twin" CSV rows (different order_id, identical
            # business key) from cannibalising each other's inserts.
            continue
        return cand
    return None


def _legacy_fingerprint(row: ParsedRow) -> str:
    payload = row.payload
    return _transaction_fingerprint(
        payload["symbol"],
        payload["type"],
        payload["quantity"],
        payload["price"],
        payload["trade_date"],
        payload["fee"],
        payload["tax"],
    )


def _insert_transaction(db: Session, row: ParsedRow) -> models.Transaction:
    # Broker statement is canonical: every row was filled, so the ledger guard the
    # generic path uses (which forbids selling without holdings) would wrongly reject
    # 融券/沖賣 short opens. Trust the statement and skip ledger validation here.
    payload = dict(row.payload)
    payload["type"] = models.TransactionType(payload["type"])
    tx = models.Transaction(
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
    db.add(tx)
    db.flush()
    svc._recompute_day_trade_flags(
        db, tx.symbol, svc._trade_calendar_date(tx.trade_date)
    )
    return tx


def _dry_run_rehash(db: Session, parsed: ParseResult) -> ImportResult:
    existing = {
        fingerprint
        for (fingerprint,) in db.query(models.Transaction.import_fingerprint)
        .filter(models.Transaction.import_fingerprint.is_not(None))
        .all()
    }
    available_legacy = set(existing)
    simulated_fingerprints = set(existing)
    would_rehash = 0
    would_insert = 0
    would_skip_duplicate = 0
    for row in parsed.rows:
        legacy_fp = _legacy_fingerprint(row)
        new_fp = row.fingerprint
        if legacy_fp in available_legacy:
            would_rehash += 1
            available_legacy.remove(legacy_fp)
            simulated_fingerprints.discard(legacy_fp)
            simulated_fingerprints.add(new_fp)
            continue
        if new_fp in simulated_fingerprints:
            would_skip_duplicate += 1
            continue
        would_insert += 1
        simulated_fingerprints.add(new_fp)
    return ImportResult(
        parsed=len(parsed.rows),
        created=0,
        skipped_duplicates=0,
        errors=list(parsed.errors),
        dry_run=True,
        created_ids=[],
        skipped_unresolved=sum(
            unresolved.occurrences for unresolved in parsed.unresolved_names
        ),
        unresolved_names=parsed.unresolved_names,
        would_rehash=would_rehash,
        would_insert=would_insert,
        would_skip_duplicate=would_skip_duplicate,
    )


def _commit_rehash(db: Session, parsed: ParseResult) -> ImportResult:
    if parsed.errors:
        db.rollback()
        return ImportResult(
            parsed=len(parsed.rows),
            created=0,
            skipped_duplicates=0,
            errors=list(parsed.errors),
            dry_run=False,
            created_ids=[],
            skipped_unresolved=sum(
                unresolved.occurrences for unresolved in parsed.unresolved_names
            ),
            unresolved_names=parsed.unresolved_names,
        )

    created_ids: list[int] = []
    errors: list[ParseError] = []
    created = 0
    skipped_duplicates = 0
    rehashed = 0
    if db.in_transaction():
        db.rollback()
    claimed_ids: set[int] = set()
    batch_fingerprints = {row.fingerprint for row in parsed.rows}
    try:
        with db.begin():
            for row in parsed.rows:
                legacy_fp = _legacy_fingerprint(row)
                existing = (
                    db.query(models.Transaction)
                    .filter_by(import_fingerprint=legacy_fp)
                    .one_or_none()
                )
                if existing is not None:
                    existing.import_fingerprint = row.fingerprint
                    db.flush()
                    rehashed += 1
                    continue
                duplicate = (
                    db.query(models.Transaction)
                    .filter_by(import_fingerprint=row.fingerprint)
                    .one_or_none()
                )
                if duplicate is not None:
                    skipped_duplicates += 1
                    continue
                business_match = _business_key_match(db, row, claimed_ids, batch_fingerprints)
                if business_match is not None:
                    business_match.import_fingerprint = row.fingerprint
                    # Normalize trade_date to the parsed value so the column matches
                    # what future fingerprints will hash; day-trade detection already
                    # strips time so this is harmless to downstream logic.
                    business_match.trade_date = row.payload["trade_date"]
                    claimed_ids.add(business_match.id)
                    db.flush()
                    rehashed += 1
                    continue
                tx = _insert_transaction(db, row)
                created += 1
                created_ids.append(tx.id)
    except ValueError as exc:
        db.rollback()
        errors.append(ParseError(row_index=0, message=str(exc)))
        created_ids.clear()
        created = 0
        rehashed = 0
    except IntegrityError as exc:
        db.rollback()
        errors.append(ParseError(row_index=0, message=str(exc)))
        created_ids.clear()
        created = 0
        rehashed = 0
    return ImportResult(
        parsed=len(parsed.rows),
        created=created,
        skipped_duplicates=skipped_duplicates,
        errors=errors,
        dry_run=False,
        created_ids=created_ids,
        rehashed=rehashed,
        skipped_unresolved=sum(
            unresolved.occurrences for unresolved in parsed.unresolved_names
        ),
        unresolved_names=parsed.unresolved_names,
    )


def parse_cathay_transactions_csv(
    raw_bytes: bytes,
    *,
    dry_run: bool,
    db: Session,
    name_overrides: dict[str, str] | None = None,
    confirmed_overrides: set[str] | None = None,
) -> ImportResult:
    parsed = parse_cathay_rows(raw_bytes, name_overrides=name_overrides)
    name_to_earliest_date: dict[str, date] = {}
    for row in parsed.rows:
        name = row.payload["name"]
        trade_date = row.payload["trade_date"].date()
        current_earliest = name_to_earliest_date.get(name)
        if current_earliest is None or trade_date < current_earliest:
            name_to_earliest_date[name] = trade_date
    name_to_code = {
        name: code
        for name, code in (name_overrides or {}).items()
        if name in name_to_earliest_date
    }
    validations = (
        per_date_verify.verify_overrides(
            name_to_code=name_to_code,
            name_to_earliest_date=name_to_earliest_date,
            confirmed=confirmed_overrides or set(),
        )
        if name_to_code
        else []
    )
    verified_names = {
        validation.name
        for validation in validations
        if validation.status in {"verified", "user_overridden"}
    }
    unverified_names = set(name_to_code) - verified_names
    filtered_rows = [
        row for row in parsed.rows if row.payload["name"] not in unverified_names
    ]
    skipped_unverified = len(parsed.rows) - len(filtered_rows)
    filtered = ParseResult(
        rows=filtered_rows,
        errors=parsed.errors,
        unresolved_names=parsed.unresolved_names,
    )
    if dry_run:
        result = _dry_run_rehash(db, filtered)
    else:
        result = _commit_rehash(db, filtered)
    result.override_validations = validations
    result.skipped_unverified = skipped_unverified
    return result
