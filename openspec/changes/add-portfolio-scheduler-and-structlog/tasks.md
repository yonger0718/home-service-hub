## 1. Dependencies

- [x] 1.1 Add `apscheduler` and `structlog` (pinned) to `requirements.txt`
- [x] 1.2 Install into local venv and verify both import cleanly

## 2. Structured Logging

- [x] 2.1 New `app/logging_config.py` exposing `configure_logging()` that sets up a structlog processor chain: timestamper, level, logger name, OTel `trace_id`/`span_id` injection (from `opentelemetry.trace.get_current_span()`), exception formatting, JSON renderer when `LOG_FORMAT` is unset or `json`, console renderer when `LOG_FORMAT=console`
- [x] 2.2 Bridge stdlib `logging` so existing `logging.getLogger(__name__)` calls route through the same processor chain (use `structlog.stdlib.ProcessorFormatter` with foreign-pre-chain)
- [x] 2.3 Call `configure_logging()` from `app/main.py` before `create_app`
- [x] 2.4 Unit tests: rendered JSON contains `event`, `level`, `logger`; rendered output contains `trace_id`/`span_id` when an OTel span is active; renderer switches on `LOG_FORMAT=console`

## 3. Scheduler

- [x] 3.1 New `app/services/scheduler.py` exposing `build_scheduler(db_session_factory)` that returns a configured `BackgroundScheduler` tied to `Asia/Taipei`
- [x] 3.2 Register `tw_daily_prices` job — `CronTrigger(hour=17, minute=0, day_of_week='mon-fri', timezone='Asia/Taipei')` → wraps `market_data_service.backfill_date(date.today(), market="BOTH")` inside a fresh DB session and structlog-logs `{job, written, twse_rows, tpex_rows}`
- [x] 3.3 Register `quote_refresh` job — `CronTrigger(minute='*/15', hour='9-13', day_of_week='mon-fri', timezone='Asia/Taipei')` — gated by `is_tw_market_session(datetime.now(TW))` so the 13:00–13:30 leg only fires inside the session; calls `TWSEClient.fetch_quotes` for all active-holding symbols
- [x] 3.4 Register `portfolio_snapshot` job — `CronTrigger(hour=15, minute=30, timezone='Asia/Taipei')` — placeholder callable that just logs `job=portfolio_snapshot status=stub` (real implementation in next change)
- [x] 3.5 New `is_tw_market_session(now: datetime) -> bool` helper — true when weekday Mon–Fri and 09:00 ≤ time < 13:30 (no holiday list this change)
- [x] 3.6 Lifespan wiring in `app/main.py`: read `SCHEDULER_ENABLED` (default `true`); if true, call `build_scheduler(...)` on `startup` and `scheduler.shutdown(wait=False)` on `shutdown`
- [x] 3.7 Unit tests: market-hours gate true/false at sample times (incl. weekend, 08:59, 09:00, 13:29, 13:30, holiday-style Saturday); scheduler boot registers expected three job IDs and timezones; `tw_daily_prices` callable invokes `backfill_date` with `market="BOTH"`; `quote_refresh` callable short-circuits when gate returns false; scheduler skipped when `SCHEDULER_ENABLED=false`

## 4. Verification

- [x] 4.1 Run full `pytest` in `services/stock-portfolio-service/` — all prior tests (128) plus new logging + scheduler tests pass (158 total)
- [ ] 4.2 Boot the service locally with `uvicorn app.main:app --port 8001` and confirm the startup log line is a single JSON object with `event=scheduler.started` and three job entries
- [ ] 4.3 Toggle `LOG_FORMAT=console` and confirm the renderer switches without restarting Python
- [ ] 4.4 Toggle `SCHEDULER_ENABLED=false` and confirm no scheduler boot log appears and shutdown is clean
