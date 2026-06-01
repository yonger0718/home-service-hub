# frontend-dividend-records Specification

## Purpose
TBD - created by archiving change redesign-dashboard-handoff. Update Purpose after archive.

## Requirements
### Requirement: Summary row

The dividends screen SHALL render a summary row with three figures: 本年度累計股利 (rendered in violet `var(--app-dividend)`), 平均殖利率, and 領取筆數.

#### Scenario: Cumulative dividend uses violet
- **WHEN** the summary renders
- **THEN** the 本年度累計股利 value uses `var(--app-dividend)`

### Requirement: Upcoming ex-dividend grid

The screen SHALL render an 即將除權息提醒 grid of upcoming ex-dividend cards. Each card MUST show stock name + code, ex-dividend date, and projected per-share amount.

#### Scenario: Upcoming card renders projected amount
- **WHEN** an upcoming ex-dividend event has a projected per-share amount
- **THEN** the card displays the amount alongside name + date

### Requirement: Violet dividend timeline

Dividend records SHALL render in a `Timeline` using `SideTag` variant `cash` (violet) and `.tl-amt.dividend` (violet) amount. Each row MUST show stock name + code, meta text `per-share × qty`, and the total amount.

#### Scenario: Dividend row uses violet
- **WHEN** a dividend row renders
- **THEN** the `SideTag` is the violet `cash` variant and the amount is rendered in `var(--app-dividend)`
