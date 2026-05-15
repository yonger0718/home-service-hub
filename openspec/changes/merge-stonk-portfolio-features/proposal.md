## Why

A standalone Taiwan-equity tracker at `/home/opc/workspace/stonk` (full ledger architecture, ~11K LOC backend) carries several mature features that home-hub's `stock-portfolio-service` lacks: manual CSV import with idempotency, day-trade auto-detection (TW half-tax `沖賣` rule), and daily TWSE/TPEx OHLC history for chart-quality price data. Reimplementing these in home-hub gives us bulk data entry, accurate tax labelling, and historical price context without rewriting the existing flat schema.

The full stonk architectural items (unified ledger, multi-account types, margin, FX P&L split, networth materialised view) are tightly coupled to stonk's schema and are explicitly out of scope here.

## What Changes

- **CSV import (transactions + dividends)** — generic CSV importer with SHA256 row fingerprinting for idempotency; dry-run preview and commit modes; rejection of duplicates within a single file and across uploads.
- **Day-trade auto-detection** — when a symbol has both BUY and SELL on the same calendar date, both rows are marked `is_day_trade=true`; flag recomputes on create, update, and delete; surfaces TW half-tax cost-estimate adjustment downstream.
- **TWSE + TPEx daily OHLC history** — new `price_history` table keyed by `(symbol, date)`; ported TWSE MI_INDEX and TPEx daily-quotes parsers (JSON-only, no bs4); manual backfill endpoint; reuses existing `TWSEClient` TLS-fallback policy. Scheduler wiring is deferred to a follow-on change.
- **Frontend import page** — new standalone Angular component with file upload, dry-run preview, error display, commit flow.

### Out of scope (deferred / cut)

- APScheduler scaffold + structlog logging (next change)
- `portfolio_snapshot` table + networth chart (next change)
- `corporate_actions` table + split fetcher (next change)
- Multi-source dividend fallback (MOPS + TWSE-ETF + TPEx) (next change)
- Daily DB backup script (next change)
- Unified ledger, multi-account types, margin, FX P&L split (architectural — never)

## Capabilities

### New Capabilities

- `stock-portfolio-csv-import`: bulk transaction and dividend import via CSV with SHA256 idempotency and dry-run preview.
- `stock-portfolio-day-trade-detection`: automatic `is_day_trade` flagging on same-symbol same-date BUY+SELL pairs, with recomputation on update/delete.
- `stock-portfolio-price-history`: daily OHLC capture and lookup for TWSE and TPEx symbols, idempotent against repeat backfills of the same trading day.

### Modified Capabilities

- None.

## Impact

- **Code**
  - `services/stock-portfolio-service/app/models/portfolio.py` — add `is_day_trade`, `import_fingerprint`
  - `services/stock-portfolio-service/app/models/price_history.py` — NEW
  - `services/stock-portfolio-service/app/services/import_service.py` — NEW
  - `services/stock-portfolio-service/app/services/market_data_service.py` — NEW
  - `services/stock-portfolio-service/app/services/portfolio_service.py` — day-trade recomputation hooks
  - `services/stock-portfolio-service/app/routers/imports.py` — NEW
  - `services/stock-portfolio-service/app/routers/history.py` — NEW
  - `services/stock-portfolio-service/app/schemas/portfolio.py` — expose `is_day_trade` on response
  - `services/stock-portfolio-service/app/main.py` — register new routers + model
  - `services/stock-portfolio-service/alembic/env.py` + 2 new migrations
  - `services/stock-portfolio-service/requirements.txt` — add `python-multipart`
  - `services/stock-portfolio-service/tests/unit/test_import_service.py` — NEW
  - `services/stock-portfolio-service/tests/unit/test_day_trade_detection.py` — NEW
  - `services/stock-portfolio-service/tests/unit/test_market_data_service.py` — NEW
  - `frontend/src/app/models/portfolio.model.ts` — add `is_day_trade`, `ImportResult`, etc.
  - `frontend/src/app/services/portfolio.service.ts` — add `uploadCsv()`
  - `frontend/src/app/components/portfolio/import/` — NEW page
  - `frontend/src/app/app.routes.ts` — add `/portfolio/import`

- **API (additive)**
  - `POST /api/portfolio/imports/transactions?dry_run=` — multipart CSV
  - `POST /api/portfolio/imports/dividends?dry_run=` — multipart CSV
  - `GET /api/portfolio/price-history?symbol=&from=&to=` — range query
  - `POST /api/portfolio/price-history/backfill?date=&market=` — manual trigger
  - `Transaction` response gains `is_day_trade: bool`

- **Dependencies**
  - Add `python-multipart` to stock-portfolio-service.

- **Operational**
  - New tables: `price_history`. New columns: `transactions.is_day_trade`, `transactions.import_fingerprint`, `dividends.import_fingerprint`.
  - Backfill is manual until scheduler ships in the next change.

- **Risks**
  - Composite-PK `Session.merge` chosen over dialect-specific `INSERT ON CONFLICT` for portability across SQLite (tests) and PostgreSQL (prod); idempotency confirmed by tests.
  - Day-trade flag uses UTC calendar date; TW market hours 01:00-05:30 UTC make UTC date == TW market date, so no DST drift in practice.
  - Fingerprint is stamped during INSERT (single atomic commit) to avoid a race window where a row exists without dedupe protection.
