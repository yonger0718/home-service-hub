## Why

Phases 1–3 of the foreign-market rollout shipped the backend schema, yfinance quotes, daily FX cron, and the Angular UI to display US/LSE holdings. The user can record a foreign trade only by hand-typing market + currency + fx_rate_to_twd into the transaction form — exactly the pain point the existing Cathay CSV importer solved for TW. We also have no way to know per-broker cash balances, so the dashboard's per-broker cash tile (currently seeded by hand: Firstrade $80.26, IB $61.51, Schwab $1500) drifts the moment the user wires or trades. Foreign dividends have no auto-fetch path either — the user would otherwise have to hand-enter every US dividend, while Phase 2 already proved yfinance is a reliable feed for that data.

## What Changes

- Add three broker-specific CSV importers — Firstrade (FT), Interactive Brokers (IB), Charles Schwab (CS) — parsing equity trades and cash-only rows from the brokers' native statement formats (real samples in `ib.csv`, `ft.csv`, `cs.csv`).
- Add a **broker dispatcher** in front of the existing generic `import_service` that sniffs the first row / section header and routes to the right parser. Manual CSV path stays the default fallback.
- Add a `broker` column to `transactions` (enum `TW_CATHAY | TW_SINOPAC | TW_MANUAL | IB | FIRSTRADE | SCHWAB | FOREIGN_MANUAL`). Every importer stamps the broker per row; manual entries default to the matching `*_MANUAL` value.
- Add a NEW `broker_cash_flows` table — separate from equity transactions — capturing wires in/out, interest income, dividend cash receipts, and broker fees. Per-broker cash balance becomes a derived view, no longer a hand-typed number.
- Auto-fetch foreign dividends via `yfinance.Ticker.dividends` on a new APScheduler cron `foreign_dividend_refresh` at 17:45 Asia/Taipei (after `fx_rate_refresh`). Upsert into the existing `dividends` table keyed by `(symbol, market, ex_dividend_date)`, with `currency` from yfinance meta and `fx_rate_to_twd` resolved against `fx_rates` at ex-date.
- Reject any import row whose `trade_date` has no matching `fx_rates` entry, surfacing the offending row index — never silently estimate.
- Day-trade detection (`is_day_trade`) stays TW-only; foreign rows skip the bucket heuristic entirely.
- Drop FT's `賬戶類別=融資` marker on parse — margin position modelling is deferred; treat as plain BUY/SELL.

## Capabilities

### New Capabilities
- `stock-portfolio-broker-firstrade-import`: parse Firstrade flat CSV (Chinese headers, 買進/賣出/存款/利息收入) into equity transactions + cash flows.
- `stock-portfolio-broker-ib-import`: parse IB multi-section CSV (`轉賬歷史` rows + `總結` base-currency anchor) into equity transactions + cash flows, honouring per-row `Price Currency`.
- `stock-portfolio-broker-schwab-import`: parse Charles Schwab English CSV (Date/Action/Symbol/Quantity/Price/Fees & Comm/Amount) into equity transactions + cash flows.
- `stock-portfolio-broker-cash-flows`: new `broker_cash_flows` table, write API used by the three importers, and read API exposing per-broker cash balance over time (consumed by the existing networth backfill service).
- `stock-portfolio-foreign-dividends-auto-fetch`: yfinance-driven dividend cron that mirrors the Phase 2 quote cron pattern; upsert keyed by `(symbol, market, ex_dividend_date)`.
- `frontend-broker-import`: new Angular page at `/portfolio/import-broker` for uploading broker CSVs with dry-run preview + commit, surfacing dispatcher-detected broker, parsed rows, and idempotent re-upload counts.

### Modified Capabilities
- `stock-portfolio-csv-import`: new format-sniffing dispatcher routes uploads to the right broker parser; manual CSV stays the fallback; idempotency contract unchanged (still SHA256 `import_fingerprint`).
- `stock-portfolio-scheduling`: new daily cron `foreign_dividend_refresh` at 17:45 Asia/Taipei, gated by `SCHEDULER_ENABLED`.
- `stock-portfolio-realized-pnl`: realized-PnL events carry the originating `broker` field through to the API response, plus the Angular realized-PnL page surfaces a per-broker badge column and broker filter chips.
- `frontend-portfolio-dashboard`: per-broker cash tile reads from `GET /api/portfolio/broker-cash-flows` instead of hand-typed values; existing aggregate cash tile retained for ALL-broker view.
- `frontend-stock-transactions`: transaction list shows a broker badge for every row whose `broker` is non-null and not `TW_MANUAL`.

## Impact

- Code: `services/stock-portfolio-service/app/services/broker_firstrade_service.py`, `broker_ib_service.py`, `broker_schwab_service.py`, `broker_dispatch_service.py` (NEW); `app/services/foreign_dividend_service.py` (NEW); `app/services/cash_flow_service.py` (NEW); `app/services/import_service.py` (sniffer hook); `app/services/scheduler.py` (cron); `app/services/portfolio_service.py` (broker passthrough on realized-PnL); `app/models/portfolio.py` (broker enum + column on `transactions`, new `BrokerCashFlow` model); `app/schemas/portfolio.py` (broker field + cash-flow schemas); one new alembic migration adding the column + table. Frontend: `frontend/src/app/components/portfolio/broker-import/*` (NEW) for the upload page; `frontend/src/app/models/portfolio.model.ts` for the `Broker` enum + `BrokerCashFlow` + `BrokerCsvImportResult` types; `frontend/src/app/services/portfolio.service.ts` for `uploadBrokerCsv` + `getBrokerCashFlows`; transaction list, realized-PnL list, and dashboard templates pick up broker badges + per-broker cash tile.
- APIs: existing `POST /api/portfolio/imports/csv` accepts the new broker formats with no client change required; new `GET /api/portfolio/broker-cash-flows` for per-broker balance read; realized-PnL response gains optional `broker` field on each event.
- Dependencies: yfinance already in `requirements.txt` from Phase 2 — no new packages.
- Tests: unit tests per parser using the real fixtures from `{ib,ft,cs}.csv`; unit tests for FX-miss rejection, idempotency, dispatcher sniffing, dividend cron upserts (mocked yfinance), and alembic round-trip. Integration test wiring the dispatcher through `POST /api/portfolio/imports/csv`.
- Risk: schema migration touches the heavily-used `transactions` table — must run as additive (nullable column + default backfill `TW_MANUAL` for existing rows) to keep the migration online-safe. `broker_cash_flows` is brand new so no rewrite risk there. Foreign dividend cron can over-fetch on first run for a long-held position; mitigated by upsert keyed on ex_dividend_date.
