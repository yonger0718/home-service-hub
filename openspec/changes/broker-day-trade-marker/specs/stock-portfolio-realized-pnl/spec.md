## MODIFIED Requirements

### Requirement: Day-trade flag respects instrument eligibility

The system SHALL set `transactions.is_day_trade=true` only for transactions whose symbol is eligible for Taiwan 現股當沖 per `symbol_map_service.is_day_trade_eligible`. The flag SHALL be derived per (symbol, calendar_date) bucket via the following priority chain:

1. **Broker marker present** — if any row in the bucket has `broker_day_trade_marker IN ('沖買', '沖賣')`, every row in the bucket SHALL have `is_day_trade=true` IFF the symbol is eligible. The broker marker is authoritative even when only one side of the round-trip is present in the bucket (the matching opposite-side row may belong to a different calendar date).
2. **No broker marker, bucket has both BUY and SELL** — fall back to legacy heuristic: every row in the bucket SHALL have `is_day_trade=true` IFF the symbol is eligible. Preserves behavior for manually entered transactions and non-Cathay broker sources.
3. **Otherwise** — every row in the bucket SHALL have `is_day_trade=false`.

In all three branches, ineligible-instrument rows (whose `symbol_map.type` contains any of `認購`, `認售`, `牛證`, `熊證`) SHALL retain `is_day_trade=false`. The eligibility gate overrides the broker marker.

Same-symbol same-day BUY+SELL pairs on ineligible instruments SHALL retain `is_day_trade=false`. This gating SHALL apply to the live transaction create/update flow.

A one-shot backfill migration SHALL set `transactions.is_day_trade=false` on every existing row currently flagged `true` whose symbol is ineligible. The migration SHALL NOT modify rows for eligible symbols.

Additionally, odd-lot rows (`quantity < 1000 OR quantity % 1000 != 0`) SHALL never have `is_day_trade=true`, regardless of broker marker, bucket pair, or eligibility outcome. In `_recompute_day_trade_flags`, the bucket is split into odd-lot and board-lot subsets; the odd-lot subset always receives `false`, and the board-lot subset receives the priority-chain result computed only over board-lot rows. A separate one-shot data migration SHALL set `transactions.is_day_trade=false` on every existing odd-lot row currently flagged `true`.

#### Scenario: Warrant BUY+SELL same day stays non-day-trade

- **GIVEN** `symbol_map` row `(symbol='045378', type='上市認購(售)權證')`
- **AND** a portfolio with a `045378` LONG BUY at 09:30 and a `045378` LONG SELL at 13:00 on the same calendar date
- **WHEN** `_recompute_day_trade_flags(db, '045378', that_date)` runs
- **THEN** both transactions SHALL have `is_day_trade=false`
- **AND** the realized-pnl event for the SELL SHALL have `is_day_trade=false`

#### Scenario: Equity BUY+SELL same day flags as day-trade (legacy heuristic fallback)

- **GIVEN** `symbol_map` row `(symbol='2330', type='股票')`
- **AND** a portfolio with a `2330` LONG BUY at 09:30 and a `2330` LONG SELL at 13:00 on the same calendar date, both with `broker_day_trade_marker IS NULL`
- **WHEN** `_recompute_day_trade_flags(db, '2330', that_date)` runs
- **THEN** both transactions SHALL have `is_day_trade=true` (priority chain branch 2: no marker, bucket has BUY+SELL, symbol eligible)

#### Scenario: Equity BUY+SELL same day without marker falls back to legacy heuristic

- **GIVEN** `symbol_map` row `(symbol='2330', type='股票')`
- **AND** a Cathay-imported `2330` LONG BUY at 09:30 with `broker_day_trade_marker IS NULL` (`買賣別='現買'`)
- **AND** an unrelated `2330` LONG SELL at 13:00 on the same date with `broker_day_trade_marker IS NULL` (`買賣別='現賣'`)
- **WHEN** `_recompute_day_trade_flags(db, '2330', that_date)` runs
- **THEN** the bucket falls through to the legacy heuristic (priority chain branch 2) and both rows SHALL have `is_day_trade=true`

> Note: distinguishing "two coincident 現買/現賣 orders" from "true 現沖" requires the explicit broker marker. The heuristic fallback intentionally trusts the bucket pair-rule for non-marked rows; this is the legacy behavior and is documented as a known limitation that only the marker can resolve.

#### Scenario: Cathay 沖買 + 沖賣 same day flips bucket to day-trade via marker

- **GIVEN** `symbol_map` row `(symbol='2330', type='股票')`
- **AND** a Cathay-imported `2330` LONG BUY with `broker_day_trade_marker='沖買'`
- **AND** a Cathay-imported `2330` LONG SELL with `broker_day_trade_marker='沖賣'` on the same date
- **WHEN** `_recompute_day_trade_flags(db, '2330', that_date)` runs
- **THEN** both transactions SHALL have `is_day_trade=true` (priority chain branch 1: marker present, symbol eligible)

#### Scenario: Marker on warrant still rejected by eligibility gate

