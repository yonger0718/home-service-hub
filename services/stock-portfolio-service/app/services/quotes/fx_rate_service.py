"""Daily yfinance FX-rate snapshots for foreign holding revaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import structlog
import yfinance as yf
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.fx_rate import FXRate

log = structlog.get_logger(__name__)

_SUPPORTED_CURRENCIES = ("USD", "GBP")
_YF_TICKER_MAP = {"USD": "USDTWD=X", "GBP": "GBPTWD=X"}
_TAIPEI = ZoneInfo("Asia/Taipei")
_RATE_QUANT = Decimal("0.00000001")


@dataclass(frozen=True)
class RefreshResult:
    ok_count: int
    skipped_count: int
    errors: list[str]


def _today_taipei() -> date:
    return datetime.now(_TAIPEI).date()


def _decimal_price(raw: object) -> Decimal:
    if raw is None or isinstance(raw, bool):
        raise ValueError("missing regularMarketPrice")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"non-numeric regularMarketPrice: {raw!r}") from exc
    if not value.is_finite() or value <= 0:
        raise ValueError(f"invalid regularMarketPrice: {raw!r}")
    return value.quantize(_RATE_QUANT)


def _fetch_rate_to_twd(yf_symbol: str) -> Decimal:
    ticker = yf.Ticker(yf_symbol)
    fast_info = ticker.fast_info
    if hasattr(fast_info, "get"):
        raw = fast_info.get("regularMarketPrice")
        if raw is None:
            raw = fast_info.get("lastPrice")
    else:
        raw = getattr(fast_info, "regularMarketPrice", None)
        if raw is None:
            raw = getattr(fast_info, "lastPrice", None)
    return _decimal_price(raw)


def _upsert_rate(
    db: Session,
    *,
    currency: str,
    date_: date,
    rate_to_twd: Decimal,
    source: str = "yfinance",
) -> None:
    if currency == "GBp":
        raise ValueError("GBp must not be written to fx_rates; write GBP instead")
    normalized = (currency or "").strip().upper()
    if normalized not in _SUPPORTED_CURRENCIES:
        raise ValueError(f"unsupported FX currency: {currency}")
    if rate_to_twd <= 0:
        raise ValueError("rate_to_twd must be > 0")
    db.merge(
        FXRate(
            currency=normalized,
            date=date_,
            rate_to_twd=rate_to_twd.quantize(_RATE_QUANT),
            source=source,
        )
    )
    db.commit()


def backfill_range(db: Session, *, period: str = "6mo") -> RefreshResult:
    """Backfill USD/GBP TWD rates for the trailing ``period`` from yfinance.

    Triggered manually (CLI) when users upload broker CSVs with trade dates
    that predate the daily ``refresh_today`` cron. Per-currency isolation —
    a yfinance failure on one ticker does not abort the batch.
    """
    ok_count = 0
    skipped_count = 0
    errors: list[str] = []

    for currency in _SUPPORTED_CURRENCIES:
        yf_symbol = _YF_TICKER_MAP[currency]
        try:
            history = yf.Ticker(yf_symbol).history(period=period, auto_adjust=False)
            if history.empty:
                raise ValueError("empty history")
            for row_index, row in history.iterrows():
                close = row.get("Close")
                if close is None or close != close:  # NaN-safe (no pandas import)
                    continue
                row_date = row_index.date() if hasattr(row_index, "date") else row_index
                _upsert_rate(
                    db,
                    currency=currency,
                    date_=row_date,
                    rate_to_twd=Decimal(str(close)).quantize(_RATE_QUANT),
                )
                ok_count += 1
        except Exception as exc:  # noqa: BLE001 - per-ticker isolation
            db.rollback()
            skipped_count += 1
            error = f"{currency} ({yf_symbol}): {exc}"
            errors.append(error)
            log.warning(
                "quotes.fx_rate.backfill_skip",
                currency=currency,
                yf_symbol=yf_symbol,
                reason=str(exc),
            )

    return RefreshResult(ok_count=ok_count, skipped_count=skipped_count, errors=errors)


def refresh_today(db: Session) -> RefreshResult:
    today = _today_taipei()
    ok_count = 0
    skipped_count = 0
    errors: list[str] = []

    for currency in _SUPPORTED_CURRENCIES:
        yf_symbol = _YF_TICKER_MAP[currency]
        try:
            rate = _fetch_rate_to_twd(yf_symbol)
            _upsert_rate(db, currency=currency, date_=today, rate_to_twd=rate)
            ok_count += 1
        except Exception as exc:  # noqa: BLE001 - per-ticker isolation
            db.rollback()
            skipped_count += 1
            error = f"{currency} ({yf_symbol}): {exc}"
            errors.append(error)
            log.warning(
                "quotes.fx_rate.skip",
                currency=currency,
                yf_symbol=yf_symbol,
                reason=str(exc),
            )

    return RefreshResult(ok_count=ok_count, skipped_count=skipped_count, errors=errors)


def get_rate(db: Session, currency: str, as_of: date) -> Decimal | None:
    if currency == "GBp":
        base = "GBP"
        divisor = Decimal("100")
    else:
        base = (currency or "").strip().upper()
        divisor = Decimal("1")

    if base == "TWD":
        return Decimal("1").quantize(_RATE_QUANT)
    if base not in _SUPPORTED_CURRENCIES:
        return None

    rate = db.execute(
        select(FXRate.rate_to_twd)
        .where(FXRate.currency == base, FXRate.date <= as_of)
        .order_by(FXRate.date.desc())
        .limit(1)
    ).scalar_one_or_none()
    if rate is None:
        return None
    return (Decimal(rate) / divisor).quantize(_RATE_QUANT)
