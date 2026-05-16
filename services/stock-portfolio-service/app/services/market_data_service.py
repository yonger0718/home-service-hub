"""Daily OHLC fetchers + persistence for TWSE and TPEx.

Parsers ported from stonk (apps/api/src/finapp/market_data/{twse_daily,tpex_daily}.py)
with two adaptations:

- Key by ``symbol`` string instead of UUID asset_id — home-hub stores
  transactions by symbol, so we mirror that.
- JSON-only payloads — drop the HTML/bs4 fallback the stonk TPEx parser
  carries. Both upstream APIs return JSON when called with ``response=json``
  (TWSE) or modern Accept headers (TPEx), so the fallback is dead weight.

Persistence uses ``Session.merge`` against the composite (symbol, date)
primary key on ``price_history``, so repeat fetches of the same trading day
are idempotent.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date as dt_date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

import requests
from sqlalchemy.orm import Session

from ..models.price_history import PriceHistory
from .twse_client import TLSMode, bootstrap_truststore, get_tls_mode

logger = logging.getLogger(__name__)

TWSE_MI_INDEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TPEX_DAILY_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"
DEFAULT_TIMEOUT_SEC = 20.0


@dataclass(frozen=True, slots=True)
class DailyPriceRow:
    """Normalised end-of-day row before persistence."""

    symbol: str
    date: dt_date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    volume: int | None
    turnover: Decimal | None
    source: str


# ---------- TWSE MI_INDEX parsing (ported from stonk twse_daily.py) ----------

_TWSE_REQUIRED_FIELDS = {
    "證券代號",
    "證券名稱",
    "成交股數",
    "成交金額",
    "開盤價",
    "最高價",
    "最低價",
    "收盤價",
}


def parse_twse_mi_index(
    payload: bytes | str | dict[str, Any],
    date: dt_date,
) -> list[DailyPriceRow]:
    data = _json_payload(payload)
    rows: list[DailyPriceRow] = []
    for table in _twse_tables(data):
        fields = [str(field) for field in table.get("fields") or []]
        if not _TWSE_REQUIRED_FIELDS.issubset(set(fields)):
            continue
        indexes = _twse_field_indexes(fields)
        for source_row in table.get("data") or []:
            parsed = _parse_twse_row(source_row, indexes, date)
            if parsed is not None:
                rows.append(parsed)
    return rows


def _twse_tables(data: dict[str, Any]) -> Iterable[dict[str, Any]]:
    tables = data.get("tables")
    if isinstance(tables, list):
        for table in tables:
            if isinstance(table, dict):
                yield table
    legacy = data.get("data9")
    if isinstance(legacy, list):
        yield {
            "fields": [
                "證券代號",
                "證券名稱",
                "成交股數",
                "成交筆數",
                "成交金額",
                "開盤價",
                "最高價",
                "最低價",
                "收盤價",
            ],
            "data": legacy,
        }


def _twse_field_indexes(fields: list[str]) -> dict[str, int]:
    return {
        "symbol": fields.index("證券代號"),
        "name": fields.index("證券名稱"),
        "volume": fields.index("成交股數"),
        "turnover": fields.index("成交金額"),
        "open": fields.index("開盤價"),
        "high": fields.index("最高價"),
        "low": fields.index("最低價"),
        "close": fields.index("收盤價"),
    }


def _parse_twse_row(
    source_row: object, indexes: dict[str, int], date: dt_date
) -> DailyPriceRow | None:
    if not isinstance(source_row, list):
        return None
    symbol = _cell(source_row, indexes["symbol"])
    if not symbol:
        return None
    close = _decimal_or_none(_cell(source_row, indexes["close"]))
    if close is None or close <= 0:
        return None
    return DailyPriceRow(
        symbol=symbol,
        date=date,
        open=_decimal_or_none(_cell(source_row, indexes["open"])),
        high=_decimal_or_none(_cell(source_row, indexes["high"])),
        low=_decimal_or_none(_cell(source_row, indexes["low"])),
        close=close,
        volume=_int_or_none(_cell(source_row, indexes["volume"])),
        turnover=_decimal_or_none(_cell(source_row, indexes["turnover"])),
        source="TWSE",
    )


# ---------- TPEx daily-quotes parsing ----------

_TPEX_REQUIRED_FIELDS = {"代號", "名稱", "收盤", "開盤", "最高", "最低", "成交股數"}


def parse_tpex_daily_quotes(
    payload: bytes | str | dict[str, Any],
    date: dt_date,
) -> list[DailyPriceRow]:
    data = _json_payload(payload)
    rows: list[DailyPriceRow] = []
    payload_date = data.get("date") if isinstance(data, dict) else None
    expected = date.strftime("%Y%m%d")
    if payload_date and str(payload_date) != expected:
        logger.warning(
            "TPEx returned date=%s for requested %s; dropping rows",
            payload_date,
            expected,
        )
        return rows
    tables = data.get("tables") if isinstance(data, dict) else None
    if not isinstance(tables, list):
        return rows
    for table in tables:
        if not isinstance(table, dict):
            continue
        fields = [str(field) for field in table.get("fields") or []]
        if not _TPEX_REQUIRED_FIELDS.issubset(set(fields)):
            continue
        indexes = _tpex_field_indexes(fields)
        for source_row in table.get("data") or []:
            parsed = _parse_tpex_row(source_row, indexes, date)
            if parsed is not None:
                rows.append(parsed)
    return rows


def _tpex_field_indexes(fields: list[str]) -> dict[str, int]:
    turnover_key = "成交金額(元)" if "成交金額(元)" in fields else "成交金額"
    return {
        "symbol": fields.index("代號"),
        "name": fields.index("名稱"),
        "close": fields.index("收盤"),
        "open": fields.index("開盤"),
        "high": fields.index("最高"),
        "low": fields.index("最低"),
        "volume": fields.index("成交股數"),
        "turnover": fields.index(turnover_key) if turnover_key in fields else -1,
    }


def _parse_tpex_row(
    source_row: object, indexes: dict[str, int], date: dt_date
) -> DailyPriceRow | None:
    if not isinstance(source_row, list):
        return None
    symbol = _cell(source_row, indexes["symbol"])
    if not symbol:
        return None
    close = _decimal_or_none(_cell(source_row, indexes["close"]))
    if close is None or close <= 0:
        return None
    turnover = (
        _decimal_or_none(_cell(source_row, indexes["turnover"]))
        if indexes["turnover"] >= 0
        else None
    )
    return DailyPriceRow(
        symbol=symbol,
        date=date,
        open=_decimal_or_none(_cell(source_row, indexes["open"])),
        high=_decimal_or_none(_cell(source_row, indexes["high"])),
        low=_decimal_or_none(_cell(source_row, indexes["low"])),
        close=close,
        volume=_int_or_none(_cell(source_row, indexes["volume"])),
        turnover=turnover,
        source="TPEx",
    )


# ---------- Shared helpers ----------


def _json_payload(payload: bytes | str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, bytes):
        return dict(json.loads(payload.decode("utf-8-sig")))
    return dict(json.loads(payload))


def _cell(row: list[object], index: int) -> str:
    if index >= len(row):
        return ""
    value = row[index]
    return "" if value is None else str(value).strip()


def _clean_number(value: object) -> str | None:
    cleaned = str(value).strip().replace(",", "")
    if not cleaned or set(cleaned) <= {"-"}:
        return None
    return cleaned


def _decimal_or_none(value: object) -> Decimal | None:
    cleaned = _clean_number(value)
    if cleaned is None:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _int_or_none(value: object) -> int | None:
    cleaned = _clean_number(value)
    if cleaned is None:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


# ---------- HTTP fetch ----------


def _http_get(url: str, params: dict[str, str]) -> bytes | None:
    """GET ``url`` with TLS verification.

    Fails closed on certificate errors. Operator-only ``TWSE_TLS_MODE=insecure``
    skips verification entirely; any other mode (including ``fallback``) requires
    a valid TLS chain and returns ``None`` on ``SSLError`` to avoid persisting
    MITM-tampered OHLC into ``price_history``.
    """

    bootstrap_truststore()
    verify = get_tls_mode() != TLSMode.INSECURE
    try:
        response = requests.get(
            url, params=params, timeout=DEFAULT_TIMEOUT_SEC, verify=verify
        )
        response.raise_for_status()
        return response.content
    except requests.exceptions.SSLError as exc:
        logger.error("Market-data TLS verification failed (failing closed): %s", exc)
        return None
    except requests.exceptions.RequestException as exc:
        logger.error("Market-data request failed: %s", exc)
        return None


def _http_post_form(url: str, data: dict[str, str]) -> bytes | None:
    """POST form-encoded body with the same fail-closed TLS policy as ``_http_get``."""
    bootstrap_truststore()
    verify = get_tls_mode() != TLSMode.INSECURE
    try:
        response = requests.post(
            url, data=data, timeout=DEFAULT_TIMEOUT_SEC, verify=verify
        )
        response.raise_for_status()
        return response.content
    except requests.exceptions.SSLError as exc:
        logger.error("Market-data TLS verification failed (failing closed): %s", exc)
        return None
    except requests.exceptions.RequestException as exc:
        logger.error("Market-data POST failed: %s", exc)
        return None


def fetch_twse_date(date: dt_date) -> list[DailyPriceRow]:
    payload = _http_get(
        TWSE_MI_INDEX_URL,
        {"response": "json", "date": date.strftime("%Y%m%d"), "type": "ALLBUT0999"},
    )
    if payload is None:
        return []
    try:
        return parse_twse_mi_index(payload, date)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.error("Failed to parse TWSE MI_INDEX for %s: %s", date, exc)
        return []


def fetch_tpex_date(date: dt_date) -> list[DailyPriceRow]:
    payload = _http_get(
        TPEX_DAILY_URL,
        {"type": "AL", "date": date.strftime("%Y/%m/%d"), "response": "json"},
    )
    if payload is None:
        return []
    try:
        return parse_tpex_daily_quotes(payload, date)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.error("Failed to parse TPEx daily quotes for %s: %s", date, exc)
        return []


# ---------- Persistence ----------


def upsert_rows(db: Session, rows: Iterable[DailyPriceRow]) -> int:
    """Insert-or-update via composite-PK upsert. Returns count written.

    Uses PG ``ON CONFLICT (symbol, date) DO UPDATE`` when running against
    Postgres so concurrent backfills writing the same ``(symbol, date)``
    cannot blow up the whole batch with ``UniqueViolation``. Falls back to
    per-row ``Session.merge`` (slow but portable) for SQLite-backed tests.
    """
    deduped: dict[tuple[str, dt_date], dict] = {}
    for row in rows:
        deduped[(row.symbol, row.date)] = {
            "symbol": row.symbol,
            "date": row.date,
            "open": row.open,
            "high": row.high,
            "low": row.low,
            "close": row.close,
            "volume": row.volume,
            "turnover": row.turnover,
            "source": row.source,
        }
    payload = list(deduped.values())
    if not payload:
        return 0

    dialect = db.bind.dialect.name if db.bind is not None else ""
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(PriceHistory).values(payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol", "date"],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "turnover": stmt.excluded.turnover,
                "source": stmt.excluded.source,
            },
        )
        db.execute(stmt)
    else:
        for entry in payload:
            db.merge(PriceHistory(**entry))
    db.commit()
    return len(payload)


def backfill_date(db: Session, date: dt_date, *, market: str = "BOTH") -> dict:
    """Fetch + persist one trading day from TWSE, TPEx, or both."""

    market = market.upper()
    twse_rows: list[DailyPriceRow] = []
    tpex_rows: list[DailyPriceRow] = []
    if market in {"TWSE", "BOTH"}:
        twse_rows = fetch_twse_date(date)
    if market in {"TPEX", "BOTH"}:
        tpex_rows = fetch_tpex_date(date)
    written = upsert_rows(db, [*twse_rows, *tpex_rows])
    return {
        "date": date.isoformat(),
        "market": market,
        "twse_rows": len(twse_rows),
        "tpex_rows": len(tpex_rows),
        "written": written,
    }


def list_history(
    db: Session,
    *,
    symbol: str,
    from_date: dt_date,
    to_date: dt_date,
) -> list[PriceHistory]:
    normalized = symbol.split(".")[0].strip().upper()
    return (
        db.query(PriceHistory)
        .filter(
            PriceHistory.symbol == normalized,
            PriceHistory.date >= from_date,
            PriceHistory.date <= to_date,
        )
        .order_by(PriceHistory.date.asc())
        .all()
    )
