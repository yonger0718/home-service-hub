# frontend-app-shell Specification

## Purpose
TBD - created by archiving change redesign-dashboard-handoff. Update Purpose after archive.

## Requirements
### Requirement: Responsive shell — dock ≥760px, mobile nav <760px

The application SHALL render a frosted top header plus a fixed left dock (grouped Supplies / Portfolio / Accounting with sub-items) on viewports ≥ 760px, and a bottom tab bar plus segmented sub-nav on viewports < 760px. The breakpoint MUST be 760px exactly.

#### Scenario: Desktop renders dock
- **WHEN** the viewport width is ≥ 760px
- **THEN** the left dock is visible with three groups (Supplies, Portfolio, Accounting)
- **AND** the bottom mobile nav is hidden

#### Scenario: Mobile renders bottom nav
- **WHEN** the viewport width is < 760px
- **THEN** the bottom mobile nav is visible
- **AND** a segmented sub-nav appears for the current group
- **AND** the left dock is hidden

### Requirement: Frosted top header

The application SHALL render a top header using `--app-surface-glass` background plus `backdrop-filter: blur(...)`. The header MUST contain the HH logo lockup and is fixed across all routes.

#### Scenario: Header is translucent
- **WHEN** the page scrolls content beneath the header
- **THEN** the header retains a frosted blur effect over the scrolled content

### Requirement: Active route highlighted in indigo

The shell SHALL highlight the active dock item and active mobile tab using `var(--app-primary)` (indigo). Active state is driven by the current Angular route id. Route ids MUST match the handoff `NAV` array (`portfolio`, `transactions`, `dividends`, `import`, `accounting-dash`, `accounting`, `settings`, `inventory`, `shopping`).

#### Scenario: Navigating updates active highlight
- **WHEN** the user navigates from `/` (inventory) to `/portfolio`
- **THEN** the Portfolio dock item gains the `.dock-item.active` indigo highlight
- **AND** the Supplies (inventory) item loses its highlight

### Requirement: Out-of-handoff routes remain reachable

The shell SHALL keep `/portfolio/realized-pnl`, `/accounting/settings`, `/accounting/cards`, `/accounting/categories`, `/accounting/recurring` reachable via their respective group sub-items. Their layouts are NOT redesigned; they consume tokens through inheritance only.

#### Scenario: Realized PnL still navigable from Portfolio group
- **WHEN** the user expands the Portfolio dock group
- **THEN** a sub-item links to `/portfolio/realized-pnl`
