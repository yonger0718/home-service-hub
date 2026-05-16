## 1. Schema check

- [x] 1.1 Confirm `price_history` PK is `(symbol, date)` — already present (migration `g4b5c6d7e8f9`).
- [x] 1.2 Confirm `ix_transactions_symbol_trade_date` already exists (migration `c4d5e6f7a8b9`).
- [x] 1.3 No new migration required.

## 2. Service layer

- [x] 2.1 Create `app/services/networth_backfill_service.py`.
- [x] 2.2 Implement `_iter_trading_days(from_d, to_d)` weekday filter; holidays detected via empty-payload probe.
- [x] 2.3 Implement `_fetch_with_retry(fetcher, date)` with retries at 2 s then 5 s on empty result.
- [x] 2.4 Implement `backfill_prices_range(db, from_d, to_d, throttle_sec=1.5)`: loop, sleep gap, empty-payload skip, per-date isolation.
- [x] 2.5 Holdings-as-of derived via chronological event walk (transactions + dividends), incremental in-memory state; stock dividends already represented as zero-cost BUY transactions.
- [x] 2.6 Cost basis maintained per symbol via average-cost reduction on SELL inside the same walk.
- [x] 2.7 Implement `replay_snapshots_range(db, from_d, to_d)`: per-date holdings × `price_history.close` ⇒ market_value; cost basis; cumulative dividends; `Session.merge` upsert with `portfolio_xirr=None`.
- [x] 2.8 Log WARN once per missing `(symbol, date)` price.

## 3. Router

- [x] 3.1 Add Pydantic `NetworthBackfillRequest{from, to, phase, throttle_sec}` co-located with the endpoint.
- [x] 3.2 Add `POST /api/portfolio/history/backfill-networth` to `app/routers/history.py` dispatching on `phase`.
- [x] 3.3 Validate `from <= to`; return 400 otherwise.

## 4. Tests

- [x] 4.1 `tests/unit/test_networth_backfill_service.py`: weekend skip, holiday-via-empty-payload skip, throttle gap respected, per-date error isolation, retry-with-backoff.
- [x] 4.2 Replay correctness: BUY, SELL with average-cost reduction, cumulative dividends, missing price ⇒ zero contribution.
- [x] 4.3 Idempotent re-run.
- [x] 4.4 Router smoke tests for snapshots phase + inverted-range rejection.

## 5. Verification

- [x] 5.1 `pytest` green in `services/stock-portfolio-service` — 286 passed.
- [x] 5.2 No new migrations.
- [x] 5.3 `openspec validate add-networth-backfill --strict` passes.
