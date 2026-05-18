## ADDED Requirements

### Requirement: Fast-path refresh-quotes endpoint runs Phase 1+2 only for today

The service SHALL expose `POST /api/portfolio/imports/refresh-quotes` that triggers a today-only quotes refresh by scheduling `post_import_orchestrator.schedule_quotes_refresh_sync` as a FastAPI `BackgroundTasks` callable. The fast path SHALL skip `symbol_map_backfill` and `dividend_auto_record`, running ONLY `_step_networth_backfill` for `recalc_from = recalc_to = today_tw`. The touched-symbols set SHALL be derived as the set of symbols whose net quantity (`sum(BUY) - sum(SELL)` across all transactions with `trade_date <= today`) is greater than zero.

#### Scenario: Refresh-quotes schedules today-only recalc
- **WHEN** `POST /api/portfolio/imports/refresh-quotes` is called and at least one symbol has positive net holdings
- **THEN** the service SHALL schedule `schedule_quotes_refresh_sync` as a background task with `recalc_from = recalc_to = today_tw` and `touched_symbols = {symbols with qty > 0}`, return HTTP 202 with `{"refresh_scheduled": true, "date": "<today>", "touched_symbols": [...]}`, and the chain status SHALL be queryable via the existing `GET /api/portfolio/imports/recalc/status`

#### Scenario: Refresh-quotes on empty portfolio
- **WHEN** `POST /api/portfolio/imports/refresh-quotes` is called and no symbol has positive net holdings
- **THEN** the response SHALL be HTTP 204 No Content and no background task SHALL be scheduled

#### Scenario: Refresh-quotes during in-flight recalc
- **WHEN** `POST /api/portfolio/imports/refresh-quotes` is called while `_RECALC_LOCK` is already held by another chain
- **THEN** the response SHALL be HTTP 409 with body `{"detail": "recalc in progress"}` and no background task SHALL be scheduled

### Requirement: Fast-path produces a single-step chain result

`schedule_quotes_refresh_sync` SHALL produce a `ChainResult` whose `steps` list contains exactly one `StepResult` with `name = "networth_backfill"`. The `recalc_from` and `recalc_to` fields SHALL both equal today's date in `Asia/Taipei`. The result SHALL be stored in `_LATEST_RESULTS` using the same TTL-pruning rules as full chain runs, so `latest_status()` returns the most recent run regardless of which entrypoint produced it.

#### Scenario: Status payload after refresh-quotes
- **WHEN** a refresh-quotes background task completes and `GET /api/portfolio/imports/recalc/status` is called
- **THEN** the response SHALL include `state in {"completed", "partial", "failed"}`, `steps` of length 1 with `name = "networth_backfill"`, and `recalc_from == recalc_to == today_tw.isoformat()`

#### Scenario: Idempotent re-trigger same day
- **WHEN** `POST /api/portfolio/imports/refresh-quotes` is called twice in succession on the same calendar day with the same holdings
- **THEN** the second call SHALL also schedule a fresh background task (Phase 1 dedupes via `price_history` PK, Phase 2 idempotently upserts the snapshot via `merge`), and no duplicate rows SHALL appear in `price_history` or `portfolio_snapshot`
