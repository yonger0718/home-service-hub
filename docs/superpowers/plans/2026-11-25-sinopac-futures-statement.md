# 永豐期貨對帳單 Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import every 永豐期貨 daily and monthly statement PDF the morning after it arrives, keep the account's equity
per day, count it in the portfolio net worth and in accounting account 147, and report gaps, differences and stopped
runs to the owner.

**Architecture:** The accounting-service worker lists both Drive folders, extracts positioned words with pdfplumber,
parses and self-checks each statement and posts it to new stock-portfolio-service endpoints. The portfolio stores one
row per statement day and per month, derives the equity series and the findings under one advisory lock, and feeds
the series into `portfolio_snapshot`, the ledger-feed valuation and a summary endpoint. A new notifier module and SPA
source read the pending and run endpoints.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2, Alembic, Postgres (SQLite in unit and most integration tests),
pydantic 2, httpx, pdfplumber 0.11.10 (already pinned in `requirements-worker.txt`), Angular 21 + Vitest.

**Spec:** `docs/superpowers/specs/2026-11-25-sinopac-futures-statement-design.md` rev 1.5 (plan review AGENT-592 and owner-requested re-check AGENT-594;
folded: consistent fixture, archive validation in one rolled-back transaction). Section
numbers (§x) refer to it. Read it before any task. Precedent: the 國泰 plan
`docs/superpowers/plans/2026-10-29-cathay-daily-statement.md` and its code; where a task says "as 國泰", copy that
code's shape, not its semantics.

## Global Constraints

- Fictional fixtures only: no real statement, name, amount, account, bank or order number in code, tests, docs,
  commits or review comments. Products `範例期貨` (futures) and `範例選` (options), order numbers like `sA1B2-01`.
- Statement PDFs are read only inside the worker user's processes; any archive check prints counts, dates and masked
  shapes (digits → 9), never amounts. The password file's values never appear anywhere.
- Every numeric word maps to exactly one label by right edge within 3 pt; text words are read by fixed sequence (§5.3).
- Any grammar or identity failure rejects the whole statement: nothing posted, job latched (§5.3, §7).
- Write-once acceptance: same sha → `duplicate`, other sha → `conflict`, the accepted row never changes (§6.2).
- One futures advisory key `0x46555455 << 32`: exclusive for imports, findings and acks; shared for
  `write_today_snapshot`, `refresh_snapshot_cash_range` and the replay; inputs re-read under it (§6.2).
- Series: latest accepted source on or before D; a monthly row is dated by `month_end` and wins a tie; value applies
  until the next source's date, exclusive; no source → 0 / `complete` false (§6.3).
- `refresh_snapshot_futures_range` never commits, inserts or deletes; the import commits once (§6.5).
- Valid zero: a present `total_assets_twd` of 0 is a total, never "missing" (§6.5).
- Pending: ≤ 50 items with `total` / `truncated`; `reason` ≤ 40 characters; notifier ≤ 10 items + 「另有 N 項」; line ≤ 120
  characters; client timeout 10 s, body ≤ 64 KiB (§8, as 國泰).
- Timer 06:45 Asia/Taipei; notifier keys `sinopac_futures_run_time` = `06:45`, `sinopac_futures_run_grace_minutes` = 15.
- Portfolio tests run with a sandbox root `.env` (dummy credentials, `POSTGRES_PORT=9`); accounting tests with the DB
  and OTEL variables of the test recipe; never the production `.env`; never `alembic` against a real database from a
  worktree (migration checks use the throwaway Postgres container of Task 7).
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. A value word sitting between two labels (4–6 pt from each right edge) must reject the statement, not land in the
   nearer column — Task 8 `test_value_between_labels_rejected`.
2. A monthly statement imported before its daily statements: the `gap` finding lists every activity date, shrinks as
   the dailies arrive, and disappears with the last one (no pending item left) — Task 4
   `test_gap_shrinks_then_clears`.
3. The same statement saved twice in Drive (re-sent mail, identical bytes, two file ids) in one run: one `imported`,
   one `duplicate`, no error, no latch — Task 9 `test_same_statement_twice_in_run`.
4. The portfolio answers 5xx halfway through a backfill run: statements posted before it stay recorded in the state
   file, the run stops without a latch and reports `failed` — Task 9 `test_portfolio_5xx_stops_without_latch`.
5. Equity below zero (losses beyond the balance): the series, the snapshot total, the chart and the card all show the
   negative value — Task 6 `test_negative_futures_equity_in_total`, Task 13 `negative equity renders`.

## Delivery

Four PRs, each branched from `origin/main` after the previous one merges: **A** portfolio (Tasks 1–7,
`feat/futures-statement-portfolio`), **B** worker (Tasks 8–10, `feat/futures-statement-worker`), **C** notifier
(Task 11, `feat/futures-statement-notify`), **D** SPA (Tasks 12–14, `feat/futures-statement-spa`). Each PR gets one
Multica review round plus at most one re-check before the owner's merge go.

---

## PR A — stock-portfolio-service

