## ADDED Requirements

### Requirement: Notification page and entry points

The SPA SHALL provide `/notifications`. The sidebar SHALL show a 通知 item above the navigation groups in both the expanded sidebar and the rail; hiding groups SHALL never hide it. Below 760 px every page header SHALL show a bell icon button linking to `/notifications`. The accounting timeline SHALL link to `/notifications` instead of showing its own reminder count.

#### Scenario: Sidebar entry
- **WHEN** the app renders at 1440 px
- **THEN** a 通知 item SHALL appear above the 投資 group and link to `/notifications`

#### Scenario: Phone header entry
- **WHEN** `/portfolio/dividends` renders at 390 px
- **THEN** the header SHALL show a bell button labelled with the current count, before the gear

### Requirement: Tasks grouped by urgency

The page SHALL list tasks in the sections 已逾期, 今天, 要你確認 and 系統, in that order, oldest first within a section, followed by a folded 追蹤中 section and a link to items handled in the last 7 days. One task SHALL stand for one card bill, one schedule definition (with its count and sum of open instances), one statement to reconcile, one batch of dividends past their payment date, one MOZE import to review, or one system failure. Chips 全部 / 財務 / 投資 SHALL filter by source and show their counts.

#### Scenario: Overdue schedule instances form one task
- **GIVEN** Netflix has instances due 8/20, 9/20 and 10/20, all unposted, today 10/24
- **WHEN** the page renders
- **THEN** 已逾期 SHALL show one row 「Netflix · 3 期未入帳」 with the sum and the primary action 全部入帳

#### Scenario: Statement to reconcile
- **GIVEN** a statement with 10 inbox items that need an answer
- **WHEN** the page renders
- **THEN** 要你確認 SHALL show one row for that statement with 「10 筆要你決定」 and a progress bar of matched lines

#### Scenario: Source filter
- **WHEN** the owner picks 投資
- **THEN** only dividend, import and 國泰 daily statement tasks SHALL remain

### Requirement: Notification count

The count SHALL be the number of tasks in 已逾期, 今天, 要你確認 and 系統 that are not snoozed; 追蹤中 SHALL never count. It SHALL render on the 通知 entry points only, in `--c-red-ink` when any counted task is 已逾期 or 系統 and in `--app-primary` otherwise, with the label `通知，N 件要處理` plus `，M 件已逾期` when M > 0. A source that fails to load SHALL contribute nothing and SHALL be named in a muted line at the bottom of the page.

#### Scenario: Count on review data
- **GIVEN** 3 overdue tasks, 1 due today, 2 to confirm and 1 system failure, and 3 debts being tracked
- **WHEN** the sidebar renders
- **THEN** 通知 SHALL show `7` in the red tone

#### Scenario: Failed source
- **WHEN** the inbox request fails
- **THEN** the count SHALL leave reconciliation out and the page SHALL show 「無法讀取：對帳」

#### Scenario: Badges off
- **GIVEN** badges are turned off in Settings
- **THEN** no count SHALL render, and the page SHALL still list every task

### Requirement: Snooze on this device

Each counted task SHALL offer 稍後提醒 with 3 小時後, 明天 09:00 and 下週一 09:00. A snoozed task SHALL leave the list and the count until the time passes or its amount, count or due date changes. Snoozes SHALL be stored in `localStorage` key `hh-notif-snooze` and never sent to the server.

#### Scenario: Snooze until tomorrow
- **WHEN** the owner snoozes the 房租 task until 明天 09:00
- **THEN** the task SHALL disappear and the count SHALL drop by one until 09:00 the next day

#### Scenario: New instance wakes a snoozed task
- **GIVEN** the Netflix task is snoozed with 3 instances
- **WHEN** a fourth instance becomes due
- **THEN** the task SHALL reappear with 4 instances

### Requirement: One case at a time reconciliation

`/notifications/recon/:statementId` SHALL show a statement's open questions one at a time: a label and headline, a 日期 / 商家 / 金額 comparison of bank line and ledger entry with differing fields highlighted, the answers as full-width buttons (first one primary), 以後都這樣 as a switch where learning applies, 略過，稍後再看 and progress `n / total`. It SHALL open in the right pane of `/notifications` at ≥ 1024 px and full screen below.

