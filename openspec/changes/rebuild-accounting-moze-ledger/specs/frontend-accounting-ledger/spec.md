## ADDED Requirements

### Requirement: Accounts list page

The SPA SHALL provide the route `/accounting/accounts` as the default accounting page. It SHALL list every account returned by `GET /api/accounting/accounts`, showing:

- name
- currency
- balance, formatted in the account's currency with grouping separators: TWD and JPY rounded to whole units (half away from zero), other currencies with up to 2 decimals; rounding is display-only
- entry count

Negative balances SHALL be visually distinguished.

The page SHALL show the latest import's timestamp, status and the number of entries needing review, taken from `GET /api/accounting/imports/latest`.

#### Scenario: Owner compares balances with MOZE
- **WHEN** the owner opens `/accounting/accounts` after an import
- **THEN** every account SHALL appear with the balance computed by the backend, in its own currency

#### Scenario: No import yet
- **GIVEN** no import has run
- **WHEN** the page loads
- **THEN** it SHALL show an empty state explaining that a MOZE CSV import is required

### Requirement: Account entry history page

The SPA SHALL provide the route `/accounting/accounts/:id`. It SHALL list the account's entries newest first, showing for each entry:

- date and time
- kind
- category path
- name and merchant
- project
- amount
- running balance

It SHALL load more entries on demand using the paginated endpoint, SHALL filter by kind and date range, and SHALL mark entries with `needs_review = true`.

#### Scenario: Unpaired transfer is flagged
- **GIVEN** an account has a transfer leg with `needs_review = true`
- **WHEN** its history is shown
- **THEN** that row SHALL carry a visible review marker

### Requirement: Mobile-usable layout

Both pages SHALL be usable at a 390 px viewport width without horizontal page scrolling. On narrow screens, the entry history SHALL collapse to a two-line row: date and amount on the first line, category and name on the second.

#### Scenario: Viewing on iPhone
- **WHEN** the accounts page is opened on a 390 px wide viewport
- **THEN** no horizontal page scroll SHALL occur and every balance SHALL be readable
