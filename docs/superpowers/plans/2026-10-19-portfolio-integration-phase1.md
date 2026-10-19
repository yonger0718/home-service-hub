# Portfolio integration — Phase 1 (國泰 settlement match, 117 read-through valuation, dividends) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bank 交割股款 lines on account 67 are explained from the portfolio's trades and booked as 67↔117 transfers under one booking owner; account 117 (and any `valuation_source` account) shows the portfolio's market value without valuation entries; dividend lines on 67 are booked as income; a portfolio import marks 67 dirty.

**Architecture:** The stock-portfolio-service gains three read endpoints (`/settlements`, `/funding`, `/valuation`) and a TW trading calendar table. The accounting-service gains `account.settlement_source` / `valuation_source`, the `settlement_booking` owner tables, a `portfolio_client` with a per-reconcile cache, a global settlement assignment step before `matching.match`, two pair-creation actions (`book_settlement`, `book_dividend`) on a new `_PairCreation` base, and read-through valuation in the account read models only (`ledger_service.account_balance` stays ledger-only). Nothing synchronises: every write carries portfolio ids and source facts; re-runs are no-ops or `source_drift` cases.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2, Alembic (both services), httpx (accounting → portfolio), pytest (`tests/unit`, `tests/integration`, `tests/worker` in accounting; `tests/unit`, `tests/integration` in portfolio), PostgreSQL.

**Spec:** `docs/superpowers/specs/2026-10-19-portfolio-integration-design.md` rev 2 (`d52b5a1`), reviewed PASS-with-Should on AGENT-176; the Shoulds listed there are tasks here (Tasks 4, 7, 9, 10).

## Global Constraints

- Amounts never appear in log lines, exception text, run summaries or latch files; messages carry ids, dates, counts and codes only (project rule; see `derive.SignError` 2026-10-19).
- Tokens (HomeHub ingest token used by the portfolio call-back, portfolio read token used by HomeHub) live in env files (0600), never in the repo, logs, API bodies or test fixtures.
- Test data is synthetic (no real statement text, MOZE rows or broker exports); fixtures reuse `tests/integration/statement_fixtures.py` (`_card`, `_submit`, `_line`, `_active`, `_cases`, `run`) and `seed`.
- `ledger_service.account_balance` keeps its contract (opening + Σ posted entries); valuation replacement happens only in `list_accounts` / `get_account` read models (spec §5; AGENT-176 Should 1). `held_balance` (`reconciliation_service.py:521`) and period `end_balance` (`ledger_service.py:627`) are unaffected and tested so.
- Settlement lines are never candidates of `backfill_entry` / `create_system_entry` (spec §4.3).
- Automatic sum matching (spec §9) stays disabled in Phase 1 (`settlement_sum_matching: false`), code path present and tested with a synthetic calendar-fault fixture, enabled only after a real TWSE closure fixture exists (AGENT-176 Should 4).
- No change to `composite_map`, `account_map`, matching rules for non-settlement lines, or the SPA (Phase 1 ships API fields; the SPA reads them in a later frontend task).
- Every migration is additive (new columns nullable, new tables, enum values appended); downgrade removes them.

## Review Focus

1. A settlement line whose portfolio net differs by the fee rounding of one trade (import edited) must not book; it must become `source_drift` only if already booked, else stay pending — Task 6 test `test_net_mismatch_stays_pending`.
2. Two equal buys two days apart with two equal bank lines: nothing books, both ambiguous — Task 5 test `test_two_lines_one_amount_two_settlements_are_ambiguous`.
3. The portfolio down during the daily sweep must leave every settlement line pending with `settlement_source_unavailable` on the result, and the rest of the statement reconciled normally — Task 6 test `test_portfolio_unavailable_reconciles_the_rest`.
4. A historical `as_of` on `GET /accounts/117` must return that date's valuation, never today's; a cold cache with the portfolio down returns `valuation: null, stale: true` — Task 8 tests.
5. Revert of a booking after the next reconcile has already re-claimed the line by the booking's leg must still delete both legs and reopen the line as `settlement_pending` — Task 7 test `test_revert_after_reconcile_reopens_pending`.

---

### Task 1: Portfolio — TW trading calendar table and settlement-date derivation

**Files:**
- Create: `services/stock-portfolio-service/alembic/versions/n1a2b3c4d5e6_tw_trading_calendar.py`
- Create: `services/stock-portfolio-service/app/services/trading_calendar_service.py`
- Modify: `services/stock-portfolio-service/app/models/portfolio.py` (append the model)
- Test: `services/stock-portfolio-service/tests/unit/test_trading_calendar.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `models.TwTradingCalendar(date: Date PK, is_trading_day: bool, note: str|None)`; `trading_calendar_service.settlement_date(db, trade_date: date, days: int = 2) -> date | None` (None past `calendar_through`); `trading_calendar_service.calendar_through(db) -> date | None`; `trading_calendar_service.load_rows(db, rows: list[tuple[date, bool, str | None]]) -> int` (upsert; validation: contiguous dates per loaded year, weekends must be non-trading, else `ValueError`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_trading_calendar.py
from datetime import date
import pytest
from app.services import trading_calendar_service as cal


def _load_week(db):
    # 2026-10-05 (Mon) .. 2026-10-11 (Sun); Thursday 10-08 is a holiday
    rows = [(date(2026, 10, d), d not in (8, 10, 11), "holiday" if d == 8 else None) for d in range(5, 12)]
    assert cal.load_rows(db, rows) == 7


def test_settlement_date_skips_weekend_and_holiday(db_session):
    _load_week(db_session)
    assert cal.settlement_date(db_session, date(2026, 10, 5)) == date(2026, 10, 7)   # Mon → Wed
    assert cal.settlement_date(db_session, date(2026, 10, 6)) == date(2026, 10, 9)   # Tue → Fri (Thu holiday)
    assert cal.settlement_date(db_session, date(2026, 10, 7)) is None                # +2 trading days past the loaded range


def test_calendar_through_and_unknown(db_session):
    assert cal.calendar_through(db_session) is None
    _load_week(db_session)
    assert cal.calendar_through(db_session) == date(2026, 10, 11)


def test_load_rows_validates_weekends_and_gaps(db_session):
    with pytest.raises(ValueError, match="weekend"):
        cal.load_rows(db_session, [(date(2026, 10, 10), True, None)])  # Saturday marked trading
    with pytest.raises(ValueError, match="gap"):
        cal.load_rows(db_session, [(date(2026, 10, 5), True, None), (date(2026, 10, 7), True, None)])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/stock-portfolio-service && .venv/bin/python -m pytest -q tests/unit/test_trading_calendar.py`
Expected: FAIL with `ModuleNotFoundError: app.services.trading_calendar_service`

- [ ] **Step 3: Write the model, migration and service**

```python
# app/models/portfolio.py (append)
class TwTradingCalendar(Base):
    __tablename__ = "tw_trading_calendar"
    date = Column(Date, primary_key=True)
    is_trading_day = Column(Boolean, nullable=False)
    note = Column(String(64), nullable=True)
```

```python
# alembic/versions/n1a2b3c4d5e6_tw_trading_calendar.py
"""tw_trading_calendar

Revision ID: n1a2b3c4d5e6
Revises: m0b1c2d3e4f6
"""
from alembic import op
import sqlalchemy as sa

revision = "n1a2b3c4d5e6"
down_revision = "m0b1c2d3e4f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tw_trading_calendar",
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("is_trading_day", sa.Boolean(), nullable=False),
        sa.Column("note", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("tw_trading_calendar")
```

```python
# app/services/trading_calendar_service.py
"""TW trading calendar: settlement dates for TWSE/TPEx trades (T+2 trading days).

Loaded by the owner from TWSE's published holiday schedule (yearly); nothing here guesses a holiday.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..models import portfolio as models


def calendar_through(db: Session) -> date | None:
    return db.scalar(select(func.max(models.TwTradingCalendar.date)))


def settlement_date(db: Session, trade_date: date, days: int = 2) -> date | None:
    """The `days`-th trading day after `trade_date`, or None when the calendar does not reach it."""
    through = calendar_through(db)
    if through is None or trade_date > through:
        return None
    rows = db.execute(
        select(models.TwTradingCalendar.date)
        .where(models.TwTradingCalendar.date > trade_date, models.TwTradingCalendar.is_trading_day.is_(True))
        .order_by(models.TwTradingCalendar.date)
        .limit(days)
    ).scalars().all()
    if len(rows) < days:
        return None
    return rows[-1]


def load_rows(db: Session, rows: list[tuple[date, bool, str | None]]) -> int:
    """Upsert calendar rows. Weekends must be non-trading; the loaded dates must be contiguous."""
    ordered = sorted(rows)
    for d, trading, _ in ordered:
        if trading and d.weekday() >= 5:
            raise ValueError(f"weekend marked trading: {d.isoformat()}")
    for prev, nxt in zip(ordered, ordered[1:]):
        if nxt[0] - prev[0] != timedelta(days=1):
            raise ValueError(f"gap between {prev[0].isoformat()} and {nxt[0].isoformat()}")
    for d, trading, note in ordered:
        db.execute(
            pg_insert(models.TwTradingCalendar)
            .values(date=d, is_trading_day=trading, note=note)
            .on_conflict_do_update(index_elements=["date"], set_={"is_trading_day": trading, "note": note})
        )
    db.flush()
    return len(ordered)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd services/stock-portfolio-service && .venv/bin/python -m pytest -q tests/unit/test_trading_calendar.py`
Expected: 3 passed

- [ ] **Step 5: Add the loader command and README line**

Create `app/cli/load_tw_calendar.py` (argparse: `--year 2026 --csv path`; CSV columns `date,is_trading_day,note`; prints the count only) and append to `services/stock-portfolio-service/README.md`: "TW trading calendar: `python -m app.cli.load_tw_calendar --csv tw-2026.csv` loads one year from TWSE's holiday schedule; `settlement_date` answers None past the loaded range."

- [ ] **Step 6: Commit**

```bash
git add services/stock-portfolio-service/alembic/versions/n1a2b3c4d5e6_tw_trading_calendar.py services/stock-portfolio-service/app/models/portfolio.py services/stock-portfolio-service/app/services/trading_calendar_service.py services/stock-portfolio-service/app/cli/load_tw_calendar.py services/stock-portfolio-service/tests/unit/test_trading_calendar.py services/stock-portfolio-service/README.md
git commit -m "portfolio: TW trading calendar table, loader and T+2 settlement dates"
```

---

### Task 2: Portfolio — `GET /settlements`, `GET /funding`, `GET /valuation`

**Files:**
- Create: `services/stock-portfolio-service/app/services/ledger_feed_service.py`
- Create: `services/stock-portfolio-service/app/routers/ledger_feed.py`
- Modify: `services/stock-portfolio-service/app/main.py` (include the router)
- Modify: `services/stock-portfolio-service/app/schemas.py` (three response models)
- Test: `services/stock-portfolio-service/tests/integration/test_ledger_feed.py`

