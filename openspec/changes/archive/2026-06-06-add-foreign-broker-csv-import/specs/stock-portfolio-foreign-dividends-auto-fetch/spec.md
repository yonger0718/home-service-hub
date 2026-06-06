## ADDED Requirements

### Requirement: foreign_dividend_refresh cron upserts yfinance dividends

The service SHALL register an APScheduler cron job `foreign_dividend_refresh` that runs at 17:45 Asia/Taipei daily, gated by `SCHEDULER_ENABLED=true`. The job SHALL iterate every open foreign position (defined as `market in ('US', 'LSE') AND total_quantity > 0`), call `yfinance.Ticker(symbol).dividends` per symbol, and upsert the returned rows into the existing `dividends` table keyed on `(symbol, market, ex_dividend_date)`. The native amount SHALL be stored verbatim in `dividends.amount`; `dividends.currency` SHALL come from `yfinance.Ticker.fast_info.currency`; `dividends.fx_rate_to_twd` SHALL be resolved against `fx_rates` at `ex_dividend_date`.

#### Scenario: Cron upserts one dividend row per yfinance entry
- **GIVEN** an open foreign holding `(symbol='AAPL', market='US', total_quantity=10)`
- **GIVEN** `yfinance.Ticker('AAPL').dividends` returns two entries `(2026-05-15, 0.24)` and `(2026-02-14, 0.24)`
- **WHEN** the `foreign_dividend_refresh` cron runs
- **THEN** the `dividends` table SHALL contain two rows for `(symbol='AAPL', market='US')` with `ex_dividend_date` matching each entry and `amount=0.24`, `currency='USD'`

#### Scenario: Re-run does not create duplicates
- **GIVEN** the cron has already run once for the same holding
- **WHEN** the cron runs again with the same yfinance response
- **THEN** no new `dividends` rows SHALL be created — the existing rows are upserted in place

#### Scenario: Missing FX rate rejects the dividend row
- **GIVEN** `yfinance.Ticker('AAPL').dividends` returns `(2026-05-15, 0.24)`
- **GIVEN** no `fx_rates` row exists for `(currency='USD', date=2026-05-15)`
- **WHEN** the cron processes the row
- **THEN** the cron SHALL skip the row with a structured log entry `quotes.foreign_dividends.skip` carrying `symbol`, `ex_dividend_date`, and a `missing_fx` reason, and SHALL NOT write the row

#### Scenario: yfinance failure on one ticker does not abort the job
- **GIVEN** the cron is processing three foreign holdings
- **WHEN** the second `yfinance.Ticker(...).dividends` call raises an exception
- **THEN** the cron SHALL log the failure, skip that ticker, and continue processing the third ticker

### Requirement: Closed positions are skipped

The cron SHALL NOT call yfinance for any symbol whose `total_quantity <= 0` at the time the cron runs.

#### Scenario: Fully sold holding is skipped
- **GIVEN** a holding `(symbol='UUUU', market='US', total_quantity=0)`
- **WHEN** the cron runs
- **THEN** no `yfinance.Ticker('UUUU').dividends` call SHALL be made
