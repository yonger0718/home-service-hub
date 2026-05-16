## ADDED Requirements

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
