"""TWSE TWTB8U face-value-change fetcher + cumulative split-factor helper.

Ported from stonk's ``TwseSplitFetcher`` with two adaptations:

- synchronous (matches home-hub's sync DB + sync HTTP elsewhere);
- keyed by ``symbol`` string instead of UUID asset_id.

Persistence uses ``Session.merge`` against the unique
``source_event_key`` column, so re-running ``backfill_year`` for the
same year is idempotent.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date as dt_date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Optional

from sqlalchemy.orm import Session

from ..models.corporate_action import CorporateAction
from .market_data_service import _http_get

logger = logging.getLogger(__name__)

TWTB8U_URL = "https://www.twse.com.tw/rwd/zh/change/TWTB8U"

_ROC_DATE_RE = re.compile(r"^\s*(\d{1,4})[年/](\d{1,2})[月/](\d{1,2})日?\s*$")


@dataclass(frozen=True, slots=True)
class CorporateActionRow:
    symbol: str
    effective_date: dt_date
    action_type: str
    ratio: Decimal
    source: str
    source_event_key: str
    raw_payload: dict[str, Any]


# ---------- ROC date ----------


def parse_roc_date(value: str) -> Optional[dt_date]:
    if not value:
        return None
    match = _ROC_DATE_RE.match(value.strip())
    if match is None:
        return None
    roc_year, month, day = (int(g) for g in match.groups())
    year = roc_year + 1911 if roc_year < 1911 else roc_year
    try:
        return dt_date(year, month, day)
    except ValueError:
        return None


# ---------- number helpers ----------


def _clean(value: object) -> str:
    return str(value).replace(",", "").replace("\xa0", "").strip()


def _decimal_or_none(value: object) -> Optional[Decimal]:
    text = _clean(value)
    if not text or set(text) <= {"-"}:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


# ---------- parser ----------


def parse_twtb8u(payload: bytes | str | dict[str, Any], year: int) -> list[CorporateActionRow]:
    """Parse one year of TWTB8U JSON into normalised rows."""
    data = _json(payload)
    rows: list[CorporateActionRow] = []
    for record in _iter_records(data):
        if not isinstance(record, list) or len(record) < 8:
            continue
        effective_date = parse_roc_date(_clean(record[0]))
        symbol = _clean(record[1])
        pre_close = _decimal_or_none(record[3])
        post_ref = _decimal_or_none(record[4])
        if effective_date is None or not symbol or pre_close is None:
            continue
        if post_ref is None or post_ref == 0:
            continue
        ratio = pre_close / post_ref
        rows.append(
            CorporateActionRow(
                symbol=symbol,
                effective_date=effective_date,
                action_type="FACE_VALUE_CHANGE",
                ratio=ratio,
                source="TWSE",
                source_event_key=f"{symbol}_{effective_date.isoformat()}",
                raw_payload={
                    "symbol": symbol,
                    "year": year,
                    "effective_date_roc": _clean(record[0]),
                    "pre_close": str(pre_close),
                    "post_ref_price": str(post_ref),
                },
            )
        )
    return rows


def _json(payload: bytes | str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, bytes):
        return dict(json.loads(payload.decode("utf-8-sig")))
    return dict(json.loads(payload))


def _iter_records(data: dict[str, Any]) -> Iterable[Any]:
    direct = data.get("data")
    if isinstance(direct, list):
        yield from direct
    tables = data.get("tables")
    if isinstance(tables, list):
        for table in tables:
            if isinstance(table, dict):
                table_data = table.get("data")
                if isinstance(table_data, list):
                    yield from table_data


# ---------- fetch + persistence ----------


def fetch_year(year: int) -> list[CorporateActionRow]:
    payload = _http_get(TWTB8U_URL, {"response": "json", "yy": str(year)})
    if payload is None:
        return []
    try:
        return parse_twtb8u(payload, year)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.error("Failed to parse TWSE TWTB8U for %s: %s", year, exc)
        return []


def upsert_rows(db: Session, rows: Iterable[CorporateActionRow]) -> int:
    """Insert-or-update keyed by ``source_event_key``; returns count written."""
    written = 0
    for row in rows:
        existing = (
            db.query(CorporateAction)
            .filter(CorporateAction.source_event_key == row.source_event_key)
            .one_or_none()
        )
        if existing is None:
            db.add(
                CorporateAction(
                    symbol=row.symbol,
                    effective_date=row.effective_date,
                    action_type=row.action_type,
                    ratio=row.ratio,
                    source=row.source,
                    source_event_key=row.source_event_key,
                    raw_payload=row.raw_payload,
                )
            )
        else:
            existing.symbol = row.symbol
            existing.effective_date = row.effective_date
            existing.action_type = row.action_type
            existing.ratio = row.ratio
            existing.source = row.source
            existing.raw_payload = row.raw_payload
        written += 1
    db.commit()
    return written


def backfill_year(db: Session, year: int) -> dict:
    rows = fetch_year(year)
    written = upsert_rows(db, rows)
    return {"year": year, "rows": len(rows), "written": written}


def list_actions(
    db: Session,
    *,
    symbol: Optional[str] = None,
    from_date: Optional[dt_date] = None,
    to_date: Optional[dt_date] = None,
) -> list[CorporateAction]:
    query = db.query(CorporateAction)
    if symbol is not None:
        query = query.filter(CorporateAction.symbol == symbol.strip().upper())
    if from_date is not None:
        query = query.filter(CorporateAction.effective_date >= from_date)
    if to_date is not None:
        query = query.filter(CorporateAction.effective_date <= to_date)
    return query.order_by(CorporateAction.effective_date.asc(), CorporateAction.id.asc()).all()


# ---------- factor helper ----------


def get_split_factor(db: Session, symbol: str, as_of: dt_date) -> Decimal:
    """Cumulative product of every ratio with ``effective_date <= as_of``."""
    rows = (
        db.query(CorporateAction.ratio)
        .filter(
            CorporateAction.symbol == symbol,
            CorporateAction.effective_date <= as_of,
        )
        .all()
    )
    factor = Decimal(1)
    for (ratio,) in rows:
        factor *= ratio
    return factor