Paths relative to `services/stock-portfolio-service/`. Test command:
`.venv/bin/python -m pytest -p no:cacheprovider -q <path>`.

### Task 1: Tables and snapshot column

**Files:**
- Create: `app/models/sinopac_futures_statement.py`, `alembic/versions/f1futures_statement.py`
- Modify: `app/models/portfolio_snapshot.py` (column), `app/main.py:8` (model import list), `alembic/env.py:10` (add
  `import app.models.sinopac_futures_statement  # noqa: F401`)
- Test: `tests/unit/test_futures_statement_models.py`, `tests/integration/test_futures_statement_migration.py`

**Interfaces:**
- Produces: `FuturesStatementDay`, `FuturesStatementMonth`, `FuturesStatementRun`; `PortfolioSnapshot.futures_equity_twd`.

`FuturesStatementDay` (`futures_statement_day`): `statement_date` Date PK; `accepted_sha256` String(64),
`accepted_at` timestamptz, `drive_file_id` String(128); Numeric(18,4) NOT NULL columns named exactly as the `equity`
keys of §5.1 (`prev_balance`, `deposits_net`, `fees_and_tax`, `futures_realized`, `premium_net`, `balance`,
`futures_unrealized`, `equity`, `total_equity`, `initial_margin`, `maintenance_margin`, `securities_collateral`,
`excess_margin`, `margin_call`, `additional_margin`, `withdrawable`); JSON `deposits`, `fills`, `fills_total`
(nullable), `positions`, `position_lines`; Integer `open_positions`; attempt `last_sha256`, `last_status`
String(16) (check ∈ imported / duplicate / conflict), `last_detail` JSON, `last_at`, `since`; findings `findings`
JSON, `findings_sha` String(64), `findings_since` timestamptz; ack `ack_sha256`, `ack_status`, `ack_at`,
`ack_findings_sha`.

`FuturesStatementMonth` (`futures_statement_month`): `month` Date PK (first of month), `month_end` Date; the same
acceptance / attempt / findings / ack columns; Numeric(18,4) columns `a, b, g, h, k, m, n, q, p, u, v, w,
securities_collateral, additional_margin, withdrawable, long_premium_value, short_premium_value, total_equity`
(the §5.2 `lines` keys, letters lower-cased); JSON `activity_dates`; Integer `open_positions`.

`FuturesStatementRun` (`futures_statement_run`): as `CathayStatementRun`.

`PortfolioSnapshot.futures_equity_twd`: Numeric(20,4) NOT NULL, server default `0`.

Migration `revision = "f1futures"`, `down_revision = "e9settle"`; JSON columns
`sa.JSON().with_variant(postgresql.JSONB(), "postgresql")`; downgrade drops the three tables and the column.

- [ ] **Step 1: Write the failing tests**: `test_day_row_roundtrip` (all JSON columns equal after reload),
  `test_day_status_check_rejects_unknown` (`last_status="held"` → `IntegrityError`), `test_month_row_roundtrip`,
  `test_snapshot_futures_default_zero` (row without the column → 0); migration test (Postgres, as
  `tests/integration/test_statement_facts_migration.py`): upgrade adds the column with 0 on existing snapshot rows,
  downgrade removes tables and column.
- [ ] **Step 2: Run** — FAIL (module missing).
- [ ] **Step 3: Implement** models and migration.
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(portfolio): futures statement tables and snapshot column`.

### Task 2: Payload schemas

**Files:**
- Create: `app/schemas/sinopac_futures_statement.py`
- Test: `tests/unit/test_futures_statement_payload.py`, fixtures `tests/fixtures/futures_daily_payload.json`,
  `tests/fixtures/futures_monthly_payload.json`: the §5.1 / §5.2 examples (rev 1.4, internally consistent) with
  `source` = `{"drive_file_id": "fixture-daily" | "fixture-monthly", "sha256": "a"*64 | "b"*64, "file_name":
  "fixture.pdf"}`. The worker's Task 8 builds positioned-word fixtures that parse to exactly these two files.

**Interfaces:**
- Produces: `DailyPayload`, `MonthlyPayload`, `FuturesPayload = Annotated[DailyPayload | MonthlyPayload,
  Field(discriminator="kind")]`; `Source{drive_file_id, sha256 [0-9a-f]{64}, file_name}`; `Fill`, `Close`,
  `Position`, `Deposit`, `Equity`, `MonthLines` with the §5.1 / §5.2 field names; all amounts `Decimal`, `qty` int ≥ 1.

Validators (422 when broken): `balance = prev_balance + deposits_net − fees_and_tax + futures_realized + premium_net`;
`equity = balance + futures_unrealized`; `total_equity = equity + Σ position premium_value` with 賣 negative;
`action` / `right` / `side` / `direction` sets of §5.1; `strike` and `right` both null or both set; monthly
`m = a + b + g − h + k`, `q = m + n`, `total_equity = q + long_premium_value − short_premium_value`; `month_end` is the
last day of `month`; `activity_dates` inside the month.

- [ ] **Step 1: Write the failing tests**: `test_daily_fixture_validates_and_roundtrips`,
  `test_monthly_fixture_validates_and_roundtrips`, `test_discriminator_by_kind`; parametrised
  `test_daily_identity_broken` (balance +1; equity +1; total_equity +1; a 賣 position counted positive) and
  `test_monthly_identity_broken` (m, q, total_equity +1) each → `ValidationError`; `test_strike_without_right_rejected`;
  `test_unknown_action_rejected` (`買進`); `test_month_end_not_last_day_rejected`.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(portfolio): futures statement payloads`.

