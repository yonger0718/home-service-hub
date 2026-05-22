# stock-portfolio-auto-record-dividends Specification

## Purpose
TBD - created by archiving change add-auto-record-dividends. Update Purpose after archive.
## Requirements
### Requirement: Dividend rows persist fee, tax, and per-share metadata

The `dividends` table SHALL store the handling fee, the 二代健保 supplementary premium, the per-share cash dividend rate, the stock-dividend shares per thousand, the recording source, and the quantity used to compute `amount`.

#### Scenario: Default values for manual rows
- **WHEN** a dividend row is inserted via the manual `POST /api/portfolio/dividends` endpoint without specifying `fee` or `tax`
- **THEN** the row SHALL persist `fee = 0`, `tax = 0`, `cash_dividend_per_share = NULL`, `stock_dividend_shares = 0`, `source = NULL`

#### Scenario: Schema reject negative fee or tax
- **WHEN** a write attempts to set `fee < 0` or `tax < 0`
- **THEN** the database SHALL reject the write through a check constraint or a Pydantic validator

### Requirement: Historical dividend fetcher

The service SHALL fetch all cash + stock dividend events for a TWSE or TPEx symbol across a given range of calendar years using publicly-available endpoints.

#### Scenario: TWSE listed symbol returns dated cash + stock fields
- **GIVEN** symbol `0050` and year `2024`
- **WHEN** `dividend_history_service.fetch_symbol_year("0050", 2024)` is called
- **THEN** the result SHALL contain one `HistoricalDividendEvent` per ex-dividend date with `cash_dividend_per_share`, `stock_dividend_per_thousand`, `previous_close`, `reference_price` populated when the upstream provides them

#### Scenario: Upstream HTTP failure does not raise
- **WHEN** the TWSE rwd endpoint returns a non-2xx status or non-JSON body
- **THEN** the fetcher SHALL log `dividend_history.failed` with `{symbol, year, error}` and return an empty list

#### Scenario: TPEx symbols route to TPEx source
- **GIVEN** a known TPEx OTC symbol
- **WHEN** `fetch_for_symbol_all_years` is called
- **THEN** the fetcher SHALL use the TPEx daily-Q source instead of TWSE TWT49U for that symbol

### Requirement: Auto-record turns events into persisted dividends and stock shares

For an event with positive **LONG** quantity held on the ex-dividend date, the service SHALL insert a cash `Dividend` row (when `cash_dividend_per_share > 0`) and a zero-cost `Transaction` row (when `stock_dividend_per_thousand > 0` and the floored shares are positive). Both inserts SHALL be idempotent.

The "quantity held" used by both legs SHALL count only `Transaction` rows with `position_side='LONG'`. `position_side='SHORT'` rows (融券 SELL, 融券/沖買 cover BUY) SHALL NOT contribute to the quantity, because the short seller is not the shareholder of record on the ex-dividend date.

#### Scenario: LONG holdings only — pre-existing behavior preserved
- **GIVEN** `quantity = 1000` from LONG BUY transactions strictly before ex_date, no SHORT transactions
- **WHEN** `auto_record_for_event` runs
- **THEN** dividend amount is computed against qty = 1000 (unchanged from prior behavior)

#### Scenario: Concurrent LONG + SHORT — SHORT SELL ignored
- **GIVEN** LONG BUY 1000 and SHORT SELL 500 (融券 open) both with `trade_date < ex_date`
- **WHEN** `auto_record_for_event` runs
- **THEN** `_qty_held_on(symbol, ex_date)` SHALL return 1000, NOT 500
- **AND** the cash leg gross is computed against 1000

#### Scenario: Concurrent LONG + SHORT cover BUY — SHORT BUY ignored
- **GIVEN** LONG BUY 1000 and SHORT BUY 500 (融券/沖買 close) both with `trade_date < ex_date`
- **WHEN** `auto_record_for_event` runs
- **THEN** `_qty_held_on(symbol, ex_date)` SHALL return 1000, NOT 1500
- **AND** the cash leg gross is computed against 1000

#### Scenario: SHORT-only history — no dividend recorded
- **GIVEN** only SHORT SELL transactions for the symbol (no LONG legs)
- **WHEN** `auto_record_for_event` runs
- **THEN** `_qty_held_on` SHALL return 0
- **AND** the function SHALL return `{cash_inserted: false, stock_inserted: false, skipped_reason: "no_holding"}`

#### Scenario: Cash dividend below NHI threshold has zero tax
- **GIVEN** LONG qty = 1000, `cash_dividend_per_share = 2.0` (gross = 2,000), `default_fee = 10`
- **WHEN** `auto_record_for_event` runs
- **THEN** the inserted `Dividend` SHALL have `fee = 10`, `tax = 0`, `amount = 1990`, `quantity_at_record_date = 1000`, `cash_dividend_per_share = 2.0`

