## 1. Pre-work — confirm divergence

- [ ] 1.1 Add temporary parity script `scripts/check_snapshot_realized_pnl.py` that prints per-date `(snapshot.total_realized_pnl, sum(iter_realized_events))` over the current DB; capture baseline diff against production data
- [x] 1.2 Write failing integration test `tests/integration/test_snapshot_realized_pnl_parity.py` with fixtures: LONG round-trip, SHORT 融券 round-trip, day-trade 沖賣 pair, oversell. Assert per-date equality. Must FAIL on current code.

## 2. Refactor replay loop

- [x] 2.1 In `app/services/networth_backfill_service.py`, import `iter_realized_events` from `realized_pnl_service`
- [x] 2.2 Before the date walk, call `events = list(iter_realized_events(transactions))`, then bucket: `realized_by_date: dict[date, Decimal] = defaultdict(Decimal); for e in events: realized_by_date[e.trade_date.date()] += e.net_pnl`
- [x] 2.3 Inside the per-date loop, advance `cumulative_realized` by `realized_by_date.pop(cur, Decimal(0))` (or sum buckets `<= cur` on first iteration)
- [x] 2.4 In the transaction walk (`:559-576`), add early-continue for `position_side == PositionSide.SHORT` BEFORE the BUY/SELL branch. Document that long-side qty/cost ignores SHORT by design.
- [x] 2.5 Remove the inline realized accumulator (lines computing `avg`, `sold`, `proceeds`, `cost_out`, `cumulative_realized += proceeds - cost_out`). Keep only qty/cost decrement for LONG SELL.
- [x] 2.6 Confirm `signed_net[sym]` tracking still drives the `mv` filter at `:617` correctly for the LONG-only case (it should — SHORT rows no longer touch `signed_net`)
- [x] 2.7 Run parity test from 1.2 — must now PASS

## 3. CLI rebuild

- [x] 3.1 Add `if __name__ == "__main__":` block (or extend existing CLI) in `networth_backfill_service.py` supporting `--rebuild-all` and `--dry-run` flags via `argparse`
- [x] 3.2 `--rebuild-all` computes range `[min(trade_date), today_tw()]`, opens session via `app.database.SessionLocal`, runs `_replay_snapshots`
- [x] 3.3 `--dry-run` skips the `session.merge` call inside `write_snapshot` and prints per-date diff `(date, old, new, delta)` to stdout
- [x] 3.4 Exit code `1` when `BackfillResult.errors` non-empty; print error list to stderr
- [x] 3.5 Add docstring + usage example referenced from `services/stock-portfolio-service/README.md`

## 4. Tests

- [x] 4.1 Unit test: SHORT-only history → `portfolio_snapshot.total_market_value == 0` AND `total_realized_pnl` matches `iter_realized_events` aggregate
- [x] 4.2 Unit test: mixed LONG+SHORT same symbol same day → long qty/cost reflect only LONG legs; realized covers both
- [x] 4.3 Unit test: day-trade 沖賣 pair → realized includes 0.15% tax reduction (matches `realized_pnl_service` output exactly)
- [x] 4.4 Unit test: oversell (SELL qty > current long qty) → realized matches endpoint; no silent excess-fee subtraction in snapshot
- [x] 4.5 Unit test: idempotent — running `_replay_snapshots` twice over same range yields identical `portfolio_snapshot` rows
- [x] 4.6 Integration test: full chain — import Cathay CSV with 融券 + 沖賣 rows → run post-import orchestrator step C → assert `portfolio_snapshot.total_realized_pnl` equals `/api/portfolio/realized-pnl` aggregate
- [x] 4.7 Regression: ensure no test in `tests/unit/test_networth_backfill_service.py` relies on the old inline realized formula; update fixtures if needed

## 5. Cleanup + verification

- [x] 5.1 Delete temporary script from 1.1
- [x] 5.2 Run `pytest tests/unit tests/integration` — all green
- [ ] 5.3 Run `python -m app.services.networth_backfill_service --rebuild-all --dry-run` against dev DB, eyeball diff list, confirm changes are explained by SHORT/day-trade/oversell rows
- [ ] 5.4 Run `--rebuild-all` (without `--dry-run`) on dev DB; spot-check `/api/portfolio/history` and `/api/portfolio/realized-pnl` agree
- [x] 5.5 `openspec validate unify-realized-pnl-snapshot --strict`
- [x] 5.6 Update `services/stock-portfolio-service/README.md` with rebuild CLI usage
- [ ] 5.7 Commit; PR; after merge run `--rebuild-all` on prod DB in maintenance window
