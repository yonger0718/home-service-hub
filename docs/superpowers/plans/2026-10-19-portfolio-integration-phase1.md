# Portfolio integration — Phase 1 (國泰 settlement match + daily stand-ins, 117 read-through valuation, dividends) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bank 交割股款 lines on account 67 are explained from the portfolio's 國泰 settlements and booked as 67↔117 transfers under one booking owner; a settlement the 國泰 daily statement attests is visible in the ledger on its settlement day as a stand-in that the monthly bank line later claims or contradicts; account 117 shows the portfolio's market value without valuation entries; dividend lines on 67 are booked as income.

**Architecture:** The stock-portfolio-service persists the 國泰 daily statement's settlement facts and serves a read-only ledger feed (`/api/portfolio/ledger-feed/settlements`, `/valuation`, later `/dividends`). The accounting-service gains `account.settlement_source` / `valuation_source`, a `settlement_booking` owner table shaped like `statement_auto_pay`, a `portfolio_client`, a global settlement assignment step inside `reconcile`, a `book_settlement` action on the `_TwinGuarded` / `PairTransferAction` patterns, a daily stand-in job modelled on `auto_pay_service`, and read-through valuation in the account read models only. Nothing synchronises: every write carries portfolio ids and source facts; re-runs are no-ops, replacements while unclaimed, or `source_drift` cases.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2, Alembic (both services), httpx 0.28.1 (accounting → portfolio, worker → accounting), pytest (accounting: `tests/unit`, `tests/integration` on PostgreSQL, `tests/worker`; portfolio: `tests/unit`, `tests/integration` on SQLite `create_all`), PostgreSQL.

**Spec:** `docs/superpowers/specs/2026-10-19-portfolio-integration-design.md` rev 4 (2026-10-30; owner rulings Q1–Q7 in `.superpowers/r7-refresh/owner-rulings.md`, errata from `.superpowers/r7-refresh/drift-audit.md`).

**Plan rev 4 (2026-10-30, refreshed against main `7b3969f4` and the owner rulings of 2026-10-30):** the trading-calendar task is replaced by persisted statement facts + a business-day fallback (Q1); the feed is rewritten on the real modules, `/api/portfolio` prefix, no auth, `TW_CATHAY_FILTER` scope (Q2) and the statement's cash net; `book_settlement` is rebuilt on `_TwinGuarded` + `PairTransferAction`, zero-charged and outside every cap (Q4), owner twins first (Q5); drift runs over claimed lines (booked lines are owned coverage and never reach `line_rows`); the portfolio call-back is replaced by the worker's call after each 國泰 run + the sweep re-check + the monthly statement's own reconcile (Q3); **owner decision Q7 adds the daily settlement stand-ins (Tasks 3, 5, 6, 9, 10, 11 and the Review Focus)**; `/funding` moves to Phase 1b; dividends are the separated last group (Tasks 12–13, Q6 formula `amount == flow`); the rollout starts with a case-by-case dry-run listing. Rev 3 / rev 2 changelogs: git history of this file (`7b3969f4`).

## Global Constraints

- Amounts never appear in log lines, exception text, run summaries, audit `reason` codes or latch files; messages carry ids, dates, counts and codes only. Audit `detail` may carry amounts (existing auto-pay precedent).
- Docs, fixtures and tests use fictional amounts and synthetic statement text only; account ids `67` / `117` appear only as the owner SQL / settings example values they already are in the spec.
- The portfolio has no auth until issue #117: the accounting client sends `Authorization: Bearer <PORTFOLIO_READ_TOKEN>` only when that env var is set; tokens never reach logs, exception text, request URLs or API bodies.
- `ledger_service.account_balance` keeps its contract (opening + Σ posted entries); valuation replaces the balance only in `list_accounts` / `get_account` (via `_account_rows`). `reconciliation_service.held_balance` (`:719`) and `period_summary`'s `end_balance` (`ledger_service.py:763`) are unaffected and tested so.
- Settlement lines are never candidates of `backfill_entry` / `create_system_entry` and never meet the near / drift / foreign / transfer / bank-only rules (spec §4.3).
- Sum matching (spec §9) ships with `settlement_sum_matching: false`; the code path exists and is tested with a synthetic calendar-fault fixture.
- `book_settlement`, `book_dividend` and the stand-ins are zero-charged and exempt from `max_amount`, the monthly budget, `per_statement_total` and `per_statement_count` (Q4); they stay revertable.
- Owner twins first (Q5): an owner entry that records the event wins over every system booking, before booking (gate / `_TwinGuarded`) and after (twin retirement).
- Stand-ins only for feed rows with `date_source = "statement"` and `bookable = true`, settlement date in [`settlement.enabled_from`, `ledger_service._today()`] (Asia/Taipei); never for margin days, CSV-only days or days before go-live (Q7).
- No change to `composite_map`, `account_map`, matching of non-settlement lines, or the SPA in this plan; setting `valuation_source` on 117 waits for the SPA to handle `balance: null` (Task 11 rollout gate).
- Migrations are additive (nullable columns, new tables, appended enum values); downgrade drops columns and tables; appended `case_kind` values stay (PostgreSQL cannot drop them).
- Do not name anything `settlement_service` (`app/services/settlement_service.py` is the D16 collections module) or `Entry.is_settlement` (existing D16 flag): the new names are `trade_settlement_service` and `Entry.settlement_stand_in`.

## Review Focus

1. A 國泰 statement re-posted after a stand-in was booked: a corrected file is a `conflict` and changes nothing; a held day later imported (new facts) replaces the unclaimed stand-in once; after a bank line claimed it, a fact change opens `source_drift` and rebooks nothing — Task 9 `test_reposted_statement_replaces_only_an_unclaimed_stand_in`, Task 6 `test_drift_on_a_claimed_stand_in_opens_source_drift`.
2. The bank posts the settlement on another day than the statement's 交割日期 (or another amount): no claim, the line is `settlement_mismatch` naming the stand-in, the stand-in surfaces as `orphaned_stand_in`, neither moves — Task 6 `test_bank_line_on_another_date_is_a_mismatch_and_an_orphan`.
3. The owner deletes a stand-in pair, then the bank line arrives: the row is `owner_deleted`, the job never re-books it, the line's `settlement` case is proposed, never auto-booked — Task 9 `test_owner_deleted_stand_in_is_never_rebooked`, Task 7 `test_policy_proposes_an_owner_deleted_settlement`.
4. Two equal buys two days apart with two equal bank lines: nothing books, both ambiguous — Task 5 `test_two_lines_one_amount_two_settlements_are_ambiguous`.
5. A settlement line whose portfolio net differs by one trade's fee rounding: never booked; pending if unbooked, `source_drift` if booked — Task 6 `test_net_mismatch_stays_pending`.
6. The portfolio down during the sweep or the job: settlement lines pending with `settlement_source_unavailable`, the rest reconciled; the job books and retires nothing — Task 6 `test_portfolio_unavailable_reconciles_the_rest`, Task 9 `test_job_with_portfolio_down_changes_nothing`.
7. A historical `as_of` on `GET /accounts/117` returns that date's valuation, never today's; a cold cache with the portfolio down returns `balance: null, stale: true` — Task 8.
8. Revert of a `book_settlement` after a later reconcile still deletes both legs and reopens the line as `settlement_pending` — Task 7 `test_revert_after_reconcile_reopens_pending`.

---

### Task 1: Persist the 國泰 statement's settlement facts (portfolio) + business-day fallback (accounting)

**Files:**
- Create: `services/stock-portfolio-service/alembic/versions/e9settle_cathay_statement_facts.py` (revision `e9settle`, down_revision `d8broker`, the sole head; re-check with `alembic heads`)
- Modify: `services/stock-portfolio-service/app/models/cathay_statement.py:20-54` (three columns)
- Modify: `services/stock-portfolio-service/app/services/broker_cathay_statement_service.py:308-337` (`_record_attempt`) + new `statement_facts`
- Modify: `services/accounting-service/app/services/business_days.py` (append `add_business_days`)
- Test: `services/stock-portfolio-service/tests/integration/test_cathay_statement_import.py` (reuse `_payload`, `_line`, `_day`), `services/stock-portfolio-service/tests/integration/test_receipt_migration.py`-style module test in a new `tests/integration/test_statement_facts_migration.py`, `services/accounting-service/tests/unit/test_business_days.py`

**Interfaces:**
- Produces (portfolio): `CathayStatementDay.settlement_date: Date|None`, `.net_amount: Numeric(18,4)|None` (應收付 = payload `net_amount`), `.cash_net: Numeric(18,4)|None` (淨收付 = `net_amount` − Σ `line.net` of `points` lines; None for an unsupported payload); `broker_cathay_statement_service.statement_facts(payload: StatementPayload) -> tuple[date, Decimal, Decimal | None]`.
- Produces (accounting): `business_days.add_business_days(day: date, n: int, *, extra_holidays: frozenset[date] = frozenset(), extra_workdays: frozenset[date] = frozenset()) -> date | None` — the n-th business day strictly after `day`; None when any step passes `LAST_KNOWN_DAY`.

- [ ] **Step 1: Write the failing portfolio tests** (append to `test_cathay_statement_import.py`)

```python
def test_facts_persisted_on_import_and_points_are_not_cash(db_session):
    points = _line("範例電子", "集買", 10, "12.3", 0, order_no="XY12301") | {"points": True}
    p = _payload(lines=DEFAULT_LINES + [points])
    import_statement(db_session, p, dry_run=False)
    day = _day(db_session)
    assert day.settlement_date == date(2026, 3, 11) and day.net_amount == Decimal(p.net_amount)
    assert day.cash_net == Decimal(p.net_amount) - Decimal(points["net"])


def test_facts_persisted_on_a_held_unsupported_day_without_cash_net(db_session):
    p = _payload(lines=[], warnings=["unsupported_category"])
    import_statement(db_session, p, dry_run=False)
    day = _day(db_session)
    assert (day.last_status, day.last_reason, day.settlement_date, day.cash_net) == ("held", "unsupported_category", date(2026, 3, 11), None)


def test_duplicate_fills_missing_facts_and_conflict_never_writes_them(db_session):
    import_statement(db_session, _payload(), dry_run=False)
    _day(db_session).settlement_date = _day(db_session).net_amount = _day(db_session).cash_net = None  # a pre-e9settle row
    db_session.commit()
    import_statement(db_session, _payload(), dry_run=False)                       # same sha → duplicate
    assert _day(db_session).settlement_date == date(2026, 3, 11)
    before = (_day(db_session).net_amount, _day(db_session).cash_net)
    other = [_line("範例電子", "集買", 1000, "12.3", 6, order_no="XY12300"), DEFAULT_LINES[1]]
    import_statement(db_session, _payload(lines=other, sha="b" * 64), dry_run=False)  # corrected file → conflict
    assert (_day(db_session).last_status, _day(db_session).net_amount, _day(db_session).cash_net) == ("conflict", *before)
```

Migration module test (`test_statement_facts_migration.py`, the `test_receipt_migration.py` pattern): create `cathay_statement_day (trade_date DATE PRIMARY KEY)` on SQLite, run `upgrade()`, assert the three columns exist and are nullable, run `downgrade()`, assert they are gone; `test_real_graph_has_one_head` in `test_receipt_migration.py` keeps passing.

- [ ] **Step 2: Run to verify they fail**

Run: `cd services/stock-portfolio-service && .venv/bin/python -m pytest -q tests/integration/test_cathay_statement_import.py -k facts tests/integration/test_statement_facts_migration.py`
Expected: FAIL (`AttributeError: settlement_date` / missing migration file)

- [ ] **Step 3: Implement the migration, columns, `statement_facts` and `_record_attempt`**

`_record_attempt` writes the facts for outcomes `imported`, `verified`, `held`, `mismatch` (the latest post's facts; owned or not); for `duplicate` it fills only NULL columns; it never writes them for `conflict` (the accepted file's facts stay). The existing early return (duplicate of a pending conflict) is unchanged. No backfill of pre-migration days: they fall back to `date_source = "legs"` (Task 2).

- [ ] **Step 4: Run the portfolio tests**

