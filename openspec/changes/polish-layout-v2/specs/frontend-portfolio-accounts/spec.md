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

### Requirement: Master-detail layout on wide screens

At ≥ 1024px `/portfolio/accounts` and `/portfolio/accounts/:id` SHALL render as one page: a left column (at most 360px) with the 現金帳戶總額 · TWD card and one card per account, and a right column with the selected account's detail (header, balance chart, cash transactions). The selected card SHALL carry a `--app-primary` ring and `aria-pressed="true"`. `/portfolio/accounts` SHALL select the first active account. Below 1024px the existing two-page flow SHALL remain. Both URLs SHALL be one route (a URL matcher), so a selection change, including the first auto-selection (one `replaceUrl` navigation), SHALL not remount the page. The page SHALL NOT write the shell's pane state: its detail is always visible, so the sidebar keeps its stored width and the 記一筆 floating button stays at the page's bottom-right (polish-layout-v2 D4). An unknown `:id` SHALL select no card and show an empty detail: the placeholder header (讀取中 / 現金帳戶), the empty states and the two load-error toasts. The 現金帳戶總額 figure SHALL be the server `total_target_balance` minus the `target_balance` of every overdraft account (`native_balance < 0`), and the footnote SHALL count those accounts. The list SHALL keep the API default `include_inactive=false`, so 已停用 cards appear only when inactive accounts are requested. The list and the detail SHALL refresh each other after a cash-ledger change (a recorded cash flow, a created or deleted cash transaction, an account edit).

#### Scenario: Wide list selects the first account
- **WHEN** the owner opens `/portfolio/accounts` at 1440px
- **THEN** the first active account SHALL be selected and its detail shown on the right

#### Scenario: Account cards
- **WHEN** the left column renders
- **THEN** the total card SHALL use the accent background with a footnote naming accounts left out of the total
- **AND** each account card SHALL show icon, nickname, `broker · currency` and native balance, a negative balance in `--app-trend-negative`, and a 未記錄入金 tag when deposits are missing

#### Scenario: Narrow keeps two pages
- **WHEN** the owner taps an account at 390px
- **THEN** the router navigates to `/portfolio/accounts/:id` as today

#### Scenario: Sidebar keeps its width
- **GIVEN** the sidebar is expanded and auto-collapse is on
- **WHEN** the owner opens `/portfolio/accounts/1` at 1440 px
- **THEN** the sidebar SHALL stay expanded and the floating button SHALL sit at the default bottom-right offset