### Task 3: Lock and equity series

**Files:**
- Create: `app/services/sinopac_futures_series.py`
- Test: `tests/integration/test_futures_series.py`

**Interfaces:**
- Consumes: Task 1 models.
- Produces:
  - `FUTURES_LOCK_KEY = 0x46555455 << 32`; `futures_lock(db: Session, *, shared: bool = False) -> None` —
    `pg_advisory_xact_lock` / `pg_advisory_xact_lock_shared` on Postgres, no-op on other dialects (as
    `broker_cathay_statement_service.lock_trade_dates`).
  - `@dataclass(frozen=True) FuturesValue(total_equity: Decimal, balance: Decimal, source_date: date,
    source_kind: Literal["daily", "monthly"], positions_open: bool)`; `frozen(self, d: date) -> bool` =
    `positions_open and source_date < d`.
  - `equity_at(db, d: date) -> FuturesValue | None`.
  - `equity_by_date(db, start: date, end: date) -> dict[date, FuturesValue | None]` — one query per table, every
    calendar day in range (used by the replay and the refresh).
  - `source_dates(db) -> set[date]` — every accepted `statement_date` and `month_end`.

`balance` of a monthly source is its `m`.

- [ ] **Step 1: Write the failing tests**: `test_no_source_before_first` (→ None), `test_daily_carries_until_next`,
  `test_monthly_wins_tie` (daily on 03-31 and monthly with `month_end` 03-31 → monthly), `test_monthly_then_next_daily`
  (04-02 daily replaces from 04-02, 04-01 still monthly), `test_frozen_flag` (open positions, D after source →
  `frozen` true; on the source date → false), `test_unaccepted_rows_ignored` (a row with `accepted_sha256` NULL is not a
  source), `test_equity_by_date_matches_equity_at` (every day of a 40-day range), `test_source_dates`.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(portfolio): futures equity series and lock`.

### Task 4: Import, findings, snapshot refresh, pending and ack

**Files:**
- Create: `app/services/sinopac_futures_statement_service.py`
- Modify: `app/services/portfolio_snapshot_service.py` (add `refresh_snapshot_futures_range`)
- Test: `tests/integration/test_futures_statement_import.py`, `tests/integration/test_futures_findings.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces:
  - `import_statement(db, payload: FuturesPayload, *, dry_run: bool) -> FuturesOutcome`, `FuturesOutcome(outcome:
    Literal["imported", "duplicate", "conflict"], key: str, findings: list[dict], errors: list[str])`; `key` =
    ISO date or `YYYY-MM`.
  - `recompute_findings(db, *, daily: date | None = None, month: date | None = None) -> None` — the §6.4 recompute set.
  - `findings_sha(findings: list[dict]) -> str | None` — sha256 of `json.dumps(findings, sort_keys=True,
    separators=(",", ":"), ensure_ascii=False)`, None for `[]`.
  - `refresh_snapshot_futures_range(db, start: date, end: date) -> int` in `portfolio_snapshot_service` — updates
    `futures_equity_twd` of existing rows in `[start, end]` from `equity_by_date`, returns rows touched; no commit, no
    insert, no delete.
  - `validate_archive(db, payloads: list[FuturesPayload]) -> dict` — `{"items": [{"file_name", "key", "outcome"}],
    "findings": {key: findings}}`: in one transaction under `futures_lock`, imports each payload in order (dailies by
    date, then monthlies) through the commit path without its commit, collects the final findings of every row, then
    rolls back.
  - `pending(db) -> dict` (§8 envelope), `acknowledge(db, key: str, status: Literal["conflict", "findings"]) -> bool`,
    `record_run(db, report) -> None`, `latest_runs(db) -> dict`.

Behaviour: commit path as 國泰 (`if db.in_transaction(): db.rollback()` then `with db.begin():`), `futures_lock`,
re-read, §6.2 cases; on `imported`: write the row, `recompute_findings`, then `refresh_snapshot_futures_range(start =
statement date or month_end, end = the next source date − 1 day or today)`. `since` / `findings_since` follow the
國泰 rules (kept on repeat, reset on change). Dry run: no lock, no write, outcome computed against current rows.

