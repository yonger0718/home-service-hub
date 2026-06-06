## ADDED Requirements

### Requirement: Transaction form accepts market plus conditional FX inputs

The transaction create/edit form SHALL include a `market` dropdown (options `TW`, `US`, `LSE`; default `TW`). Selecting a non-TW market SHALL reveal a `currency` input pre-filled from the market choice (`US`→`USD`, `LSE`→`GBP`, user-editable so `GBp` can be entered) and a required `fx_rate_to_twd` Decimal input. TW selections SHALL render the form identically to the pre-Phase-3 layout (no FX inputs, no extra friction).

#### Scenario: Submitting a TW trade sends no fx fields
- **WHEN** the user submits the form with `market === 'TW'`
- **THEN** the request body SHALL NOT include `fx_rate_to_twd` and the `currency` field, if sent, SHALL be `TWD`

#### Scenario: Submitting a US trade includes USD currency and fx_rate_to_twd
- **WHEN** the user selects `US`, leaves `currency` as `USD`, enters a valid `fx_rate_to_twd`, and submits
- **THEN** the request body SHALL include `market: 'US'`, `currency: 'USD'`, and the entered `fx_rate_to_twd`

#### Scenario: Submitting an LSE GBp trade preserves the override currency
- **WHEN** the user selects `LSE`, changes `currency` to `GBp`, enters a valid `fx_rate_to_twd`, and submits
- **THEN** the request body SHALL include `market: 'LSE'`, `currency: 'GBp'`, and the entered `fx_rate_to_twd`

#### Scenario: Non-positive fx_rate_to_twd blocks submission
- **WHEN** the user selects `US` and enters `fx_rate_to_twd <= 0`
- **THEN** client-side validation SHALL block submission and surface an inline error

### Requirement: Transaction list renders market column for non-TW rows

The transaction list (timeline view per existing `frontend-stock-transactions` requirements) SHALL render a `market` badge next to each non-TW row. TW rows SHALL render with no market badge so existing visuals stay unchanged.

#### Scenario: US trade renders market badge
- **GIVEN** a transaction with `market === 'US'`
- **WHEN** the row renders
- **THEN** a `US` badge SHALL appear in the row meta area

#### Scenario: TW trade has no market badge
- **GIVEN** a transaction with `market === 'TW'`
- **WHEN** the row renders
- **THEN** no market badge SHALL appear and the row layout SHALL match the pre-Phase-3 layout pixel-for-pixel
