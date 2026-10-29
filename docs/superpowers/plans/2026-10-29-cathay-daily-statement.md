# 國泰證券 Daily Statement Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import every 國泰證券 cash / day-trade fill from the daily statement PDF into the portfolio the morning after,
with date ownership against the CSV import and notifier alerts for stuck dates.

**Architecture:** The accounting-service worker reads the PDF from Drive (rclone), extracts text with pypdfium2,
parses and self-checks it, and posts a JSON payload to a new stock-portfolio-service endpoint. The portfolio maps the
lines onto CSV-shaped transactions under a per-date lock and records ownership; the CSV import consults that record.
The notifier reads two new portfolio endpoints in an isolated step.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2, Alembic, Postgres (SQLite in tests), httpx, pypdfium2 5.14.0,
pydantic, Angular 21 (one label).

**Spec:** `docs/superpowers/specs/2026-10-29-cathay-daily-statement-design.md` rev 2.4 (`97da2b25`); rev 2.5 (PR B) records the real text layout from the 466-statement archive. Section numbers
below (§x) refer to it. Read the spec before any task.

## Global Constraints

- Fictional fixtures only: no real statement, name, amount, account or order number in code, tests, docs or commits.
  Use names like `範例電子`, codes like `9999`, order numbers like `XY12300`.
- Supported 類別: 集買, 集賣, 沖買, 沖賣. 資買 / 資賣 / 券買 / 券賣 → `held` / `unsupported_category` (§4.1).
- Buy lines (集買, 沖買) require tax = 0, in the worker and in the portfolio (§4.2).
- Net identity per line: buy `net = −(amount + fee)`, sell `net = amount − fee − tax` (§2, §4.2).
- Order number: `[A-Za-z0-9]{7}`; the CSV 委託書號 is it minus the last two characters (§2, §5.5).
- Fingerprint namespace: `_broker_transaction_fingerprint(broker="TW_CATHAY_STATEMENT", market="TW", currency="TWD", note=<7-char order_no>, …)` (§5.1).
- Inserted rows: same columns as `broker_cathay_service._insert_transaction`, `broker` NULL (§5.1).
- Lock: `pg_advisory_xact_lock(<key>)` per trade date, ascending, inside the writing transaction (§5.3).
- Worker timer 06:40 Asia/Taipei; notifier keys `portfolio_base`, `cathay_run_time` = `06:40`, `cathay_run_grace_minutes` = 15 (§3, §7.2).
- Pending list ≤ 50 items with `total` / `truncated`; notifier ≤ 10 items + 「另有 N 天」; `reason` ≤ 40 chars; line ≤ 120 chars; portfolio client timeout 10 s, body ≤ 64 KiB (§7).
- Cutover 2026-10-16: earlier dates are dry-run validation only (§8).
- Portfolio tests run only with a sandbox root `.env` (dummy credentials, `POSTGRES_PORT=9`), e.g. the worktree's own; never the production `.env`.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. A CSV upload whose dates are half owned and half not: owned dates come back `covered` or `error`, the other dates import exactly as before in the same request — Task 4 `test_csv_mixed_owned_and_free_dates`.
2. Two statements for the same date arriving in one worker run (the Apps Script saved a re-sent mail twice, identical bytes): one `imported`, the other `duplicate`, no double rows — Task 9 `test_same_sha_twice_in_one_run`.
3. A worker run where Drive is unreachable: run report `failed` reaches the portfolio when the portfolio is up, no latch, exit 1 — Task 9 `test_drive_failure_reports_failed_run`.
4. A pending date older than the cutover (would only appear from a manual post): the notifier still lists it; nothing filters by cutover on the read side — Task 11 `test_pending_lists_any_date`.
5. A statement whose holdings table is empty (everything sold that day): accepted, `holdings_diff` lists the portfolio's remaining positions as differences rather than crashing — Task 3 `test_holdings_diff_empty_table`.

## Delivery

Three PRs in this order, each from `origin/main` (never from a local main): **A** portfolio (Tasks 1–7, branch
`feat/cathay-statement-portfolio`), **B** worker (Tasks 8–10, `feat/cathay-statement-worker`), **C** notifier
(Tasks 11–12, `feat/cathay-statement-notify`). B and C may branch from A's merge. Each PR gets the Multica review
of the owner's process before merge.

---

## PR A — stock-portfolio-service

Paths relative to `services/stock-portfolio-service/`.

### Task 1: Ownership and run tables

**Files:**
- Create: `app/models/cathay_statement.py`, `alembic/versions/b6cathay_statement_day.py`
- Modify: `app/main.py:7` (model import list), `alembic/env.py:10-14` (add `import app.models.cathay_statement  # noqa`)
- Test: `tests/unit/test_cathay_statement_models.py`

**Interfaces:**
- Produces: models `CathayStatementDay` (table `cathay_statement_day`) and `CathayStatementRun` (table `cathay_statement_run`).

