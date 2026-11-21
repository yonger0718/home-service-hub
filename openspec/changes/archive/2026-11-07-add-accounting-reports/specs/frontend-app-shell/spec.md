## MODIFIED Requirements

### Requirement: Responsive shell — dock ≥760px, mobile nav <760px

The application SHALL render a labelled left sidebar on viewports ≥ 760px and a bottom tab bar plus in-page sub-nav on viewports < 760px. The breakpoint MUST be 760px exactly. The sidebar SHALL be built from `NAV_GROUPS` and SHALL have two widths: **expanded** (248px: logo lockup, collapse button, group headings, icon + label per item) and **rail** (72px: logo icon, expand button, a divider per group, icon per item with a tooltip on hover and focus). At ≥ 1280px the sidebar SHALL use the owner's stored default (expanded unless set to rail); from 760px to 1279px it SHALL always be the rail. The bottom tab bar SHALL have a raised centre "+" that navigates to `/accounting/entry` from any route; it is the primary action of the app on phones. The accounting group's sub-nav SHALL be 記錄 (`/accounting`), 帳戶 (`/accounting/accounts`), 報表 (`/accounting/reports`) and 記帳設定 (`/accounting/settings`); the portfolio group's sub-nav SHALL be horizontally scrolling chips 概覽 / 交易 / 股利 / 已實現 / 現金 / 匯入.

#### Scenario: Desktop renders dock
- **WHEN** the viewport width is 1440px and no sidebar default is stored
- **THEN** the sidebar SHALL be 248px wide with group headings and item labels
- **AND** the bottom tab bar SHALL be hidden

#### Scenario: Narrow desktop renders the rail
- **WHEN** the viewport width is 1100px
- **THEN** the sidebar SHALL be the 72px rail
- **AND** focusing an item SHALL show its label in a tooltip

#### Scenario: Mobile renders bottom nav
- **WHEN** the viewport width is < 760px
- **THEN** the bottom tab bar SHALL be visible with "+" in the centre
- **AND** the current group's sub-nav SHALL appear at the top of the page
- **AND** the sidebar SHALL be hidden

#### Scenario: Centre plus opens the entry form
- **WHEN** the viewport width is < 760px and the "+" in the tab bar is tapped from the 投資 group
- **THEN** the router navigates to `/accounting/entry`

#### Scenario: Reports in the accounting sub-nav
- **WHEN** `/accounting/reports` renders at 390px
- **THEN** the sub-nav SHALL show 記錄, 帳戶, 報表 and 記帳設定 with 報表 active
