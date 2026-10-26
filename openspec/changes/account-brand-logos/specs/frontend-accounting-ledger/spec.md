## MODIFIED Requirements

### Requirement: Accounts list page

The SPA SHALL provide the route `/accounting/accounts`. It SHALL list every non-archived account returned by `GET /api/accounting/accounts`, grouped by account group in group order, each group with a collapsible header showing its subtotal in the main currency. Each row SHALL show:

- its brand logo when set, else its icon, and name
- a second line: currency when not the main currency, and for credit accounts the available credit (`credit_limit + balance`, shared across a shared-limit set) and, when rules exist, the names and rates of enabled rules
- balance, formatted in the account's currency with grouping separators: TWD and JPY rounded to whole units (half away from zero), other currencies with up to 2 decimals; rounding is display-only
- for accounts whose `combined_account_id` points at a 主帳戶, nesting under that master's row, which shows a count badge and the sum of its own and its children's balances

Negative balances SHALL be visually distinguished. A header SHALL show the total of accounts with `include_in_total` in the main currency, plus assets and liabilities (the sums of positive and negative balances). A collapsed 封存 section SHALL list archived accounts. A "+" in the page header SHALL open the new-account form.

The page SHALL show the latest import's timestamp, status and the number of entries needing review, taken from `GET /api/accounting/imports/latest`.

#### Scenario: Owner compares balances with MOZE
- **WHEN** the owner opens `/accounting/accounts` after an import
- **THEN** every account SHALL appear under its group with the balance computed by the backend, in its own currency

#### Scenario: No import yet
- **GIVEN** no import has run and no account exists
- **WHEN** the page loads
- **THEN** it SHALL show an empty state explaining that a MOZE backup import is required, with a link to the settings page

#### Scenario: Master account tree
- **GIVEN** three cards whose `combined_account_id` is `玉山信用卡`
- **WHEN** the page renders
- **THEN** `玉山信用卡` SHALL show a badge `3` and the three cards SHALL be indented beneath it

#### Scenario: Brand logo in the list
- **GIVEN** an account with `logo` `cathay-united` and one with none
- **THEN** the first row SHALL render the bundled image (`assets/logos/…`) and no emoji; the second SHALL render its emoji
- **AND** an unknown slug or a failed image load SHALL render the emoji

### Requirement: Account settings page

The SPA SHALL provide `/accounting/accounts/:id/settings` and `/accounting/accounts/new` as a settings-style list: the account's brand logo when set, else its icon, and live balance at the top; rows 名稱, 主幣種, 帳戶分組, 品牌 logo (a button showing the current choice that opens a grid of the bundled logo set with `無` first), 圖示, 初始金額, 帳單週期 (closing day with the computed current cycle), 信用帳戶 toggle revealing 繳款期限 (fixed day or days after closing, showing the computed due date), 信用額度, 額度共用 (multi-select of credit accounts), 主帳戶 (picker), 自動扣繳 (account picker); 紅利回饋 (read-only list of the account's rules in 2a); 國外交易手續費 toggle revealing %, rounding, refund flag; 納入總餘額; 封存帳戶; 刪除帳戶 (enabled only with no entries). 主幣種 SHALL be read-only once the account has entries. Saving calls the account write endpoints and validation errors SHALL be shown beside the field. An imported account whose settings were edited locally SHALL show a note "已自訂，匯入不再覆寫" with a 還原 MOZE 設定 action that clears the flag.

#### Scenario: Credit toggle reveals card fields
- **WHEN** 信用帳戶 is switched on
- **THEN** the 繳款期限, 信用額度, 額度共用, 主帳戶 and 自動扣繳 rows SHALL appear

#### Scenario: Due date preview
- **GIVEN** closing day 15 and 繳款期限 = 20 days after closing
- **WHEN** the row renders on 2026-10-02
- **THEN** it SHALL show the current cycle `09/16 – 10/15` and the due date `11/04`

#### Scenario: Pick a logo
- **WHEN** the owner opens the 品牌 logo grid and taps a logo
- **THEN** the grid SHALL close, the header SHALL show that logo's image, and saving SHALL send its slug as `logo`

#### Scenario: Clear the logo
- **GIVEN** an account with a logo and the emoji `💳`
- **WHEN** the owner taps `無` in the 品牌 logo grid and saves
- **THEN** `logo` SHALL be null, the header SHALL show the emoji, and `icon` SHALL be unchanged
