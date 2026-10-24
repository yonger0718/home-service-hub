## ADDED Requirements

### Requirement: Rows keep the page gutter

Entry rows on the timeline (list mode) and in the account entry history SHALL align with the page's content gutter on every width; a selected row's highlight MAY bleed only by `--row-sel-bleed`.

#### Scenario: Phone timeline
- **WHEN** the timeline renders in list mode at 390 px
- **THEN** each row's category icon SHALL start at the same left edge as the day heading above it

### Requirement: Empty pane shows the month

At ≥ 1280 px, when no entry or account is selected, the accounting right pane SHALL show the month overview: the category distribution with amounts and the next three due items (card bills and schedule instances). Below 1280 px the one-line empty state SHALL remain.

#### Scenario: Timeline without a selection
- **GIVEN** the timeline month is 2026-10
- **WHEN** `/accounting` opens at 1440 px with nothing selected
- **THEN** the right pane SHALL show October's category amounts and the next three due items

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

## MODIFIED Requirements

### Requirement: Timeline home page

The SPA SHALL provide `/accounting` as the accounting home, replacing the redirect to the accounts list. It SHALL show a month navigator with the month's expense, income and net totals from the summary endpoint, a category distribution bar under the totals (the month's expense split by top-level category: the five largest plus 其他, each segment in the category's colour, with a legend of name and amount), a filter bar (`<app-filter-bar>`: text search and the 帳戶 control inline; 類型, 類別, 專案, 標籤, 商家 and 金額範圍 behind 篩選 at every width, with active filters as removable chips), and entries from `GET /api/accounting/entries` grouped by day with a per-day net. Each row SHALL show category icon and colour, name or category, merchant and tags, amount (red for outflow, green for inflow per the preference), the original-currency amount inline when converted, and project and account pills. Reward entries SHALL be hidden when the preference `hide_rewards_on_timeline` is true. A split group SHALL render as one row with the group's name, the sum and a count badge. Tapping a row opens the entry detail. The page SHALL load more on scroll.

#### Scenario: Foreign-currency amount visible without hover
- **GIVEN** an expense converted from `¥5,390` at `0.2163`
- **WHEN** the timeline renders at 390 px
- **THEN** the row SHALL show `¥5,390` and the rate beneath the TWD amount

#### Scenario: Month totals
- **WHEN** the owner moves the navigator to 2026-09
- **THEN** the header SHALL show September's expense, income and net from the summary endpoint and the list SHALL show September's entries

#### Scenario: Category distribution bar
- **GIVEN** the month's expenses span eight top-level categories
- **WHEN** the summary renders
- **THEN** the bar SHALL show the five largest categories plus 其他 at their real spending amounts, and the legend SHALL reconcile to the month's expense: Σ segments − 退款 = |expense| (up to FX rounding), where 退款 is a legend-only line for categories whose refunds exceeded their spending this month (absent when there are none)

#### Scenario: Net-refund month
- **GIVEN** the month's refunds exceed its spending (the summary expense is ≥ 0)
- **WHEN** the summary renders
- **THEN** no distribution bar SHALL be shown

#### Scenario: Phone filters
- **WHEN** the timeline renders at 390 px with a 類別 and a 商家 filter active
- **THEN** the first entry row SHALL start within 200 px of the filter bar, the 篩選 button SHALL read `篩選 2` and two chips SHALL appear under the bar
