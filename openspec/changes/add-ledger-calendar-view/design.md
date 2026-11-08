## Context

Today's pieces:

- `TimelineComponent` has `view: 'list' | 'calendar'`, which is remembered on the device (`storeView`, localStorage), and `selectedDay`, a signal that is not in the URL.
  - Picking a day calls `loadDay(date)`, which runs `GET /entries` with `date_from = date_to = date`. The rows render under the grid.
  - Tapping the selected day again clears it. 近 7 天 (`openUpcoming`) switches to 日曆 and selects the first due day.
- `CalendarMonthComponent` draws the grid from:
  - `GET /entries/summary/daily?month=`: per day `expense`, `income` and `count`, in the main currency;
  - the billing due events: `dueCount`, plus a `closing` mark.
- `AccountingLayoutComponent` switches by width (`LayoutModeService`):
  - `< 760`: one screen per route;
  - `760–1023`: one pane, with detail, form and settings in a sheet from the right;
  - `≥ 1024`: list pane plus `pane` outlet.
  - At ≥ 1280 with nothing selected, the right pane shows `MonthOverviewComponent` (category amounts, next three due items).
- `GET /reports/breakdown` (add-accounting-reports) groups by `day`, `merchant`, `category`, … with an optional `split_by`, using the reports' inclusion rules.
- The reconciliation inbox returns `einvoices` (ambiguous, near and unmatched invoices, each with an `issue_date` and a `candidates` list whose items are an entry or a split group).
- The timeline reads `month` from the URL once, at construction (the 報表 drill-through), and never writes it back; `month` otherwise lives in a signal.
- The layout knows only `listKey === 'timeline'`, not which view the timeline shows. The timeline publishes state for the layout through `AccountingOverviewService` (month, summary, due items).
- A day cell today shows the day number, a credit-card icon badge on due days (with a count above one), a top border on closing days, and separate compact expense and income figures. It has no paid state.
- Every exit from an entry drops the query: the layout's close action (`navigateByUrl('/accounting')`), `EntryDetail.close()` / `parentUrl()`, the row-open navigation and the next-entry navigation.

The canvas artboards are `design_handoff_v3/design_refs/CalendarDesktop.dc.html` and `CalendarPhone.dc.html`. They are interactive (day, entry, ↑ ↓, ‹ ›, 展開整月) and use invented October 2026 data: expense 29,626, income 72,000, today 10/22.

## Decisions

### D1. Calendar first, rows on demand

In 日曆 view nothing is selected by default.

- **≥ 1024 px:** the calendar alone fills the list pane, and the right pane shows 本月摘要 (D5).
- **< 1024 px:** the grid takes the screen, with a 今天 bar under it (D6).

Picking a day shows only that day's rows. Owners who want a scrolling list keep the 清單 view, which this change leaves alone. The cost is one extra tap for "what did I log today". The 今天 shortcut on both widths answers that.

### D2. The selected day lives in the URL

Add a `day=YYYY-MM-DD` query parameter beside `month`, so that back, reload, a shared link and the return from an entry all land on the same day. This is the reason for D2: today a return from an entry loses the day, because `selectedDay` is not in the URL.

- The timeline starts writing `month` and `day` to the URL, and reacts to query changes (Back, Forward, links) instead of reading the URL only at construction.
- Picking a day, ‹ ›, swipes and the month navigator replace the current history entry rather than push one, so Back leaves the 日曆 view instead of stepping through days.
- `day` implies the 日曆 view for that visit; the stored view is not overwritten.
- `month` follows `day`, and a `day` outside `month` wins.
- ‹ › and swipes step one calendar day, across month edges, and move `month` with them.
- Moving the month with the navigator drops `day`.
- Every `month` / `day` write merges into the current query, so the 報表 drill-through filters (`category_id`, `account_id`, `merchant`, `tag`, `no_project`) survive a pick.
- `day` forces 日曆 only while it is in the URL. Choosing 清單 removes `day` and stores 清單.
- 近 7 天 (`openUpcoming`) writes `month` and `day` (the first due day) under the same history rules, and still stores 日曆 as the view, as it does today.
- Entry routes opened from the calendar carry `month` and `day` along (`/accounting/entries/:id?month=…&day=…`), so ✕ and back restore the selection. Every exit listed in Context keeps them, and so do the return after deleting the entry and the entry form's exits: `leave()`'s fallback to `/accounting` when there is no in-app previous page, and the fallback after an unmappable split save.
- 「＋ 在這天記一筆」 opens `/accounting/entry?date=YYYY-MM-DD&month=…&day=…`. `date` is a new prefill parameter the entry form reads beside `kind`, `copy`, `schedule` and `select`.