`CathayStatementDay` columns: `trade_date` Date PK; acceptance `accepted_kind` String(16) nullable (`imported` |
`verified`), `accepted_sha256` String(64), `accepted_at` timestamptz, `canonical_lines` JSON; latest attempt
`last_sha256` String(64), `last_status` String(16), `last_reason` String(80), `last_detail` JSON, `last_at`
timestamptz, `since` timestamptz (first attempt in the current status); acknowledgement `ack_sha256` String(64),
`ack_status` String(16), `ack_at` timestamptz; `drive_file_id` String(128). Check constraints on the two status
columns with the sets of §5.2. JSON columns are `sa.JSON().with_variant(postgresql.JSONB(), "postgresql")` (SQLite
tests use `create_all`).

`CathayStatementRun` columns: `id` PK, `kind` String(8) (`latest` | `ok`, unique), `run_at` timestamptz, `result`
String(8) (`ok` | `latched` | `failed`), `code` String(64) nullable, `files_seen` Integer, `posted` Integer. Two rows
at most, upserted by `kind`.

Migration `revision = "b6cathay"`, `down_revision = "a5receipt"`; `downgrade` drops both tables.

- [ ] **Step 1: Write the failing tests** — `test_day_row_roundtrip` (insert a day with `canonical_lines=[{...}]`, read back equal dict), `test_status_check_rejects_unknown` (`last_status="bogus"` → `IntegrityError` on flush), `test_run_kind_unique`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest -p no:cacheprovider -q tests/unit/test_cathay_statement_models.py` — FAIL (module missing).
- [ ] **Step 3: Implement the models and the migration.**
- [ ] **Step 4: Run the tests** — PASS. Then `alembic upgrade head` and `alembic downgrade -1` against a throwaway Postgres (see Task 6 Step 1 for the container) — both succeed.
- [ ] **Step 5: Commit** `feat(portfolio): cathay statement ownership and run tables`.

### Task 2: Payload schema, mapping and fingerprint

**Files:**
- Create: `app/schemas/cathay_statement.py`, `app/services/broker_cathay_statement_service.py`
- Test: `tests/unit/test_cathay_statement_mapping.py`

**Interfaces:**
- Produces:
  - pydantic `StatementPayload` (fields exactly the §4 JSON: `source{drive_file_id, sha256, file_name}`, `trade_date: date`, `settlement_date: date`, `net_amount: Decimal`, `lines: list[StatementLine]`, `totals`, `holdings: list[Holding]`, `warnings: list[str]`), `StatementLine` (`name, category, quantity: int, price, amount, fee, tax, net: Decimal, order_no: str, points: bool`), `Holding(code, name, shares: int)`. Model validators repeat §4.2: per-line amount and net identity, buy tax 0, totals sums, header net; `category` is `Literal["集買", "集賣", "沖買", "沖賣"]`. An unsupported statement (§4.1) arrives as `lines: []` with `"unsupported_category"` in `warnings`; the sum checks are skipped only in that shape (any other empty-lines payload → 422). Any failure → FastAPI 422.
  - `CATEGORY_TO_CSV_SIDE: dict[str, str]` = {集買: 現買, 集賣: 現賣, 沖買: 沖買, 沖賣: 沖賣}.
  - `resolve_line_symbol(db, name: str, holdings: list[Holding]) -> str | None` — `broker_cathay_service.resolve_symbol(name)`, else the single distinct `Transaction.symbol` with `Transaction.name == name` and `market == "TW"`, else the holdings code for that name, else None (ambiguity at any step = try the next step; none left = None).
  - `statement_fingerprint(row: ParsedRow, order_no: str) -> str` — `_broker_transaction_fingerprint(broker="TW_CATHAY_STATEMENT", symbol, market="TW", type_, quantity=Decimal(q), price, trade_date, fee, tax, currency="TWD", note=order_no)`.
  - `map_statement(db, payload) -> Mapped | HeldReason` where `Mapped(rows: list[ParsedRow], canonical: list[dict])` and `HeldReason(code: str, name: str | None)`; codes `unsupported_category`, `unresolved_symbol`. A `ParsedRow.payload` carries the same keys as `parse_cathay_rows` (symbol, type, position_side, quantity, price, trade_date = `datetime(d, 00:00, UTC)`, fee, tax, name, order_id = `order_no[:-2]`, broker_subtype = CSV side[0], broker_day_trade_marker = CSV side if 沖 else None). Canonical dict keys: symbol, type, position_side, marker, quantity, price (str), fee (str), tax (str), order_no.

- [ ] **Step 1: Write the failing tests**:
  - `test_mapping_table` — each of the four categories → (type, position_side, marker) = (BUY, LONG, None), (SELL, LONG, None), (BUY, LONG, "沖買"), (SELL, LONG, "沖賣").
  - `test_unsupported_payload_holds` — `lines=[]`, `warnings=["unsupported_category"]` → `HeldReason("unsupported_category", None)`; `lines=[]` without the warning → `ValidationError`.
  - `test_symbol_resolution_order` — name in `name_to_symbol` wins; else unique stored name; else holdings code; else `HeldReason("unresolved_symbol", "範例電子")`.
  - `test_fingerprint_namespace_disjoint` — for one line, `statement_fingerprint` ≠ `_transaction_fingerprint(... order_id=order_no[:-2])` ≠ the same with the full order_no.
  - `test_payload_rejects_buy_tax` — a 集買 line with tax 7, totals and net adjusted → `ValidationError`.
  - `test_payload_rejects_fee_change_matched_in_totals` — fee +1 on a line and in totals, nets unchanged → `ValidationError` (net identity).
  - `test_payload_field_negatives` (parametrised, each → `ValidationError`): quantity 0 and 1.5; price 0; amount / fee / tax negative or fractional; order_no of 6 and of 8 characters and with `-`; holdings shares −1; holdings code `12`; Σ amount, Σ fee, Σ tax and Σ net each off by 1 against `totals`; header `net_amount` ≠ `totals.net`; `warnings=["unsupported_category"]` with a non-empty `lines` whose sums are off (the warning never bypasses checks on real lines).
  - `test_payload_roundtrip` — Task 8's fictional `to_payload` output (copied as a JSON fixture `tests/fixtures/cathay_statement_payload.json`) validates and dumps back equal.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(portfolio): cathay statement payload, mapping and fingerprint`.

