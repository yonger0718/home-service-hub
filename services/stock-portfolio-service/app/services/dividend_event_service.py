"""Merge TWT48U + TWT49U + TPEx dividend events for a holdings set."""

from __future__ import annotations

import logging
from datetime import date as dt_date, datetime, timedelta, timezone
from typing import Optional, Set

from .dividend_sources import DividendEventRow
from .dividend_sources import twse_twt48u, twse_twt49u, tpex_otc

logger = logging.getLogger(__name__)

_TW_OFFSET = timezone(timedelta(hours=8))

_SOURCE_PRIORITY = (twse_twt48u, twse_twt49u, tpex_otc)


def _current_tw_year() -> int:
    return datetime.now(_TW_OFFSET).year


def _safe_fetch(module, year: int) -> list[DividendEventRow]:
    fetcher = {
        twse_twt48u: twse_twt48u.fetch_twt48u,
        twse_twt49u: twse_twt49u.fetch_twt49u,
        tpex_otc: tpex_otc.fetch_tpex_otc,
    }[module]
    try:
        return fetcher(year)
    except Exception as exc:  # noqa: BLE001 — one bad source must not kill the chain
        source_name = getattr(module, "SOURCE", module.__name__)
        logger.exception(
            "dividend_source.failed", extra={"source": source_name, "error": str(exc)}
        )
        return []


def fetch_for_holdings(
    held_symbols: Set[str], *, year: Optional[int] = None
) -> list[DividendEventRow]:
    if not held_symbols:
        return []
    target_year = year or _current_tw_year()

    merged: dict[tuple[str, dt_date], DividendEventRow] = {}
    for module in _SOURCE_PRIORITY:
        for row in _safe_fetch(module, target_year):
            if row.symbol not in held_symbols:
                continue
            key = (row.symbol, row.ex_dividend_date)
            existing = merged.get(key)
            if existing is None:
                merged[key] = row
                continue
            existing_empty = existing.cash_dividend is None and existing.stock_dividend is None
            row_has_payload = row.cash_dividend is not None or row.stock_dividend is not None
            if existing_empty and row_has_payload:
                merged[key] = row

    return sorted(merged.values(), key=lambda r: (r.ex_dividend_date, r.symbol))
