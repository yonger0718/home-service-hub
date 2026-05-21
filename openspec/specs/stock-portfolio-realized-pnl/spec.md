# stock-portfolio-realized-pnl Specification

## Purpose
TBD - created by archiving change add-realized-pnl-events-page. Update Purpose after archive.
## Requirements
### Requirement: Per-SELL realized event listing

The system SHALL expose `GET /api/portfolio/realized-pnl` returning realized P&L events computed against the corporate-action-adjusted transaction view using moving-average cost basis per `position_side` pool. Each event SHALL include `trade_date`, `symbol`, `name`, `quantity`, `sell_price`, `avg_cost_at_sale`, `fee`, `tax`, `proceeds_gross`, `proceeds_net`, `cost_out`, `realized_pnl`, `is_day_trade`, `position_side`, and an optional `note` field.

Realized events SHALL be emitted for closing transactions only:

- `position_side='LONG'` and `type='SELL'` — long close, realizes gain from long pool.
- `position_side='SHORT'` and `type='BUY'` — short cover, realizes gain from short pool.

Position-opening transactions (`LONG BUY`, `SHORT SELL`) SHALL NOT emit realized events.

#### Scenario: Long SELL after one long BUY uses that BUY's price as average cost

- **WHEN** a portfolio contains a single LONG BUY of 1000 shares at 100 NT$ followed by a LONG SELL of 400 shares at 120 NT$ with zero fees and zero tax
- **THEN** the endpoint returns exactly one event with `position_side='LONG'`, `quantity=400`, `avg_cost_at_sale=100`, `cost_out=40000`, `proceeds_net=48000`, and `realized_pnl=8000`

#### Scenario: Long SELL after multiple long BUYs uses moving average

- **WHEN** a portfolio contains LONG BUYs of 1000 @ 100 and 500 @ 130 (both zero fee) followed by a LONG SELL of 600 @ 140 (zero fee, zero tax)
- **THEN** the endpoint returns one event whose `avg_cost_at_sale` equals the weighted average `110` and whose `realized_pnl` equals `(600 * 140) - (600 * 110) = 18000`

#### Scenario: Fees and taxes are included in net proceeds

- **WHEN** a SELL records `fee=85` and `tax=255`
- **THEN** the event's `proceeds_net` equals `proceeds_gross - 85 - 255` and `realized_pnl` is computed from `proceeds_net`

#### Scenario: Day-trade flag is propagated

- **WHEN** a SELL transaction has `is_day_trade=true`
- **THEN** the corresponding event has `is_day_trade=true`

#### Scenario: Short SELL alone emits no realized event

- **WHEN** a portfolio contains a single SHORT SELL of 1000 shares at 100 NT$ (zero fees, zero tax) and no SHORT BUY cover
- **THEN** the endpoint returns zero events for that symbol

#### Scenario: Short cover realizes gain inverted from long math

- **WHEN** a portfolio contains a SHORT SELL of 1000 @ 100 (zero fee, zero tax) followed by a SHORT BUY (cover) of 400 @ 80 (zero fee)
- **THEN** the endpoint returns one event derived from the SHORT BUY transaction with `position_side='SHORT'`, `quantity=400`, `avg_cost_at_sale=100` (the short open price), `cost_out=32000` (cover gross), `proceeds_net=40000` (covered_qty × avg_open_net_per_share = 400 × 100), and `realized_pnl=(100-80)*400 = 8000`

#### Scenario: Partial short cover leaves residual short inventory

- **WHEN** a portfolio contains a SHORT SELL of 1000 @ 100 (zero fee) followed by a SHORT BUY of 400 @ 80 (zero fee)
- **THEN** the endpoint returns one event for the covered 400 shares and the residual 600-share short remains in the short pool for future cover events

### Requirement: Aggregate equals dashboard cumulative

The system SHALL guarantee that the sum of `realized_pnl` across every event returned by the endpoint with no filters applied equals `PortfolioSummary.total_realized_pnl` for the same portfolio state. The summary SHALL count both long-side and short-side realized P&L.

#### Scenario: Sum of unfiltered events matches summary (long-only fixture)

- **WHEN** a portfolio contains only LONG transactions
- **AND** the endpoint is called with no filters
- **AND** `get_portfolio_summary()` is called for the same portfolio
- **THEN** `sum(event.realized_pnl for event in response.items_across_all_pages)` equals `summary.total_realized_pnl`

#### Scenario: Sum of unfiltered events matches summary (mixed long + short fixture)

- **WHEN** a portfolio contains both LONG and SHORT round-trips on the same symbol
- **AND** the endpoint is called with no filters
- **THEN** `sum(event.realized_pnl)` equals `summary.total_realized_pnl`, including the short-cover gain

### Requirement: Filter by symbol, date range, year, and day-trade

The endpoint SHALL accept the optional query parameters `symbol`, `date_from`, `date_to`, `year`, and `day_trade_only` and return only events that match all supplied filters. When `year` is supplied alongside `date_from` or `date_to`, the explicit date range SHALL take precedence.

#### Scenario: Symbol filter narrows results

- **WHEN** a portfolio contains SELL events for both `2330` and `6488`
- **AND** the endpoint is called with `symbol=2330`
- **THEN** only events with `symbol=2330` are returned

#### Scenario: Year preset maps to calendar year boundaries

