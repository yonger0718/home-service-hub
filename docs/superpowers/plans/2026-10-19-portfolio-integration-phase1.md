# Portfolio integration — Phase 1 (國泰 settlement match + daily stand-ins, 117 read-through valuation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bank 交割股款 lines on account 67 are explained from the portfolio's 國泰 settlements and booked as 67↔117 transfers under one booking owner; a settlement the 國泰 daily statement attests is visible in the ledger on its settlement day as a stand-in that the monthly bank line later claims or contradicts; account 117 shows the portfolio's market value without valuation entries. Dividends (spec §4.6) are the follow-up plan `docs/superpowers/plans/2026-10-30-portfolio-integration-dividends.md`.

**Architecture:** The stock-portfolio-service persists the 國泰 daily statement's settlement facts and serves a read-only ledger feed (`/api/portfolio/ledger-feed/settlements`, `/valuation`, later `/dividends`). The accounting-service gains `account.settlement_source` / `valuation_source`, a `settlement_booking` owner table shaped like `statement_auto_pay`, a `portfolio_client`, a global settlement assignment step inside `reconcile`, a `book_settlement` action on the `_TwinGuarded` / `PairTransferAction` patterns, a daily stand-in job modelled on `auto_pay_service`, and read-through valuation in the account read models only. Nothing synchronises: every write carries portfolio ids and source facts; re-runs are no-ops, replacements while unclaimed, or `source_drift` cases.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2, Alembic (both services), httpx 0.28.1 (accounting → portfolio, worker → accounting), pytest (accounting: `tests/unit`, `tests/integration` on PostgreSQL, `tests/worker`; portfolio: `tests/unit`, `tests/integration` on SQLite `create_all`), PostgreSQL.

**Spec:** `docs/superpowers/specs/2026-10-19-portfolio-integration-design.md` rev 4.3 (2026-10-30; owner rulings Q1–Q7 in `.superpowers/r7-refresh/owner-rulings.md`, errata from `.superpowers/r7-refresh/drift-audit.md`).

**Plan rev 4.5 (2026-10-30, pre-flight conflict scan `.superpowers/sdd/2026-10-19-portfolio-integration-phase1/preflight-scan.md`, controller rulings; applied findings in `preflight-rulings-applied.md` next to it):** B1/B2 existing head assertions (`test_receipt_migration.py:118`, `test_migration.py:316/352/422`) updated in Tasks 1 and 3; B3/B4/B6 Task 6 test data fixed; B5 forced-case `already_booked`; B7 a line covered by a settlement booking leg leaves the contestant graph (spec §4.3 amended, Tasks 5–6); C1 every prose-only test got a body; C2/C3 vacuous assertions replaced; C4 shared `pair_locks.lock_row_and_legs` + `stand_ins.unclaimed_settlement_legs`, the rest of the auto-pay machinery deliberately re-implemented; C5 `Feed.complete` kept and documented; C6 task text is the spec for Step 3; C8/D1 `settlement_bank` / `settlement_target`; D2 `held_balance` assertion; D3/D4 constraints reworded; D6 `nearest`; D7 admin `PATCH /accounts/{id}`; D8 `settlement_mismatch` only for candidate-less pending lines; M1–M17 (M7 follows the spec: `skipped` rows are never final). Spec rev 4.5: §4.3, §5, §9, §10.

**Plan rev 4.4 (2026-10-30, Multica AGENT-360 re-check on `49aba8ca`: PASS-with-minor, four minors folded):** the engine's lock order quoted precisely (statements → case → account row + barrier → entries NOWAIT; account-row takers hold no entry/group lock) and the revert's later booking-row lock stays NOWAIT because `lock_rows` (engine.py:482) already holds the legs (Task 6); the trade-axis feed test seeds its own data (Task 2); `trade_bounds` defines the calendar-expiry branch → `PortfolioUnavailable("calendar")`, lines pending (Tasks 5, 6); the job looks an existing stand-in up by (source, trade_date), the stored fingerprint is provenance only, a fingerprint change on the same trade day is a replacement, never "gone" (Task 9).

**Plan rev 4.3 (2026-10-30, internal re-verify 2 of rev 4.2, before the external re-check):** the feed's trade axis returns every row of a trade date (facts or not) so `fetch_by_trade_date` finds a normal statement day and `candidate_changed` fires only on a real change (Tasks 2, 6, 7); the stand-in lifecycle takes no account row lock — booking row → legs NOWAIT, the auto-pay order, with the engine_locks invariant and the two cycles as the reason (Tasks 6, 9); the claim-vs-replace test exercises the real race through a stale unlocked read; 春節 bound 02-12; `housekeeping` report has fixed zero-filled keys; Review Focus renumbered; dividend pre-provisions named.

**Plan rev 4.2 (2026-10-30, after Multica review AGENT-360, FAIL on the stand-in lifecycle):** owner-delete / edit housekeeping for both booking origins runs in `reconcile` before the reservation read (Task 6); one lock protocol for the job, `reconcile`, `book_settlement` and its revert — the job re-reads the claim under row + leg locks and never retires or replaces a claimed pair (Tasks 6, 9, two-session test); `fetch_by_settlement_window` / `fetch_by_trade_date` split, the action re-verifies by trade date + fingerprint (Tasks 6, 7, 9); every live neighbour line, covered or not, is a contestant (Task 6); replacement re-runs the candidate rules and gates on the new facts (Task 9); claimed-row drift covers settlement date, `bookable` and `date_source` (Task 6); the feed is queried on two axes with a trade margin from `business_days` and echoes its bounds — no fixed calendar margin, an incomplete answer assigns nothing (Tasks 2, 4, 5, 6, 9); `unknown_dates` travels fetch → assign → result (Tasks 4–6); Tasks 12–13 moved to the dividends follow-up plan.

**Plan rev 4.1 (2026-10-30, fix wave 1 after `.superpowers/r7-refresh/verify-rev4.md`):** `{"rows": [...]}` envelope everywhere; the reconcile fetch is on the settlement-date axis (trade-date query widened by 15 days, then filtered) with a 春節 test; `find_twins` takes explicit fields; a dismissed case or a reverted / owner-deleted bank booking is final for the stand-in job (controller ruling, owner to confirm); zero-net rows are omitted from the feed and the job; `assign` filters non-settlement lines; every test has a body; `_submit` is unpacked through `submit_bank`.

**Plan rev 4 (2026-10-30, refreshed against main `7b3969f4` and the owner rulings of 2026-10-30):** the trading-calendar task is replaced by persisted statement facts + a business-day fallback (Q1); the feed is rewritten on the real modules, `/api/portfolio` prefix, no auth, `TW_CATHAY_FILTER` scope (Q2) and the statement's cash net; `book_settlement` is rebuilt on `_TwinGuarded` + `PairTransferAction`, zero-charged and outside every cap (Q4), owner twins first (Q5); drift runs over claimed lines (booked lines are owned coverage and never reach `line_rows`); the portfolio call-back is replaced by the worker's call after each 國泰 run + the sweep re-check + the monthly statement's own reconcile (Q3); **owner decision Q7 adds the daily settlement stand-ins (Tasks 3, 5, 6, 9, 10, 11 and the Review Focus)**; `/funding` moves to Phase 1b; dividends are the separated last group (Tasks 12–13, Q6 formula `amount == flow`); the rollout starts with a case-by-case dry-run listing. Rev 3 / rev 2 changelogs: git history of this file (`7b3969f4`).

## Global Constraints

- Amounts never appear in log lines, exception text, run summaries, audit `reason` codes or latch files; messages carry ids, dates, counts and codes only. Audit `detail` may carry amounts (existing auto-pay precedent).
- Docs, fixtures and tests use fictional amounts and synthetic statement text only; account ids `67` / `117` appear only as the owner SQL / settings example values they already are in the spec.
- The portfolio has no auth until issue #117: the accounting client sends `Authorization: Bearer <PORTFOLIO_READ_TOKEN>` only when that env var is set; tokens never reach logs, exception text, request URLs or API bodies.
- `ledger_service.account_balance` keeps its contract (opening + Σ posted entries); valuation replaces the balance only in `list_accounts` / `get_account` (via `_account_rows`). `reconciliation_service.held_balance` (`:719`) and `period_summary`'s `end_balance` (`ledger_service.py:763`) are unaffected and tested so.
- Settlement lines are never candidates of `backfill_entry` / `create_system_entry` and never meet the near / drift / foreign / pair-transfer / bank-only matcher rules (spec §4.3); an owner `transfer_out` of the exact flow on the exact date is the owner-twin step (spec §4.3 step 1), not the transfer rule.
- Sum matching (spec §9) ships with `settlement_sum_matching: false`; the code path exists and is tested with a synthetic calendar-fault fixture.
- `book_settlement`, `book_dividend` and the stand-ins are zero-charged and exempt from `max_amount`, the monthly budget, `per_statement_total` and `per_statement_count` (Q4); they stay revertable.
- Owner twins first (Q5): an owner entry that records the event wins over every system booking, before booking (gate / `_TwinGuarded`) and after (twin retirement).
- The stand-in job (`check_row`, `replace`) never retires or replaces a pair whose bank leg holds active coverage; it re-reads coverage under the booking row and leg locks (NOWAIT; never the account row) before every retirement or replacement (AGENT-360 major 2, re-verify 2 B2). The one claimed-pair exception is reconcile's owner-twin retirement (`retire_twins_for_statement`, spec §4.7 "owner twins first"), which releases the claim as auto-pay does.
- Stand-ins only for feed rows with `date_source = "statement"` and `bookable = true`, settlement date in [`settlement.enabled_from`, `ledger_service._today()`] (Asia/Taipei); never for margin days, CSV-only days or days before go-live (Q7).
- No change to `composite_map`, `account_map`, matching of non-settlement lines, or the SPA in this plan; setting `valuation_source` on 117 waits for the SPA to handle `balance: null` (Task 11 rollout gate).
- Migrations are additive (nullable columns, new tables, appended enum values); downgrade drops columns and tables; appended `case_kind` values stay (PostgreSQL cannot drop them).
- Do not name anything `settlement_service` (`app/services/settlement_service.py` is the D16 collections module) or `Entry.is_settlement` (existing D16 flag): the new names are `trade_settlement_service` and `Entry.settlement_stand_in`.

## Review Focus

1. A 國泰 statement re-posted after a stand-in was booked: a corrected file is a `conflict` and changes nothing; a held day later imported (new facts) replaces the unclaimed stand-in once; after a bank line claimed it, a fact change opens `source_drift` and rebooks nothing — Task 9 `test_reposted_statement_replaces_only_an_unclaimed_stand_in`, Task 6 `test_drift_on_a_claimed_stand_in_opens_source_drift`.
2. The bank posts the settlement on another day than the statement's 交割日期 (or another amount): no claim, the line is `settlement_mismatch` naming the stand-in, the stand-in surfaces as `orphaned_stand_in`, neither moves — Task 6 `test_bank_line_on_another_date_is_a_mismatch_and_an_orphan`.
3. The owner deletes a stand-in pair, then the bank line arrives: the row is `owner_deleted`, the job never re-books it, the line's `settlement` case is proposed, never auto-booked — Task 9 `test_owner_deleted_stand_in_is_never_rebooked`, Task 7 `test_policy_proposes_an_owner_deleted_settlement`.
4. Two equal buys two days apart with two equal bank lines: nothing books, both ambiguous — Task 5 `test_two_lines_one_amount_two_settlements_are_ambiguous`.
5. A trade on the last session before 春節 settles 9–11 calendar days later, inside a statement whose period starts after the trade date: the settlement must still be fetched (its printed settlement date) and, with a same-amount CSV-only settlement visible in the window, be ambiguous rather than booked — Task 6 `test_a_long_holiday_settlement_is_fetched_by_settlement_date`, `test_an_incomplete_feed_answer_assigns_nothing`.
6. The owner deletes the legs of a bank-origin booking: the next reconcile alone (no job run) frees the reservation, tombstones the row `owner_deleted`, and the policy proposes instead of booking — Task 7 `test_policy_proposes_an_owner_deleted_settlement`.
7. The job reads a stand-in as unclaimed, a reconcile claims it and commits, then the job acquires the locks: the pair survives — Task 9 `test_claim_wins_over_a_concurrent_replacement`.
8. Only the printed settlement date of a claimed stand-in changes: the pair stays, `source_drift` opens — Task 6 `test_date_only_drift_on_a_claimed_row_opens_source_drift`.
9. A settlement line whose portfolio net differs by one trade's fee rounding: never booked; pending if unbooked, `source_drift` if booked — Task 6 `test_net_mismatch_stays_pending`.
10. The portfolio down during the sweep or the job: settlement lines pending with `settlement_source_unavailable`, the rest reconciled; the job books and retires nothing — Task 6 `test_portfolio_unavailable_reconciles_the_rest`, Task 9 `test_job_with_portfolio_down_changes_nothing`.
11. A historical `as_of` on `GET /accounts/117` returns that date's valuation, never today's; a cold cache with the portfolio down returns `balance: null, stale: true` — Task 8 `test_historical_as_of_asks_for_that_date`, `test_cold_cache_with_portfolio_down_is_null_and_stale`.
12. Revert of a `book_settlement` after a later reconcile still deletes both legs and reopens the line as `settlement_pending` — Task 7 `test_revert_after_reconcile_reopens_pending`.

---

### Task 1: Persist the 國泰 statement's settlement facts (portfolio) + business-day fallback (accounting)

**Files:**
- Create: `services/stock-portfolio-service/alembic/versions/e9settle_cathay_statement_facts.py` (revision `e9settle`, down_revision `d8broker`, the sole head; re-check with `alembic heads`)
- Modify: `services/stock-portfolio-service/app/models/cathay_statement.py:20-54` (three columns)
- Modify: `services/stock-portfolio-service/app/services/broker_cathay_statement_service.py:308-337` (`_record_attempt`) + new `statement_facts`
- Modify: `services/accounting-service/app/services/business_days.py` (append `add_business_days`)
- Modify: `services/stock-portfolio-service/tests/integration/test_receipt_migration.py:118` (`test_real_graph_has_one_head`: `script.get_current_head() == 'e9settle'` — the new sole head; B1)
- Test: `services/stock-portfolio-service/tests/integration/test_cathay_statement_import.py` (reuse `_payload`, `_line`, `_day`), a new `tests/integration/test_statement_facts_migration.py` (the `test_receipt_migration.py` module-test pattern), `services/accounting-service/tests/unit/test_business_days.py`

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
    day = _day(db_session)
    day.settlement_date = day.net_amount = day.cash_net = None  # a pre-e9settle row
    db_session.commit()
    import_statement(db_session, _payload(), dry_run=False)                       # same sha → duplicate
    assert _day(db_session).settlement_date == date(2026, 3, 11)
    before = (_day(db_session).net_amount, _day(db_session).cash_net)
    other = [_line("範例電子", "集買", 1000, "12.3", 6, order_no="XY12300"), DEFAULT_LINES[1]]
    import_statement(db_session, _payload(lines=other, sha="b" * 64), dry_run=False)  # corrected file → conflict
    assert (_day(db_session).last_status, _day(db_session).net_amount, _day(db_session).cash_net) == ("conflict", *before)
