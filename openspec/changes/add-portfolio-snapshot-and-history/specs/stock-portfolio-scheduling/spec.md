## MODIFIED Requirements

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
