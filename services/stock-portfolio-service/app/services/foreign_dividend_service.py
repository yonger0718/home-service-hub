from __future__ import annotations

from datetime import datetime, time, timezone
from decimal import Decimal, InvalidOperation

import structlog
import yfinance as yf
from sqlalchemy.orm import Session

from ..models import portfolio as models
from ..models.fx_rate import FXRate
from . import portfolio_service

log = structlog.get_logger(__name__)


def _open_foreign_positions(db: Session) -> list[tuple[str, str]]:
    active = portfolio_service._aggregate_active_holdings(
        portfolio_service._load_adjusted_transactions(db),
        None,
    )
    return [
        (symbol, market)
        for (symbol, market), info in sorted(active.items())
        if market in {"US", "LSE"} and Decimal(info["total_quantity"]) > 0
    ]


def _fast_info_currency(ticker: object) -> str:
    fast_info = getattr(ticker, "fast_info", {})
    if hasattr(fast_info, "get"):
        raw = fast_info.get("currency")
    else:
        raw = getattr(fast_info, "currency", None)
    currency = (raw or "").strip()
    if not currency:
        raise ValueError("missing yfinance fast_info.currency")
    return currency


def _fx_lookup_currency(currency: str) -> str:
    return "GBP" if currency == "GBp" else currency.upper()


def _exact_fx_rate(db: Session, currency: str, ex_date) -> Decimal | None:
    base = _fx_lookup_currency(currency)
    if base == "TWD":
        return None
    row = (
        db.query(FXRate.rate_to_twd)
        .filter(FXRate.currency == base)
        .filter(FXRate.date == ex_date)
        .scalar()
    )
    return Decimal(row) if row is not None else None


def _decimal_amount(raw: object) -> Decimal:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"non-numeric dividend amount: {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"invalid dividend amount: {raw!r}")
    return value


def refresh_today(db: Session) -> dict[str, object]:
    inserted = 0
    updated = 0
    skipped = 0
    errors: list[str] = []
    positions = _open_foreign_positions(db)
    for symbol, market in positions:
        try:
            ticker = yf.Ticker(symbol)
            currency = _fast_info_currency(ticker)
            dividends = ticker.dividends
            for index, raw_amount in dividends.items():
                ex_date = index.date() if hasattr(index, "date") else index
                fx_rate = _exact_fx_rate(db, currency, ex_date)
                if _fx_lookup_currency(currency) != "TWD" and fx_rate is None:
                    skipped += 1
                    log.warning(
                        "quotes.foreign_dividends.skip",
                        symbol=symbol,
                        market=market,
                        ex_dividend_date=ex_date.isoformat(),
                        reason="missing_fx",
                    )
                    continue
                amount = _decimal_amount(raw_amount)
                ex_dt = datetime.combine(ex_date, time.min, tzinfo=timezone.utc)
                existing = (
                    db.query(models.Dividend)
                    .filter(models.Dividend.symbol == symbol)
                    .filter(models.Dividend.market == market)
                    .filter(models.Dividend.ex_dividend_date == ex_dt)
                    .one_or_none()
                )
                if existing is None:
                    db.add(
                        models.Dividend(
                            symbol=symbol,
                            market=market,
                            amount=amount,
                            currency=currency,
                            fx_rate_to_twd=fx_rate,
                            ex_dividend_date=ex_dt,
                            received_date=ex_dt,
                            fee=Decimal("0"),
                            tax=Decimal("0"),
                            cash_dividend_per_share=amount,
                            source="yfinance",
                        )
                    )
                    inserted += 1
                else:
                    existing.amount = amount
                    existing.currency = currency
                    existing.fx_rate_to_twd = fx_rate
                    existing.cash_dividend_per_share = amount
                    existing.source = "yfinance"
                    updated += 1
            db.commit()
        except Exception as exc:  # noqa: BLE001 - one bad ticker must not abort batch
            db.rollback()
            skipped += 1
            error = f"{symbol} ({market}): {exc}"
            errors.append(error)
            log.warning(
                "quotes.foreign_dividends.skip",
                symbol=symbol,
                market=market,
                reason=str(exc),
            )
    return {
        "requested": len(positions),
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
    }