**Interfaces:**
- Consumes: Task 1 `settlement_date`, `calendar_through`; existing `cash_flow_service.get_broker_balance(db, broker, as_of_date)` and `list_balances`; `models.BrokerAccount`, `models.CashTransaction`, `models.BrokerCashFlow`, `portfolio_service` holdings (`_load_adjusted_transactions`, price lookup via `market_data_service` latest close on or before a date).
- Produces (read scope, JSON):
  - `GET /ledger-feed/settlements?broker=cathay&from=&to=` → `[{settlement_date|null, trade_date, net_amount, currency, components: {buy_settle, sell_settle, fee, tax}, cash_transaction_ids: [int], fingerprint}]`, plus header-level `calendar_through`. `from`/`to` filter the DERIVED settlement date; rows whose settlement date is unknown are returned when `trade_date ≥ from − 10 days` with `settlement_date: null`.
  - `GET /ledger-feed/funding?broker=ib&from=&to=` → `[{date, type: deposit|withdrawal, amount, currency, source_id: "bcf:<id>"|"ct:<id>", note}]`.
  - `GET /ledger-feed/valuation?broker=cathay&as_of=` → `{broker, currency, as_of, priced_through, market_value, cash, total}`.
  - `fingerprint = f"{broker}:{trade_date}:{sha256(','.join(sorted ids)).hexdigest()[:16]}"`.

- [ ] **Step 1: Write the failing integration tests**

```python
# tests/integration/test_ledger_feed.py
from datetime import date
from decimal import Decimal

from app.models import portfolio as models
from app.services import trading_calendar_service as cal


def _calendar(db):
    rows = [(date(2026, 9, d), date(2026, 9, d).weekday() < 5, None) for d in range(1, 31)]
    cal.load_rows(db, rows)


def _cathay(db):
    acct = models.BrokerAccount(broker="cathay", nickname="國泰", currency="TWD", opening_balance=Decimal("0"),
                                opening_date=date(2026, 1, 1), is_active=True)
    db.add(acct); db.flush(); return acct


def _cash(db, acct, day, type_, amount, txn=None):
    row = models.CashTransaction(account_id=acct.id, txn_date=day, type=type_, amount=Decimal(amount), currency="TWD",
                                 related_transaction_id=txn, source="csv_import", import_fingerprint=f"t-{type_}-{day}-{amount}")
    db.add(row); db.flush(); return row


def test_settlements_net_per_trade_date_with_derived_settlement_date(client, db_session):
    _calendar(db_session); acct = _cathay(db_session)
    a = _cash(db_session, acct, date(2026, 9, 10), "buy_settle", "-100000")
    b = _cash(db_session, acct, date(2026, 9, 10), "fee", "-142")
    _cash(db_session, acct, date(2026, 9, 11), "sell_settle", "50000")
    _cash(db_session, acct, date(2026, 9, 11), "tax", "-150")
    db_session.commit()
    body = client.get("/ledger-feed/settlements", params={"broker": "cathay", "from": "2026-09-01", "to": "2026-09-30"}).json()
    assert body["calendar_through"] == "2026-09-30"
    rows = {r["trade_date"]: r for r in body["rows"]}
    assert rows["2026-09-10"]["settlement_date"] == "2026-09-14" and rows["2026-09-10"]["net_amount"] == "-100142.0000"
    assert rows["2026-09-10"]["components"] == {"buy_settle": 1, "sell_settle": 0, "fee": 1, "tax": 0}
    assert sorted(rows["2026-09-10"]["cash_transaction_ids"]) == sorted([a.id, b.id])
    assert rows["2026-09-11"]["net_amount"] == "49850.0000" and rows["2026-09-11"]["settlement_date"] == "2026-09-15"
    assert rows["2026-09-10"]["fingerprint"].startswith("cathay:2026-09-10:")


def test_settlement_past_calendar_is_null_and_zero_nets_are_omitted(client, db_session):
    _calendar(db_session); acct = _cathay(db_session)
    _cash(db_session, acct, date(2026, 9, 29), "buy_settle", "-1000")          # +2 trading days = Oct 1, past the calendar
    _cash(db_session, acct, date(2026, 9, 3), "buy_settle", "-500")
    _cash(db_session, acct, date(2026, 9, 3), "sell_settle", "500")             # nets to zero
    db_session.commit()
    rows = client.get("/ledger-feed/settlements", params={"broker": "cathay", "from": "2026-09-01", "to": "2026-10-05"}).json()["rows"]
    by = {r["trade_date"]: r for r in rows}
    assert by["2026-09-29"]["settlement_date"] is None and "2026-09-03" not in by


def test_funding_reads_broker_cash_flows_for_foreign_and_cash_transaction_for_cathay(client, db_session):
    acct = _cathay(db_session)
    ct = _cash(db_session, acct, date(2026, 8, 26), "deposit", "20000")
    bcf = models.BrokerCashFlow(broker="IB", date=date(2026, 6, 1), type="DEPOSIT", amount=Decimal("3000"), currency="USD",
                                fx_rate_to_twd=Decimal("32.1"), note="wire", import_fingerprint="ib-dep-1")
    db_session.add(bcf); db_session.commit()
    ib = client.get("/ledger-feed/funding", params={"broker": "ib", "from": "2026-01-01", "to": "2026-12-31"}).json()
    assert ib == [{"date": "2026-06-01", "type": "deposit", "amount": "3000.0000", "currency": "USD", "source_id": f"bcf:{bcf.id}", "note": "wire"}]
    tw = client.get("/ledger-feed/funding", params={"broker": "cathay", "from": "2026-08-01", "to": "2026-08-31"}).json()
    assert tw[0]["source_id"] == f"ct:{ct.id}" and tw[0]["amount"] == "20000.0000"


def test_valuation_uses_last_price_on_or_before_as_of_and_broker_cash(client, db_session, seed_holding):
    # seed_holding: fixture from tests/fixtures — one cathay holding of 1000 shares with prices on 09-10 (100) and 09-15 (110)
    body = client.get("/ledger-feed/valuation", params={"broker": "cathay", "as_of": "2026-09-12"}).json()
    assert body["market_value"] == "100000.0000" and body["priced_through"] == "2026-09-10" and body["cash"] == "0.0000"
    assert body["currency"] == "TWD" and body["total"] == "100000.0000"
```

