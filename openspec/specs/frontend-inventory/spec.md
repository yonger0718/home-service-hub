# frontend-inventory Specification

## Purpose
TBD - created by archiving change redesign-dashboard-handoff. Update Purpose after archive.

## Requirements
### Requirement: Card grid layout

The inventory screen SHALL render items in a responsive card grid where each card shows item name, current quantity, low-stock threshold, and a stock-status badge.

#### Scenario: Grid reflows on narrow viewport
- **WHEN** the viewport is below 760px
- **THEN** the grid reflows to a single column

### Requirement: Stock-status badges use muted non-trend tokens

Status badges SHALL use `--app-success`, `--app-warning`, `--app-danger` (muted slate / violet) — NOT trend red/green.

#### Scenario: Low-stock badge uses warning slate
- **WHEN** an item is below its low-stock threshold but not zero
- **THEN** the badge background is `var(--app-state-warning-bg)` and text is `var(--app-warning)`

### Requirement: Quantity steppers update state live

Each card SHALL include working +/− buttons that increment / decrement the item quantity. The card's stock-status badge MUST recompute (低庫存 ↔ 正常) without a server round-trip on each click; persistence to the backend follows the existing inventory service contract.

#### Scenario: Decrement to below threshold
- **WHEN** the user clicks − until quantity drops below the low-stock threshold
- **THEN** the badge immediately switches to 低庫存 state

#### Scenario: Cannot decrement below zero
- **WHEN** the quantity is 0 and the user clicks −
- **THEN** the quantity stays 0 and no service call is made

### Requirement: 低庫存 filter pill

The screen SHALL include a 只看低庫存 filter pill. When active, the grid shows only items currently at or below their low-stock threshold.

#### Scenario: Filter active hides healthy stock
- **WHEN** the 只看低庫存 pill is active
- **THEN** only cards with low-stock badges are visible
