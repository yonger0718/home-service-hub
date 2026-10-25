# frontend-portfolio-accounts Specification

## Purpose
TBD - created by archiving change add-broker-cash-accounts. Update Purpose after archive.

## Requirements

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

### Requirement: Account detail page lives at `/portfolio/accounts/:id`

Clicking an account card SHALL navigate to `/portfolio/accounts/:id` and render an account-detail page containing three sections in order:

1. **Header** — account metadata (broker, nickname, currency, opening balance, opening date), current balance, "編輯" button opening a PATCH-account modal.
2. **Balance-over-time chart** — ECharts line chart driven by `GET /api/portfolio/accounts/{id}/balance-history`, with a window selector (`1M`, `3M`, `1Y`, `All`, default `3M`).
3. **Cash transactions list** — paginated list using the existing `hub-modern-list` card layout, with an `<app-filter-bar>` (the 類型 multi-select inline, no search; date range and sort (`txn_date desc` default) behind 篩選, the active date range as a removable chip), page-size selector (`25 / 50 / 100`, persisting to localStorage), and a "新增交易" button opening the manual-entry modal.

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

#### Scenario: Date range behind 篩選
- **WHEN** the user sets a date range behind 篩選
- **THEN** the list SHALL reload with `date_from` / `date_to`, the 篩選 button SHALL read `篩選 1` and a removable chip SHALL show the range

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

### Requirement: Delete control on manual cash rows

The account detail cash transactions list SHALL render a trash-icon button on every row whose `source === 'manual'`. The button SHALL be absent (not disabled, not present) on rows with any other `source` value.

Clicking the trash icon SHALL open a confirmation dialog with body text `{type label} {amount with sign} {currency} on {txn_date}{note ? " — " + note : ""}` and buttons `刪除` (severity danger) + `取消`.

Confirming the dialog SHALL call `DELETE /api/portfolio/accounts/{id}/cash-transactions/{txn_id}`. On HTTP 200, the page SHALL refetch the cash transactions list, balance history (current window), and the parent account summary in parallel. On any non-2xx response, a toast SHALL surface the error and the row SHALL remain in the list.

#### Scenario: Manual row shows trash icon

- **GIVEN** a row with `source=manual` in the rendered list
- **THEN** a trash icon button is visible on the row

#### Scenario: Non-manual row hides trash icon

- **GIVEN** a row with `source=auto_derive` or `source=csv_import` in the rendered list
- **THEN** no trash icon is rendered on the row

#### Scenario: Confirmation dialog shows row context

- **GIVEN** a manual deposit row with `amount=+10000`, `currency=TWD`, `txn_date=2026-06-03`, `note="testing"`
- **WHEN** the user clicks the trash icon
- **THEN** a confirmation dialog opens
- **AND** the dialog body contains `入金 +10,000 TWD on 2026-06-03 — testing`

#### Scenario: Confirm fires DELETE and refreshes

- **WHEN** the user clicks `刪除` in the confirmation dialog
- **THEN** the page fires `DELETE /api/portfolio/accounts/1/cash-transactions/42`
- **AND** on 200, the page re-fires the cash transactions list query, balance history query, and account summary query
- **AND** the deleted row no longer appears in the list

#### Scenario: Cancel closes dialog without DELETE

- **WHEN** the user clicks `取消` in the confirmation dialog
- **THEN** no DELETE request is fired
- **AND** the row remains in the list

#### Scenario: Server error surfaces toast

- **GIVEN** the backend returns 403 or 500 to the DELETE call
- **THEN** a toast appears with severity `error` and message including `刪除失敗`
- **AND** the row remains in the list

### Requirement: Merge toggle on account detail page

The account detail page SHALL provide a toggle control labeled `合併同筆交易` next to the cash transactions section header. When ON, the page sends `merge_related=true` to the cash-transactions endpoint and renders synthetic group rows with an expand/collapse chevron that reveals the original legs inline (no additional API call). When OFF, the page renders individual leg rows as before.

Toggle state SHALL persist per account in `localStorage` under key `accounts.merge.<account_id>`. Default state on first visit is OFF.

#### Scenario: Toggle defaults OFF for new account

- **GIVEN** the user visits `/portfolio/accounts/1` for the first time (no localStorage key)
- **WHEN** the cash transactions list loads
- **THEN** the toggle shows OFF
- **AND** the request to the cash-transactions endpoint omits `merge_related` (or sends `false`)
- **AND** individual leg rows are rendered

