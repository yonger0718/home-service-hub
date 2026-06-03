## ADDED Requirements

### Requirement: Accounts list page lives at `/portfolio/accounts`

The Angular app SHALL register a standalone route `/portfolio/accounts` rendering an accounts-list page. The page SHALL fetch `GET /api/portfolio/accounts?in_currency=TWD` on activation and display one PrimeNG card per account showing: broker label (Chinese for Cathay/SinoPac, English for foreign), nickname, currency code, `native_balance` formatted with currency symbol, `target_balance` in TWD (when present), and a small `is_active` badge.

The page header SHALL render a TWD-total summary card: sum of all `target_balance` values, with a footnote listing any `skipped_currencies` returned by the API.

A primary "新增帳戶" button SHALL open a modal containing the create-account form (POST `/api/portfolio/accounts`).

#### Scenario: List renders one card per active account
- **GIVEN** the API returns three active accounts
- **WHEN** the user navigates to `/portfolio/accounts`
- **THEN** three account cards SHALL render
- **AND** each card SHALL display broker, nickname, currency, and native balance

#### Scenario: TWD total reflects converted balances
- **GIVEN** the API returns an account with `target_balance=100000` and another with `target_balance=50000`
- **WHEN** the page renders
- **THEN** the header summary SHALL show `NT$150,000`

#### Scenario: Skipped currencies are surfaced
- **GIVEN** the API response contains `skipped_currencies=["JPY"]`
- **WHEN** the page renders
- **THEN** a footnote SHALL display `未換算: JPY` (or equivalent localized message)

### Requirement: Account detail page lives at `/portfolio/accounts/:id`

Clicking an account card SHALL navigate to `/portfolio/accounts/:id` and render an account-detail page containing three sections in order:

1. **Header** — account metadata (broker, nickname, currency, opening balance, opening date), current balance, "編輯" button opening a PATCH-account modal.
2. **Balance-over-time chart** — ECharts line chart driven by `GET /api/portfolio/accounts/{id}/balance-history`, with a window selector (`1M`, `3M`, `1Y`, `All`, default `3M`).
3. **Cash transactions list** — paginated list using the existing `hub-modern-list` card layout, with filters (date range, type multi-select), sort dropdown (`txn_date desc` default), page-size selector (`25 / 50 / 100`, persisting to localStorage), and a "新增交易" button opening the manual-entry modal.

#### Scenario: Chart renders 3M default window
- **GIVEN** the user opens an account detail page
- **WHEN** the page loads
- **THEN** the chart SHALL fetch balance-history for the last 90 days
- **AND** display a step-line of balance points

#### Scenario: Switching window refetches and rerenders
- **WHEN** the user clicks `1Y`
- **THEN** the chart SHALL fetch the 365-day window and rerender without a full page reload

#### Scenario: Cash transaction list paginates and filters
- **GIVEN** the account has 200 cash rows
- **WHEN** the user sets type=`deposit` and page size 50
- **THEN** the list SHALL show up to 50 deposit rows and the paginator SHALL reflect the correct total

#### Scenario: Manual-entry modal posts to the correct endpoint
- **WHEN** the user submits the manual-entry form with `type=deposit, amount=5000, txn_date=2026-06-01, note="…"`
- **THEN** the page SHALL POST to `/api/portfolio/accounts/{id}/cash-transactions`
- **AND** on success refresh the cash list and balance chart

### Requirement: Nav entry "現金帳戶"

The Angular shell SHALL add a nav link labelled `現金帳戶` linking to `/portfolio/accounts`, positioned alongside the existing portfolio sub-page links (`交易紀錄`, `股息`, `已實現損益`). The link SHALL highlight when the route matches `/portfolio/accounts` or `/portfolio/accounts/:id`.

#### Scenario: Nav entry exists and is wired to the right route
- **WHEN** the user opens the app shell
- **THEN** the portfolio nav SHALL include `現金帳戶` as a sibling of the existing portfolio sub-links
- **AND** clicking it SHALL navigate to `/portfolio/accounts`

#### Scenario: Nav highlight tracks the detail route
- **GIVEN** the user is on `/portfolio/accounts/123`
- **WHEN** the page renders
- **THEN** the `現金帳戶` nav link SHALL appear active

### Requirement: Portfolio service exposes typed methods for accounts endpoints

The Angular `PortfolioService` SHALL expose typed methods backed by the new endpoints: `getAccounts(opts?: {in_currency?: string, include_inactive?: boolean})`, `createAccount(body: CreateBrokerAccount)`, `patchAccount(id, patch)`, `getCashTransactions(id, query)`, `createCashTransaction(id, body)`, `getBalanceHistory(id, {date_from, date_to})`, `refreshFxRates(opts?)`. Models SHALL include `BrokerAccount`, `CashTransaction`, `CashTransactionType` (enum matching backend), `BalancePoint`, `AccountSummary`, `FxFetchResult`.

#### Scenario: Service methods compose to the documented URLs
- **WHEN** `portfolioService.getCashTransactions(1, {date_from: '2026-01-01', limit: 50})` is called
- **THEN** the HTTP request SHALL be `GET /api/portfolio/accounts/1/cash-transactions?date_from=2026-01-01&limit=50`

#### Scenario: Type enum stays in sync with backend
- **WHEN** the backend adds a new type value (future change)
- **THEN** the Angular `CashTransactionType` enum SHALL fail compilation if the new value is consumed by the UI without being added — i.e. enum is exhaustive, no string-typing leak