Run: `cd services/stock-portfolio-service && .venv/bin/python -m pytest -q tests/integration/test_cathay_statement_import.py tests/integration/test_statement_facts_migration.py tests/integration/test_receipt_migration.py tests/integration/test_cathay_statement_routes.py`
Expected: all pass

- [ ] **Step 5: Write the failing accounting test** (append to `tests/unit/test_business_days.py`)

```python
from datetime import date
from app.services.business_days import add_business_days


def test_t_plus_two_over_weekend_holiday_and_overrides():
    assert add_business_days(date(2026, 10, 1), 2) == date(2026, 10, 5)     # Thu → Mon
    assert add_business_days(date(2026, 10, 7), 2) == date(2026, 10, 12)    # Wed → Mon (10-09 補假, weekend)
    assert add_business_days(date(2026, 10, 7), 2, extra_workdays=frozenset({date(2026, 10, 10)})) == date(2026, 10, 10)
    assert add_business_days(date(2026, 10, 1), 2, extra_holidays=frozenset({date(2026, 10, 2)})) == date(2026, 10, 6)
    assert add_business_days(date(2027, 12, 30), 2) is None                  # past LAST_KNOWN_DAY
```

- [ ] **Step 6: Run to verify it fails, implement `add_business_days` (loop over `is_business_day`, stop with None once `calendar_covers` is false), run again**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/unit/test_business_days.py`
Expected: FAIL (`ImportError`), then all pass

- [ ] **Step 7: Commit (two commits)**

```bash
git add services/stock-portfolio-service/alembic/versions/e9settle_cathay_statement_facts.py services/stock-portfolio-service/app/models/cathay_statement.py services/stock-portfolio-service/app/services/broker_cathay_statement_service.py services/stock-portfolio-service/tests/integration/test_cathay_statement_import.py services/stock-portfolio-service/tests/integration/test_statement_facts_migration.py
git commit -m "portfolio: persist the 國泰 daily statement's settlement date, net and cash net per trade date"
git add services/accounting-service/app/services/business_days.py services/accounting-service/tests/unit/test_business_days.py
git commit -m "accounting: add_business_days for the T+2 settlement fallback"
```

---

### Task 2: Portfolio — ledger feed `GET /api/portfolio/ledger-feed/settlements` and `/valuation`

**Files:**
- Create: `services/stock-portfolio-service/app/schemas/ledger_feed.py`
- Create: `services/stock-portfolio-service/app/services/ledger_feed_service.py`
- Create: `services/stock-portfolio-service/app/routers/ledger_feed.py` (`APIRouter(prefix="/api/portfolio/ledger-feed")`)
- Modify: `services/stock-portfolio-service/app/main.py:8,21-35` (import + register `ledger_feed.router`)
- Test: `services/stock-portfolio-service/tests/integration/test_ledger_feed.py` (fixtures in the module: a `BrokerAccount` CATHAY TWD like `test_cathay_statement_import._env`, `Transaction` rows with `broker="TW_CATHAY"` / `None` / `"TW_MANUAL"`, their `cash_transaction` legs written through `cash_account_service`, `CathayStatementDay` rows with facts, `PriceHistory` and `CorporateAction` rows)

**Interfaces:**
- Consumes: Task 1 columns; `broker_cathay_statement_service.TW_CATHAY_FILTER` (`:135`); `cash_account_service.resolve_default_cathay_twd_account` (`:157`); `portfolio_service._load_corp_actions_by_symbol` (`:159`), `_apply_corp_action_factors` (`:138`); `PriceHistory`.
- Produces: `ledger_feed_service.settlements(db, broker: str, date_from: date, date_to: date) -> list[dict]` and `ledger_feed_service.valuation(db, broker: str, as_of: date) -> dict`; `broker != "cathay"` → `UnknownBroker` → 404 `{"detail": "unknown_broker"}`.
  - Settlement row (spec §4.1): `{"trade_date", "settlement_date": str|None, "date_source": "statement"|"legs", "net_amount", "legs_net", "currency": "TWD", "bookable": bool, "reason": None|"margin_day"|"legs_disagree", "items": [{"source_id": "ct:<cash_transaction.id>", "type", "amount"}], "fingerprint": "cathay:<trade_date>:<sha256(','.join(sorted ids))[:16]>"}`; amounts as 4-place strings. `from`/`to` bound the trade date. Legs = `buy_settle` / `sell_settle` / `fee` / `tax` cash rows of the 國泰 TWD account whose `related_transaction_id` is a `TW_CATHAY_FILTER` transaction. A day with statement facts: `date_source "statement"`, `settlement_date` from the statement; margin (`last_status == "held"` and `last_reason == "unsupported_category"`) → `net_amount` = statement `net_amount`, `bookable false`, `reason "margin_day"`; else `net_amount = cash_net`, and `cash_net != legs_net` → `bookable false`, `reason "legs_disagree"`. A day without facts: `date_source "legs"`, `settlement_date null`, `net_amount = legs_net`, `bookable true`. Rows with `net_amount == 0` and no items are omitted. Sorted by trade date.
  - Valuation: `{"broker": "cathay", "currency": "TWD", "as_of", "priced_through": str|None, "market_value", "cash": "0.0000", "total", "complete": bool, "missing": [symbol]}` — LONG quantities of `TW_CATHAY_FILTER` transactions traded on or before `as_of`, corporate actions effective on or before `as_of` only, × the last `price_history.close` on or before `as_of`; `priced_through` = the oldest of those price dates; a held symbol with no price → in `missing`, excluded from `market_value`, `complete false`.

- [ ] **Step 1: Write the failing tests**

```python
def test_statement_day_uses_the_printed_date_and_cash_net(client, cathay):
    cathay.trade("2026-09-10", "BUY", qty=1000, price="12.3", fee="5")          # legs -12305 (fictional)
    cathay.statement_day("2026-09-10", settlement="2026-09-14", net="-12305", cash_net="-12305")
    (row,) = client.get("/api/portfolio/ledger-feed/settlements", params={"broker": "cathay", "from": "2026-09-01", "to": "2026-09-30"}).json()
    assert (row["settlement_date"], row["date_source"], row["net_amount"], row["bookable"], row["reason"]) == ("2026-09-14", "statement", "-12305.0000", True, None)
    assert {i["type"] for i in row["items"]} == {"buy_settle", "fee"} and row["fingerprint"].startswith("cathay:2026-09-10:")


def test_csv_only_day_has_no_date_and_other_brokers_are_left_out(client, cathay):
    cathay.trade("2026-09-11", "SELL", qty=100, price="50", fee="7", tax="15")
    cathay.trade("2026-09-11", "BUY", qty=10, price="10", fee="1", broker="TW_MANUAL")  # same cash account, out of scope
    (row,) = client.get("/api/portfolio/ledger-feed/settlements", params={"broker": "cathay", "from": "2026-09-11", "to": "2026-09-11"}).json()
    assert (row["settlement_date"], row["date_source"], row["net_amount"], row["legs_net"]) == (None, "legs", "4978.0000", "4978.0000")


def test_margin_day_and_disagreeing_legs_are_not_bookable(client, cathay):
    cathay.statement_day("2026-09-15", settlement="2026-09-17", net="-30000", cash_net=None, held="unsupported_category")
    cathay.trade("2026-09-16", "BUY", qty=1000, price="10", fee="14")
    cathay.statement_day("2026-09-16", settlement="2026-09-18", net="-10014", cash_net="-10015")
    rows = client.get("/api/portfolio/ledger-feed/settlements", params={"broker": "cathay", "from": "2026-09-15", "to": "2026-09-16"}).json()
    assert [(r["trade_date"], r["bookable"], r["reason"]) for r in rows] == [("2026-09-15", False, "margin_day"), ("2026-09-16", False, "legs_disagree")]


def test_unknown_broker_is_404(client):
    assert client.get("/api/portfolio/ledger-feed/settlements", params={"broker": "ib", "from": "2026-09-01", "to": "2026-09-02"}).status_code == 404


def test_valuation_last_price_on_or_before_as_of(client, cathay):
    cathay.trade("2026-09-01", "BUY", qty=1000, price="90", fee="0", symbol="9999")
    cathay.price("9999", "2026-09-10", "100"); cathay.price("9999", "2026-09-15", "110")
    body = client.get("/api/portfolio/ledger-feed/valuation", params={"broker": "cathay", "as_of": "2026-09-12"}).json()
    assert (body["market_value"], body["priced_through"], body["cash"], body["total"], body["complete"]) == ("100000.0000", "2026-09-10", "0.0000", "100000.0000", True)


def test_valuation_reports_unpriced_holdings_and_ignores_later_splits(client, cathay):
    cathay.trade("2026-09-01", "BUY", qty=1000, price="90", fee="0", symbol="9999"); cathay.price("9999", "2026-09-10", "100")
    cathay.trade("2026-09-02", "BUY", qty=10, price="5", fee="0", symbol="9998")       # never priced
    cathay.split("9999", "2026-09-20", ratio="2")
    body = client.get("/api/portfolio/ledger-feed/valuation", params={"broker": "cathay", "as_of": "2026-09-12"}).json()
    assert (body["market_value"], body["complete"], body["missing"]) == ("100000.0000", False, ["9998"])
```

`cathay` is a module fixture object with `trade(...)` (inserts the `Transaction` and its legs through the same `cash_account_service` path the CSV importer uses, `CASH_LEG_ENABLED=true`), `statement_day(...)`, `price(...)`, `split(...)`.

- [ ] **Step 2: Run to verify they fail**

Run: `cd services/stock-portfolio-service && .venv/bin/python -m pytest -q tests/integration/test_ledger_feed.py`
Expected: FAIL (404 on every route)

- [ ] **Step 3: Implement schemas, service and router; register the router in `main.py`**

Response models are plain Pydantic models with `str` amounts (`f"{value:.4f}"`). Corporate actions for `as_of`: filter the `_load_corp_actions_by_symbol` lists to `effective_date <= as_of` before `_apply_corp_action_factors`.

- [ ] **Step 4: Run tests**

Run: `cd services/stock-portfolio-service && .venv/bin/python -m pytest -q tests/integration/test_ledger_feed.py tests/unit`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add services/stock-portfolio-service/app/schemas/ledger_feed.py services/stock-portfolio-service/app/services/ledger_feed_service.py services/stock-portfolio-service/app/routers/ledger_feed.py services/stock-portfolio-service/app/main.py services/stock-portfolio-service/tests/integration/test_ledger_feed.py
git commit -m "portfolio: read-only ledger feed — 國泰 settlements per trade date and point-in-time valuation"
```

---

### Task 3: Accounting — schema: account sources, `settlement_booking`, case kinds, audit events

**Files:**
- Create: `services/accounting-service/alembic/versions/d5e1f7a3b9c2_settlement_booking.py` (down_revision `c4a7e2d9f1b3`, the sole head)
- Modify: `services/accounting-service/app/models/ledger.py` (Account: two columns)
- Modify: `services/accounting-service/app/models/statements.py` (`CASE_KINDS` `:27` += `settlement`, `dividend`; models `SettlementBooking`, `SettlementBookingItem` after `StatementAutoPay` `:392`)
- Modify: `services/accounting-service/app/services/reconciliation_service.py:76` (`MATCHER_KINDS` += `settlement`, `dividend`)
- Modify: `services/accounting-service/app/services/reconciliation_actions/audit.py:10-19` (`EVENTS` += `settlement_booked`, `settlement_skipped`, `settlement_retired`, `settlement_replaced`, `settlement_owner_deleted`, `settlement_owner_edited`, `settlement_claimed`)
- Test: `services/accounting-service/tests/integration/test_migration.py` (next to `test_statement_auto_pay_round_trip` `:709`; `test_head_schema_matches_the_models` `:189` must keep passing)