#### Scenario: Toggle ON groups BUY/SELL legs

- **GIVEN** a BUY transaction with 3 cash legs in the current page
- **WHEN** the user flips the toggle to ON
- **THEN** the request re-fires with `merge_related=true`
- **AND** the BUY row renders as ONE list item showing summed amount + leg-count badge + chevron control
- **AND** clicking the chevron expands the row inline to show the 3 legs (settle / fee / tax) with their individual amounts
- **AND** localStorage `accounts.merge.1` = `"1"`

#### Scenario: Toggle state persists across navigations

- **GIVEN** the user toggled merge ON for account 1 earlier in the session
- **WHEN** the user navigates away and returns to `/portfolio/accounts/1`
- **THEN** the toggle initializes ON
- **AND** the first fetch sends `merge_related=true`

#### Scenario: Toggle state independent per account

- **GIVEN** localStorage `accounts.merge.1` = `"1"` and no key for account 2
- **WHEN** the user opens `/portfolio/accounts/2`
- **THEN** the toggle for account 2 initializes OFF
- **AND** the fetch for account 2 omits `merge_related`

#### Scenario: Paginator resets to page 1 on toggle change

- **GIVEN** the user is on page 3 of an unmerged list
- **WHEN** they flip the toggle to ON
- **THEN** the paginator resets `offset` to 0 and the request re-fires with `merge_related=true&offset=0`

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

### Requirement: Running balance in cash transactions

The cash transactions list SHALL show, per row: date, a type icon with a subtitle, a source tag (手動, 交易, 股利, 匯入) and the signed amount. When the current page's rows form a contiguous range ending at the current balance, a 餘額 column SHALL show the balance after each row; otherwise the column SHALL be omitted. The header row SHALL keep the merge toggle (合併同筆交易), filters and 新增交易. The subtitles SHALL come from the response-only fields `related_symbol`, `related_quantity`, `related_trade_date`, `related_per_share`, `related_fee` and `related_tax` that `GET /api/portfolio/accounts/{id}/cash-transactions` adds, filled from the linked transaction or, for `dividend_cash` rows, the linked dividend; `related_trade_date` SHALL be the Asia/Taipei calendar date of the trade. The fields SHALL be null for rows without a linked transaction or dividend (manual entries, CSV broker cash flows, interest, fx; CSV-imported trades are linked and do carry them) and when the linked row no longer exists; such rows, and a dividend row without a per-share figure, SHALL show the row's note. The 餘額 anchor SHALL be the displayed `native_balance` (for phase-4 brokers the cash-flow index, which MAY differ from the ledger behind 餘額走勢). Besides a gap, the column SHALL be omitted on a page other than the first, under another sort, with a date or type filter, while the page holds a row dated after today, and while it holds rows of another account. 合併同筆交易 SHALL be a switch (`role="switch"`). Amounts and balances SHALL be parsed from the API's decimal strings and formatted to the currency's digits (0 for TWD / JPY, else 2), never echoed.

#### Scenario: Row subtitles
- **WHEN** a settlement row and a dividend row render
- **THEN** the settlement (交割 · 買進 / 交割 · 賣出) SHALL show shares and trade date beneath its type
- **AND** the dividend SHALL show per-share × quantity and fees beneath its type

#### Scenario: Balance column omitted for a gap
- **GIVEN** the filters leave a non-contiguous set of rows
- **WHEN** the list renders
- **THEN** no 餘額 column SHALL be shown

#### Scenario: Balance after each row
- **GIVEN** the current balance is 438,360 and the newest row is a deposit of +20,000
- **WHEN** the list renders the first page sorted newest first
- **THEN** the first row SHALL show 438,360 and the second row 418,360

### Requirement: Balance chart fills its card

The account balance chart SHALL fill its card's plot height, label the x-axis with `MM/DD` ticks (at most 8), draw no point markers, and take its line and fill colours from theme tokens.

#### Scenario: Three-month range
- **WHEN** the 3M range is selected on `/portfolio/accounts/1`
- **THEN** the chart SHALL show at most 8 horizontal `MM/DD` labels and the line SHALL span the plot height between the range's minimum and maximum

#### Scenario: Dark mode line
- **GIVEN** dark mode is on
- **WHEN** the chart renders
- **THEN** the line SHALL use the dark `--app-primary`
