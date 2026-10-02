"""Daily historical FX rates for converting foreign-currency MOZE rows (design D11).

Rates come from the accounting `fx_rate` cache; missing ones are fetched from the same
fawazahmed0 currency API that stock-portfolio-service uses (jsDelivr, then pages.dev).
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Callable, Iterable

import requests
from requests import Response
from requests.exceptions import RequestException
from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..models import FxRate
from .moze_csv import MozeImportError

PRIMARY_URL_TEMPLATE = "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{slot}/v1/currencies/{base_lc}.json"
FALLBACK_URL_TEMPLATE = "https://{slot}.currency-api.pages.dev/v1/currencies/{base_lc}.json"
PRIMARY_SOURCE_LABEL = "fawazahmed0-jsdelivr"
FALLBACK_SOURCE_LABEL = "fawazahmed0-pages"
HTTP_TIMEOUT_SEC = 10
MAX_PARALLEL_FETCHES = 8
RATE_QUANTUM = Decimal("0.0000000001")  # fx_rate is NUMERIC(20,10)

HttpGet = Callable[..., Response]
RateKey = tuple[date, str, str]  # (day, base = row currency, quote = account currency)


class FxRateUnavailableError(MozeImportError):
    """A needed rate could not be read from the cache or fetched from either source."""


def _fetch_json(http_get: HttpGet, url: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        response = http_get(url, timeout=HTTP_TIMEOUT_SEC)
        if response.status_code < 200 or response.status_code >= 300:
            return None, f"{response.status_code} from {url}"
        return response.json(), None
    except (RequestException, ValueError) as exc:
        return None, str(exc)


def _fetch_day(http_get: HttpGet, day: date, base: str) -> tuple[dict[str, Any] | None, str, str | None]:
    """Return (rates object for `base`, source label, error) for one day."""
    slot, base_lc = day.isoformat(), base.lower()
    errors = []
    for template, label in ((PRIMARY_URL_TEMPLATE, PRIMARY_SOURCE_LABEL), (FALLBACK_URL_TEMPLATE, FALLBACK_SOURCE_LABEL)):
        payload, error = _fetch_json(http_get, template.format(slot=slot, base_lc=base_lc))
        if payload is not None and isinstance(payload.get(base_lc), dict):
            return payload[base_lc], label, None
        errors.append(error or f"payload missing rates object for {base_lc}")
    return None, "", "; ".join(errors)


def _parse_rate(raw) -> Decimal | None:
    """Return the quantized rate, or None when it is missing, unparseable or not positive."""
    if raw is None:
        return None
    try:
        rate = Decimal(str(raw)).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None
    return rate if rate.is_finite() and rate > 0 else None


def ensure_rates(
    session: Session,
    needed: Iterable[RateKey],
    *,
    persist: bool,
    http_get: HttpGet | None = None,
) -> dict[RateKey, Decimal]:
    """Return every needed rate, fetching missing ones; with `persist`, store fetched rates and commit.

    Raises FxRateUnavailableError naming the pair and date when a rate cannot be obtained.
    """
    needed = sorted(set(needed))
    if not needed:
        return {}
    rates: dict[RateKey, Decimal] = {
        (row.date, row.base, row.quote): row.rate
        for row in session.scalars(
            select(FxRate).where(tuple_(FxRate.date, FxRate.base, FxRate.quote).in_(needed))
        )
    }
    missing = [key for key in needed if key not in rates]
    if not missing:
        return rates

    http_get = http_get or requests.get
    days = sorted({(day, base) for day, base, _ in missing})
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_FETCHES) as pool:
        fetched = dict(zip(days, pool.map(lambda key: _fetch_day(http_get, *key), days)))

    new_rows, failures = [], []
    for day, base, quote in missing:
        payload, source, error = fetched[(day, base)]
        raw = payload.get(quote.lower()) if payload is not None else None
        rate = _parse_rate(raw)
        if rate is None:
            reason = error or f"no usable {quote} rate in the {source} payload"
            failures.append(f"no FX rate for {base}→{quote} on {day.isoformat()}: {reason}")
            continue
        rates[(day, base, quote)] = rate
        new_rows.append({"date": day, "base": base, "quote": quote, "rate": rate, "source": source})

    if persist and new_rows:
        # Keep what was fetched even if another rate failed, so a retry only fetches the gaps.
        session.execute(pg_insert(FxRate).values(new_rows).on_conflict_do_nothing())
        session.commit()
    if failures:
        raise FxRateUnavailableError("; ".join(failures))
    return rates
