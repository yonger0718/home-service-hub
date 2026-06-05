## Context

Phase 1 (PR #25) added market/currency/fx_rate_to_twd columns and made realized PnL FX-aware. The portfolio service can persist foreign rows but currently has no quote source for them: `market_data_service` only knows TWSE/TPEx OHLC + on-demand TWSE intraday quotes. The dashboard's `get_portfolio_summary` calls `get_stock_quotes(active_symbols)` against `twse_service`, which is why Phase 1 filtered foreign holdings out of that path defensively.

Phase 2 closes the gap: add yfinance as the foreign quote source plus a daily FX cron, behind a market-aware dispatcher. TW path stays bit-identical; foreign path becomes functional end-to-end.

## Goals / Non-Goals

**Goals:**
- Daily snapshot of US + LSE closes via yfinance, persisted to `price_history` with `market` populated.
- Daily FX-rate snapshot (`USDTWD=X`, `GBPTWD=X`) persisted to new `fx_rates` table.
- Market-value revaluation of foreign holdings in `get_portfolio_summary` using live close × live FX, without disturbing the existing TW summary path or frozen-FX cost basis math.
- Per-currency FX rate surfaced on `StockHolding` schema so Phase 3 UI can render per-currency breakdown.
- GBp/GBP/USD ambiguity on LSE handled by trusting yfinance `meta.currency` per ticker, with GBp = GBP/100 conversion applied on read.
- Cron jobs gated by existing `SCHEDULER_ENABLED` env flag (consistent with `tw_daily_prices` / `portfolio_snapshot`).

**Non-Goals:**
- Foreign broker CSV import (Phase 4+, deferred).
- Frontend UI changes — Phase 3.
- Real-time / WebSocket quotes — daily yfinance snapshot is sufficient.
- Historical foreign-price or FX backfill before deploy date.
- yfinance HA / fallback source — accept skip+warn on transient outage.
- Changes to realized PnL math (Phase 1 froze the cost-basis FX).

## Decisions

### D1 — Market-aware dispatcher abstraction

`app/services/quotes/dispatcher.py` exposes:
```python
def get_quotes(db, items: list[tuple[str, str]]) -> dict[tuple[str, str], Quote]: ...
def refresh_daily_ohlc(db, items: list[tuple[str, str]]) -> RefreshResult: ...
```
Groups by `market`, delegates to per-market fetchers. Default `market='TW'` keeps existing call sites compatible.

**Alternative considered:** strategy pattern via abstract class. Rejected — only two backends, single dispatch table is simpler and easier to test.

### D2 — yfinance as the only foreign quote source

Single library; no fallback chain in Phase 2.

**Alternative considered:** Alpha Vantage + IEX Cloud as fallback. Rejected — both require API keys and rate-limit free tier hard; yfinance is "free + occasionally flakes," and skip+warn covers it.

### D3 — Suffix mapping per market

| Market | yfinance suffix | Example |
|---|---|---|
| `US`  | bare ticker     | `AAPL` |
| `LSE` | `.L`            | `VOD.L` |

Mapping lives in `yfinance_fetcher._SYMBOL_SUFFIX = {'US': '', 'LSE': '.L'}`. New markets only need a row added.

### D4 — Trust yfinance `meta.currency`, store native + ISO base for FX

For each fetched ticker:
- Persist OHLC unchanged to `price_history`.
- Persist `meta.currency` to `price_history.currency` (NEW column added in this migration).
- `fx_rates` stores only ISO base codes: `USD`, `GBP`. `GBp = GBP / 100` derived at read time inside `_apply_live_fx`.

**Alternative considered:** treat GBp as separate currency in `fx_rates`. Rejected — GBp is a display convention, not an independent FX pair; storing it would double-write daily.

### D5 — Read-path revaluation (cost basis stays frozen)

In `portfolio_service.get_portfolio_summary`:
1. Existing TW path runs unchanged.
2. For each foreign holding `(symbol, market)`:
   - Look up latest `price_history` row with `(symbol, market)`.
   - Read `native_close` and `native_currency`.
   - Apply GBp→GBP divide-by-100 if `native_currency='GBp'`.
   - Look up latest `fx_rates` row for `(currency=base, date<=today)` → `live_fx`.
   - `market_value_twd = qty * native_close_in_base * live_fx`.
   - Stash `live_fx`, `native_close`, `native_currency` on `StockHolding`.
3. Cost basis remains computed from frozen `fx_rate_to_twd` via the Phase 1 realized-PnL engine — unchanged.

**Consequence by design:** `unrealized_pnl = market_value_twd - cost_basis_twd` carries embedded FX P&L for foreign rows. Documented in the realized-pnl delta spec; not a bug.

### D6 — `fx_rates` table shape

```sql
CREATE TABLE fx_rates (
  currency  CHAR(3) NOT NULL,
  date      DATE    NOT NULL,
  rate_to_twd NUMERIC(20,8) NOT NULL,
  source    VARCHAR(16) NOT NULL DEFAULT 'yfinance',
  PRIMARY KEY (currency, date)
);
```
No history backfill — first cron run on/after deploy date populates forward.

### D7 — Cron timing

| Job | Cron (Asia/Taipei) | Why |
|---|---|---|
| `fx_rate_refresh`      | 17:00 daily | After TW close (13:30), before foreign price job, gives FX value for the prior session. |
| `foreign_price_refresh`| 17:30 daily | After FX job; yfinance has US/LSE prior-day close populated by then. |

Asia/Taipei has no DST, so absolute UTC stays fixed (09:00 / 09:30 UTC).

### D8 — Per-ticker isolation

`_fetch_one(symbol)` runs in `try/except`. A single ticker failure logs `quotes.yfinance.skip` and continues — does not abort batch. Resulting `RefreshResult` carries `(ok_count, skipped_count, errors)` so the dashboard can surface "12/13 quotes fresh" gracefully.

### D9 — Tests

- Unit: dispatcher routing, GBp handling, FX rate parsing, suffix mapping, skip+warn on per-ticker fail.
- Integration: marked `@pytest.mark.live` — runs against real yfinance; skipped by default in CI, run on-demand. No VCR cassette (yfinance HTML/JSON shape changes too often).
- Migration: alembic round-trip on fresh local DB; verify create + drop both clean.

## Risks / Trade-offs

- **Risk:** yfinance silently changes payload shape (it has before). → Mitigation: fetcher checks `meta.currency` presence + numeric `regularMarketPrice`; missing fields produce `skip`, surfaced in `RefreshResult.errors`.
- **Risk:** US/LSE same calendar date in different timezones — `price_history.date` collision across markets. → Mitigation: `(symbol, market, date)` PK from Phase 1; date is the yfinance-reported trading date (their session date, not UTC).
- **Risk:** `meta.currency='GBp'` for an LSE ticker we'd assumed was GBP — silent 100× error. → Mitigation: always store `meta.currency` per row; read-path looks at the persisted value, not a market default.
- **Risk:** FX rate missing for a date a holding is being valued. → Mitigation: read-path picks `MAX(date) <= today` per currency. If table empty (first deploy day, cron not run yet), holding shows native close only and `quotes_status='partial'`.
- **Risk:** Live FX × live close drifts from frozen-FX cost basis = embedded FX P&L in `unrealized_pnl`. → Trade-off accepted by design; spec'd in realized-pnl delta and Phase 3 will surface the breakdown.

## Migration Plan

1. Merge new alembic revision creating `fx_rates` table + `price_history.currency` column.
2. Deploy code with `SCHEDULER_ENABLED=true`.
3. First `fx_rate_refresh` cron (17:00 TW) populates today's USD/GBP rows.
4. First `foreign_price_refresh` cron (17:30 TW) populates today's US/LSE prices.
5. Dashboard immediately revalues any pre-existing foreign holdings.

**Rollback:** revert code commit; downgrade migration drops `fx_rates` + `price_history.currency`. Foreign holdings revert to the Phase-1 behavior (filtered out of TW summary path).

## Open Questions

- Should `foreign_price_refresh` also write to `portfolio_snapshot` for the day, or leave that to the existing `portfolio_snapshot` job at 15:30 TW? → Defer: snapshot job runs first, will miss today's foreign close; first-snapshot for new foreign holdings appears one day later. Acceptable for Phase 2.
- LSE `meta.currency='USD'` for dual-listed tickers (e.g., some ADRs) — store as USD and FX via `USDTWD`? → Yes, that's exactly what D4 + D5 already cover; no special-case needed.