- **WHEN** the endpoint is called with `year=2025` and no `date_from` / `date_to`
- **THEN** the response contains only events whose `trade_date` falls within `2025-01-01` to `2025-12-31` inclusive

#### Scenario: Day-trade toggle filters non-day-trade rows out

- **WHEN** the endpoint is called with `day_trade_only=true`
- **THEN** the response contains only events with `is_day_trade=true`

### Requirement: Pagination and sort

The endpoint SHALL return a paged response of the shape `{items, total, summary}` and accept `offset`, `limit`, and `sort` query parameters. Default `limit` SHALL be 25; default `sort` SHALL be `trade_date:desc`. `total` SHALL reflect the total event count after filters and before pagination.

#### Scenario: Pagination boundary returns expected slice

- **WHEN** a filtered query produces 60 events and the endpoint is called with `offset=50&limit=25`
- **THEN** `items` contains 10 events, `total` equals 60

#### Scenario: Sort by realized PnL descending

- **WHEN** the endpoint is called with `sort=realized_pnl:desc`
- **THEN** `items` is ordered by `realized_pnl` from highest to lowest

### Requirement: Filter-scope and YTD aggregates in response

The endpoint SHALL include a `summary` object containing `filter_scope_total` (sum of `realized_pnl` across all events matching the active filter, regardless of pagination) and `ytd_total` (sum of `realized_pnl` across all events whose `trade_date` falls in the current calendar year, ignoring `symbol`, `date_from`, `date_to`, `year`, and `day_trade_only` filters).

#### Scenario: Filter-scope total reflects active filter

- **WHEN** the endpoint is called with `symbol=2330`
- **THEN** `summary.filter_scope_total` equals the sum of `realized_pnl` across every `2330` event, ignoring `offset` / `limit`

#### Scenario: YTD total ignores filters

- **WHEN** the endpoint is called with `symbol=6488`
- **AND** the portfolio also has `2330` SELL events in the current calendar year
- **THEN** `summary.ytd_total` includes the realized PnL of every event in the current calendar year for every symbol

### Requirement: No-inventory SELL is flagged

When a closing transaction occurs against zero prior inventory of the matching side (`LONG SELL` with no long pool, or `SHORT BUY` with no short pool), the system SHALL still emit an event and SHALL classify the anomaly in the `note` field.

- `LONG SELL` with empty long pool SHALL emit `cost_out=0`, `realized_pnl=proceeds_net`, `note="no_long_inventory"`.
- `SHORT BUY` with empty short pool SHALL emit `cost_out=cover_cost+fee+tax`, `realized_pnl=-cost_out`, `note="no_short_inventory"`.

#### Scenario: LONG SELL with no prior long BUY is flagged

- **WHEN** a portfolio contains a LONG SELL of 100 shares of `9999` at 50 NT$ (zero fees, zero tax) with no prior BUY of `9999`
- **THEN** the response contains an event with `position_side='LONG'`, `cost_out=0`, `realized_pnl=5000`, and `note="no_long_inventory"`

#### Scenario: SHORT BUY with no prior short SELL is flagged

- **WHEN** a portfolio contains a SHORT BUY of 100 shares at 50 NT$ (zero fees, zero tax) with no prior SHORT SELL
- **THEN** the response contains an event with `position_side='SHORT'`, `cost_out=5000`, `realized_pnl=-5000`, and `note="no_short_inventory"`

### Requirement: Frontend page and navigation

The frontend SHALL provide a route `/portfolio/realized-pnl` rendering a page that displays two aggregate cards (filter-scope total and YTD total), a filter bar (symbol autocomplete, date-from, date-to, year preset chips, day-trade toggle, sort dropdown), an event list using the existing hub-modern-list card layout, and a paginator with selectable page sizes. The top-level navigation SHALL include a link "已實現損益" to this route. Event list rows SHALL display a "融券" badge alongside the existing "當沖" badge when `position_side === 'SHORT'`.

#### Scenario: Page renders with required regions

- **WHEN** a user navigates to `/portfolio/realized-pnl`
- **THEN** the page renders the aggregate cards, the filter bar, the event list, and the paginator

#### Scenario: Year preset clears manual date range

- **WHEN** a user has typed a manual date range and then clicks a year preset chip
- **THEN** the manual date range inputs are cleared and the query uses the year preset

#### Scenario: Short cover row shows 融券 badge

- **WHEN** the event list renders an event with `position_side='SHORT'`
- **THEN** the row card SHALL render a "融券" badge in the badge slot, distinct from and able to co-exist with the "當沖" badge

### Requirement: Symbol filter uses prefix match

The endpoint's `symbol` query parameter SHALL match events whose `symbol` starts with the supplied value after `sanitize_symbol` normalization. This mirrors the prefix-match behavior of `list_transactions` / `list_dividends` (ILIKE `'<stem>%'`) so the symbol filter behaves the same across all three portfolio pages.

#### Scenario: Two-digit ETF prefix narrows to ETF family

- **WHEN** a portfolio contains events for `0050`, `0056`, and `2330`
- **AND** the endpoint is called with `symbol=00`
- **THEN** only events with `symbol` starting `00` are returned (`0050` + `0056`); `2330` is excluded

#### Scenario: Exact ticker still matches exactly

- **WHEN** the endpoint is called with `symbol=2330`
- **THEN** only events with `symbol` starting `2330` are returned (the original exact-match behavior is preserved as the degenerate case of prefix matching)

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

