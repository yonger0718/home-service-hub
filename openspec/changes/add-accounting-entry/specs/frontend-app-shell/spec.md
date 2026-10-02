## MODIFIED Requirements

### Requirement: Responsive shell — dock ≥760px, mobile nav <760px

The application SHALL render a frosted top header plus a fixed left dock (grouped Supplies / Portfolio / Accounting with sub-items) on viewports ≥ 760px, and a bottom tab bar plus segmented sub-nav on viewports < 760px. The breakpoint MUST be 760px exactly. The bottom tab bar SHALL have a raised centre "+" button between the second and third group tabs that navigates to `/accounting/entry` from any group; it is the primary action of the app and is visible on every route. The accounting group's sub-nav SHALL be 紀錄 (`/accounting`), 帳戶 (`/accounting/accounts`) and 設定 (`/accounting/settings`).

#### Scenario: Desktop renders dock
- **WHEN** the viewport width is ≥ 760px
- **THEN** the left dock is visible with three groups (Supplies, Portfolio, Accounting)
- **AND** the bottom mobile nav is hidden

#### Scenario: Mobile renders bottom nav
- **WHEN** the viewport width is < 760px
- **THEN** the bottom mobile nav is visible
- **AND** a segmented sub-nav appears for the current group
- **AND** the left dock is hidden

#### Scenario: Centre plus opens the entry form
- **WHEN** the viewport width is < 760px and the "+" in the tab bar is tapped from the Supplies group
- **THEN** the router navigates to `/accounting/entry`
