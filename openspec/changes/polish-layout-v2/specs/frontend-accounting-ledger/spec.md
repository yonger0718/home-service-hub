## ADDED Requirements

### Requirement: Rows keep the page gutter

Entry rows on the timeline (list mode) and in the account entry history SHALL align with the page's content gutter on every width; a selected row's highlight MAY bleed only by `--row-sel-bleed`.

#### Scenario: Phone timeline
- **WHEN** the timeline renders in list mode at 390 px
- **THEN** each row's category icon SHALL start at the same left edge as the day heading above it

### Requirement: Empty pane shows the month

At ≥ 1280 px, when no entry or account is selected, the accounting right pane SHALL show the month overview: the category distribution with amounts and the next three due items (card bills and schedule instances). Below 1280 px the one-line empty state SHALL remain.

#### Scenario: Timeline without a selection
- **WHEN** `/accounting` opens at 1440 px with nothing selected
- **THEN** the right pane SHALL show the October category amounts and the next three due items

### Requirement: Accounts list marks the selection

On `/accounting/accounts` at ≥ 1024 px the selected account SHALL carry the indigo selection ring and `aria-current="true"`, and the empty pane copy SHALL read 選擇帳戶查看明細，或按 ＋ 新增.

#### Scenario: Account opened
- **WHEN** the owner opens 玉山銀行
- **THEN** its card in the left list SHALL show the selection ring

### Requirement: Page title on the page background

Accounting pages SHALL render their `<h1>` and page actions on the page background above the list card, as portfolio and settings pages do.

#### Scenario: Timeline header
- **WHEN** `/accounting` renders at 1440 px
- **THEN** 記帳記錄 SHALL sit above the list card, not inside it