### D3. Data: no new endpoint

| Cell or panel part | Source | When |
|---|---|---|
| Net, count, heat bar | `/entries/summary/daily` (existing) | month shown |
| Category dots | `GET /reports/breakdown?group_by=day&split_by=category&metric=expense&include_excluded_projects=true&date_from={month}-01&date_to={last day}`; each split's `key` (category id) is joined to the category list for its colour | month shown in 日曆, once |
| Bill mark, bill line | billing due events (existing) | as today |
| Invoice marker | inbox `einvoices` by `issue_date` (`InboxService.get()`; today the timeline only reads the bell count with `limit: 1`) | month shown; refreshed on `InboxService.changed` and `entriesChanged` |
| 花最多的商家 | `GET /reports/breakdown?group_by=merchant&metric=expense&include_excluded_projects=true&date_from={month}-01&date_to={last day}` (first five rows, rows with a null key dropped) | month shown, wide 本月摘要 only |
| Category distribution, due items | `MonthOverviewComponent` inputs (existing) | as today |
| Day rows | `GET /entries?date_from=d&date_to=d` (existing `loadDay`) | day picked |

A `CalendarDataService` caches the two breakdown reads per month and drops the cache on `entriesChanged`. If a breakdown read fails, only its part is left out: no dots, or no merchant card. The grid and the summary still render, with no error toast; a muted 「商家排行讀取失敗」 line replaces the merchant card.

The dots come from the reports' rules, not from the daily summary. Both use the same spend filter (refunds reduce it; transfers, debts and adjustments are excluded). They differ on projects: report endpoints drop `exclude_from_reports` projects unless `include_excluded_projects=true`, while the daily and month summaries keep them. Both breakdown reads therefore pass `include_excluded_projects=true`, so the dots, the merchants, the net, the heat bar and the category distribution count the same expense.

### D4. Day cells

Each cell holds, top to bottom:

- **Date.** Today sits on a `--app-primary` disc. Weekend numbers are muted.
- **Net.**
  - The net is `income + expense` from the daily summary (`expense` is a negative string). The heat bar uses |expense| only when `expense` is below 0, so a refund-heavy day draws no bar.
  - ≥ 1024 px: thousands separators, for example −3,096.
  - < 1024 px: compact form, for example −3.1k or +71.7k, one decimal with a trailing .0 dropped.
  - A past day with no rows shows — on wide screens and nothing on narrow ones. Future days show no figure and are dimmed.
- **Category dots.** Up to three, from the day's largest expense categories, in the owner's category colours. A +N follows when there are more. Narrow screens hide the dots once a day is picked (week mode, D6).
- **Heat bar.** A 4 px bar (3 px under 1024) along the bottom. Its length is the day's |expense| divided by the month's largest daily |expense|, drawn in a translucent `--tone-neg`. Income-only days have no bar.
- **Marks.**
  - A card mark on bill due days, replacing today's card icon badge: 帳單, or 已繳 when the due event's resolved statement (`BillState`, the figures behind the 繳費提醒 line) has nothing left to pay, on wide screens. Today the grid drops paid due days (`billing` keeps only open statements), so paid ones must be passed to the grid as well; a muted dot on narrow ones. Closing days keep their top border.
  - A warning-ink marker when an inbox invoice is dated that day: 發票待對帳 on wide screens, a dot on narrow ones. The ink is `--app-banner-warn` once add-notification-center lands; until then it is `--app-warning`.

The cell's `aria-label` reads everything, for example 「10 月 21 日 週三，3 筆，支出 $3,096，發票待對帳」. The heat scale and the dots are decoration and are `aria-hidden`.

A small legend under the wide grid explains the bar, the dots and the card mark.

### D5. Wide layout (≥ 1024 px, 日曆 view)

`AccountingLayoutComponent` gains a calendar mode. It is active when `list` is `timeline`, the view is `calendar`, and the mode is `panes`. The timeline publishes its view through `AccountingOverviewService` (a new `calendar` signal), because the layout cannot see it otherwise. In calendar mode:

