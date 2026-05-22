## MODIFIED Requirements

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
