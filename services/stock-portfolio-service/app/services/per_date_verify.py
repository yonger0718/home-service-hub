"""Verify user-supplied broker name overrides against dated market history."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
import re
from typing import Literal

import requests
from requests.exceptions import SSLError

from .twse_client import TLSMode, bootstrap_truststore, get_tls_mode

logger = logging.getLogger(__name__)


def _get_with_tls_fallback(url: str, *, timeout: int = 10) -> requests.Response:
    """GET honouring `TWSE_TLS_MODE` exactly like `market_data_service._http_get`:

    * ``insecure`` → single `verify=False` request, no probe.
    * ``verify``   → single `verify=True` request; SSLError re-raised.
    * ``fallback`` (default) → try `verify=True`; on SSLError log + retry with
      `verify=False`. Mirrors the canonical TWSE-on-OL-ARM workaround so
      observability matches the rest of the codebase instead of silently
      downgrading.
    """
    mode = get_tls_mode()
    if mode == TLSMode.INSECURE:
        return requests.get(url, timeout=timeout, verify=False)
    try:
        return requests.get(url, timeout=timeout, verify=True)
    except SSLError as exc:
        if mode != TLSMode.FALLBACK:
            raise
        logger.warning(
            "per_date_verify TLS verification failed; retrying insecurely: %s", exc
        )
        return requests.get(url, timeout=timeout, verify=False)

ValidationStatus = Literal[
    "verified",
    "name_mismatch",
    "not_traded_on_date",
    "fetch_failed",
    "user_overridden",
]
StatusHint = Literal["ok", "not_traded", "error"]

_TWSE_CODE_RE = re.compile(r"^\d{4}$")
_TPEX_CODE_RE = re.compile(r"^\d{4,6}[A-Z]?$")
_TWSE_TITLE_RE = re.compile(r"^\d+年\d+月\s+(\S+)\s+(\S+)\s+")
_TPEX_SUBTITLE_RE = re.compile(r"^(\S+)\s+(\S+)\s+\d+年\d+月")

_NAME_CACHE: dict[tuple[str, str], str | None] = {}
_ERROR_CACHE: set[tuple[str, str]] = set()

bootstrap_truststore()


@dataclass
class OverrideValidation:
    name: str
    code: str
    status: ValidationStatus
    expected_name: str | None = None
    fetched_name: str | None = None


def verify_single(name: str, code: str, trade_date: date) -> OverrideValidation:
    fetched_name, status_hint = fetch_name_for_date(code, trade_date)
    if status_hint == "ok" and fetched_name and fetched_name.strip() == name.strip():
        return OverrideValidation(
            name=name, code=code, status="verified",
            expected_name=name, fetched_name=fetched_name,
        )
    if status_hint == "ok":
        return OverrideValidation(
            name=name, code=code, status="name_mismatch",
            expected_name=name, fetched_name=fetched_name,
        )
    if status_hint == "not_traded":
        return OverrideValidation(
            name=name, code=code, status="not_traded_on_date",
            expected_name=name, fetched_name=None,
        )
    return OverrideValidation(
        name=name, code=code, status="fetch_failed",
        expected_name=name, fetched_name=None,
    )


def _fetch_twse_name(code: str, trade_date: date) -> tuple[str | None, StatusHint]:
    yyyymmdd = trade_date.strftime("%Y%m%d")
    url = (
        "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
        f"?response=json&date={yyyymmdd}&stockNo={code}"
    )
    try:
        response = _get_with_tls_fallback(url)
        if not 200 <= response.status_code < 300:
            return None, "error"
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None, "error"
    if payload.get("stat") != "OK":
        return None, "error"
    if not payload.get("data"):
        return None, "not_traded"
    match = _TWSE_TITLE_RE.match(payload.get("title", ""))
    if match is None:
        return None, "error"
    return match.group(2), "ok"


def _fetch_tpex_name(code: str, trade_date: date) -> tuple[str | None, StatusHint]:
    yyyy_mm_dd = trade_date.strftime("%Y/%m/%d")
    url = (
        "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock"
        f"?date={yyyy_mm_dd}&code={code}&response=json"
    )
    try:
        response = _get_with_tls_fallback(url)
        if not 200 <= response.status_code < 300:
            return None, "error"
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None, "error"
    tables = payload.get("tables")
    if not tables:
        return None, "error"
    table = tables[0]
    if not table.get("data"):
        return None, "not_traded"
    match = _TPEX_SUBTITLE_RE.match(table.get("subtitle", ""))
    if match is None:
        return None, "error"
    return match.group(2), "ok"


def fetch_name_for_date(code: str, trade_date: date) -> tuple[str | None, StatusHint]:
    yyyymmdd = trade_date.strftime("%Y%m%d")
    cache_key = (code, yyyymmdd)
    if cache_key in _NAME_CACHE:
        if cache_key in _ERROR_CACHE:
            return None, "error"
        cached_name = _NAME_CACHE[cache_key]
        return (cached_name, "ok") if cached_name is not None else (None, "not_traded")

    # Always try both exchanges: 4-digit codes aren't actually TWSE-exclusive
    # (e.g. some 5/6/8xxx ranges trade on TPEx), and the cache + `not_traded`
    # fall-through keeps the cost ≈1 extra request only on the genuinely-empty
    # path. TWSE first so listed equities resolve in one hop.
    fetchers = (_fetch_twse_name, _fetch_tpex_name)

    fetched_name: str | None = None
    status_hint: StatusHint = "error"
    for fetcher in fetchers:
        fetched_name, status_hint = fetcher(code, trade_date)
        if status_hint == "ok":
            break

    _NAME_CACHE[cache_key] = fetched_name
    if status_hint == "error":
        _ERROR_CACHE.add(cache_key)
    return fetched_name, status_hint


def verify_overrides(
    *,
    name_to_code: dict[str, str],
    name_to_earliest_date: dict[str, date],
    confirmed: set[str],
) -> list[OverrideValidation]:
    """Verify override names against dated market history or user confirmation."""

    validations: list[OverrideValidation] = []
    for name, code in name_to_code.items():
        if name in confirmed:
            validations.append(
                OverrideValidation(
                    name=name,
                    code=code,
                    status="user_overridden",
                    expected_name=name,
                    fetched_name=None,
                )
            )
            continue

        fetched_name, status_hint = fetch_name_for_date(
            code,
            name_to_earliest_date[name],
        )
        if status_hint == "ok" and fetched_name.strip() == name.strip():
            validations.append(
                OverrideValidation(
                    name=name,
                    code=code,
                    status="verified",
                    expected_name=name,
                    fetched_name=fetched_name,
                )
            )
        elif status_hint == "ok":
            validations.append(
                OverrideValidation(
                    name=name,
                    code=code,
                    status="name_mismatch",
                    expected_name=name,
                    fetched_name=fetched_name,
                )
            )
        elif status_hint == "not_traded":
            validations.append(
                OverrideValidation(
                    name=name,
                    code=code,
                    status="not_traded_on_date",
                    expected_name=name,
                    fetched_name=None,
                )
            )
        else:
            validations.append(
                OverrideValidation(
                    name=name,
                    code=code,
                    status="fetch_failed",
                    expected_name=name,
                    fetched_name=None,
                )
            )
    return validations
