## ADDED Requirements

### Requirement: Phase 2 forward-fills weekends and holidays during held intervals

The snapshot replay function SHALL write a `portfolio_snapshot` row on every weekend and full-market-holiday date in `[from_d, to_d]` provided that, immediately prior to that date, the user held at least one share of any symbol. The forward-filled row SHALL carry the previous trading day's `total_market_value` and `total_cost`, with `total_dividends` / `total_realized_pnl` advanced to include any events that posted on the non-trading date.

#### Scenario: Single weekend inside held interval gets two filled rows
- **WHEN** the user holds a position across Friday `2024-09-13` (trading) through Monday `2024-09-16` (trading), and replay runs over `[2024-09-13, 2024-09-16]`
- **THEN** snapshot rows exist for `2024-09-13`, `2024-09-14`, `2024-09-15`, and `2024-09-16`, with the Saturday and Sunday rows carrying the Friday `total_market_value` and `total_cost`

#### Scenario: 春節 cluster inside held interval gets forward-filled
- **WHEN** the user holds a position across `2023-01-17` (last trading day before LNY) through `2023-01-30` (first trading day after LNY) and replay runs over a range covering both
- **THEN** snapshot rows exist for every calendar date in `[2023-01-17, 2023-01-30]`, with the closed-market dates (`2023-01-18` through `2023-01-29`) carrying `2023-01-17`'s `total_market_value` and `total_cost`

#### Scenario: Non-trading date after SELL closing position stays empty
- **WHEN** the user has a final SELL on `2024-09-13` (Friday) that brings qty to 0, and replay runs over `[2024-09-13, 2024-09-16]`
- **THEN** a snapshot row exists for `2024-09-13` only; no rows are written for `2024-09-14` (Sat), `2024-09-15` (Sun), or `2024-09-16` (Mon, also inactive because qty=0)

#### Scenario: Forward-fill seeded from prior snapshot when range starts on a non-trading date
- **WHEN** replay is invoked with `from_d = 2023-01-21` (a Saturday during 春節 2023), `to_d = 2023-01-25`, and a snapshot row with `total_market_value > 0` exists in the DB for `2023-01-17`
- **THEN** snapshot rows are written for `2023-01-21` through `2023-01-25` carrying the `2023-01-17` row's values (until a real trading day inside the range overwrites the running state)

#### Scenario: Dividends posted on a non-trading date advance the cumulative total
- **WHEN** a cash dividend has an `ex_dividend_date` on a Saturday inside a held interval
- **THEN** the forward-filled Saturday row's `total_dividends` is greater than the prior Friday's by the dividend amount

### Requirement: Phase 2 treats `mv=0 cost>0` trading-day computations as a data gap

When the per-date market-value computation on a real trading day produces `mv = 0` while `total_cost > 0` — meaning every held symbol's price lookup missed despite the date being in `trading_dates` — the function SHALL NOT write a snapshot row for that date and SHALL NOT update `last_trading_mv` / `last_trading_cost`. The date SHALL be added to `stale_candidates` so the end-of-replay bulk DELETE removes any pre-existing bad row, and subsequent forward-fills SHALL continue to use the prior good snapshot value.

#### Scenario: Trading day with all held-symbol prices missing produces no row
- **WHEN** the user holds 5 symbols on a date that `price_history` contains rows for (so the date passes the `trading_dates` membership check) but none of those rows match any of the 5 held symbols
- **THEN** no `portfolio_snapshot` row is written for that date, `last_trading_mv` retains its previous value, and the date is included in the stale-candidate DELETE batch

#### Scenario: Subsequent forward-fill carries the prior good value, not zero
- **WHEN** the data-gap day above is immediately followed by a held-through weekend
- **THEN** the Saturday / Sunday forward-fill rows carry the MV+cost from the last GOOD trading-day snapshot, not from the gap day

### Requirement: Phase 2 self-heals stale `MV=0 cost>0` rows on skipped dates

When `replay_snapshots_range` would NOT write a snapshot row on a given date (the date is skipped because the user held nothing, or is a holiday not eligible for forward-fill, or any other skip path), the function SHALL DELETE any existing `portfolio_snapshot` row on that date whose `total_market_value = 0` AND `total_cost > 0`. Other pre-existing rows (e.g., legit zero-holding rows where `total_cost = 0`) SHALL remain untouched.

#### Scenario: Stale row on a holiday after SELL-closing the position is deleted
- **WHEN** a `portfolio_snapshot` row exists for `2023-01-23` with `total_market_value = 0` and `total_cost = 521478.2241`, the user closed their position before that holiday cluster, and replay runs over a range that includes `2023-01-23`
- **THEN** the row for `2023-01-23` is removed from `portfolio_snapshot` after replay completes

#### Scenario: Legit zero-holdings row with cost=0 is preserved
- **WHEN** a `portfolio_snapshot` row exists for `2024-05-01` with `total_market_value = 0` AND `total_cost = 0` (user genuinely held nothing), and replay runs over a range that includes `2024-05-01`
- **THEN** the row is NOT deleted

#### Scenario: Self-heal batched into a single DELETE per replay
- **WHEN** replay encounters 50 stale `MV=0 cost>0` rows across the range
- **THEN** a single bulk DELETE statement is issued at end-of-replay (not 50 separate round-trips)

### Requirement: Active-date helper exposes calendar-inclusive holding intervals

`compute_active_dates` SHALL accept an optional `include_non_trading: bool = False` parameter. When `True`, the returned set includes every calendar date (Mon-Sun) inside each held interval, not just weekdays. When `False` (default), behaviour is unchanged.

#### Scenario: include_non_trading=True returns weekend dates inside intervals
- **WHEN** the user holds shares from `2024-09-13` (Friday) through `2024-09-16` (Monday) and the helper is called with `include_non_trading=True`
- **THEN** the returned set includes `2024-09-14` (Sat) and `2024-09-15` (Sun)

#### Scenario: Default call shape unchanged
- **WHEN** the helper is called without `include_non_trading`
- **THEN** the returned set contains only weekdays in held intervals, identical to today's behaviour
