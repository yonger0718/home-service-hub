## ADDED Requirements

### Requirement: FX rate model

The system SHALL persist daily FX snapshots in a new `fx_rate` table with columns: `date` (DATE, NOT NULL), `base_currency` (CHAR(3), ISO-4217), `quote_currency` (CHAR(3), ISO-4217), `rate` (NUMERIC(20,8), NOT NULL, > 0), `source` (VARCHAR(32), e.g. `open-er-api`, NOT NULL), `fetched_at` (TIMESTAMPTZ, default `now()`). The primary key SHALL be `(date, base_currency, quote_currency)`.

#### Scenario: Inserting a rate row succeeds
- **WHEN** `(2026-06-01, USD, TWD, 32.05, open-er-api)` is inserted
- **THEN** the row SHALL exist and `fetched_at` SHALL be set

#### Scenario: Duplicate primary key is rejected
- **GIVEN** the same `(date, base, quote)` row already exists
- **WHEN** a second INSERT with the same key is attempted
- **THEN** the database SHALL reject it; the upsert path SHALL UPDATE the existing row's `rate`, `source`, `fetched_at`

### Requirement: fawazahmed0/exchange-api daily fetch with CDN fallback

The system SHALL provide `fx_rate_service.fetch_and_store(base_currencies: list[str], quote_currencies: list[str], asof: date | None = None) -> FetchResult`. When invoked, it SHALL issue one HTTPS GET per base currency to the primary URL `https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{slot}/v1/currencies/{base_lc}.json`, where `{slot}` is `latest` when `asof` is None and `YYYY-MM-DD` matching `asof` otherwise, and `{base_lc}` is the lowercase base currency code.

If the primary URL returns a non-2xx response or a network error, the service SHALL retry exactly once against the fallback URL `https://{slot}.currency-api.pages.dev/v1/currencies/{base_lc}.json` (where `{slot}` becomes `latest` or `YYYY-MM-DD` per the same rule). Both URLs return JSON of shape `{"date": "YYYY-MM-DD", "{base_lc}": {"{quote_lc}": rate, ...}}` where `rate` is a JSON number expressed in `quote` per unit `base`.

The service SHALL upsert one `fx_rate` row per `(asof_or_response_date, base.upper(), quote.upper())` pair where the quote (lowercase) is present in the response payload AND in the requested `quote_currencies` (upper-cased for comparison). The persisted `source` SHALL be `fawazahmed0-jsdelivr` for primary-URL hits and `fawazahmed0-pages` for fallback hits.

The default base set SHALL be `["USD", "TWD"]` and the default quote set SHALL be `["TWD", "USD", "GBP", "JPY"]`. Self-pairs (base == quote) SHALL be skipped silently.

On both URLs failing for a given base, the service SHALL log the error, record `success=False` for that base in `FetchResult.per_base`, and continue with the next base. The service SHALL NOT raise; `FetchResult.success` SHALL be the conjunction of per-base success.

#### Scenario: Primary CDN succeeds and upserts the expected rates
- **GIVEN** `https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json` returns `{"date":"2026-06-01","usd":{"twd":32.05,"gbp":0.79,"jpy":156.3}}`
- **WHEN** `fetch_and_store(base_currencies=["USD"], quote_currencies=["TWD","GBP","JPY"])` is called with `asof=None`
- **THEN** three `fx_rate` rows SHALL exist for `(2026-06-01, USD, TWD/GBP/JPY)` with the published rates and `source='fawazahmed0-jsdelivr'`
- **AND** `FetchResult.success` SHALL be `True`

#### Scenario: Primary fails, fallback succeeds
- **GIVEN** the jsdelivr URL returns 503 and the `currency-api.pages.dev` URL returns the same payload as above
- **WHEN** the same call is issued
- **THEN** the rows SHALL be persisted with `source='fawazahmed0-pages'`
- **AND** `FetchResult.success` SHALL be `True`

#### Scenario: Both URLs fail — per-base failure recorded, others continue
- **GIVEN** both URLs return 503 for base `USD`, and both succeed for base `TWD`
- **WHEN** `fetch_and_store(base_currencies=["USD","TWD"], quote_currencies=["TWD","USD","GBP","JPY"])` is called
- **THEN** TWD-based rows SHALL be upserted
- **AND** `FetchResult.per_base["USD"].success` SHALL be `False` with `error` containing `503`
- **AND** `FetchResult.success` SHALL be `False` (conjunction)
- **AND** the service SHALL NOT raise

