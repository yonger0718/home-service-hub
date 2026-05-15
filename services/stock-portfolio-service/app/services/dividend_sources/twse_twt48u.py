"""TWSE TWT48U OpenAPI (current ex-dividend / ex-rights snapshot)."""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from . import DividendEventRow
from ..twse_client import get_twse_client

logger = logging.getLogger(__name__)

URL = "https://openapi.twse.com.tw/v1/exchangeReport/TWT48U"
SOURCE = "TWSE_TWT48U"


def _roc_to_date(roc_str: str):
    from datetime import date as dt_date

    if not roc_str or roc_str.strip() in ("", "-"):
        return None
    try:
        parts = roc_str.strip().split("/")
        if len(parts) != 3:
            return None
        return dt_date(int(parts[0]) + 1911, int(parts[1]), int(parts[2]))
    except (ValueError, IndexError):
        return None


def _decimal_or_none(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "0"}:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def parse_twt48u(raw: list[dict[str, Any]]) -> list[DividendEventRow]:
    rows: list[DividendEventRow] = []
    for item in raw or []:
        symbol = str(item.get("股票代號", "")).strip()
        if not symbol:
            continue
        ex_div = _roc_to_date(item.get("除息交易日", ""))
        ex_rights = _roc_to_date(item.get("除權交易日", ""))
        event_date = ex_div or ex_rights
        if event_date is None:
            continue
        rows.append(
            DividendEventRow(
                symbol=symbol,
                ex_dividend_date=event_date,
                cash_dividend=_decimal_or_none(item.get("最近一次配息")),
                stock_dividend=_decimal_or_none(item.get("最近一次配股")),
                source=SOURCE,
            )
        )
    return rows


def fetch_twt48u(year: Optional[int] = None) -> list[DividendEventRow]:
    """TWT48U has no year filter — returns the current snapshot regardless."""
    try:
        raw = get_twse_client().fetch_exdividend_json(URL)
    except Exception as exc:
        logger.error("Failed to fetch TWT48U: %s", exc)
        return []
    if not isinstance(raw, list):
        logger.warning("TWT48U returned unexpected payload type: %s", type(raw))
        return []
    return parse_twt48u(raw)