- **The list pane is the calendar alone.** It is wider than in the list view, about 2 : 1 against the right pane. The month card collapses into the page header: 支出 / 收入 / 結餘 between the month navigator and 清單 / 日曆.
- **The right pane** has a base layer and an overlay layer.
  - **Base, no day: 本月摘要.** This is `MonthOverviewComponent` extended and shown from 1024 px (not only from 1280 px) in calendar mode. Top to bottom:
    1. A search field. Searching switches the base to the 清單-style result list for the month.
    2. A 今天 card (count, names, net). It selects today, and is shown only when the month is the current month.
    3. The category distribution, with a 看報表 › link to `/accounting/reports?month=…`.
    4. 花最多的商家: top five, with count and amount, from D3.
    5. 這個月要處理: inbox invoices dated in the month, and unpaid card bills due in the month. A row selects its day. An invoice row also opens its target when the matcher proposed exactly one candidate, keeping `month` and `day`: an entry opens `/accounting/entries/:id`; a split group opens `/accounting/entries/{memberId}/group`, where `memberId` is a member of that group found in the invoice day's `GET /entries` rows, because the group page is routed by a member entry and the candidate carries only the group id.
  - **Base, day picked: the day panel** (new `CalendarDayPanelComponent`). It contains:
    - ‹ day label › with count and net under the label, and a 本月摘要 button that clears `day`;
    - the bill line for that day, if any;
    - the day's rows (time · category · merchant · account, the existing pills);
    - an empty state: 這天沒有記錄, or 還沒到這天 for a future day, with 「＋ 在這天記一筆」. That button opens the entry form with the date filled.
  - **Overlay: the entry detail.** The `pane` outlet renders the detail as a drawer covering the right pane, using the sheet styles of the 760–1023 mode and a raised shadow. The day panel stays mounted under it, so closing the drawer is instant and keeps the scroll position.
    - ✕ or Esc navigates back to `/accounting?month&day` and returns focus to the opening row. Today Esc and the focus trap run only while `sheetOpen` (the 760–1023 sheet); the drawer gets its own handlers, and `sheetOpen` stays the 760–1023 sheet, so the grid is not made inert.
    - ↑ ↓ move through the month's entries in list order (newest first). The selected day follows the entry. Today's `moveSelection` walks `[data-entry-id]` nodes in the DOM from a document-level listener and drops the query; in calendar mode it stands down and the drawer steps through the month's entry list instead.
    - The entry form opened from 編輯 uses the same drawer, widened to 560 px.

The calendar keeps the same cell contents at 1024–1279. Only the right pane narrows, and the merchant card wraps its amounts below the names.

### D6. Narrow layout (< 1024 px, 日曆 view)

- **The month card** becomes one line under the navigator: 支出 $… 收入 $… 結餘 $….
- **No day picked.** The grid fills the screen's width with cells about 76 px tall. Under it sits a 今天 bar: a date disc, 今天 · N 筆, the names, the net and a › chevron. When the month shown is not the current month, the bar reads 回到今天 and sets `month` and `day` to today, which collapses the grid to today's week.
- **Day picked.**
  - The grid collapses to the week that contains the day, with cells about 52 px tall and no dots. A 展開整月 handle under the week clears `day`.
  - Below the week sit the day header (‹ label ›, count and net) and the day's rows.
  - A horizontal swipe on the day area moves one day. A swipe on the week strip moves seven days, keeping the weekday. Both update `day`, and `month` follows. A gesture counts as a swipe only when its horizontal movement dominates; vertical movement scrolls the day list. The week follows `Preference.week_start` (Sunday or Monday).
- **Entry detail.** Tapping a row opens the detail as today:
  - below 760 px, its own route (full screen);
  - at 760–1023 px, the right-hand sheet.

  The canvas draws the phone detail under the week strip. The existing full-screen route is kept instead because the detail is long, has its own action bar, and is already reached that way from every other list. D2 restores the day on return.

### D7. Notification center

The invoice marker and 這個月要處理 read the inbox. `add-notification-center` is still an open change with no `NotificationCenterService` on main. When it lands, both read `NotificationCenterService` instead. They show only items dated in the shown month, and they never add to the 通知 count.

### D8. Keyboard and focus

- In calendar mode the grid is a roving-tabindex group. ← → move one day, ↑ ↓ one week, Enter or Space picks the day, and Home and End jump to the first and last day.
- With a day panel open and the focus outside the drawer, `[` and `]` step one day.
- The drawer traps focus while open and returns it to the row that opened it.

None of these handlers is global: they are bound to the grid and to the panel.

## Risks

- **Two breakdown reads per month.** They are cached and only made in 日曆 view. If the reports endpoints are slow on long histories, dots render after the grid (the cell layout reserves their row), and D3's failure rule applies.
- **Wider list pane in calendar mode.** The pane's width rule is shared with the accounts list. Calendar mode sets its own flex basis and does not change the shared default.

## Open questions

- Should 本月摘要 keep the timeline's category distribution exactly (five plus 其他) or list every category, as drawn? This proposal keeps the existing distribution. The artboard lists every category because the sample month has six.
