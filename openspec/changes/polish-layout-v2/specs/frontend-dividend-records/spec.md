## MODIFIED Requirements

### Requirement: Summary row

The dividends screen SHALL render summary cards using the `CONTEXT.md` vocabulary: 已確認收款 (count; footnote 已寫入現金帳), 有效待收 (count; value in `--app-primary`; footnote 納入預估含息損益), 舊資料帳面 (count; footnote 收款未核實; swatch drawn dashed), and 已記錄股利 (value in `var(--app-dividend)`; footnote 已確認 ＋ 舊資料). When 舊資料帳面 is 0 its card SHALL be omitted and three cards render. 已記錄股利 MUST equal 已確認收款 + 舊資料帳面 and MUST NOT include 有效待收. At < 760px only 已確認收款 and 有效待收 SHALL be shown. A note SHALL explain that pending dividends count toward 預估含息損益 but not toward available cash, that amounts under review are excluded, and that legacy receipts are unverified. The figures SHALL be TWD totals computed client-side over every row of the active filter, each dividend converted by its own `fx_rate_to_twd`, with the same 100-row paging, 50-request cap and notes (`⋯ 僅載入前 5,000 筆`, `不含外幣 n 筆`) as the transactions cards. 有效待收 SHALL be the sum of rows with `receipt_status = pending` (owner decision) and MAY differ from the dashboard's server figure `total_pending_dividends_net`. The 即將除權息提醒 strip SHALL stay on the page, below the note, while it has at least one event.

#### Scenario: Recorded dividends exclude pending
- **GIVEN** confirmed 37,578, legacy 21,342 and pending 14,600
- **WHEN** the summary renders
- **THEN** 已記錄股利 SHALL show 58,920

#### Scenario: No legacy receipts
- **GIVEN** no legacy dividend rows
- **WHEN** the summary renders at 1440 px
- **THEN** three summary cards SHALL render (已確認收款, 有效待收, 已記錄股利)

#### Scenario: Cumulative dividend uses violet
- **WHEN** the summary renders
- **THEN** the 已記錄股利 value uses `var(--app-dividend)`

### Requirement: Upcoming ex-dividend grid

When at least one upcoming ex-dividend event exists the screen SHALL render an 即將除權息提醒 grid of upcoming ex-dividend cards; each card MUST show stock name + code, ex-dividend date, and projected per-share amount. When the list is empty no 即將除權息提醒 row SHALL render.

#### Scenario: Upcoming card renders projected amount
- **WHEN** an upcoming ex-dividend event has a projected per-share amount
- **THEN** the card displays the amount alongside name + date

#### Scenario: No upcoming events
- **WHEN** the upcoming ex-dividend list is empty
- **THEN** no 即將除權息提醒 row SHALL render
