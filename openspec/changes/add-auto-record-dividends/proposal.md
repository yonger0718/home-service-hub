## Why

Today every cash / stock dividend row in the `dividends` table is hand-typed (manual UI, or a CSV import). The service already pulls dividend *announcements* from three upstream feeds (`dividend_event_service`), but nothing turns an announced event into a persisted dividend payout — and there is no historical backfill, so a holding bought five years ago has zero dividend history in the dashboard. Users want the system to record every dividend automatically on the ex-dividend date and to backfill past payouts in one shot, with reasonable Taiwan-specific math (NT$10 handling fee, 二代健保 2.11% supplementary premium when the single payout exceeds NT$20,000) and per-row overrides for edge cases (overseas ETFs are NHI-exempt; some brokers waive the NT$10 fee).

Concurrently, the dashboard's "公司行動 / 面額變更" panel currently shows only TWSE face-value-change events. Users want the same panel to surface upcoming ex-dividend / ex-rights events alongside face-value changes, since both move the reference price. Rename to "除權息 / 股價變動" and merge the feeds.

## What Changes

### Schema (new migration)

- **`dividends` table — add columns**
  - `fee NUMERIC(12,2) NOT NULL DEFAULT 0` — handling fee (default NT$10 for auto-recorded cash payouts; 0 for stock-only events)
  - `tax NUMERIC(12,2) NOT NULL DEFAULT 0` — 二代健保 supplementary premium (auto-computed; user-editable)
  - `cash_dividend_per_share NUMERIC(12,4) NULL` — preserves the per-share rate from the upstream event
  - `stock_dividend_shares INTEGER NOT NULL DEFAULT 0` — shares awarded for stock dividend (`floor(qty * stockDividendShares / 1000)`)
  - `source VARCHAR(32) NULL` — e.g. `auto:TWT49U`, `auto:TPEX`, `manual`, `csv`
  - `quantity_at_record_date NUMERIC(18,4) NULL` — quantity used to compute `amount`; aids audit + recomputation when user edits fee/tax
  - Existing `amount` column stays the source of truth for the realised cash; auto-record writes `amount = qty * cash_div_per_share - fee - tax` (clamped to ≥ 0.01 to satisfy `ck_dividends_amount_positive`).

### New services

- **`dividend_history_service.py`** — fetch a single symbol's historical cash + stock dividend events.
  - Primary source: TWSE rwd `https://www.twse.com.tw/rwd/zh/exRight/TWT49U?startDate&endDate&stockNo` (the same endpoint `node-twstock` scrapes). Returns `previousClose`, `referencePrice`, `cashDividend`, `stockDividendShares`, `dividend`, `dividendType`.
  - Fallback for TPEx symbols: `https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ` (already wired by `tpex_otc.py`; reused).
  - Returns a normalised `HistoricalDividendEvent` per (`symbol`, `ex_date`) with both cash + stock fields.

- **`dividend_auto_record_service.py`** — turns events into persisted `Dividend` rows.
  - `auto_record_for_event(db, symbol, event)`:
    1. `quantity = qty_held_on(symbol, ex_date - 1 trading day)` via `transactions` aggregation.
    2. If `quantity <= 0`, skip.
    3. `cash_amount_gross = quantity * event.cash_dividend_per_share` (Decimal).
    4. `fee = 10` if `cash_amount_gross > 0` else `0`.
    5. `tax = round(cash_amount_gross * 0.0211, 2)` if `cash_amount_gross > 20000` else `0`.
    6. `amount = max(cash_amount_gross - fee - tax, Decimal("0.01"))`.
    7. Insert `Dividend` with fingerprint `auto:{event.source}:{symbol}:{ex_date.isoformat()}:cash`; on conflict do nothing.
    8. If `event.stock_dividend_per_thousand > 0`, compute `extra_shares = floor(quantity * event.stock_dividend_per_thousand / 1000)`. If `extra_shares > 0`, insert a `Transaction(type=BUY, price=0, quantity=extra_shares, trade_date=ex_date, fee=0, tax=0, import_fingerprint=f"auto-stk-div:{event.source}:{symbol}:{ex_date}:stk", is_day_trade=false)`.

- **Backfill endpoint** — `POST /api/portfolio/dividends/backfill`
  - For every held symbol, walks every calendar year from `min(first BUY trade_date).year` through current TW year, calls `dividend_history_service`, and feeds each event into `auto_record_for_event`.
  - Returns `{symbols_scanned, events_seen, cash_inserted, stock_inserted, skipped_no_holding}`.
  - Fully idempotent via the synthetic fingerprints — re-runs are a no-op.

### Scheduler

- New cron job `dividend_auto_record` — **18:00 TW, Mon-Fri** — runs `dividend_event_service.fetch_for_holdings(year=current_tw_year)`, filters to events with `ex_dividend_date in [today - 7, today]` (catches events the scheduler missed on a quiet day), feeds each into `auto_record_for_event`. Logs `{events_seen, cash_inserted, stock_inserted}`.

### Frontend

- **Panel rename**: `公司行動 / 面額變更` → `除權息 / 股價變動`.
- **New endpoint `GET /api/portfolio/upcoming-events`** merges:
  - Upcoming dividend events (from `dividend_event_service.fetch_for_holdings`, filtered to `ex_dividend_date >= today`).
  - Face-value changes (from `corporate_actions` where `effective_date >= today`).
  - Sort ascending by date. Each row tags `type` ∈ {`CASH_DIV`, `STOCK_DIV`, `BOTH`, `FACE_VALUE`} and carries the relevant numeric field (`cash_dividend`, `stock_dividend_shares`, `ratio`) plus optional `reference_price_change` (= `referencePrice - previousClose` for div events, `null` for face-value).
