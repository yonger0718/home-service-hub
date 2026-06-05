"""Market-aware quote dispatcher."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Iterable

import structlog
from sqlalchemy.orm import Session

from .. import market_data_service, twse_service
from . import yfinance_fetcher
from .fx_rate_service import RefreshResult

log = structlog.get_logger(__name__)


class _TWSEBackend:
    def refresh_daily_ohlc(self, db: Session, items: list[tuple[str, str]]) -> RefreshResult:
        result = market_data_service.backfill_date(db, date.today(), market="BOTH")
        return RefreshResult(
            ok_count=int(result.get("written", 0)),
            skipped_count=0,
            errors=[],
        )

    def get_quotes(self, db: Session, items: list[tuple[str, str]]):
        symbols = [symbol for symbol, _market in items]
        quotes = twse_service.get_stock_quotes(symbols)
        return {
            (symbol, "TW"): quote
            for symbol, quote in quotes.items()
        }


twse_backend = _TWSEBackend()
yfinance_backend = yfinance_fetcher
_BACKENDS = {"TW": twse_backend, "US": yfinance_backend, "LSE": yfinance_backend}


def _normalize_items(items: Iterable[str | tuple[str, str]]) -> list[tuple[str, str]]:
    normalized: list[tuple[str, str]] = []
    for item in items:
        if isinstance(item, str):
            symbol, market = item, "TW"
        else:
            symbol, market = item
        normalized.append((str(symbol).strip().upper(), str(market or "TW").strip().upper()))
    return normalized


def _current_backends():
    return {"TW": twse_backend, "US": yfinance_backend, "LSE": yfinance_backend}


def _group(items: Iterable[str | tuple[str, str]]):
    grouped: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for symbol, market in _normalize_items(items):
        grouped[market].append((symbol, market))
    return grouped


def refresh_daily_ohlc(db: Session, items: list[str | tuple[str, str]]) -> RefreshResult:
    ok_count = 0
    skipped_count = 0
    errors: list[str] = []
    backends = _current_backends()

    for market, market_items in _group(items).items():
        backend = backends.get(market)
        if backend is None:
            for symbol, _ in market_items:
                error = f"{symbol}: unsupported market {market}"
                errors.append(error)
                log.warning("quotes.dispatcher.skip", symbol=symbol, market=market)
            skipped_count += len(market_items)
            continue
        result = backend.refresh_daily_ohlc(db, market_items)
        ok_count += result.ok_count
        skipped_count += result.skipped_count
        errors.extend(result.errors)

    return RefreshResult(ok_count=ok_count, skipped_count=skipped_count, errors=errors)


def get_quotes(db: Session, items: list[str | tuple[str, str]]):
    quotes = {}
    backends = _current_backends()
    for market, market_items in _group(items).items():
        backend = backends.get(market)
        if backend is None:
            for symbol, _ in market_items:
                log.warning("quotes.dispatcher.skip", symbol=symbol, market=market)
            continue
        quotes.update(backend.get_quotes(db, market_items))
    return quotes