**Interfaces:**
- Produces: `Account.settlement_source: str|None`, `Account.valuation_source: str|None` (String(64)).
- `SettlementBooking` (`settlement_booking`): `id`, `kind` (`settlement` | `dividend`), `origin` (`bank` | `stand_in`), `account_id` FK account RESTRICT, `target_account_id` FK account NULL, `event_id` FK statement_event NULL (indexed), `source` String(64), `fingerprint` String(128), `trade_date` Date NULL, `settlement_date` Date NOT NULL, `net_amount` MONEY, `facts` JSONB default `{}`, `transfer_group_id` Uuid NULL, `out_entry_id` / `in_entry_id` FK ledger_entry ON DELETE SET NULL, `status` (`booked` | `retired` | `replaced` | `owner_deleted` | `owner_edited` | `skipped`, CHECK), `reason` String(64) NULL, `action_id` FK reconciliation_action NULL, `created_at`, `updated_at`; partial unique indexes `ux_settlement_booking_fingerprint_booked (fingerprint) WHERE status = 'booked'` and `ux_settlement_booking_day_booked (account_id, settlement_date) WHERE status = 'booked' AND kind = 'settlement'`; relationship `items` (cascade all, delete-orphan).
- `SettlementBookingItem` (`settlement_booking_item`): `id`, `booking_id` FK CASCADE, `source` String(16) (`ct` | `div`), `source_id` String(64) (the feed id verbatim, e.g. `ct:5`, `div:12`), `amount` MONEY, `UNIQUE(source, source_id)` named `ux_settlement_booking_item_source`.
- `case_kind` gains `settlement`, `dividend` (`ALTER TYPE … ADD VALUE IF NOT EXISTS` inside `op.get_context().autocommit_block()`, as `d1f3a7c2e9b4:214`).

- [ ] **Step 1: Write the failing migration test**

```python
def test_settlement_booking_round_trip(database_factory, alembic_config):
    engine = database_factory()
    command.upgrade(alembic_config(engine), "head")
    insp = sa.inspect(engine)
    assert {"settlement_source", "valuation_source"} <= {c["name"] for c in insp.get_columns("account")}
    assert "settlement_booking_id" not in {c["name"] for c in insp.get_columns("ledger_entry")}
    idx = {i["name"]: i for i in insp.get_indexes("settlement_booking")}
    assert idx["ux_settlement_booking_fingerprint_booked"]["unique"] and idx["ux_settlement_booking_day_booked"]["unique"]
    assert ("source", "source_id") in {tuple(u["column_names"]) for u in insp.get_unique_constraints("settlement_booking_item")}
    with engine.connect() as conn:
        kinds = conn.execute(sa.text("select unnest(enum_range(null::case_kind))::text")).scalars().all()
    assert {"settlement", "dividend"} <= set(kinds)
    command.downgrade(alembic_config(engine), "c4a7e2d9f1b3")
    assert "settlement_booking" not in sa.inspect(engine).get_table_names()
```

Plus one ORM test in the same file: two `booked` rows with the same fingerprint → `IntegrityError`; the first `retired`, then a second `booked` one with the same fingerprint and the same (account, settlement_date) → accepted.

- [ ] **Step 2: Run to verify it fails**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/integration/test_migration.py -k "settlement_booking or head_schema"`
Expected: FAIL (columns / tables missing)

- [ ] **Step 3: Write the migration and the models** (partial indexes as `Index(..., unique=True, postgresql_where=...)` in both so `test_head_schema_matches_the_models` compares equal)

- [ ] **Step 4: Run the migration tests and the unit directory**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/integration/test_migration.py tests/unit`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add services/accounting-service/alembic/versions/d5e1f7a3b9c2_settlement_booking.py services/accounting-service/app/models/ledger.py services/accounting-service/app/models/statements.py services/accounting-service/app/services/reconciliation_service.py services/accounting-service/app/services/reconciliation_actions/audit.py services/accounting-service/tests/integration/test_migration.py
git commit -m "accounting: settlement booking owner table, account settlement/valuation sources, settlement/dividend case kinds"
```

---

### Task 4: Accounting — `portfolio_client`

**Files:**
- Create: `services/accounting-service/app/services/portfolio_client.py`
- Modify: `services/accounting-service/requirements.txt` (add `httpx==0.28.1`, the pin of `requirements-worker.txt:6`)
- Test: `services/accounting-service/tests/unit/test_portfolio_client.py` (`httpx.MockTransport`)

**Interfaces:**
- Produces: frozen dataclasses `Item(source_id: str, type: str, amount: Decimal)`; `Settlement(trade_date: date, settlement_date: date | None, date_source: str, net_amount: Decimal, legs_net: Decimal, currency: str, bookable: bool, reason: str | None, items: tuple[Item, ...], fingerprint: str)` with property `source_ids -> tuple[str, ...]` (sorted); `Valuation(broker, currency, as_of: date, priced_through: date | None, market_value: Decimal, cash: Decimal, total: Decimal, complete: bool, missing: tuple[str, ...])`.
- `PortfolioClient(base_url: str, token: str | None = None, *, timeout: float = 5.0, transport: httpx.BaseTransport | None = None)` with `settlements(broker, date_from, date_to) -> list[Settlement]` and `valuation(broker, as_of) -> Valuation`; paths `/api/portfolio/ledger-feed/settlements|valuation`; header `Authorization: Bearer <token>` only when a token is set; `__repr__ = object.__repr__`.
- `PortfolioUnavailable(RuntimeError)` with `.code` ∈ {`connect`, `timeout`, `http_<status>`, `bad_body`} on any failure; message = the code only.
- `client_for() -> PortfolioClient`: base URL `PORTFOLIO_BASE_URL` (default `http://127.0.0.1:8001`), token `PORTFOLIO_READ_TOKEN` (optional), read at call time.
- `cached(client) -> ContextManager[PortfolioClient]`: memoises reads per (method, args) inside the block.

- [ ] **Step 1: Write the failing tests**

```python
TOKEN = "secret-token-value"
ROW = {"trade_date": "2026-09-10", "settlement_date": "2026-09-14", "date_source": "statement", "net_amount": "-12305.0000",
       "legs_net": "-12305.0000", "currency": "TWD", "bookable": True, "reason": None, "fingerprint": "cathay:2026-09-10:abc",
       "items": [{"source_id": "ct:7", "type": "fee", "amount": "-5.0000"}, {"source_id": "ct:5", "type": "buy_settle", "amount": "-12300.0000"}]}


def test_settlements_parse_and_header_only_with_a_token():
    seen = []
    def handler(request):
        seen.append((request.url.path, request.headers.get("authorization")))
        return httpx.Response(200, json=[ROW])
    (row,) = pc.PortfolioClient("http://p.test", transport=httpx.MockTransport(handler)).settlements("cathay", date(2026, 9, 1), date(2026, 9, 30))
    pc.PortfolioClient("http://p.test", TOKEN, transport=httpx.MockTransport(handler)).settlements("cathay", date(2026, 9, 1), date(2026, 9, 30))
    assert seen == [("/api/portfolio/ledger-feed/settlements", None), ("/api/portfolio/ledger-feed/settlements", f"Bearer {TOKEN}")]
    assert (row.settlement_date, row.net_amount, row.bookable, row.source_ids) == (date(2026, 9, 14), Decimal("-12305.0000"), True, ("ct:5", "ct:7"))


@pytest.mark.parametrize("make, code", [(lambda r: httpx.Response(500), "http_500"), (lambda r: httpx.Response(404), "http_404"),
                                        (lambda r: httpx.Response(200, json={"x": 1}), "bad_body"),
                                        (lambda r: (_ for _ in ()).throw(httpx.ConnectError("down")), "connect")])
def test_failures_are_unavailable_with_a_code_only(make, code, caplog):
    with pytest.raises(pc.PortfolioUnavailable) as info:
        pc.PortfolioClient("http://p.test", TOKEN, transport=httpx.MockTransport(make)).valuation("cathay", date(2026, 9, 30))
    assert info.value.code == code and TOKEN not in str(info.value) + repr(info.value) + caplog.text


def test_cached_client_memoises_within_the_block():
    calls = []
    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=[ROW])
    with pc.cached(pc.PortfolioClient("http://p.test", transport=httpx.MockTransport(handler))) as c:
        c.settlements("cathay", date(2026, 9, 1), date(2026, 9, 30)); c.settlements("cathay", date(2026, 9, 1), date(2026, 9, 30))
    assert calls == [1]


def test_client_for_defaults_to_loopback(monkeypatch):
    monkeypatch.delenv("PORTFOLIO_BASE_URL", raising=False); monkeypatch.delenv("PORTFOLIO_READ_TOKEN", raising=False)
    assert pc.client_for().base_url == "http://127.0.0.1:8001"
```

- [ ] **Step 2: Run to verify failure** — `cd services/accounting-service && .venv/bin/python -m pytest -q tests/unit/test_portfolio_client.py` → FAIL (`ModuleNotFoundError`)
- [ ] **Step 3: Implement** (one `httpx.Client` per call; parse errors map to `bad_body`)
- [ ] **Step 4: Run** the same command → all pass
- [ ] **Step 5: Commit**

```bash
git add services/accounting-service/app/services/portfolio_client.py services/accounting-service/requirements.txt services/accounting-service/tests/unit/test_portfolio_client.py
git commit -m "accounting: read-only portfolio ledger-feed client with token-safe failures"
```

---

### Task 5: Accounting — settlement assignment and the stand-in claim (pure matching code)

**Files:**
- Create: `services/accounting-service/app/services/statements/settlement_match.py`
- Modify: `services/accounting-service/app/services/statements/matching.py` (`Rules` `:128-143`; `Entry` `:66-92`; `_representations` `:603`; `match` `:1066`)
- Test: `services/accounting-service/tests/unit/test_settlement_match.py`, `services/accounting-service/tests/unit/test_statement_matching.py` (+3)

**Interfaces:**
- Consumes: `matching.Line`, `matching.Entry`, `matching._same` (`:333`), Task 4 `Settlement`, Task 1 `add_business_days`.
- Produces in `matching`: `SETTLEMENT_LABELS = ("交割股款", "申購股款")`; `Rules.settlement_labels: tuple[str, ...] = SETTLEMENT_LABELS`, `Rules.settlement_window_days: int = 2`, `Rules.settlement_sum_matching: bool = False` (`RULE_DEFAULTS` and `_check_rules` pick them up from the dataclass, `settings_service.py:81,228`); `Entry.settlement_stand_in: bool = False`; `is_settlement_line(line: Line, rules: Rules) -> bool` (`line_kind ∈ {withdrawal, deposit}` and a label is a substring of `merchant_norm`); `SETTLEMENT_STAND_IN_RULE = "settlement_stand_in"`; `SETTLEMENT_LINE_HINT = "settlement_line"`.
- `match` on a bank statement, for a settlement line: the accept and ambiguous steps as today (owner twins; stand-in reps are absent); no claimed-by-earlier-line case (the global assignment treats look-alikes); then `_settlement_stand_in_claim(line, entries, claimed) -> Representation | None` (exactly one unclaimed entry with `settlement_stand_in`, `flow == line.flow`, `entry_date == line.posted_date` → standalone claim at `rules.accept`, rule `settlement_stand_in`); otherwise `Case(line.id, None, "line_unmatched", (), {"hint": "settlement_line"})` — no installment, auto-pay, foreign, transfer, near, drift, bank-only or needs-peer step. `_representations` never represents an entry with `settlement_stand_in`.
- Produces in `settlement_match`: `with_dates(rows: list[Settlement], *, extra_holidays, extra_workdays) -> list[Settlement]` (a `legs` row gets `add_business_days(trade_date, 2, …)`; None stays None); `@dataclass Assignment(booked: dict[int, Settlement], ambiguous: dict[int, list[Settlement]], pending: dict[int, str | None], unknown_dates: int)`; `assign(lines: list[Line], settlements: list[Settlement], *, reserved_fps: set[str], reserved_ids: set[str], contestants: set[int], rules: Rules) -> Assignment`.
  - Candidate of a line: `net_amount == line.flow` (`_same`), dated, `|settlement_date − posted_date| <= settlement_window_days`, fingerprint not in `reserved_fps`, no constituent in `reserved_ids`. Sum candidates only when `settlement_sum_matching` (spec §9 gate; fingerprint `f1+f2`, items merged).
  - A line in `contestants` (already explained) competes and is never in `booked` / `ambiguous` / `pending`.
  - Booked: the line has one candidate, no constituent is wanted by another line (contestants included), and the candidate is `bookable`. One non-bookable sole candidate → `pending[line] = reason`; no candidate → `pending[line] = None`; else `ambiguous`.

- [ ] **Step 1: Write the failing tests**

