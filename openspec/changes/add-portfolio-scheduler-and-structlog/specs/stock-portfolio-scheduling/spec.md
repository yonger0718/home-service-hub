## ADDED Requirements

### Requirement: Service runs an in-process background scheduler with three TW cron jobs

The service SHALL boot a `BackgroundScheduler` on FastAPI startup (when enabled) and SHALL register exactly three jobs anchored to `Asia/Taipei`.

#### Scenario: Scheduler registers tw_daily_prices
- **WHEN** the scheduler boots
- **THEN** a job with id `tw_daily_prices` SHALL be registered with a cron trigger of `hour=17, minute=0, day_of_week=mon-fri, timezone=Asia/Taipei`

#### Scenario: Scheduler registers quote_refresh
- **WHEN** the scheduler boots
- **THEN** a job with id `quote_refresh` SHALL be registered with a cron trigger of `minute=*/15, hour=9-13, day_of_week=mon-fri, timezone=Asia/Taipei`

#### Scenario: Scheduler registers portfolio_snapshot
- **WHEN** the scheduler boots
- **THEN** a job with id `portfolio_snapshot` SHALL be registered with a cron trigger of `hour=15, minute=30, timezone=Asia/Taipei`

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
