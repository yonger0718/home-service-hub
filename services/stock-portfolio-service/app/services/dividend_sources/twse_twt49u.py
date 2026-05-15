"""TWSE TWT49U OpenAPI — historical ex-rights events."""

from __future__ import annotations

import json
import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from . import DividendEventRow
from ..market_data_service import _http_get

logger = logging.getLogger(__name__)

URL = "https://openapi.twse.com.tw/v1/exchangeReport/TWT49U"
SOURCE = "TWSE_TWT49U"


def _roc_to_date(roc_str: str):
    from datetime import date as dt_date

    if not roc_str:
        return None
    text = str(roc_str).strip()
    if not text or text == "-":
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


def _decimal_or_none(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "0", "0.0", "0.00"}:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def parse_twt49u(raw: bytes | str | list[dict[str, Any]]) -> list[DividendEventRow]:
    data = _coerce_list(raw)
    rows: list[DividendEventRow] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("公司代號") or item.get("股票代號") or "").strip()
        if not symbol:
            continue
        ex_date = _roc_to_date(item.get("除權息日期") or item.get("除權交易日") or item.get("除息交易日"))
        if ex_date is None:
            continue
        rows.append(
            DividendEventRow(
                symbol=symbol,
                ex_dividend_date=ex_date,
                cash_dividend=_decimal_or_none(item.get("現金股利")),
                stock_dividend=_decimal_or_none(item.get("股票股利")),
                source=SOURCE,
            )
        )
    return rows


def _coerce_list(raw: bytes | str | list) -> list:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, bytes):
        try:
            return json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return []
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return []
    return []


def fetch_twt49u(year: Optional[int] = None) -> list[DividendEventRow]:
    payload = _http_get(URL, {"response": "json"})
    if payload is None:
        return []
    try:
        return parse_twt49u(payload)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.error("Failed to parse TWT49U: %s", exc)
        return []
