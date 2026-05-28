"""TPEx OTC exDailyQ — daily ex-dividend / ex-right list for OTC symbols."""

from __future__ import annotations

import json
import logging
from datetime import date as dt_date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Optional

from . import DividendEventRow
from ..market_data_service import _http_get

logger = logging.getLogger(__name__)

URL = "https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ"
SOURCE = "TPEX_OTC"


def _roc_to_date(value: object):
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"-", ""}:
        return None
    parts = text.replace("年", "/").replace("月", "/").replace("日", "").split("/")
    if len(parts) != 3:
        return None
    try:
        roc_year = int(parts[0])
        year = roc_year + 1911 if roc_year < 1911 else roc_year
        return dt_date(year, int(parts[1]), int(parts[2]))
    except (ValueError, IndexError):
        return None


def _decimal_or_none(value: object) -> Optional[Decimal]:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "0", "0.0", "0.00", "0.000"}:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def parse_tpex_otc(raw: bytes | str | dict[str, Any]) -> list[DividendEventRow]:
    data = _coerce_dict(raw)
    rows: list[DividendEventRow] = []
    for table_data in _iter_records(data):
        for source_row in table_data:
            row = _parse_row(source_row)
            if row is not None:
                rows.append(row)
    return rows


def _parse_row(source_row: object) -> Optional[DividendEventRow]:
    if not isinstance(source_row, list) or len(source_row) < 15:
        return None
    ex_date = _roc_to_date(source_row[0])
    if ex_date is None:
        return None
    symbol = str(source_row[1]).strip()
    if not symbol:
        return None
    cash = _decimal_or_none(source_row[13])
    stock_per_thousand = _decimal_or_none(source_row[14])
    stock_per_share = (
        (stock_per_thousand / Decimal(1000)) if stock_per_thousand is not None else None
    )
    return DividendEventRow(
        symbol=symbol,
        ex_dividend_date=ex_date,
        cash_dividend=cash,
        stock_dividend=stock_per_share,
        source=SOURCE,
    )


def _coerce_dict(raw: bytes | str | dict) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, bytes):
        try:
            return dict(json.loads(raw.decode("utf-8-sig")))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
    if isinstance(raw, str):
        try:
            return dict(json.loads(raw))
        except json.JSONDecodeError:
            return {}
    return {}


def _iter_records(data: dict[str, Any]) -> Iterable[list]:
    tables = data.get("tables")
    if isinstance(tables, list):
        for table in tables:
            if isinstance(table, dict):
                table_data = table.get("data")
                if isinstance(table_data, list):
                    yield table_data
    direct = data.get("data")
    if isinstance(direct, list):
        yield direct


def fetch_tpex_otc(year: Optional[int] = None) -> list[DividendEventRow]:
    params: dict[str, str] = {"response": "json"}
    if year is not None:
        params["startDate"] = f"{year}/01/01"
        params["endDate"] = f"{year}/12/31"
    payload = _http_get(URL, params)
    if payload is None:
        return []
    try:
        return parse_tpex_otc(payload)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.error("Failed to parse TPEx OTC: %s", exc)
        return []