```

Migration module test (`test_statement_facts_migration.py`, the `test_receipt_migration.py` pattern); `test_real_graph_has_one_head` in `test_receipt_migration.py` is changed to expect `e9settle` (B1):

```python
def test_statement_facts_columns_round_trip_on_sqlite():
    path = Path(__file__).parents[2] / "alembic/versions/e9settle_statement_facts.py"
    spec = importlib.util.spec_from_file_location("statement_facts_migration", path)
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE cathay_statement_day (trade_date DATE PRIMARY KEY, last_status VARCHAR(16))")
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            cols = {c["name"]: c for c in sa.inspect(conn).get_columns("cathay_statement_day")}
            assert {"settlement_date", "net_amount", "cash_net"} <= set(cols) and all(cols[k]["nullable"] for k in ("settlement_date", "net_amount", "cash_net"))
            migration.downgrade()
            assert {"settlement_date", "net_amount", "cash_net"}.isdisjoint({c["name"] for c in sa.inspect(conn).get_columns("cathay_statement_day")})
    engine.dispose()
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd services/stock-portfolio-service && .venv/bin/python -m pytest -q tests/integration/test_cathay_statement_import.py -k facts tests/integration/test_statement_facts_migration.py`
Expected: FAIL (`AttributeError: settlement_date` / missing migration file)

- [ ] **Step 3: Implement the migration, columns, `statement_facts` and `_record_attempt`** (and the one-line head change in `test_receipt_migration.py:118`)

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
- Produces: `ledger_feed_service.settlements(db, broker: str, *, settle_from: date, settle_to: date, trade_from: date, trade_to: date) -> dict` → `{"rows": [...], "bounds": {"settle_from", "settle_to", "from", "to"}, "complete": bool}` (spec §4.1 rev 4.2): a trade date is included when it lies in `trade_from … trade_to` (every row, with or without statement facts), OR its persisted `settlement_date` lies in `settle_from … settle_to` (the only clause that reads the settlement date; re-verify 2 B1); `complete` is false when more than `ROW_CAP = 2000` trade dates matched (the rows are then cut at the cap); the query parameters are `settle_from`, `settle_to`, `from`, `to`, all required; and `ledger_feed_service.valuation(db, broker: str, as_of: date) -> dict` (a bare object); `broker != "cathay"` → `UnknownBroker` → 404 `{"detail": "unknown_broker"}`.
  - Settlement row (spec §4.1): `{"trade_date", "settlement_date": str|None, "date_source": "statement"|"legs", "net_amount", "legs_net", "currency": "TWD", "bookable": bool, "reason": None|"margin_day"|"legs_disagree", "items": [{"source_id": "ct:<cash_transaction.id>", "type", "amount"}], "fingerprint": "cathay:<trade_date>:<sha256(','.join(sorted ids))[:16]>"}`; amounts as 4-place strings. Legs = `buy_settle` / `sell_settle` / `fee` / `tax` cash rows of the 國泰 TWD account whose `related_transaction_id` is a `TW_CATHAY_FILTER` transaction. A day with statement facts: `date_source "statement"`, `settlement_date` from the statement; margin (`last_status == "held"` and `last_reason == "unsupported_category"`) → `net_amount` = statement `net_amount`, `bookable false`, `reason "margin_day"`; else `net_amount = cash_net`, and `cash_net != legs_net` → `bookable false`, `reason "legs_disagree"`. A day without facts: `date_source "legs"`, `settlement_date null`, `net_amount = legs_net`, `bookable true`. Zero-net rows are omitted (spec §4.1): a day whose chosen `net_amount` is 0 — `cash_net == 0` with facts (every buy paid with points), `legs_net == 0` without facts (a 沖買/沖賣 day) — never appears, items or not; the `legs_disagree` test is run only on non-zero figures. The `SettlementRow` schema docstring says: "`net_amount` is the statement's cash net (淨收付) on a bookable statement day, the legs' net on a CSV-only day, and the statement's 應收付 on a `margin_day` row — never book from a row whose `bookable` is false". Sorted by trade date.
  - Valuation: `{"broker": "cathay", "currency": "TWD", "as_of", "priced_through": str|None, "market_value", "cash": "0.0000", "total", "complete": bool, "missing": [symbol]}` — LONG quantities of `TW_CATHAY_FILTER` transactions traded on or before `as_of`, corporate actions effective on or before `as_of` only, × the last `price_history.close` on or before `as_of`; `priced_through` = the oldest of those price dates; a held symbol with no price → in `missing`, excluded from `market_value`, `complete false`.

- [ ] **Step 1: Write the failing tests**

```python
def test_statement_day_uses_the_printed_date_and_cash_net(client, cathay):
    cathay.trade("2026-09-10", "BUY", qty=1000, price="12.3", fee="5")          # legs -12305 (fictional)
    cathay.statement_day("2026-09-10", settlement="2026-09-14", net="-12305", cash_net="-12305")
    body = client.get("/api/portfolio/ledger-feed/settlements", params=Q("2026-09-12", "2026-09-16", "2026-09-12", "2026-09-16")).json()  # trade 09-10 is outside the trade range: found by its printed date
    (row,) = body["rows"]
    assert (row["settlement_date"], row["date_source"], row["net_amount"], row["bookable"], row["reason"]) == ("2026-09-14", "statement", "-12305.0000", True, None)
    assert body["bounds"] == {"settle_from": "2026-09-12", "settle_to": "2026-09-16", "from": "2026-09-12", "to": "2026-09-16"} and body["complete"] is True
    assert {i["type"] for i in row["items"]} == {"buy_settle", "fee"} and row["fingerprint"].startswith("cathay:2026-09-10:")


def test_statement_day_is_also_found_by_its_trade_date_alone(client, cathay):
    cathay.trade("2026-09-10", "BUY", qty=1000, price="12.3", fee="5")          # seeded here: every test stands alone
    cathay.statement_day("2026-09-10", settlement="2026-09-14", net="-12305", cash_net="-12305")
    (row,) = client.get("/api/portfolio/ledger-feed/settlements", params=Q("2026-09-10", "2026-09-10", "2026-09-10", "2026-09-10")).json()["rows"]  # settlement 09-14 is outside the settlement window
    assert (row["trade_date"], row["settlement_date"], row["date_source"]) == ("2026-09-10", "2026-09-14", "statement")    # the trade axis ignores the settlement date


def test_csv_only_day_has_no_date_and_other_brokers_are_left_out(client, cathay):
    cathay.trade("2026-09-11", "SELL", qty=100, price="50", fee="7", tax="15")
    cathay.trade("2026-09-11", "BUY", qty=10, price="10", fee="1", broker="TW_MANUAL")  # same cash account, out of scope
    (row,) = client.get("/api/portfolio/ledger-feed/settlements", params=Q("2026-09-11", "2026-09-11", "2026-09-11", "2026-09-11")).json()["rows"]
    assert (row["settlement_date"], row["date_source"], row["net_amount"], row["legs_net"]) == (None, "legs", "4978.0000", "4978.0000")
    assert client.get("/api/portfolio/ledger-feed/settlements", params=Q("2026-09-11", "2026-09-11", "2026-09-12", "2026-09-12")).json()["rows"] == []  # a CSV-only day is found by trade date only


def test_margin_day_and_disagreeing_legs_are_not_bookable(client, cathay):
    cathay.statement_day("2026-09-15", settlement="2026-09-17", net="-30000", cash_net=None, held="unsupported_category")
    cathay.trade("2026-09-16", "BUY", qty=1000, price="10", fee="14")
    cathay.statement_day("2026-09-16", settlement="2026-09-18", net="-10014", cash_net="-10015")
    rows = client.get("/api/portfolio/ledger-feed/settlements", params=Q("2026-09-15", "2026-09-18", "2026-09-15", "2026-09-16")).json()["rows"]
    assert [(r["trade_date"], r["bookable"], r["reason"]) for r in rows] == [("2026-09-15", False, "margin_day"), ("2026-09-16", False, "legs_disagree")]


def test_zero_net_days_are_omitted_with_or_without_items(client, cathay):
    cathay.trade("2026-09-17", "BUY", qty=100, price="10", fee="0", points=True)          # legs exist; statement cash net 0
    cathay.statement_day("2026-09-17", settlement="2026-09-21", net="-1000", cash_net="0")
    cathay.trade("2026-09-18", "BUY", qty=100, price="10", fee="0"); cathay.trade("2026-09-18", "SELL", qty=100, price="10", fee="0", tax="0")  # CSV-only, legs net 0
    assert client.get("/api/portfolio/ledger-feed/settlements", params=Q("2026-09-17", "2026-09-21", "2026-09-17", "2026-09-18")).json()["rows"] == []


def test_row_cap_reports_incomplete(client, cathay, monkeypatch):
    monkeypatch.setattr(ledger_feed_service, "ROW_CAP", 1)
    cathay.trade("2026-09-22", "BUY", qty=100, price="10", fee="1"); cathay.trade("2026-09-23", "BUY", qty=100, price="10", fee="1")
    body = client.get("/api/portfolio/ledger-feed/settlements", params=Q("2026-09-22", "2026-09-30", "2026-09-22", "2026-09-23")).json()
    assert body["complete"] is False and len(body["rows"]) == 1


def test_unknown_broker_is_404(client):
    assert client.get("/api/portfolio/ledger-feed/settlements", params=Q("2026-09-01", "2026-09-02", "2026-09-01", "2026-09-02", broker="ib")).status_code == 404


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

`Q(settle_from, settle_to, trade_from, trade_to, broker="cathay")` builds the query dict (`from` / `to` for the trade bounds). `cathay` is a module fixture object with `trade(..., points=False)` (inserts the `Transaction` and, unless `points`, its legs through the same `cash_account_service` path the CSV importer uses, `CASH_LEG_ENABLED=true`), `statement_day(...)`, `price(...)`, `split(...)`.

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
- Modify: `services/accounting-service/app/routers/accounts.py` + `app/schemas/writes.py` (admin-only `PATCH /accounts/{account_id}`, body `AccountSourcesPatch(settlement_source: str | None = None, valuation_source: str | None = None)`, `dependencies=[Depends(require("admin"))]` as `routers/statements.py:70` `ADMIN`; sets only the fields present, returns `AccountDetailOut`; spec §4.2, D7)
- Modify: `services/accounting-service/tests/integration/test_migration.py:26,316,352,422` (new constant `SETTLEMENT_HEAD = "d5e1f7a3b9c2"`; the three `_version(url) == AUTO_PAY_HEAD` assertions after `upgrade("head")` become `== SETTLEMENT_HEAD`; B2)
- Test: `services/accounting-service/tests/integration/test_migration.py` (next to `test_statement_auto_pay_round_trip` `:709`; `test_head_schema_matches_the_models` `:189` must keep passing), `services/accounting-service/tests/integration/test_accounts_api.py` (+2: the PATCH)

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

ORM test in the same file (`db_session` on the migrated schema, fictional amounts):

```python
def _booking(account_id, fingerprint, status="booked", **kw):
    return SettlementBooking(kind="settlement", origin="stand_in", account_id=account_id, source="portfolio:cathay", fingerprint=fingerprint,
                             trade_date=date(2026, 9, 10), settlement_date=kw.get("settlement_date", date(2026, 9, 14)), net_amount=Decimal("-12305"),
                             facts={}, status=status)


def test_booked_rows_are_unique_per_fingerprint_and_day_but_retired_rows_are_not(seed, db_session):
    bank = seed.account("測試銀行"); db_session.commit()
    db_session.add(_booking(bank.id, "cathay:2026-09-10:aa")); db_session.commit()
    db_session.add(_booking(bank.id, "cathay:2026-09-10:aa"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
    db_session.add(_booking(bank.id, "cathay:2026-09-10:bb"))                                   # same day, other fingerprint
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
    db_session.scalars(select(SettlementBooking)).one().status = "retired"; db_session.commit()
    db_session.add(_booking(bank.id, "cathay:2026-09-10:aa")); db_session.commit()               # the retired row no longer reserves
    assert sorted(r.status for r in db_session.scalars(select(SettlementBooking))) == ["booked", "retired"]
```

`test_accounts_api.py` (+2): `test_admin_patch_sets_the_sources` (`PATCH /accounts/{id}` with the admin header and `{"settlement_source": "portfolio:cathay"}` → 200, `GET` shows it, `valuation_source` untouched) and `test_patch_sources_needs_admin` (the write header → 403).

- [ ] **Step 2: Run to verify it fails**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/integration/test_migration.py -k "settlement_booking or head_schema"`
Expected: FAIL (columns / tables missing)

- [ ] **Step 3: Write the migration, the models, the `PATCH` route and the `SETTLEMENT_HEAD` constant** (partial indexes as `Index(..., unique=True, postgresql_where=...)` in both so `test_head_schema_matches_the_models` compares equal)

- [ ] **Step 4: Run the migration tests and the unit directory**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/integration/test_migration.py tests/integration/test_accounts_api.py tests/unit`
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
- Modify: `services/accounting-service/requirements.txt` (add `httpx==0.28.1`, the pin of `requirements-worker.txt:6` and `requirements-notify.txt:1`; the three files stay equal)
- Test: `services/accounting-service/tests/unit/test_portfolio_client.py` (`httpx.MockTransport`)

**Interfaces:**
- Produces: frozen dataclasses `Item(source_id: str, type: str, amount: Decimal)`; `Settlement(trade_date: date, settlement_date: date | None, date_source: str, net_amount: Decimal, legs_net: Decimal, currency: str, bookable: bool, reason: str | None, items: tuple[Item, ...], fingerprint: str)` with property `source_ids -> tuple[str, ...]` (sorted); `Valuation(broker, currency, as_of: date, priced_through: date | None, market_value: Decimal, cash: Decimal, total: Decimal, complete: bool, missing: tuple[str, ...])`.
- `PortfolioClient(base_url: str, token: str | None = None, *, timeout: float = 5.0, transport: httpx.BaseTransport | None = None)` with `settlements(broker, *, settle_from: date, settle_to: date, trade_from: date, trade_to: date) -> Feed` and `valuation(broker, as_of) -> Valuation` (a bare object); `Feed(rows: tuple[Settlement, ...], bounds: Bounds, complete: bool)` with `Bounds(settle_from, settle_to, trade_from, trade_to)`; the client raises `PortfolioUnavailable("incomplete")` when `complete` is false or the echoed bounds differ from the request (completeness is asserted here, once); paths `/api/portfolio/ledger-feed/settlements|valuation`; header `Authorization: Bearer <token>` only when a token is set; `__repr__ = object.__repr__`.
- `PortfolioUnavailable(RuntimeError)` with `.code` ∈ {`connect`, `timeout`, `http_<status>`, `bad_body`, `incomplete`, `calendar`} on any failure (`calendar` is raised by Task 6, never by the client; M2); message = the code only. `Feed.complete` is always True past the client's own check and is kept on purpose (C5): the object states its own contract, and the Task 6 stub / tests assert it rather than inferring completeness from the absence of an exception.
- `client_for() -> PortfolioClient`: base URL `PORTFOLIO_BASE_URL` (default `http://127.0.0.1:8001`), token `PORTFOLIO_READ_TOKEN` (optional), read at call time.
- `cached(client) -> ContextManager[PortfolioClient]`: memoises reads per (method, args) inside the block. Callers that want the memo pass the cached client explicitly: Task 6 `fetch_by_*` take `client: PortfolioClient | None = None` and fall back to `client_for()` (M3; a monkeypatched `client_for` in the tests still wins because `reconcile` builds its cached client from `client_for()`).

- [ ] **Step 1: Write the failing tests**

