## Context

Phase 1 widened the schema for multi-market. Phase 2 added yfinance + FX cron. Phase 3 wired the dashboard. Phase 4 closes the data-ingest loop: stop hand-typing foreign trades, stop drifting per-broker cash, stop missing foreign dividends. The existing TW pattern is the model — Cathay CSV importer (`broker_cathay_service.py`) for TW trades, TWSE auto-fetch for TW dividends, manual entry as fallback. We now mirror that for foreign brokers.

Real CSV samples live in the repo root (`ib.csv`, `ft.csv`, `cs.csv`) and are the authoritative format reference. The generic CSV importer (`app/services/import_service.py`) and Cathay broker importer give us the SHA256 idempotency contract and the per-row reject + report shape; both stay intact.

Stakeholders: solo user account (yonger0718); three brokers in active use (Firstrade, IB, Schwab); networth view consumes per-broker cash; realized-PnL endpoint feeds the dashboard.

## Goals / Non-Goals

**Goals:**
- Three production-quality broker parsers driven off real CSV samples.
- One dispatcher that sniffs format from the first row / section header so users upload the broker's native statement without flag-passing.
- A first-class `broker_cash_flows` table that's the canonical source for per-broker cash balance over time.
- yfinance-driven foreign dividend cron that mirrors Phase 2's quote cron, gated by `SCHEDULER_ENABLED`.
- Idempotency contract preserved: same upload twice → 0 new rows.
- Fail loud: any row at an FX-uncovered date rejects with a clear error pointing at the row index, never silently estimates.

**Non-Goals:**
- Margin position tracking, margin interest accrual, short positions (deferred — FT `融資` marker is dropped at parse time).
- LSE-native broker importers (user holds LSE via IB, which is covered).
- Frontend per-broker import pages (Phase 5).
- Reconciliation alerts when broker cash flow + per-broker cash diverges (Phase 5).
- Tax withholding split on dividends — yfinance gross only; broker-net is deferred.
- Backfilling historical foreign dividends beyond the period yfinance returns by default.

## Decisions

### D1: Broker dispatcher sniffs format, not a query param

A new `app/services/broker_dispatch_service.py` reads the first ~5 lines of the upload and pattern-matches:

| Signal | Routes to |
|---|---|
| Line 1 starts with `Statement,Header,域名稱,域值` | IB |
| Header contains both `交易類別` and `代號` | Firstrade |
| Header is `"Date","Action","Symbol","Description",...` | Schwab |
| None of the above | existing generic `import_service` (manual CSV) |

Rationale: front-end stays a single endpoint; users drop the broker's raw export without picking a dropdown. Sniffing is restricted to header signatures we have real samples for — never content-based heuristics that could mis-route a hand-edited file.

Alternative considered: dropdown on the import page (broker picker). Rejected because the FT/IB headers are stable and unambiguous; one less click matters for a feature the user runs every wire-in.

### D2: New `broker_cash_flows` table is separate from `transactions`

```
broker_cash_flows
  id              SERIAL PK
  broker          ENUM (TW_CATHAY..FOREIGN_MANUAL)
  date            DATE NOT NULL
  type            ENUM (deposit, withdrawal, interest, dividend_cash, fee)
  amount          NUMERIC(18,4) NOT NULL  -- native currency, signed
  currency        CHAR(3) NOT NULL
  fx_rate_to_twd  NUMERIC(20,8) NULL      -- frozen at row date
  note            TEXT NULL
  import_fingerprint VARCHAR(64) UNIQUE NULL
  created_at      TIMESTAMP DEFAULT now()
```

Rationale: cash flows are conceptually distinct from equity trades — they have no `symbol`, no `quantity`, no `price`. Stuffing them into `transactions` would force `NULL` columns everywhere and confuse every read query. A dedicated table also lets us iterate the cash-balance derivation independently without touching the equity P&L path.

Alternative considered: reuse `transactions` with `type='CASH_DEPOSIT'`. Rejected because every existing query in `portfolio_service.py` would need an explicit type filter and we'd be one column rename away from a multi-day bug.

### D3: Broker enum added as nullable + backfilled, not synchronous

The migration adds `transactions.broker` as nullable, then a follow-up `UPDATE transactions SET broker = 'TW_MANUAL' WHERE broker IS NULL` runs inside the same migration. The column stays nullable for one release so any in-flight client without the field doesn't 500. Phase 5 tightens it to `NOT NULL`.

Rationale: the table has thousands of TW rows already; we want a single online-safe migration with no application downtime. Nullable + default backfill is the lowest-risk shape.

### D4: FX miss = explicit row reject, not silent estimate

If a row's `trade_date` has no matching `fx_rates` entry for that currency, the importer rejects the row with `{"row_index": N, "reason": "missing FX rate for 2026-06-02 USD"}` in the response payload. The whole upload still completes for the rows that did parse; the user sees the rejected rows and runs the FX cron manually or supplies the missing date.

Rationale: FX is the single biggest correctness lever in this whole stack. Estimating with the last known rate would create silent cost-basis drift that compounds over years. Failing loud is cheap — the user just re-runs the FX cron.

