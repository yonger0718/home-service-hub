"""Historical cash + stock dividend fetcher.

Primary source: TWSE ``rwd/zh/exRight/TWT49U`` (same endpoint
``node-twstock`` scrapes) for listed symbols. For OTC symbols, falls
back to ``dividend_sources.tpex_otc`` (which only exposes the current
year — historical TPEx OTC backfill is a known v1 limitation).

The fetcher is read-only and tolerant: HTTP / parse failures are
logged as ``dividend_history.failed`` and reported as an empty list so
the backfill loop can continue across other (symbol, year) pairs.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date as dt_date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Optional

from functools import lru_cache

from .dividend_sources import tpex_otc
from .market_data_service import _http_get, _http_post_form

logger = logging.getLogger(__name__)

TWT49U_URL = "https://www.twse.com.tw/rwd/zh/exRight/TWT49U"
TWT49U_DETAIL_URL = "https://www.twse.com.tw/rwd/zh/exRight/TWT49UDetail"
TPEX_EX_DAILY_Q_URL = "https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ"

_TW_OFFSET = timezone(timedelta(hours=8))


@dataclass(frozen=True, slots=True)
class HistoricalDividendEvent:
    """A single ex-dividend / ex-rights event for one symbol."""

    symbol: str
    ex_date: dt_date
    cash_dividend_per_share: Optional[Decimal]
    stock_dividend_per_thousand: Optional[Decimal]
    previous_close: Optional[Decimal]
    reference_price: Optional[Decimal]
    source: str


def _decimal_or_none(value: Any) -> Optional[Decimal]:
    """Lenient Decimal parse: strips commas, whitespace, and trailing units like ``元／股`` / ``股``."""
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "N/A"}:
        return None
    import re as _re
    match = _re.match(r"^[-+]?\d+(?:\.\d+)?", text)
    if match is None:
        return None
    try:
        result = Decimal(match.group(0))
    except InvalidOperation:
        return None
    return result if result != 0 else None


def _roc_to_date(text: str) -> Optional[dt_date]:
    if not text:
        return None
    parts = str(text).strip().replace("年", "/").replace("月", "/").replace("日", "").split("/")
    if len(parts) != 3:
        return None
    try:
        roc_year = int(parts[0])
        year = roc_year + 1911 if roc_year < 1911 else roc_year
        return dt_date(year, int(parts[1]), int(parts[2]))
    except (ValueError, IndexError):
        return None


def _current_tw_year() -> int:
    return datetime.now(_TW_OFFSET).year


def _twse_date(d: dt_date) -> str:
    return d.strftime("%Y%m%d")


@lru_cache(maxsize=32)
def _fetch_twt49u_year_raw(year: int) -> bytes | str | dict | None:
    """Cached TWT49U fetch for one calendar year.

    Upstream returns every symbol's events for the year regardless of
    ``stockNo`` — so one fetch serves the whole backfill, not 177×.
    """
    start_date = _twse_date(dt_date(year, 1, 1))
    end_date = _twse_date(dt_date(year, 12, 31))
    return _http_get(
        TWT49U_URL,
        {"startDate": start_date, "endDate": end_date, "response": "json"},
    )


@lru_cache(maxsize=4096)
def _fetch_detail_cached(symbol: str, detail_date_param: str) -> tuple:
    """Pickled-tuple cache around ``_fetch_detail`` (one row per ex-date)."""
    cash, stock = _fetch_detail_raw(symbol, detail_date_param)
    return (cash, stock)


def _fetch_detail(symbol: str, detail_date_param: str) -> tuple[Optional[Decimal], Optional[Decimal]]:
    cash, stock = _fetch_detail_cached(symbol, detail_date_param)
    return cash, stock


def _fetch_detail_raw(symbol: str, detail_date_param: str) -> tuple[Optional[Decimal], Optional[Decimal]]:
    """Return (cash_dividend_per_share, stock_dividend_per_thousand).

    Mirrors node-twstock's ``fetchStocksDividendsDetail``: params
    ``STK_NO`` + ``T1``, ``stat`` envelope check is lowercase ``ok``,
    and ``data[0]`` is shaped ``[symbol, name, cash, ?, stock_per_thousand, ...]``.
    """
    payload = _http_get(
        TWT49U_DETAIL_URL,
        {"STK_NO": symbol, "T1": detail_date_param, "response": "json"},
    )
    if payload is None:
        return None, None
    data = _coerce_dict(payload)
    if str(data.get("stat", "")).lower() != "ok":
        return None, None
    rows = data.get("data") or []
    if not isinstance(rows, list) or not rows:
        return None, None
    first = rows[0]
    if not isinstance(first, list) or len(first) < 5:
        return None, None
    cash = _decimal_or_none(first[2])
    stock_per_thousand = _decimal_or_none(first[4])
    return cash, stock_per_thousand


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


def parse_twt49u_response(
    symbol: str, payload: bytes | str | dict
) -> list[tuple[dt_date, Optional[Decimal], Optional[Decimal], Optional[str], Optional[Decimal], Optional[str]]]:
    """Parse the rwd/TWT49U JSON envelope and filter to ``symbol``.

    The upstream endpoint ignores ``stockNo`` and returns every event
    for the date range, so we must filter here.

    Returns a list of (ex_date, previous_close, reference_price,
    detail_date_param, raw_dividend_value, dividend_type) tuples. The
    detail-date param is extracted from the row's "詳細資料" column
    which is shaped ``"{symbol},{YYYYMMDD}"``.
    """
    data = _coerce_dict(payload)
    if str(data.get("stat", "")).upper() != "OK":
        return []
    rows = data.get("data") or []
    fields = data.get("fields") or []
    if not isinstance(rows, list) or not isinstance(fields, list):
        return []
    field_to_idx = {str(f).strip(): i for i, f in enumerate(fields)}
    date_idx = field_to_idx.get("資料日期", 0)
    sym_idx = field_to_idx.get("股票代號", 1)
    prev_idx = field_to_idx.get("除權息前收盤價")
    ref_idx = field_to_idx.get("除權息參考價")
    div_value_idx = field_to_idx.get("權值+息值")
    div_type_idx = field_to_idx.get("權/息")
    detail_idx = field_to_idx.get("詳細資料")
    out: list[
        tuple[dt_date, Optional[Decimal], Optional[Decimal], Optional[str], Optional[Decimal], Optional[str]]
    ] = []
    for row in rows:
        if not isinstance(row, list) or len(row) <= sym_idx:
            continue
        row_symbol = str(row[sym_idx]).strip()
        if row_symbol != symbol:
            continue
        ex_date = _roc_to_date(str(row[date_idx])) if date_idx < len(row) else None
        if ex_date is None:
            continue
        prev = _decimal_or_none(row[prev_idx]) if prev_idx is not None and prev_idx < len(row) else None
        ref = _decimal_or_none(row[ref_idx]) if ref_idx is not None and ref_idx < len(row) else None
        div_value = (
            _decimal_or_none(row[div_value_idx])
            if div_value_idx is not None and div_value_idx < len(row)
            else None
        )
        div_type = (
            str(row[div_type_idx]).strip()
            if div_type_idx is not None and div_type_idx < len(row)
            else None
        )
        detail_param: Optional[str] = None
        if detail_idx is not None and detail_idx < len(row):
            raw_detail = str(row[detail_idx])
            import re as _re
            match = _re.search(r"\b\d{8}\b", raw_detail)
            if match is not None:
                detail_param = match.group(0)
        out.append((ex_date, prev, ref, detail_param, div_value, div_type))
    return out


def fetch_symbol_year(symbol: str, year: int) -> list[HistoricalDividendEvent]:
    """Fetch all ex-dividend events for one symbol in one calendar year.

    The upstream TWT49U endpoint ignores ``stockNo`` and returns every
    event in the date range — :func:`parse_twt49u_response` filters by
    symbol after the fetch. HTTP / parse failures are logged + an empty
    list returned so the backfill loop is not interrupted.
    """
    payload = _fetch_twt49u_year_raw(year)
    if payload is None:
        logger.info(
            "dividend_history.failed",
            extra={"symbol": symbol, "year": year, "error": "empty_payload"},
        )
        return []
    try:
        rows = parse_twt49u_response(symbol, payload)
    except (ValueError, KeyError) as exc:
        logger.warning(
            "dividend_history.failed",
            extra={"symbol": symbol, "year": year, "error": str(exc)},
        )
        return []
    events: list[HistoricalDividendEvent] = []
    for ex_date, prev_close, ref_price, detail_param, div_value, div_type in rows:
        cash, stock_per_thousand = (None, None)
        if detail_param is not None:
            cash, stock_per_thousand = _fetch_detail(symbol, detail_param)
        # Fallback: when detail endpoint returns empty, infer from the
        # main row using the combined dividend value + 權/息 type.
        if cash is None and stock_per_thousand is None and div_value is not None and div_type:
            if "息" in div_type and "權" not in div_type:
                cash = div_value
            elif "權" in div_type and "息" not in div_type:
                stock_per_thousand = div_value * Decimal(100)
        if cash is None and stock_per_thousand is None:
            continue
        events.append(
            HistoricalDividendEvent(
                symbol=symbol,
                ex_date=ex_date,
                cash_dividend_per_share=cash,
                stock_dividend_per_thousand=stock_per_thousand,
                previous_close=prev_close,
                reference_price=ref_price,
                source="TWT49U",
            )
        )
    return events


def _parse_tpex_history(payload: bytes | str | dict) -> list[tuple[str, dt_date, Optional[Decimal], Optional[Decimal]]]:
    """Parse TPEx exDailyQ POST response.

    Row schema (from node-twstock + observed payload):
    ``[ex_date_roc, symbol, name, prev_close, ref_price, 權值, 息值,
      權值+息值, 權/息, 漲停, 跌停, 開始基準, 減除股利參考, 現金股利,
      每仟股無償配股, 現金增資股數, ...]``
    """
    data = _coerce_dict(payload)
    tables = data.get("tables") or []
    if not isinstance(tables, list) or not tables:
        return []
    rows = tables[0].get("data") if isinstance(tables[0], dict) else None
    if not isinstance(rows, list):
        return []
    out: list[tuple[str, dt_date, Optional[Decimal], Optional[Decimal]]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 15:
            continue
        ex_date = _roc_to_date(str(row[0]))
        if ex_date is None:
            continue
        symbol = str(row[1]).strip()
        if not symbol:
            continue
        cash = _decimal_or_none(row[13])
        stock_per_thousand = _decimal_or_none(row[14])
        if cash is None and stock_per_thousand is None:
            continue
        out.append((symbol, ex_date, cash, stock_per_thousand))
    return out


@lru_cache(maxsize=32)
def _fetch_tpex_year_cached(year: int) -> tuple:
    """Cached POST fetch for one calendar year of TPEx OTC dividends."""
    start = f"{year}/01/01"
    end = f"{year}/12/31"
    payload = _http_post_form(
        TPEX_EX_DAILY_Q_URL,
        {"startDate": start, "endDate": end, "response": "json"},
    )
    if payload is None:
        logger.info(
            "dividend_history.tpex_failed",
            extra={"year": year, "error": "empty_payload"},
        )
        return tuple()
    try:
        return tuple(_parse_tpex_history(payload))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "dividend_history.tpex_failed",
            extra={"year": year, "error": str(exc)},
        )
        return tuple()


def fetch_tpex_symbol_year(symbol: str, year: int) -> list[HistoricalDividendEvent]:
    """Pull TPEx OTC events for one (symbol, year) — uses the cached year payload."""
    rows = _fetch_tpex_year_cached(year)
    out: list[HistoricalDividendEvent] = []
    for row_symbol, ex_date, cash, stock_per_thousand in rows:
        if row_symbol != symbol:
            continue
        out.append(
            HistoricalDividendEvent(
                symbol=symbol,
                ex_date=ex_date,
                cash_dividend_per_share=cash,
                stock_dividend_per_thousand=stock_per_thousand,
                previous_close=None,
                reference_price=None,
                source="TPEX",
            )
        )
    return out


def fetch_for_symbol_all_years(
    symbol: str, since: dt_date, *, until_year: Optional[int] = None
) -> list[HistoricalDividendEvent]:
    """Walk every calendar year from ``since.year`` to current TW year.

    Tries TWSE TWT49U first; when that returns zero events for the
    (symbol, year), falls back to the TPEx OTC feed. One year's
    exception is logged and the loop continues.
    """
    end_year = until_year or _current_tw_year()
    all_events: list[HistoricalDividendEvent] = []
    for year in range(since.year, end_year + 1):
        try:
            year_events = fetch_symbol_year(symbol, year)
        except Exception as exc:  # noqa: BLE001 — keep backfill loop alive
            logger.exception(
                "dividend_history.year_failed",
                extra={"symbol": symbol, "year": year, "error": str(exc)},
            )
            year_events = []
        if not year_events:
            try:
                year_events = fetch_tpex_symbol_year(symbol, year)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "dividend_history.tpex_year_failed",
                    extra={"symbol": symbol, "year": year, "error": str(exc)},
                )
                year_events = []
        all_events.extend(year_events)
    return all_events


def fetch_otc_fallback(symbol: str, year: int) -> list[HistoricalDividendEvent]:
    """Best-effort current-year TPEx OTC fallback (no historical multi-year API).

    Reuses :func:`dividend_sources.tpex_otc.fetch_tpex_otc` and filters
    to the requested symbol.
    """
    rows = tpex_otc.fetch_tpex_otc(year)
    out: list[HistoricalDividendEvent] = []
    for row in rows:
        if row.symbol != symbol:
            continue
        out.append(
            HistoricalDividendEvent(
                symbol=row.symbol,
                ex_date=row.ex_dividend_date,
                cash_dividend_per_share=row.cash_dividend,
                stock_dividend_per_thousand=(
                    (row.stock_dividend * Decimal(1000)) if row.stock_dividend is not None else None
                ),
                previous_close=None,
                reference_price=None,
                source="TPEX_OTC",
            )
        )
    return out