```python
R = m.Rules(period_end=date(2026, 9, 30))


def L(i, day, flow, merchant="交割股款", kind="withdrawal"):
    return m.Line(id=i, event_id=100 + i, posted_date=day, txn_date=day, flow=Decimal(flow), foreign_amount=None,
                  foreign_currency=None, line_kind=kind, merchant_norm=merchant, installment_seq=None, installment_total=None)


def S(fp, sd, net, *, td=None, ids=("ct:1",), bookable=True, reason=None, source="statement"):
    items = tuple(Item(i, "buy_settle", Decimal(net) / len(ids)) for i in ids)
    return Settlement(td or sd, sd, source, Decimal(net), Decimal(net), "TWD", bookable, reason, items, fp)


def A(lines, sets, *, fps=(), ids=(), contestants=(), rules=R):
    return sm.assign(lines, sets, reserved_fps=set(fps), reserved_ids=set(ids), contestants=set(contestants), rules=rules)


def test_unique_pair_books_within_two_days():
    a = A([L(1, date(2026, 9, 14), "-12305")], [S("f1", date(2026, 9, 12), "-12305")])
    assert list(a.booked) == [1] and a.ambiguous == {} and a.pending == {}


def test_two_lines_one_amount_two_settlements_are_ambiguous():
    a = A([L(1, date(2026, 9, 14), "-5000"), L(2, date(2026, 9, 16), "-5000")], [S("f1", date(2026, 9, 14), "-5000"), S("f2", date(2026, 9, 15), "-5000", ids=("ct:2",))])
    assert a.booked == {} and set(a.ambiguous) == {1, 2}


def test_a_contestant_blocks_its_look_alike_and_is_never_booked():
    a = A([L(1, date(2026, 9, 14), "-5000"), L(2, date(2026, 9, 15), "-5000")], [S("f1", date(2026, 9, 14), "-5000")], contestants={1})
    assert a.booked == {} and set(a.ambiguous) == {2} and 1 not in a.pending


def test_reserved_and_undated_are_never_candidates():
    sets = [S("f1", date(2026, 9, 14), "-5000"), S("f2", None, "-5000", ids=("ct:2",)), S("f3", date(2026, 9, 14), "-5000", ids=("ct:9", "ct:1"))]
    a = A([L(1, date(2026, 9, 14), "-5000")], sets, fps={"f1"}, ids={"ct:1"})
    assert a.booked == {} and a.pending == {1: None} and a.unknown_dates == 1


def test_a_sole_non_bookable_candidate_is_pending_with_its_reason():
    a = A([L(1, date(2026, 9, 17), "-30000")], [S("f1", date(2026, 9, 17), "-30000", bookable=False, reason="margin_day")])
    assert a.pending == {1: "margin_day"} and a.booked == {}


def test_non_settlement_lines_are_ignored_and_amount_is_exact():
    a = A([L(1, date(2026, 9, 14), "-5000", merchant="網銀轉帳"), L(2, date(2026, 9, 14), "-5001")], [S("f1", date(2026, 9, 14), "-5000")])
    assert a.pending == {2: None} and 1 not in a.ambiguous


def test_sum_matching_is_off_by_default_and_gated_when_on():
    lines, sets = [L(1, date(2026, 9, 14), "-8000")], [S("f1", date(2026, 9, 14), "-5000"), S("f2", date(2026, 9, 15), "-3000", ids=("ct:2",))]
    assert A(lines, sets).pending == {1: None}
    on = m.Rules(period_end=date(2026, 9, 30), settlement_sum_matching=True)
    a = A(lines, sets, rules=on)
    assert (a.booked[1].fingerprint, a.booked[1].source_ids, a.booked[1].net_amount) == ("f1+f2", ("ct:1", "ct:2"), Decimal("-8000"))
    assert A(lines + [L(2, date(2026, 9, 14), "-5000")], sets, rules=on).booked == {}           # sum vs single → ambiguous


def test_with_dates_derives_t_plus_two_only_for_legs_rows():
    rows = sm.with_dates([S("f1", None, "-1", td=date(2026, 10, 7), source="legs"), S("f2", date(2026, 10, 8), "-1", td=date(2026, 10, 6))],
                         extra_holidays=frozenset(), extra_workdays=frozenset())
    assert [r.settlement_date for r in rows] == [date(2026, 10, 12), date(2026, 10, 8)]
```

`test_statement_matching.py` (+3), with the file's existing `Entry` / `Line` builders: `test_settlement_line_is_claimed_by_an_exact_owner_entry_first` (an owner `transfer_out` of the flow on the posted date and a `settlement_stand_in` entry → the owner claims, the stand-in stays unclaimed), `test_settlement_stand_in_claims_only_on_the_exact_date` (posted date +1 → no claim, the `settlement_line` placeholder case), `test_settlement_line_never_gets_near_or_bank_only` (a near-amount owner entry → placeholder, no `amount_delta`).

- [ ] **Step 2: Run to verify failure**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/unit/test_settlement_match.py tests/unit/test_statement_matching.py -k settlement`
Expected: FAIL (`ModuleNotFoundError`, `TypeError: settlement_stand_in`)

- [ ] **Step 3: Implement `settlement_match.py` and the `matching` hooks**

Contest rule (the one algorithm the tests do not pin alone): build `wanted: dict[source_id, set[line_id]]` over every line's candidates (contestants included); a line's candidates are contested when any constituent is wanted by another line.

- [ ] **Step 4: Run tests**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/unit/test_settlement_match.py tests/unit/test_statement_matching.py tests/unit/test_schedule_rules.py tests/integration/test_settings_crud_api.py tests/integration/test_auto_pay_settings.py`
Expected: all pass (no existing fixture prints a settlement label; a test that snapshots `RULE_DEFAULTS` gains the three keys)

- [ ] **Step 5: Commit**

```bash
git add services/accounting-service/app/services/statements/settlement_match.py services/accounting-service/app/services/statements/matching.py services/accounting-service/tests/unit/test_settlement_match.py services/accounting-service/tests/unit/test_statement_matching.py
git commit -m "accounting: settlement lines meet owner twins, then the stand-in on its exact date, then a global assignment"
```

---

### Task 6: Accounting — orchestration in `reconcile`: fetch, assign, cases, stand-in claims, drift over claimed lines

**Files:**
- Create: `services/accounting-service/app/services/trade_settlement_service.py` (reconcile half; Task 9 adds the job half)
- Modify: `services/accounting-service/app/services/stand_ins.py` (`settlement_legs` next to `auto_pay_legs` `:27`; `stand_in_entry_ids` `:39` includes them; `orphans` `:339` appends `_settlement_orphans`)
- Modify: `services/accounting-service/app/services/reconciliation_service.py` (`ReconcileResult` `:86`; population flags `:302-312`; `reconcile` `:963-1039`; `_desired_cases` `:638`)
- Modify: `services/accounting-service/app/services/statement_revision_service.py:62` (`reconciliation_hook`: after a successful reconcile of a `settlement_source` statement, flag neighbours)
- Modify: `services/accounting-service/app/schemas/statements.py` (`ReconcileOut` `:450`, `SweepOut` `:466`), `services/accounting-service/app/routers/statements.py:277-321` (map the new fields)
- Modify: `services/accounting-service/app/services/inbox_service.py:30,50-75,137,267` (`classify` + entry-candidate guards)
- Create: `services/accounting-service/tests/integration/settlement_fixtures.py`
- Test: `services/accounting-service/tests/integration/test_settlement_reconcile.py`, `services/accounting-service/tests/unit/test_inbox_service.py` (+2)

**Interfaces:**
- Consumes: Task 3 models, Task 4 client, Task 5 `assign` / `with_dates` / `is_settlement_line` / `SETTLEMENT_LINE_HINT`; `auto_pay_service.calendar_sets` (`:81`) over `read_auto_pay_settings` for the calendar overrides; `_guard_neighbours` / `guard_lines` (`reconciliation_service.py:170-201`) for the live neighbour lines.
- Produces in `stand_ins`: `settlement_legs(db, account_ids: list[int]) -> dict[int, int]` (entry id → `settlement_booking.id` for legs of `booked` rows of `kind = settlement` and `origin = stand_in` whose own account is listed); `stand_in_entry_ids` = backfills ∪ auto-pay legs ∪ settlement legs; `_settlement_orphans(db, statement, accounts, claimed) -> list[dict]` (unclaimed settlement stand-in legs dated in the period with no settlement twin → `{"entry_id", "event_id": None, "line_id": None, "backfill_action_id": None, "settlement_booking_id", "claimant_entry_id": None}`; `_desired_cases` copies `settlement_booking_id` into the `orphaned_stand_in` context like `auto_pay_id` `:689-690`).
- Produces in `trade_settlement_service`: `ACTOR = "settlement"`; `fetch(db, account: Account, lo: date, hi: date) -> list[Settlement]` (client + `with_dates`, raises `PortfolioUnavailable`); `find_twins(db, row: SettlementBooking, *, window_days: int, exclude: Iterable[int] = ()) -> list[int]` (top-level non-statement-source entries within `window_days` of `row.settlement_date`: on the bank account with the bank leg's amount, or a transfer leg on the target account with its mirror); `retire_pair(db, row, *, reason: str) -> set[int] | None` (the `auto_pay_service.retire_pair` sequence `:357-409`: lock row then legs, re-check, release coverage, `delete_entries_cascade`, items deleted, status `retired`, released statements `needs_recheck`, audit `settlement_retired`); `retire_twins_for_statement(db, statement, accounts) -> list[int]`; `link_claims(db, statement) -> list[int]` (a booked stand-in whose bank leg holds active coverage on a current line of this statement gets `event_id` = that line's event, audit `settlement_claimed`; idempotent); `bookings_by_event(db, account_id) -> dict[int, SettlementBooking]`.
- `reconcile` (when `accounts_by_id[statement.account_id].settlement_source` is set and the statement is a bank statement printing a settlement line): before `_lock_candidates` (`:1006`) fetch `lo, hi = period_start − 5, period_end + 5` inside `portfolio_client.cached`; then `trade_settlement_service.retire_twins_for_statement` right after the auto-pay call at `:990`; population entries get `settlement_stand_in=e.id in settlement_legs`; after `_write_coverage` (`:1023`) call `link_claims`; in live mode, before `_write_cases`, replace each `settlement_line` placeholder with the assignment's outcome:
  - lines in the graph = settlement lines of this statement's `all_lines` ∪ uncovered settlement lines of live neighbour statements with a posted date in `lo − 2 … hi + 2`; contestants = lines with active coverage (any kind, from `all_lines`) or claimed in this run;
  - `reserved_fps` / `reserved_ids` = fingerprints / item ids of `booked` rows;
  - booked → `settlement` case, candidates `[{"fingerprint", "settlement_date", "trade_date", "net_amount", "date_source", "items": [...]}]`, context `{"fingerprint", "source": account.settlement_source, "owner_deleted": bool}` (`owner_deleted` = an `owner_deleted` row with this fingerprint or this (account, settlement_date) exists);
  - ambiguous → `ambiguous` + `{"hint": "settlement_ambiguous"}`, candidates as above;
  - pending → `line_unmatched` + `{"hint": "settlement_pending", "reason": <reason|None>}`; a pending line with an unclaimed booked stand-in of the account within 2 days → `{"hint": "settlement_mismatch", "settlement_booking_id", "stand_in_date", "stand_in_amount"}` instead;
  - portfolio unavailable → every placeholder `settlement_pending` + `result.settlement_source_unavailable = True`.
