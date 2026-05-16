## 1. Schema migration

- [x] 1.1 New Alembic revision `add_dividend_fee_tax_source`:
  - Add columns to `dividends`: `fee NUMERIC(12,2) NOT NULL DEFAULT 0`, `tax NUMERIC(12,2) NOT NULL DEFAULT 0`, `cash_dividend_per_share NUMERIC(12,4) NULL`, `stock_dividend_shares INTEGER NOT NULL DEFAULT 0`, `source VARCHAR(32) NULL`, `quantity_at_record_date NUMERIC(18,4) NULL`
  - Reversible `downgrade` drops the same six columns
- [x] 1.2 Update `app/models/portfolio.py` `Dividend` to declare the new columns
- [x] 1.3 Update `app/schemas/portfolio.py` `DividendBase` and `DividendCreate` to expose the new fields (all optional in `DividendBase`, default-zero in `DividendCreate`)

## 2. Historical dividend fetcher

- [x] 2.1 New `app/services/dividend_history_service.py` with a `HistoricalDividendEvent` dataclass (`symbol`, `ex_date`, `cash_dividend_per_share: Decimal | None`, `stock_dividend_per_thousand: Decimal | None`, `previous_close: Decimal | None`, `reference_price: Decimal | None`, `source: str`)
- [x] 2.2 `fetch_symbol_year(symbol, year)` for TWSE listed symbols:
  - GET `https://www.twse.com.tw/rwd/zh/exRight/TWT49U?startDate={year}0101&endDate={year}1231&stockNo={symbol}&response=json`
  - Parse `data` rows: cols `[ex_date_roc, previousClose, referencePrice, dividend, dividendType, limitUpPrice, limitDownPrice, openingReferencePrice, exdividendReferencePrice, detailLink]`
  - For each row, call detail endpoint `TWT49UDetail?date={detailDate}&stockNo={symbol}` to obtain `cashDividend` + `stockDividendShares` (per 1000)
  - Skip rows where both cash + stock are zero/null