#### Scenario: Historical slot URL is used when asof is supplied
- **GIVEN** `asof=date(2025, 12, 31)` and a stub HTTP layer
- **WHEN** the fetch is issued for base `USD`
- **THEN** the primary URL SHALL be `https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@2025-12-31/v1/currencies/usd.json`
- **AND** any successful upsert SHALL key on `(2025-12-31, USD, *)`

#### Scenario: Quotes absent from response are skipped, not errored
- **GIVEN** the response payload contains `twd` and `gbp` but the caller also requested `xyz`
- **WHEN** the fetch is issued
- **THEN** rows for `(USD, TWD)` and `(USD, GBP)` SHALL be upserted
- **AND** no row SHALL be inserted for `XYZ`
- **AND** `FetchResult.per_base["USD"].success` SHALL be `True`

#### Scenario: Upsert overwrites a stale row and rewrites source
- **GIVEN** an existing `(2026-06-01, USD, TWD, 31.50, manual)` row
- **WHEN** a fresh primary-URL fetch returns `32.05`
- **THEN** the row SHALL be updated to `(2026-06-01, USD, TWD, 32.05, fawazahmed0-jsdelivr)`

### Requirement: Rate lookup with as-of fallback

The system SHALL provide `fx_rate_service.get_rate(date: date, base: str, quote: str) -> Decimal | None`. When the exact `(date, base, quote)` row exists, return its rate. Otherwise return the rate of the most recent row dated `<= date`. If no such row exists, return `None`.

Cross-pair lookup SHALL be supported by triangulation when a direct pair is missing but both `(base, USD)` and `(USD, quote)` are available (USD as pivot).

#### Scenario: Exact match returns the stored rate
- **GIVEN** an `fx_rate` row `(2026-06-01, USD, TWD, 32.05)`
- **WHEN** `get_rate(2026-06-01, "USD", "TWD")` is called
- **THEN** the result SHALL be `Decimal("32.05")`

#### Scenario: As-of falls back to the most recent earlier date
- **GIVEN** only `(2026-05-30, USD, TWD, 32.05)` exists for that pair
- **WHEN** `get_rate(2026-06-01, "USD", "TWD")` is called
- **THEN** the result SHALL be `Decimal("32.05")` and the source row SHALL be the May-30 one

#### Scenario: No rate at all returns None
- **GIVEN** zero rows for `(JPY, TWD)` and no `(JPY, USD)` row either
- **WHEN** `get_rate(2026-06-01, "JPY", "TWD")` is called
- **THEN** the result SHALL be `None`

#### Scenario: USD-pivot triangulation
- **GIVEN** `(2026-06-01, GBP, USD, 1.27)` and `(2026-06-01, USD, TWD, 32.05)` exist but no direct `(GBP, TWD)` row
- **WHEN** `get_rate(2026-06-01, "GBP", "TWD")` is called
- **THEN** the result SHALL be `Decimal("1.27") * Decimal("32.05") = Decimal("40.7035")`

### Requirement: Daily scheduler job

The system SHALL register a recurring scheduled job that calls `fetch_and_store` once per day at a configurable time (default `02:00 Asia/Taipei`). The job SHALL use the existing scheduler infrastructure (`stock-portfolio-scheduling` capability). On job failure, the next scheduled run SHALL still trigger normally; failures SHALL be logged via the existing structured-logging path.

#### Scenario: Scheduler registers the job on app startup
- **WHEN** the FastAPI app boots with the scheduler enabled
- **THEN** an APScheduler job named `fx_rate_daily` SHALL exist with a cron trigger of `02:00 Asia/Taipei`

#### Scenario: Job failure does not crash the scheduler
- **GIVEN** the fetch raises an unexpected exception
- **WHEN** the job runs
- **THEN** the exception SHALL be caught and logged
- **AND** the scheduler SHALL continue to operate

### Requirement: Manual refresh endpoint

The system SHALL expose `POST /api/portfolio/fx/refresh` accepting optional body `{"base_currencies": [...], "quote_currencies": [...], "asof": "YYYY-MM-DD"}`. The endpoint SHALL invoke `fetch_and_store` synchronously and return its `FetchResult` shape so operators can verify the result.

#### Scenario: Manual refresh returns per-base result
- **WHEN** `POST /api/portfolio/fx/refresh` is called with no body
- **THEN** the response SHALL include `success`, `per_base`, and `upserted_count` reflecting the fetch result