#### Scenario: Cash dividend above NHI threshold computes 2.11% surtax
- **GIVEN** LONG qty = 10000, `cash_dividend_per_share = 2.5` (gross = 25,000)
- **WHEN** `auto_record_for_event` runs
- **THEN** the inserted `Dividend` SHALL have `tax = 527.50` (`round(25000 * 0.0211, 2)`), `fee = 10`, `amount = 25000 - 10 - 527.50 = 24462.50`

#### Scenario: Stock dividend rounds down to whole shares
- **GIVEN** LONG qty = 1500, `stock_dividend_per_thousand = 100` (i.e. 100 shares per 1000 held)
- **WHEN** `auto_record_for_event` runs
- **THEN** a `Transaction` SHALL be inserted with `type = "BUY"`, `quantity = 150`, `price = 0`, `trade_date = event.ex_date`, `import_fingerprint LIKE 'auto-stk-div:%'`

#### Scenario: Zero LONG holding on ex-date skips silently
- **GIVEN** `_qty_held_on(symbol, ex_date) == 0`
- **WHEN** `auto_record_for_event` runs
- **THEN** no `Dividend` and no `Transaction` SHALL be inserted, and the return value SHALL be `{cash_inserted: false, stock_inserted: false, skipped_reason: "no_holding"}`

#### Scenario: Repeated event is idempotent
- **WHEN** `auto_record_for_event` is called twice with the same event
- **THEN** the second call SHALL insert nothing because the synthetic fingerprint conflicts on the unique index

#### Scenario: Cash amount is clamped to keep the existing positive constraint
- **GIVEN** `gross - fee - tax <= 0` (e.g. very small holding)
- **WHEN** the cash row is built
- **THEN** `amount` SHALL be set to `0.01` (the minimum value permitted by `ck_dividends_amount_positive`)

### Requirement: Backfill endpoint walks every held symbol across all owning years

The service SHALL expose `POST /api/portfolio/dividends/backfill` that, for each held symbol, calls the historical fetcher across every calendar year from the symbol's first trade date through the current TW year and feeds every event through `auto_record_for_event`.

#### Scenario: Per-symbol exception does not abort the run
- **GIVEN** symbol `A` raises during `fetch_for_symbol_all_years` and symbol `B` succeeds
- **WHEN** the endpoint is invoked
- **THEN** the response SHALL include any rows inserted for `B`, the error for `A` SHALL be logged, and the HTTP status SHALL still be 200

#### Scenario: Response surfaces aggregate counts
- **WHEN** the endpoint runs
- **THEN** the response SHALL be `{symbols_scanned, events_seen, cash_inserted, stock_inserted, skipped_no_holding}` as integers

#### Scenario: Second invocation is a no-op
- **WHEN** the endpoint is invoked twice in succession against the same DB
- **THEN** the second response SHALL have `cash_inserted = 0` and `stock_inserted = 0`

### Requirement: Manual override of fee and tax

The dividend CRUD endpoint SHALL accept `fee` and `tax` as optional fields on create and update, and edits SHALL persist without recomputing `amount`.

#### Scenario: User edits tax to zero for an overseas ETF
- **GIVEN** an existing auto-recorded `Dividend` with `tax = 527.50`
- **WHEN** the user issues `PUT /api/portfolio/dividends/{id}` with `tax = 0`
- **THEN** the stored row SHALL have `tax = 0` and `amount` SHALL remain at the persisted value (no auto-recompute)

### Requirement: Upcoming-events endpoint merges dividend events and face-value changes

The service SHALL expose `GET /api/portfolio/upcoming-events?from=YYYY-MM-DD` returning a list of future events combining upcoming dividends (cash and / or stock) and face-value changes for the caller's holdings.

#### Scenario: Default from-date is today TW
- **WHEN** the client omits `from`
- **THEN** the service SHALL substitute the current `Asia/Taipei` date

#### Scenario: Dividend and face-value events appear in one list
- **GIVEN** a held symbol with an upcoming cash dividend and a held symbol with an upcoming face-value change
- **WHEN** the endpoint runs
- **THEN** the response SHALL include both rows tagged with `type = "CASH_DIV"` and `type = "FACE_VALUE"` respectively, sorted ascending by `date`

#### Scenario: Past events excluded
- **WHEN** an event's date is strictly less than `from`
- **THEN** that event SHALL NOT appear in the response

#### Scenario: Type tagging
- **WHEN** a dividend event has both `cash_dividend_per_share > 0` and `stock_dividend_per_thousand > 0`
- **THEN** the response row SHALL be tagged `type = "BOTH"`

#### Scenario: Reference price change carried for dividend events
- **WHEN** the upstream dividend event includes `previous_close` and `reference_price`
- **THEN** the response row SHALL include `reference_price_change = reference_price - previous_close`; for face-value rows this field SHALL be `null`