- [x] 2.3 `fetch_symbol_year` for TPEx symbols (4-digit starts with `5`, `6`, `8` or symbols in `tpex_otc.SOURCE`'s known set): reuse `dividend_sources.tpex_otc.fetch_tpex_otc(year)` and filter to the requested symbol
- [x] 2.4 `fetch_for_symbol_all_years(symbol, since: date)`: walk every year from `since.year` to current TW year, calling `fetch_symbol_year` and concatenating; one source exception per (symbol, year) is logged + skipped
- [x] 2.5 Treat all HTTP failures / non-JSON responses as empty + log `dividend_history.failed` with `{symbol, year, error}`

## 3. Auto-record service

- [x] 3.1 New `app/services/dividend_auto_record_service.py`
- [x] 3.2 `_qty_held_on(db, symbol, on_date)`: sum signed quantity (`BUY = +qty`, `SELL = -qty`) over `transactions.trade_date < on_date`; return `Decimal`
- [x] 3.3 `compute_nhi_surtax(gross: Decimal) -> Decimal`: `round(gross * Decimal("0.0211"), 2)` when `gross > 20000` else `Decimal("0")`. Constant `NHI_SURTAX_RATE = Decimal("0.0211")`, `NHI_SURTAX_THRESHOLD = Decimal("20000")`
- [x] 3.4 `auto_record_for_event(db, event: HistoricalDividendEvent | DividendEventRow, *, default_fee=Decimal("10")) -> AutoRecordResult`:
  - returns `{cash_inserted: bool, stock_inserted: bool, skipped_reason: str | None}`
  - Lookup `qty = _qty_held_on(db, event.symbol, event.ex_date)`. If `qty <= 0`, return `skipped_reason="no_holding"`
  - Cash branch (when `cash_dividend_per_share` is set + > 0):
    - `gross = qty * cash_div_per_share`
    - `fee = default_fee` if `gross > 0` else `Decimal("0")`
    - `tax = compute_nhi_surtax(gross)`
    - `amount = max(gross - fee - tax, Decimal("0.01"))`
    - Build `Dividend` with `import_fingerprint = sha256(f"auto:{event.source}:{event.symbol}:{event.ex_date}:cash")`, populated `fee`, `tax`, `cash_dividend_per_share`, `quantity_at_record_date=qty`, `source=f"auto:{event.source}"`
    - `INSERT ... ON CONFLICT (import_fingerprint) DO NOTHING`; track inserted row count
  - Stock branch (when `stock_dividend_per_thousand` is set + > 0):
    - `extra_shares = int((qty * stock_div_per_thousand / 1000).to_integral_value(rounding=ROUND_DOWN))`
    - If `extra_shares <= 0`, skip stock branch (do not block cash branch)
    - Insert `Transaction(type=BUY, quantity=extra_shares, price=Decimal("0"), trade_date=event.ex_date, fee=0, tax=0, symbol=event.symbol, name=existing_name_or_null, is_day_trade=False, import_fingerprint=sha256(f"auto-stk-div:{event.source}:{event.symbol}:{event.ex_date}:stk"))`
    - `ON CONFLICT` no-op
- [x] 3.5 `auto_record_for_holdings(db, events: Iterable[event])`: loop calling `auto_record_for_event`; aggregate counts

## 4. Backfill endpoint

- [x] 4.1 New `app/routers/dividends_backfill.py` exposing `POST /api/portfolio/dividends/backfill`
- [x] 4.2 Handler:
  - `held_symbols` ← `portfolio_service.get_active_holdings(db).keys()`
  - For each symbol, find `first_trade_date = min(trade_date) WHERE symbol = sym`; default to today if no holdings
  - Call `dividend_history_service.fetch_for_symbol_all_years(symbol, first_trade_date)`
  - For each event, call `auto_record_for_event(db, event)`
  - Commit per symbol; on per-symbol exception, log + continue
- [x] 4.3 Response: `{symbols_scanned: int, events_seen: int, cash_inserted: int, stock_inserted: int, skipped_no_holding: int}`
- [x] 4.4 Wire router in `app/main.py`

## 5. Upcoming-events endpoint

- [x] 5.1 Extend `dividend_event_service` with `fetch_upcoming_for_holdings(held_symbols, from_date: date) -> list[UpcomingDividendEvent]`. Reuse existing three sources; filter to `ex_dividend_date >= from_date`
- [x] 5.2 New `app/routers/upcoming_events.py` exposing `GET /api/portfolio/upcoming-events?from=YYYY-MM-DD` (default `from = today_tw`)
- [x] 5.3 Handler merges:
  - Dividend events from step 5.1 (`type` ∈ {`CASH_DIV`, `STOCK_DIV`, `BOTH`} based on which fields are non-null)
  - Rows from `corporate_actions` where `effective_date >= from` (`type=FACE_VALUE`)
- [x] 5.4 Response items carry: `date`, `symbol`, `name?`, `type`, `cash_dividend?`, `stock_dividend_shares?`, `ratio?`, `reference_price_change?`, `source`
- [x] 5.5 Sort ascending by `date`

## 6. Scheduler job

- [x] 6.1 In `app/services/scheduler.py`, add `run_dividend_auto_record(session_factory)`:
  - `today = _today_tw()`; `window_start = today - timedelta(days=7)`
  - Pull `held_symbols`; call `dividend_event_service.fetch_upcoming_for_holdings(held_symbols, from_date=window_start)`; filter to `ex_dividend_date <= today`
  - Feed each into `auto_record_for_event`
  - Log `scheduler.dividend_auto_record.done` with aggregate counts
- [x] 6.2 Register cron `dividend_auto_record` at `hour=18, minute=0, day_of_week="mon-fri"` in `build_scheduler`
- [x] 6.3 Wrap fetch + record body in try/except; scheduler must not die on upstream failure

## 7. Dividend dialog (frontend)

- [x] 7.1 Extend `frontend/src/app/models/portfolio.model.ts` `Dividend` interface with `fee`, `tax`, `cash_dividend_per_share?`, `stock_dividend_shares?`, `source?`, `quantity_at_record_date?`
- [x] 7.2 Add `UpcomingEvent` interface: `{date, symbol, name?, type, cash_dividend?, stock_dividend_shares?, ratio?, reference_price_change?, source}`
- [x] 7.3 Add `getUpcomingEvents()`, `triggerDividendBackfill()` to `portfolio.service.ts`
- [x] 7.4 In the dividend create / edit dialog: add `fee` and `tax` `p-inputNumber` fields
- [x] 7.5 Below `amount`, show hint when `cash_dividend_per_share` and `quantity_at_record_date` are populated: `配息 NT${gross} (補充保費 −NT${tax})`. Hide hint when both are null

## 8. Corporate-actions panel rename + merge

- [x] 8.1 Change panel header in `corporate-actions-panel.html`: `公司行動 / 面額變更` → `除權息 / 股價變動`
- [x] 8.2 Component fetches from new `getUpcomingEvents()` instead of `getCorporateActions()`
- [x] 8.3 Columns: 生效日 / 代號 / 類型 (除息 | 除權 | 除息+除權 | 面額變更) / 數值 (現金股利 X / 配股 Y 股/仟 / 比例 R) / 參考價變動 / 來源
- [x] 8.4 Empty-state copy updated to "尚無除權息 / 股價變動事件"

## 9. Tests

- [x] 9.1 `tests/unit/test_dividend_history_service.py`:
  - TWT49U parser converts the rwd payload into events
  - Detail endpoint enrichment produces cash + stock fields
  - HTTP failure returns empty + log `dividend_history.failed`
  - Symbol routing: 4-digit `2330` → TWT49U; 4-digit `8044` (OTC) → tpex_otc fallback
- [x] 9.2 `tests/unit/test_dividend_auto_record_service.py`:
  - `_qty_held_on` aggregates signed quantity correctly
  - `compute_nhi_surtax`: below threshold → 0; above → `gross * 0.0211` rounded 2dp
  - Cash branch inserts with correct fee + tax; `amount` clamped to ≥ 0.01
  - Stock branch inserts zero-cost transaction with floored quantity
  - Both branches idempotent under repeated calls (fingerprint conflict)
  - `qty <= 0` returns `skipped_reason="no_holding"` and inserts nothing
- [x] 9.3 `tests/unit/test_dividends_backfill_router.py`:
  - Active holdings drive symbol scan
  - Per-symbol exception isolated
  - Response counts match service output
- [x] 9.4 `tests/unit/test_upcoming_events_router.py`:
  - Future dividend events + future face-value changes both appear
  - Sorted ascending by date
  - Past events excluded
- [x] 9.5 `tests/unit/test_scheduler.py`: extend with `dividend_auto_record` registration and `is_enabled` gating
- [x] 9.6 Frontend: extend existing transaction-list dialog spec to assert `fee` / `tax` inputs exist

## 10. Verification

- [x] 10.1 `cd services/stock-portfolio-service && alembic upgrade head` on a fresh DB
- [x] 10.2 `alembic downgrade -1` reverses the new migration cleanly
- [x] 10.3 `pytest` full suite passes (existing 188+ tests + new tests)
- [x] 10.4 Manual: hit `POST /api/portfolio/dividends/backfill` on a real DB; verify auto-rows appear with `source LIKE 'auto:%'` and second invocation is a no-op
- [x] 10.5 Manual: hit `GET /api/portfolio/upcoming-events` and verify the dashboard panel renders future events + face-value changes
- [x] 10.6 `openspec validate add-auto-record-dividends --strict` passes
