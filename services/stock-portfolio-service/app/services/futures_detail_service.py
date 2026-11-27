"""期貨明細 page data: month list, month summary and per-day fills / deposits from stored statement rows."""
from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.sinopac_futures_statement import FuturesStatementDay, FuturesStatementMonth


def _amount(value) -> str:
    return f"{Decimal(value):.4f}"


def _price(value) -> str:
    return str(Decimal(value))


def months(db: Session) -> dict:
    keys = {d.strftime("%Y-%m") for d in db.scalars(select(FuturesStatementDay.statement_date))}
    keys |= {d.strftime("%Y-%m") for d in db.scalars(select(FuturesStatementMonth.month))}
    return {"months": sorted(keys, reverse=True)}


def _fill(raw: dict) -> dict:
    return {
        "kind": "option" if raw.get("right") else "future",
        "order_no": raw["order_no"], "product": raw["product"], "month": raw["month"],
        "strike": None if raw.get("strike") is None else _price(raw["strike"]),
        "right": raw.get("right"), "action": raw["action"], "qty": raw["qty"],
        "price": _price(raw["price"]), "tax": _amount(raw["tax"]), "fee": _amount(raw["fee"]),
        "pnl": _amount(raw["pnl"]),
        "closes": [
            {"date": c["date"], "qty": c["qty"], "price": _price(c["price"])}
            for c in raw.get("closes") or []
        ],
    }


def _day(row: FuturesStatementDay) -> dict:
    fills = [_fill(f) for f in row.fills or []]
    total = row.fills_total
    return {
        "date": row.statement_date.isoformat(),
        "no_trade": not fills,
        "fills": fills,
        "fills_total": None if not total else {k: _amount(total[k]) for k in ("tax", "fee", "pnl")},
        "deposits": [
            {"direction": d["direction"], "amount": _amount(d["amount"])} for d in row.deposits or []
        ],
    }


def _signed_premium(raw: dict) -> str:
    """The stored 權利金市值 is unsigned; a sold (賣) option counts negative, as in the equity identity."""
    value = _amount(raw["premium_value"])
    if raw["side"] == "賣" and Decimal(value) != 0:
        return _amount(-Decimal(value))
    return value


def _position(raw: dict) -> dict:
    return {
        "product": raw["product"], "month": raw["month"],
        "strike": None if raw.get("strike") is None else _price(raw["strike"]),
        "right": raw.get("right"), "side": raw["side"], "qty": raw["qty"],
        "entry_price": _price(raw["entry_price"]), "mark_price": _price(raw["mark_price"]),
        "unrealized": _amount(raw["unrealized"]), "premium_value": _signed_premium(raw),
    }


def _positions(db: Session, cutoff: date, month_row: FuturesStatementMonth | None) -> dict | None:
    source = db.scalars(
        select(FuturesStatementDay)
        .where(FuturesStatementDay.statement_date <= cutoff)
        .order_by(FuturesStatementDay.statement_date.desc())
        .limit(1)
    ).first()
    if source is None:
        return None
    items = [_position(p) for p in source.positions or []]
    return {
        "date": source.statement_date.isoformat(),
        "month_end_may_differ": month_row is not None and month_row.open_positions != len(items),
        "items": items,
    }


def detail(db: Session, month: str) -> dict | None:
    first = date(int(month[:4]), int(month[5:]), 1)
    last = date(first.year, first.month, calendar.monthrange(first.year, first.month)[1])
    rows = list(db.scalars(
        select(FuturesStatementDay)
        .where(FuturesStatementDay.statement_date >= first, FuturesStatementDay.statement_date <= last)
        .order_by(FuturesStatementDay.statement_date)
    ))
    month_row = db.get(FuturesStatementMonth, first)
    if not rows and month_row is None:
        return None

    if month_row is not None:
        have = {r.statement_date.isoformat() for r in rows}
        missing = sum(1 for d in month_row.activity_dates or [] if d not in have)
        summary = {
            "futures_realized": _amount(month_row.k), "premium_net": _amount(month_row.g),
            "fees_and_tax": _amount(month_row.h), "deposits_net": _amount(month_row.b),
            "source": "monthly", "complete": True, "reason": None, "missing_days": missing,
        }
    else:
        findings = any(r.findings for r in rows)
        summary = {
            **{
                out: _amount(sum((getattr(r, col) for r in rows), Decimal(0)))
                for out, col in (
                    ("futures_realized", "futures_realized"), ("premium_net", "premium_net"),
                    ("fees_and_tax", "fees_and_tax"), ("deposits_net", "deposits_net"),
                )
            },
            "source": "daily_sum", "complete": not findings,
            "reason": "findings" if findings else None, "missing_days": 0,
        }
    return {
        "month": month, "summary": summary,
        "days": [_day(r) for r in reversed(rows)],
        "positions": _positions(db, month_row.month_end if month_row is not None else last, month_row),
    }
