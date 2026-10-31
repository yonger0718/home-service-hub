"""Read-through market valuation for `valuation_source` accounts (spec §5). The ledger balance is never touched:
only the account read models display the portfolio's total. Results are cached per (broker, as_of) for ten
minutes; when the portfolio is unreachable the last known value, warm or expired, is served flagged `stale`."""
import logging
import time
from datetime import date
from decimal import Decimal

from ..models import Account
from . import portfolio_client
from .portfolio_client import PortfolioUnavailable, Valuation

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 600
NEGATIVE_TTL = 60  # a failed fetch is remembered this long, so a down portfolio costs one timeout per minute per key
AMOUNT_QUANTUM = Decimal("0.0001")

_cache: dict[tuple[str, date], tuple[float, Valuation]] = {}
_failed: dict[tuple[str, date], float] = {}


def clear_cache() -> None:
    _cache.clear()
    _failed.clear()


def expire_cache() -> None:
    """Test seam: back-date every entry past the TTL without dropping it."""
    for key, (_, valuation) in list(_cache.items()):
        _cache[key] = (float("-inf"), valuation)
    for key in list(_failed):
        _failed[key] = float("-inf")


def broker_of(account: Account) -> str | None:
    source = account.valuation_source
    if not source or ":" not in source:
        return None
    return source.split(":", 1)[1] or None


def _shape(valuation: Valuation, *, stale: bool) -> dict:
    return {
        "value": valuation.total.quantize(AMOUNT_QUANTUM),
        "currency": valuation.currency,
        "as_of": valuation.as_of,
        "priced_through": valuation.priced_through,
        "complete": valuation.complete,
        "missing": list(valuation.missing),
        "stale": stale,
    }


def valuation_for(db, account: Account, as_of: date) -> dict | None:
    """The account's portfolio valuation for `as_of`, or None while none is available (cold miss, no broker,
    or a broker currency other than the account's)."""
    broker = broker_of(account)
    if broker is None:
        return None
    key = (broker, as_of)
    entry = _cache.get(key)
    stale = False
    if entry is not None and time.monotonic() - entry[0] < CACHE_TTL_SECONDS:
        valuation = entry[1]
    else:
        failed_at = _failed.get(key)
        if failed_at is not None and time.monotonic() - failed_at < NEGATIVE_TTL:
            if entry is None:
                return None
            valuation, stale = entry[1], True
        else:
            try:
                valuation = portfolio_client.client_for().valuation(broker, as_of)
            except PortfolioUnavailable as exc:
                logger.warning("valuation unavailable broker=%s code=%s", broker, exc.code)
                _failed[key] = time.monotonic()
                if entry is None:
                    return None
                valuation, stale = entry[1], True
            else:
                _failed.pop(key, None)
                _cache[key] = (time.monotonic(), valuation)
    if valuation.currency != account.currency:
        logger.warning("valuation currency mismatch broker=%s code=currency", broker)
        return None
    return _shape(valuation, stale=stale)