- [ ] **Step 1: Write the failing tests**:
  - import: `test_daily_imported`, `test_same_sha_duplicate_writes_nothing`, `test_other_sha_conflict_keeps_row`
    (every accepted column unchanged, `last_detail` has both shas), `test_monthly_imported`, `test_dry_run_writes_nothing`,
    `test_failure_after_refresh_rolls_back` (monkeypatch `refresh_snapshot_futures_range` to raise → no day row, no
    snapshot change), `test_refresh_updates_existing_rows_only` (rows on 03-05 and 03-07, statement 03-06 → only
    03-07 updated, no row inserted for 03-06), `test_refresh_does_not_commit` (spy on `Session.commit`: called once,
    by the import).
  - findings: `test_prev_on_gap` (03-02 balance 100, 03-09 prev_balance 90 → `[{"code": "prev", "dates":
    ["2026-03-02"], "letters": []}]`), `test_prev_rechecked_when_missing_day_arrives` (03-05 arrives with balance 90
    → 03-09 finding cleared), `test_first_statement_no_prev`, `test_gap_lists_dates`, `test_gap_shrinks_then_clears`
    (Review Focus 2), `test_diff_only_without_gap` (B differs, no gap → `diff` letters `["B"]`; with a gap → only
    `gap`), `test_carry`, `test_carry_out_of_order` (February then January with M ≠ February A → February gets
    `carry`), `test_findings_identity_same_count_other_dates` (gap 03-02 → gap 03-03 after ack: pending again).
  - pending / ack: `test_pending_conflict_and_findings_two_items` (one row, both → two items, each acked alone),
    `test_ack_findings_then_identical_recompute_stays_acked`, `test_ack_404_when_not_pending`,
    `test_pending_envelope_and_cap` (51 → 50 items, total 51, truncated), `test_reason_codes_and_cut`
    (`prev:03/02,gap:2` style, ≤ 40 characters), `test_run_report_latest_rows`.
  - validation: `test_validate_archive_empty_db_multi_file` (empty DB; dailies 03-02, 03-09 with a gap between,
    monthly 2026-03 listing 03-05, then the same 03-02 file again and a second sha for 03-09 → outcomes imported,
    imported, imported, duplicate, conflict; findings `prev` on 03-09 and `gap` on 2026-03; afterwards zero rows in
    both tables and every snapshot row's `futures_equity_twd` unchanged).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS; then the full portfolio suite — no new failures.
- [ ] **Step 5: Commit** `feat(portfolio): futures statement import, findings and ack`.

### Task 5: Endpoints, ledger feed and summary

**Files:**
- Modify: `app/routers/imports.py` (after the `/cathay-statement` routes), `app/services/ledger_feed_service.py:20-31,173-215`
- Create: `app/routers/futures.py` (registered in `app/main.py:9`), `app/schemas/futures_summary.py`
- Test: `tests/integration/test_futures_statement_routes.py`, `tests/integration/test_ledger_feed.py` (extend),
  `tests/integration/test_futures_summary.py`

**Interfaces:**
- Consumes: Task 4 service.
- Produces (prefix `/api/portfolio/imports`): `POST /sinopac-futures-statement?dry_run=` (body `FuturesPayload`,
  response `{outcome, key, findings, errors}`, 500 when rolled back); `GET /sinopac-futures-statement/pending`;
  `POST /sinopac-futures-statement/validate` (body `{"payloads": [FuturesPayload, …]}`, ≤ 200 payloads, response of
  `validate_archive`); `POST /sinopac-futures-statement/runs` (body as `CathayRunReport`, 204); `GET /sinopac-futures-statement/runs/latest`;
  `POST /sinopac-futures-statement/days/{key}/ack?status=conflict|findings` (`key` `\d{4}-\d{2}(-\d{2})?`; 204 / 404).
  `GET /api/portfolio/futures/summary?as_of=` → `FuturesSummary{as_of, value: {total_equity, balance, source_date,
  source_kind, positions_open, frozen} | null, latest_daily: {statement_date, withdrawable, initial_margin,
  maintenance_margin, excess_margin, margin_call} | null, month: {month, pnl, pnl_complete, pnl_reason,
  futures_realized} | null}` — amounts as strings; `month.pnl` = value at `as_of` − value at the previous month end −
  the month's deposits (monthly `b` when that month's row exists, else Σ daily `deposits_net`); no source on or before
  the previous month end → `pnl` null, `pnl_complete` false, `pnl_reason` `no_baseline`; deposits from daily rows and
  any of the month's daily rows or the month row has findings → `pnl` computed, `pnl_complete` false, `pnl_reason`
  `findings`; otherwise `pnl_complete` true, `pnl_reason` null. `futures_realized` = monthly `k` or Σ daily.
  Ledger feed: `valuation(db, broker, as_of)` dispatches `cathay` (unchanged) / `sinopac_futures` (§6.5 fields, `currency`
  `TWD`) and echoes `broker`; `settlements` / `dividends` keep `_check_broker` = cathay only.

