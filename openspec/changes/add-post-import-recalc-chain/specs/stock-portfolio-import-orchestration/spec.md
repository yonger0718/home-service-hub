# stock-portfolio-import-orchestration Specification

## ADDED Requirements

### Requirement: Post-import recalc chain runs after every successful CSV commit

After a transactions or dividends CSV import succeeds with `created > 0` (i.e. `dry_run=false` and at least one row inserted), the service SHALL schedule a post-import orchestration chain to run in a FastAPI `BackgroundTasks` slot. The chain SHALL run sequentially: symbol-name backfill → dividend auto-record for touched symbols → networth backfill. The HTTP response to the import endpoint SHALL NOT wait for the chain to finish.

#### Scenario: Chain fires after successful import
- **WHEN** `POST /api/portfolio/imports/transactions` with `dry_run=false` returns with `created >= 1`
- **THEN** the service SHALL schedule `post_import_orchestrator.run_chain` as a background task with `recalc_from = min(created_rows.trade_date)` and the HTTP response SHALL be sent before the chain completes

#### Scenario: Chain skipped on zero inserts (idempotent re-upload)
- **WHEN** an import returns with `created == 0` and `skipped_duplicates >= 1`
- **THEN** the orchestrator SHALL NOT be scheduled and the response body SHALL include `recalc_scheduled: false`

#### Scenario: Chain steps run in fixed order
- **WHEN** the orchestrator executes
- **THEN** it SHALL call `symbol_map_service.backfill_transactions(dry_run=False)`, then fetch dividend events for the touched symbols via `dividend_event_service.fetch_for_holdings` and feed each in-range event through `dividend_auto_record_service.auto_record_for_event`, then `networth_backfill_service.run_backfill(start=recalc_from, end=today_tw, phase="both")`

#### Scenario: One step's failure does not skip later steps
- **WHEN** the dividend re-fetch step raises an exception
- **THEN** the orchestrator SHALL log `event=post_import.step_failed step=dividends` with the exception and SHALL continue to run the networth backfill step

#### Scenario: Chain disabled by feature flag
- **WHEN** the env var `POST_IMPORT_RECALC_ENABLED` is set to `false`
- **THEN** the orchestrator SHALL NOT be scheduled even when `inserted_count > 0`, and the response body SHALL include `recalc_scheduled: false`

### Requirement: Manual recalc endpoint accepts a date range and reruns the chain

The service SHALL expose `POST /api/portfolio/imports/recalc` that runs the same chain on demand without requiring a fresh CSV upload. The endpoint SHALL accept an optional `{start_date, end_date}` body and SHALL default `start_date` to `min(transactions.trade_date)` and `end_date` to today in `Asia/Taipei`.

#### Scenario: Manual recalc with explicit range
- **WHEN** `POST /api/portfolio/imports/recalc` is called with body `{"start_date": "2024-01-15", "end_date": "2026-05-17"}`
- **THEN** the orchestrator SHALL run with `recalc_from = 2024-01-15` and `recalc_to = 2026-05-17`

#### Scenario: Manual recalc with default range
- **WHEN** `POST /api/portfolio/imports/recalc` is called with empty body
- **THEN** the orchestrator SHALL resolve `recalc_from = min(transactions.trade_date)` and `recalc_to = today_tw` from the DB and current clock

#### Scenario: Manual recalc on empty portfolio
- **WHEN** the endpoint is called and the `transactions` table has zero rows
- **THEN** the response SHALL be HTTP 409 with body `{"error": "no transactions to recalc"}`

### Requirement: Concurrent chains are serialized

The orchestrator SHALL hold a process-wide `asyncio.Lock` named `post_import_recalc_lock` so that at most one chain runs at a time. Subsequent chain invocations SHALL wait for the lock rather than run in parallel.

#### Scenario: Second chain waits for first
- **WHEN** chain A is mid-run and chain B is scheduled
- **THEN** chain B SHALL await the lock and start only after chain A releases it

### Requirement: Chain progress is observable via status endpoint

The service SHALL expose `GET /api/portfolio/imports/recalc/status` returning the in-memory result of the most recent chain run (status per step + start/end timestamps). The status object SHALL persist in memory for at least 10 minutes after chain completion so the UI can poll after a page refresh.

#### Scenario: Status during a running chain
- **WHEN** a chain is currently executing and the status endpoint is called
- **THEN** the response SHALL be `{"state": "running", "step": "<current step>", "started_at": "<iso>"}`

#### Scenario: Status after completion
- **WHEN** the most recent chain finished within the last 10 minutes
- **THEN** the response SHALL include `state: "completed" | "partial" | "failed"` plus per-step `status` values (`"ok"`, `"failed"`, or `"skipped"`)

#### Scenario: Status after expiry or never-run
- **WHEN** no chain has run, or the last run completed more than 10 minutes ago
- **THEN** the response SHALL be `{"state": "idle"}`

### Requirement: CSV upload page is reachable from primary navigation

The Angular shell (`app.html`) SHALL include a top-level navigation link labelled "匯入 CSV" that routes to `/portfolio/import`. The link SHALL appear in the same nav group as the existing Dashboard / Transactions / Dividends links.

#### Scenario: Nav link visible
- **WHEN** the user loads any page of the Angular app
- **THEN** the top navigation SHALL render a link "匯入 CSV" pointing to `/portfolio/import`

### Requirement: Import page surfaces recalc result

The import page UI SHALL display a non-blocking toast immediately after commit ("Recalculation running…") and SHALL poll `/api/portfolio/imports/recalc/status` every 5 seconds until the state leaves `"running"`. On completion the UI SHALL show a final toast: success, "partial — some steps failed" with a link to retry via the manual endpoint, or "failed".

#### Scenario: Successful chain shows success toast
- **WHEN** the status endpoint returns `state: "completed"` with all step statuses `"ok"`
- **THEN** the UI SHALL show a green success toast "資料重算完成" and stop polling

#### Scenario: Partial failure offers retry
- **WHEN** the status endpoint returns `state: "partial"`
- **THEN** the UI SHALL show an orange warning toast listing the failed step(s) and a "重試" button that calls `POST /api/portfolio/imports/recalc`
