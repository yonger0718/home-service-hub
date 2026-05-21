## MODIFIED Requirements

### Requirement: Snapshot replay from historical prices
The system SHALL provide a snapshot-replay function that, given an inclusive date range `[from, to]`, recomputes `portfolio_snapshot` rows from `transactions`, `dividends`, and `price_history` already present in the database. The replay SHALL delegate realized-PnL computation to `realized_pnl_service.iter_realized_events` (the canonical engine) and SHALL honor `Transaction.position_side` in the long-side qty/cost roll-up.

#### Scenario: Holdings-as-of-date calculated from LONG transactions only
- **WHEN** replay processes date `D`
- **THEN** long-side holdings per symbol equal the signed sum of `transactions.quantity` for that symbol where `trade_date <= D` AND `position_side = 'LONG'`, plus the sum of `dividends.stock_dividend_shares` for that symbol where `ex_dividend_date <= D`
- **AND** transactions with `position_side = 'SHORT'` contribute zero to long-side qty and zero to long-side cost

#### Scenario: Market value from same-date price_history
- **WHEN** replay computes market value for symbol `S` on date `D`
- **THEN** the value is `long_holdings[S] * price_history.close where symbol=S and date=D`

#### Scenario: Missing price treated as zero contribution
- **WHEN** no `price_history` row exists for symbol `S` on date `D`
- **THEN** symbol `S` contributes zero to that date's market value and a WARN is logged once per symbol-date pair

#### Scenario: Cumulative dividends summed
- **WHEN** replay computes `total_dividends` for date `D`
- **THEN** the value equals the sum of `dividends.amount` where `ex_dividend_date <= D`

#### Scenario: Realized PnL sourced from realized_pnl_service
- **WHEN** replay computes `total_realized_pnl` for date `D`
- **THEN** the value equals `sum(event.net_pnl for event in realized_pnl_service.iter_realized_events(all_transactions) if event.trade_date.date() <= D)`
- **AND** the value MUST equal the same-range aggregate returned by `GET /api/portfolio/realized-pnl?to=D` for any `D` in `[from, to]`

#### Scenario: SHORT open does not inflate long cost
- **WHEN** a transaction with `position_side='SHORT'` and `type='SELL'` (券賣 / 沖賣 open) is processed
- **THEN** long-side `qty[symbol]` and `cost[symbol]` are unchanged
- **AND** realized-PnL impact is captured via the `iter_realized_events` SHORT pool

#### Scenario: SHORT close does not add long qty
- **WHEN** a transaction with `position_side='SHORT'` and `type='BUY'` (券買 / 沖買 close) is processed
- **THEN** long-side `qty[symbol]` and `cost[symbol]` are unchanged
- **AND** realized-PnL impact is captured via the `iter_realized_events` SHORT pool

#### Scenario: Day-trade pair realized matches Taiwan tax rule
- **WHEN** a same-day BUY+SELL or short open+close pair is processed and both legs carry `is_day_trade=true`
- **THEN** `cumulative_realized` advances by `iter_realized_events` events which apply the 0.15% day-trade tax rule
- **AND** the inline replay loop SHALL NOT compute realized PnL itself

#### Scenario: Oversell handled via iter_realized_events
- **WHEN** a SELL transaction with `quantity > current long holdings` is processed
- **THEN** realized PnL for the closed portion comes from `iter_realized_events` (which flags the no-inventory excess per `stock-portfolio-realized-pnl` rules)
- **AND** the replay loop does NOT silently drop the excess fee/tax

#### Scenario: XIRR left null on backfilled rows
- **WHEN** replay writes a snapshot row
- **THEN** `portfolio_xirr` is `NULL`

#### Scenario: Idempotent upsert on date PK
- **WHEN** replay is invoked twice over the same range
- **THEN** the second run overwrites the same `portfolio_snapshot.date` rows via `Session.merge`, with no duplicate or orphan rows

## ADDED Requirements

### Requirement: Rebuild-all CLI for stale snapshot rows
The system SHALL provide a CLI invocation that recomputes every `portfolio_snapshot.total_realized_pnl` (and other replay-derived fields) for the full transaction history.

#### Scenario: CLI rebuilds full range
- **WHEN** operator runs `python -m app.services.networth_backfill_service --rebuild-all`
- **THEN** the command computes `from = min(transactions.trade_date)`, `to = today (TW)`, runs Phase 2 replay over that range, and upserts every affected `portfolio_snapshot.date`

#### Scenario: CLI dry-run prints diffs without writing
- **WHEN** operator runs `python -m app.services.networth_backfill_service --rebuild-all --dry-run`
- **THEN** the command prints per-date diffs `(date, old_total_realized_pnl, new_total_realized_pnl)` to stdout
- **AND** no rows in `portfolio_snapshot` are modified

#### Scenario: CLI exits non-zero on backfill error
- **WHEN** the rebuild encounters a per-date failure recorded in `BackfillResult.errors`
- **THEN** the CLI prints the error list to stderr and exits with status code `1`