- Drift (live mode, after `_write_coverage`): for every current line of `all_lines` whose active coverage entry is a leg of a `booked` settlement row (owned and matcher coverage alike), the feed row with that fingerprint's trade date recomputed; a different net or different `(source_id, amount)` set, or the row gone → a `settlement` case `{"hint": "source_drift", "booking_id", "booked": {...}, "now": {...}|None}` with no candidates, written through `_write_cases` with the line taken from `all_lines` (not `lines`). Nothing is rebooked.
- `ReconcileResult.settlement_source_unavailable: bool = False`, `ReconcileResult.settlements: dict[str, int]` (`booked`, `ambiguous`, `pending`, `mismatch`, `drift`, `claimed`); `ReconcileOut` and `SweepOut` (`settlement_source_unavailable: int` = count of statements) carry them.
- `reconciliation_hook`: after its reconcile of a statement whose account has `settlement_source`, set `needs_recheck` on the account's other live, non-retired statements whose period overlaps `period_start − 7 … period_end + 7`.
- `inbox_service.classify(..., settlement_labels=matching.SETTLEMENT_LABELS)`: `kind == "settlement"` → `settlement`; `kind == "ambiguous"` with hint `settlement_ambiguous` → `settlement`; the existing `line_unmatched` + label rule keeps its place; `kind == "dividend"` → `other`. `SETTLEMENT_LABELS` (`:30`) becomes `matching.SETTLEMENT_LABELS`; the caller passes the rules' labels. `_single_candidate` / `_listed_candidates` skip candidates without an integer `entry_id`.

