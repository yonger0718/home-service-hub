## Why

`stock-portfolio-service` already has TWSE/TPEx daily OHLC fetchers (`market_data_service.backfill_date`) and TWSE quote fetchers, but they only run via manual API calls or per-request user actions. To keep `price_history` populated for charts and to make `portfolio_summary` quotes hot during market hours, the service needs a small in-process scheduler. The next milestone (`portfolio_snapshot`) also needs a cron hook to materialise a daily snapshot.

At the same time, the service currently uses stdlib `logging` with default `Formatter`. Logs lose structure when piped to Loki, and OTel `trace_id`/`span_id` are present on log records but not surfaced in the rendered output. Switching to `structlog` with a JSON renderer (and a fallback console renderer for local dev) makes logs queryable and correlates them with traces.

## What Changes

- **APScheduler scaffold (`BackgroundScheduler`)** wired into the FastAPI lifecycle via `add_event_handler("startup")`/`("shutdown")`. Three cron jobs:
  - `tw_daily_prices` — 17:00 Asia/Taipei, Mon–Fri — calls `market_data_service.backfill_date(today, market="BOTH")`.
  - `quote_refresh` — every 15 min between 09:00 and 13:30 Asia/Taipei, Mon–Fri — calls `TWSEClient.fetch_quotes` for every active-holding symbol and warms the in-process quote cache.
  - `portfolio_snapshot` — 15:30 Asia/Taipei daily — registered as a no-op stub here; the implementation ships in the next change.
- **Market-hours gate** — small helper `is_tw_market_session(now)` used to short-circuit `quote_refresh` if it fires outside the TW session (holiday list intentionally out of scope; Mon–Fri + time-of-day window only).
- **structlog logging config** — new `app/logging_config.py` configures structlog with: stdlib bridge, OTel `trace_id`/`span_id` injection processor, JSON renderer in prod (default) or console renderer when `LOG_FORMAT=console`. Called from `app/main.py` before `create_app`.
- **Env-gated scheduler** — `SCHEDULER_ENABLED=true|false` (default `true`); tests set it `false`. Lifespan startup skips scheduler boot when disabled.

## Capabilities

### New Capabilities

- `stock-portfolio-scheduling`: in-process background scheduler that periodically refreshes daily prices, warms quote cache during TW market hours, and exposes a daily snapshot cron hook.
- `stock-portfolio-structured-logging`: structlog-based JSON logging with OpenTelemetry trace correlation and a console renderer for local dev.

### Modified Capabilities

- None.

## Impact

- **Code**
  - `services/stock-portfolio-service/app/logging_config.py` — NEW
  - `services/stock-portfolio-service/app/services/scheduler.py` — NEW
  - `services/stock-portfolio-service/app/main.py` — wire structlog + scheduler lifecycle
  - `services/stock-portfolio-service/requirements.txt` — add `apscheduler`, `structlog`
  - `services/stock-portfolio-service/tests/unit/test_scheduler.py` — NEW
  - `services/stock-portfolio-service/tests/unit/test_logging_config.py` — NEW

- **API**
  - None. Pure operational change.

- **Operational**
  - New env vars: `SCHEDULER_ENABLED` (default `true`), `LOG_FORMAT` (`json` default, `console` for local dev).
  - APScheduler uses `Asia/Taipei` timezone exclusively for job schedules.

- **Risks**
  - `BackgroundScheduler` spawns OS threads inside the FastAPI process. Worker forking (gunicorn) would multiply jobs by worker count; the service runs single-process under `uvicorn`/`pm2`, so this is safe today, but document it.
  - `quote_refresh` querying the DB for active holdings every 15 min is bounded by symbol count and SQL cost; if it ever becomes noisy, downgrade per-symbol logs to DEBUG (already done in the reliability change).
  - structlog replaces logger config globally for the process. Other services in the same monorepo are unaffected because they have their own `main.py`; the change is scoped to `stock-portfolio-service`.
