## ADDED Requirements

### Requirement: Expense doughnut with custom legend

The accounting analytics screen SHALL render a Chart.js doughnut chart of expense breakdown by category, accompanied by a custom legend listing each category, its colour swatch, share percentage, and absolute amount.

#### Scenario: Legend matches doughnut segments
- **WHEN** the doughnut renders N categories
- **THEN** the custom legend renders N rows in the same order, each with matching colour swatch + label + percentage + amount

### Requirement: Category-change list

The screen SHALL render a list of category month-over-month changes with `PctBadge` deltas.

#### Scenario: Up category shows positive delta
- **WHEN** a category increased month-over-month
- **THEN** the row shows a positive-signed `PctBadge`

### Requirement: Credit-card limit monitor

The screen SHALL render a credit-card limit monitor block showing current period usage versus limit per card.

#### Scenario: Card over limit highlights
- **WHEN** a card's usage exceeds its limit
- **THEN** the row visually indicates an over-limit state using `var(--c-red)` accent (cashflow convention, not market trend)

### Requirement: Cashflow colours decoupled from data-gainloss

All cashflow colours on this screen (income green, expense neutral, increased-spending red) SHALL use direct tokens (`--c-green`, `--app-text-muted`, `--c-red`) and SHALL NOT consume `--app-trend-positive`/`--app-trend-negative`.

#### Scenario: Convention flip does NOT recolour cashflow
- **WHEN** the user flips `data-gainloss` from asian to western
- **THEN** the doughnut, legend, category-change list, and credit-card monitor colours remain unchanged