`settlement_fixtures.py`: `bank_67(seed, db)` → (bank account with `settlement_source="portfolio:cathay"`, a target TWD account); `feed(*rows, fingerprint=None, amounts=None)` builds feed JSON from `(trade_date, settlement_date|None, net, ids[, overrides])` tuples (fictional amounts; `amounts` = per-item amounts, default: the whole net on the first item and 0 on the rest; `overrides` patch `bookable` / `reason` / `date_source`; the fingerprint defaults to `cathay:<trade_date>:<ids joined>`); fixture `portfolio_stub` (monkeypatches `portfolio_client.client_for` to a client over `httpx.MockTransport`; `.rows` list, `.down = True` → 503, `.calls` counter); `seed_stand_in(db, bank, target, *, settlement_date, net, fingerprint, ids)` (a `create_transfer` pair with `source="statement"` and a `booked` `origin = stand_in` row with items — the Task 9 job's shape, written directly); `bank_line(seq, day, merchant, flow)` → a `_line` of kind `withdrawal` / `deposit` by the flow's sign, printed so that `_flow` returns `flow`; `settle_line(seq, day, flow)` = `bank_line(seq, day, "交割股款", flow)`.

- [ ] **Step 1: Write the failing integration tests**

```python
def test_unique_settlement_writes_a_settlement_case_and_never_a_backfill(seed, db_session, run, portfolio_stub):
    bank, _ = bank_67(seed, db_session)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5", "ct:7")))
    sid = _submit(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")], kind="bank")
    (case,) = _cases(db_session, sid, "open")
    assert (case.kind, case.context["fingerprint"], case.candidates[0]["settlement_date"]) == ("settlement", portfolio_stub.rows[0]["fingerprint"], "2026-09-14")


def test_net_mismatch_stays_pending(seed, db_session, run, portfolio_stub):
    bank, _ = bank_67(seed, db_session)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12306", ("ct:5",)))
    sid = _submit(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")], kind="bank")
    (case,) = _cases(db_session, sid, "open")
    assert (case.kind, case.context["hint"]) == ("line_unmatched", "settlement_pending")


def test_portfolio_unavailable_reconciles_the_rest(seed, db_session, run, portfolio_stub):
    bank, _ = bank_67(seed, db_session); portfolio_stub.down = True
    sid = _submit(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305"), bank_line(2, date(2026, 9, 15), "ATM", "-1000")], kind="bank")
    result = _reconcile(db_session, sid)
    assert result.settlement_source_unavailable is True
    assert {(c.kind, (c.context or {}).get("hint")) for c in _cases(db_session, sid, "open")} >= {("line_unmatched", "settlement_pending")}


def test_owner_twin_claims_first_and_contests_its_settlement(seed, db_session, run, portfolio_stub):
    bank, target = bank_67(seed, db_session)
    _transfer(seed, bank, target, Decimal("12305"), date(2026, 9, 14))                         # the owner's own record
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    sid = _submit(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305"), settle_line(2, date(2026, 9, 15), "-12305")], kind="bank")
    kinds = {c.line_id: (c.kind, c.context.get("hint")) for c in _cases(db_session, sid, "open")}
    assert len(_active(db_session, sid)) == 1 and list(kinds.values()) == [("ambiguous", "settlement_ambiguous")]


def test_stand_in_is_claimed_on_its_exact_date_and_linked(seed, db_session, run, portfolio_stub):
    bank, target = bank_67(seed, db_session)
    booking = seed_stand_in(db_session, bank, target, settlement_date=date(2026, 9, 14), net="-12305", fingerprint="cathay:2026-09-10:aa", ids=("ct:5",))
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)), fingerprint="cathay:2026-09-10:aa")
    sid = _submit(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")], kind="bank")
    db_session.refresh(booking)
    assert _cases(db_session, sid, "open") == [] and booking.event_id is not None and booking.status == "booked"
    assert [c.match_rule for c in _active(db_session, sid)] == ["settlement_stand_in"]


def test_bank_line_on_another_date_is_a_mismatch_and_an_orphan(seed, db_session, run, portfolio_stub):
    bank, target = bank_67(seed, db_session)
    booking = seed_stand_in(db_session, bank, target, settlement_date=date(2026, 9, 14), net="-12305", fingerprint="cathay:2026-09-10:aa", ids=("ct:5",))
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)), fingerprint="cathay:2026-09-10:aa")
    sid = _submit(db_session, run, bank, [settle_line(1, date(2026, 9, 15), "-12305")], kind="bank")
    hints = {c.context.get("hint"): c for c in _cases(db_session, sid, "open")}
    assert hints["settlement_mismatch"].context["settlement_booking_id"] == booking.id
    assert hints["orphaned_stand_in"].context["settlement_booking_id"] == booking.id
    db_session.refresh(booking)
    assert (booking.status, booking.event_id) == ("booked", None)                              # nothing moved


def test_drift_on_a_claimed_stand_in_opens_source_drift(seed, db_session, run, portfolio_stub):
    bank, target = bank_67(seed, db_session)
    seed_stand_in(db_session, bank, target, settlement_date=date(2026, 9, 14), net="-12305", fingerprint="cathay:2026-09-10:aa", ids=("ct:5", "ct:7"))
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5", "ct:7")), fingerprint="cathay:2026-09-10:aa")
    sid = _submit(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")], kind="bank")
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5", "ct:7")), fingerprint="cathay:2026-09-10:aa", amounts=("-12299", "-6"))  # offsetting edit
    _reconcile(db_session, sid)
    (case,) = _cases(db_session, sid, "open")
    assert (case.kind, case.context["hint"]) == ("settlement", "source_drift") and len(_active(db_session, sid)) == 1


def test_owner_twin_retires_an_unclaimed_stand_in_at_reconcile(seed, db_session, run, portfolio_stub):
    bank, target = bank_67(seed, db_session)
    booking = seed_stand_in(db_session, bank, target, settlement_date=date(2026, 9, 14), net="-12305", fingerprint="cathay:2026-09-10:aa", ids=("ct:5",))
    _transfer(seed, bank, target, Decimal("12305"), date(2026, 9, 14))
    _submit(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")], kind="bank")
    db_session.refresh(booking)
    assert (booking.status, booking.reason, booking.out_entry_id) == ("retired", "twin", None)


def test_a_new_statement_flags_its_live_neighbour_for_recheck(seed, db_session, run, portfolio_stub):
    bank, _ = bank_67(seed, db_session)
    aug = _submit(db_session, run, bank, [settle_line(1, date(2026, 8, 31), "-5000")], kind="bank",
                  period=(date(2026, 8, 1), date(2026, 8, 31)))
    _submit(db_session, run, bank, [settle_line(1, date(2026, 9, 1), "-5000")], kind="bank")
    assert db_session.get(AccountStatement, aug).needs_recheck is True


def test_a_retired_or_historical_neighbours_line_is_not_in_the_graph(seed, db_session, run, portfolio_stub): ...
def test_neighbour_lines_at_the_window_edge_are_in_the_graph(seed, db_session, run, portfolio_stub): ...
```

The last two keep the rev 3 assertions (an August period and `statement_fixtures.SEP`; a `mode != "live"` or retired neighbour's equal line does not make the line ambiguous; a neighbour line at `period_end + 7` does). `test_inbox_service.py` (+2): a `settlement` case and an `ambiguous` + `settlement_ambiguous` case classify as `settlement`; a `settlement` case's fingerprint candidates do not raise in `_listed_candidates`.

- [ ] **Step 2: Run to verify failure**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/integration/test_settlement_reconcile.py tests/unit/test_inbox_service.py`
Expected: FAIL

- [ ] **Step 3: Implement `trade_settlement_service` (reconcile half), the `stand_ins` additions, the `reconcile` wiring, the revision-hook flagging, the result fields and the inbox mapping**

- [ ] **Step 4: Run tests**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/integration/test_settlement_reconcile.py tests/unit/test_inbox_service.py tests/integration/test_auto_pay_stand_ins.py tests/integration/test_reconciliation_api.py tests/integration/test_coverage_sweep.py`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add services/accounting-service/app/services/trade_settlement_service.py services/accounting-service/app/services/stand_ins.py services/accounting-service/app/services/reconciliation_service.py services/accounting-service/app/services/statement_revision_service.py services/accounting-service/app/schemas/statements.py services/accounting-service/app/routers/statements.py services/accounting-service/app/services/inbox_service.py services/accounting-service/tests/integration/settlement_fixtures.py services/accounting-service/tests/integration/test_settlement_reconcile.py services/accounting-service/tests/unit/test_inbox_service.py
git commit -m "accounting: reconcile assigns settlement lines globally, claims stand-ins on the exact date, flags drift and mismatches"
```

---

### Task 7: Accounting — `book_settlement` action, policy wiring (zero-charged, cap-exempt), revert

**Files:**
- Create: `services/accounting-service/app/services/reconciliation_actions/settlement_actions.py` (registered from `reconciliation_actions/__init__.py` like the other action modules)
- Modify: `services/accounting-service/app/services/reconciliation_actions/__init__.py:24-37,126-141` (`KIND_ACTIONS["settlement"] = ("book_settlement", "match", "dismiss")`, `"ambiguous"` += `book_settlement`; `CONDITIONS[("ambiguous", "book_settlement")]` = hint `settlement_ambiguous`)
- Modify: `services/accounting-service/app/services/stand_ins.py:23` (`OWNER_TWIN_GUARDED` += `book_settlement`)
- Modify: `services/accounting-service/app/services/policy_service.py` (`_settlement_candidate`; `POLICY_ACTIONS` `:353`; `OWNER_DELETED_GUARDED` `:351`; `collect_candidates` kinds `:398-400` += `settlement`; `CAP_EXEMPT`; `_applied_totals` `:465`; `_cap_reason` `:478`; `_amount_now` `:489`; `_prefilter` `:634`; `apply_candidate` `:657`)
- Modify: `services/accounting-service/app/services/settings_service.py` (`POLICY_DEFAULTS["actions"]["book_settlement"] = {"enabled": True, "max_amount": "0"}` `:118-131`; the `settlement` block: `SETTLEMENT_DEFAULTS`, `_check_settlement`, `read_settlement_settings`, wired into `update_reconciliation_settings` `:447` and `_reconciliation_dict` like `auto_pay` `:147`, `:327`)
- Modify: `services/accounting-service/app/schemas/statements.py:415-424` (`ReconciliationSettingsIn.settlement: dict = {}`, like `auto_pay`)
- Test: `services/accounting-service/tests/integration/test_auto_pay_settings.py` (+2: an unknown `settlement` key → 422; `stand_in_enabled: true` without `enabled_from` → 422), `services/accounting-service/tests/integration/test_settlement_actions.py` (helpers `_apply` / `_revert` / `_refuse` / `_code` / `_fresh` from `test_reconciliation_actions_ledger.py`; fixtures from `settlement_fixtures.py`)

**Interfaces:**
- Consumes: Task 3 models, Task 6 cases and `trade_settlement_service.fetch`; `_TwinGuarded` (`ledger_actions.py:132-169`: `lock_targets` = `LockSet(account_lock=True, barrier=True)`, `check_before_locks`, `_twin_check`); the `PairTransferAction.apply` / `invert` sequence (`transfer_actions.py:434-478`: `transfer_service.create_transfer(db, TransferIn(...), source=STATEMENT_SOURCE, remember=False)`, `transfer_legs`, claim, `_release` → `ews.delete_entries_cascade`); `engine._check_creation_family` (`engine.py:313`, slot `CREATE`).
- Produces: `BookSettlementParams(fingerprint: str)`; `BookSettlementAction(_TwinGuarded)`, `name = "book_settlement"`:
  - `prepare` / `apply`: the fingerprint must be one of the case's candidates; the settlement is re-fetched (`fetch` over the candidate's trade date) and must still equal the candidate (net, `(source_id, amount)` set, `bookable`), else `candidate_changed`; the statement must not be `needs_recheck` (`needs_recheck`); `_twin_check(line)`; a `booked` row for the same event → nothing new (replay); a `booked` row with this fingerprint for another event → `already_booked`; then the transfer (67 → target for a negative net, target → 67 for a positive one; `out_amount = |line.flow|`; dates = the line's posted date; name `證券交割`; description `證券交割 <trade_date>`; `needs_review = False`), the booking row (`kind settlement`, `origin bank`, `event_id`, `facts` = the candidate, `action_id`) and its items; the 67 leg claims the line (`match_rule = "book_settlement"`). An `IntegrityError` on `ux_settlement_booking_item_source` → `constituent_reserved`, on `ux_settlement_booking_fingerprint_booked` → `already_booked`, on `ux_settlement_booking_day_booked` → `day_booked` (the savepoint rolls back; the session stays usable).
  - `invert`: `_release` → `delete_entries_cascade` of both legs → the booking row `retired` (reason `revert`) with its items deleted → reopen as `line_unmatched` + `{"hint": "settlement_pending"}` with `candidates = []`; the engine's snapshot gate refuses a revert after either leg changed.
- Settings: `SETTLEMENT_DEFAULTS = {"stand_in_enabled": False, "enabled_from": None, "target_accounts": {}, "twin_window_days": 3, "dividend_category_id": None}`; `_check_settlement(block) -> dict` (`enabled_from` ISO date, required when `stand_in_enabled`; `target_accounts` maps an account id string to an account id, both existing, same currency; `twin_window_days` int 0–15; `dividend_category_id` int or None); `read_settlement_settings(db) -> dict` (block + settings `version`).
- Target account: `read_settlement_settings(db)["target_accounts"][str(statement.account_id)]`; missing → refused `no_target`.
- `settlement_fixtures.bank_67` gains a `target=True` default that writes `settlement.target_accounts = {str(bank.id): target.id}` through `update_reconciliation_settings` (Task 6 tests are unaffected).
- Policy: `CAP_EXEMPT = frozenset({"book_settlement", "book_dividend"})`; `_settlement_candidate(db, case, statement) -> Candidate | None` (open `settlement` case without a hint, `amount = Decimal("0.0000")`, params `{"fingerprint": case.context["fingerprint"]}`); `_cap_reason` returns None for `CAP_EXEMPT`; `_applied_totals` skips `CAP_EXEMPT` rows (count and amount); `_amount_now` returns `0.0000` for them; `apply_candidate` passes `budget=None` for them (no reservation, no charge, no `policy_budget` in `after`); `_prefilter` returns `("propose", None, "owner_deleted")` for a candidate whose case context has `owner_deleted: true`.

- [ ] **Step 1: Write the failing tests**

```python
def test_book_settlement_books_two_legs_one_owner_and_reverts(seed, db_session, run, portfolio_stub):
    bank, target = bank_67(seed, db_session)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5", "ct:7")), amounts=("-12300", "-5"))
    sid = _submit(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")], kind="bank")
    (case,) = _cases(db_session, sid, "open")
    row = _apply(db_session, case.id, "book_settlement", {"fingerprint": case.context["fingerprint"]})
    booking = db_session.scalar(select(SettlementBooking))
    legs = [db_session.get(LedgerEntry, i) for i in (booking.out_entry_id, booking.in_entry_id)]
    assert (booking.origin, booking.status, booking.event_id) == ("bank", "booked", case.event_id)
    assert [(l.account_id, l.amount) for l in legs] == [(bank.id, Decimal("-12305")), (target.id, Decimal("12305"))]
    assert sorted((i.source, i.source_id, i.amount) for i in booking.items) == [("ct", "ct:5", Decimal("-12300")), ("ct", "ct:7", Decimal("-5"))]
    _revert(db_session, row.id)
    db_session.refresh(booking)
    assert booking.status == "retired" and booking.items == [] and all(db_session.get(LedgerEntry, l.id) is None for l in legs)


def test_book_settlement_takes_the_account_lock_and_barrier():
    assert reconciliation_actions.ACTIONS["book_settlement"].lock_targets(None, None) == LockSet(account_lock=True, barrier=True)


def test_policy_books_settlements_uncapped_and_charges_nothing(seed, db_session, run, portfolio_stub, policy_on):
    policy_on(max_amount_everywhere="1", per_statement_count=0, per_statement_total="0", monthly_total="0")
    bank, _ = bank_67(seed, db_session)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-999999", ("ct:5",)))
    sid = _submit(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-999999")], kind="bank")
    policy_service.run_policy(db_session, sid)
    assert db_session.scalar(select(SettlementBooking.status)) == "booked"
    assert db_session.scalar(select(func.coalesce(func.sum(PolicyBudget.used_count), 0))) == 0
    _reconcile(db_session, sid); policy_service.run_policy(db_session, sid)                 # idempotent
    assert db_session.scalar(select(func.count(SettlementBooking.id))) == 1


def test_policy_proposes_an_owner_deleted_settlement(seed, db_session, run, portfolio_stub, policy_on): ...
def test_policy_refuses_when_an_owner_twin_appeared_under_the_barrier(seed, db_session, run, portfolio_stub, policy_on): ...
def test_another_event_with_the_same_fingerprint_is_already_booked(seed, db_session, run, portfolio_stub): ...
def test_owner_booking_from_an_ambiguous_case_reserves_the_constituents(seed, db_session, run, portfolio_stub): ...
def test_a_fingerprint_sharing_a_booked_constituent_is_refused(seed, db_session, run, portfolio_stub): ...
def test_revert_is_refused_when_either_leg_was_edited(seed, db_session, run, portfolio_stub, mutate): ...
def test_revert_after_reconcile_reopens_pending(seed, db_session, run, portfolio_stub): ...
def test_refused_while_the_statement_needs_recheck(seed, db_session, run, portfolio_stub): ...
```

Bodies of the stubbed tests (one assertion set each): owner-deleted — a row `owner_deleted` for the fingerprint, the policy run leaves the case open with one pending proposal whose rationale is `owner_deleted`; owner twin — an owner transfer inserted after `collect_candidates` and before `apply_candidate` → `_code == "candidate_changed"`, nothing booked; already booked — two lines in two statements, same fingerprint, the second apply → `already_booked`; ambiguous — the owner applies `book_settlement` with one candidate's fingerprint on an `ambiguous` case, the other line's next reconcile is `settlement_pending`; constituent — a booked `{ct:1, ct:2}` and a case candidate `{ct:2}` → `constituent_reserved` and `_fresh(db_session)` still queries; `mutate` parametrised over (edit the bank leg's amount, edit the target leg's date, add a child entry) → `_code == "snapshot_conflict"`; revert-after-reconcile — apply, reconcile (the claim is kept), revert → both legs gone, the case `line_unmatched` + `settlement_pending` with `candidates == []`; needs-recheck — `statement.needs_recheck = True` → `_code == "needs_recheck"`. `policy_on` is a local fixture writing the policy through `settings_service.update_reconciliation_settings`.

- [ ] **Step 2: Run to verify failure**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/integration/test_settlement_actions.py`
Expected: FAIL (`unknown action book_settlement`)

- [ ] **Step 3: Implement the action, registry entries, policy candidate and cap exemption, `POLICY_DEFAULTS`**

- [ ] **Step 4: Run tests**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/integration/test_settlement_actions.py tests/integration/test_reconciliation_actions_ledger.py tests/integration/test_reconciliation_policy.py tests/integration/test_auto_pay_settings.py`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add services/accounting-service/app/services/reconciliation_actions/settlement_actions.py services/accounting-service/app/services/reconciliation_actions/__init__.py services/accounting-service/app/services/stand_ins.py services/accounting-service/app/services/policy_service.py services/accounting-service/app/services/settings_service.py services/accounting-service/app/schemas/statements.py services/accounting-service/tests/integration/test_auto_pay_settings.py services/accounting-service/tests/integration/test_settlement_actions.py
git commit -m "accounting: book_settlement — owner-twin-guarded 67↔117 pair under one booking owner, zero-charged and cap-exempt"
```

---

### Task 8: Accounting — read-through valuation in the account read models (ledger balance untouched)

**Files:**
- Create: `services/accounting-service/app/services/valuation_service.py`
- Modify: `services/accounting-service/app/services/ledger_service.py:146-205` (`_account_rows`; `list_accounts` `:207` and `get_account` `:211` inherit)
- Modify: `services/accounting-service/app/schemas/ledger.py:44-50` (`AccountOut`)
- Test: `services/accounting-service/tests/integration/test_valuation_read.py` (the `client` fixture and headers of `test_accounts_api.py`; `seed.fx` `tests/conftest.py:487`)

**Interfaces:**
- Consumes: Task 4 `client_for`, `cached`, `PortfolioUnavailable`, `Valuation`.
- Produces: `valuation_service.valuation_for(db, account: Account, as_of: date) -> dict | None` (`{"value", "currency", "as_of", "priced_through", "complete", "missing", "stale"}`; 10-minute module cache keyed `(broker, as_of)`, `clear_cache()`; on `PortfolioUnavailable` the cached value with `stale: True`, else None; a broker currency other than the account's → None, logged by code). `AccountOut.balance: Decimal | None`, `balance_kind: Literal["ledger", "valuation"] = "ledger"`, `stale: bool = False`, `valuation: ValuationOut | None = None` (`value, currency, as_of, priced_through, complete, missing`). In `_account_rows`, for a `valuation_source` account: complete valuation → `balance = value`, `balance_main` by the same expression as `:195`; unavailable or incomplete → `balance = None`, `balance_main = None`; always `balance_kind = "valuation"`. Phase 1 serves TWD (= main currency) accounts only; a foreign valuation account (Phase 1b) reads None until its FX rule lands. `account_balance` (`:109`), `held_balance` (`reconciliation_service.py:719`), `period_summary` (`:731`), `_converted_totals` (`:607`), `entry_write_service:688`, `moze_backup_import_service:1248`: unchanged.

- [ ] **Step 1: Write the failing tests**

```python
def test_valuation_account_reads_the_portfolio_total_but_the_ledger_balance_stays(client, seed, db_session, valuation_stub):
    pos = seed.account("國泰持股", opening="0", valuation_source="portfolio:cathay")
    seed.entry(pos, "100000", kind="transfer_in", day=date(2026, 9, 1))
    valuation_stub.total = "123456"
    body = client.get(f"/accounts/{pos.id}").json()
    assert (body["balance"], body["balance_main"], body["balance_kind"], body["stale"]) == ("123456.0000", "123456.0000", "valuation", False)
    assert ledger_service.account_balance(db_session, pos.id) == Decimal("100000")


def test_historical_as_of_asks_for_that_date(client, seed, db_session, valuation_stub): ...      # valuation_stub.seen == [("cathay", "2026-09-12")]
def test_cold_cache_with_portfolio_down_is_null_and_stale(client, seed, db_session, valuation_stub): ...  # balance None, stale True, valuation None
def test_incomplete_valuation_is_never_a_balance(client, seed, db_session, valuation_stub): ...  # balance None, valuation.missing == ["9998"]
def test_ledger_only_readers_ignore_valuation(seed, db_session, valuation_stub): ...  # period_summary(...)["end_balance"] == Decimal("100000")
```

`valuation_stub`: module fixture monkeypatching `portfolio_client.client_for` (fields `total`, `complete`, `missing`, `down`, `seen`) and calling `valuation_service.clear_cache()`.

- [ ] **Step 2: Run to verify failure** — `cd services/accounting-service && .venv/bin/python -m pytest -q tests/integration/test_valuation_read.py` → FAIL (`balance_kind` missing)
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run** `cd services/accounting-service && .venv/bin/python -m pytest -q tests/integration/test_valuation_read.py tests/integration/test_accounts_api.py tests/unit/test_ledger_today.py` → all pass
- [ ] **Step 5: Commit**

```bash
git add services/accounting-service/app/services/valuation_service.py services/accounting-service/app/services/ledger_service.py services/accounting-service/app/schemas/ledger.py services/accounting-service/tests/integration/test_valuation_read.py
git commit -m "accounting: read-through valuation for valuation_source accounts in the account read models"
```

---

### Task 9: Accounting — daily settlement stand-ins (owner decision Q7)

**Files:**
- Modify: `services/accounting-service/app/services/trade_settlement_service.py` (job half)
- Test: `services/accounting-service/tests/integration/test_settlement_stand_ins.py`

**Interfaces:**
- Consumes: Task 3 models, Task 4 client, Task 6 `fetch` / `find_twins` / `retire_pair`; `transfer_service.create_transfer` (`transfer_service.py:139`) and `transfer_legs` (`:164`); `take_import_key_shared`; the `auto_pay_job.run` shape (`auto_pay_job.py:206`: a session-level `pg_try_advisory_lock`, one transaction per candidate, `ImportRunningError` → stop with `import_running`).
- Consumes also: Task 7 `read_settlement_settings` (the `settlement` block).
- Produces in `trade_settlement_service`: `SETTLEMENT_JOB_LOCK_KEY` (a new advisory key, distinct from `AUTO_PAY_JOB_LOCK_KEY`); `@dataclass(frozen=True) StandInCandidate(account_id, target_account_id, settlement: Settlement)`; `stand_in_candidates(db, rows: list[Settlement], account: Account, settings: dict, today: date) -> list[StandInCandidate]`; `gate(db, cand, settings) -> str | None` (in order: `disabled`; a row for (account, settlement_date) with status `booked`, `owner_deleted`, `owner_edited`, or `retired`/`skipped` with reason `twin` → `final`; a reserved constituent → `reserved`; an owner twin → `twin`); `book_stand_in(db, cand, settings) -> SettlementBooking` (the transfer of Task 7 dated the settlement date, `origin stand_in`, `event_id None`, items, audit `settlement_booked`); `record_skip(db, cand, reason) -> SettlementBooking` (one `skipped` row per (account, settlement_date, reason), audit `settlement_skipped` on change); `check_row(db, row, feed: dict[str, Settlement], settings) -> str | None` (in order: both legs gone → `owner_deleted`; one leg gone or not `source='statement'` → `owner_edited`; an owner twin → `retire_pair(reason="twin")` → `retired`; unclaimed and the feed row of its trade date gone or no longer `bookable` → `retire_pair(reason=<feed reason or "gone">)`; unclaimed and the feed row's net / date / items differ from `facts` → `replace(db, row, new)` → `replaced`; claimed → None (drift belongs to reconcile)); `replace(db, row, settlement) -> SettlementBooking` (retire the pair with status `replaced`, then `book_stand_in` for the new figures, both in one transaction, audit `settlement_replaced`); `run_stand_ins(engine, *, today: date | None = None, dry_run: bool = False) -> dict` (report `{"status": completed|busy|disabled|source_unavailable|import_running|dry_run, "booked": [...], "skipped": [...], "housekeeping": {...}, "touched_dates": {account_id: [dates]}}`; for each `settlement_source` account with a target: fetch trade dates `max(enabled_from − 10, min(today − 14, oldest unclaimed stand-in's trade_date)) … today`, housekeeping over its `booked` stand-ins first, then the candidates; `PortfolioUnavailable` → `source_unavailable`, nothing written; a dry run takes no import key and rolls every transaction back).

- [ ] **Step 1: Write the failing tests**

```python
# tss = app.services.trade_settlement_service
def test_job_books_once_per_settlement_day(pg_engine, seed, db_session, portfolio_stub, stand_ins_on):
    bank, target = bank_67(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-01")
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    first = tss.run_stand_ins(pg_engine, today=date(2026, 9, 14))
    again = tss.run_stand_ins(pg_engine, today=date(2026, 9, 15))
    (row,) = db_session.scalars(select(SettlementBooking)).all()
    assert (len(first["booked"]), again["booked"], row.origin, row.settlement_date) == (1, [], "stand_in", date(2026, 9, 14))
    out = db_session.get(LedgerEntry, row.out_entry_id)
    assert (out.account_id, out.amount, out.entry_date, out.source) == (bank.id, Decimal("-12305"), date(2026, 9, 14), "statement")


def test_job_skips_future_margin_csv_only_and_pre_go_live_days(pg_engine, seed, db_session, portfolio_stub, stand_ins_on):
    bank, target = bank_67(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-10")
    portfolio_stub.rows = feed(("2026-09-04", "2026-09-08", "-1000", ("ct:1",)),                          # before go-live
                               ("2026-09-11", "2026-09-15", "-2000", ("ct:2",), {"bookable": False, "reason": "margin_day"}),
                               ("2026-09-11", None, "-3000", ("ct:3",), {"date_source": "legs"}),       # CSV-only
                               ("2026-09-14", "2026-09-16", "-4000", ("ct:4",)))                        # settles tomorrow
    assert tss.run_stand_ins(pg_engine, today=date(2026, 9, 15))["booked"] == []


def test_owner_twin_first_skips_the_stand_in(pg_engine, seed, db_session, portfolio_stub, stand_ins_on): ...   # skipped/twin, no legs
def test_reposted_statement_replaces_only_an_unclaimed_stand_in(pg_engine, seed, db_session, run, portfolio_stub, stand_ins_on): ...
def test_feed_row_no_longer_bookable_retires_an_unclaimed_stand_in(pg_engine, seed, db_session, portfolio_stub, stand_ins_on): ...
def test_owner_deleted_stand_in_is_never_rebooked(pg_engine, seed, db_session, run, portfolio_stub, stand_ins_on): ...
def test_owner_edited_leg_is_left_alone(pg_engine, seed, db_session, portfolio_stub, stand_ins_on): ...
def test_job_with_portfolio_down_changes_nothing(pg_engine, seed, db_session, portfolio_stub, stand_ins_on): ...
def test_dry_run_writes_nothing(pg_engine, seed, db_session, portfolio_stub, stand_ins_on): ...
def test_stand_ins_are_outside_the_policy_caps(pg_engine, seed, db_session, portfolio_stub, stand_ins_on, policy_on): ...
```

Assertions of the stubbed tests: owner twin — an owner transfer 67 → 117 of 12305 on 09-13 → one `skipped` row reason `twin`, no ledger rows, a second run adds no row; re-post — booked stand-in, the feed row's net changes (a held day imported later) → status `replaced` on the old row, a new `booked` row with the new net, one pair of legs left; then a bank statement claims the new one (`_submit` with `settle_line` on the settlement date), the feed changes again → the job does nothing (`check_row` None) and the next `_reconcile` opens `source_drift`; not bookable — feed row turns `legs_disagree` → `retired` reason `legs_disagree`, legs deleted; owner-deleted — the owner deletes the transfer (`transfer_service` delete path) → `owner_deleted`; the next run books nothing for that day; then the bank statement arrives → a `settlement` case with context `owner_deleted: true` and, after `run_policy`, one pending proposal and no booking; owner-edited — the owner edits the bank leg's description through `PUT /transfers` (source no longer `statement`) → `owner_edited`, legs kept; portfolio down → status `source_unavailable`, no rows; dry run → `status dry_run`, `booked` lists the candidate, no rows; caps — policy `monthly_total "0"` and `per_statement_count 0` → the stand-in still books and `PolicyBudget` stays empty. `stand_ins_on` writes the `settlement` block through `update_reconciliation_settings`; the job tests pass `pg_engine` as `tests/integration/test_auto_pay_job.py:68` does.


- [ ] **Step 2: Run to verify failure**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/integration/test_settlement_stand_ins.py`
Expected: FAIL

- [ ] **Step 3: Implement the job half of `trade_settlement_service`**

- [ ] **Step 4: Run tests**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/integration/test_settlement_stand_ins.py tests/integration/test_settlement_reconcile.py tests/integration/test_auto_pay_settings.py tests/integration/test_auto_pay_job.py`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add services/accounting-service/app/services/trade_settlement_service.py services/accounting-service/tests/integration/test_settlement_stand_ins.py
git commit -m "accounting: daily settlement stand-ins from the 國泰 daily statement, replaceable until a bank line claims them"
```

---

### Task 10: Re-reconcile triggers — `POST /reconciliation/settlements/run`, the sweep re-check, the worker call

**Files:**
- Modify: `services/accounting-service/app/routers/statements.py` (new route next to the sweep `:293`; the sweep runs the job first and adds the re-check ids)
- Modify: `services/accounting-service/app/services/trade_settlement_service.py` (`recheck_statement_ids`, `flag_for_recheck`)
- Modify: `services/accounting-service/app/schemas/statements.py` (`SettlementRunIn`, `SettlementRunOut`; `SweepOut.stand_ins: dict`)
- Modify: `services/accounting-service/worker/cathay.py:296-330` (`run(..., homehub: ApiClient | None = None)`; after a plain, unlatched commit run)
- Modify: `services/accounting-service/worker/cli.py:64-76` (`_cathay` builds an `httpx.Client(base_url=cfg.api_url)` and `ApiClient(client, cfg.api_token_file.read_text().strip())`, as `worker/pipeline.py:102-109`)
- Test: `services/accounting-service/tests/integration/test_reconciliation_api.py` (+3), `services/accounting-service/tests/worker/test_cathay_job.py` (+3, the `World` fixture)

**Interfaces:**
- Consumes: Task 9 `run_stand_ins`; `reconciliation_service.pending_statement_ids` (`:1067`), `reconcile_each` (`:1076`); `worker.api.ApiClient` (`worker/api.py:50`).
- Produces: `trade_settlement_service.recheck_statement_ids(db) -> list[int]` (live, non-retired statements of `settlement_source` accounts holding an open case with hint `settlement_pending`, `settlement_ambiguous` or `settlement_mismatch`); `flag_for_recheck(db, report: dict) -> int` (sets `needs_recheck` on the live, non-retired statements whose period holds a `touched_dates` day, plus `recheck_statement_ids`; returns the count). Route `POST /reconciliation/settlements/run` (`dependencies=INGEST`, body `SettlementRunIn{source: Literal["portfolio:cathay"]}`, response `SettlementRunOut{status: str, booked: int, skipped: int, housekeeping: dict[str, int], flagged: int}`; counts and codes only). The sweep: `run_stand_ins` + `flag_for_recheck` before `pending_statement_ids`, then reconciles `sorted(set(pending) | set(recheck_statement_ids(db)))`; `SweepOut.stand_ins` = the counts. Worker: `cathay.run` calls `homehub._call("POST", "/reconciliation/settlements/run", json={"source": "portfolio:cathay"}, timeout=cfg.cathay_timeout_s)` after a plain commit run that is not latched; any exception → `summary["settlement_run"] = "failed:<ExceptionClass>"`, the run's exit status unchanged; success → `summary["settlement_run"] = <status>`.

- [ ] **Step 1: Write the failing tests**

```python
def test_settlement_run_books_flags_and_reports_counts_only(client, seed, db_session, run, portfolio_stub, stand_ins_on): ...
    # a live Sept statement with a settlement_pending line; feed row now present → response {"status": "completed", "booked": 1, "flagged": 1, ...}; the statement needs_recheck; no amount in the body
def test_settlement_run_needs_the_ingest_scope(client): ...                               # a write-only token → 403
def test_sweep_rechecks_statements_with_open_settlement_cases(client, seed, db_session, run, portfolio_stub, lease): ...  # `lease`: as test_owner_rules.py:259 obtains it
    # the line was pending (feed empty); the feed row appears; POST /reconciliation/sweep → the case is now `settlement`
```

```python
def test_plain_commit_run_calls_homehub_after_posting(world):
    world.add_statement("2026-09-10"); summary = world.run()
    assert world.homehub_calls == [("POST", "/reconciliation/settlements/run", {"source": "portfolio:cathay"})] and summary["settlement_run"] == "completed"


def test_dry_run_latched_and_file_runs_do_not_call_homehub(world): ...                      # three runs, homehub_calls == []
def test_homehub_failure_never_changes_the_run_result(world): ...                            # homehub raises ApiError(503) → exit codes equal, summary["settlement_run"] == "failed:ApiError"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/integration/test_reconciliation_api.py -k settlement tests/worker/test_cathay_job.py -k homehub`
Expected: FAIL

- [ ] **Step 3: Implement the route, the sweep change, the worker call and the CLI wiring**

- [ ] **Step 4: Run tests**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/integration/test_reconciliation_api.py tests/integration/test_coverage_sweep.py tests/worker/test_cathay_job.py tests/worker/test_cli_config.py`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add services/accounting-service/app/routers/statements.py services/accounting-service/app/services/trade_settlement_service.py services/accounting-service/app/schemas/statements.py services/accounting-service/worker/cathay.py services/accounting-service/worker/cli.py services/accounting-service/tests/integration/test_reconciliation_api.py services/accounting-service/tests/worker/test_cathay_job.py
git commit -m "accounting: settlement re-reconcile triggers — worker call after each 國泰 run, sweep re-check of open settlement cases"
```

---

### Task 11: Docs, dry-run listing, rollout

**Files:**
- Modify: `services/accounting-service/scripts/r6_dryrun.py` (`--settlements`) and `services/accounting-service/scripts/README-r6-dryrun.md`
- Modify: `services/accounting-service/README.md` (rules keys `settlement_labels` / `settlement_window_days` / `settlement_sum_matching`; the `settlement` settings block; policy action `book_settlement`; case kinds and hints `settlement_pending` / `settlement_ambiguous` / `settlement_mismatch` / `source_drift` / `orphaned_stand_in` + `settlement_booking_id`; `balance_kind` / `valuation` / nullable `balance`; `POST /reconciliation/settlements/run`; env `PORTFOLIO_BASE_URL`, optional `PORTFOLIO_READ_TOKEN`)
- Modify: `services/stock-portfolio-service/README.md` (`/api/portfolio/ledger-feed/*`; the persisted statement facts; `CASH_LEG_ENABLED=true` is a precondition)
- Modify: `services/accounting-service/deploy/statements/README.md` (rollout section below)
- Test: `services/accounting-service/tests/unit/test_r6_dryrun.py` (+1, in the style of its `_dump` helper)

**Interfaces:**
- Consumes: the `ReconcileResult.settlements` counts and the case kinds/hints of Tasks 6–9.
- Produces: `r6_dryrun.py --settlements` — from the candidate dump, one row per settlement line of every live statement of a `settlement_source` account: `statement_id, event_id, outcome ∈ {stand_in_claim, owner_match, book_settlement, pending, ambiguous, mismatch, drift}`, the cases it would supersede or dismiss, and every stand-in the job would book (dates and booking ids only, no amounts, no merchants, mode 600 under `~/reports`).

- [ ] **Step 1: Write the failing dry-run test** (`test_settlements_listing_lists_one_row_per_settlement_line`: a two-statement synthetic dump → two rows with the expected outcomes and no `flow` / `merchant` key anywhere in the output)
- [ ] **Step 2: Run it** — `cd services/accounting-service && .venv/bin/python -m pytest -q tests/unit/test_r6_dryrun.py -k settlements` → FAIL
- [ ] **Step 3: Implement `--settlements`; write the READMEs.** Rollout section, in order:
  1. Deploy the portfolio release (migration `e9settle`), confirm `CASH_LEG_ENABLED=true` in its env.
  2. Deploy the accounting API (migration `d5e1f7a3b9c2`, `PORTFOLIO_BASE_URL` in its env if not the default) and the worker checkout.
  3. Owner SQL: `UPDATE account SET settlement_source = 'portfolio:cathay' WHERE id = 67;` (`valuation_source` on 117 waits for the SPA follow-up that renders `balance: null` as 「市值暫不可得」 — rollout gate).
  4. Settings PUT (whole document, current values kept): `policy.actions.book_settlement.enabled = false` for the dry run; `settlement = {"stand_in_enabled": false, "enabled_from": null, "target_accounts": {"67": 117}, "twin_window_days": 3}`.
  5. Dry run (`r6_case_dump.py` baseline / candidate + `r6_dryrun.py --settlements`); the owner reviews it case by case, one case per question (ruling Q5); counts from 2026-10-19 are not expected to hold.
  6. Enable: `book_settlement.enabled = true`; `settlement.stand_in_enabled = true`, `enabled_from` = the go-live date (no stand-ins for days settled before it); reconcile 67's live statements; check the first stand-in on the next settlement day.
- [ ] **Step 4: Run** `cd services/accounting-service && .venv/bin/python -m pytest -q tests/unit/test_r6_dryrun.py && git diff --check` → pass; `grep -c "book_settlement\|settlement_mismatch\|settlements/run" README.md` ≥ 3
- [ ] **Step 5: Commit**

```bash
git add services/accounting-service/scripts/r6_dryrun.py services/accounting-service/scripts/README-r6-dryrun.md services/accounting-service/tests/unit/test_r6_dryrun.py services/accounting-service/README.md services/stock-portfolio-service/README.md services/accounting-service/deploy/statements/README.md
git commit -m "docs: portfolio integration phase 1 — settings, routes, settlement dry-run listing, rollout"
```

---

## Dividends (Tasks 12–13, separable)

Owner ruling Q6 (a); spec §4.6. Tasks 1–11 ship without these; the group may become its own PR or follow-up plan (controller's call). Both reuse the Task 3 tables (`kind = 'dividend'`, items `source = 'div'`) and the Task 7 cap exemption.

### Task 12: Portfolio `GET /api/portfolio/ledger-feed/dividends` + client method

**Files:**
- Modify: `services/stock-portfolio-service/app/services/ledger_feed_service.py`, `app/routers/ledger_feed.py`, `app/schemas/ledger_feed.py`
- Modify: `services/accounting-service/app/services/portfolio_client.py`
- Test: `services/stock-portfolio-service/tests/integration/test_ledger_feed.py` (+2), `services/accounting-service/tests/unit/test_portfolio_client.py` (+1)

**Interfaces:**
- Produces: `ledger_feed_service.dividends(db, broker: str, date_from: date, date_to: date) -> list[dict]` — `dividends` rows with `receipt_status == "confirmed"`, `receipt_account_id` = `resolve_default_cathay_twd_account(db).id`, `receipt_date` in range: `{"id", "symbol", "receipt_date", "amount" (net, as stored), "currency", "fingerprint": "dividend:<id>"}`; client `Dividend(id: int, symbol: str, receipt_date: date, amount: Decimal, currency: str, fingerprint: str)` and `PortfolioClient.dividends(broker, date_from, date_to) -> list[Dividend]`.

- [ ] **Step 1: Failing tests** — `test_dividends_lists_confirmed_receipts_on_the_cathay_account_net` (a confirmed receipt with `amount 990`, `fee 10`: `amount == "990.0000"`, never `980`; a `pending` row and a receipt on another account are absent); `test_dividends_range_bounds_the_receipt_date`; client parse test.
- [ ] **Step 2: Run** `cd services/stock-portfolio-service && .venv/bin/python -m pytest -q tests/integration/test_ledger_feed.py -k dividends` → FAIL
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run** both services' touched test files → pass
- [ ] **Step 5: Commit** `git commit -m "portfolio+accounting: dividend receipts in the ledger feed"`

### Task 13: Accounting — `dividend` candidates and `book_dividend`

**Files:**
- Modify: `services/accounting-service/app/services/statements/matching.py` (`Rules.dividend_labels: tuple[str, ...] = ("股利", "股息", "現金股利")`)
- Modify: `services/accounting-service/app/services/reconciliation_service.py` (after the settlement step: an unclaimed `deposit` line with a dividend label and exactly one unreserved receipt with `amount == line.flow` and `|receipt_date − posted_date| <= 3` → `dividend` case; several → `ambiguous` + `dividend_ambiguous`; none → the line's ordinary case)
- Modify: `services/accounting-service/app/services/reconciliation_actions/settlement_actions.py` (`BookDividendAction(_TwinGuarded)`, `name = "book_dividend"`), `reconciliation_actions/__init__.py` (`KIND_ACTIONS["dividend"] = ("book_dividend", "match", "dismiss")`), `stand_ins.py` (`OWNER_TWIN_GUARDED` += `book_dividend`), `policy_service.py` (`_dividend_candidate`, `POLICY_ACTIONS`, `OWNER_DELETED_GUARDED`, `collect_candidates` kinds += `dividend`), `settings_service.py` (`POLICY_DEFAULTS["actions"]["book_dividend"] = {"enabled": True, "max_amount": "0"}`)
- Test: `services/accounting-service/tests/integration/test_dividend_actions.py`

**Interfaces:**
- Consumes: Task 12 `Dividend`; Task 7 `CAP_EXEMPT`, booking helpers; `settlement.dividend_category_id` (Task 9).
- Produces: `book_dividend` books one `income` entry on the bank account through `_Creation._book` (category = `dividend_category_id`, `needs_review = True` when it is None), a `settlement_booking` row (`kind dividend`, `origin bank`, `target_account_id None`, `in_entry_id None`, fingerprint `dividend:<id>`) and one item (`div`, `<id>`); revert = `_Creation.invert` + the row `retired` with its items deleted.

- [ ] **Step 1: Failing tests** — `test_book_dividend_books_income_idempotently_and_reverts` (apply → one income entry of the line's flow, row + item; reconcile again → no second case; revert → entry gone, row `retired`); `test_dividend_amount_must_equal_the_line_exactly` (a receipt 1 TWD off → no `dividend` case); `test_owner_income_entry_claims_first`; `test_policy_books_dividends_cap_exempt`.
- [ ] **Step 2: Run** `cd services/accounting-service && .venv/bin/python -m pytest -q tests/integration/test_dividend_actions.py` → FAIL
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run** `tests/integration/test_dividend_actions.py tests/integration/test_settlement_actions.py tests/integration/test_reconciliation_policy.py` → pass
- [ ] **Step 5: Commit** `git commit -m "accounting: book_dividend — confirmed 國泰 dividend receipts booked as income on the bank line, cap-exempt"`

---

## Self-review notes (plan author, 2026-10-30)

- Spec coverage: §4.1 → Tasks 1–2; §4.2 → Task 3 + Task 11 owner SQL; §4.3 → Tasks 5–6; §4.4 → Tasks 3, 6 (drift), 7; §4.5 → Task 6 (revision hook) + Task 10; §4.6 → Tasks 12–13; §4.7 → Task 3 (table), 5 (claim), 6 (claim link, mismatch, orphan, twin retirement), 9 (job), 10 (triggers); §5 → Tasks 2 + 8; §8 → Tasks 3, 5, 7, 9; §9 → Task 5 (sum gate), 6 (unavailable, drift, mismatch), 7 (unique races, revert), 9 (owner delete, job unavailable); §10 → the named tests; §11 step 1 → Task 11. §6 (wires, `/funding`) and §7 (Flex) are the Phase 1b / Phase 2 plans.
- Q7 changed: Task 3 (`origin`, `status` set, partial uniques, no `ledger_entry` column), Task 5 (stand-in claim, `settlement_stand_in` flag), Task 6 (claim link, `settlement_mismatch`, settlement orphans, twin retirement, drift over stand-in claims), Task 7 (`owner_deleted` → propose), Task 9 (new), Task 10 (the worker call runs after every plain run, so a settlement day reached that morning is seen), Task 11 (`enabled_from`, stand-ins in the dry-run listing), Review Focus 1–3.
- Type consistency: `Settlement` fields (`trade_date, settlement_date, date_source, net_amount, legs_net, currency, bookable, reason, items, fingerprint`) are the same in Tasks 4–10; case candidate keys (`fingerprint, settlement_date, trade_date, net_amount, date_source, items`) identical in Tasks 6–7; `settlement_booking_id` is the context key in Task 6 orphans/mismatch and Task 11 docs; `trade_settlement_service` functions named in Task 6 are the ones Tasks 7, 9, 10 consume.
- Known gaps for the implementer: the transfer delete path used by the owner-deleted test (Task 9) is the one `test_auto_pay_stand_ins.py` uses for auto-pay legs; the `lease` helper of Task 10 is copied from `test_owner_rules.py`.
