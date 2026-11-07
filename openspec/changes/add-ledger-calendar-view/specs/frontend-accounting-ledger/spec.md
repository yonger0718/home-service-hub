## ADDED Requirements

### Requirement: Calendar day selection in the URL

In the timeline's 日曆 view the selected day SHALL be the query parameter `day=YYYY-MM-DD` on `/accounting`, beside `month`. Without `day` nothing SHALL be selected, and opening 日曆 SHALL NOT select today by itself. A `day` outside `month` SHALL move `month` to the day's month. A URL with `day` SHALL open the 日曆 view for that visit without changing the stored view preference.

Picking a day SHALL set `day`. Picking the selected day again, 本月摘要 (≥ 1024 px) or 展開整月 (< 1024 px) SHALL remove it. ‹ and › beside the day label SHALL move one calendar day, crossing month edges and moving `month` with them. Moving the month with the navigator SHALL remove `day`. An entry opened from the calendar SHALL keep `month` and `day` in its URL, so that closing it or going back restores the selection.

#### Scenario: Shared link
- **WHEN** `/accounting?month=2026-10&day=2026-10-21` is opened
- **THEN** the 日曆 view SHALL show October with 10/21 selected and 10/21's rows listed

#### Scenario: Crossing a month edge
- **GIVEN** 2026-10-31 is selected
- **WHEN** › is pressed
- **THEN** the URL SHALL become `?month=2026-11&day=2026-11-01` and the grid SHALL show November

#### Scenario: Back from an entry
- **GIVEN** 10/21 is selected on a 390 px screen
- **WHEN** a row is opened and the owner goes back
- **THEN** the calendar SHALL show the week of 10/21 with 10/21 selected

#### Scenario: Month navigator clears the day
- **GIVEN** 10/21 is selected
- **WHEN** the month navigator moves to November
- **THEN** `day` SHALL be removed and no day SHALL be selected

### Requirement: Calendar day cells

Each day cell of the 日曆 view SHALL show:

- the date, with today on a primary-colour disc;
- the day's net from `GET /entries/summary/daily`: with thousands separators at ≥ 1024 px, in compact form below 1024 px (`−3.1k`, `+71.7k`, `−250`);
- up to three category dots for the day's largest expense categories, in the owner's category colours, followed by `+N` when there are more, read once per month from `GET /reports/breakdown?group_by=day&split_by=category&metric=expense`;
- a bar along the bottom edge whose length is the day's |expense| divided by the month's largest daily |expense|, with no bar on days without expense;
- the card mark on bill due days (帳單, or 已繳 once paid, at ≥ 1024 px; a muted dot below);
- a warning-ink marker when an inbox e-invoice awaiting a match has that day as its `issue_date` (發票待對帳 at ≥ 1024 px; a dot below).

Future days SHALL be dimmed and show no figure. The cell's accessible name SHALL include the date, the weekday, the row count, the day's expense and any bill or invoice mark; the bar and the dots SHALL be hidden from assistive technology. When the category breakdown cannot be read, the dots SHALL be left out and the rest of the cell SHALL render without an error message.

#### Scenario: Heaviest day
- **GIVEN** October's largest daily expense is 12,120 on 10/19 and 10/21's expense is 3,096
- **THEN** 10/19's bar SHALL span the full cell width and 10/21's about a quarter of it

#### Scenario: Invoice waiting on a day
- **GIVEN** the inbox holds an unmatched invoice from 路易莎 issued 2026-10-22
- **WHEN** October renders at 1440 px
- **THEN** 10/22's cell SHALL show 發票待對帳 and its accessible name SHALL end with 「發票待對帳」

#### Scenario: Breakdown unavailable
- **GIVEN** `GET /reports/breakdown` fails
- **WHEN** the 日曆 view renders
- **THEN** the cells SHALL show dates, nets, bars and marks without dots, and no error toast SHALL appear

### Requirement: Calendar layout on wide screens

From 1024 px, in the 日曆 view:

- **The list pane SHALL hold the calendar only.** No rows SHALL appear under the grid.
- **The month's 支出 / 收入 / 結餘 SHALL sit in the page header.**
- **The right pane SHALL follow the selection.**
  - **No day selected: 本月摘要.** In this order:
    1. a search field;
    2. a 今天 card, in the current month only, showing today's row count, names and net, which selects today;
    3. the category distribution, with a link to `/accounting/reports?month=…`;
    4. 花最多的商家: the five merchants with the largest expense, from `GET /reports/breakdown?group_by=merchant&metric=expense`, excluding rows without a merchant, each showing its count and amount;
    5. 這個月要處理: e-invoices awaiting a match and unpaid card bills, dated in the month; each row selects its day.
  - **A day selected: the day panel.** ‹ day label › with the count and net, a 本月摘要 button, the bill line for that day, and the day's rows with their time. A day without rows SHALL show 這天沒有記錄 (還沒到這天 for a future day) and 「＋ 在這天記一筆」, which opens the entry form with that date.