Alternative considered: fall back to the most recent fx_rate within ±2 days. Rejected because the user already has the daily cron and any miss is a real signal (yfinance outage, weekend, holiday) worth surfacing.

### D5: Foreign dividend cron is independent of broker import

`app/services/foreign_dividend_service.py` iterates all open foreign positions (`market in ('US', 'LSE') and total_quantity > 0`) and calls `yfinance.Ticker(symbol).dividends`. Each returned row upserts into the existing `dividends` table keyed on `(symbol, market, ex_dividend_date)`. Currency comes from yfinance `Ticker.fast_info.currency`; `fx_rate_to_twd` resolved against `fx_rates` at ex-date (with the same explicit reject as D4 if missing).

Rationale: keeps the broker-CSV path purely about what brokers sent (cash dividends, post-tax) and the auto-fetch path about the canonical announced dividend (gross, native). The two never need to agree, and we keep the existing TW pattern (TWSE = canonical announcement; per-broker statement is separate truth, deferred to Phase 5+).

Scheduling: `foreign_dividend_refresh` cron at 17:45 Asia/Taipei, after `fx_rate_refresh` (17:00) and `foreign_price_refresh` (17:30) so FX + close are both already populated for ex-date matching.

### D6: Per-broker cash balance is computed, not stored

Networth backfill service gains a helper `get_broker_cash_balance(broker, as_of_date) -> Decimal` that does `SELECT SUM(amount) FROM broker_cash_flows WHERE broker = :b AND date <= :d`. No materialised view, no daily snapshot — the table is small (one row per cash event), and the daily aggregate is cheap.

Rationale: avoids the entire class of "cash drifts from cash_flows" bugs. Single source of truth.

### D7: Realized-PnL endpoint stamps `broker` on each event

`iter_realized_events` reads the originating transaction's `broker` column and copies it to the emitted event. API response gains an optional `broker` field on each row. Frontend (Phase 5) will use this for per-broker P&L splits; backend ships the field now to avoid a second migration.

### D8: `import_fingerprint` on `broker_cash_flows` shares the existing SHA256 contract

Same `_source_row_hash(broker, date, type, amount, currency, note)` shape as the equity importer's fingerprint. Uniqueness enforced at the DB level via `UNIQUE` constraint; re-upload triggers `ON CONFLICT DO NOTHING`.

## Risks / Trade-offs

- **[Schema migration on big table]** Adding a nullable column to `transactions` + backfilling in one migration is online-safe for the row count we have today (low thousands). Mitigation: explicit `nullable=True` and a small UPDATE in the same migration — verified locally on a clone before merge.
- **[yfinance dividend over-fetch on first run]** For a long-held position, `Ticker.dividends` returns the whole history. Mitigation: idempotent upsert keyed on `(symbol, market, ex_dividend_date)` — duplicates do nothing. Acceptable one-time write spike.
- **[Sniffer false negatives]** A hand-edited Firstrade CSV with a re-saved header could break detection. Mitigation: sniffer falls back to the existing manual CSV path with an explicit warning; users can still re-export from the broker.
- **[Cash event ambiguity in FT 利息收入]** Interest income is recorded as a cash flow `type=interest`, not as a dividend. Mitigation: explicit in spec + tests; if the user later wants this rolled into realized-PnL, that's a separate (small) change.
- **[broker enum churn]** Adding a new broker (e.g., Robinhood) needs a migration. Mitigation: enum stored as VARCHAR + check constraint, not a true PG enum, so future expansion is just a constraint update + parser file — no table rewrite.
- **[Margin marker drop is lossy]** FT 融資 rows lose the margin signal. Mitigation: noted in proposal as deferred; if reintroduced, the row can be re-derived from the broker statement (we keep the original CSV bytes in import audit).

## Migration Plan

1. Alembic revision: add nullable `transactions.broker VARCHAR(32) NULL` with check constraint `IN (...)`. Same revision creates `broker_cash_flows` table.
2. In the same revision, run `UPDATE transactions SET broker='TW_MANUAL' WHERE broker IS NULL` so all existing rows get a stamp.
3. Ship importers + dispatcher + cash-flow service + dividend cron behind the existing `SCHEDULER_ENABLED` flag.
4. Phase 5 PR tightens `transactions.broker` to `NOT NULL` once the application has been writing the column for one release cycle.

Rollback: drop the new table + revert the column (nullable add → drop is safe, no data loss on the existing TW rows since the broker tag is purely additive context).

## Open Questions

- For LSE-via-IB rows where `Price Currency = GBP`, do we treat the trade as `market='LSE'`? Decision needed at parse time. Default: yes, infer `market='LSE'` when `Price Currency == GBP`; fall back to `market='US'` otherwise. (No yfinance network call at import — deterministic currency-based heuristic with an optional `market_resolver` hook for symbol-map overrides applied later in `import_service`.)
- `dividend_cash` cash-flow rows from broker CSVs vs `dividends` table rows from yfinance: same dividend, two paths. For Phase 4 they coexist; reconciliation deferred to Phase 5.
