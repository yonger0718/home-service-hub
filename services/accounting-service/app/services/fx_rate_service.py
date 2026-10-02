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


def _rates_object(payload: Any, day: date, base_lc: str) -> tuple[dict[str, Any] | None, str | None]:
    """Return the `base` rates object when the payload is well-formed and dated `day`, else (None, reason)."""
    if not isinstance(payload, dict):
        return None, "payload is not a JSON object"
    if payload.get("date") != day.isoformat():
        return None, f"payload dated {payload.get('date')!r}, expected {day.isoformat()}"
    rates = payload.get(base_lc)
    if not isinstance(rates, dict):
        return None, f"payload missing rates object for {base_lc}"
    return rates, None


def _resolve_day(
    http_get: HttpGet, day: date, base: str, quotes: list[str]
) -> tuple[dict[str, tuple[Decimal, str]], dict[str, str]]:
    """Resolve each quote for one (day, base): primary first, then the fallback for whatever is still unusable.

    Returns ({quote: (rate, source label)}, {quote: reason it was unavailable from every source}).
    """
    slot, base_lc = day.isoformat(), base.lower()
    resolved: dict[str, tuple[Decimal, str]] = {}
    reasons: dict[str, list[str]] = {quote: [] for quote in quotes}
    for template, label in ((PRIMARY_URL_TEMPLATE, PRIMARY_SOURCE_LABEL), (FALLBACK_URL_TEMPLATE, FALLBACK_SOURCE_LABEL)):
        pending = [quote for quote in quotes if quote not in resolved]
        if not pending:
            break
        payload, error = _fetch_json(http_get, template.format(slot=slot, base_lc=base_lc))
        rates, error = (None, error) if error else _rates_object(payload, day, base_lc)
        for quote in pending:
            rate = _parse_rate(rates.get(quote.lower())) if rates is not None else None
            if rate is not None:
                resolved[quote] = (rate, label)
            else:
                reasons[quote].append(error or f"no usable {quote} rate in the {label} payload")
    return resolved, {quote: "; ".join(reasons[quote]) for quote in quotes if quote not in resolved}


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
    quotes_by_day: dict[tuple[date, str], list[str]] = {}
    for day, base, quote in missing:
        quotes_by_day.setdefault((day, base), []).append(quote)
    days = sorted(quotes_by_day)
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_FETCHES) as pool:
        resolved = dict(zip(days, pool.map(lambda key: _resolve_day(http_get, *key, quotes_by_day[key]), days)))

    new_rows, failures = [], []
    for day, base, quote in missing:
        found, reasons = resolved[(day, base)]
        if quote not in found:
            failures.append(f"no FX rate for {base}→{quote} on {day.isoformat()}: {reasons[quote]}")
            continue
        rate, source = found[quote]
        rates[(day, base, quote)] = rate
        new_rows.append({"date": day, "base": base, "quote": quote, "rate": rate, "source": source})

    if persist and new_rows:
        # Keep what was fetched even if another rate failed, so a retry only fetches the gaps.
        session.execute(pg_insert(FxRate).values(new_rows).on_conflict_do_nothing())
        session.commit()
    if failures:
        raise FxRateUnavailableError("; ".join(failures))
    return rates
