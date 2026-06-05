"""yfinance daily OHLC fetcher for foreign markets."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date as date_type
from decimal import Decimal, InvalidOperation
from typing import Iterable

import pandas as pd
import structlog
import yfinance as yf
from sqlalchemy.orm import Session

from ...models.price_history import PriceHistory
from .fx_rate_service import RefreshResult

log = structlog.get_logger(__name__)

_SYMBOL_SUFFIX = {"US": "", "LSE": ".L"}


@dataclass(frozen=True)
class QuoteRow:
    symbol: str
    market: str
    date: date_type
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    volume: int | None
    currency: str


def _normalize_items(items: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    normalized: list[tuple[str, str]] = []
    for symbol, market in items:
        normalized.append((str(symbol).strip().upper(), str(market or "TW").strip().upper()))
    return normalized


def _yf_symbol(symbol: str, market: str) -> str:
    suffix = _SYMBOL_SUFFIX[market]
    if suffix and not symbol.endswith(suffix):
        return f"{symbol}{suffix}"
    return symbol


def _decimal_or_none(raw: object) -> Decimal | None:
    if raw is None or pd.isna(raw):
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def _decimal_required(raw: object, field: str) -> Decimal:
    value = _decimal_or_none(raw)
    if value is None or value <= 0:
        raise ValueError(f"missing or invalid {field}")
    return value


def _regular_market_price(meta: dict[str, object]) -> Decimal:
    if "regularMarketPrice" not in meta:
        raise ValueError("missing regularMarketPrice")
    return _decimal_required(meta.get("regularMarketPrice"), "regularMarketPrice")


def _meta_for(yf_symbol: str) -> dict[str, object]:
    ticker = yf.Ticker(yf_symbol)
    meta = ticker.get_history_metadata()
    return meta if isinstance(meta, dict) else {}


def _frame_for_symbol(history: pd.DataFrame, yf_symbol: str, group_size: int) -> pd.DataFrame:
    if history.empty:
        return history
    if isinstance(history.columns, pd.MultiIndex):
        if yf_symbol not in history.columns.get_level_values(0):
            return pd.DataFrame()
        return history.xs(yf_symbol, axis=1, level=0, drop_level=True)
    if group_size == 1:
        return history
    return pd.DataFrame()


def _latest_ohlc_row(frame: pd.DataFrame) -> tuple[object, object]:
    if frame.empty or "Close" not in frame:
        raise ValueError("missing price history")
    valid = frame[frame["Close"].notna()]
    if valid.empty:
        raise ValueError("missing close")
    return valid.index[-1], valid.iloc[-1]


def _date_from_index(value: object) -> date_type:
    if hasattr(value, "date"):
        return value.date()
    return date_type.fromisoformat(str(value)[:10])


def _skip(symbol: str, reason: str, errors: list[str]) -> None:
    error = f"{symbol}: {reason}"
    errors.append(error)
    log.warning("quotes.yfinance.skip", symbol=symbol, reason=reason)


def fetch(items: list[tuple[str, str]]) -> tuple[list[QuoteRow], list[str]]:
    rows: list[QuoteRow] = []
    errors: list[str] = []

    grouped: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for symbol, market in _normalize_items(items):
        if market not in _SYMBOL_SUFFIX:
            _skip(symbol, f"unsupported market {market}", errors)
            continue
        grouped[market].append((symbol, market, _yf_symbol(symbol, market)))

    for market_items in grouped.values():
        yf_symbols = [yf_symbol for _symbol, _market, yf_symbol in market_items]
        try:
            history = yf.download(
                yf_symbols,
                period="7d",
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                progress=False,
                threads=True,
            )
        except Exception as exc:  # noqa: BLE001
            for symbol, _market, yf_symbol in market_items:
                _skip(symbol, f"{yf_symbol} download failed: {exc}", errors)
            continue

        for symbol, market, yf_symbol in market_items:
            try:
                meta = _meta_for(yf_symbol)
                currency = meta.get("currency")
                if not isinstance(currency, str) or not currency.strip():
                    raise ValueError("missing currency")
                _regular_market_price(meta)
                frame = _frame_for_symbol(history, yf_symbol, len(market_items))
                row_date_raw, row = _latest_ohlc_row(frame)
                close = _decimal_required(row.get("Close"), "close")
                rows.append(
                    QuoteRow(
                        symbol=symbol,
                        market=market,
                        date=_date_from_index(row_date_raw),
                        open=_decimal_or_none(row.get("Open")),
                        high=_decimal_or_none(row.get("High")),
                        low=_decimal_or_none(row.get("Low")),
                        close=close,
                        volume=(
                            int(row.get("Volume"))
                            if row.get("Volume") is not None and not pd.isna(row.get("Volume"))
                            else None
                        ),
                        currency=currency.strip(),
                    )
                )
            except Exception as exc:  # noqa: BLE001 - per-ticker isolation
                _skip(symbol, str(exc), errors)

    return rows, errors


def get_quotes(db: Session, items: list[tuple[str, str]]) -> dict[tuple[str, str], dict]:
    """Return latest persisted yfinance quotes keyed by (symbol, market).

    Missing or unsupported items are omitted from the returned mapping.
    """
    quotes: dict[tuple[str, str], dict] = {}
    for symbol, market in _normalize_items(items):
        if market not in _SYMBOL_SUFFIX:
            continue
        row = (
            db.query(PriceHistory)
            .filter(PriceHistory.symbol == symbol)
            .filter(PriceHistory.market == market)
            .order_by(PriceHistory.date.desc())
            .first()
        )
        if row is None:
            continue
        quotes[(symbol, market)] = {
            "close": Decimal(row.close),
            "date": row.date,
            "currency": row.currency,
            "source": row.source,
        }
    return quotes


def refresh_daily_ohlc(db: Session, items: list[tuple[str, str]]) -> RefreshResult:
    rows, errors = fetch(items)
    for row in rows:
        db.merge(
            PriceHistory(
                symbol=row.symbol,
                market=row.market,
                date=row.date,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                volume=row.volume,
                turnover=None,
                currency=row.currency,
                source="yfinance",
            )
        )
    db.commit()
    return RefreshResult(ok_count=len(rows), skipped_count=len(errors), errors=errors)
