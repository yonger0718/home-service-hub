## ADDED Requirements

### Requirement: Holding identity is composite `(symbol, market)` everywhere in the frontend

The frontend SHALL identify every holding by the composite key `(symbol, market)`. A single helper `holdingKey(holding)` exported from `frontend/src/app/models/portfolio.model.ts` SHALL be the only place the key format lives, returning the string `\`${symbol}|${market}\``. `PortfolioService` cache, row `trackBy` functions, selection state, and any per-holding lookup SHALL consume `holdingKey()` and MUST NOT key on `symbol` alone.

#### Scenario: Same symbol across two markets does not collide
- **WHEN** the summary response contains a holding `{ symbol: 'AAPL', market: 'US' }` and a holding `{ symbol: 'AAPL', market: 'TW' }`
- **THEN** `PortfolioService` SHALL expose two distinct holdings under two distinct keys (`AAPL|US` and `AAPL|TW`) and the dashboard table SHALL render two separate rows

#### Scenario: TrackBy uses composite key
- **WHEN** the holdings table renders
- **THEN** every `*ngFor` / table row binding over holdings SHALL use a `trackBy` function whose return value comes from `holdingKey(holding)`

### Requirement: Native price column renders verbatim with currency suffix for non-TW rows

The holdings table SHALL render a `Native Price` column for every holding. For `market !== 'TW'` the cell SHALL render `native_close` exactly as returned by the backend (no `/100` conversion, no rounding beyond 4 decimal places for `GBp` and 2 decimal places for any other currency) followed by `native_currency` as a suffix (e.g. `8050.0000 GBp`, `190.50 USD`). For `market === 'TW'` the cell SHALL render the price without a currency suffix.

#### Scenario: LSE pence ticker shows pence verbatim
- **GIVEN** a holding `{ market: 'LSE', native_close: 8050, native_currency: 'GBp' }`
- **WHEN** the holdings table renders the row
- **THEN** the `Native Price` cell SHALL display `8050.0000 GBp`

#### Scenario: US ticker shows USD suffix
- **GIVEN** a holding `{ market: 'US', native_close: 190.5, native_currency: 'USD' }`
- **WHEN** the holdings table renders the row
- **THEN** the `Native Price` cell SHALL display `190.50 USD`

#### Scenario: TW ticker shows no currency suffix
- **GIVEN** a holding `{ market: 'TW', native_close: 590, native_currency: 'TWD' }`
- **WHEN** the holdings table renders the row
- **THEN** the `Native Price` cell SHALL display `590.00`

### Requirement: Live FX rate is surfaced as a tooltip on the foreign TWD market-value cell

For every holding row where `market !== 'TW'` AND `live_fx_rate_to_twd != null`, the `market_value` (TWD) cell SHALL render an info icon whose tooltip text reads `Revalued at 1 ${native_currency} = ${live_fx_rate_to_twd} TWD`. For TW rows or rows where `live_fx_rate_to_twd` is null, no icon SHALL be rendered.

#### Scenario: Foreign row with live rate shows tooltip
- **GIVEN** a holding `{ market: 'US', native_currency: 'USD', live_fx_rate_to_twd: 31.45, market_value: 5993.2 }`
- **WHEN** the row renders
- **THEN** the market-value cell SHALL contain an info icon and its tooltip SHALL read `Revalued at 1 USD = 31.45 TWD`

#### Scenario: Foreign row with no live rate falls back to dash
- **GIVEN** a holding `{ market: 'US', live_fx_rate_to_twd: null }`
- **WHEN** the row renders
- **THEN** the market-value cell SHALL display a dash placeholder and SHALL NOT render the info icon

#### Scenario: TW row never renders the tooltip
- **GIVEN** a holding `{ market: 'TW' }`
- **WHEN** the row renders
- **THEN** no FX-rate info icon SHALL be present on the market-value cell

### Requirement: Transaction form market picker reveals FX inputs for non-TW markets

The transaction create/edit form SHALL contain a `market` dropdown with options `TW`, `US`, `LSE`, defaulting to `TW`. When the selected market is `TW`, the form SHALL NOT display `currency` or `fx_rate_to_twd` inputs. When the selected market is `US` or `LSE`, the form SHALL display a `currency` input (pre-filled `USD` for `US`, `GBP` for `LSE`, user-editable) and a required `fx_rate_to_twd` Decimal input.

#### Scenario: TW selection hides FX inputs
- **WHEN** the user selects `TW` in the market dropdown
- **THEN** the `currency` and `fx_rate_to_twd` inputs SHALL NOT be visible

#### Scenario: US selection pre-fills USD
- **WHEN** the user selects `US` in the market dropdown
- **THEN** the `currency` input SHALL be visible with the pre-filled value `USD` and SHALL remain editable

#### Scenario: LSE selection pre-fills GBP but allows GBp override
- **WHEN** the user selects `LSE` in the market dropdown
- **THEN** the `currency` input SHALL be visible with the pre-filled value `GBP`
- **AND** the user SHALL be able to change the value to `GBp` and submit successfully

#### Scenario: Non-TW submission rejects non-positive fx_rate_to_twd
- **WHEN** the user selects `US` and enters `fx_rate_to_twd` `<= 0`
- **THEN** the form SHALL block submission with a client-side validation error and SHALL NOT call the backend

### Requirement: Realized P&L list adds market and native amount columns

The realized-PnL list SHALL render a `Market` badge column and a `Native Amount` column for each event. The `Native Amount` column SHALL render native cost / proceeds with a currency suffix using the same formatting rule as the holdings table. When every event in the current dataset has `market === 'TW'`, both new columns SHALL be hidden so TW-only users see no layout change.

#### Scenario: Mixed dataset renders both new columns
- **GIVEN** the realized-PnL response includes events with at least one `market !== 'TW'`
- **WHEN** the list renders
- **THEN** the `Market` and `Native Amount` columns SHALL be visible

#### Scenario: TW-only dataset hides new columns
- **GIVEN** every event in the realized-PnL response has `market === 'TW'`
- **WHEN** the list renders
- **THEN** the `Market` and `Native Amount` columns SHALL NOT be rendered

### Requirement: nativeAmount pipe formats native values consistently

The frontend SHALL expose a pure pipe `nativeAmount` that accepts `(value: number | string | null, currency: string | null)` and returns a formatted string. Decimal places SHALL be 4 when `currency === 'GBp'` and 2 otherwise. When `currency` is `TWD` or null, no currency suffix SHALL be appended; otherwise the currency SHALL appear as a space-separated suffix. The same pipe SHALL be used by the holdings table, realized-PnL list, and any other foreign-market-aware component.

#### Scenario: GBp value uses 4 decimals
- **WHEN** the pipe is called with `(8050, 'GBp')`
- **THEN** it SHALL return `8050.0000 GBp`

#### Scenario: USD value uses 2 decimals
- **WHEN** the pipe is called with `(190.5, 'USD')`
- **THEN** it SHALL return `190.50 USD`

#### Scenario: TWD value omits the suffix
- **WHEN** the pipe is called with `(590, 'TWD')`
- **THEN** it SHALL return `590.00`

#### Scenario: Null value returns a dash
- **WHEN** the pipe is called with `(null, 'USD')`
- **THEN** it SHALL return `—`