- [ ] **Step 1: Write the failing tests**: routes — `test_validate_route_rolls_back`, `test_post_daily_imports`, `test_post_identity_broken_422_writes_nothing`,
  `test_post_500_when_rolled_back`, `test_pending_shape`, `test_ack_by_status`, `test_ack_bad_key_422`,
  `test_runs_latest`, `test_runs_invalid_422`; ledger feed — `test_valuation_sinopac_futures`,
  `test_valuation_sinopac_futures_before_first` (`"0"`, `priced_through` null, `complete` false),
  `test_settlements_and_dividends_reject_sinopac_futures` (404 `unknown_broker`), `test_valuation_cathay_unchanged`;
  summary — `test_summary_empty`, `test_summary_no_baseline_pnl_null` (first source in the month → `pnl` null,
  `no_baseline`), `test_summary_findings_marks_incomplete` (a `prev` finding in the month → `pnl_complete` false,
  `findings`), `test_summary_monthly_row_complete` (monthly `b` used → complete), `test_summary_month_pnl_daily_only`, `test_summary_month_pnl_with_monthly`,
  `test_summary_frozen`.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS; full suite — no new failures.
- [ ] **Step 5: Commit** `feat(portfolio): futures statement endpoints, valuation and summary`.

### Task 6: Net worth integration

**Files:**
- Modify: `app/services/portfolio_snapshot_service.py:44-140` (`write_today_snapshot`, `_is_cash_only_row`,
  `refresh_snapshot_cash_range`), `app/services/networth_backfill_service.py:492-960` (`replay_snapshots_range`: the
  `PortfolioSnapshot(...)` sites near :765 / :781 / :894, `write_snapshot` near :649, the gates near :776 / :879, the
  phantom-delete filter near :922), `app/routers/history.py:73`, `app/services/portfolio_service.py:1412-1413`,
  `app/schemas/portfolio.py:280` (`futures_equity_twd` field on `PortfolioSummary`)
- Create: `app/services/sinopac_futures_backfill.py` (`backfill_futures_snapshots(db, start: date, end: date, *,
  dry_run: bool) -> dict` with keys `updated`, `inserted`, `changed`, `delta_sum`; `_main(argv)` for
  `python -m app.services.sinopac_futures_backfill --from YYYY-MM-DD [--to YYYY-MM-DD] [--dry-run]`, printing the
  counts as JSON; `--to` defaults to today Asia/Taipei). The networth replay CLI is not changed.
- Test: `tests/integration/test_futures_networth.py`, `tests/integration/test_futures_backfill.py`

**Interfaces:**
- Consumes: Task 3 (`futures_lock(shared=True)`, `equity_by_date`, `equity_at`, `source_dates`).
- Produces: `total_assets_twd = market + cash + futures` in `/history` points (plus `futures_equity_twd`) and in the
  summary.

Every writer takes `futures_lock(db, shared=True)` before reading the series. The cash-only row rule: zero cash
deletes a row only when `futures_equity_twd == 0`; inserted rows take the series value. The replay adds
`source_dates(db)` to its eligibility gates and keeps rows with non-zero futures in the phantom-delete filter.

