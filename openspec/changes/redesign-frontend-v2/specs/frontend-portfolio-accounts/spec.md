## ADDED Requirements

### Requirement: Master-detail layout on wide screens

At ≥ 1024px `/portfolio/accounts` and `/portfolio/accounts/:id` SHALL render as one page: a left column (at most 360px) with the 現金帳戶總額 · TWD card and one card per account, and a right column with the selected account's detail (header, balance chart, cash transactions). The selected card SHALL carry a `--app-primary` ring and `aria-pressed="true"`. `/portfolio/accounts` SHALL select the first active account. Below 1024px the existing two-page flow SHALL remain. Both URLs SHALL be one route (a URL matcher), so a selection change, including the first auto-selection (one `replaceUrl` navigation), SHALL not remount the page. While the master-detail shows a selection the sidebar SHALL collapse to its rail. An unknown `:id` SHALL select no card and show an empty detail. The 現金帳戶總額 figure SHALL be the server `total_target_balance` minus the `target_balance` of every overdraft account (`native_balance < 0`), and the footnote SHALL count those accounts. The list SHALL keep the API default `include_inactive=false`, so 已停用 cards appear only when inactive accounts are requested. The list and the detail SHALL refresh each other after a cash-ledger change (a recorded cash flow, a created or deleted cash transaction, an account edit).

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

### Requirement: Running balance in cash transactions

The cash transactions list SHALL show, per row: date, a type icon with a subtitle, a source tag (手動, 交易, 股利, 匯入) and the signed amount. When the current page's rows form a contiguous range ending at the current balance, a 餘額 column SHALL show the balance after each row; otherwise the column SHALL be omitted. The header row SHALL keep the merge toggle (合併同筆交易), filters and 新增交易. The subtitles SHALL come from the response-only fields `related_symbol`, `related_quantity`, `related_trade_date`, `related_per_share`, `related_fee` and `related_tax` that `GET /api/portfolio/accounts/{id}/cash-transactions` adds, filled from the linked transaction or, for `dividend_cash` rows, the linked dividend; `related_trade_date` SHALL be the Asia/Taipei calendar date of the trade. The fields SHALL be null for unlinked rows (manual, CSV) and when the linked row no longer exists; such rows, and a dividend row without a per-share figure, SHALL show the row's note. The 餘額 anchor SHALL be the displayed `native_balance` (for phase-4 brokers the cash-flow index, which MAY differ from the ledger behind 餘額走勢). Besides a gap, the column SHALL be omitted on a page other than the first, under another sort, with a date or type filter, while the page holds a row dated after today, and while it holds rows of another account. 合併同筆交易 SHALL be a switch (`role="switch"`). Amounts and balances SHALL be parsed from the API's decimal strings and formatted to the currency's digits (0 for TWD / JPY, else 2), never echoed.

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
