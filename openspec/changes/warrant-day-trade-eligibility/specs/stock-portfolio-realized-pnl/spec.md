## ADDED Requirements

### Requirement: Day-trade flag respects instrument eligibility

The system SHALL set `transactions.is_day_trade=true` only for transactions whose symbol is eligible for Taiwan 現股當沖 per `symbol_map_service.is_day_trade_eligible`. Same-symbol same-day BUY+SELL pairs on ineligible instruments (whose `symbol_map.type` contains any of `認購`, `認售`, `牛證`, `熊證` — i.e. 認購權證, 認售權證, 牛證, 熊證 in any market variant) SHALL retain `is_day_trade=false`. This gating SHALL apply to the live transaction create/update flow. A one-shot backfill migration SHALL ALSO set `transactions.is_day_trade=false` on every existing row currently flagged `true` whose symbol is ineligible. The migration SHALL NOT modify rows for eligible symbols (the legacy bucket heuristic's positive-direction over-classification is out of scope and is tracked separately).

#### Scenario: Warrant BUY+SELL same day stays non-day-trade

- **GIVEN** `symbol_map` row `(symbol='045378', type='上市認購(售)權證')`
- **AND** a portfolio with a `045378` LONG BUY at 09:30 and a `045378` LONG SELL at 13:00 on the same calendar date
- **WHEN** `_recompute_day_trade_flags(db, '045378', that_date)` runs
- **THEN** both transactions SHALL have `is_day_trade=false`
- **AND** the realized-pnl event for the SELL SHALL have `is_day_trade=false`

#### Scenario: Equity BUY+SELL same day flags as day-trade

- **GIVEN** `symbol_map` row `(symbol='2330', type='股票')`
- **AND** a portfolio with a `2330` LONG BUY at 09:30 and a `2330` LONG SELL at 13:00 on the same calendar date
- **WHEN** `_recompute_day_trade_flags(db, '2330', that_date)` runs
- **THEN** both transactions SHALL have `is_day_trade=true`

#### Scenario: Unmapped symbol BUY+SELL same day flags as day-trade (fail-open)

- **GIVEN** no `symbol_map` row exists for `'9999'`
- **AND** a portfolio with a `9999` LONG BUY and LONG SELL on the same date
- **WHEN** `_recompute_day_trade_flags(db, '9999', that_date)` runs
- **THEN** both transactions SHALL have `is_day_trade=true`

#### Scenario: Backfill migration clears wrong warrant flags and leaves equities alone

- **GIVEN** a transactions table containing a `045378` warrant BUY+SELL pair both currently flagged `is_day_trade=true`
- **AND** an equity `2330` BUY+SELL pair currently flagged `is_day_trade=true`
- **AND** `symbol_map` rows `(symbol='045378', type='上市認購(售)權證')` and `(symbol='2330', type='股票')` populated
- **WHEN** the data migration `backfill_day_trade_flags` runs
- **THEN** both `045378` rows SHALL be updated to `is_day_trade=false`
- **AND** the equity `2330` rows SHALL remain `is_day_trade=true` (migration touches only ineligible-symbol rows)
