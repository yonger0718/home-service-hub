## MODIFIED Requirements

### Requirement: Active route highlighted in indigo

The shell SHALL highlight the active dock item and active mobile tab using `var(--app-primary)` (indigo). Active state is driven by the current Angular route id. Route ids MUST match the handoff `NAV` array (`portfolio`, `transactions`, `dividends`, `import`, `accounting`, `settings`, `inventory`, `shopping`). The `accounting` id SHALL cover `/accounting/accounts` and `/accounting/accounts/:id`.

#### Scenario: Navigating updates active highlight
- **WHEN** the user navigates from `/` (inventory) to `/portfolio`
- **THEN** the Portfolio dock item gains the `.dock-item.active` indigo highlight
- **AND** the Supplies (inventory) item loses its highlight

#### Scenario: Account history keeps Accounting highlighted
- **WHEN** the user navigates to `/accounting/accounts/5`
- **THEN** the Accounting dock item SHALL be highlighted

### Requirement: Out-of-handoff routes remain reachable

The shell SHALL keep `/portfolio/realized-pnl` reachable via its group sub-item. Its layout is NOT redesigned; it consumes tokens through inheritance only. The former accounting sub-routes (`/accounting/dashboard`, `/accounting/transactions`, `/accounting/settings`, `/accounting/cards`, `/accounting/categories`, `/accounting/recurring`) are removed by `rebuild-accounting-moze-ledger`; navigating to any of them SHALL redirect to `/accounting/accounts`.

#### Scenario: Realized PnL still navigable from Portfolio group
- **WHEN** the user expands the Portfolio dock group
- **THEN** a sub-item links to `/portfolio/realized-pnl`

#### Scenario: Old accounting links redirect
- **WHEN** the user opens a bookmarked `/accounting/cards`
- **THEN** the SPA SHALL redirect to `/accounting/accounts`