- **An entry opened from the day panel SHALL render as a drawer over the right pane.** The day panel stays mounted underneath.
  - ✕ or Esc SHALL close it back to the day.
  - ↑ and ↓ SHALL move to the previous or next entry of the month in list order, and the selected day SHALL follow.
- **The 清單 view SHALL keep its two-pane layout.**

#### Scenario: Opening the calendar on a desktop
- **WHEN** `/accounting?month=2026-10` opens at 1440 px in the 日曆 view
- **THEN** the left pane SHALL show the October grid alone and the right pane SHALL show 本月摘要 with 今天, the category distribution, 花最多的商家 and 這個月要處理

#### Scenario: Picking a day
- **WHEN** 10/21 is clicked
- **THEN** the right pane SHALL list 全聯 採買, 電費 and Netflix under 「10/21 週三 · 3 筆 · −$3,096」 and the cell SHALL be marked selected

#### Scenario: Entry drawer
- **GIVEN** 10/21 is selected
- **WHEN** 全聯 採買 is clicked
- **THEN** the detail SHALL cover the right pane as a drawer, the calendar SHALL stay visible, and the URL SHALL be `/accounting/entries/{id}?month=2026-10&day=2026-10-21`

#### Scenario: Stepping across days in the drawer
- **GIVEN** the drawer shows the last entry of 10/21
- **WHEN** ↓ is pressed
- **THEN** the drawer SHALL show the newest entry of the previous day with rows, and the calendar's selection SHALL move to that day

#### Scenario: Merchant ranking unavailable
- **GIVEN** the merchant breakdown fails
- **THEN** 本月摘要 SHALL show 「商家排行讀取失敗」 in place of 花最多的商家 and render its other parts

### Requirement: Calendar layout on narrow screens

Below 1024 px, in the 日曆 view, the month's 支出 / 收入 / 結餘 SHALL be one line under the month navigator.

- **No day selected.** The month grid SHALL be shown with category dots, followed by a 今天 bar showing today's row count, names and net, which selects today. In another month the bar SHALL read 回到今天 and move to today.
- **A day selected.** The grid SHALL collapse to the week containing the day, without dots, with a 展開整月 handle under it. The day header (‹ label ›, count and net) and the day's rows SHALL follow.
  - A horizontal swipe on the day area SHALL move one day.
  - A horizontal swipe on the week strip SHALL move one week.
- **Opening a row.** It SHALL open the entry detail as it does today: its own screen below 760 px, the right-hand sheet from 760 px.

#### Scenario: Phone default
- **WHEN** `/accounting` opens at 390 px in the 日曆 view in October 2026 on 10/22
- **THEN** the October grid and a bar 「今天 · 3 筆 … −$250」 SHALL be shown, no day SHALL be selected, and no rows SHALL be listed

#### Scenario: Picking a day on a phone
- **WHEN** 10/21 is tapped
- **THEN** the grid SHALL collapse to 10/18–10/24, and 10/21's three rows SHALL be listed under 「10/21 週三」

#### Scenario: Swiping to the next day
- **GIVEN** 10/21 is selected at 390 px
- **WHEN** the day area is swiped from right to left
- **THEN** 10/22 SHALL be selected and its rows listed

## MODIFIED Requirements

### Requirement: Empty pane shows the month

At ≥ 1280 px, when no entry or account is selected, the accounting right pane SHALL show the month overview: the category distribution with amounts and the next three due items (card bills and schedule instances). Below 1280 px the one-line empty state SHALL remain. While the overview is shown, the list pane's summary strip SHALL hide its category distribution (the 支出 / 收入 / 結餘 figures stay); below 1280 px the strip keeps the distribution. In the timeline's 日曆 view the requirement "Calendar layout on wide screens" governs the right pane instead, from 1024 px.

#### Scenario: Timeline without a selection
- **GIVEN** the timeline month is 2026-10
- **WHEN** `/accounting` opens at 1440 px with nothing selected
- **THEN** the right pane SHALL show October's category amounts and the next three due items

#### Scenario: Distribution shown once
- **GIVEN** `/accounting` at 1440 px with nothing selected
- **THEN** the category distribution SHALL render in the overview only, not in the list pane's summary strip

#### Scenario: Calendar view at 1100 px
- **WHEN** `/accounting` opens at 1100 px in the 日曆 view with no day selected
- **THEN** the right pane SHALL show 本月摘要 rather than the one-line empty state