```python
TOKEN = "secret-token-value"
WINDOW = dict(settle_from=date(2026, 9, 1), settle_to=date(2026, 9, 30), trade_from=date(2026, 8, 27), trade_to=date(2026, 9, 30))
BOUNDS = {"settle_from": "2026-09-01", "settle_to": "2026-09-30", "from": "2026-08-27", "to": "2026-09-30"}
ROW = {"trade_date": "2026-09-10", "settlement_date": "2026-09-14", "date_source": "statement", "net_amount": "-12305.0000",
       "legs_net": "-12305.0000", "currency": "TWD", "bookable": True, "reason": None, "fingerprint": "cathay:2026-09-10:abc",
       "items": [{"source_id": "ct:7", "type": "fee", "amount": "-5.0000"}, {"source_id": "ct:5", "type": "buy_settle", "amount": "-12300.0000"}]}


def test_settlements_parse_and_header_only_with_a_token():
    seen = []
    def handler(request):
        seen.append((request.url.path, request.headers.get("authorization")))
        return httpx.Response(200, json={"rows": [ROW], "bounds": BOUNDS, "complete": True})
    (row,) = pc.PortfolioClient("http://p.test", transport=httpx.MockTransport(handler)).settlements("cathay", **WINDOW).rows
    pc.PortfolioClient("http://p.test", TOKEN, transport=httpx.MockTransport(handler)).settlements("cathay", **WINDOW)
    assert seen == [("/api/portfolio/ledger-feed/settlements", None), ("/api/portfolio/ledger-feed/settlements", f"Bearer {TOKEN}")]
    assert (row.settlement_date, row.net_amount, row.bookable, row.source_ids) == (date(2026, 9, 14), Decimal("-12305.0000"), True, ("ct:5", "ct:7"))


@pytest.mark.parametrize("body", [{"rows": [ROW], "bounds": BOUNDS, "complete": False},
                                  {"rows": [ROW], "bounds": {**BOUNDS, "from": "2026-09-05"}, "complete": True}])
def test_incomplete_or_narrowed_answers_are_unavailable(body):
    with pytest.raises(pc.PortfolioUnavailable) as info:
        pc.PortfolioClient("http://p.test", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))).settlements("cathay", **WINDOW)
    assert info.value.code == "incomplete"


@pytest.mark.parametrize("make, code", [(lambda r: httpx.Response(500), "http_500"), (lambda r: httpx.Response(404), "http_404"),
                                        (lambda r: httpx.Response(200, json=[ROW]), "bad_body"),  # a bare list: no envelope
                                        (lambda r: (_ for _ in ()).throw(httpx.ConnectError("down")), "connect")])
def test_failures_are_unavailable_with_a_code_only(make, code, caplog):
    with pytest.raises(pc.PortfolioUnavailable) as info:
        pc.PortfolioClient("http://p.test", TOKEN, transport=httpx.MockTransport(make)).valuation("cathay", date(2026, 9, 30))
    assert info.value.code == code and TOKEN not in str(info.value) + repr(info.value) + caplog.text


def test_cached_client_memoises_within_the_block():
    calls = []
    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"rows": [ROW], "bounds": BOUNDS, "complete": True})
    with pc.cached(pc.PortfolioClient("http://p.test", transport=httpx.MockTransport(handler))) as c:
        c.settlements("cathay", **WINDOW); c.settlements("cathay", **WINDOW)
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
- Modify: `services/accounting-service/app/services/statements/matching.py` (`Rules` `:128-152`; `Entry` `:66-101`; `_representations` `:603`; `match` `:1066`)
- Test: `services/accounting-service/tests/unit/test_settlement_match.py`, `services/accounting-service/tests/unit/test_statement_matching.py` (+3)

**Interfaces:**
- Consumes: `matching.Line`, `matching.Entry`, `matching._same` (`:333`), Task 4 `Settlement`, Task 1 `add_business_days`.
- Produces in `matching`: `SETTLEMENT_LABELS = ("交割股款", "申購股款")`; `Rules.settlement_labels: tuple[str, ...] = SETTLEMENT_LABELS`, `Rules.settlement_window_days: int = 2`, `Rules.settlement_sum_matching: bool = False` (`RULE_DEFAULTS` and `_check_rules` pick them up from the dataclass, `settings_service.py:81,228`); `Entry.settlement_stand_in: bool = False`; `is_settlement_line(line: Line, rules: Rules) -> bool` (`line_kind ∈ {withdrawal, deposit}` and a label is a substring of `merchant_norm`); `SETTLEMENT_STAND_IN_RULE = "settlement_stand_in"`; `SETTLEMENT_LINE_HINT = "settlement_line"`.
- `match` on a bank statement, for a settlement line: the accept and ambiguous steps as today (owner twins; stand-in reps are absent); no claimed-by-earlier-line case (the global assignment treats look-alikes); then `_settlement_stand_in_claim(line, entries, claimed) -> Representation | None` (exactly one unclaimed entry with `settlement_stand_in`, `flow == line.flow`, `entry_date == line.posted_date` → standalone claim at `rules.accept`, rule `settlement_stand_in`); otherwise `Case(line.id, None, "line_unmatched", (), {"hint": "settlement_line"})` — no installment, auto-pay, foreign, transfer, near, drift, bank-only or needs-peer step. `_representations` never represents an entry with `settlement_stand_in`.
- Produces in `settlement_match`: `trade_bounds(settle_from: date, settle_to: date, *, extra_holidays, extra_workdays) -> tuple[date, date]` — the trade range whose T+2 can land in the settlement window: `trade_from` = the earliest day `t` with `add_business_days(t, 2, …) >= settle_from` (walk back from `settle_from` until the condition fails), `trade_to = settle_to`; computed from `business_days`, never a fixed margin; calendar expiry (`business_days.LAST_KNOWN_DAY` `:17`, `calendar_covers` `:94`): when `calendar_covers(settle_to)` is false or `add_business_days` returns None for any `t` on the walk, the bound cannot be proven complete → raise `settlement_match.CalendarExpired(ValueError)`; Task 6 turns it into `PortfolioUnavailable("calendar")`, so the lines go `settlement_pending` and nothing is assigned automatically (AGENT-360 re-check minor 3); `with_dates(rows: list[Settlement], *, extra_holidays, extra_workdays) -> list[Settlement]` (a `legs` row gets `add_business_days(trade_date, 2, …)`; None stays None — the row is kept and `assign` counts it in `unknown_dates`); `@dataclass Assignment(booked: dict[int, Settlement], ambiguous: dict[int, list[Settlement]], pending: dict[int, str | None], unknown_dates: int)`; `assign(lines: list[Line], settlements: list[Settlement], *, reserved_fps: set[str], reserved_ids: set[str], contestants: set[int], rules: Rules) -> Assignment` — `assign` first drops every line for which `is_settlement_line(line, rules)` is false (callers may pass a statement's whole line list).
  - Candidate of a line: `net_amount == line.flow` (`_same`), dated, `|settlement_date − posted_date| <= settlement_window_days`, fingerprint not in `reserved_fps`, no constituent in `reserved_ids`. Sum candidates only when `settlement_sum_matching` (spec §9 gate; fingerprint `f1+f2`, items merged).
  - A line in `contestants` (already explained) competes and is never in `booked` / `ambiguous` / `pending`. Lines explained by a settlement booking leg are not passed at all (Task 6 drops them from the graph — spec §4.3 rev 4.5, B7): their settlement is reserved through `reserved_fps` / `reserved_ids` instead.
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


def test_trade_bounds_past_the_calendar_raise():
    with pytest.raises(sm.CalendarExpired):
        sm.trade_bounds(date(2028, 1, 5), date(2028, 1, 31), extra_holidays=frozenset(), extra_workdays=frozenset())   # past LAST_KNOWN_DAY 2027-12-31


def test_trade_bounds_follow_the_bank_calendar():
    assert sm.trade_bounds(date(2026, 2, 24), date(2026, 3, 23), extra_holidays=frozenset(), extra_workdays=frozenset()) == (date(2026, 2, 13), date(2026, 3, 23))  # 春節: 02-13 is the first trade day settling on or after 02-24
    assert sm.trade_bounds(date(2026, 9, 1), date(2026, 9, 30), extra_holidays=frozenset(), extra_workdays=frozenset()) == (date(2026, 8, 28), date(2026, 9, 30))


def test_with_dates_derives_t_plus_two_only_for_legs_rows():
    rows = sm.with_dates([S("f1", None, "-1", td=date(2026, 10, 7), source="legs"), S("f2", date(2026, 10, 8), "-1", td=date(2026, 10, 6))],
                         extra_holidays=frozenset(), extra_workdays=frozenset())
    assert [r.settlement_date for r in rows] == [date(2026, 10, 12), date(2026, 10, 8)]
```

`test_statement_matching.py` (+3), with the file's existing `L` / `E` builders and its `m.match(kind, lines, entries, groups, plan_map, fee, rules, "TWD")` call shape (`:724`):

```python
def _bank_match(lines, entries, rules=None):
    return m.match("bank", lines, entries, {}, {}, lambda e: None, rules or m.Rules(), "TWD")


def test_settlement_line_is_claimed_by_an_exact_owner_entry_first():
    line = L(1, date(2026, 9, 14), "-12305", kind="withdrawal", merchant="交割股款")
    owner = E(10, date(2026, 9, 14), "-12305", kind="transfer_out", merchant="")
    stand_in = E(11, date(2026, 9, 14), "-12305", kind="transfer_out", merchant="", settlement_stand_in=True)
    res = _bank_match([line], [owner, stand_in])
    (claim,) = res.claims
    assert (claim.line_id, claim.entry_ids, claim.rule) == (1, (10,), "exact") and res.cases == []


def test_settlement_stand_in_claims_only_on_the_exact_date():
    line = L(1, date(2026, 9, 14), "-12305", kind="withdrawal", merchant="交割股款")
    assert _bank_match([line], [E(11, date(2026, 9, 14), "-12305", kind="transfer_out", merchant="", settlement_stand_in=True)]).claims[0].rule == "settlement_stand_in"
    res = _bank_match([line], [E(11, date(2026, 9, 15), "-12305", kind="transfer_out", merchant="", settlement_stand_in=True)])
    assert res.claims == [] and [(c.kind, c.context) for c in res.cases] == [("line_unmatched", {"hint": "settlement_line"})]


def test_settlement_line_never_gets_near_or_bank_only():
    line = L(1, date(2026, 9, 14), "-12305", kind="withdrawal", merchant="交割股款")
    res = _bank_match([line], [E(10, date(2026, 9, 14), "-12300", kind="transfer_out", merchant="")])   # near amount: no amount_delta case
    assert res.claims == [] and [(c.kind, c.context.get("hint")) for c in res.cases] == [("line_unmatched", "settlement_line")]
```

(`E(..., settlement_stand_in=True)` needs the builder to pass the new field through `**kw`; add `settlement_stand_in=kw.get("settlement_stand_in", False)` to `E`.)

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
- Shared with auto-pay (C4, deliberate scope): a new `app/services/pair_locks.py` with `lock_row_and_legs(db, row, *, leg_ids: tuple[int | None, int | None], busy_code: str) -> tuple[LedgerEntry | None, LedgerEntry | None]` — the model-agnostic "row FOR UPDATE NOWAIT, then legs FOR UPDATE NOWAIT, held → `CodedConflictError(busy_code)`" step; `auto_pay_service.lock_pair` / `_lock_row` (`:325`, `:344`) become thin calls to it (behaviour unchanged, `test_auto_pay_service.py` keeps passing). Everything else of the auto-pay lifecycle (`find_twins`, `retire_pair`, `retire_twins_for_statement`, `check_row`, `housekeep`) is re-implemented for `settlement_booking` on purpose: the lifecycle differs (claimed pairs refused except twins, replacement, two origins, feed facts instead of a statement header), so a shared abstraction would carry flags for every difference. The final reviewer should read the similarity as intended.
- Produces in `stand_ins`: `settlement_legs(db, account_ids: list[int]) -> dict[int, int]` (entry id → `settlement_booking.id` for legs of `booked` rows of `kind = settlement` and `origin = stand_in` whose own account is listed); `stand_in_entry_ids` = backfills ∪ auto-pay legs ∪ settlement legs; `unclaimed_settlement_legs(db, account_id: int) -> dict[int, int]` (bank leg entry id → booking id for `booked` `origin = stand_in` rows of the account with no active coverage on either leg — the one "unclaimed stand-in" query; Task 9 `unclaimed_stand_ins` returns its value set; C4); `_settlement_orphans(db, statement, accounts, claimed) -> list[dict]` (over `unclaimed_settlement_legs`, the legs dated in the period with no settlement twin → `{"entry_id", "event_id": None, "line_id": None, "backfill_action_id": None, "settlement_booking_id", "claimant_entry_id": None}`; `_desired_cases` copies `settlement_booking_id` into the `orphaned_stand_in` context like `auto_pay_id` `:689-690`).
- Produces in `trade_settlement_service`: `ACTOR = "settlement"`; `SETTLEMENT_BUSY = "settlement_busy"`.
  - `fetch_by_settlement_window(db, account: Account, settle_from: date, settle_to: date, *, client: PortfolioClient | None = None) -> Feed` — `trade_from, trade_to = settlement_match.trade_bounds(...)` with the auto-pay calendar overrides (`CalendarExpired` → `PortfolioUnavailable("calendar")`, before any client call), one client call, `with_dates` over the rows; raises `PortfolioUnavailable` (incl. `incomplete`, `calendar`). `fetch_by_trade_date(db, account: Account, trade_from: date, trade_to: date, *, client=None) -> Feed` — the same call with `settle_from = settle_to = trade_from` and the given trade range: the trade axis returns every row of those trade dates whatever their settlement date (Task 2 contract, re-verify 2 B1), so a normal statement day (trade 09-10, settlement 09-14) is found by `fetch_by_trade_date(09-10, 09-10)`; rows of OTHER trade dates that settle on `trade_from` come back too, so every caller selects `r.trade_date == wanted` and, for a booking, `r.fingerprint == facts["fingerprint"]` (M1). `Feed.rows` keeps undated rows.
  - `find_twins(db, *, account_id: int, target_account_id: int, amount: Decimal, settlement_date: date, window_days: int, exclude: Iterable[int] = ()) -> list[int]` (the `auto_pay_service.find_twins` shape `:142`; top-level entries not of statement source within `window_days` of `settlement_date`: on `account_id` an entry of `amount` (negative = money out), or a transfer leg on `target_account_id` of `−amount` whose peer is on `account_id`).
  - `lock_pair(db, row: SettlementBooking) -> tuple[LedgerEntry | None, LedgerEntry | None]` — `pair_locks.lock_row_and_legs(db, row, leg_ids=(row.out_entry_id, row.in_entry_id), busy_code=SETTLEMENT_BUSY)`: the booking row `FOR UPDATE NOWAIT`, then both legs `FOR UPDATE NOWAIT`, inside one savepoint as `auto_pay_service.retire_pair` `:366-392`; a held row or leg → `CodedConflictError(SETTLEMENT_BUSY)`, never a wait (a blocking row lock closes a cycle against a reconcile that holds its statement row and waits for the booking row in `retire_twins_for_statement`). **The one lock protocol** (spec §4.7 Locks, rev 4.3): booking row NOWAIT → legs NOWAIT → coverage re-read, and **no account row lock** anywhere in the stand-in lifecycle (job, `housekeep`, `retire_pair`, `book_stand_in`): the engine's real order is statement rows → case row → account row + barrier (blocking) → schedule / group / entry rows NOWAIT (engine.py:244-270; revert :479-482), and the engine_locks.py:1-9 invariant is that the account-row takers hold no entry or group lock and no ledger writer locks a case or action row — a job or a reconcile taking the account row while holding entry locks or a statement row would close a cycle against another statement's `needs_recheck` update or against a flushed tombstone row (spec §4.7 Locks (a)/(b)). `book_settlement` / revert keep the engine's own account lock + barrier (`_TwinGuarded.lock_targets`) and lock the booking row → legs NOWAIT after the case checks; the revert already holds the legs NOWAIT from `lock_rows` (engine.py:482) when it reaches the booking row, so that later lock is a reverse-order try-lock and must stay NOWAIT (409 `settlement_busy`), never blocking; `reconcile` holds a stand-in leg through `_lock_candidates` (NOWAIT, `:1006`) while it claims it, and runs `housekeep` under its statement row (`_lock_statement`, `:976`) with the same row → legs order; so the two sides exclude each other on the leg rows, jobs serialise on `SETTLEMENT_JOB_LOCK_KEY`, and bookings for one day serialise on the partial unique over `booked` rows.
  - `housekeep(db, account_id: int) -> dict[str, int]` — every `booked` row of the account, both origins: both legs gone → status `owner_deleted`, items deleted (the reservation is released in the same transaction), audit `settlement_owner_deleted`; a leg gone or not `source='statement'` → `owner_edited`, items kept, audit `settlement_owner_edited`. Called by `reconcile` (below) and by the job; flushes, never commits. Departure from auto-pay: the owner-delete / owner-edit part of auto-pay's `check_row` runs only in its job; here it is `housekeep`, and because the bank origin has no job of its own `reconcile` runs it before reading reservations (AGENT-360 major 1, M8).
  - `retire_pair(db, row, *, reason: str) -> set[int] | None` — `lock_pair`, re-check `booked` + legs present + statement source; for `reason == "twin"` the twin must still exist and the pair is retired with its coverage released (`coverage_service.release_entries`, the auto-pay sequence `:357-409`); for every other reason (`gone`, a feed `reason`, `replaced`) the active coverage of the legs is re-read under the locks and ANY active row → return None (claimed: never retired by the job; AGENT-360 major 2). Then `delete_entries_cascade`, items deleted, status / reason set, released statements `needs_recheck`, audit `settlement_retired`.
  - `retire_twins_for_statement(db, statement, accounts) -> list[int]` (twin reason only, for both origins' `booked` rows whose settlement date lies in the period ± population window); `link_claims(db, statement) -> list[int]` (a booked stand-in whose bank leg holds active coverage on a current line of this statement gets `event_id` = that line's event, audit `settlement_claimed`; idempotent); `bookings_by_event(db, account_id) -> dict[int, SettlementBooking]`.
