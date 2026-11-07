## Why

The timeline's 日曆 view puts the month grid and the selected day's rows in one column. On a desktop the grid stays small, the rows sit under it and the entry detail takes the right pane, so the month never reads at a glance. On a phone the grid shares the screen with the month card and a list that starts below the fold. The cells show the day's expense and income and a card badge, and nothing else: no hint of what the money went on, how heavy a day was compared to the rest of the month, or that an e-invoice is waiting for a match.

The owner asked for a calendar-first layout. Calendar alone by default; a day's rows only after a day is picked; an entry opening as a floating panel rather than replacing what is on screen. The owner reviewed the design on the canvas (`design_handoff_v3/design_refs/CalendarDesktop.dc.html`, `CalendarPhone.dc.html`) and asked for this handoff on 2026-11-07. **This PR contains no code.**

## What Changes

- **Selection in the URL.** `/accounting?month=YYYY-MM&day=YYYY-MM-DD` selects a day in the 日曆 view. With no `day`, nothing is selected; today is not picked automatically. Back, reload and shared links keep the day.
- **Richer day cells.** Each cell shows:
  - the day's net;
  - up to three category dots;
  - a bottom bar scaled to the day's expense against the month's heaviest day;
  - the card-bill mark;
  - a marker when an e-invoice dated that day is waiting for a match.
- **Wide screens (≥ 1024 px), 日曆 view.** The left pane holds the calendar alone. The right pane follows the selection:
  - With no day, it shows 本月摘要: a 今天 shortcut, the category distribution, 花最多的商家 and 這個月要處理.
  - With a day selected, it shows that day's rows, with ‹ › to move one day and 本月摘要 to clear the day.
  - An opened entry shows as a drawer over the day panel. ↑ ↓ step through the month and move the selected day with them.
- **Narrow screens (< 1024 px), 日曆 view.**
  - With no day, a full-height month grid and a 今天 bar under it.
  - Picking a day collapses the grid to that week, with 展開整月 to go back, and lists the day's rows below. ‹ › or a horizontal swipe move one day.
  - The month card shrinks to one line of 支出 / 收入 / 結餘.
- **The 清單 view is unchanged** on every width.

No backend change: the cells and the summary read endpoints that exist today.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-accounting-ledger`: the timeline's 日曆 view (layout by width, day selection in the URL, day cells, 本月摘要, day panel, entry drawer).

## Non-goals

- No API or schema change. If the per-day category request proves slow, a `top_categories` field on `/entries/summary/daily` is a follow-up.
- The 清單 view, the entry detail's content and the entry form are unchanged.
- Collapsing the grid when the day list is scrolled (as iOS Calendar does) is a follow-up. In this change, the grid collapses when a day is picked and expands with 展開整月.
- Separating fixed costs (rent, insurance) from discretionary spending in 本月摘要 is a follow-up for the reports change.

## Impact

- **Frontend files:**
  - `components/accounting/timeline/*` (view state, URL).
  - `components/accounting/calendar-month/*` (cell content, week mode).
  - `components/accounting/accounting-layout/*` (calendar-only left pane, drawer over the right pane).
  - `components/accounting/month-overview/*` (本月摘要 additions).
  - A new day-panel component.
  - A `CalendarDataService` for the per-month reads.
- **Requests per month shown in 日曆**, cached per month: two extra `GET /reports/breakdown` calls, one per day × category and one per merchant.
- **Caddy:** deep links are unchanged. `day` is a query parameter on the existing `/accounting` path.
- **Design reference:** `design_handoff_v3/` (CalendarDesktop, CalendarPhone).