(`seed_holding` is added to `tests/fixtures/__init__.py` by this task: a `Transaction` buy of 1000 shares of a synthetic symbol on 2026-09-01 with `broker="cathay"` and two `price_history` rows.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/stock-portfolio-service && .venv/bin/python -m pytest -q tests/integration/test_ledger_feed.py`
Expected: FAIL (404 on `/ledger-feed/...`)

- [ ] **Step 3: Implement the service and router**

```python
# app/services/ledger_feed_service.py
from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import portfolio as models
from . import cash_flow_service, trading_calendar_service as cal

SETTLEMENT_TYPES = ("buy_settle", "sell_settle", "fee", "tax")
BROKER_ENUM = {"cathay": "TW_CATHAY", "ib": "IB", "firstrade": "FIRSTRADE", "cs": "SCHWAB"}  # broker_account.broker → Broker enum


def _broker_account(db: Session, broker: str) -> models.BrokerAccount:
    row = db.scalar(select(models.BrokerAccount).where(models.BrokerAccount.broker == broker))
    if row is None:
        raise LookupError(f"broker {broker} not found")
    return row


def fingerprint(broker: str, trade_date: date, ids: list[int]) -> str:
    digest = hashlib.sha256(",".join(str(i) for i in sorted(ids)).encode()).hexdigest()[:16]
    return f"{broker}:{trade_date.isoformat()}:{digest}"


def settlements(db: Session, broker: str, from_: date, to: date) -> dict:
    acct = _broker_account(db, broker)
    rows = db.execute(
        select(models.CashTransaction)
        .where(models.CashTransaction.account_id == acct.id,
               models.CashTransaction.type.in_(SETTLEMENT_TYPES),
               models.CashTransaction.txn_date >= from_ - timedelta(days=10),
               models.CashTransaction.txn_date <= to)
        .order_by(models.CashTransaction.txn_date, models.CashTransaction.id)
    ).scalars().all()
    by_day: dict[date, list[models.CashTransaction]] = defaultdict(list)
    for r in rows:
        by_day[r.txn_date].append(r)
    out = []
    for trade_date, group in sorted(by_day.items()):
        net = sum((Decimal(r.amount) for r in group), Decimal(0))
        if net == 0:
            continue
        sd = cal.settlement_date(db, trade_date)
        if sd is not None and not (from_ <= sd <= to):
            continue
        comps = {t: sum(1 for r in group if r.type == t) for t in SETTLEMENT_TYPES}
        ids = [r.id for r in group]
        out.append({"settlement_date": sd, "trade_date": trade_date, "net_amount": net, "currency": acct.currency,
                    "components": comps, "cash_transaction_ids": ids, "fingerprint": fingerprint(broker, trade_date, ids)})
    return {"calendar_through": cal.calendar_through(db), "rows": out}


def funding(db: Session, broker: str, from_: date, to: date) -> list[dict]:
    if broker == "cathay":
        acct = _broker_account(db, broker)
        rows = db.execute(select(models.CashTransaction).where(
            models.CashTransaction.account_id == acct.id, models.CashTransaction.type.in_(("deposit", "withdrawal")),
            models.CashTransaction.txn_date.between(from_, to)).order_by(models.CashTransaction.txn_date)).scalars().all()
        return [{"date": r.txn_date, "type": r.type, "amount": abs(Decimal(r.amount)), "currency": r.currency,
                 "source_id": f"ct:{r.id}", "note": r.note} for r in rows]
    enum = BROKER_ENUM[broker]
    rows = db.execute(select(models.BrokerCashFlow).where(
        models.BrokerCashFlow.broker == enum, models.BrokerCashFlow.type.in_(("DEPOSIT", "WITHDRAWAL")),
        models.BrokerCashFlow.date.between(from_, to)).order_by(models.BrokerCashFlow.date)).scalars().all()
    return [{"date": r.date, "type": r.type.lower(), "amount": abs(Decimal(r.amount)), "currency": r.currency,
             "source_id": f"bcf:{r.id}", "note": r.note} for r in rows]


def valuation(db: Session, broker: str, as_of: date) -> dict:
    acct = _broker_account(db, broker)
    holdings = cash_flow_service.positions_as_of(db, broker, as_of)  # [(symbol, market, quantity)] — see Step 3b
    market_value = Decimal(0)
    priced_through: date | None = None
    for symbol, market, qty in holdings:
        price, price_date = cash_flow_service.last_price_on_or_before(db, symbol, market, as_of)  # Step 3b
        if price is None:
            continue
        market_value += (qty * price)
        priced_through = price_date if priced_through is None else min(priced_through, price_date)
    cash = Decimal(0) if broker == "cathay" else cash_flow_service.get_broker_balance(db, BROKER_ENUM[broker], as_of)
    return {"broker": broker, "currency": acct.currency, "as_of": as_of, "priced_through": priced_through,
            "market_value": market_value.quantize(Decimal("0.0001")), "cash": Decimal(cash).quantize(Decimal("0.0001")),
            "total": (market_value + Decimal(cash)).quantize(Decimal("0.0001"))}
```

Step 3b: `positions_as_of(db, broker, as_of)` and `last_price_on_or_before(db, symbol, market, as_of)` go into `cash_flow_service.py` only if no equivalent exists; the implementer first greps `portfolio_service.py` for the holdings builder used by `/summary` (`_load_adjusted_transactions` + quantity accumulation, `_quantity_at_window_start` at `:261`) and `market_data_service` for the latest-close lookup, and reuses them (wrapping, not copying). The two helpers return `Decimal`s in the broker's currency (no TWD conversion here — spec §5).

```python
# app/routers/ledger_feed.py
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from ..database import get_db
from .. import schemas
from ..services import ledger_feed_service as svc

router = APIRouter(prefix="/ledger-feed", tags=["ledger-feed"])


@router.get("/settlements", response_model=schemas.SettlementsOut)
def get_settlements(broker: str = Query(...), from_: date = Query(alias="from"), to: date = Query(...), db: Session = Depends(get_db)):
    try:
        return svc.settlements(db, broker, from_, to)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.get("/funding", response_model=list[schemas.FundingOut])
def get_funding(broker: str = Query(...), from_: date = Query(alias="from"), to: date = Query(...), db: Session = Depends(get_db)):
    try:
        return svc.funding(db, broker, from_, to)
    except (LookupError, KeyError) as exc:
        raise HTTPException(404, str(exc))


@router.get("/valuation", response_model=schemas.ValuationOut)
def get_valuation(broker: str = Query(...), as_of: date | None = Query(default=None), db: Session = Depends(get_db)):
    try:
        return svc.valuation(db, broker, as_of or date.today())
    except LookupError as exc:
        raise HTTPException(404, str(exc))
```

Schemas (`app/schemas.py`): `SettlementRow(settlement_date: date|None, trade_date: date, net_amount: Decimal, currency: str, components: dict[str,int], cash_transaction_ids: list[int], fingerprint: str)`, `SettlementsOut(calendar_through: date|None, rows: list[SettlementRow])`, `FundingOut(date, type, amount: Decimal, currency, source_id: str, note: str|None)`, `ValuationOut(broker, currency, as_of: date, priced_through: date|None, market_value: Decimal, cash: Decimal, total: Decimal)`; Decimals serialise as strings with 4 decimals (the service's existing `_money` pattern).

Register in `app/main.py`: `app.include_router(ledger_feed.router)` beside the other routers; the read-token middleware the other GET routes use applies unchanged.

- [ ] **Step 4: Run the tests**

Run: `cd services/stock-portfolio-service && .venv/bin/python -m pytest -q tests/integration/test_ledger_feed.py tests/unit`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add services/stock-portfolio-service/app services/stock-portfolio-service/tests
git commit -m "portfolio: ledger-feed read API — settlements per trade date, funding with namespaced ids, per-broker valuation"
```

---

### Task 3: Accounting — schema: `settlement_source`, `valuation_source`, booking tables, case kinds

**Files:**
- Create: `services/accounting-service/alembic/versions/a7c1e2d3f4b5_settlement_booking.py`
- Modify: `services/accounting-service/app/models/ledger.py` (Account: two columns; LedgerEntry: `settlement_booking_id`)
- Modify: `services/accounting-service/app/models/statements.py` (`CASE_KINDS` += `settlement`, `dividend`, `wire`; new models `SettlementBooking`, `SettlementBookingItem`)
- Test: `services/accounting-service/tests/integration/test_migration.py` (extend the existing round-trip test)

**Interfaces:**
- Produces: `Account.settlement_source: str|None`, `Account.valuation_source: str|None`; `LedgerEntry.settlement_booking_id: int|None` (FK `settlement_booking.id`, indexed); `SettlementBooking(id, account_id, event_id, source, fingerprint UNIQUE, net_amount Numeric(18,4), settlement_date Date, trade_date Date, action_id FK reconciliation_action.id nullable, facts JSONB, created_at)`; `SettlementBookingItem(id, booking_id FK ON DELETE CASCADE, source, source_id str, amount Numeric(18,4); UNIQUE(source, source_id))`; enum `case_kind` gains `settlement`, `dividend`, `wire`.

- [ ] **Step 1: Extend the migration round-trip test**

```python
# tests/integration/test_migration.py — add
def test_settlement_booking_tables_and_columns(migrated_engine):
    insp = sa.inspect(migrated_engine)
    assert {"settlement_source", "valuation_source"} <= {c["name"] for c in insp.get_columns("account")}
    assert "settlement_booking_id" in {c["name"] for c in insp.get_columns("ledger_entry")}
    assert "settlement_booking" in insp.get_table_names() and "settlement_booking_item" in insp.get_table_names()
    uniques = {tuple(u["column_names"]) for u in insp.get_unique_constraints("settlement_booking_item")}
    assert ("source", "source_id") in uniques
    with migrated_engine.connect() as conn:
        kinds = conn.execute(sa.text("select unnest(enum_range(null::case_kind))::text")).scalars().all()
    assert {"settlement", "dividend", "wire"} <= set(kinds)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/integration/test_migration.py -k settlement_booking`
Expected: FAIL (columns/tables missing)

- [ ] **Step 3: Migration + models**

```python
# alembic/versions/a7c1e2d3f4b5_settlement_booking.py
"""settlement booking owner, account sources, case kinds

Revision ID: a7c1e2d3f4b5
Revises: f3b1d2c4a9e7
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a7c1e2d3f4b5"
down_revision = "f3b1d2c4a9e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("account", sa.Column("settlement_source", sa.String(64), nullable=True))
    op.add_column("account", sa.Column("valuation_source", sa.String(64), nullable=True))
    op.create_table(
        "settlement_booking",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("statement_event.id"), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(128), nullable=False, unique=True),
        sa.Column("net_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("settlement_date", sa.Date(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=True),
        sa.Column("action_id", sa.Integer(), sa.ForeignKey("reconciliation_action.id"), nullable=True),
        sa.Column("facts", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_settlement_booking_event", "settlement_booking", ["event_id"])
    op.create_table(
        "settlement_booking_item",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("booking_id", sa.Integer(), sa.ForeignKey("settlement_booking.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("amount", sa.Numeric(18, 4), nullable=False),
        sa.UniqueConstraint("source", "source_id", name="ux_settlement_booking_item_source"),
    )
    op.add_column("ledger_entry", sa.Column("settlement_booking_id", sa.Integer(), sa.ForeignKey("settlement_booking.id"), nullable=True))
    op.create_index("ix_ledger_entry_settlement_booking_id", "ledger_entry", ["settlement_booking_id"])
    for value in ("settlement", "dividend", "wire"):
        op.execute(f"ALTER TYPE case_kind ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    op.drop_index("ix_ledger_entry_settlement_booking_id", table_name="ledger_entry")
    op.drop_column("ledger_entry", "settlement_booking_id")
    op.drop_table("settlement_booking_item")
    op.drop_index("ix_settlement_booking_event", table_name="settlement_booking")
    op.drop_table("settlement_booking")
    op.drop_column("account", "valuation_source")
    op.drop_column("account", "settlement_source")
    # enum values are not removed (PostgreSQL cannot drop enum values); documented in README
```

Models: `Account.settlement_source = Column(String(64), nullable=True)`, `Account.valuation_source = Column(String(64), nullable=True)`; `LedgerEntry.settlement_booking_id = Column(Integer, ForeignKey("settlement_booking.id"), nullable=True, index=True)`; in `models/statements.py` append `"settlement", "dividend", "wire"` to `CASE_KINDS` and add the two models mirroring the migration (relationship `items = relationship("SettlementBookingItem", cascade="all, delete-orphan")`).

- [ ] **Step 4: Run the migration tests and the whole unit directory**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/integration/test_migration.py tests/unit`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add services/accounting-service/alembic/versions/a7c1e2d3f4b5_settlement_booking.py services/accounting-service/app/models/ledger.py services/accounting-service/app/models/statements.py services/accounting-service/tests/integration/test_migration.py
git commit -m "accounting: settlement booking owner tables, account settlement/valuation sources, settlement/dividend/wire case kinds"
```

---

### Task 4: Accounting — `portfolio_client` with a per-reconcile cache and the unavailable path

**Files:**
- Create: `services/accounting-service/app/services/portfolio_client.py`
- Modify: `services/accounting-service/app/config.py` (`PORTFOLIO_BASE_URL`, `PORTFOLIO_READ_TOKEN` from env; both optional — absent means "no portfolio")
- Test: `services/accounting-service/tests/unit/test_portfolio_client.py`

**Interfaces:**
- Produces: `PortfolioClient(base_url, token, timeout=5.0)` with `settlements(broker, from_, to) -> SettlementsPage`, `funding(broker, from_, to) -> list[Funding]`, `valuation(broker, as_of) -> Valuation`; dataclasses `Settlement(settlement_date: date|None, trade_date: date, net_amount: Decimal, currency: str, ids: tuple[int, ...], fingerprint: str, components: dict)`, `SettlementsPage(calendar_through: date|None, rows: list[Settlement])`, `Valuation(broker, currency, as_of, priced_through, market_value, cash, total)`; `PortfolioUnavailable(RuntimeError)` on connection error / timeout / 5xx / 404 broker; `client_for(db) -> PortfolioClient | None` (None when unconfigured); `cached(client)` context returning a client whose reads memoise per (method, args) for the current reconcile.

- [ ] **Step 1: Write the failing tests (httpx MockTransport; the token must never appear in exception text or logs)**

```python
# tests/unit/test_portfolio_client.py
import json, logging
from datetime import date
from decimal import Decimal
import httpx, pytest
from app.services import portfolio_client as pc

TOKEN = "secret-token-value"


def _client(handler):
    transport = httpx.MockTransport(handler)
    return pc.PortfolioClient("http://portfolio.test", TOKEN, transport=transport)


def test_settlements_parses_rows_and_sends_bearer():
    seen = {}
    def handler(request):
        seen["auth"] = request.headers.get("authorization"); seen["url"] = str(request.url)
        return httpx.Response(200, json={"calendar_through": "2026-09-30", "rows": [{
            "settlement_date": "2026-09-14", "trade_date": "2026-09-10", "net_amount": "-100142.0000", "currency": "TWD",
            "components": {"buy_settle": 1, "sell_settle": 0, "fee": 1, "tax": 0}, "cash_transaction_ids": [5, 7],
            "fingerprint": "cathay:2026-09-10:abc"}]})
    page = _client(handler).settlements("cathay", date(2026, 9, 1), date(2026, 9, 30))
    assert seen["auth"] == f"Bearer {TOKEN}" and "from=2026-09-01" in seen["url"]
    assert page.calendar_through == date(2026, 9, 30)
    (row,) = page.rows
    assert (row.settlement_date, row.net_amount, row.ids, row.fingerprint) == (date(2026, 9, 14), Decimal("-100142.0000"), (5, 7), "cathay:2026-09-10:abc")


@pytest.mark.parametrize("make", [lambda r: httpx.Response(500), lambda r: httpx.Response(404), lambda r: (_ for _ in ()).throw(httpx.ConnectError("down"))])
def test_failures_raise_unavailable_without_the_token(make, caplog):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(pc.PortfolioUnavailable) as info:
        _client(make).valuation("cathay", date(2026, 9, 30))
    assert TOKEN not in str(info.value) and TOKEN not in repr(info.value) and TOKEN not in caplog.text


def test_cached_client_memoises_per_reconcile():
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"broker": "cathay", "currency": "TWD", "as_of": "2026-09-30", "priced_through": "2026-09-30",
                                         "market_value": "1.0000", "cash": "0.0000", "total": "1.0000"})
    with pc.cached(_client(handler)) as c:
        c.valuation("cathay", date(2026, 9, 30)); c.valuation("cathay", date(2026, 9, 30))
    assert calls == ["/ledger-feed/valuation"]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/unit/test_portfolio_client.py`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Implement**

```python
# app/services/portfolio_client.py
"""Read-only client for the stock-portfolio-service ledger feed (spec §4.1, §5, §6).

Every failure is `PortfolioUnavailable` with a code only — never the URL with a token, never a body.
"""
from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import httpx


class PortfolioUnavailable(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Settlement:
    settlement_date: date | None
    trade_date: date
    net_amount: Decimal
    currency: str
    ids: tuple[int, ...]
    fingerprint: str
    components: dict


@dataclass(frozen=True)
class SettlementsPage:
    calendar_through: date | None
    rows: list[Settlement]


@dataclass(frozen=True)
class Funding:
    date: date
    type: str
    amount: Decimal
    currency: str
    source_id: str
    note: str | None


@dataclass(frozen=True)
class Valuation:
    broker: str
    currency: str
    as_of: date
    priced_through: date | None
    market_value: Decimal
    cash: Decimal
    total: Decimal


def _d(v: str | None) -> date | None:
    return None if v is None else date.fromisoformat(v)


class PortfolioClient:
    def __init__(self, base_url: str, token: str, *, timeout: float = 5.0, transport=None):
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport,
                                  headers={"Authorization": f"Bearer {token}"})
        self._memo: dict | None = None

    def _get(self, path: str, params: dict) -> dict | list:
        key = (path, tuple(sorted(params.items())))
        if self._memo is not None and key in self._memo:
            return self._memo[key]
        try:
            response = self._http.get(path, params=params)
        except httpx.HTTPError as exc:  # connect/timeout/protocol: class name only
            raise PortfolioUnavailable(type(exc).__name__) from None
        if response.status_code != 200:
            raise PortfolioUnavailable(f"http_{response.status_code}")
        body = response.json()
        if self._memo is not None:
            self._memo[key] = body
        return body

    def settlements(self, broker: str, from_: date, to: date) -> SettlementsPage:
        body = self._get("/ledger-feed/settlements", {"broker": broker, "from": from_.isoformat(), "to": to.isoformat()})
        rows = [Settlement(_d(r["settlement_date"]), date.fromisoformat(r["trade_date"]), Decimal(r["net_amount"]), r["currency"],
                           tuple(int(i) for i in r["cash_transaction_ids"]), r["fingerprint"], dict(r["components"])) for r in body["rows"]]
        return SettlementsPage(_d(body.get("calendar_through")), rows)

    def funding(self, broker: str, from_: date, to: date) -> list[Funding]:
        body = self._get("/ledger-feed/funding", {"broker": broker, "from": from_.isoformat(), "to": to.isoformat()})
        return [Funding(date.fromisoformat(r["date"]), r["type"], Decimal(r["amount"]), r["currency"], r["source_id"], r.get("note")) for r in body]

    def valuation(self, broker: str, as_of: date) -> Valuation:
        r = self._get("/ledger-feed/valuation", {"broker": broker, "as_of": as_of.isoformat()})
        return Valuation(r["broker"], r["currency"], date.fromisoformat(r["as_of"]), _d(r.get("priced_through")),
                         Decimal(r["market_value"]), Decimal(r["cash"]), Decimal(r["total"]))


@contextlib.contextmanager
def cached(client: PortfolioClient):
    client._memo = {}
    try:
        yield client
    finally:
        client._memo = None


def client_for() -> PortfolioClient | None:
    base, token = os.getenv("PORTFOLIO_BASE_URL"), os.getenv("PORTFOLIO_READ_TOKEN")
    if not base or not token:
        return None
    return PortfolioClient(base, token)
```

`PortfolioClient.__repr__` is the default object repr (no fields); add `__repr__ = object.__repr__` explicitly with a comment so a future dataclass conversion cannot leak the header.

- [ ] **Step 4: Run tests**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/unit/test_portfolio_client.py`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add services/accounting-service/app/services/portfolio_client.py services/accounting-service/app/config.py services/accounting-service/tests/unit/test_portfolio_client.py
git commit -m "accounting: read-only portfolio client with per-reconcile memo and token-safe failures"
```

---

### Task 5: Accounting — settlement candidates and global assignment (pure matching code)

**Files:**
- Create: `services/accounting-service/app/services/statements/settlement_match.py`
- Modify: `services/accounting-service/app/services/statements/matching.py` (`Rules` gains `settlement_window_days: int = 2`, `settlement_labels: tuple[str, ...] = ("交割股款", "申購股款")`, `settlement_sum_matching: bool = False`; `_bank_only`-style exclusion: a settlement line never reaches `_near_case`/`_drift_case`/`_foreign_case` and is skipped by `_representations`' standalone/group rules — it returns the settlement outcome instead)
- Test: `services/accounting-service/tests/unit/test_settlement_match.py`

**Interfaces:**
- Consumes: `matching.Line`, `portfolio_client.Settlement`.
- Produces: `settlement_match.is_settlement_line(line, rules) -> bool`; `settlement_match.assign(lines: list[Line], settlements: list[Settlement], reserved: set[str], rules) -> Assignment` where `Assignment.booked: dict[int, Settlement]` (line id → the unique settlement), `Assignment.ambiguous: dict[int, list[Settlement]]`, `Assignment.pending: list[int]` (settlement lines with no candidate), `Assignment.unknown_dates: int` (settlements with `settlement_date None`, never candidates). `lines` are EVERY settlement-labelled line of the account in the window (the orchestrator passes neighbours' lines too, Task 6); `reserved` = fingerprints already booked (any event).

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_settlement_match.py
from datetime import date
from decimal import Decimal
from app.services.statements import matching as m
from app.services.statements import settlement_match as sm
from app.services.portfolio_client import Settlement

R = m.Rules(period_end=date(2026, 9, 30))


def L(i, day, flow, merchant="交割股款", kind="withdrawal"):
    return m.Line(id=i, event_id=100 + i, posted_date=day, txn_date=day, flow=Decimal(flow), foreign_amount=None,
                  foreign_currency=None, line_kind=kind, merchant_norm=merchant, installment_seq=None, installment_total=None)


def S(fp, sd, net, td=None):
    return Settlement(sd, td or sd, Decimal(net), "TWD", (1,), fp, {})


def test_unique_pair_books_and_tolerates_two_days():
    a = sm.assign([L(1, date(2026, 9, 14), "-100142")], [S("f1", date(2026, 9, 12), "-100142")], set(), R)
    assert a.booked == {1: S("f1", date(2026, 9, 12), "-100142")} and a.ambiguous == {} and a.pending == []


def test_two_lines_one_amount_two_settlements_are_ambiguous():
    lines = [L(1, date(2026, 9, 14), "-5000"), L(2, date(2026, 9, 16), "-5000")]
    sets = [S("f1", date(2026, 9, 14), "-5000"), S("f2", date(2026, 9, 15), "-5000")]
    a = sm.assign(lines, sets, set(), R)
    assert a.booked == {} and set(a.ambiguous) == {1, 2} and a.pending == []


def test_one_settlement_two_lines_is_ambiguous_on_both():
    a = sm.assign([L(1, date(2026, 9, 14), "-5000"), L(2, date(2026, 9, 15), "-5000")], [S("f1", date(2026, 9, 14), "-5000")], set(), R)
    assert a.booked == {} and set(a.ambiguous) == {1, 2}


def test_reserved_and_unknown_date_are_never_candidates():
    a = sm.assign([L(1, date(2026, 9, 14), "-5000")], [S("f1", date(2026, 9, 14), "-5000"), S("f2", None, "-5000")], {"f1"}, R)
    assert a.booked == {} and a.pending == [1] and a.unknown_dates == 1


def test_non_settlement_lines_are_ignored_and_amount_must_be_exact():
    a = sm.assign([L(1, date(2026, 9, 14), "-5000", merchant="網銀轉帳"), L(2, date(2026, 9, 14), "-5001")], [S("f1", date(2026, 9, 14), "-5000")], set(), R)
    assert a.booked == {} and a.pending == [2] and 1 not in a.ambiguous


def test_sum_matching_is_off_by_default_and_gated_when_on():
    lines = [L(1, date(2026, 9, 14), "-8000")]
    sets = [S("f1", date(2026, 9, 14), "-5000"), S("f2", date(2026, 9, 15), "-3000")]
    assert sm.assign(lines, sets, set(), R).pending == [1]
    on = m.Rules(period_end=date(2026, 9, 30), settlement_sum_matching=True)
    a = sm.assign(lines, sets, set(), on)
    assert a.booked[1].fingerprint == "f1+f2" and a.booked[1].ids == (1, 1) or a.booked[1].net_amount == Decimal("-8000")
    # overlap: a single candidate for another line uses f1 → everything ambiguous
    lines2 = lines + [L(2, date(2026, 9, 14), "-5000")]
    a2 = sm.assign(lines2, sets, set(), on)
    assert a2.booked == {} and set(a2.ambiguous) == {1, 2}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/unit/test_settlement_match.py`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Implement**

```python
# app/services/statements/settlement_match.py
"""Global assignment of bank settlement lines to portfolio settlements (spec §4.3, §9).

Pure: no DB, no HTTP. A pair books only when it is the unique feasible assignment on both sides."""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal

from app.services.portfolio_client import Settlement
from . import matching as m


@dataclass
class Assignment:
    booked: dict[int, Settlement] = field(default_factory=dict)
    ambiguous: dict[int, list[Settlement]] = field(default_factory=dict)
    pending: list[int] = field(default_factory=list)
    unknown_dates: int = 0


def is_settlement_line(line: m.Line, rules: m.Rules) -> bool:
    return line.line_kind in ("withdrawal", "deposit") and line.merchant_norm in rules.settlement_labels


def _candidates(line: m.Line, settlements: list[Settlement], rules: m.Rules) -> list[Settlement]:
    out = []
    for s in settlements:
        if s.settlement_date is None:
            continue
        if m._same(s.net_amount, line.flow) and abs((s.settlement_date - line.posted_date).days) <= rules.settlement_window_days:
            out.append(s)
    return out


def _sums(line: m.Line, settlements: list[Settlement], rules: m.Rules) -> list[Settlement]:
    """Gated (spec §9): two settlements within 2 days of each other whose sum is the line, both inside the window."""
    if not rules.settlement_sum_matching:
        return []
    out = []
    dated = [s for s in settlements if s.settlement_date is not None
             and abs((s.settlement_date - line.posted_date).days) <= rules.settlement_window_days]
    for a, b in itertools.combinations(dated, 2):
        if abs((a.settlement_date - b.settlement_date).days) <= 2 and m._same(a.net_amount + b.net_amount, line.flow):
            out.append(Settlement(max(a.settlement_date, b.settlement_date), min(a.trade_date, b.trade_date), a.net_amount + b.net_amount,
                                  a.currency, tuple(sorted(a.ids + b.ids)), f"{a.fingerprint}+{b.fingerprint}",
                                  {k: a.components.get(k, 0) + b.components.get(k, 0) for k in set(a.components) | set(b.components)}))
    return out


def assign(lines: list[m.Line], settlements: list[Settlement], reserved: set[str], rules: m.Rules) -> Assignment:
    out = Assignment(unknown_dates=sum(1 for s in settlements if s.settlement_date is None))
    live = [s for s in settlements if s.fingerprint not in reserved]
    cands: dict[int, list[Settlement]] = {}
    for line in sorted(lines, key=lambda l: l.id):
        if not is_settlement_line(line, rules):
            continue
        cands[line.id] = _candidates(line, live, rules) + _sums(line, live, rules)
    # constituent usage: a settlement (or any constituent of a sum) wanted by more than one line is contested
    wanted: dict[str, set[int]] = {}
    for lid, cs in cands.items():
        for c in cs:
            for part in c.fingerprint.split("+"):
                wanted.setdefault(part, set()).add(lid)
    for lid, cs in cands.items():
        if not cs:
            out.pending.append(lid)
            continue
        contested = any(len(wanted[part]) > 1 for c in cs for part in c.fingerprint.split("+"))
        if len(cs) == 1 and not contested:
            out.booked[lid] = cs[0]
        else:
            out.ambiguous[lid] = cs
    return out
```

`matching.Rules` additions (defaults as above) and, in `matching.match`, settlement lines are routed out before any representation: `if settlement_match.is_settlement_line(line, rules): continue` — the orchestrator (Task 6) writes their cases from the `Assignment`; `match` therefore never claims, nears or foreign-matches them. `settings_service.reconciliation_rules` validates the three new keys (`settlement_window_days` int ≥ 0, `settlement_labels` list of non-empty strings, `settlement_sum_matching` bool).

- [ ] **Step 4: Run tests**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/unit/test_settlement_match.py tests/unit/test_statement_matching.py tests/unit/test_schedule_rules.py`
Expected: all pass (existing matcher tests untouched: no settlement labels in them)

- [ ] **Step 5: Commit**

```bash
git add services/accounting-service/app/services/statements/settlement_match.py services/accounting-service/app/services/statements/matching.py services/accounting-service/app/services/settings_service.py services/accounting-service/tests/unit/test_settlement_match.py
git commit -m "accounting: settlement lines are assigned globally against portfolio settlements, never matched by the ledger rules"
```

---

### Task 6: Accounting — orchestration in `reconcile`: fetch, assign, write `settlement` / pending / ambiguous cases, drift check

**Files:**
- Modify: `services/accounting-service/app/services/reconciliation_service.py` (`reconcile`, `_desired_cases`, `ReconcileResult.settlement_source_unavailable: bool`, `ReconcileResult.settlements: dict` counts)
- Modify: `services/accounting-service/app/services/policy_service.py` (`_backfill_candidate` and `_system_entry_candidate` return None for a settlement-labelled line; `registry.backfill_eligibility` likewise)
- Modify: `services/accounting-service/app/schemas/statements.py` (`ReconcileOut.settlement_source_unavailable`, `settlements` counts)
- Test: `services/accounting-service/tests/integration/test_settlement_reconcile.py`

**Interfaces:**
- Consumes: Task 3 models, Task 4 client (`client_for`, `cached`, `PortfolioUnavailable`), Task 5 `assign`.
- Produces: in `reconcile`, when `account.settlement_source` is set: lines = settlement-labelled lines of the account within `period_start − window … period_end + window` across non-retired statements (`StatementLine` join `StatementRevision` = current revision); settlements = `client.settlements(broker, lo, hi)`; `reserved` = fingerprints in `settlement_booking`; cases: `settlement` (candidates `[{"fingerprint", "settlement_date", "trade_date", "net_amount"}]`, context `{"fingerprint", "ids", "facts": components}`), `ambiguous` with `hint: settlement_ambiguous`, `line_unmatched` with `hint: settlement_pending` (and `calendar_through` when the date was unknown), and `settlement` + `hint: source_drift` on a booked line whose recomputed facts differ (`net_amount` or `ids`). A booked line whose facts match is re-claimed by its 67 leg (coverage `match_rule book_settlement`) when not already active. Portfolio unavailable → every settlement line `settlement_pending`, `result.settlement_source_unavailable = True`, everything else reconciled as usual.
- Test fixture: `portfolio_stub` (monkeypatches `portfolio_client.client_for` to a client over `httpx.MockTransport` serving canned JSON per test).

- [ ] **Step 1: Failing integration tests**

```python
# tests/integration/test_settlement_reconcile.py
from datetime import date
from decimal import Decimal
import httpx, pytest
from app.models import Account, ReconciliationCase, StatementCoverage
from app.services import portfolio_client as pc
from app.services import reconciliation_service as rec
from tests.integration.statement_fixtures import _active, _cases, _line, _submit, run  # noqa: F401

DAY = date(2026, 9, 14)


def _bank67(seed, db):
    acct = seed.account("國泰", kind="bank", statement_live_from=date(2026, 9, 1))
    acct.settlement_source = "portfolio:cathay"
    db.commit(); return acct


@pytest.fixture
def portfolio_stub(monkeypatch):
    state = {"rows": [], "calendar_through": "2026-09-30", "fail": None}
    def handler(request):
        if state["fail"]:
            return httpx.Response(state["fail"])
        return httpx.Response(200, json={"calendar_through": state["calendar_through"], "rows": state["rows"]})
    client = pc.PortfolioClient("http://portfolio.test", "t", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(pc, "client_for", lambda: client)
    return state


def _row(fp, sd, net, ids=(1,), td=None):
    return {"settlement_date": sd, "trade_date": td or sd, "net_amount": net, "currency": "TWD",
            "components": {"buy_settle": 1, "sell_settle": 0, "fee": 0, "tax": 0}, "cash_transaction_ids": list(ids), "fingerprint": fp}


def test_unique_settlement_writes_a_settlement_case_and_never_a_backfill(seed, db_session, run, portfolio_stub):
    acct = _bank67(seed, db_session)
    portfolio_stub["rows"] = [_row("cathay:2026-09-10:a", "2026-09-14", "-100142.0000")]
    stmt, (line,), _ = _submit(db_session, run, acct, [_line(1, DAY, "交割股款", 100142, "withdrawal")], kind="bank")
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.line_id == line.id]
    assert case.kind == "settlement" and case.candidates[0]["fingerprint"] == "cathay:2026-09-10:a"
    from app.services import reconciliation_actions as registry
    assert "backfill_entry" not in registry.allowed(case)


def test_net_mismatch_stays_pending(seed, db_session, run, portfolio_stub):
    acct = _bank67(seed, db_session)
    portfolio_stub["rows"] = [_row("cathay:2026-09-10:a", "2026-09-14", "-100141.0000")]
    stmt, (line,), _ = _submit(db_session, run, acct, [_line(1, DAY, "交割股款", 100142, "withdrawal")], kind="bank")
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.line_id == line.id]
    assert (case.kind, case.context.get("hint")) == ("line_unmatched", "settlement_pending")


def test_portfolio_unavailable_reconciles_the_rest(seed, db_session, run, portfolio_stub):
    acct = _bank67(seed, db_session)
    portfolio_stub["fail"] = 503
    seed.entry(acct, "-700", day=DAY, merchant="全聯"); db_session.commit()
    stmt, (s_line, other), _ = _submit(db_session, run, acct, [_line(1, DAY, "交割股款", 100142, "withdrawal"), _line(2, DAY, "全聯", 700, "withdrawal")], kind="bank")
    assert [c.line_id for c in _active(db_session, stmt.id)] == [other.id]
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.line_id == s_line.id]
    assert case.context.get("hint") == "settlement_pending"
    assert rec.reconcile(db_session, stmt.id).settlement_source_unavailable is True


def test_two_statements_share_the_window(seed, db_session, run, portfolio_stub):
    # a settlement two days before the period start belongs to the previous statement's line, not this one's
    acct = _bank67(seed, db_session)
    portfolio_stub["rows"] = [_row("cathay:2026-08-27:a", "2026-08-31", "-5000.0000")]
    prev, (pline,), _ = _submit(db_session, run, acct, [_line(1, date(2026, 8, 31), "交割股款", 5000, "withdrawal")], kind="bank", period=(date(2026, 8, 1), date(2026, 8, 31)))
    stmt, (line,), _ = _submit(db_session, run, acct, [_line(1, date(2026, 9, 1), "交割股款", 5000, "withdrawal")], kind="bank")
    kinds = {c.line_id: (c.kind, c.context.get("hint")) for c in _cases(db_session, stmt.id, "open") + _cases(db_session, prev.id, "open")}
    assert kinds[pline.id][0] == "ambiguous" and kinds[line.id][0] == "ambiguous"  # one settlement wanted by two lines across statements
```

- [ ] **Step 2: Run to verify failure**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/integration/test_settlement_reconcile.py`
Expected: FAIL (no settlement cases; `seed.account(..., statement_live_from=...)` exists already per conftest — if the keyword is missing, pass it via attribute after creation)

- [ ] **Step 3: Implement in `reconciliation_service`**

In `reconcile`, after `line_rows` / `rules` are known and before `matching.match`:

```python
    settlement = _settlement_assignment(db, statement, account, line_rows, rules)  # None when the account has no source
    settlement_line_ids = set(settlement.lines) if settlement else set()
    match_lines = [_as_line(row) for row in line_rows if row.id not in settlement_line_ids]
    result = matching.match(statement.kind, match_lines, entries, groups, plan_map(...), fee, rules, statement.currency)
    ...
    created = _write_cases(db, statement, _desired_cases(...) + (_settlement_cases(db, statement, settlement, lines) if settlement else []))
```

```python
@dataclass
class SettlementOutcome:
    lines: dict[int, StatementLine]          # settlement lines of THIS statement
    assignment: settlement_match.Assignment | None   # None = unavailable
    unavailable: bool
    calendar_through: date | None
    drift: dict[int, dict]                   # line id → {"fingerprint", "facts"} for booked lines whose facts changed
    rebook: dict[int, int]                   # line id → existing 67 leg entry id to re-claim


def _settlement_assignment(db, statement, account, line_rows, rules) -> SettlementOutcome | None:
    if not account.settlement_source:
        return None
    broker = account.settlement_source.split(":", 1)[1]
    mine = {row.id: row for row in line_rows if settlement_match.is_settlement_line(_as_line(row), rules)}
    lo, hi = statement.period_start - timedelta(days=rules.settlement_window_days), statement.period_end + timedelta(days=rules.settlement_window_days)
    neighbours = _settlement_lines_of_account(db, account.id, lo, hi, exclude_statement=statement.id)  # current revisions, not retired
    client = portfolio_client.client_for()
    if client is None:
        return SettlementOutcome(mine, None, True, None, {}, {})
    try:
        with portfolio_client.cached(client) as c:
            page = c.settlements(broker, lo, hi)
    except portfolio_client.PortfolioUnavailable:
        return SettlementOutcome(mine, None, True, None, {}, {})
    bookings = {b.fingerprint: b for b in db.execute(select(SettlementBooking).where(SettlementBooking.account_id == account.id)).scalars()}
    by_event = {b.event_id: b for b in bookings.values()}
    drift, rebook, reserved = {}, {}, set(bookings)
    for s in page.rows:
        b = bookings.get(s.fingerprint)
        if b is None:
            continue
        if b.net_amount != s.net_amount or sorted(int(i.source_id.split(":")[1]) for i in b.items) != sorted(s.ids):
            for lid, row in mine.items():
                if row.event_id == b.event_id:
                    drift[lid] = {"fingerprint": s.fingerprint, "facts": {"net_amount": str(s.net_amount), "ids": list(s.ids)}}
    for lid, row in mine.items():
        b = by_event.get(row.event_id)
        if b is not None and lid not in drift:
            leg = db.scalar(select(LedgerEntry.id).where(LedgerEntry.settlement_booking_id == b.id, LedgerEntry.account_id == account.id))
            rebook[lid] = leg
    open_lines = [_as_line(r) for r in list(mine.values()) + neighbours if r.event_id not in by_event]
    assignment = settlement_match.assign(open_lines, page.rows, reserved, rules)
    return SettlementOutcome(mine, assignment, False, page.calendar_through, drift, rebook)
```

`_settlement_cases` turns the outcome into `_case(...)` dicts for THIS statement's lines only: booked → `settlement` (candidates `[{"fingerprint", "settlement_date", "trade_date", "net_amount"}]`, context `{"fingerprint", "ids", "facts", "source": account.settlement_source}`); ambiguous → `ambiguous` + `{"hint": "settlement_ambiguous"}` with the same candidate shape; pending → `line_unmatched` + `{"hint": "settlement_pending", "calendar_through": …}`; drift → `settlement` + `{"hint": "source_drift", ...}`; unavailable → every line `settlement_pending` and `result.settlement_source_unavailable = True`. For `rebook` lines, `_write_coverage` re-claims the leg (`match_rule book_settlement`, `match_kind manual`) when no active coverage exists for the line — the existing `_manual_claims` keep-path already keeps `manual` rows, so this only runs after a revert-then-rebook or a lost claim.

`policy_service._backfill_candidate` / `_system_entry_candidate` and `registry.backfill_eligibility`: return None when `case.context.get("hint") in ("settlement_pending", "settlement_ambiguous")` or the line's `merchant_norm` is in the account's settlement labels — the test `test_unique_settlement_writes_a_settlement_case_and_never_a_backfill` and a parametrised pending variant pin it.

- [ ] **Step 4: Run tests**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/integration/test_settlement_reconcile.py tests/integration/test_reconciliation_service.py tests/integration/test_reconciliation_policy.py`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add services/accounting-service/app/services/reconciliation_service.py services/accounting-service/app/services/policy_service.py services/accounting-service/app/services/reconciliation_actions/__init__.py services/accounting-service/app/schemas/statements.py services/accounting-service/tests/integration/test_settlement_reconcile.py
git commit -m "accounting: reconcile fetches portfolio settlements, assigns globally, writes settlement/pending/ambiguous/drift cases; settlement lines never backfill"
```

---

### Task 7: Accounting — `_PairCreation`, `book_settlement`, `book_dividend`, policy wiring, revert

**Files:**
- Modify: `services/accounting-service/app/services/reconciliation_actions/ledger_actions.py` (`_PairCreation`, `BookSettlementAction`, `BookDividendAction` — dividend is a single-row creation on `_Creation` with a booking owner too)
- Modify: `services/accounting-service/app/services/reconciliation_actions/__init__.py` (`KIND_ACTIONS["settlement"] = ("book_settlement", "dismiss")`, `KIND_ACTIONS["dividend"] = ("book_dividend", "dismiss")`; `ambiguous` keeps `match`, `dismiss` and gains `book_settlement` when `hint == settlement_ambiguous`)
- Modify: `services/accounting-service/app/services/policy_service.py` (`_settlement_candidate`, `_dividend_candidate`; policy keys `book_settlement {"enabled": true, "max_amount": "0"}` (0 = uncapped, like `replace_stand_in`), `book_dividend {"enabled": true, "max_amount": "0"}`; both exempt from `per_statement_total`, counted in `per_statement_count`)
- Modify: `services/accounting-service/app/services/settings_service.py` (defaults + validation for the two actions and `settlement_target_account` map `{"67": 117}`)
- Test: `services/accounting-service/tests/integration/test_settlement_actions.py`

**Interfaces:**
- Consumes: Task 3 models, Task 6 cases (`settlement` with `context.fingerprint/ids/facts`, `ambiguous` with settlement candidates).
- Produces: `book_settlement` params `{"line_id": int, "fingerprint": str}` (for an `ambiguous` case the owner names the settlement); effect: one `SettlementBooking` (+ items from `context.ids`, `facts`), two `LedgerEntry` legs (`source = statement`, `transfer_group_id` shared, `settlement_booking_id` set; 67 leg `kind transfer_out` for a negative net / `transfer_in` for a positive, 117 leg the mirror; `description = f"證券交割 {trade_date}"`), coverage claim of the 67 leg (`match_rule book_settlement`), `created_entry_ids = [leg67, leg117]`, `inverse = {"booking_id", "leg_ids"}`, snapshots of both legs; refusals: `already_booked` (fingerprint exists for another event), `constituent_reserved` (unique violation on items), `candidate_changed` (fingerprint not among the case's candidates), `target_missing` (no `settlement_target_account` for the statement's account). Revert: both legs must equal their snapshots (`ews.delete_entries_cascade` guards: no child, settlement or refund on either), delete legs + booking (items cascade) + release the line; `Effect(deleted_entry_ids=[both], touched_group_ids=[])`.
- `book_dividend`: params `{"line_id", "dividend_id"}`; one income entry on the bank account (category from settings `dividend_category`), booking owner with `source = portfolio:cathay`, fingerprint `dividend:<id>`, item `("dividend", "<id>")`.

- [ ] **Step 1: Failing tests**

```python
# tests/integration/test_settlement_actions.py
from datetime import date
from decimal import Decimal
import pytest
from app.models import LedgerEntry, ReconciliationCase, SettlementBooking, SettlementBookingItem
from app.services import policy_service
from app.services.errors import CodedConflictError
from tests.integration.test_reconciliation_actions_ledger import _apply, _refuse, _revert, _fresh, _actions
from tests.integration.test_settlement_reconcile import _bank67, _row, portfolio_stub, DAY  # noqa: F401
from tests.integration.statement_fixtures import _active, _cases, _line, _reconcile, _submit, run  # noqa: F401


def _target(seed, db, bank):
    pos = seed.account("國泰高雄", kind="asset")
    from app.services import settings_service
    settings_service.put_reconciliation(db, {**settings_service.reconciliation_settings(db), "settlement_target_account": {str(bank.id): pos.id}})
    db.commit(); return pos


def test_book_settlement_books_two_legs_one_owner_and_reverts(seed, db_session, run, portfolio_stub):
    bank = _bank67(seed, db_session); pos = _target(seed, db_session, bank)
    portfolio_stub["rows"] = [_row("cathay:2026-09-10:a", "2026-09-14", "-100142.0000", ids=(5, 7), td="2026-09-10")]
    stmt, (line,), _ = _submit(db_session, run, bank, [_line(1, DAY, "交割股款", 100142, "withdrawal")], kind="bank")
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "settlement"]
    row = _apply(db_session, case, "book_settlement", {"line_id": line.id, "fingerprint": "cathay:2026-09-10:a"})
    booking = db_session.query(SettlementBooking).one()
    assert (booking.fingerprint, booking.event_id, booking.net_amount) == ("cathay:2026-09-10:a", line.event_id, Decimal("-100142.0000"))
    assert sorted(i.source_id for i in booking.items) == ["ct:5", "ct:7"]
    legs = db_session.query(LedgerEntry).filter(LedgerEntry.settlement_booking_id == booking.id).order_by(LedgerEntry.account_id).all()
    assert [(e.account_id, e.amount) for e in legs] == [(bank.id, Decimal("-100142")), (pos.id, Decimal("100142"))]
    assert legs[0].transfer_group_id == legs[1].transfer_group_id and legs[0].description == "證券交割 2026-09-10"
    (claim,) = _active(db_session, stmt.id)
    assert (claim.entry_id, claim.match_rule) == (legs[0].id, "book_settlement")
    assert sorted(row.created_entry_ids) == sorted(e.id for e in legs) and row.inverse["booking_id"] == booking.id
    _revert(db_session, row.id)
    assert db_session.query(SettlementBooking).count() == 0 and db_session.query(LedgerEntry).filter(LedgerEntry.settlement_booking_id.isnot(None)).count() == 0
    assert _active(db_session, stmt.id) == [] and _fresh(db_session, ReconciliationCase, case.id).status == "open"


def test_policy_books_settlements_uncapped_and_reconcile_is_idempotent(seed, db_session, run, portfolio_stub):
    bank = _bank67(seed, db_session); _target(seed, db_session, bank)
    portfolio_stub["rows"] = [_row("cathay:2026-09-10:a", "2026-09-14", "-900000.0000")]
    stmt, (line,), _ = _submit(db_session, run, bank, [_line(1, DAY, "交割股款", 900000, "withdrawal")], kind="bank")
    result = policy_service.run_policy(db_session, stmt.id)
    assert len(result.applied) == 1 and result.proposed == []
    _reconcile(db_session, stmt.id)
    assert db_session.query(SettlementBooking).count() == 1 and len(_active(db_session, stmt.id)) == 1
    assert [c for c in _cases(db_session, stmt.id, "open")] == []


def test_another_event_with_the_same_fingerprint_is_already_booked(seed, db_session, run, portfolio_stub):
    bank = _bank67(seed, db_session); _target(seed, db_session, bank)
    portfolio_stub["rows"] = [_row("cathay:2026-09-10:a", "2026-09-14", "-5000.0000")]
    stmt, (l1,), _ = _submit(db_session, run, bank, [_line(1, DAY, "交割股款", 5000, "withdrawal")], kind="bank")
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "settlement"]
    _apply(db_session, case, "book_settlement", {"line_id": l1.id, "fingerprint": "cathay:2026-09-10:a"})
    # a second statement prints the same line again (a bank re-print): its line gets no candidate (reserved) …
    stmt2, (l2,), _ = _submit(db_session, run, bank, [_line(1, date(2026, 10, 1), "交割股款", 5000, "withdrawal")], kind="bank", period=(date(2026, 10, 1), date(2026, 10, 31)))
    (pending,) = [c for c in _cases(db_session, stmt2.id, "open") if c.line_id == l2.id]
    assert pending.context.get("hint") == "settlement_pending"
    # … and forcing the action with that fingerprint is refused
    exc = _refuse(db_session, lambda: _apply(db_session, pending, "book_settlement", {"line_id": l2.id, "fingerprint": "cathay:2026-09-10:a"}))
    assert isinstance(exc, CodedConflictError) and exc.code in ("already_booked", "candidate_changed")


def test_source_drift_after_booking_opens_a_drift_case_and_keeps_the_legs(seed, db_session, run, portfolio_stub):
    bank = _bank67(seed, db_session); _target(seed, db_session, bank)
    portfolio_stub["rows"] = [_row("cathay:2026-09-10:a", "2026-09-14", "-5000.0000", ids=(5,))]
    stmt, (line,), _ = _submit(db_session, run, bank, [_line(1, DAY, "交割股款", 5000, "withdrawal")], kind="bank")
    policy_service.run_policy(db_session, stmt.id)
    portfolio_stub["rows"] = [_row("cathay:2026-09-10:a", "2026-09-14", "-5000.0000", ids=(5, 9))]  # an edited import, same fingerprint id set changed
    _reconcile(db_session, stmt.id)
    (drift,) = [c for c in _cases(db_session, stmt.id, "open")]
    assert (drift.kind, drift.context.get("hint")) == ("settlement", "source_drift")
    assert db_session.query(SettlementBooking).count() == 1 and len(_active(db_session, stmt.id)) == 1


def test_revert_after_reconcile_reopens_pending(seed, db_session, run, portfolio_stub):
    bank = _bank67(seed, db_session); _target(seed, db_session, bank)
    portfolio_stub["rows"] = [_row("cathay:2026-09-10:a", "2026-09-14", "-5000.0000")]
    stmt, (line,), _ = _submit(db_session, run, bank, [_line(1, DAY, "交割股款", 5000, "withdrawal")], kind="bank")
    (action_id,) = policy_service.run_policy(db_session, stmt.id).applied
    _reconcile(db_session, stmt.id)
    _revert(db_session, action_id)
    _reconcile(db_session, stmt.id)
    (case,) = _cases(db_session, stmt.id, "open")
    assert case.kind == "settlement"  # the settlement is free again: a fresh candidate, booked on the next policy run
```

- [ ] **Step 2: Run to verify failure**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/integration/test_settlement_actions.py`
Expected: FAIL (`book_settlement` unknown)

- [ ] **Step 3: Implement**

```python
# ledger_actions.py — additions
class _PairCreation(ActionBase):
    """Books two rows that mirror each other (a transfer) under one owner row; reverting deletes both and the owner
    atomically. Unlike `_Creation` (one row, one `created_entry_id`), the inverse carries both leg ids."""
    slot = CREATE

    def _book_pair(self, ctx: Context, line: StatementLine, effect: Effect, *, booking: SettlementBooking,
                   counter_account_id: int, description: str) -> tuple[LedgerEntry, LedgerEntry]:
        flow = Decimal(line.flow_amount)
        group = str(uuid4())
        here = LedgerEntry(account_id=ctx.statement.account_id, currency=ctx.statement.currency, amount=flow,
                           kind="transfer_out" if flow < 0 else "transfer_in", entry_date=line.txn_date or line.posted_date,
                           posted_date=line.posted_date, merchant=line.merchant_raw[:128], description=description,
                           source=STATEMENT_SOURCE, transfer_group_id=group, settlement_booking_id=booking.id)
        there = LedgerEntry(account_id=counter_account_id, currency=ctx.statement.currency, amount=-flow,
                            kind="transfer_in" if flow < 0 else "transfer_out", entry_date=here.entry_date,
                            posted_date=line.posted_date, merchant=here.merchant, description=description,
                            source=STATEMENT_SOURCE, transfer_group_id=group, settlement_booking_id=booking.id)
        ctx.db.add_all([here, there]); ctx.db.flush()
        effect.coverage_ids = _claim(ctx, line, [(here, "principal")], self.name)
        effect.created_entry_ids = [here.id, there.id]
        effect.touched_entry_ids = [here.id, there.id]
        effect.inverse = {"booking_id": booking.id, "leg_ids": [here.id, there.id],
                          "snapshots": {str(e.id): snapshots.entry(e) for e in (here, there)}}
        return here, there

    def invert(self, ctx: Context, action: ReconciliationAction) -> Effect:
        legs = [ctx.db.get(LedgerEntry, i) for i in action.inverse["leg_ids"]]
        if any(e is None for e in legs):
            raise NotRevertable("a leg is gone")
        for e in legs:
            if snapshots.entry(e) != action.inverse["snapshots"][str(e.id)]:
                raise NotRevertable(f"entry {e.id} changed since the booking")
        _release(ctx, action)
        booking = ctx.db.get(SettlementBooking, action.inverse["booking_id"])
        ews.delete_entries_cascade(ctx.db, [e.id for e in legs])
        if booking is not None:
            ctx.db.delete(booking)  # items cascade
        ctx.db.flush()
        return Effect(deleted_entry_ids=[e.id for e in legs])


class BookSettlementParams(ActionParams):
    line_id: Int32
    fingerprint: str = Field(min_length=1, max_length=128)


class BookSettlementAction(_PairCreation):
    name = "book_settlement"
    Params = BookSettlementParams
    case_kinds = frozenset({"settlement", "ambiguous"})
    actors = frozenset({OWNER, POLICY})

    def lock_targets(self, ctx, params):
        return LockSet()  # the booking row and the unique constraints are the serialisation point

    def _plan(self, ctx: Context, params: BookSettlementParams):
        line = _open_line(ctx, case_line(ctx, params.line_id))
        cands = [c for c in (ctx.case.candidates or []) if c.get("fingerprint") == params.fingerprint]
        if not cands:
            raise CodedConflictError("candidate_changed", f"fingerprint not among the case's candidates")
        targets = settings_service.reconciliation_settings(ctx.db).get("settlement_target_account") or {}
        counter = targets.get(str(ctx.statement.account_id))
        if counter is None:
            raise _refuse("params", "target_missing", f"account {ctx.statement.account_id}")
        existing = ctx.db.scalar(select(SettlementBooking).where(SettlementBooking.fingerprint == params.fingerprint))
        if existing is not None and existing.event_id != line.event_id:
            raise CodedConflictError("already_booked", f"event {existing.event_id}")
        return line, cands[0], int(counter), existing

    def validate_before(self, ctx, params, prepared, before):
        self._plan(ctx, params)

    def apply(self, ctx: Context, params: BookSettlementParams, prepared: Prepared, effect: Effect) -> None:
        line, cand, counter, existing = self._plan(ctx, params)
        context = ctx.case.context or {}
        if existing is not None:  # same event: re-claim the leg, book nothing
            leg = ctx.db.scalar(select(LedgerEntry).where(LedgerEntry.settlement_booking_id == existing.id, LedgerEntry.account_id == ctx.statement.account_id))
            effect.coverage_ids = _claim(ctx, line, [(leg, "principal")], self.name)
            effect.inverse = {"booking_id": existing.id, "leg_ids": [], "snapshots": {}}
            return
        booking = SettlementBooking(account_id=ctx.statement.account_id, event_id=line.event_id, source=context.get("source", ""),
                                    fingerprint=params.fingerprint, net_amount=Decimal(cand["net_amount"]),
                                    settlement_date=date.fromisoformat(cand["settlement_date"]), trade_date=date.fromisoformat(cand["trade_date"]),
                                    facts=context.get("facts") or {})
        ctx.db.add(booking)
        try:
            ctx.db.flush()
            for sid in context.get("ids") or []:
                ctx.db.add(SettlementBookingItem(booking_id=booking.id, source="ct", source_id=str(sid), amount=Decimal(0)))
            ctx.db.flush()
        except IntegrityError:
            raise CodedConflictError("constituent_reserved", params.fingerprint) from None
        self._book_pair(ctx, line, effect, booking=booking, counter_account_id=counter, description=f"證券交割 {booking.trade_date.isoformat()}")
        booking.action_id = ctx.action_id if hasattr(ctx, "action_id") else None
```

(`IntegrityError` inside the engine's savepoint: the engine already wraps each action in a savepoint — the implementer confirms in `engine.py` and uses `ctx.db.begin_nested()` around the item inserts if not.) Register both actions; `KIND_ACTIONS` as in Interfaces; `allowed()` adds `book_settlement` to an `ambiguous` case only when `context.hint == "settlement_ambiguous"`.

Policy: `_settlement_candidate(db, case, statement)` → `Candidate(case.id, "settlement", case.status, "book_settlement", {"line_id": case.line_id, "fingerprint": ctx.fingerprint}, _money(line.flow_amount))` for an open `settlement` case without `source_drift`; budget: `apply_candidate` skips `_charge_budget` and the `max_amount` cap when the action is in `UNCAPPED_ACTIONS = ("replace_stand_in", "book_settlement", "book_dividend")` (the bank already moved the money); `per_statement_count` still counts it.

`book_dividend` mirrors `_Creation` with a booking owner (`source = portfolio:cathay`, `fingerprint = f"dividend:{id}"`, item `("dividend", id)`), income entry on the bank account with `category_id = settings dividend_category` (nullable → `needs_review` true when unset). Dividend candidates are produced in Task 6's orchestration by the same client (`funding`-like `GET /ledger-feed/dividends?broker=&from&to` — **add it to Task 2** as `[{dividend_id, payment_date, amount_net, currency}]` from `dividends` where `receipt_account_id` is the broker's account; net = amount − fee − tax) and `settlement_match.assign_dividends` (exact amount, ±3 days, unique both sides, same shape as `assign`).

- [ ] **Step 4: Run tests**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/integration/test_settlement_actions.py tests/integration/test_settlement_reconcile.py tests/integration/test_reconciliation_actions_ledger.py tests/integration/test_reconciliation_policy.py`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add services/accounting-service/app/services/reconciliation_actions services/accounting-service/app/services/policy_service.py services/accounting-service/app/services/settings_service.py services/accounting-service/app/services/statements/settlement_match.py services/stock-portfolio-service/app services/stock-portfolio-service/tests services/accounting-service/tests
git commit -m "accounting: book_settlement / book_dividend pair creations under a booking owner; policy books them uncapped; revert deletes both legs atomically"
```

---

### Task 8: Accounting — read-through valuation in the account read models (ledger balance untouched)

**Files:**
- Modify: `services/accounting-service/app/services/ledger_service.py` (`list_accounts`, `get_account`: for accounts with `valuation_source`, replace `balance` with the portfolio total converted once, add `balance_kind`, `valuation`)
- Modify: `services/accounting-service/app/schemas/ledger.py` (`AccountOut.balance_kind: Literal["ledger","valuation"] = "ledger"`, `AccountOut.valuation: ValuationOut | None = None` with `{value, currency, as_of, priced_through, stale, fx_rate_used}`)
- Create: `services/accounting-service/app/services/valuation_service.py` (10-minute cache keyed `(broker, as_of)`; `stale` on miss with the portfolio down)
- Test: `services/accounting-service/tests/integration/test_valuation_read.py`

**Interfaces:**
- Consumes: Task 4 client; existing `fx_rate` lookup (`ledger_service` / `fx_rate_service` — the implementer greps `fx_rate` usage in `ledger_service._converted_totals:522` and reuses that rate function).
- Produces: `valuation_service.valuation_for(db, account, as_of) -> dict | None` (`{"value": Decimal in account currency, "as_of", "priced_through", "stale": bool}`); `account_balance` unchanged; `list_accounts` / `get_account` output as above; net worth (`_converted_totals` or the SPA's sum) uses the same read model.

- [ ] **Step 1: Failing tests**

```python
# tests/integration/test_valuation_read.py
from datetime import date
from decimal import Decimal
import httpx, pytest
from app.services import ledger_service, portfolio_client as pc
from app.services import reconciliation_service as rec


@pytest.fixture
def valuation_stub(monkeypatch):
    state = {"fail": None, "value": "123456.0000", "as_of": None}
    def handler(request):
        if state["fail"]:
            return httpx.Response(state["fail"])
        as_of = request.url.params["as_of"]
        return httpx.Response(200, json={"broker": "cathay", "currency": "TWD", "as_of": as_of, "priced_through": as_of,
                                         "market_value": state["value"], "cash": "0.0000", "total": state["value"]})
    client = pc.PortfolioClient("http://portfolio.test", "t", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(pc, "client_for", lambda: client)
    from app.services import valuation_service
    valuation_service.clear_cache()
    return state


def test_valuation_account_reads_portfolio_total_but_ledger_balance_stays(seed, db_session, valuation_stub):
    pos = seed.account("國泰高雄"); pos.valuation_source = "portfolio:cathay"
    seed.entry(pos, "100000", kind="transfer_in", day=date(2026, 9, 14)); db_session.commit()
    (out,) = [a for a in ledger_service.list_accounts(db_session) if a["id"] == pos.id]
    assert out["balance"] == Decimal("123456.0000") and out["balance_kind"] == "valuation" and out["valuation"]["stale"] is False
    assert ledger_service.account_balance(db_session, pos.id) == Decimal("100000")  # ledger primitive untouched


def test_historical_as_of_asks_for_that_date(seed, db_session, valuation_stub):
    pos = seed.account("國泰高雄"); pos.valuation_source = "portfolio:cathay"; db_session.commit()
    out = ledger_service.get_account(db_session, pos.id, as_of=date(2026, 9, 12))
    assert out["valuation"]["as_of"] == date(2026, 9, 12)


def test_cold_cache_with_portfolio_down_is_null_and_stale(seed, db_session, valuation_stub):
    pos = seed.account("國泰高雄"); pos.valuation_source = "portfolio:cathay"; db_session.commit()
    valuation_stub["fail"] = 503
    out = ledger_service.get_account(db_session, pos.id)
    assert out["valuation"] is None and out["balance_kind"] == "valuation" and out["balance"] is None and out["stale"] is True


def test_held_balance_and_period_end_balance_ignore_valuation(seed, db_session, run, valuation_stub):
    bank = seed.account("國泰", kind="bank"); bank.valuation_source = None
    pos = seed.account("國泰高雄"); pos.valuation_source = "portfolio:cathay"; db_session.commit()
    assert ledger_service.account_balance(db_session, pos.id) == Decimal("0")
    # period figures come from the ledger primitive
    period = ledger_service.account_period(db_session, pos.id, "2026-09")  # existing read used by the SPA ledger page
    assert period["end_balance"] == Decimal("0")
```

- [ ] **Step 2: Run to verify failure**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/integration/test_valuation_read.py`
Expected: FAIL (`balance_kind` missing)

- [ ] **Step 3: Implement**

`valuation_service.py`: module-level `_cache: dict[tuple[str, date], tuple[datetime, dict]]`, `TTL = timedelta(minutes=10)`; `valuation_for(db, account, as_of)`: parse `broker` from `account.valuation_source`; on hit return; else `client_for()`; on `None`/`PortfolioUnavailable` return the cached value with `stale: True` if any, else `None`; convert `total` to the account's currency only when the broker currency differs (`fx_rate` of `as_of` via the existing rate function; `fx_rate_used` recorded). `ledger_service.list_accounts` / `get_account`: after computing the ledger balance, for `valuation_source` accounts set `balance = valuation["value"]` (or `None` with `stale: True` when unavailable), `balance_kind = "valuation"`, `valuation = {...}`; every other account keeps `balance_kind = "ledger"`. `account_balance`, `held_balance`, period reads: no change (test pins it).

- [ ] **Step 4: Run tests**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/integration/test_valuation_read.py tests/integration/test_ledger_api.py tests/unit/test_ledger_today.py`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add services/accounting-service/app/services/valuation_service.py services/accounting-service/app/services/ledger_service.py services/accounting-service/app/schemas/ledger.py services/accounting-service/tests/integration/test_valuation_read.py
git commit -m "accounting: read-through valuation for valuation_source accounts in the account read models; ledger balance primitive unchanged"
```

---

### Task 9: `POST /reconciliation/dirty` and the portfolio call-back

**Files:**
- Modify: `services/accounting-service/app/routers/statements.py` (route, `INGEST` scope, body `{"accounts": [int]}` → marks every live, non-retired statement of those accounts dirty via the existing `CoverageDirty` mechanism; returns `{"statements": n}`)
- Modify: `services/stock-portfolio-service/app/services/post_import_orchestrator.py` (final step `_step_notify_ledger`: POST to `HOMEHUB_BASE_URL/reconciliation/dirty` with `HOMEHUB_INGEST_TOKEN`, accounts from settings `LEDGER_NOTIFY_ACCOUNTS` (default `67`); failure → step result `failed` with the HTTP code only, never the token/URL)
- Test: `services/accounting-service/tests/integration/test_reconciliation_api.py` (+1), `services/stock-portfolio-service/tests/unit/test_post_import_notify.py`

- [ ] **Step 1: Failing tests** — accounting: `client.post("/reconciliation/dirty", json={"accounts": [bank.id]}, headers=INGEST)` returns `{"statements": 1}` for one live statement and the sweep then reconciles it (`pending_statement_ids` contains it); a non-ingest token gets 403. Portfolio: with `httpx.MockTransport`, `_step_notify_ledger` posts the body `{"accounts": [67]}` with the bearer header, returns `ok`; on 503 returns `failed` with `detail == "http_503"` and the token absent from the step's repr/log.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** (the accounting route reuses the dirty-marking helper the revision hook uses — `coverage_service.mark_dirty(db, statement_id, reason="portfolio_import")` or the equivalent the implementer finds in `coverage_service`; the sweep already picks dirty statements).
- [ ] **Step 4: Run** both test files + `tests/integration/test_coverage_sweep.py`.
- [ ] **Step 5: Commit** `"accounting+portfolio: POST /reconciliation/dirty and the post-import call-back"`.

---

### Task 10: Docs, settings PUT for the owner, rollout notes

**Files:**
- Modify: `services/accounting-service/README.md` (settings keys `settlement_labels`, `settlement_window_days`, `settlement_sum_matching`, `settlement_target_account`, `dividend_category`, actions `book_settlement` / `book_dividend`; case kinds; `balance_kind` / `valuation` on account reads; `POST /reconciliation/dirty`; env `PORTFOLIO_BASE_URL` / `PORTFOLIO_READ_TOKEN`)
- Modify: `services/stock-portfolio-service/README.md` (`/ledger-feed/*`, calendar loader, `HOMEHUB_*` env)
- Modify: `services/accounting-service/deploy/statements/README.md` (rollout: migration both services → env vars → calendar load → `UPDATE account SET settlement_source='portfolio:cathay' WHERE id=67; UPDATE account SET valuation_source='portfolio:cathay' WHERE id=117` (owner SQL) → settings PUT with `settlement_target_account {"67": 117}` → reconcile 67's live statements → expect the 12 pending settlement proposals superseded by `settlement` cases → policy books → open cases on 67 drop to the non-settlement lines)
- Modify: `docs/superpowers/specs/2026-10-19-portfolio-integration-design.md` — §4.1 JSON example date aligned to the calendar (Nit), §10 "USD with fx" → "converted once in HomeHub" (Nit).

- [ ] **Step 1: Write the docs.**
- [ ] **Step 2: `git diff --check`; grep the README for every new settings key and action name.**
- [ ] **Step 3: Commit** `"docs: portfolio integration phase 1 — settings, routes, rollout"`.

---

## Self-review notes (plan author, 2026-10-19)

- Spec coverage: §4.1 → Task 1–2; §4.2 → Task 3 (columns) + Task 10 (owner SQL); §4.3 → Task 5–6; §4.4 → Task 3 + 7; §4.5 → Task 9; §4.6 → Task 2 (dividends endpoint, folded) + 7; §5 → Task 2 + 8; §8 → Task 3 + 7 + 10; §9 → Task 5 (sum gate), 6 (unavailable, drift), 7 (race via unique constraint, revert); §10 → the tests named per task. §6 (wires) and §7 (Flex) are Phase 1b / Phase 2 plans.
- AGENT-176 Shoulds: ledger primitive untouched (Task 8, Global Constraints); Flex fingerprint contract — Phase 2 plan; drift validation before the already-booked skip (Task 6 `_settlement_assignment` computes drift first, Task 7 test); sum matching disabled, line-date eligibility kept, sum-vs-single and sum-vs-sum contested (Task 5 `wanted` map); calendar envelope (`calendar_through`, null dates) in Task 1–2.
- Type consistency: `Settlement` dataclass fields (`settlement_date, trade_date, net_amount, currency, ids, fingerprint, components`) are used identically in Tasks 4–7; case context keys (`fingerprint`, `ids`, `facts`, `source`, `hint`) identical in Tasks 6–7.
- Known plan gap for the implementer: the exact helper names for positions/prices in the portfolio service (Task 2 Step 3b) and the dirty-marking helper (Task 9) are to be found, not invented; the tests pin behaviour, not names.