- **GIVEN** `symbol_map` row `(symbol='045378', type='上市認購(售)權證')`
- **AND** a Cathay-imported `045378` LONG BUY with `broker_day_trade_marker='沖買'` (broker mis-tag)
- **AND** a Cathay-imported `045378` LONG SELL with `broker_day_trade_marker='沖賣'` on the same date
- **WHEN** `_recompute_day_trade_flags(db, '045378', that_date)` runs
- **THEN** both transactions SHALL have `is_day_trade=false` (eligibility gate overrides marker)

#### Scenario: Marker on only one side still flips the bucket

- **GIVEN** `symbol_map` row `(symbol='2330', type='股票')`
- **AND** a Cathay-imported `2330` LONG BUY with `broker_day_trade_marker='沖買'` on date D (its paired SELL landed in a different bucket due to data lag)
- **WHEN** `_recompute_day_trade_flags(db, '2330', D)` runs
- **THEN** the BUY row SHALL have `is_day_trade=true` (priority chain branch 1: any marker in bucket is authoritative; symbol eligible)

#### Scenario: Odd-lot BUY+SELL same day stays false even with marker

- **GIVEN** `symbol_map` row `(symbol='6491', type='股票')`
- **AND** a `6491` LONG BUY of `25` shares with `broker_day_trade_marker='沖買'` and a `6491` LONG SELL of `25` shares with `broker_day_trade_marker='沖賣'` on the same calendar date (both odd-lot)
- **WHEN** `_recompute_day_trade_flags(db, '6491', that_date)` runs
- **THEN** both rows SHALL have `is_day_trade=false` (odd-lot rule overrides marker)

#### Scenario: Mixed odd-lot + board-lot bucket — only board-lot subset flips

- **GIVEN** `symbol_map` row `(symbol='2330', type='股票')`
- **AND** a `2330` LONG BUY of `1000` shares (board-lot) AND a `2330` LONG SELL of `1000` shares (board-lot) on date D
- **AND** an unrelated `2330` LONG BUY of `42` shares (odd-lot accumulation) on the same date D
- **WHEN** `_recompute_day_trade_flags(db, '2330', D)` runs
- **THEN** the two board-lot rows SHALL have `is_day_trade=true` (board-lot subset has BUY+SELL pair, symbol eligible)
- **AND** the odd-lot BUY row SHALL have `is_day_trade=false` (odd-lot rule)

#### Scenario: Board-lot pair flag ignores odd-lot rows in pair detection

- **GIVEN** `symbol_map` row `(symbol='2330', type='股票')`
- **AND** a `2330` LONG BUY of `1000` shares (board-lot) on date D
- **AND** a `2330` LONG SELL of `42` shares (odd-lot) on date D
- **WHEN** `_recompute_day_trade_flags(db, '2330', D)` runs
- **THEN** the board-lot subset has only a BUY (no SELL after odd-lot filter); priority chain branch 3 yields False
- **AND** the board-lot row SHALL have `is_day_trade=false`
- **AND** the odd-lot row SHALL have `is_day_trade=false`

#### Scenario: Odd-lot backfill migration clears legacy wrong flags

- **GIVEN** a transactions table containing `0056 BUY 42` and `0056 SELL 254` on the same date both currently flagged `is_day_trade=true` (both odd-lot)
- **AND** a `2330 BUY 1000 + 2330 SELL 1000` board-lot pair on a different date currently flagged `is_day_trade=true`
- **WHEN** the odd-lot backfill data migration runs
- **THEN** the two `0056` odd-lot rows SHALL be updated to `is_day_trade=false`
- **AND** the `2330` board-lot rows SHALL remain `is_day_trade=true`

#### Scenario: Empty bucket → False

- **GIVEN** `symbol_map` row `(symbol='2330', type='股票')`
- **AND** a portfolio containing only a single `2330` LONG BUY on date D with `broker_day_trade_marker IS NULL`
- **WHEN** `_recompute_day_trade_flags(db, '2330', D)` runs
- **THEN** the BUY row SHALL have `is_day_trade=false` (priority chain branch 3: no marker, no opposing side)

#### Scenario: Unmapped symbol BUY+SELL same day flags as day-trade (fail-open)

- **GIVEN** no `symbol_map` row exists for `'9999'`
- **AND** a portfolio with a `9999` LONG BUY and LONG SELL on the same date with `broker_day_trade_marker IS NULL`
- **WHEN** `_recompute_day_trade_flags(db, '9999', that_date)` runs
- **THEN** both transactions SHALL have `is_day_trade=true` (legacy fallback + fail-open eligibility)

#### Scenario: Backfill migration clears wrong warrant flags and leaves equities alone

- **GIVEN** a transactions table containing a `045378` warrant BUY+SELL pair both currently flagged `is_day_trade=true`
- **AND** an equity `2330` BUY+SELL pair currently flagged `is_day_trade=true`
- **AND** `symbol_map` rows `(symbol='045378', type='上市認購(售)權證')` and `(symbol='2330', type='股票')` populated
- **WHEN** the prior data migration `backfill_day_trade_flags` (from warrant-day-trade-eligibility) ran
- **THEN** both `045378` rows SHALL have `is_day_trade=false`
- **AND** the equity `2330` rows SHALL remain `is_day_trade=true` (migration touches only ineligible-symbol rows)
