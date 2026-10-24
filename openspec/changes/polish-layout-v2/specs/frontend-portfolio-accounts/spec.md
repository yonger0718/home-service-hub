## ADDED Requirements

### Requirement: Balance chart fills its card

The account balance chart SHALL fill its card's plot height, label the x-axis with `MM/DD` ticks (at most 8), draw no point markers, and take its line and fill colours from theme tokens.

#### Scenario: Three-month range
- **WHEN** the 3M range is selected on `/portfolio/accounts/1`
- **THEN** the chart SHALL show at most 8 horizontal `MM/DD` labels and the line SHALL span the plot height between the range's minimum and maximum

#### Scenario: Dark mode line
- **GIVEN** dark mode is on
- **WHEN** the chart renders
- **THEN** the line SHALL use the dark `--app-primary`

## MODIFIED Requirements

### Requirement: Accounts list page lives at `/portfolio/accounts`

The Angular app SHALL register a standalone route `/portfolio/accounts` rendering an accounts-list page. The page SHALL fetch `GET /api/portfolio/accounts?in_currency=TWD` on activation and display one card per account showing: broker label (Chinese for Cathay/SinoPac, English for foreign), nickname, currency code, `native_balance` through the uniform formatter (`frontend-design-system`), `target_balance` in TWD (when present), and a small `is_active` badge.

The page header SHALL render a TWD-total summary: sum of all `target_balance` values, with a footnote listing any `skipped_currencies` returned by the API.

A primary "新增帳戶" button SHALL open a modal containing the create-account form (POST `/api/portfolio/accounts`).

#### Scenario: List renders one card per active account
- **GIVEN** the API returns three active accounts
- **WHEN** the user navigates to `/portfolio/accounts`
- **THEN** three account cards SHALL render
- **AND** each card SHALL display broker, nickname, currency, and native balance

#### Scenario: TWD total reflects converted balances
- **GIVEN** the API returns an account with `target_balance=100000` and another with `target_balance=50000`
- **WHEN** the page renders
- **THEN** the header summary SHALL show `$150,000`

#### Scenario: Skipped currencies are surfaced
- **GIVEN** the API response contains `skipped_currencies=["JPY"]`
- **WHEN** the page renders
- **THEN** a footnote SHALL display `未換算: JPY` (or equivalent localized message)
