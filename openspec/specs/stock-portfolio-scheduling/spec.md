# stock-portfolio-scheduling Specification

## Purpose
TBD - created by archiving change add-auto-record-dividends. Update Purpose after archive.
## Requirements
### Requirement: Daily dividend auto-record cron

The scheduler SHALL register a cron job `dividend_auto_record` running at 18:00 Mon-Fri in `Asia/Taipei` that records any newly-passed cash + stock dividend events for currently-held symbols.

#### Scenario: Job registered with the correct schedule
- **WHEN** `build_scheduler` runs
- **THEN** the returned `BackgroundScheduler` SHALL contain a job with id `dividend_auto_record` and a `CronTrigger` for `hour=18, minute=0, day_of_week="mon-fri", timezone=Asia/Taipei`

#### Scenario: Job covers a 7-day lookback
- **WHEN** `run_dividend_auto_record` executes on a given TW date `today`
- **THEN** the job SHALL query upcoming events using `from_date = today - 7 days` and process only events whose `ex_dividend_date <= today`

#### Scenario: Job swallows upstream failure
- **WHEN** the underlying `dividend_event_service` or `dividend_auto_record_service` raises an exception
- **THEN** the job SHALL log `scheduler.dividend_auto_record.failed` with the error and return without crashing the scheduler thread

#### Scenario: Job is disabled in tests
- **WHEN** `SCHEDULER_ENABLED=false` is set
- **THEN** the scheduler SHALL NOT boot and the job SHALL NOT run

### Requirement: Service runs an in-process background scheduler with three TW cron jobs

The service SHALL boot a `BackgroundScheduler` on FastAPI startup (when enabled) and SHALL register exactly three jobs anchored to `Asia/Taipei`. The `portfolio_snapshot` job SHALL persist a real `portfolio_snapshot` row on each fire; it MUST NOT propagate exceptions raised by the underlying snapshot service.

#### Scenario: Scheduler registers tw_daily_prices
- **WHEN** the scheduler boots
- **THEN** a job with id `tw_daily_prices` SHALL be registered with a cron trigger of `hour=17, minute=0, day_of_week=mon-fri, timezone=Asia/Taipei`

#### Scenario: Scheduler registers quote_refresh
- **WHEN** the scheduler boots
- **THEN** a job with id `quote_refresh` SHALL be registered with a cron trigger of `minute=*/15, hour=9-13, day_of_week=mon-fri, timezone=Asia/Taipei`

#### Scenario: Scheduler registers portfolio_snapshot
- **WHEN** the scheduler boots
- **THEN** a job with id `portfolio_snapshot` SHALL be registered with a cron trigger of `hour=15, minute=30, timezone=Asia/Taipei`

#### Scenario: portfolio_snapshot callable persists a row
- **WHEN** the `portfolio_snapshot` job callable runs
- **THEN** it SHALL call `portfolio_snapshot_service.write_today_snapshot` inside a fresh DB session

#### Scenario: portfolio_snapshot callable swallows exceptions
- **WHEN** the underlying snapshot write raises an exception (e.g. TWSE outage breaks the live summary)
- **THEN** the job callable SHALL log `event=snapshot.failed` and SHALL NOT propagate the exception so the scheduler thread stays alive

### Requirement: Scheduler lifecycle is bound to the FastAPI process

The service SHALL start the scheduler on FastAPI `startup` and SHALL stop it cleanly on `shutdown`.

#### Scenario: Scheduler is gated by `SCHEDULER_ENABLED`
- **WHEN** `SCHEDULER_ENABLED=false`
- **THEN** the scheduler SHALL NOT start and no jobs SHALL be registered

#### Scenario: Shutdown waits no longer than necessary
- **WHEN** the FastAPI app shuts down
- **THEN** the scheduler SHALL be torn down with `wait=False` so the process does not hang on in-flight jobs

### Requirement: Daily price backfill job invokes the existing market-data service

The `tw_daily_prices` job callable SHALL call `market_data_service.backfill_date(today, market="BOTH")` inside a fresh DB session and SHALL log the structured result.

#### Scenario: Job triggers backfill with the correct market
- **WHEN** the `tw_daily_prices` callable is invoked
- **THEN** `backfill_date` SHALL be called once with `market="BOTH"` and today's date in `Asia/Taipei`

### Requirement: Quote-refresh job is gated by TW market hours

The `quote_refresh` callable SHALL short-circuit when `is_tw_market_session(now)` returns false.

#### Scenario: Inside session window
- **GIVEN** the current TW time is a weekday at 10:00
- **WHEN** the callable runs
- **THEN** it SHALL fetch quotes for active-holding symbols

#### Scenario: Outside session window
- **GIVEN** the current TW time is a weekday at 14:00
- **WHEN** the callable runs
- **THEN** it SHALL NOT call the quote fetcher

#### Scenario: Weekend short-circuit
- **GIVEN** the current TW time is a Saturday at 10:00
- **WHEN** the callable runs
- **THEN** it SHALL NOT call the quote fetcher

#### Scenario: Session boundaries are inclusive-open
- **GIVEN** session window is defined as `09:00 <= time < 13:30`
- **WHEN** the time is exactly `09:00`
- **THEN** `is_tw_market_session` SHALL return true
- **WHEN** the time is exactly `13:30`
- **THEN** `is_tw_market_session` SHALL return false

