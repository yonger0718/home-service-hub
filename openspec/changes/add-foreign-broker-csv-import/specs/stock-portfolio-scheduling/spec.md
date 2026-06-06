## ADDED Requirements

### Requirement: Daily foreign dividend refresh cron

The scheduler SHALL register a job with id `foreign_dividend_refresh` bound to a cron trigger of `hour=17, minute=45, timezone=Asia/Taipei`. The job SHALL be gated by `SCHEDULER_ENABLED=true`. The job callable SHALL invoke `foreign_dividend_service.refresh_today` inside a fresh DB session and SHALL NOT propagate exceptions raised by the underlying service.

#### Scenario: Scheduler registers foreign_dividend_refresh
- **WHEN** the scheduler boots with `SCHEDULER_ENABLED=true`
- **THEN** a job with id `foreign_dividend_refresh` SHALL be registered with a cron trigger of `hour=17, minute=45, timezone=Asia/Taipei`

#### Scenario: foreign_dividend_refresh callable swallows exceptions
- **WHEN** `foreign_dividend_service.refresh_today` raises an exception (e.g. yfinance outage)
- **THEN** the job callable SHALL log `event=foreign_dividends.failed` and SHALL NOT propagate the exception so the scheduler thread stays alive

#### Scenario: foreign_dividend_refresh fires after fx_rate_refresh and foreign_price_refresh
- **WHEN** the day's three foreign-data jobs run
- **THEN** the execution order SHALL be `fx_rate_refresh` (17:00) → `foreign_price_refresh` (17:30) → `foreign_dividend_refresh` (17:45) so dividend FX lookups always find a populated `fx_rates` row for ex-date