### Task 3: Per-date lock and statement import

**Files:**
- Modify: `app/services/broker_cathay_statement_service.py`
- Test: `tests/integration/test_cathay_statement_import.py`

**Interfaces:**
- Consumes: Task 1 models; Task 2 `map_statement`, `statement_fingerprint`.
- Produces:
  - `lock_trade_dates(db: Session, dates: Iterable[date]) -> None` — sorted unique dates; on the `postgresql` dialect `SELECT pg_advisory_xact_lock(:k)` with `k = 0x43415448 << 32 | d.toordinal()` (`CATH` tag in the high word); on any other dialect a no-op (SQLite tests are single-writer; Task 6 tests the real lock).
  - `TW_CATHAY_FILTER` — SQLAlchemy clause `Transaction.market == "TW" and (Transaction.broker.is_(None) | (Transaction.broker == "TW_CATHAY"))`.
  - `import_statement(db: Session, payload: StatementPayload, *, dry_run: bool) -> StatementOutcome` with `StatementOutcome(outcome: str, trade_date: date, reason: str | None, counts: dict[str, int], holdings_diff: list[dict], created_ids: list[int], errors: list[str])`.

Behaviour: commit runs `if db.in_transaction(): db.rollback()` then `with db.begin():` (the CSV path's pattern), `lock_trade_dates`, re-read the day row and the date's TW Cathay rows, then §5.4 cases 1–5 in order. Case 4 inserts via `broker_cathay_service._insert_transaction(db, row, cash_account_id)` (resolve the account once with `cash_account_service.resolve_default_cathay_twd_account` when `cash_leg_enabled()`), with each row's fingerprint replaced by `statement_fingerprint`. Case 5 grouping key (symbol, type, position_side, marker, price) summing quantity, fee, tax; the DB marker is `broker_day_trade_marker`. `since` is set when `last_status` changes, kept when it repeats. Dry run: no lock, no write, same outcome computed from a plain read. Holdings diff (§5.6) after acceptance: for each symbol in the holdings table or with a LONG position, `{symbol, statement_shares, portfolio_shares}` where they differ; portfolio shares = Σ BUY − Σ SELL LONG quantity of TW Cathay rows with trade date ≤ D.

- [ ] **Step 1: Write the failing tests** (fixtures `db_session`; a `_payload(**overrides)` builder with fictional lines; `CASH_LEG_ENABLED=true` and `_add_cathay_account` as in `tests/integration/test_cathay_import_cash_leg.py`):
  - `test_case4_inserts_and_owns` — empty DB → outcome `imported`, 2 transactions with statement fingerprints, `broker` NULL, cash legs present, day row `accepted_kind == "imported"`, `canonical_lines` length 2.
  - `test_case1_same_sha_duplicate` — post twice → second `duplicate`, still 2 rows.
  - `test_case2_corrected_sha_conflict_keeps_rows` — second post with another sha and a changed line → `conflict`; rows, fingerprints and `accepted_sha256` unchanged; `last_detail` holds both line sets.
  - `test_case3_held_unresolved_name` — unknown name, empty holdings → `held`, reason `unresolved_symbol:範例電子`, no rows, `accepted_kind` NULL.
  - `test_case3_unsupported_holds` — Task 2's unsupported shape → `held`, reason `unsupported_category`, nothing written.
  - `test_case3_held_then_fixed_imports` — add a stored row with that name elsewhere (another date), post again → `imported`.
  - `test_case5_csv_first_verifies` — seed CSV-shaped rows (two fills summing to the line) → `verified`, no new rows, canonical lines stored.
  - `test_case5_marker_differs_mismatch` — seed rows with marker NULL for a 沖買 line → `mismatch`, not owned; then fix the marker and repost the same sha → `verified`.
  - `test_tw_manual_row_ignored` — a `TW_MANUAL` row on D does not block case 4.
  - `test_dry_run_writes_nothing` — dry run of case 4 → outcome `imported`, zero rows, no day row.
  - `test_failure_rolls_back_ownership` — monkeypatch `sync_transaction_cash_legs` to raise after the first insert → no transactions, no cash-leg rows, no day-trade flag changes on the date's other rows, no day row.
  - `test_verified_then_same_sha_duplicate` — case 5 `verified`, same sha again → `duplicate`, nothing written.
  - `test_since_kept_on_repeat_and_reset_on_change` — held twice (`since` unchanged, `last_at` advances), then mismatch (`since` = the new attempt's time).
  - `test_holdings_diff_in_response_and_detail` — imported with one differing position → the same list in the outcome and in `last_detail["holdings_diff"]`; a held outcome → `holdings_diff == []`.
  - `test_holdings_diff_empty_table` — accepted statement with `holdings=[]` while the portfolio holds a position → diff lists it.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS; then the full portfolio suite — no new failures.
- [ ] **Step 5: Commit** `feat(portfolio): cathay statement import with date ownership`.

### Task 4: CSV import on owned dates

**Files:**
- Modify: `app/services/broker_cathay_service.py` (`parse_cathay_transactions_csv` :649, `_dry_run_rehash` :420, `_commit_rehash` :502), `app/services/import_service.py:93` (`RowStatus` gains `"covered"`), `ImportResult` (new counter `covered: int = 0`), `app/routers/imports.py` `_serialize_result` (emit `covered`)
- Test: `tests/integration/test_cathay_csv_owned_dates.py`

**Interfaces:**
- Consumes: Task 3 `lock_trade_dates`, `CathayStatementDay`.
- Produces: `split_owned(rows: list[ParsedRow], owned: dict[date, CathayStatementDay]) -> tuple[dict[date, list[ParsedRow]], list[ParsedRow]]`; `covered_check(rows: list[ParsedRow], canonical: list[dict]) -> bool`.

Behaviour (§5.5): dry run reads the owned dates once and previews; commit inside `_commit_rehash`'s `with db.begin():` first calls `lock_trade_dates` for every date in the batch, re-reads ownership, then for each owned date runs `covered_check` — grouping CSV rows by (symbol, type, position_side, marker, price, order_id) and canonical lines by (symbol, type, position_side, marker, price, order_no[:-2]), comparing summed quantity / fee / tax. Equal → every row of that date `covered`; unequal → every row of that date `error` with a `ParseError` naming the date, nothing of that date written. Free-date rows go through the existing legacy / duplicate / business-key / insert branches unchanged. Statement rows exist only on owned dates and the business key includes the date, so the rehash branches cannot select one; add an assertion test rather than a fingerprint check (a sha256 does not reveal its namespace).

- [ ] **Step 1: Write the failing tests**:
  - `test_csv_on_imported_date_is_covered` — statement imported (Task 3 helper), CSV with the same fills split in two → both rows `covered`, `ImportResult.covered == 2`, no new transaction.
  - `test_csv_on_owned_date_mismatch_errors` — one fill quantity off → rows of that date `error`, nothing written for that date.
  - `test_csv_mixed_owned_and_free_dates` — one owned date, one free date in one upload → owned rows `covered`, free rows `new` and inserted.
  - `test_csv_owned_mismatch_free_date_still_imports` — owned date unequal, free date in the same upload → owned rows `error`, free rows inserted, request 200.
  - `test_conflict_then_csv_multifill_unchanged` — imported → corrected sha (`conflict`) → CSV multi-fill upload → transactions and fingerprints byte-identical to before.
  - `test_dry_run_previews_covered` — dry run shows `covered` and writes nothing.
  - `test_rehash_never_touches_statement_rows` — a legacy-fingerprint row on a free date next to statement rows on an owned date with an identical business key except the date → only the free-date row is rehashed.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS; full suite — no new failures.
- [ ] **Step 5: Commit** `feat(portfolio): CSV import respects statement-owned dates`.

### Task 5: Endpoints

**Files:**
- Modify: `app/routers/imports.py`
- Test: `tests/integration/test_cathay_statement_routes.py`

**Interfaces:**
- Consumes: Task 3 `import_statement`; Task 1 models.
- Produces routes under `/api/portfolio/imports/cathay-statement`:
  - `POST ""` (`dry_run: bool = Query(False)`, body `StatementPayload`) → 200 `{"outcome", "trade_date", "reason", "counts", "holdings_diff", "errors"}`; schedules the recalc chain with `_touched_symbols_and_min_trade_date` / `_maybe_schedule_chain` when rows were created. 422 from validation.
  - `GET "/pending"` → `{"items": [{"trade_date", "status", "reason", "since", "last_at"}], "total", "truncated"}` per §7.1 (≤ 50 items, ack excluded by (sha, status) equality, `reason` cut to 40 chars).
  - `POST "/runs"` body `{"run_at", "result", "code", "files_seen", "posted"}` → 204; upserts `latest`, and `ok` when result is ok.
  - `GET "/runs/latest"` → `{"latest": row | null, "latest_ok": row | null}`.
  - `POST "/days/{trade_date}/ack"` → 200 `{"trade_date", "ack_status"}`; 404 when the date has no row or nothing pending; never touches acceptance.

- [ ] **Step 1: Write the failing tests** (`client`):
  - `test_post_statement_imports` — 200, outcome `imported`.
  - `test_post_statement_422_buy_tax` — 422, no row.
  - `test_pending_envelope_and_cap` — 51 held dates → 50 items, `total == 51`, `truncated is True`, sorted by date.
  - `test_pending_lists_any_date` — a held date before 2026-10-16 is listed.
  - `test_ack_identical_retry_stays_acked` — held → ack → same sha reposted (held again) → not pending; a new sha → pending again.
  - `test_ack_conflict_keeps_acceptance` — imported → conflict → ack → not pending, `accepted_kind` unchanged.
  - `test_pending_status_matrix` — not owned + held, not owned + mismatch, owned + conflict → all three listed with their status; owned + duplicate / imported / verified last → not listed.
  - `test_ack_same_sha_new_status_reappears` — held → ack → same sha now `mismatch` (a CSV row added) → pending again.
  - `test_ack_held_then_import` — held → ack → fixed and reposted → `imported`, not pending.
  - `test_runs_latest` — post failed, then ok → `latest.result == "ok"`, `latest_ok` set; post latched → `latest.result == "latched"`, `latest_ok` unchanged.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(portfolio): cathay statement endpoints`.

### Task 6: Real lock on Postgres

**Files:**
- Test: `tests/integration/test_cathay_statement_lock_pg.py` (skips unless `CATHAY_PG_TEST_URL` is set; its own variable so the sandbox `.env` stays untouched and the suite never reaches a real database)

- [ ] **Step 1: Start a throwaway Postgres** — `docker run -d --rm --name cathay-pg-test -e POSTGRES_PASSWORD=test -p 127.0.0.1:55439:5432 postgres:16-alpine`; `CATHAY_PG_TEST_URL=postgresql://postgres:test@127.0.0.1:55439/postgres` (the shared lib's psycopg2 scheme); the test builds its schema with `alembic upgrade head` against that URL in a module fixture.
Both callers reach the lock as the module attribute `broker_cathay_statement_service.lock_trade_dates` (Tasks 3 and 4 call it that way), so the test can wrap it: the wrapper takes the real lock, sets an `Event` `first_locked`, and waits on an `Event` `release` before returning.

- [ ] **Step 2: Write the tests** (two `Session(engine)` in a `ThreadPoolExecutor`):
  - `test_statement_first_csv_waits[statement_first]` and `[csv_first]` (parametrised roles): the first path runs with the wrapper and stops holding the lock; once `first_locked` is set the second path starts unwrapped; poll `pg_locks` (`locktype = 'advisory' AND NOT granted`, matched to the second connection's `pg_backend_pid()` and the date's key, ≤ 5 s) until the second path is seen waiting; `release` is set in a `finally` so a failing case never hangs — the deterministic proof it blocks; set `release`; join both. Statement first → statement rows only, CSV rows `covered`; CSV first → CSV rows only, statement `verified`. Both cases also assert cash-leg rows equal the transaction rows' and exactly one ownership outcome.
  - `test_lock_order_ascending` — capture executed SQL with a `before_cursor_execute` listener; a CSV commit with dates given D3, D1, D2 locks the keys in ascending date order.
- [ ] **Step 3: Run against the container** — all PASS. A no-op lock is not used as a control: with the pause at the lock, a no-op still serialises by luck, so the `pg_locks` wait is the evidence.
- [ ] **Step 4: Stop the container** (`docker stop cathay-pg-test`).
- [ ] **Step 5: Commit** `test(portfolio): cathay date lock on Postgres`.

### Task 7: 匯入 page shows `covered`

**Files:**
- Modify: `frontend/src/app/models/portfolio.model.ts:65` (`ImportRowStatus` + `covered`), `frontend/src/app/components/portfolio/import/import-math.ts` (`RowResult`, `ROW_RESULT_LABEL` `covered: '已由對帳單匯入'`, `ROW_RESULTS`, `RESULT_VARIANT` same variant as `duplicate`, dimmed like `duplicate`, counted apart in `importCounts`)
- Test: `frontend/src/app/components/portfolio/import/import-math.spec.ts`

- [ ] **Step 1: Write the failing tests** — `covered row gets the label and duplicate variant`, `covered rows are not counted as new`.
- [ ] **Step 2: Run** `cd frontend && npx vitest run src/app/components/portfolio/import/import-math.spec.ts` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS; `npm run build` succeeds.
- [ ] **Step 5: Commit** `feat(frontend): show statement-covered CSV rows`.

---

## PR B — accounting-service worker

Paths relative to `services/accounting-service/`.

### Task 8: Statement parser

**Files:**
- Create: `worker/cathay_statement.py`
- Test: `tests/worker/test_cathay_statement.py`, fixtures `tests/worker/fixtures/cathay/*.txt` (fictional texts written in the pypdfium2 layout of §2 / §4.1)

**Interfaces:**
- Produces:
  - `class StatementError(ValueError)` with `.code: str` (codes: `header`, `date_line`, `item_grammar`, `line3`, `line4`, `stray_line`, `amount`, `net_identity`, `buy_tax`, `totals`, `net_total`, `holdings`).
  - `extract_text(data: bytes, password: str | None) -> str` — pypdfium2 per §4.1 (pages joined by `"\n"`).
  - `parse_statement(text: str) -> ParsedStatement` — dataclass with `trade_date`, `settlement_date`, `net_amount`, `lines` (dicts with the §4 keys), `totals`, `holdings`, `unsupported: bool` (any item line whose 類別 is another two-character value, §4.1; the header, footer and holdings checks still run, the sum checks are skipped, and `lines` is empty).
  - `to_payload(st: ParsedStatement, *, drive_file_id: str, sha256: str, file_name: str) -> dict` — the §4 JSON (Decimals as strings), `warnings` gains `filename_date_differs` when the file-name date prefix ≠ trade date, and `unsupported_category` (with `lines: []`) when `st.unsupported`.

- [ ] **Step 1: Write fixtures and failing tests**: one fixture per shape (single line; two pages; 沖買 + 沖賣 pair; `(小樹點數) ` prefix; one order in two price groups `XY12300` / `XY12301`; a 資買 line → `unsupported`); negatives, each expecting its code: header/date disagree; a stray line inside the item section; line 3 with five numbers; line 4 not `0`; amount off by 2; fee changed with every total changed to match (`net_identity`); buy tax 7 with footer tax 7 and nets unchanged (`buy_tax`); Σ fee ≠ 總合計; header net ≠ 淨收付金額; holdings code `12`; plus `test_item_field_negatives` (parametrised, each → its code): qty `0` and `1.5`, price `0`, amount / fee / tax `-1` or `1.5`, order_no of 6 / 8 characters or with `-`, holdings shares `-1`; and `test_filename_date_warning_only` — `to_payload` with file name `2026-10-20_…` for a 10/15 statement → `warnings == ["filename_date_differs"]`, every other field equal to the same-name case. After the first unsupported item line the rest of the item section is not checked line by line (margin lines carry columns §4.1 leaves open). Plus `test_extract_text_layout` — generate a 2-page encrypted PDF of ASCII lines with `tests/worker/pdfgen.py` (it renders latin-1 only; the Chinese grammar is tested on the `.txt` fixtures), assert `extract_text` returns both pages' text separated by a newline.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest -p no:cacheprovider -q tests/worker/test_cathay_statement.py` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Owner-data check (not committed):** run the parser over the decrypted archive texts in the author's scratchpad (never in the repo) and record counts in the task report only: statements parsed, lines, `unsupported` count (expect 2 statements), zero `StatementError`.
- [ ] **Step 6: Commit** `feat(worker): cathay daily statement parser`.

### Task 9: `python -m worker cathay`

**Files:**
- Create: `worker/cathay.py`
- Modify: `worker/cli.py` (subcommand), `worker/config.py` (fields below)
- Test: `tests/worker/test_cathay_job.py`

**Interfaces:**
- Consumes: Task 8; `drive.Drive`, `inbox.publish`, `passwords.load`, `pdf.unlock`, `parser.write_private_json`.
- Produces: `run(cfg, *, portfolio_http: httpx.Client, drive: Drive, dry_run=False, file_id: str | None = None, since: date | None = None, until: date | None = None, retry_date: date | None = None, ack_date: date | None = None, unlatch=False, validate_history=False) -> dict`; exceptions `CathayDisabled`, `AlreadyRunning` (same shapes as flex).

Config (env, `config.py` style): `STATEMENT_CATHAY_FOLDER` default `投資/國泰證券/日對帳單` (also the password key), `STATEMENT_CATHAY_CUTOVER` default `2026-10-16`, `STATEMENT_CATHAY_TIMEOUT_S` default 60.

Behaviour (§6): lock `state_dir/.cathay.lock`; latch `state_dir/cathay-disabled.json`; state file `state_dir/cathay-state.json` `{"<drive_file_id>:<sha256>": {"outcome", "trade_date", "md5", "at"}}`. List `cfg.drive_root + "/" + folder` with `Drive._list_root`; a listed file whose id and Drive md5 match a state entry with a terminal outcome (`imported`, `verified`, `duplicate`, `conflict`, `mismatch`, `pre_cutover`) is skipped without download unless `retry_date` equals that entry's `trade_date` (the file name never decides anything, §4.2); otherwise download by id into staging, `inbox.publish` for sha256, `pdf.unlock` with the folder's candidates, `extract_text`, `parse_statement`, `to_payload`; an `unsupported` statement posts the Task 8 payload (`lines: []`, warning `unsupported_category`) and gets `held` back like any held date. **After parsing**, the 成交日期 decides: outside `--since` / `--until` → skipped; before the cutover on a run without `validate_history` → not posted, state entry `pre_cutover` on a commit run; before the cutover with `validate_history` → posted with `dry_run=true` regardless of other flags. POST `/api/portfolio/imports/cathay-statement` (`dry_run` query from the flag or `validate_history`), validate the body (outcome in the set, trade_date equal, errors empty for imported/verified). Write the state entry only on a commit run and only for terminal outcomes (`held` never). `--ack D` posts `…/days/D/ack` and exits. At the end of every commit run (also on latch or failure) POST `…/runs`. Latch on decrypt failure, any `StatementError`, HTTP 422; transient on 5xx / 408 / 429 / timeout / invalid body; Drive errors → run result `failed`, exit 1, no latch. `--file ID` limits the run to one Drive file and `--since` / `--until DATE` to a 成交日期 range (§3 owner-run command). `--validate-history` is a dry run of every listed file (state ignored, nothing written to state, no run report) and writes `state_dir/cathay-history-report.json` (0600) `{"counts": {outcome: n}, "mismatch_dates": [...], "held": [{"trade_date", "reason"}], "errors": [{"file_name", "code"}]}`; it prints the counts only. A dry run never sets or clears the latch; while latched, every run except `--unlatch` refuses (flex's rule). CLI flags: `--dry-run`, `--file ID`, `--since DATE`, `--until DATE`, `--retry DATE`, `--ack DATE`, `--unlatch`, `--validate-history`; exit codes as flex (3 already running, 1 disabled/failed, 2 config).

- [ ] **Step 1: Write the failing tests** (fakes: `FakeRunner` for rclone as in `tests/worker/test_drive.py`, `httpx.MockTransport` portfolio as in `tests/worker/test_flex.py`, PDFs from `pdfgen.py` with fixture text):
  - `test_imports_and_records_state`, `test_held_reposted_next_run`, `test_mismatch_not_reposted_without_retry`, `test_retry_reposts_mismatch`, `test_dry_run_never_writes_state`, `test_422_latches`, `test_statement_error_latches`, `test_unlatch_after_clean_run`;
  - cutover by parsed date (fictional cutover 2026-10-16): `test_name_after_cutover_trade_before_is_not_committed` (file `2026-10-20_…`, 成交日期 10/15 → no POST on a daily run, state `pre_cutover`, and with `validate_history` exactly one POST carrying `dry_run=true`), `test_name_before_cutover_trade_after_is_committed` (file `2026-10-14_…`, 成交日期 10/16 → committed POST), `test_range_uses_trade_date`;
  - `test_validate_history_report` — three files answered verified / mismatch / held → report counts `{verified: 1, mismatch: 1, held: 1}`, `mismatch_dates` = that date, file mode 0600, state file unchanged, no `…/runs` POST;
  - `test_all_passwords_fail_latches`;
  - `test_transient_statuses_no_latch` (parametrised 500, 502, 408, 429, `httpx.ReadTimeout`, invalid body) → exit 1, no latch, no state entry;
  - `test_dry_run_keeps_latch` — latched → `--dry-run` refuses with `CathayDisabled` / exit 1 like flex (`worker/flex.py:288`), and `--dry-run --unlatch` after a clean dry run leaves the latch file in place (only a clean commit run clears it, `flex.py:320`); `test_commit_after_dry_run` — a dry run then a commit run on the same file → committed POST, state written; `test_run_report_posted_on_ok_latched_failed`, `test_same_sha_twice_in_one_run`, `test_drive_failure_reports_failed_run`, `test_ack_posts_and_exits`, `test_file_and_range_filters`, `test_cli_exit_codes`.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS; full accounting suite — no new failures.
- [ ] **Step 5: Commit** `feat(worker): cathay statement job`.

### Task 10: Units and README

**Files:**
- Create: `deploy/statements/homehub-portfolio-cathay.service`, `deploy/statements/homehub-portfolio-cathay.timer`
- Modify: `deploy/statements/README.md` (new section after "IB Flex statement timer"; Install glob `homehub-{statements,portfolio-flex,portfolio-cathay}*`)

Service = the flex service with `ExecStart=… -m worker cathay`; timer `OnCalendar=*-*-* 06:40:00 Asia/Taipei`, `Persistent=true`. README: password line `投資/國泰證券/日對帳單=$ID`, state files, latch / `--unlatch`, `--retry`, `--ack`, `--validate-history`, enabling the timer, the Loki query.

- [ ] **Step 1: Write** the two units and the README section.
- [ ] **Step 2: Verify** `systemd-analyze verify --user deploy/statements/homehub-portfolio-cathay.service deploy/statements/homehub-portfolio-cathay.timer` (warnings about missing paths on this host are acceptable; syntax errors are not).
- [ ] **Step 3: Commit** `feat(worker): cathay statement timer and docs`.

---

## PR C — notifier

Paths relative to `services/accounting-service/`.

### Task 11: Portfolio status reader

**Files:**
- Create: `notify/cathay.py`
- Modify: `notify/config.py` (`portfolio_base: str | None = None`, `cathay_run_time: time = time(6, 40)`, `cathay_run_grace_minutes: int = 15`; validated like the other optionals, `HH:MM` for the time)
- Test: `tests/notify/test_cathay.py`, `tests/notify/test_notify_config.py`

**Interfaces:**
- Produces:
  - `CathayLine(key: str, text: str)` (frozen).
  - `cathay_lines(cfg: NotifyConfig, now: datetime, client_factory: Callable[..., httpx.Client] | None = None) -> list[CathayLine] | None` — None when `portfolio_base` is unset. A None factory resolves to `httpx.Client` **at call time** (module attribute lookup, so `cli.httpx.Client` patches in `test_cli.py:75-77` reach it). Builds its own client (`base_url=cfg.portfolio_base`, `timeout=10`), reads `GET /api/portfolio/imports/cathay-statement/pending` and `…/runs/latest` with `client.stream(...)`, counting bytes while iterating and aborting past 65536 (the body is never loaded unbounded), validates strictly (status 200, exact keys, ISO dates / timestamps, status in {held, mismatch, conflict}, run result in {ok, latched, failed}, `reason` ≤ 40 chars, `total` int ≥ len(items)), then returns lines: per pending item (first 10 by date) `MM/DD 保留：<reason text>` / `MM/DD 對不上 CSV` / `MM/DD 收到不同版本的對帳單`, then `另有 N 天` when total > shown, then a run line `國泰對帳單匯入已停用` (latched) / `國泰對帳單匯入失敗` (failed) / `國泰對帳單今天沒有執行` (stale per §7.2) — each ≤ 120 chars. **Any exception inside** (including validation) returns `[CathayLine("cathay:unreadable", "國泰對帳單狀態讀不到")]`. Keys: `cathay:{date}:{status}`, `cathay:more`, `cathay:run:{result}`, `cathay:stale:{scheduled run ISO}`.
  - `last_scheduled_run(now: datetime, run_time: time, grace_minutes: int) -> datetime` (Asia/Taipei).

- [ ] **Step 1: Write the failing tests** (`make_client` from `notify_fakes.py` passed through `client_factory`):
  - `test_off_without_portfolio_base` → None.
  - `test_lines_per_status` — held (reason `unresolved_symbol:範例電子` → `10/27 保留：名稱對不到代號 範例電子`), mismatch, conflict.
  - `test_cap_and_more` — 12 items, total 51 → 10 lines + `另有 41 天`.
  - `test_pending_lists_any_date` — a 2026-09 date is listed.
  - `test_stale_timeline` — latest ok Monday 06:41; now Tuesday 07:00 → stale line; now Tuesday 06:50 → no stale line (grace).
  - `test_empty_history_is_stale`, `test_latched_and_failed_lines`.
  - `test_unreadable_on` each of: timeout, 500, invalid JSON, missing `total`, bad timestamp, unknown status, reason of 41 chars, body of 70 000 bytes → exactly the one unreadable line, and the fake transport recorded the request (proves the factory was used, no outside connection).
  - `test_oversize_stream_stops_reading` — a streamed body of 1 MiB in 8 KiB chunks → unreadable, and the fake yielded at most 9 chunks.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(notify): read cathay statement status from the portfolio`.

### Task 12: Daily section and weekly block

**Files:**
- Modify: `notify/cli.py:70-96` (`_run`: compute `cathay = cathay_lines(cfg, now)` **before** `build_daily` / `build_weekly`), `notify/debits.py` (`DailyReport.cathay: list[CathayLine] = field(default_factory=list)`), `notify/digest.py` (`WeeklyReport.cathay: list[CathayLine]`), `_run` also adds each daily `CathayLine.key` to the `keys` it compares with `state.last_send.triggers`, `notify/render.py` (`render_daily`: block `**國泰對帳單**` after `⏭ 自動扣繳跳過`, counts toward "has blocks"; `render_weekly`: block `**國泰對帳單待處理**` right after `**自動扣繳本週跳過**` (:137), before `**設定缺漏**`; a weekly with only the unreadable line still shows that block and suppresses `✅ 本週沒有待處理…`)
- Test: `tests/notify/test_cli.py`, `tests/notify/test_render.py`

- [ ] **Step 1: Write the failing tests**: `test_daily_only_cathay_section_sends` (no debit items, one held line → one message sent); `test_daily_dedupe_includes_cathay_keys` (same day, same lines → nothing sent the second time; a new date → sent); `test_daily_portfolio_down_debits_still_sent` (the `cli_env` debit fixture plus a portfolio route returning 500 → the debit message and the unreadable line both delivered, exit 0); `test_daily_oversized_portfolio_body_debits_within_limit` (every message ≤ `render.LIMIT`); `test_weekly_block_order`; `test_weekly_unreadable_only`. In `test_cli.py`, `Env` patches `cli.httpx.Client` for every client (Task 11 resolves the factory at call time, so the patch reaches it); extend `_routes` with the two portfolio paths (default: empty pending, latest ok at the as-of day 06:41) so existing tests keep passing, set `portfolio_base = "http://portfolio.invalid"` in the test config, and have `Env` record request paths: success cases assert both portfolio paths went through the mock; failure cases assert the failing path went through the mock, parametrised with the fault on the first and on the second endpoint (a failure on the first may stop before the second request). Plus `test_reader_failure_matrix[daily|weekly]` — for each failure of Task 11's `test_unreadable_on` list, the run exits 0, the debit / weekly items are byte-identical to a run without `portfolio_base` except the one added unreadable line, and every message ≤ `render.LIMIT`.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS; the whole notifier suite (`tests/notify/`) — 145 + new, all PASS.
- [ ] **Step 5: Commit** `feat(notify): cathay statement section in daily and weekly`.

---

## Rollout (after each PR's merge; owner go per PR)

1. **A:** portfolio release dir + venv per the release-checkout procedure; `alembic upgrade head` (back up `stock_portfolio_db` first); pm2 switch; probe `/health` and `GET …/cathay-statement/pending` (empty). SPA release for Task 7 can follow separately.
2. **B:** worker checkout to the merge SHA; `python -m worker cathay --validate-history` → report to the owner (counts per outcome, mismatch list); owner go → first commit run (2026-10-16 → today) → check portfolio rows and `pending`; enable the timer.
3. **C:** notifier release worktree; add `portfolio_base = "http://127.0.0.1:8001"` to `~/.config/homehub-notify.toml`; `python -m notify daily --dry-run --as-of <tomorrow>` shows the section (or nothing); weekly dry run.
