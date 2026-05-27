## Why

Today the ex-dividend endpoint pulls from a single TWSE feed (TWT48U). That feed only covers events the exchange has just announced; it misses historical events and every TPEx (OTC) symbol entirely. For users holding OTC stocks or wanting a fuller history before deciding on a position, the dashboard is silent. Stonk solves this by pulling from multiple sources and merging them; this change brings the same approach to home-hub without disturbing the existing `/upcoming` contract.

## What Changes

- **`dividend_sources/` package** — one module per upstream, each producing a normalised `DividendEventRow` list:
  - `twse_twt48u.py` — current TWT48U feed (refactored from `exdividend_service.parse_twse_exdividend_records` with no behaviour change).
  - `twse_twt49u.py` — TWSE historical ex-rights (TWT49U OpenAPI), JSON-only.
  - `tpex_otc.py` — TPEx OTC ex-daily-Q (`exDailyQ`), JSON-only.
- **`dividend_event_service.fetch_for_holdings(held_symbols, *, year=None)`** — runs the three sources in parallel-by-loop, filters each by `held_symbols`, merges into a deduped list keyed by `(symbol, ex_dividend_date)`, sorts ascending by date. A source raising or timing out is logged and its results dropped — the other sources still produce output.
- **`GET /api/portfolio/dividend-events?year=YYYY`** — new endpoint backed by the orchestrator. `year` defaults to the current TW year.
- **Existing `/upcoming` endpoint unchanged.** The TWT48U-only path keeps its semantics so the dashboard's upcoming widget is not affected.

### Out of scope

- MOPS (公開資訊觀測站) source — schema differs significantly and would require its own design pass.
- Capital reductions — different ratio semantics, needs corp-action plumbing.
- Persisting dividend events to a table — the new endpoint stays read-through; storage can come later.
- Frontend changes — no new UI this change. The endpoint is available for future panels.

## Capabilities

### New Capabilities

- `stock-portfolio-dividend-events`: multi-source ex-dividend / ex-right event aggregation across TWSE listed + TPEx OTC symbols with deduped output.

### Modified Capabilities

- None.

## Impact

- **Code**
  - `services/stock-portfolio-service/app/services/dividend_sources/__init__.py` — NEW
  - `services/stock-portfolio-service/app/services/dividend_sources/twse_twt48u.py` — NEW (extracted from `exdividend_service`)
  - `services/stock-portfolio-service/app/services/dividend_sources/twse_twt49u.py` — NEW
  - `services/stock-portfolio-service/app/services/dividend_sources/tpex_otc.py` — NEW
  - `services/stock-portfolio-service/app/services/dividend_event_service.py` — NEW
  - `services/stock-portfolio-service/app/routers/exdividend.py` — add `/dividend-events` route
  - `services/stock-portfolio-service/tests/unit/test_dividend_sources.py` — NEW
  - `services/stock-portfolio-service/tests/unit/test_dividend_event_service.py` — NEW

- **API (additive)**
  - `GET /api/portfolio/dividend-events?year=YYYY`

- **Operational**
  - No new env vars. Uses existing `TWSEClient` TLS-fallback policy where TWSE-side; TPEx uses the same `_http_get` helper from `market_data_service`.
  - Three HTTP calls per request — bounded; cached at the `TWSEClient` layer where applicable.

- **Risks**
  - Two new upstream JSON feeds; both have been observed in stonk's tests. If TPEx returns the form payload as multipart instead of JSON, the source returns empty and the orchestrator continues.
  - Dedupe key collisions: TWT48U and TWT49U can both list the same `(symbol, ex_date)`. Resolution: first non-null cash/stock wins; `source` tag records which feed produced the surviving record.