- `reconcile` (when `accounts_by_id[statement.account_id].settlement_source` is set and the statement is a bank statement printing a settlement line): right after the auto-pay call at `:990`: `trade_settlement_service.housekeep(db, statement.account_id)` then `retire_twins_for_statement`; before `_lock_candidates` (`:1006`): `settle_lo, settle_hi = period_start − 5, period_end + 5` and `feed = fetch_by_settlement_window(db, account, settle_lo, settle_hi, client=cached_client)` with `cached_client` from `with portfolio_client.cached(portfolio_client.client_for()) as cached_client` around the settlement step (the drift re-reads below pass the same client; M3) (`PortfolioUnavailable`, `incomplete` included → the unavailable outcome below: no automatic assignment on an answer whose completeness is not asserted); population entries get `settlement_stand_in=e.id in settlement_legs`; after `_write_coverage` (`:1023`) call `link_claims`; in live mode, before `_write_cases`, replace each `settlement_line` placeholder with the assignment's outcome:
  - lines in the graph = EVERY settlement line (covered or not) of this statement's `all_lines` ∪ every settlement line (covered or not) of the account's live, non-retired neighbour statements (`_guard_neighbours`, `:170`) whose posted date lies in `settle_lo − 2 … settle_hi + 2`, MINUS every line whose active coverage entry is a leg of a `booked` `settlement_booking` row (either origin; `settlement_legs` + `bookings_by_event` — its settlement is reserved, keeping it would make same-amount neighbours ambiguous forever; spec §4.3 rev 4.5, B7); contestants = every remaining graph line with active coverage anywhere (`coverage_service.active_rows` per statement, this run's claims included) — spec §4.3: an owner-covered or otherwise explained line still competes and is never booked (AGENT-360 major 4);
  - `reserved_fps` / `reserved_ids` = fingerprints / item ids of `booked` rows (read AFTER `housekeep`, so an owner-deleted booking no longer reserves);
  - `FACTS_KEYS = ("fingerprint", "trade_date", "settlement_date", "net_amount", "date_source", "bookable", "items")` with `items = [{"source_id", "type", "amount"}]`, dates ISO, amounts as 4-dp strings: ONE schema for `settlement_booking.facts` of both origins and for every case candidate (`facts_of(settlement: Settlement) -> dict`; M6);
  - booked → `settlement` case, candidates `[facts_of(s)]`, context `{"fingerprint", "source": account.settlement_source, "owner_deleted": bool}` (`owner_deleted` = an `owner_deleted` row with this fingerprint or this (account, settlement_date) exists);
  - ambiguous → `ambiguous` + `{"hint": "settlement_ambiguous"}`, candidates as above;
  - pending → `line_unmatched` + `{"hint": "settlement_pending", "reason": <reason|None>, "nearest": {"date", "delta"} | None}` — `nearest` (spec §9, D6) = the dated, unreserved settlement of the window with the smallest `|settlement_date − posted_date|` (ties: smallest `|net_amount − flow|`), `delta = net_amount − line.flow` as a 4-dp string, None when the window has none; a pending line with NO candidate (`reason is None`) and an unclaimed booked stand-in of the account within 2 days → `{"hint": "settlement_mismatch", "settlement_booking_id", "stand_in_date", "stand_in_amount"}` instead (spec §4.3 bullet 3; a sole non-bookable candidate keeps `settlement_pending` with its `reason`, D8);
  - portfolio unavailable, incomplete or calendar expired → every placeholder `settlement_pending` + `result.settlement_source_unavailable = True` (`settlements["unavailable_code"]` = the client code: `connect` / `timeout` / `http_<status>` / `bad_body` / `incomplete` / `calendar`).
- Drift (live mode, after `_write_coverage`): for every current line of `all_lines` whose active coverage entry is a leg of a `booked` settlement row (either origin, owned and matcher coverage alike), the row's trade date is re-read through `fetch_by_trade_date(trade_date, trade_date, client=cached_client)` and the row selected by `trade_date` AND `facts["fingerprint"]` (M1); a difference in net, settlement date, `(source_id, amount)` set, `bookable` or `date_source` against `facts`, or no such row → a `settlement` case `{"hint": "source_drift", "booking_id", "booked": {...}, "now": {...}|None}` with no candidates, written through `_write_cases` with the line taken from `all_lines` (not `lines`). The pair stays; nothing is rebooked (AGENT-360 major 6).
- `ReconcileResult.settlement_source_unavailable: bool = False`, `ReconcileResult.settlements: dict[str, int | str | None]` (`booked`, `ambiguous`, `pending`, `mismatch`, `drift`, `claimed`, `unknown_dates` = `Assignment.unknown_dates`, `unavailable_code`); `ReconcileOut.settlements: dict` and `SweepOut.settlement_source_unavailable: int` (count of statements) carry them (AGENT-360 minor 8).
- `reconciliation_hook`: after its reconcile of a statement whose account has `settlement_source`, set `needs_recheck` on the account's other live, non-retired statements whose period overlaps `period_start − 7 … period_end + 7`.
- `inbox_service.classify(..., settlement_labels=matching.SETTLEMENT_LABELS)`: `kind == "settlement"` → `settlement`; `kind == "ambiguous"` with hint `settlement_ambiguous` → `settlement`; the existing `line_unmatched` + label rule keeps its place; `kind == "dividend"` → `other`. `SETTLEMENT_LABELS` (`:30`) becomes `matching.SETTLEMENT_LABELS`; the caller passes the rules' labels. `_single_candidate` / `_listed_candidates` skip candidates without an integer `entry_id`.

`settlement_fixtures.py` (no real account ids anywhere in names, docstrings or comments — D1): `settlement_target(seed, db)` → a TWD target account; `settlement_bank(seed, db)` → (a bank account with `settlement_source="portfolio:cathay"`, `settlement_target(...)`) — the fixture's own ids, never the owner's; `submit_bank(db, run, bank, lines, **kw) -> int` (= `statement_fixtures._submit(..., kind="bank", **kw)` — which returns `(AccountStatement, lines, res)` — unpacked to the statement id; every `sid` below comes from it); `feed(*rows, fingerprint=None, amounts=None)` builds feed JSON from `(trade_date, settlement_date|None, net, ids[, overrides])` tuples (fictional amounts; `amounts` = per-item amounts, default: the whole net on the first item and 0 on the rest; `overrides` patch `bookable` / `reason` / `date_source`; the fingerprint defaults to `cathay:<trade_date>:<ids joined>`); fixture `portfolio_stub` (monkeypatches `portfolio_client.client_for` to a client over `httpx.MockTransport`; `.rows` list, served FILTERED by the request's two axes exactly as Task 2 does and echoed with the request's bounds; `.down = True` → 503; `.narrow = True` → the echoed `from` is one day later than requested; `.calls` = the query params of each request); `seed_stand_in(db, bank, target, *, settlement_date, net, fingerprint, ids, origin="stand_in", amounts=None, trade_date=None)` (a `create_transfer` pair with `source="statement"` and a `booked` row of that origin with items — the Task 9 job's shape, written directly; `amounts` defaults exactly as `feed()` does — whole net on the first id, 0 on the rest — and `facts` is `facts_of(...)` of the same values, so a `feed()` row with the same arguments compares equal; `trade_date` defaults to the date in the fingerprint; M6); `bank_line(seq, day, merchant, flow)` → a `_line` of kind `withdrawal` / `deposit` by the flow's sign, printed so that `_flow` returns `flow`; `settle_line(seq, day, flow)` = `bank_line(seq, day, "交割股款", flow)`.

- [ ] **Step 1: Write the failing integration tests**

```python
def test_unique_settlement_writes_a_settlement_case_and_never_a_backfill(seed, db_session, run, portfolio_stub):
    bank, _ = settlement_bank(seed, db_session)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5", "ct:7")))
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
    (case,) = _cases(db_session, sid, "open")
    assert (case.kind, case.context["fingerprint"], case.candidates[0]["settlement_date"]) == ("settlement", portfolio_stub.rows[0]["fingerprint"], "2026-09-14")


def test_net_mismatch_stays_pending(seed, db_session, run, portfolio_stub):
    bank, _ = settlement_bank(seed, db_session)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12306", ("ct:5",)))
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
    (case,) = _cases(db_session, sid, "open")
    assert (case.kind, case.context["hint"]) == ("line_unmatched", "settlement_pending")
    assert case.context["nearest"] == {"date": "2026-09-14", "delta": "-1.0000"}                   # spec §9: the nearest settlement, to spot the missing import


def test_portfolio_unavailable_reconciles_the_rest(seed, db_session, run, portfolio_stub):
    bank, _ = settlement_bank(seed, db_session); portfolio_stub.down = True
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305"), bank_line(2, date(2026, 9, 15), "ATM", "-1000")])
    result = _reconcile(db_session, sid)
    assert result.settlement_source_unavailable is True
    assert {(c.kind, (c.context or {}).get("hint")) for c in _cases(db_session, sid, "open")} >= {("line_unmatched", "settlement_pending")}


def test_owner_twin_claims_first_and_contests_its_settlement(seed, db_session, run, portfolio_stub):
    bank, target = settlement_bank(seed, db_session)
    _transfer(seed, bank, target, Decimal("12305"), date(2026, 9, 14))                         # the owner's own record
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305"), settle_line(2, date(2026, 9, 15), "-12305")])
    kinds = {c.line_id: (c.kind, c.context.get("hint")) for c in _cases(db_session, sid, "open")}
    assert len(_active(db_session, sid)) == 1 and list(kinds.values()) == [("ambiguous", "settlement_ambiguous")]


def test_stand_in_is_claimed_on_its_exact_date_and_linked(seed, db_session, run, portfolio_stub):
    bank, target = settlement_bank(seed, db_session)
    booking = seed_stand_in(db_session, bank, target, settlement_date=date(2026, 9, 14), net="-12305", fingerprint="cathay:2026-09-10:aa", ids=("ct:5",))
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)), fingerprint="cathay:2026-09-10:aa")
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
    db_session.refresh(booking)
    assert _cases(db_session, sid, "open") == [] and booking.event_id is not None and booking.status == "booked"
    assert [c.match_rule for c in _active(db_session, sid)] == ["settlement_stand_in"]


def test_bank_line_on_another_date_is_a_mismatch_and_an_orphan(seed, db_session, run, portfolio_stub):
    bank, target = settlement_bank(seed, db_session)
    booking = seed_stand_in(db_session, bank, target, settlement_date=date(2026, 9, 14), net="-12305", fingerprint="cathay:2026-09-10:aa", ids=("ct:5",))
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)), fingerprint="cathay:2026-09-10:aa")
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 15), "-12305")])
    hints = {c.context.get("hint"): c for c in _cases(db_session, sid, "open")}
    assert hints["settlement_mismatch"].context["settlement_booking_id"] == booking.id
    assert hints["orphaned_stand_in"].context["settlement_booking_id"] == booking.id
    db_session.refresh(booking)
    assert (booking.status, booking.event_id) == ("booked", None)                              # nothing moved


def test_drift_on_a_claimed_stand_in_opens_source_drift(seed, db_session, run, portfolio_stub):
    bank, target = settlement_bank(seed, db_session)
    seed_stand_in(db_session, bank, target, settlement_date=date(2026, 9, 14), net="-12305", fingerprint="cathay:2026-09-10:aa", ids=("ct:5", "ct:7"))
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5", "ct:7")), fingerprint="cathay:2026-09-10:aa")
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5", "ct:7")), fingerprint="cathay:2026-09-10:aa", amounts=("-12299", "-6"))  # offsetting edit
    _reconcile(db_session, sid)
    (case,) = _cases(db_session, sid, "open")
    assert (case.kind, case.context["hint"]) == ("settlement", "source_drift") and len(_active(db_session, sid)) == 1


def test_owner_twin_retires_an_unclaimed_stand_in_at_reconcile(seed, db_session, run, portfolio_stub):
    bank, target = settlement_bank(seed, db_session)
    booking = seed_stand_in(db_session, bank, target, settlement_date=date(2026, 9, 14), net="-12305", fingerprint="cathay:2026-09-10:aa", ids=("ct:5",))
    _transfer(seed, bank, target, Decimal("12305"), date(2026, 9, 14))
    submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
    db_session.refresh(booking)
    assert (booking.status, booking.reason, booking.out_entry_id) == ("retired", "twin", None)


def test_a_new_statement_flags_its_live_neighbour_for_recheck(seed, db_session, run, portfolio_stub):
    bank, _ = settlement_bank(seed, db_session)
    aug = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 8, 31), "-5000")], period=AUG)
    submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 1), "-5000")])
    assert db_session.get(AccountStatement, aug).needs_recheck is True


def test_a_window_past_the_calendar_assigns_nothing(seed, db_session, run, portfolio_stub):
    bank, _ = settlement_bank(seed, db_session)
    portfolio_stub.rows = feed(("2028-01-04", "2028-01-06", "-1000", ("ct:5",)))
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2028, 1, 6), "-1000")], period=(date(2028, 1, 1), date(2028, 1, 31)))
    result = _reconcile(db_session, sid)
    assert portfolio_stub.calls == [] and result.settlements["unavailable_code"] == "calendar"                       # no feed call without a provable bound
    assert _cases(db_session, sid, "open")[0].context["hint"] == "settlement_pending"


def test_a_long_holiday_settlement_is_fetched_by_settlement_date(seed, db_session, run, portfolio_stub):
    bank, _ = settlement_bank(seed, db_session)
    portfolio_stub.rows = feed(("2026-02-13", "2026-02-24", "-12305", ("ct:5",)),                # last session before 春節, printed date
                               ("2026-02-23", None, "-12305", ("ct:6",), {"date_source": "legs"}))  # a same-amount CSV-only day, T+2 = 02-25
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 2, 24), "-12305")], period=(date(2026, 2, 24), date(2026, 3, 23)))
    (case,) = _cases(db_session, sid, "open")
    assert (case.kind, case.context["hint"]) == ("ambiguous", "settlement_ambiguous") and len(case.candidates) == 2   # both visible → never a silent single booking
    assert portfolio_stub.calls[-1]["settle_from"] == "2026-02-19" and portfolio_stub.calls[-1]["from"] == "2026-02-12"  # T+2(02-12) = 02-23 ≥ 02-19, T+2(02-11) = 02-13 < 02-19: from business_days, not a constant


def test_an_incomplete_feed_answer_assigns_nothing(seed, db_session, run, portfolio_stub):
    bank, _ = settlement_bank(seed, db_session)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",))); portfolio_stub.narrow = True
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
    result = _reconcile(db_session, sid)
    (case,) = _cases(db_session, sid, "open")
    assert (case.context["hint"], result.settlement_source_unavailable, result.settlements["unavailable_code"]) == ("settlement_pending", True, "incomplete")


def test_undated_rows_are_counted_through_to_the_result(seed, db_session, run, portfolio_stub):
    bank, _ = settlement_bank(seed, db_session)
    portfolio_stub.rows = feed(("2027-12-29", None, "-1000", ("ct:5",), {"date_source": "legs"}))   # T+2 passes LAST_KNOWN_DAY 2027-12-31 → None
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2027, 12, 26), "-1000")], period=(date(2027, 12, 1), date(2027, 12, 26)))   # settle_hi = 12-31, still covered (B3)
    result = _reconcile(db_session, sid)
    assert result.settlements["unknown_dates"] == 1 and _cases(db_session, sid, "open")[0].context["hint"] == "settlement_pending"


def test_a_covered_neighbour_line_still_contests(seed, db_session, run, portfolio_stub):
    bank, target = settlement_bank(seed, db_session)
    _transfer(seed, bank, target, Decimal("5000"), date(2026, 9, 30))                            # the owner recorded September's line
    portfolio_stub.rows = feed(("2026-09-29", "2026-10-01", "-5000", ("ct:5",)))
    sep = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 30), "-5000")])      # claimed by the owner twin → covered
    assert _cases(db_session, sep, "open") == []
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 10, 2), "-5000")], period=OCT)
    (case,) = _cases(db_session, sid, "open")
    assert (case.kind, case.context["hint"]) == ("ambiguous", "settlement_ambiguous")            # the covered 09-30 line contests 10-01


def test_date_only_drift_on_a_claimed_row_opens_source_drift(seed, db_session, run, portfolio_stub):
    bank, target = settlement_bank(seed, db_session)
    seed_stand_in(db_session, bank, target, settlement_date=date(2026, 9, 14), net="-12305", fingerprint="cathay:2026-09-10:aa", ids=("ct:5",))
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)), fingerprint="cathay:2026-09-10:aa")
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-15", "-12305", ("ct:5",)), fingerprint="cathay:2026-09-10:aa")   # only the printed date moved
    _reconcile(db_session, sid)
    (case,) = _cases(db_session, sid, "open")
    assert (case.kind, case.context["hint"], case.context["now"]["settlement_date"]) == ("settlement", "source_drift", "2026-09-15")
    assert len(_active(db_session, sid)) == 1 and db_session.scalar(select(SettlementBooking.status)) == "booked"      # the pair stays


def test_reconcile_tombstones_an_owner_deleted_bank_booking_before_reserving(seed, db_session, run, portfolio_stub):
    bank, target = settlement_bank(seed, db_session)
    booking = seed_stand_in(db_session, bank, target, settlement_date=date(2026, 9, 14), net="-12305", fingerprint="cathay:2026-09-10:aa", ids=("ct:5",), origin="bank")
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)), fingerprint="cathay:2026-09-10:aa")
    ews.delete_entry(db_session, booking.out_entry_id); db_session.commit()
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])     # no job ran
    db_session.refresh(booking)
    (case,) = _cases(db_session, sid, "open")
    assert (booking.status, booking.items) == ("owner_deleted", []) and (case.kind, case.context["owner_deleted"]) == ("settlement", True)


def test_a_retired_or_historical_neighbours_line_is_not_in_the_graph(seed, db_session, run, portfolio_stub):
    bank, _ = settlement_bank(seed, db_session)
    portfolio_stub.rows = feed(("2026-09-29", "2026-10-01", "-5000", ("ct:5",)))
    old = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 30), "-5000")])      # a September line that would contest 10-01
    statement_retire_service.retire_statement(db_session, account_id=bank.id, statement_id=old, note="t", label="owner-test"); db_session.commit()
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 10, 2), "-5000")], period=OCT)
    assert [c.kind for c in _cases(db_session, sid, "open")] == ["settlement"]                 # the retired statement's line does not contest (B4)


def test_neighbour_lines_at_the_window_edge_are_in_the_graph(seed, db_session, run, portfolio_stub):
    bank, _ = settlement_bank(seed, db_session)
    portfolio_stub.rows = feed(("2026-09-29", "2026-10-01", "-5000", ("ct:5",)))
    sep = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 30), "-5000")])      # posted within the window of 10-01
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 10, 2), "-5000")], period=OCT)
    assert {c.context.get("hint") for c in _cases(db_session, sid, "open")} == {"settlement_ambiguous"}
    assert db_session.get(AccountStatement, sep).needs_recheck is True                          # the hook only flags September …
    _reconcile(db_session, sep)                                                                 # … its own reconcile sees October's line (B6)
    assert {c.context.get("hint") for c in _cases(db_session, sep, "open")} == {"settlement_ambiguous"}
```

`AUG = (date(2026, 8, 1), date(2026, 8, 31))` is defined in `settlement_fixtures`; `OCT` is `statement_fixtures.OCT`; `portfolio_stub.calls` records each request's query params. `test_inbox_service.py` (+2), on the file's `ib.classify(kind=..., status=..., statement_kind=..., hint=..., merchant_norm=..., allowed=...)` shape (`:32-33`):

```python
@pytest.mark.parametrize("kind,hint", [("settlement", None), ("ambiguous", "settlement_ambiguous"), ("settlement", "source_drift")])
def test_settlement_cases_classify_as_settlement(kind, hint):
    assert ib.classify(kind=kind, status="open", statement_kind="bank", hint=hint, merchant_norm="交割股款", allowed=["book_settlement", "dismiss"]) == "settlement"


def test_fingerprint_candidates_are_skipped_not_raised():
    case = ReconciliationCase(kind="settlement", status="open", candidates=[{"fingerprint": "cathay:2026-09-10:aa", "net_amount": "-12305.0000", "items": []}], context={})
    assert ib._single_candidate(case) is None and ib._listed_candidates(case, {}, {}) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/integration/test_settlement_reconcile.py tests/unit/test_inbox_service.py`
Expected: FAIL

- [ ] **Step 3: Implement `trade_settlement_service` (reconcile half), `pair_locks`, the `stand_ins` additions, the `reconcile` wiring, the revision-hook flagging, the result fields and the inbox mapping** — the task text above is the spec for this step; the tests are its acceptance, not its definition (C6).

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
- Modify: `services/accounting-service/app/services/reconciliation_actions/__init__.py:24-37,126-141` (`KIND_ACTIONS["settlement"] = ("book_settlement", "dismiss")` — no `match`: a `settlement` case's candidates carry fingerprints, not `entry_id` (M5); `"ambiguous"` += `book_settlement`; `CONDITIONS[("ambiguous", "book_settlement")]` = hint `settlement_ambiguous`)
- Modify: `services/accounting-service/app/services/stand_ins.py:23` (`OWNER_TWIN_GUARDED` += `book_settlement`)
- Modify: `services/accounting-service/app/services/policy_service.py` (`_settlement_candidate`; `POLICY_ACTIONS` `:353`; `OWNER_DELETED_GUARDED` `:351`; `collect_candidates` kinds `:398-400` += `settlement`; `CAP_EXEMPT`; `_applied_totals` `:465`; `_cap_reason` `:478`; `_amount_now` `:489`; `_prefilter` `:634`; `apply_candidate` `:657`)
- Modify: `services/accounting-service/app/services/settings_service.py` (`POLICY_DEFAULTS["actions"]["book_settlement"] = {"enabled": True, "max_amount": "0"}` `:118-131`; the `settlement` block: `SETTLEMENT_DEFAULTS`, `_check_settlement`, `read_settlement_settings`, wired into `update_reconciliation_settings` `:447` and `_reconciliation_dict` like `auto_pay` `:147`, `:327`)
- Modify: `services/accounting-service/app/schemas/statements.py:415-426` (`ReconciliationSettingsIn.settlement: dict = {}`, after `auto_pay` `:426`)
- Modify: `services/accounting-service/tests/integration/settlement_fixtures.py` (`settlement_bank(target=True)`, `policy_on`; M12)
- Test: `services/accounting-service/tests/integration/test_auto_pay_settings.py` (+2: an unknown `settlement` key → 422; `stand_in_enabled: true` without `enabled_from` → 422), `services/accounting-service/tests/integration/test_settlement_actions.py` (helpers `_apply` / `_revert` / `_refuse` / `_code` / `_fresh` from `test_reconciliation_actions_ledger.py`; fixtures from `settlement_fixtures.py`)

**Interfaces:**
- Consumes: Task 3 models, Task 6 cases and `trade_settlement_service.fetch_by_trade_date`; `_TwinGuarded` (`ledger_actions.py:132-169`: `lock_targets` = `LockSet(account_lock=True, barrier=True)`, `check_before_locks`, `_twin_check`); the `PairTransferAction.apply` / `invert` sequence (`transfer_actions.py:434-478`: `transfer_service.create_transfer(db, TransferIn(...), source=STATEMENT_SOURCE, remember=False)`, `transfer_legs`, claim, `_release` → `ews.delete_entries_cascade`); `engine._check_creation_family` (`engine.py:313`, slot `CREATE`).
- Produces: `BookSettlementParams(fingerprint: str)`; `BookSettlementAction(_TwinGuarded)`, `name = "book_settlement"`:
  - `prepare` / `apply`: the fingerprint must be one of the case's candidates; the settlement is re-fetched with `fetch_by_trade_date(trade_date, trade_date)` (the trade axis returns the day's row whatever its settlement date, so a normal statement day is found) and the row with the candidate's fingerprint must still equal it (net, settlement date, `(source_id, amount)` set, `bookable`, `date_source`), else `candidate_changed` (AGENT-360 major 3); the statement must not be `needs_recheck` (`needs_recheck`); `_twin_check(line)`; a `booked` row for the same event → nothing new (replay); a `booked` row with this fingerprint for another event → `already_booked`; then, under the engine's account lock + barrier, the booking-row → legs NOWAIT step for any `booked` row of this fingerprint or day that is about to be read (`lock_pair`; held → 409 `settlement_busy`; M11), then the transfer (bank → target for a negative net, target → bank for a positive one; `out_amount = |line.flow|`; dates = the line's posted date; name `證券交割`; description `證券交割 <trade_date>`; `needs_review` stays the `create_transfer` default False), the booking row (`kind settlement`, `origin bank`, `event_id`, `facts` = the candidate, `action_id`) and its items; the bank leg claims the line (`match_rule = "book_settlement"`). An `IntegrityError` on `ux_settlement_booking_item_source` → `constituent_reserved`, on `ux_settlement_booking_fingerprint_booked` → `already_booked`, on `ux_settlement_booking_day_booked` → `day_booked` (the savepoint rolls back; the session stays usable).
  - `invert`: the engine already holds the legs NOWAIT (`lock_rows`, engine.py:482); the booking row is then locked `FOR UPDATE NOWAIT` — a reverse-order try-lock, held → 409 `settlement_busy`, never a wait (M11) — then `_release` → `delete_entries_cascade` of both legs → the booking row `retired` (reason `revert`) with its items deleted → reopen as `line_unmatched` + `{"hint": "settlement_pending"}` with `candidates = []`; the engine's snapshot gate refuses a revert after either leg changed.
- Settings: `SETTLEMENT_DEFAULTS = {"stand_in_enabled": False, "enabled_from": None, "target_accounts": {}, "twin_window_days": 3, "dividend_category_id": None}`; `_check_settlement(block) -> dict` (`enabled_from` ISO date, required when `stand_in_enabled`; `target_accounts` maps an account id string to an account id, both existing, same currency; `twin_window_days` int 0–15; `dividend_category_id` int or None); `read_settlement_settings(db) -> dict` (block + settings `version`).
- Target account: `read_settlement_settings(db)["target_accounts"][str(statement.account_id)]`; missing → refused `no_target`.
- `settlement_fixtures.settlement_bank` gains a `target=True` default that writes `settlement.target_accounts = {str(bank.id): target.id}` through `update_reconciliation_settings` (Task 6 tests are unaffected).
- Policy: `CAP_EXEMPT = frozenset({"book_settlement", "book_dividend"})`; `_settlement_candidate(db, case, statement) -> Candidate | None` (open `settlement` case without a hint, `amount = Decimal("0.0000")`, params `{"fingerprint": case.context["fingerprint"]}`); `_cap_reason` returns None for `CAP_EXEMPT`; `_applied_totals` skips `CAP_EXEMPT` rows (count and amount); `_amount_now` returns `0.0000` for them; `apply_candidate` passes `budget=None` for them (no reservation, no charge, no `policy_budget` in `after`); `_prefilter` returns `("propose", None, "owner_deleted")` for a candidate whose case context has `owner_deleted: true`.

- [ ] **Step 1: Write the failing tests**

```python
def test_book_settlement_books_two_legs_one_owner_and_reverts(seed, db_session, run, portfolio_stub):
    bank, target = settlement_bank(seed, db_session)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5", "ct:7")), amounts=("-12300", "-5"))
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
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
    bank, _ = settlement_bank(seed, db_session)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-999999", ("ct:5",)))
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-999999")])
    policy_service.run_policy(db_session, sid)
    assert db_session.scalar(select(SettlementBooking.status)) == "booked"
    assert db_session.scalar(select(func.coalesce(func.sum(PolicyBudget.used_count), 0))) == 0
    _reconcile(db_session, sid); policy_service.run_policy(db_session, sid)                 # idempotent
    assert db_session.scalar(select(func.count(SettlementBooking.id))) == 1


def _case_and_booking(seed, db_session, run, portfolio_stub, *, net="-12305", ids=("ct:5",)):
    """One live bank statement with one settlement line and its unique feed row; returns (bank, target, sid, case)."""
    bank, target = settlement_bank(seed, db_session)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", net, ids))
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), net)])
    (case,) = _cases(db_session, sid, "open")
    return bank, target, sid, case


def test_policy_proposes_an_owner_deleted_settlement(seed, db_session, run, portfolio_stub, policy_on):
    policy_on()
    bank, target, sid, case = _case_and_booking(seed, db_session, run, portfolio_stub)
    row = _apply(db_session, case.id, "book_settlement", {"fingerprint": case.context["fingerprint"]})
    booking = db_session.scalar(select(SettlementBooking))
    ews.delete_entry(db_session, booking.out_entry_id); db_session.commit()                    # the owner deletes the pair (both legs go)
    _reconcile(db_session, sid); policy_service.run_policy(db_session, sid)                     # reconcile's housekeep tombstones the row; no job run
    (case,) = _cases(db_session, sid, "open", "proposed")
    assert db_session.get(SettlementBooking, booking.id).status == "owner_deleted"
    assert (case.kind, case.context["owner_deleted"], case.status) == ("settlement", True, "proposed")
    assert db_session.scalar(select(func.count(SettlementBooking.id)).where(SettlementBooking.status == "booked")) == 0


def test_policy_refuses_when_an_owner_twin_appeared_under_the_barrier(seed, db_session, run, portfolio_stub, policy_on, monkeypatch):
    policy_on()
    bank, target, sid, case = _case_and_booking(seed, db_session, run, portfolio_stub)
    (cand,) = policy_service.collect_candidates(db_session, db_session.get(AccountStatement, sid), settings_service.reconciliation_policy(db_session))
    _transfer(seed, bank, target, Decimal("12305"), date(2026, 9, 14))                         # lands between the listing and the apply
    exc = _refuse(db_session, lambda: policy_service.apply_candidate(db_session, db_session.get(AccountStatement, sid), cand, month=date(2026, 9, 1)))
    assert _code(exc) == "candidate_changed" and db_session.scalar(select(func.count(SettlementBooking.id))) == 0


def test_another_event_with_the_same_fingerprint_is_already_booked(seed, db_session, run, portfolio_stub):
    bank, target, sid, case = _case_and_booking(seed, db_session, run, portfolio_stub)
    _apply(db_session, case.id, "book_settlement", {"fingerprint": case.context["fingerprint"]})
    other = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 10, 1), "-12305")], period=OCT)   # a look-alike line, next period
    (pending,) = _cases(db_session, other, "open")
    assert pending.context["hint"] == "settlement_pending"                                     # the booked fingerprint is reserved: never a candidate
    forced = ReconciliationCase(statement_id=other, line_id=pending.line_id, kind="settlement", status="open",   # a stale listing that still names it (B5)
                                candidates=[{**case.candidates[0], "settlement_date": "2026-10-01"}], context={**case.context})
    db_session.add(forced); db_session.commit()
    exc = _refuse(db_session, lambda: _apply(db_session, forced.id, "book_settlement", {"fingerprint": case.context["fingerprint"]}))
    assert _code(exc) == "already_booked" and db_session.scalar(select(func.count(SettlementBooking.id))) == 1


def test_owner_booking_from_an_ambiguous_case_reserves_the_constituents(seed, db_session, run, portfolio_stub):
    bank, target = settlement_bank(seed, db_session)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-5000", ("ct:1",)), ("2026-09-11", "2026-09-15", "-5000", ("ct:2",)))
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-5000"), settle_line(2, date(2026, 9, 16), "-5000")])
    first, second = _cases(db_session, sid, "open")
    _apply(db_session, first.id, "book_settlement", {"fingerprint": portfolio_stub.rows[0]["fingerprint"]})
    _reconcile(db_session, sid)
    (case,) = _cases(db_session, sid, "open")
    assert case.line_id == second.line_id and case.kind == "settlement" and case.candidates[0]["fingerprint"] == portfolio_stub.rows[1]["fingerprint"]


def test_a_fingerprint_sharing_a_booked_constituent_is_refused(seed, db_session, run, portfolio_stub):
    bank, target, sid, case = _case_and_booking(seed, db_session, run, portfolio_stub, net="-12305", ids=("ct:1", "ct:2"))
    _apply(db_session, case.id, "book_settlement", {"fingerprint": case.context["fingerprint"]})
    portfolio_stub.rows = feed(("2026-09-29", "2026-10-01", "-6000", ("ct:2",)), fingerprint="cathay:2026-09-29:x")   # a re-import moves ct:2 into an October row
    other = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 10, 1), "-6000")], period=OCT)
    (pending,) = _cases(db_session, other, "open")
    assert (pending.context["hint"], pending.candidates) == ("settlement_pending", [])          # inside the window, yet never a candidate: ct:2 is reserved (C2)
    forced = ReconciliationCase(statement_id=other, line_id=pending.line_id, kind="settlement", status="open",
                                candidates=[{**portfolio_stub.rows[0], "items": [{"source_id": "ct:2", "type": "buy_settle", "amount": "-6000.0000"}]}],
                                context={"fingerprint": "cathay:2026-09-29:x", "source": "portfolio:cathay"})
    db_session.add(forced); db_session.commit()
    exc = _refuse(db_session, lambda: _apply(db_session, forced.id, "book_settlement", {"fingerprint": "cathay:2026-09-29:x"}))
    assert _code(exc) == "constituent_reserved" and _fresh(db_session, SettlementBooking, 1).status == "booked"


@pytest.mark.parametrize("mutate", ["out_amount", "in_date", "child"])
def test_revert_is_refused_when_either_leg_was_edited(seed, db_session, run, portfolio_stub, mutate):
    bank, target, sid, case = _case_and_booking(seed, db_session, run, portfolio_stub)
    row = _apply(db_session, case.id, "book_settlement", {"fingerprint": case.context["fingerprint"]})
    booking = db_session.scalar(select(SettlementBooking))
    out_leg, in_leg = db_session.get(LedgerEntry, booking.out_entry_id), db_session.get(LedgerEntry, booking.in_entry_id)
    if mutate == "out_amount":
        out_leg.amount = Decimal("-12300")
    elif mutate == "in_date":
        in_leg.entry_date = date(2026, 9, 15)
    else:
        seed.entry(bank, "-1", kind="expense", parent_entry_id=out_leg.id)
    db_session.commit()
    exc = _refuse(db_session, lambda: _revert(db_session, row.id))
    assert _code(exc) == "snapshot_conflict" and _fresh(db_session, SettlementBooking, booking.id).status == "booked"


def test_revert_after_reconcile_reopens_pending(seed, db_session, run, portfolio_stub):
    bank, target, sid, case = _case_and_booking(seed, db_session, run, portfolio_stub)
    row = _apply(db_session, case.id, "book_settlement", {"fingerprint": case.context["fingerprint"]})
    _reconcile(db_session, sid)                                                               # the claim is owned coverage: kept
    assert [c.match_rule for c in _active(db_session, sid)] == ["book_settlement"]
    _revert(db_session, row.id)
    booking = db_session.scalar(select(SettlementBooking))
    (case,) = _cases(db_session, sid, "open")
    assert (booking.status, booking.out_entry_id, booking.in_entry_id, booking.items) == ("retired", None, None, [])
    assert (case.kind, case.context["hint"], case.candidates) == ("line_unmatched", "settlement_pending", [])
    _reconcile(db_session, sid)
    assert [c.kind for c in _cases(db_session, sid, "open")] == ["settlement"]                # re-derived: the bank path books again (spec §9)


def test_refused_while_the_statement_needs_recheck(seed, db_session, run, portfolio_stub):
    bank, target, sid, case = _case_and_booking(seed, db_session, run, portfolio_stub)
    db_session.get(AccountStatement, sid).needs_recheck = True; db_session.commit()
    exc = _refuse(db_session, lambda: _apply(db_session, case.id, "book_settlement", {"fingerprint": case.context["fingerprint"]}))
    assert _code(exc) == "needs_recheck"
```

`policy_on(**caps)` is a `settlement_fixtures` fixture writing the policy through `settings_service.update_reconciliation_settings` (`enabled: true`, `book_settlement` enabled, the named caps). The owner-delete path is `entry_write_service.delete_entry` on one leg (both legs go), as `tests/integration/test_auto_pay_service.py:423-429` does.

Also in `test_settlement_actions.py` (M11, needs `pg_engine`):

```python
def test_revert_is_settlement_busy_while_another_session_holds_the_booking_row(pg_engine, seed, db_session, run, portfolio_stub):
    bank, target, sid, case = _case_and_booking(seed, db_session, run, portfolio_stub)
    row = _apply(db_session, case.id, "book_settlement", {"fingerprint": case.context["fingerprint"]})
    with Session(pg_engine) as other:
        other.execute(select(SettlementBooking).where(SettlementBooking.action_id == row.id).with_for_update())   # a job mid-housekeeping
        exc = _refuse(db_session, lambda: _revert(db_session, row.id))
        other.rollback()
    assert _code(exc) == "settlement_busy" and _fresh(db_session, SettlementBooking, 1).status == "booked"   # nothing released
```

`test_auto_pay_settings.py` (+2), with the file's `_body(client, **auto_pay)` helper (`:15`) extended to take `settlement=None`:

```python
def test_unknown_settlement_key_is_refused(client):
    res = client.put("/settings/reconciliation", json=_body(client, settlement={"bogus": 1}))
    assert res.status_code == 422 and "settlement" in str(res.json()["detail"])


def test_stand_ins_need_a_go_live_date(client):
    res = client.put("/settings/reconciliation", json=_body(client, settlement={"stand_in_enabled": True, "enabled_from": None}))
    assert res.status_code == 422 and "enabled_from" in str(res.json()["detail"])
```

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
git commit -m "accounting: book_settlement — owner-twin-guarded bank↔target pair under one booking owner, zero-charged and cap-exempt"
```

---

### Task 8: Accounting — read-through valuation in the account read models (ledger balance untouched)

**Files:**
- Create: `services/accounting-service/app/services/valuation_service.py`
- Modify: `services/accounting-service/app/services/ledger_service.py:146-205` (`_account_rows`; `list_accounts` `:207` and `get_account` `:211` inherit)
- Modify: `services/accounting-service/app/schemas/ledger.py:44-50` (`AccountOut`)
- Test: `services/accounting-service/tests/integration/test_valuation_read.py` (the `client` fixture and headers of `test_accounts_api.py`; `seed.fx` `tests/conftest.py:487`; `statement_fixtures._submit` + the `run` fixture for the `held_balance` assertion)

**Interfaces:**
- Consumes: Task 4 `client_for`, `cached`, `PortfolioUnavailable`, `Valuation`.
- Produces: `valuation_service.valuation_for(db, account: Account, as_of: date) -> dict | None` (`{"value", "currency", "as_of", "priced_through", "complete", "missing", "stale"}`; 10-minute module cache keyed `(broker, as_of)`, `clear_cache()`; on `PortfolioUnavailable` the last cached value for the key — warm or expired — is served with `stale: True` and `balance` stays non-null; only a cold miss yields None (spec §5 "on a miss", M15); a broker currency other than the account's → None, logged by code). `AccountOut.balance: Decimal | None`, `balance_kind: Literal["ledger", "valuation"] = "ledger"`, `stale: bool = False`, `valuation: ValuationOut | None = None` (`value, currency, as_of, priced_through, complete, missing`). In `_account_rows`, for a `valuation_source` account: complete valuation → `balance = value`, `balance_main` by the same expression as `:195`; unavailable or incomplete → `balance = None`, `balance_main = None`; always `balance_kind = "valuation"`. Phase 1 serves TWD (= main currency) accounts only; a foreign valuation account (Phase 1b) reads None until its FX rule lands. `account_balance` (`:109`), `held_balance` (`reconciliation_service.py:719`), `period_summary` (`:731`), `_converted_totals` (`:607`), `entry_write_service:688`, `moze_backup_import_service:1248`: unchanged.

- [ ] **Step 1: Write the failing tests**

```python
def test_valuation_account_reads_the_portfolio_total_but_the_ledger_balance_stays(client, seed, db_session, valuation_stub):
    pos = seed.account("國泰持股", opening="0", valuation_source="portfolio:cathay")
    seed.entry(pos, "100000", kind="transfer_in", day=date(2026, 9, 1))
    valuation_stub.total = "123456"
    body = client.get(f"/accounts/{pos.id}").json()
    assert (body["balance"], body["balance_main"], body["balance_kind"], body["stale"]) == ("123456.0000", "123456.0000", "valuation", False)
    assert ledger_service.account_balance(db_session, pos.id) == Decimal("100000")


def test_historical_as_of_asks_for_that_date(client, seed, db_session, valuation_stub):
    pos = seed.account("國泰持股", valuation_source="portfolio:cathay")
    valuation_stub.total = "99000"
    body = client.get(f"/accounts/{pos.id}", params={"as_of": "2026-09-12"}).json()
    assert valuation_stub.seen == [("cathay", date(2026, 9, 12))] and body["valuation"]["as_of"] == "2026-09-12"


def test_cold_cache_with_portfolio_down_is_null_and_stale(client, seed, db_session, valuation_stub):
    pos = seed.account("國泰持股", valuation_source="portfolio:cathay")
    valuation_stub.down = True
    body = client.get(f"/accounts/{pos.id}").json()
    assert (body["balance"], body["balance_main"], body["balance_kind"], body["stale"], body["valuation"]) == (None, None, "valuation", True, None)
    listed = next(a for a in client.get("/accounts").json() if a["id"] == pos.id)
    assert (listed["balance"], listed["stale"]) == (None, True)


def test_incomplete_valuation_is_never_a_balance(client, seed, db_session, valuation_stub):
    pos = seed.account("國泰持股", valuation_source="portfolio:cathay")
    valuation_stub.total, valuation_stub.complete, valuation_stub.missing = "100000", False, ["9998"]
    body = client.get(f"/accounts/{pos.id}").json()
    assert (body["balance"], body["balance_main"], body["valuation"]["complete"], body["valuation"]["missing"]) == (None, None, False, ["9998"])


def test_ledger_only_readers_ignore_valuation(seed, db_session, valuation_stub):
    pos = seed.account("國泰持股", opening="0", valuation_source="portfolio:cathay")
    seed.entry(pos, "100000", kind="transfer_in", day=date(2026, 9, 1))
    valuation_stub.total = "123456"
    assert ledger_service.period_summary(db_session, pos.id, date(2026, 9, 1), date(2026, 9, 30))["end_balance"] == Decimal("100000")
    assert ledger_service.account_balance(db_session, pos.id) == Decimal("100000")
    stmt, _, _ = _submit(db_session, run, pos, [], kind="bank", period=(date(2026, 9, 1), date(2026, 9, 30)))
    assert reconciliation_service.held_balance(db_session, stmt, [pos.id]) == Decimal("100000")   # D2: the reconcile balance is a ledger figure


def test_warm_cache_with_portfolio_down_serves_the_stale_value(client, seed, db_session, valuation_stub):
    pos = seed.account("國泰持股", valuation_source="portfolio:cathay")
    valuation_stub.total = "123456"
    client.get(f"/accounts/{pos.id}")                                                           # warms the cache
    valuation_stub.down = True; valuation_service.expire_cache()                                # expired, not cleared
    body = client.get(f"/accounts/{pos.id}").json()
    assert (body["balance"], body["stale"]) == ("123456.0000", True)                             # M15: a known value beats null
```

(`valuation_service.expire_cache()` is a test seam that back-dates every cache entry past the TTL; `test_ledger_only_readers_ignore_valuation` takes the `run` fixture.)

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
- Modify: `services/accounting-service/tests/integration/settlement_fixtures.py` (`stand_ins_on`, `ingest_fixture`; M12)
- Test: `services/accounting-service/tests/integration/test_settlement_stand_ins.py`

**Interfaces:**
- Consumes: Task 3 models, Task 4 client, Task 6 `fetch_by_settlement_window` / `find_twins` / `lock_pair` / `retire_pair` / `housekeep`; `transfer_service.create_transfer` (`transfer_service.py:139`) and `transfer_legs` (`:164`); `take_import_key_shared`; the `auto_pay_job.run` shape (`auto_pay_job.py:206`: a session-level `pg_try_advisory_lock`, one transaction per candidate, `ImportRunningError` → stop with `import_running`).
- Consumes also: Task 7 `read_settlement_settings` (the `settlement` block).
- Produces in `trade_settlement_service`: `SETTLEMENT_JOB_LOCK_KEY` (a new advisory key, distinct from `AUTO_PAY_JOB_LOCK_KEY`); `@dataclass(frozen=True) StandInCandidate(account_id, target_account_id, settlement: Settlement)`.
  - `stand_in_candidates(db, rows: list[Settlement], account: Account, settings: dict, today: date) -> list[StandInCandidate]` (`date_source == "statement"`, `bookable`, `net_amount != 0`, `enabled_from <= settlement_date <= today`).
  - `gate(db, cand, settings) -> str | None` (in order: `disabled`; `net_amount == 0` → `zero`; a row for (account, settlement_date) with status `booked`, `owner_deleted`, `owner_edited`, or `retired` with reason `twin` or `revert` → `final` (exactly the spec §4.7 set; a `skipped` row is NEVER final — it is a record of the last verdict, re-evaluated every run, so an owner twin deleted later or a dismissed case reopened later lets the day book; M7 follows the spec); a `dismissed` reconciliation case of the account on a settlement line whose context fingerprint is the candidate's or whose line posted date is the settlement date → `dismissed` (controller ruling 2026-10-30, spec §4.7); a reserved constituent → `constituent_reserved`; a `booked` row for the day under another fingerprint → `day_booked` (M10); an owner twin (`find_twins` with the candidate's fields) → `twin`). `final` is reported in `skipped` and never recorded as a row (M7).
  - `book_stand_in(db, cand, settings) -> SettlementBooking` (no account row lock — the partial unique over `booked` rows on (account, settlement_date) serialises it against a concurrent `book_settlement`, the transfer + row + items are written inside `db.begin_nested()`; an `IntegrityError` rolls that savepoint back and maps the constraint to the skip reason — `ux_settlement_booking_day_booked` → `record_skip(reason="day_booked")`, `ux_settlement_booking_item_source` → `record_skip(reason="constituent_reserved")` (the Task 7 split, same codes; M10); the transfer of Task 7 dated the settlement date, `origin stand_in`, `event_id None`, items, audit `settlement_booked`); `record_skip(db, account: Account, settlement: Settlement, reason: str) -> SettlementBooking` (takes the feed row, not a `StandInCandidate`, so ineligible rows can be recorded too; one `skipped` row per (account, settlement_date, reason), audit `settlement_skipped` on change); `ineligibility(settlement, settings, today) -> str | None` names why `stand_in_candidates` would drop a row: `not_statement_dated`, `not_bookable`, `zero`, `pre_go_live`, `future` (M9).
  - `check_row(db, row, feed: dict[date, Settlement], settings, today) -> str | None` (`feed` keyed by trade date; the row is looked up by `(row.source, row.trade_date)` — the account's single `settlement_source` and the booking's trade day — never by the stored `fingerprint`, which is provenance only) — for `booked` rows of `origin = stand_in` whose bank leg is UNCLAIMED on the unlocked read `unclaimed_stand_ins(db, account_id) -> set[int]` (called by `check_row` itself as a module global of `trade_settlement_service` — no default argument, partial or by-name import — so the Task 9 monkeypatch replaces it) (booking ids with no active coverage on either leg; claimed rows and bank-origin rows are `housekeep`'s and reconcile's business), in order: an owner twin → `retire_pair(reason="twin")` → `retired`; no feed row for that trade date, or the row no longer `bookable` → `retire_pair(reason=<feed reason or "gone">)` → `retired`; the trade day's row present but its net / settlement date / items (hence fingerprint) / `date_source` differ from `facts` → `replace(db, row, new, settings, today)` → `replaced` or `replaced_waiting` — a fingerprint change on the same trade day is a replacement inside one transaction, never "gone" + a rebuild in another (AGENT-360 re-check minor 4). Every retirement re-reads the claim under `lock_pair` (Task 6): a claim that landed since the unlocked read → `retire_pair` returns None → `check_row` returns None, the pair stays (AGENT-360 major 2). `SETTLEMENT_BUSY` → logged by ids, the row is left for the next run.
  - `replace(db, row, new: Settlement, settings, today) -> str` — `retire_pair(row, reason="replaced")` (status `replaced`; None → return None, nothing else happens), then the new facts go through `stand_in_candidates([new], …)` and `gate` again: eligible → `book_stand_in` in the same transaction → `replaced`; not eligible → `record_skip(account, new, ineligibility(new, …) or gate(…))` (`future`, `pre_go_live`, `not_bookable`, `zero`, `final`, `dismissed`, `twin`, `constituent_reserved`, `day_booked` on the new date; M9) → `replaced_waiting`: the old pair is gone, the day waits for a legal date or a bank line (AGENT-360 major 5). Audit `settlement_replaced` with both fingerprints.
  - `run_stand_ins(engine, *, today: date | None = None, dry_run: bool = False) -> dict` (report `{"status": completed|busy|disabled|source_unavailable|import_running|dry_run, "booked": [{"account_id", "settlement_date", "booking_id"}], "skipped": [{"account_id", "settlement_date", "reason"}], "housekeeping": dict.fromkeys(HOUSEKEEPING_KEYS, 0) with `HOUSEKEEPING_KEYS = ("checked" — every booked stand-in row looked at, claimed or not; claimed rows count here and nowhere else —, "owner_deleted", "owner_edited", "retired", "replaced", "replaced_waiting", "settlement_busy", "failed")` (every key present, zero-filled, the `auto_pay_job` :72 shape; re-verify 2 S3), "touched_dates": {account_id: [dates]}}` — `touched_dates` = the settlement dates of rows the run booked, retired (any reason) or replaced (both the old and the new date) and of every `replaced_waiting` skip (spec §4.5 "booked, replaced or retired"; M13); ids, dates, codes, never amounts). Per `settlement_source` account with a target, one `fetch_by_settlement_window(db, account, settle_from, today)` with `settle_from = min(enabled_from, oldest booked stand-in's settlement_date)` (statement days are found by their persisted date, so a statement that arrives late for an old trade day — a held day fixed weeks later — is picked up on the next run as long as its settlement date is on or after go-live; CSV-only days are irrelevant to the job; no `enabled_from − N` or rolling-14-day constant), then `housekeep(account_id)`, then `check_row` over its `booked` stand-ins (`feed = {r.trade_date: r for r in feed.rows}`, lookup by the row's source + trade date), then the candidates. Each decision runs in its own transaction: shared import key → (booking row → legs NOWAIT for a retirement or replacement) → write → commit — no account row lock (Task 6 `lock_pair`); `PortfolioUnavailable` → `source_unavailable`, nothing written; a dry run takes no import key and rolls every transaction back.
- Departures from the auto-pay pattern, stated once: (1) no `pay_account_id` / statement header — the candidate is a feed row, so `header_changed` has no analogue; (2) the pair can be replaced while unclaimed, auto-pay's never is; (3) retirement for a source reason refuses a claimed pair, auto-pay's twin retirement releases the claim (kept here for twins only); (4) `housekeep` also runs inside `reconcile` because bank-origin rows have no job; (5) like auto-pay, nothing here takes the account row — the engine_locks invariant (blocking account lock, takers hold no entry/statement lock) would otherwise close the two cycles spec §4.7 Locks (a)/(b) describes; the job advisory lock and the partial unique do the serialising.

- [ ] **Step 1: Write the failing tests**

```python
# tss = app.services.trade_settlement_service
def test_job_books_once_per_settlement_day(pg_engine, seed, db_session, portfolio_stub, stand_ins_on):
    bank, target = settlement_bank(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-01")
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    first = tss.run_stand_ins(pg_engine, today=date(2026, 9, 14))
    again = tss.run_stand_ins(pg_engine, today=date(2026, 9, 15))
    (row,) = db_session.scalars(select(SettlementBooking)).all()
    assert (len(first["booked"]), again["booked"], row.origin, row.settlement_date) == (1, [], "stand_in", date(2026, 9, 14))
    out = db_session.get(LedgerEntry, row.out_entry_id)
    assert (out.account_id, out.amount, out.entry_date, out.source) == (bank.id, Decimal("-12305"), date(2026, 9, 14), "statement")


def test_job_skips_future_margin_csv_only_and_pre_go_live_days(pg_engine, seed, db_session, portfolio_stub, stand_ins_on):
    bank, target = settlement_bank(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-10")
    portfolio_stub.rows = feed(("2026-09-04", "2026-09-08", "-1000", ("ct:1",)),                          # before go-live
                               ("2026-09-11", "2026-09-15", "-2000", ("ct:2",), {"bookable": False, "reason": "margin_day"}),
                               ("2026-09-11", None, "-3000", ("ct:3",), {"date_source": "legs"}),       # CSV-only
                               ("2026-09-14", "2026-09-16", "-4000", ("ct:4",)))                        # settles tomorrow
    assert tss.run_stand_ins(pg_engine, today=date(2026, 9, 15))["booked"] == []


def test_zero_net_statement_day_gets_no_stand_in(pg_engine, seed, db_session, portfolio_stub, stand_ins_on):
    bank, target = settlement_bank(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-01")
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "0", ("ct:5",)))                    # a defensive row the feed should never send
    report = tss.run_stand_ins(pg_engine, today=date(2026, 9, 14))
    assert report["booked"] == [] and db_session.scalar(select(func.count(LedgerEntry.id))) == 0


def _booked(pg_engine, seed, db_session, portfolio_stub, stand_ins_on, **row):
    bank, target = settlement_bank(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-01")
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)), **row)
    tss.run_stand_ins(pg_engine, today=date(2026, 9, 14))
    return bank, target, db_session.scalar(select(SettlementBooking))


def _entries(db):
    return db.scalar(select(func.count(LedgerEntry.id)))


def test_owner_twin_first_skips_the_stand_in(pg_engine, seed, db_session, portfolio_stub, stand_ins_on):
    bank, target = settlement_bank(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-01")
    _transfer(seed, bank, target, Decimal("12305"), date(2026, 9, 13))                          # the owner recorded it a day early
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    tss.run_stand_ins(pg_engine, today=date(2026, 9, 14)); tss.run_stand_ins(pg_engine, today=date(2026, 9, 15))
    rows = db_session.scalars(select(SettlementBooking)).all()
    assert [(r.status, r.reason) for r in rows] == [("skipped", "twin")] and _entries(db_session) == 2       # only the owner's legs


def test_reposted_statement_replaces_only_an_unclaimed_stand_in(pg_engine, seed, db_session, run, portfolio_stub, stand_ins_on):
    bank, target, old = _booked(pg_engine, seed, db_session, portfolio_stub, stand_ins_on)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12310", ("ct:5", "ct:9")))          # a held day imported later: new net
    tss.run_stand_ins(pg_engine, today=date(2026, 9, 15))
    db_session.expire_all()
    rows = {r.status: r for r in db_session.scalars(select(SettlementBooking))}
    assert rows["replaced"].id == old.id and rows["booked"].net_amount == Decimal("-12310") and _entries(db_session) == 2
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12310")])     # the bank line claims the new one
    assert [c.match_rule for c in _active(db_session, sid)] == ["settlement_stand_in"]
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12310", ("ct:5", "ct:9")), amounts=("-12300", "-10"))  # offsetting edit after the claim
    report = tss.run_stand_ins(pg_engine, today=date(2026, 9, 16))
    assert report["housekeeping"] == _hk(checked=1) and _entries(db_session) == 2               # claimed: the job leaves it
    _reconcile(db_session, sid)
    assert {(c.kind, c.context.get("hint")) for c in _cases(db_session, sid, "open")} == {("settlement", "source_drift")}


def test_feed_row_no_longer_bookable_retires_an_unclaimed_stand_in(pg_engine, seed, db_session, portfolio_stub, stand_ins_on):
    bank, target, row = _booked(pg_engine, seed, db_session, portfolio_stub, stand_ins_on)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",), {"bookable": False, "reason": "legs_disagree"}))
    tss.run_stand_ins(pg_engine, today=date(2026, 9, 15))
    db_session.refresh(row)
    assert (row.status, row.reason, row.out_entry_id, _entries(db_session)) == ("retired", "legs_disagree", None, 0)


def test_owner_deleted_stand_in_is_never_rebooked(pg_engine, seed, db_session, run, portfolio_stub, stand_ins_on, policy_on):
    policy_on()
    bank, target, row = _booked(pg_engine, seed, db_session, portfolio_stub, stand_ins_on)
    ews.delete_entry(db_session, row.out_entry_id); db_session.commit()                         # the owner reverts the stand-in (both legs go)
    tss.run_stand_ins(pg_engine, today=date(2026, 9, 15)); tss.run_stand_ins(pg_engine, today=date(2026, 9, 16))
    db_session.expire_all()
    assert [r.status for r in db_session.scalars(select(SettlementBooking))] == ["owner_deleted"] and _entries(db_session) == 0
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])     # the monthly statement arrives
    policy_service.run_policy(db_session, sid)
    (case,) = _cases(db_session, sid, "open", "proposed")
    assert (case.kind, case.status, case.context["owner_deleted"]) == ("settlement", "proposed", True) and _entries(db_session) == 0


def test_claim_wins_over_a_concurrent_replacement(pg_engine, seed, db_session, run, portfolio_stub, stand_ins_on, monkeypatch):
    bank, target, row = _booked(pg_engine, seed, db_session, portfolio_stub, stand_ins_on)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12310", ("ct:5", "ct:9")))          # the job will want to replace
    feed_map = {r.trade_date: r for r in tss.fetch_by_settlement_window(db_session, bank, date(2026, 9, 1), date(2026, 9, 16)).rows}
    stale = tss.unclaimed_stand_ins(db_session, bank.id)                                        # the job's unlocked read, taken BEFORE the claim
    assert stale == {row.id}
    monkeypatch.setattr(tss, "unclaimed_stand_ins", lambda db, account_id: stale)               # freeze it: the race window, made deterministic
    with Session(pg_engine) as other:                                                           # session B: the monthly statement claims and commits inside the window
        other_run = ingest_fixture(other)
        portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
        sid = submit_bank(other, other_run, other.get(Account, bank.id), [settle_line(1, date(2026, 9, 14), "-12305")]); other.commit()
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12310", ("ct:5", "ct:9")))
    assert tss.check_row(db_session, db_session.get(SettlementBooking, row.id), feed_map, _settings(db_session), date(2026, 9, 16)) is None   # only the locked re-read in retire_pair can see the claim now
    db_session.commit(); db_session.refresh(row)
    assert row.status == "booked" and _entries(db_session) == 2 and [c.match_rule for c in _active(db_session, sid)] == ["settlement_stand_in"]
    _reconcile(db_session, sid)
    assert {c.context.get("hint") for c in _cases(db_session, sid, "open")} == {"source_drift"}   # the change is reconcile's to show


def test_job_skips_a_pair_whose_leg_another_session_holds(pg_engine, seed, db_session, portfolio_stub, stand_ins_on):
    bank, target, row = _booked(pg_engine, seed, db_session, portfolio_stub, stand_ins_on)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",), {"bookable": False, "reason": "legs_disagree"}))
    with Session(pg_engine) as other:
        other.execute(select(LedgerEntry).where(LedgerEntry.id == row.out_entry_id).with_for_update())   # a reconcile holding the leg
        report = tss.run_stand_ins(pg_engine, today=date(2026, 9, 15))
        other.rollback()
    assert report["housekeeping"] == _hk(checked=1, settlement_busy=1) and _fresh(db_session, SettlementBooking, row.id).status == "booked"
    assert tss.run_stand_ins(pg_engine, today=date(2026, 9, 15))["housekeeping"] == _hk(checked=1, retired=1)      # next run: retired


def test_replacement_to_a_future_date_waits(pg_engine, seed, db_session, portfolio_stub, stand_ins_on):
    bank, target, row = _booked(pg_engine, seed, db_session, portfolio_stub, stand_ins_on)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-17", "-12305", ("ct:5",)))                # the printed date moved to the future
    report = tss.run_stand_ins(pg_engine, today=date(2026, 9, 15))
    db_session.expire_all()
    assert report["housekeeping"] == _hk(checked=1, replaced_waiting=1) and _entries(db_session) == 0
    assert sorted(r.status for r in db_session.scalars(select(SettlementBooking))) == ["replaced", "skipped"]
    assert tss.run_stand_ins(pg_engine, today=date(2026, 9, 17))["booked"][0]["settlement_date"] == "2026-09-17"   # booked on its day


def test_replacement_to_a_dismissed_date_is_final(pg_engine, seed, db_session, run, portfolio_stub, stand_ins_on):
    bank, target = settlement_bank(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-01")
    portfolio_stub.rows = feed(("2026-09-11", "2026-09-15", "-7000", ("ct:6",)))
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 15), "-7000")])
    (case,) = _cases(db_session, sid, "open"); _apply(db_session, case.id, "dismiss", {"note": "owner says no"})
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    tss.run_stand_ins(pg_engine, today=date(2026, 9, 16))                                       # books 09-14
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-15", "-12305", ("ct:5",)))                # re-posted onto the dismissed day
    report = tss.run_stand_ins(pg_engine, today=date(2026, 9, 16))
    assert report["housekeeping"] == _hk(checked=1, replaced_waiting=1) and report["skipped"][0]["reason"] == "dismissed" and _entries(db_session) == 0


def test_dismissed_case_or_reverted_bank_booking_is_final_for_the_job(pg_engine, seed, db_session, run, portfolio_stub, stand_ins_on):
    bank, target = settlement_bank(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-01")
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)), ("2026-09-11", "2026-09-15", "-7000", ("ct:6",)))
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305"), settle_line(2, date(2026, 9, 15), "-7000")])
    first, second = _cases(db_session, sid, "open")
    _apply(db_session, first.id, "dismiss", {"note": "owner says no"})                          # a dismissed settlement case
    row = _apply(db_session, second.id, "book_settlement", {"fingerprint": second.context["fingerprint"]})
    _revert(db_session, row.id)                                                                 # a reverted bank booking
    report = tss.run_stand_ins(pg_engine, today=date(2026, 9, 16))
    assert report["booked"] == [] and sorted(s["reason"] for s in report["skipped"]) == ["dismissed", "final"]
    assert db_session.scalar(select(func.count(SettlementBooking.id)).where(SettlementBooking.status == "booked")) == 0


def test_owner_edited_leg_is_left_alone(pg_engine, seed, db_session, portfolio_stub, stand_ins_on, client):
    bank, target, row = _booked(pg_engine, seed, db_session, portfolio_stub, stand_ins_on)
    res = client.put(f"/transfers/{row.transfer_group_id}", headers=S, json={                   # PUT /transfers/{group_id} (routers/transfers.py:29)
        "from_account_id": bank.id, "to_account_id": target.id, "out_amount": "12305", "entry_date": "2026-09-14", "description": "我改過了"})
    assert res.status_code == 200                                                               # an owner edit: the legs' source leaves `statement`
    tss.run_stand_ins(pg_engine, today=date(2026, 9, 15))
    db_session.refresh(row)
    assert (row.status, _entries(db_session)) == ("owner_edited", 2)


def test_job_with_portfolio_down_changes_nothing(pg_engine, seed, db_session, portfolio_stub, stand_ins_on):
    bank, target, row = _booked(pg_engine, seed, db_session, portfolio_stub, stand_ins_on)
    portfolio_stub.down = True
    report = tss.run_stand_ins(pg_engine, today=date(2026, 9, 15))
    db_session.refresh(row)
    assert (report["status"], row.status, _entries(db_session)) == ("source_unavailable", "booked", 2)


def test_dry_run_writes_nothing(pg_engine, seed, db_session, portfolio_stub, stand_ins_on):
    bank, target = settlement_bank(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-01")
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    report = tss.run_stand_ins(pg_engine, today=date(2026, 9, 14), dry_run=True)
    assert (report["status"], len(report["booked"])) == ("dry_run", 1)
    assert db_session.scalar(select(func.count(SettlementBooking.id))) == 0 and _entries(db_session) == 0


def test_stand_ins_are_outside_the_policy_caps(pg_engine, seed, db_session, run, portfolio_stub, stand_ins_on, policy_on):
    policy_on(monthly_total="0", per_statement_count=0)
    assert settings_service.reconciliation_policy(db_session)["actions"]["book_settlement"]["max_amount"] == "0"   # the caps really are zero
    bank, target = settlement_bank(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-01")
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    report = tss.run_stand_ins(pg_engine, today=date(2026, 9, 14))
    assert (len(report["booked"]), report["skipped"]) == (1, []) and db_session.scalar(select(func.count(PolicyBudget.month))) == 0   # C3: a cap-aware job would have skipped
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
    assert [c.match_rule for c in _active(db_session, sid)] == ["settlement_stand_in"]          # and the claim needs no budget either
```

`stand_ins_on(bank, target, *, enabled_from)` writes the `settlement` block through `update_reconciliation_settings`; `_settings(db)` = `read_settlement_settings`; `_hk(**counts)` = `{**dict.fromkeys(tss.HOUSEKEEPING_KEYS, 0), **counts}`; `ingest_fixture(session)` builds a `(run, token)` pair like the `run` fixture on another session; `policy_on` is the Task 7 fixture; the job tests pass `pg_engine` as `tests/integration/test_auto_pay_job.py:68` does; the owner-delete path is `entry_write_service.delete_entry` on one leg (both legs go), as `tests/integration/test_auto_pay_service.py:423-429` does.


- [ ] **Step 2: Run to verify failure**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/integration/test_settlement_stand_ins.py`
Expected: FAIL

- [ ] **Step 3: Implement the job half of `trade_settlement_service`** — the task text above is the spec for this step; the tests are its acceptance, not its definition (C6).

- [ ] **Step 4: Run tests**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/integration/test_settlement_stand_ins.py tests/integration/test_settlement_reconcile.py tests/integration/test_auto_pay_settings.py tests/integration/test_auto_pay_job.py` (the two-session tests need the `pg_engine` PostgreSQL fixture, as `statement_fixtures._interleave` does)
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
- Modify: `services/accounting-service/tests/integration/settlement_fixtures.py` (nothing new; listed because the tests import `settlement_bank` / `stand_ins_on`; M12)
- Test: `services/accounting-service/tests/integration/test_reconciliation_api.py` (+3), `services/accounting-service/tests/worker/test_cathay_job.py` (+3, the `World` fixture)

**Interfaces:**
- Consumes: Task 9 `run_stand_ins`; `reconciliation_service.pending_statement_ids` (`:1067`), `reconcile_each` (`:1076`); `worker.api.ApiClient` (`worker/api.py:50`) over the worker's existing token (`STATEMENT_API_TOKEN_FILE`, `worker/config.py:113`; label `worker`, scope `ingest` only, `deploy/statements/README.md:462`) — the new route is `INGEST` like the sweep it already calls, so no new token or scope.
- Produces: `trade_settlement_service.recheck_statement_ids(db) -> list[int]` (live, non-retired statements of `settlement_source` accounts holding an open case with hint `settlement_pending`, `settlement_ambiguous` or `settlement_mismatch`); `flag_for_recheck(db, report: dict) -> int` (sets `needs_recheck` on the live, non-retired statements whose period holds a `touched_dates` day, plus `recheck_statement_ids`; returns the count). Route `POST /reconciliation/settlements/run` (`dependencies=INGEST`, body `SettlementRunIn{source: Literal["portfolio:cathay"]}`, response `SettlementRunOut{status: str, booked: int, skipped: int, housekeeping: dict[str, int], flagged: int}`; counts and codes only). The sweep: `run_stand_ins` + `flag_for_recheck` before `pending_statement_ids`, then reconciles `sorted(set(pending) | set(recheck_statement_ids(db)))`; `SweepOut.stand_ins` = the counts. Worker: `cathay.run` calls `homehub._call("POST", "/reconciliation/settlements/run", json={"source": "portfolio:cathay"}, timeout=cfg.cathay_timeout_s)` after a plain commit run that is not latched; any exception → `summary["settlement_run"] = "failed:<ExceptionClass>"`, the run's exit status unchanged; success → `summary["settlement_run"] = <status>`. Order (M14): `summary = job.summary()` → `_report(cfg, portfolio_http, summary)` (unchanged; it reads only `codes` / `files_seen` / `posted`, cathay.py:96-99) → the HomeHub call adds `settlement_run` to the returned summary → the latch step.

- [ ] **Step 1: Write the failing tests**

```python
def test_settlement_run_books_flags_and_reports_counts_only(client, seed, db_session, run, portfolio_stub, stand_ins_on):
    bank, target = settlement_bank(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-01")
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])       # feed empty → settlement_pending
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    res = client.post("/reconciliation/settlements/run", headers=W, json={"source": "portfolio:cathay"})
    assert res.status_code == 200 and (res.json()["status"], res.json()["booked"], res.json()["flagged"]) == ("completed", 1, 1)
    assert "12305" not in res.text and db_session.get(AccountStatement, sid).needs_recheck is True


def test_settlement_run_needs_the_ingest_scope(client):
    assert client.post("/reconciliation/settlements/run", headers=S, json={"source": "portfolio:cathay"}).status_code == 403


def test_sweep_rechecks_statements_with_open_settlement_cases(client, seed, db_session, run, portfolio_stub):
    bank, _ = settlement_bank(seed, db_session)
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])       # pending: nothing dirty, nothing needs_recheck
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    lease = _claim(client)                                                                    # tests/integration/test_reconciliation_policy._claim
    res = client.post("/reconciliation/sweep", headers=W, json={"run_id": lease["run_id"], "lease_token": lease["lease_token"]})
    assert res.status_code == 200 and res.json()["statements"] == 1
    assert [c.kind for c in _cases(db_session, sid, "open")] == ["settlement"]
```

```python
# tests/worker/test_cathay_job.py — World gains `self.homehub = HomeHub()` (a MockTransport handler recording (method, path, json) in
# `.calls`, answering {"status": "completed", ...} or raising when `.fail = 503`) and `go(**kw)` passes
# `homehub=ApiClient(httpx.Client(base_url="http://homehub.test", transport=httpx.MockTransport(self.homehub)), "worker-token")`.
def test_plain_commit_run_calls_homehub_after_posting(w):
    w.add("id-1", "2026-03-09_對帳單.pdf", fx("single.txt"))
    summary = w.go()
    assert w.homehub.calls == [("POST", "/reconciliation/settlements/run", {"source": "portfolio:cathay"})]
    assert summary["settlement_run"] == "completed" and summary["codes"] == []


def test_dry_run_latched_and_file_runs_do_not_call_homehub(w):
    w.add("id-1", "2026-03-09_對帳單.pdf", fx("single.txt"))
    w.go(dry_run=True)
    w.go(file_id="id-1")
    w.latch.write_text("{}", encoding="utf-8")
    with pytest.raises(cathay.CathayDisabled):
        w.go()
    assert w.homehub.calls == []


def test_homehub_failure_never_changes_the_run_result(w):
    w.add("id-1", "2026-03-09_對帳單.pdf", fx("single.txt"))
    w.homehub.fail = 503
    summary = w.go()
    assert summary["settlement_run"] == "failed:ApiError" and summary["codes"] == [] and summary["counts"] == {"imported": 1}
    assert "worker-token" not in json.dumps(summary)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q tests/integration/test_reconciliation_api.py tests/worker/test_cathay_job.py -k "settlement or homehub"` (one `-k`; pytest keeps only the last — M17)
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
- Consumes: the `r6_case_dump.py` dump — per statement `claims = [[event_id, rule, entry_ids]]` and `cases = [[event_id, kind, hint, candidate_keys, entry_id]]` (`scripts/r6_case_dump.py:68`), which already carry the case `kind` / `hint` and the claim `rule` the listing needs (M16) — plus a `run_stand_ins(dry_run=True)` report written as JSON (`--stand-ins <file>`) for the would-book list; the `ReconcileResult.settlements` counts are shown as a footer only.
- Produces: `r6_dryrun.py --settlements [--stand-ins report.json]` — from the candidate dump, one row per settlement line of every live statement of a `settlement_source` account: `statement_id, event_id, outcome ∈ {stand_in_claim, owner_match, book_settlement, pending, ambiguous, mismatch, drift}`, the cases it would supersede or dismiss, and every stand-in the job would book (dates and booking ids only, no amounts, no merchants, mode 600 under `~/reports`).

- [ ] **Step 1: Write the failing dry-run test** (in `test_r6_dryrun.py`, with its `_dump` helper):

```python
def test_settlements_listing_lists_one_row_per_settlement_line():
    dump = _dump({"1": {"claims": [[11, "settlement_stand_in", [5]]], "cases": [[12, "line_unmatched", "settlement_pending", [], None]]},
                  "2": {"claims": [], "cases": [[21, "settlement", None, ["cathay:2026-09-10:aa"], None], [22, "ambiguous", "settlement_ambiguous", ["f1", "f2"], None]]}})
    stand_ins = {"booked": [{"account_id": 1, "settlement_date": "2026-09-16", "booking_id": 7}], "skipped": []}
    out = dr.settlements_listing(dump, stand_ins)
    assert [(r["statement_id"], r["event_id"], r["outcome"]) for r in out["lines"]] == [
        ("1", 11, "stand_in_claim"), ("1", 12, "pending"), ("2", 21, "book_settlement"), ("2", 22, "ambiguous")]
    assert out["stand_ins"] == [{"account_id": 1, "settlement_date": "2026-09-16", "booking_id": 7}]
    assert not {"flow", "merchant", "amount", "net_amount"} & set(json.dumps(out))              # ids, dates and codes only
```
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

## Dividends

Moved to `docs/superpowers/plans/2026-10-30-portfolio-integration-dividends.md` (AGENT-360): a separate booking owner path with its own recovery semantics; it reuses the Task 3 tables (`kind = dividend`), the Task 7 cap exemption and the Task 2 feed. The dividend pre-provisions that stay in Phase 1 — the `book_dividend` cap exemption (Global Constraints), Task 3 `kind = dividend` / the `case_kind` and `MATCHER_KINDS` values, Task 7 `CAP_EXEMPT` and `dividend_category_id`, the inbox `kind == "dividend"` → `other` mapping — are deliberate and are consumed unchanged by that plan. `Rules.dividend_labels` is NOT pre-provisioned: it arrives with its consumer in that plan (spec §9 rev 4.5, M4).

---

## Self-review notes (plan author, 2026-10-30)

- Spec coverage: §4.1 → Tasks 1–2; §4.2 → Task 3 + Task 11 owner SQL; §4.3 → Tasks 5–6; §4.4 → Tasks 3, 6 (drift), 7; §4.5 → Task 6 (revision hook) + Task 10; §4.6 → the dividends follow-up plan; §4.7 → Task 3 (table), 5 (claim), 6 (claim link, mismatch, orphan, twin retirement), 9 (job), 10 (triggers); §5 → Tasks 2 + 8; §8 → Tasks 3, 5, 7, 9; §9 → Task 5 (sum gate), 6 (unavailable, drift, mismatch), 7 (unique races, revert), 9 (owner delete, job unavailable); §10 → the named tests; §11 step 1 → Task 11. §6 (wires, `/funding`) and §7 (Flex) are the Phase 1b / Phase 2 plans.
- Pre-flight scan (rev 4.5): B1–B7, C1–C8, D1–D9, M1–M17 — see `.superpowers/sdd/2026-10-19-portfolio-integration-phase1/preflight-rulings-applied.md` for the finding → change table.
- AGENT-360 re-check (rev 4.4): minor 1 → Task 6 `lock_pair` + spec §4.7 Locks quote engine.py:244-270 / :482 and the entry/group wording; minor 2 → Task 2 test seeds; minor 3 → Task 5 `CalendarExpired` + unit test, Task 6 `PortfolioUnavailable("calendar")` + `test_a_window_past_the_calendar_assigns_nothing`; minor 4 → Task 9 lookup by (source, trade_date), P:1047 atomic-replacement test unchanged.
- Re-verify 2 (rev 4.3): B1 trade axis any row (Task 2 contract + `test_statement_day_is_also_found_by_its_trade_date_alone`, Task 6 `fetch_by_trade_date`, Task 7); B2 no account row lock (Task 6 `lock_pair`, Task 9 `book_stand_in` / per-decision order / departure (5), spec §4.7 Locks); S1 02-12; S2 stale `unclaimed_stand_ins` monkeypatch; S3 `HOUSEKEEPING_KEYS` + `_hk`; N1–N3.
- AGENT-360 (rev 4.2): major 1 → Task 6 `housekeep` in `reconcile` + `test_reconcile_tombstones_an_owner_deleted_bank_booking_before_reserving`; major 2 → `lock_pair`, `retire_pair` claim re-read, Task 9 two-session tests; major 3 → `fetch_by_settlement_window` / `fetch_by_trade_date`; major 4 → covered neighbour lines as contestants + test; major 5 → `replace` re-gates + two tests; major 6 → drift fields + test; major 7 → two-axis feed with `trade_bounds` from `business_days`, bounds echo, `incomplete`; minor 8 → `unknown_dates` through `Feed` → `assign` → `settlements`; Tasks 12–13 → the dividends plan.
- Q7 changed: Task 3 (`origin`, `status` set, partial uniques, no `ledger_entry` column), Task 5 (stand-in claim, `settlement_stand_in` flag), Task 6 (claim link, `settlement_mismatch`, settlement orphans, twin retirement, drift over stand-in claims), Task 7 (`owner_deleted` → propose), Task 9 (new), Task 10 (the worker call runs after every plain run, so a settlement day reached that morning is seen), Task 11 (`enabled_from`, stand-ins in the dry-run listing), Review Focus 1–3.
- Type consistency: `Settlement` fields (`trade_date, settlement_date, date_source, net_amount, legs_net, currency, bookable, reason, items, fingerprint`) are the same in Tasks 4–10; case candidate keys (`fingerprint, settlement_date, trade_date, net_amount, date_source, items`) identical in Tasks 6–7; `settlement_booking_id` is the context key in Task 6 orphans/mismatch and Task 11 docs; `trade_settlement_service` functions named in Task 6 are the ones Tasks 7, 9, 10 consume.
- Precedents named in the tests: owner delete = `entry_write_service.delete_entry` (`test_auto_pay_service.py:423-429`); the sweep lease = `test_reconciliation_policy._claim` (`:971`).
