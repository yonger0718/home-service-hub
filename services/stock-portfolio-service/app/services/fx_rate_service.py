"""FX rate fetch, persistence, and lookup helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable

import requests
from requests import Response
from requests.exceptions import RequestException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.fx_rate import FxRate

logger = logging.getLogger(__name__)

DEFAULT_BASE_CURRENCIES = ("USD", "TWD")
DEFAULT_QUOTE_CURRENCIES = ("TWD", "USD", "GBP", "JPY")
PRIMARY_URL_TEMPLATE = "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{slot}/v1/currencies/{base_lc}.json"
FALLBACK_URL_TEMPLATE = "https://{slot}.currency-api.pages.dev/v1/currencies/{base_lc}.json"
PRIMARY_SOURCE_LABEL = "fawazahmed0-jsdelivr"
FALLBACK_SOURCE_LABEL = "fawazahmed0-pages"
HTTP_TIMEOUT_SEC = 10


@dataclass(frozen=True)
class PerBaseResult:
    success: bool
    upserted: int
    source_url: str | None
    error: str | None


@dataclass(frozen=True)
class FetchResult:
    success: bool
    per_base: dict[str, PerBaseResult]
    upserted_count: int
    error: str | None


HttpGet = Callable[..., Response]


def _normalize_currency(value: str) -> str:
    return value.strip().upper()


def _unique_currencies(values: tuple[str, ...] | list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for value in values:
        currency = _normalize_currency(value)
        if not currency or currency in seen:
            continue
        seen.add(currency)
        normalized.append(currency)
    return normalized


def _fetch_json(
    http_get: HttpGet,
    url: str,
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        response = http_get(url, timeout=HTTP_TIMEOUT_SEC)
        if response.status_code < 200 or response.status_code >= 300:
            return None, f"{response.status_code} from {url}"
        return response.json(), None
    except RequestException as exc:
        return None, str(exc)


def _upsert_rates(session: Session, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0

    dialect = session.bind.dialect.name if session.bind is not None else ""
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(FxRate).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["date", "base_currency", "quote_currency"],
            set_={
                "rate": stmt.excluded.rate,
                "source": stmt.excluded.source,
                "fetched_at": stmt.excluded.fetched_at,
            },
        )
        session.execute(stmt)
    else:
        for row in rows:
            session.merge(FxRate(**row))
    session.commit()
    return len(rows)


def _rows_from_payload(
    *,
    payload: dict[str, Any],
    base: str,
    quote_currencies: list[str],
    source: str,
    asof: date | None,
) -> list[dict[str, Any]]:
    base_lc = base.lower()
    rates = payload.get(base_lc)
    if not isinstance(rates, dict):
        raise ValueError(f"payload missing rates object for {base_lc}")

    row_date = asof if asof is not None else date.fromisoformat(str(payload["date"]))
    fetched_at = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for quote in quote_currencies:
        if quote == base:
            continue
        raw_rate = rates.get(quote.lower())
        if raw_rate is None:
            continue
        rows.append(
            {
                "date": row_date,
                "base_currency": base,
                "quote_currency": quote,
                "rate": Decimal(str(raw_rate)),
                "source": source,
                "fetched_at": fetched_at,
            }
        )
    return rows


def fetch_and_store(
    session: Session,
    base_currencies: tuple[str, ...] | list[str] = DEFAULT_BASE_CURRENCIES,
    quote_currencies: tuple[str, ...] | list[str] = DEFAULT_QUOTE_CURRENCIES,
    asof: date | None = None,
    http_get: HttpGet | None = None,
) -> FetchResult:
    http_get = http_get or requests.get
    slot = "latest" if asof is None else asof.isoformat()
    bases = _unique_currencies(base_currencies)
    quotes = _unique_currencies(quote_currencies)

    per_base: dict[str, PerBaseResult] = {}
    upserted_count = 0

    for base in bases:
        base_lc = base.lower()
        primary_url = PRIMARY_URL_TEMPLATE.format(slot=slot, base_lc=base_lc)
        fallback_url = FALLBACK_URL_TEMPLATE.format(slot=slot, base_lc=base_lc)
        source_url: str | None = None
        source_label: str | None = None

        try:
            payload, primary_error = _fetch_json(http_get, primary_url)
            if payload is not None:
                source_url = primary_url
                source_label = PRIMARY_SOURCE_LABEL
            else:
                payload, fallback_error = _fetch_json(http_get, fallback_url)
                if payload is not None:
                    source_url = fallback_url
                    source_label = FALLBACK_SOURCE_LABEL
                else:
                    error = f"primary failed: {primary_error}; fallback failed: {fallback_error}"
                    logger.error("fx_rate.fetch_base.failed", extra={"base": base, "error": error})
                    per_base[base] = PerBaseResult(
                        success=False,
                        upserted=0,
                        source_url=None,
                        error=error,
                    )
                    continue

            rows = _rows_from_payload(
                payload=payload,
                base=base,
                quote_currencies=quotes,
                source=source_label,
                asof=asof,
            )
            upserted = _upsert_rates(session, rows)
            upserted_count += upserted
            per_base[base] = PerBaseResult(
                success=True,
                upserted=upserted,
                source_url=source_url,
                error=None,
            )
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            logger.exception(
                "fx_rate.fetch_base.failed",
                extra={"base": base, "error": str(exc)},
            )
            per_base[base] = PerBaseResult(
                success=False,
                upserted=0,
                source_url=source_url,
                error=str(exc),
            )

    errors = [f"{base}: {result.error}" for base, result in per_base.items() if not result.success]
    return FetchResult(
        success=not errors,
        per_base=per_base,
        upserted_count=upserted_count,
        error="; ".join(errors) if errors else None,
    )


def _direct_rate(session: Session, date_: date, base: str, quote: str) -> Decimal | None:
    return session.execute(
        select(FxRate.rate)
        .where(
            FxRate.base_currency == base,
            FxRate.quote_currency == quote,
            FxRate.date <= date_,
        )
        .order_by(FxRate.date.desc())
        .limit(1)
    ).scalar_one_or_none()


def get_rate(session: Session, date_: date, base: str, quote: str) -> Decimal | None:
    base = _normalize_currency(base)
    quote = _normalize_currency(quote)

    direct = _direct_rate(session, date_, base, quote)
    if direct is not None:
        return direct

    if base == "USD" or quote == "USD":
        return None

    base_to_usd = get_rate(session, date_, base, "USD")
    usd_to_quote = get_rate(session, date_, "USD", quote)
    if base_to_usd is None or usd_to_quote is None:
        return None
    return base_to_usd * usd_to_quote
