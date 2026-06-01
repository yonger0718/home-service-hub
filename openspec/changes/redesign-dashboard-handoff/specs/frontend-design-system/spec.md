## ADDED Requirements

### Requirement: Design tokens lifted from handoff verbatim

The frontend SHALL define all colour, typography, spacing, radius, elevation, and motion tokens by lifting `design_handoff_dashboard/design_refs/colors_and_type.css` into `frontend/src/styles.scss` verbatim, preserving CSS custom property names (`--app-*`, `--c-*`, `--fs-*`, `--space-*`, `--radius-*`, `--ease-*`, `--dur-*`). Deprecated slate trend values previously in `styles.scss` MUST be removed.

#### Scenario: Light tokens match handoff
- **WHEN** the application loads in light mode
- **THEN** `getComputedStyle(documentElement).getPropertyValue('--app-bg')` returns `#f1f3f6`
- **AND** `--app-primary` returns `#533afd`
- **AND** `--c-red` returns `#e5484d` and `--c-green` returns `#1f9d6b`

#### Scenario: Dark tokens override on dark mode
- **WHEN** the root element has class `app-dark-mode`
- **THEN** `--app-bg`, `--app-surface`, `--app-text`, and other `--app-*` tokens resolve to their dark-block values from `colors_and_type.css`

#### Scenario: No deprecated slate trend values remain
- **WHEN** searching `frontend/src/styles.scss` for the deprecated slate values (`#5f7f98`, `#7b87a8`) as trend tokens
- **THEN** they appear only as `--app-success`/`--app-warning` non-trend status colours, NOT as `--app-trend-positive`/`--app-trend-negative`

### Requirement: Gain/loss colour convention resolved via root attribute

The frontend SHALL resolve `--app-trend-positive` and `--app-trend-negative` from a `data-gainloss` attribute on the root element. `data-gainloss="asian"` MUST map positive to `--c-red` and negative to `--c-green`. `data-gainloss="western"` MUST swap them. No component CSS may hard-code red/green for gain/loss state.

#### Scenario: Asian convention (default)
- **WHEN** the root element has `data-gainloss="asian"`
- **THEN** `--app-trend-positive` resolves to the red base and `--app-trend-negative` to the green base

#### Scenario: Western convention
- **WHEN** the root element has `data-gainloss="western"`
- **THEN** `--app-trend-positive` resolves to the green base and `--app-trend-negative` to the red base

#### Scenario: Single attribute flip recolours app
- **WHEN** `data-gainloss` flips from `asian` to `western`
- **THEN** every element using `var(--app-trend-positive)` or `var(--app-trend-negative)` repaints with the swapped colour without per-component code changes

### Requirement: Typography stack and tokens

The frontend SHALL use the system font stack defined in handoff (`-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, …`) and define `--fs-display`, `--fs-value`, `--fs-h2`, `--fs-h3`, `--fs-card-title`, `--fs-body`, `--fs-sm`, `--fs-label`, `--fs-micro` per the handoff scale. Numeric values MUST use `font-variant-numeric: tabular-nums`.

#### Scenario: Numeric values render tabular
- **WHEN** a KPI value, transaction amount, or chart axis label is rendered
- **THEN** it inherits `font-variant-numeric: tabular-nums` via a `.value`, `.tl-amt`, or equivalent class

### Requirement: Spacing, radius, elevation, motion tokens

The frontend SHALL define spacing (`--space-1` through `--space-8`), radius (`--radius-sm/md/lg/xl/sheet/pill`), elevation (`--app-card-shadow`, `--app-raised-shadow`, `--app-inset-line`), and motion (`--ease-ios`, `--ease-standard`, `--dur-fast`, `--dur-base`) tokens per `colors_and_type.css`.

#### Scenario: Cards use card shadow + inset line
- **WHEN** a `.bento` or card surface renders
- **THEN** its computed `box-shadow` includes both `--app-card-shadow` and an `inset 0 1px 0 var(--app-inset-line)` top highlight