- [ ] **Step 1: Write the failing tests**: `test_today_snapshot_includes_futures`, `test_history_total_includes_futures`,
  `test_summary_total_includes_futures`, `test_zero_cash_row_with_futures_kept`, `test_cash_insert_carries_futures`,
  `test_replay_writes_futures_only_date` (no stock / cash activity on a statement date → row with futures),
  `test_replay_matches_live_writer` (same day, replay vs `write_today_snapshot` → equal `futures_equity_twd` and total),
  `test_negative_futures_equity_in_total` (Review Focus 5), `test_phantom_filter_keeps_futures_row`; history fill —
  `test_backfill_updates_only_futures_column` (rows with non-null `portfolio_xirr`, stock and cash values: after the
  fill every column except `futures_equity_twd` is unchanged), `test_backfill_inserts_forward_filled_row` (a source
  date without a row → new row copying the previous row's columns, XIRR included, plus futures),
  `test_backfill_no_previous_row_inserts_zero_row`, `test_backfill_never_deletes`, `test_backfill_dry_run_writes_nothing`
  (counts equal the real run's; table unchanged), `test_backfill_cli_dry_run_writes_nothing` (through `_main` with
  `--dry-run`), `test_backfill_takes_exclusive_lock`.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS; full suite — no new failures.
- [ ] **Step 5: Commit** `feat(portfolio): futures equity in net worth`.

### Task 7: Real lock and migration on Postgres

**Files:**
- Create: `tests/integration/test_futures_statement_lock_pg.py` (skipped unless `FUTURES_LOCK_TEST_URL` is set, as
  `test_cathay_statement_lock_pg.py`)

- [ ] **Step 1:** Start a throwaway Postgres container as the 國泰 Task 6 does; `alembic upgrade head` /
  `downgrade -1` / `upgrade head` against it only (`DATABASE_URL` pointed at the container, printed before running).
- [ ] **Step 2: Write the tests** (two sessions, threads with an event at the read point):
  `test_two_dates_serialise` (imports for 03-06 and 03-09 interleaved: the second sees the first's row and computes
  `prev` from it), `test_daily_and_monthly_serialise`, `test_snapshot_writer_waits_for_import` (shared lock waits; the
  written `futures_equity_twd` equals the committed series), `test_replay_waits_for_import`.
- [ ] **Step 3: Run** — PASS against the container; skipped without the URL.
- [ ] **Step 4: Commit** `test(portfolio): futures lock on Postgres`.

---

## PR B — accounting-service worker

Paths relative to `services/accounting-service/`. Test command:
`PYTHONPATH=$PWD/../shared-python-lib .venv/bin/python -m pytest tests/worker -q -p no:cacheprovider` with the test
recipe's variables.

### Task 8: Census and parser

**Files:**
- Create: `worker/sinopac_futures_statement.py`, `tests/worker/futures_fixtures.py` (builders of `pdf.Line` /
  `pdf.Word` with explicit x positions)
- Modify: spec §2 / §5.3 (rev 1.6: the monthly grammar and the confirmed sequences)
- Test: `tests/worker/test_sinopac_futures_statement.py`

**Interfaces:**
- Consumes: `worker.pdf.Line`, `worker.pdf.Word`.
- Produces: `StatementError(code: str)`; `parse_daily(lines: list[Line]) -> DailyStatement`; `parse_monthly(lines:
  list[Line]) -> MonthlyStatement`; `to_payload(st, *, drive_file_id: str, sha256: str, file_name: str) -> dict`
  (exactly the §5 JSON). Error codes: `title`, `section`, `header`, `line`, `column`, `sequence`, `date`, `currency`,
  `page2`, `identity:<name>`.

- [ ] **Step 1: Census (archive, counts only).** As `homehub-worker`, with the two password lines in place, run a
  private census script over both folders (not committed; scratch under `~homehub-worker/tmp`, removed after): per
  section the masked line classes and counts, the text-word sequences of fill and position lines, the monthly section
  lines, the label sets. Record the result in spec §2 / §5.3 as rev 1.6. Any line kind the spec does not list stops the
  task until the spec covers it.
- [ ] **Step 2: Write the failing tests** (fictional fixtures only): `test_daily_fills_and_close_link`,
  `test_daily_no_trade_day` (`無交易`, `fills_total` null), `test_daily_deposit_and_withdrawal`,
  `test_daily_option_and_futures_positions` (signed premium value), `test_daily_combo_margin_lines`,
  `test_equity_row_with_blank_columns`, `test_monthly_month_end_block`, `test_value_between_labels_rejected`
  (Review Focus 1), `test_value_matching_two_labels_rejected`, `test_unknown_line_rejected`,
  parametrised `test_daily_layout_rejected` with one case per §5.3 guard: section missing, repeated, out of order;
  header row missing, repeated; an 權益帳 label missing, repeated; an 權益帳 value row missing, doubled; page count 1
  and 3; page 2 with a section marker, a header row, a dated line, `總計`, `小計`; foreign currency; two dates; fill
  sequence `Taiwan` without `F`; an option fill with a strike and no right; a right and no strike; a futures fill with
  a right; position side not 買 / 賣 — each asserting `StatementError` with its code; parametrised
  `test_monthly_layout_rejected`: unknown line in each monthly section, section missing, repeated, a numeric word
  matching no label, a month-end label missing; parametrised `test_identity_broken_alone` (each §2 identity broken with the
  others intact → `identity:<name>`), `test_filename_date_differs_warning`, `test_payload_matches_portfolio_fixture`
  (equal to PR A's `futures_daily_payload.json` / `futures_monthly_payload.json`).
- [ ] **Step 3: Run** — FAIL.
- [ ] **Step 4: Implement.**
- [ ] **Step 5: Run** — PASS.
- [ ] **Step 6: Commit** `feat(worker): futures statement parser` (with the spec rev 1.6 change).

### Task 9: `python -m worker sinopac-futures`

**Files:**
- Create: `worker/sinopac_futures.py`
- Modify: `worker/config.py` (fields `sinopac_futures_daily_folder`, `sinopac_futures_monthly_folder`,
  `sinopac_futures_timeout_s`; env `STATEMENT_SINOPAC_FUTURES_DAILY_FOLDER`, `…_MONTHLY_FOLDER`, `…_TIMEOUT_S`),
  `worker/cli.py` (subcommand after `cathay`, same flags; `--ack` takes `YYYY-MM-DD` or `YYYY-MM` plus
  `--status conflict|findings`)
- Test: `tests/worker/test_sinopac_futures_job.py`

**Interfaces:**
- Consumes: Task 8; PR A endpoints.
- Produces: `run(cfg, *, portfolio_http, drive, dry_run, file_id, since, until, retry_date, ack_key, ack_status,
  unlatch, validate_history) -> dict` (summary as 國泰); files `sinopac-futures-state.json`,
  `sinopac-futures-disabled.json`, `.sinopac-futures.lock`, `sinopac-futures-history-report.json` in `state_dir`.

Order: daily files by statement date, then monthly files by month. Password candidates by folder key
(`cfg.sinopac_futures_daily_folder` / `cfg.sinopac_futures_monthly_folder`, as `_Job.candidates` does for 國泰).
Only a plain commit run reports a run.

- [ ] **Step 1: Write the failing tests** (fake drive, fake portfolio): parametrised
  `test_malformed_statement_rejected_at_job_boundary` (three of Task 8's malformed daily fixtures and one monthly →
  no POST, latch file written, summary codes `["latched", <code>]`), `test_validate_history_posts_one_batch` (one
  `/validate` call with every parsed payload in order; report = its answer; no state written),
  `test_posts_daily_then_monthly_in_order`,
  `test_state_skips_known_sha`, `test_same_statement_twice_in_run` (Review Focus 3),
  `test_portfolio_5xx_stops_without_latch` (Review Focus 4), `test_parse_failure_latches_and_posts_nothing`,
  `test_422_latches`, `test_decrypt_failure_latches`, `test_unlatch`, `test_dry_run_never_writes_state`,
  `test_ack_posts_status`,
  `test_only_plain_run_reports`, `test_missing_password_file_reports_failed`.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS; full worker suite — no new failures.
- [ ] **Step 5: Commit** `feat(worker): sinopac-futures statement job`.

### Task 10: Units, README and archive validation

**Files:**
- Create: `deploy/statements/homehub-portfolio-sinopac-futures.service`, `…timer` (`OnCalendar=*-*-* 06:45:00
  Asia/Taipei`, `Persistent=true`; service as the 國泰 one with `-m worker sinopac-futures`)
- Modify: `deploy/statements/README.md` (install, the two password lines, flags, ack, unlatch)

- [ ] **Step 1:** Write the units and README section.
- [ ] **Step 2:** `systemd-analyze verify` on both units — no errors.
- [ ] **Step 3:** After PR A is deployed: as `homehub-worker`, `python -m worker sinopac-futures --validate-history`
  against the real archive; the report lists 88 daily and 27 monthly files, every one parsed, and the findings the
  spec expects (5 `prev`, 4 `gap`, 0 `carry`). Report counts only; any parse failure goes back to Task 8.
- [ ] **Step 4: Commit** `feat(worker): futures statement timer and runbook`.

---

## PR C — notifier

### Task 11: 永豐期貨對帳單 section

**Files:**
- Create: `notify/sinopac_futures.py`
- Modify: `notify/config.py` (`sinopac_futures_run_time` 06:45, `sinopac_futures_run_grace_minutes` 15),
  `notify/cli.py:108-124`, `notify/render.py` (daily section 「永豐期貨對帳單」 after 「國泰對帳單」, weekly block
  「永豐期貨對帳單待處理」 after 「國泰對帳單待處理」), `notify/debits.py:103` / `notify/digest.py:68` (report field),
  `deploy/notify/homehub-notify.example.toml`
- Test: `tests/notify/test_sinopac_futures.py`

**Interfaces:**
- Produces: `futures_lines(cfg, now, client_factory=None) -> list[FuturesLine] | None` (as `cathay_lines`); line keys
  `futures:<key>:<status>`, `futures:run:latched`, `futures:run:failed`, `futures:stale:<iso>`, `futures:unreadable`.

Texts: conflict `11/03 收到不同版本的日對帳單` (month key: `2026-03 收到不同版本的月對帳單`); findings from the reason
codes — `prev` → `11/05 前日餘額接不上 10/28（中間有一天沒有日對帳單）`, `gap` → `2026-03 月對帳單有 1 天沒有日對帳單`,
`carry` → `2026-04 月初餘額接不上 3 月月底`, `diff` → `2026-03 月對帳單對不上：B K`; several codes on one key → one line,
joined with `；`; unreadable → 「永豐期貨對帳單狀態讀不到」.

- [ ] **Step 1: Write the failing tests**: the 國泰 notifier set for the new source (section off without
  `portfolio_base`, each status and code, latched / failed / stale timeline at 06:45, empty run history, item cap,
  section-only day sends, timeout / invalid JSON / missing field / bad timestamp / unknown status / unknown kind /
  oversized body / long reason → one unreadable item with debit messages still delivered); plus
  `test_both_sections_render_independently` (國泰 unreadable, futures fine → only 國泰 collapses).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS; `tests/notify` full — no new failures.
- [ ] **Step 5: Commit** `feat(notify): sinopac futures statement section`.

---

## PR D — SPA

Paths relative to `frontend/`. Test command: `npx ng test --watch=false --include='<spec glob>'`, then
`npx ng test --watch=false`. Never `npm run build` / `npm start` / `npm run config` in a worktree.

### Task 12: Totals include futures

**Files:**
- Modify: `src/app/models/portfolio.model.ts:301-321` (`futures_equity_twd` on history points and summary),
  `src/app/components/portfolio/dashboard/dashboard-math.ts` (`composition` fourth segment 期貨權益, `todayChange`,
  `carryHoldings`), `src/app/components/portfolio/dashboard/dashboard.ts:184-197,749-753` (`totalAssetsValue`),
  `dashboard.html:46-78` (legend)
- Test: `dashboard-math.spec.ts`, `dashboard.spec.ts`

- [ ] **Step 1: Write the failing tests**: `composition includes futures segment`, `totalAssetsValue keeps a present
  zero` (market 100, cash 0, futures −100, total "0" → 0), `totalAssetsValue falls back with futures` (total absent →
  market + cash + futures), `carryHoldings carries futures`, `todayChange counts futures`.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(spa): futures equity in portfolio totals`.

### Task 13: 永豐期貨 card

**Files:**
- Create: `src/app/components/portfolio/dashboard/futures-card/futures-card.{ts,html,scss,spec.ts}`
- Modify: `src/app/services/portfolio.service.ts` (`getFuturesSummary(asOf?: string)`), models, `dashboard.html`
  (card after the KPI cards)

Card content (§9): 權益總值, 可動用(出金)保證金, 原始 / 維持保證金, 超額 / 追繳保證金, 本月損益, 本月期貨平倉損益, source
date; when `frozen`: 「持倉中，數字停在 <MM/DD>」; `value` null → the card shows 「尚無期貨對帳單」. No 風險指標.

- [ ] **Step 1: Write the failing tests**: `renders summary fields`, `frozen note shows source date`,
  `empty state`, `negative equity renders` (Review Focus 5: −1,234 shown with the negative style, not hidden).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(spa): sinopac futures dashboard card`.

### Task 14: Import status card and notification center source

**Files:**
- Create: `src/app/components/portfolio/import/futures-status-card/futures-status-card.{ts,html,scss,spec.ts}`,
  `src/app/components/notifications/sources/sinopac-futures.ts` (+ spec)
- Modify: `import.html:288` (card `id="sinopac-futures"` after the 國泰 card; it also reads
  `getFuturesSummary()` from Task 13 and shows 權益總值 with the source date and, when `frozen`, 「持倉中，數字停在
  <MM/DD>」), `import.ts` (fragment list),
  `notif-task.ts` (`NotifSourceKey` `'sinopac_futures'`, `SOURCE_NAMES` 「永豐期貨對帳單」, command `ackFutures`),
  `notification-center.service.ts` (status entry, data signal, tasks, load round, validators, import-dot badges),
  `notifications.ts` (ack handler), `portfolio.service.ts` (pending, runs, ack calls)

- [ ] **Step 1: Write the failing tests**: status card — `lists pending items with ack`, `shows run state`,
  `frozen note for an old source with open positions`, `no frozen note when the source is today or flat`; source —
  `one task per item with id sinopac_futures:<key>:<status>`, `conflict and findings on one key give two tasks`,
  `run task when latched / failed / stale`, `查看 links to /portfolio/import#sinopac-futures`; service —
  `invalid payload marks the source unreadable without touching 國泰`.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS; full SPA suite — no new failures.
- [ ] **Step 5: Commit** `feat(spa): futures statement status card and notification source`.

---

PR A final review notes for later PRs (contracts as built): the validate call refreshes snapshots once per payload
and can take far longer than one import, so Task 9 gives `--validate-history` its own client timeout
(`sinopac_futures_validate_timeout_s`, default 600 s) and Task 10 reports its duration; a replay running from the
國泰 job holds the shared lock and can delay the 06:45 import (Task 10 README line); Task 9 never records an outcome
taken from a 5xx body; pending `reason` is null on `conflict` items (Task 11 validator accepts null); the ack route
returns 204 with no body (Task 14 client parses none); the summary's `month` is null only when `value` is null,
`pnl` is a string or null, `pnl_reason` ∈ {`no_baseline`, `findings`, null}.

## Rollout (after each PR's merge; owner go per step)

1. PR A: deploy the portfolio release (migration `f1futures`); `/history` unchanged except `futures_equity_twd` 0.
2. PR D: SPA publish (preview first, owner go). It renders exactly as today while futures is 0, and it must be live
   before step 4: the current SPA rewrites cash-only points to carried market + cash and draws a two-segment bar, so
   it would drop futures from the 淨值走勢 chart once the history fill runs (PR A final review).
3. The owner adds `投資/永豐期貨/日對帳單=$ID` and `投資/永豐期貨/月對帳單=$ID` to the worker password file.
4. PR B: update the worker checkout; `--validate-history` report to the owner (Task 10 Step 3, with the validate
   call's duration); then one plain commit run imports the archive; then, from `services/stock-portfolio-service` in
   the release checkout with the production env as the portfolio service runs it, `python -m
   app.services.sinopac_futures_backfill --from 2024-08-27 --dry-run` (counts to the owner), then the same without
   `--dry-run`; check on a before/after export of `/history` that every point changed by exactly its
   `futures_equity_twd`; enable the timer.
5. PR C: notifier release; the 9 archive findings appear; the owner acks them.
6. Account 147: `valuation_source = portfolio:sinopac_futures` by admin PATCH with the owner's go; the accounts page
   shows 市值 for 147.
7. Clean up `~homehub-worker/tmp/sinopac-spike*` and the census scratch.
