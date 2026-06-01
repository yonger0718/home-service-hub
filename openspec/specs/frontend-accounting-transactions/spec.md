# frontend-accounting-transactions Specification

## Purpose
TBD - created by archiving change redesign-dashboard-handoff. Update Purpose after archive.

## Requirements
### Requirement: Month navigator + summary row

The accounting transactions screen SHALL render a month navigator (previous / current month label / next) at the top and a summary row showing the selected month's income, expense, and net.

#### Scenario: Navigator advances month
- **WHEN** the user clicks the next-month arrow
- **THEN** the displayed month advances by one and the summary row recomputes for that month

### Requirement: Type-pill filter and live search

The screen SHALL render a segmented `type-pills` filter with options 全部 / 支出 / 收入 / 信用卡 (or equivalent existing categories) and a live search input that filters the timeline by description text.

#### Scenario: Filter to expenses
- **WHEN** the user selects 支出
- **THEN** the timeline shows only expense rows

#### Scenario: Search filters live
- **WHEN** the user types in the search input
- **THEN** the timeline filters in real time as the user types

### Requirement: Date-grouped expense/income timeline

Transactions SHALL render in a `Timeline` grouped by date descending. Each row MUST show category icon, description, and amount. Cashflow colours follow accounting-analytics rules (not data-gainloss).

#### Scenario: Income row shows green
- **WHEN** an income row renders
- **THEN** its amount uses `var(--c-green)` regardless of `data-gainloss`

### Requirement: TxnDialog modal for adding a transaction

The 新增交易 action SHALL open a modal dialog (`TxnDialog`) containing a segmented control for type (支出 / 收入 / 信用卡) and form fields for amount, date, category, and notes. The dialog MUST validate required fields and call the existing accounting service on save.

#### Scenario: Dialog opens
- **WHEN** the user clicks 新增交易
- **THEN** `TxnDialog` opens with the type segmented control defaulted to 支出

#### Scenario: Save calls existing service
- **WHEN** the form is valid and the user clicks save
- **THEN** the existing accounting service is invoked with the form payload
- **AND** the dialog closes and the timeline refreshes