#### Scenario: Keyboard
- **WHEN** the flow is open at 1440 px and no text field has focus
- **THEN** keys 1–3 SHALL pick an answer, S SHALL skip, ← and → SHALL move without answering and Esc SHALL close

#### Scenario: Answering moves on
- **WHEN** the owner presses 1 on the first question
- **THEN** the answer SHALL be applied through `POST /reconciliation/inbox/apply` and the second question SHALL show with progress `2 / 10`

#### Scenario: Stop midway
- **WHEN** the owner closes after answering 4 of 10
- **THEN** the statement's task SHALL remain with 「6 筆要你決定」

#### Scenario: End state
- **WHEN** the last open question is answered or skipped
- **THEN** the screen SHALL show how many were handled and how many remain, with 繼續 and 回到通知

### Requirement: Related questions handled together

When two open questions of a statement reference the same ledger entry, the flow SHALL show them once, with a note naming the other question, and SHALL apply the chosen answer with both cases in one request.

#### Scenario: Amount difference and missing bank line on one entry
- **GIVEN** an `amount_differs` question and a `ledger_only` question on entry 812
- **WHEN** the owner chooses 改成銀行的金額
- **THEN** one apply request SHALL carry both case ids and the sequence SHALL skip the second question

### Requirement: Transfer and amount answers

When an item carries `transfer.adjust_transfer`, the flow SHALL offer 改成帳單的轉帳; when `transfer.pair_transfer.peer_candidates` is non-empty it SHALL offer 配成轉帳 with a picker of the candidates (account, date, amount); on an `ambiguous` case that allows `adjust_amount` it SHALL offer 改金額. These SHALL call `POST /reconciliation/cases/{id}/actions` and show a refusal code as an inline error.

#### Scenario: Pair a transfer
- **GIVEN** a `needs_peer` case with one peer candidate on 玉山銀行
- **WHEN** the owner picks 配成轉帳 and the candidate
- **THEN** the SPA SHALL post `{action: 'pair_transfer', params: {peer_line_id}}` and move to the next question

#### Scenario: Ambiguous transfer leg
- **GIVEN** `adjust_transfer.entry_id` is null with two `candidate_entry_ids`
- **WHEN** the owner picks 改成帳單的轉帳
- **THEN** the SPA SHALL ask which entry first and send its id

### Requirement: Portfolio and system task sources

The page SHALL also read portfolio and system sources: pending dividends past their `payment_date`, the import failure behind the 匯入 dot, the 國泰 daily statement's pending dates and latest run (`/api/portfolio/imports/cathay-statement/pending`, `/runs/latest`), and skipped card-statement auto-pay (`GET /reconciliation/audit?event=auto_pay_skipped`, last 30 days).

#### Scenario: Dividends past payment date
- **GIVEN** 玉山金 and 台積電 dividends are pending with payment dates before today
- **THEN** one 要你確認 task SHALL read 「股利待確認收款 · 2 筆」 and link to 股利

#### Scenario: Stale 國泰 run
- **GIVEN** at 07:00 the latest run is yesterday 06:41
- **THEN** a 系統 task SHALL say the 06:40 run did not report

#### Scenario: Held 國泰 date
- **GIVEN** `pending` returns 2026-10-28 with status `held`
- **WHEN** the owner presses 確認 on that task
- **THEN** the SPA SHALL post the ack and the task SHALL disappear

#### Scenario: Auto-pay over the cap
- **GIVEN** the latest auto-pay row of the 國泰 CUBE statement is skipped with `over_cap`
- **THEN** a 系統 task SHALL read 「自動扣繳未執行：超過單筆上限」 and link to the statement

### Requirement: Empty state

When no task is counted, the page SHALL show 都處理好了, the next upcoming item from 追蹤中 if any, and a link to 追蹤中.

#### Scenario: Nothing to do
- **GIVEN** only tracking items exist
- **WHEN** the page renders
- **THEN** it SHALL show 都處理好了 and the nearest upcoming bill