- Panel binds the new endpoint; columns: 生效日 / 代號 / 類型 / 數值 / 參考價變動 / 來源.

- **Dividend dialog** in transaction-list (the existing dividend form, which today only has `symbol`, `amount`, `ex_dividend_date`, `received_date`):
  - Add `fee` and `tax` editable fields.
  - When user opens an auto-recorded row, surface a hint line `配息 NT$21,000 (補充保費 −NT$443)` showing gross → net so the deduction is visible and can be edited.

### Out of scope

- Cash account / ledger entry — home-hub's `Dividend` table stays standalone. No double-entry posting against a cash account.
- Withholding tax (advance income tax) beyond the 2.11% NHI surtax — Taiwan domestic equity dividends are not subject to advance withholding for residents, so we model only the NHI surtax.
- Multi-source dedup beyond fingerprint — if two upstreams report the same event with mismatched amounts, fingerprint collision skips the second insert. Logged but no reconciliation UI.
- US / overseas dividend ingestion — TWSE/TPEx symbols only.
- Calling `node-twstock` as a Node sidecar — we hit the same TWSE rwd URLs directly from Python.

## Capabilities

### New Capabilities

- `stock-portfolio-auto-record-dividends`: persisted historical dividends, daily auto-record cron, NT$10 fee + NHI 2.11% surtax math with per-row overrides, stock-dividend → zero-cost transaction conversion, merged upcoming-events endpoint.

### Modified Capabilities

- `stock-portfolio-scheduling`: adds the `dividend_auto_record` cron.

## Impact

- **Code (backend)**
  - `services/stock-portfolio-service/app/models/portfolio.py` — new columns on `Dividend`
  - `services/stock-portfolio-service/app/schemas/portfolio.py` — new fields on `DividendBase`, `DividendCreate`
  - `services/stock-portfolio-service/app/services/dividend_history_service.py` — NEW
  - `services/stock-portfolio-service/app/services/dividend_auto_record_service.py` — NEW
  - `services/stock-portfolio-service/app/services/dividend_event_service.py` — add `fetch_upcoming_for_holdings(held_symbols, from_date)` that filters by date window
  - `services/stock-portfolio-service/app/services/scheduler.py` — register `dividend_auto_record` job
  - `services/stock-portfolio-service/app/routers/portfolio.py` — accept `fee`, `tax`, `cash_dividend_per_share`, `stock_dividend_shares`, `source`, `quantity_at_record_date` in dividend CRUD
  - `services/stock-portfolio-service/app/routers/dividends_backfill.py` — NEW (`POST /api/portfolio/dividends/backfill`)
  - `services/stock-portfolio-service/app/routers/upcoming_events.py` — NEW (`GET /api/portfolio/upcoming-events`)
  - `services/stock-portfolio-service/alembic/versions/*_add_dividend_fee_tax_source.py` — NEW migration
  - `services/stock-portfolio-service/tests/unit/test_dividend_history_service.py` — NEW
  - `services/stock-portfolio-service/tests/unit/test_dividend_auto_record_service.py` — NEW
  - `services/stock-portfolio-service/tests/unit/test_dividends_backfill_router.py` — NEW
  - `services/stock-portfolio-service/tests/unit/test_upcoming_events_router.py` — NEW

- **Code (frontend)**
  - `frontend/src/app/models/portfolio.model.ts` — extend `Dividend` interface; new `UpcomingEvent` interface
  - `frontend/src/app/services/portfolio.service.ts` — `getUpcomingEvents()`, `triggerDividendBackfill()`
  - `frontend/src/app/components/portfolio/corporate-actions-panel/` — bind new endpoint, rename header, add type / value / reference-price-change columns
  - `frontend/src/app/components/portfolio/transaction-list/` (or wherever the dividend dialog lives) — add `fee` / `tax` inputs + gross hint
  - `frontend/src/app/components/portfolio/dashboard/dashboard.html` — header text update

- **API (additive)**
  - `POST /api/portfolio/dividends/backfill` → `{symbols_scanned, events_seen, cash_inserted, stock_inserted, skipped_no_holding}`
  - `GET /api/portfolio/upcoming-events?from=YYYY-MM-DD` → list of merged events
  - `PUT /api/portfolio/dividends/{id}` accepts `fee`, `tax`

- **Operational**
  - One new cron at 18:00 TW. Bounded HTTP calls — one TWT49U fetch per held symbol per backfill year; production use only triggers the backfill endpoint manually.
  - No new env vars.

- **Risks**
  - TWSE rwd `TWT49U` endpoint is HTML-form-style; if upstream returns multipart / blocks via CAPTCHA, the fetcher returns empty for that symbol-year and logs `dividend_history.failed`. Backfill still succeeds for other symbols.
  - `qty_held_on(symbol, ex_date - 1)` relies on transactions sorted ascending; if a user later inserts a back-dated transaction, the previously-recorded dividend amount becomes inconsistent. Out of scope to auto-recompute — surfaced via the editable `fee`/`tax`/`amount` fields.
  - 2.11% NHI rate hard-coded. If the rate changes, a follow-up change is required (single constant). Documented in `dividend_auto_record_service`.
