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
- The reconciliation inbox returns `einvoices` (ambiguous, near and unmatched invoices, each with an `issue_date`).

The canvas artboards are `design_handoff_v3/design_refs/CalendarDesktop.dc.html` and `CalendarPhone.dc.html`. They are interactive (day, entry, ↑ ↓, ‹ ›, 展開整月) and use invented October 2026 data: expense 29,626, income 72,000, today 10/22.

## Decisions

### D1. Calendar first, rows on demand

In 日曆 view nothing is selected by default.

- **≥ 1024 px:** the calendar alone fills the list pane, and the right pane shows 本月摘要 (D5).
- **< 1024 px:** the grid takes the screen, with a 今天 bar under it (D6).

Picking a day shows only that day's rows. Owners who want a scrolling list keep the 清單 view, which this change leaves alone. The cost is one extra tap for "what did I log today". The 今天 shortcut on both widths answers that.

### D2. The selected day lives in the URL

Add a `day=YYYY-MM-DD` query parameter beside `month`, so that back, reload, a shared link and the return from an entry all land on the same day. This is the reason for D2: today a return from an entry loses the day, because `selectedDay` is not in the URL.

- `day` implies the 日曆 view for that visit; the stored view is not overwritten.
- `month` follows `day`, and a `day` outside `month` wins.
- ‹ › and swipes step one calendar day, across month edges, and move `month` with them.
- Moving the month with the navigator drops `day`.
- Entry routes opened from the calendar carry `month` and `day` along (`/accounting/entries/:id?month=…&day=…`), so ✕ and back restore the selection.

### D3. Data: no new endpoint

| Cell or panel part | Source | When |
|---|---|---|
| Net, count, heat bar | `/entries/summary/daily` (existing) | month shown |
| Category dots | `GET /reports/breakdown?group_by=day&split_by=category&metric=expense&date_from&date_to` | month shown in 日曆, once |
| Bill mark, bill line | billing due events (existing) | as today |
| Invoice marker | inbox `einvoices` by `issue_date` (existing `InboxService`) | as today |
| 花最多的商家 | `GET /reports/breakdown?group_by=merchant&metric=expense` (first five rows, rows with a null key dropped) | month shown, wide 本月摘要 only |
| Category distribution, due items | `MonthOverviewComponent` inputs (existing) | as today |
| Day rows | `GET /entries?date_from=d&date_to=d` (existing `loadDay`) | day picked |

A `CalendarDataService` caches the two breakdown reads per month and drops the cache on `entriesChanged`. If a breakdown read fails, only its part is left out: no dots, or no merchant card. The grid and the summary still render, with no error toast; a muted 「商家排行讀取失敗」 line replaces the merchant card.

The dots come from the reports' rules, not from the daily summary. The two agree on what counts as expense (refunds reduce it; transfers, debts and adjustments are excluded) because add-accounting-reports moved the month summary onto those shared rules.

### D4. Day cells

Each cell holds, top to bottom:

- **Date.** Today sits on a `--app-primary` disc. Weekend numbers are muted.
- **Net.**
  - ≥ 1024 px: thousands separators, for example −3,096.
  - < 1024 px: compact form, for example −3.1k or +71.7k, one decimal with a trailing .0 dropped.
  - A past day with no rows shows — on wide screens and nothing on narrow ones. Future days show no figure and are dimmed.
- **Category dots.** Up to three, from the day's largest expense categories, in the owner's category colours. A +N follows when there are more. Narrow screens hide the dots once a day is picked (week mode, D6).
- **Heat bar.** A 4 px bar (3 px under 1024) along the bottom. Its length is the day's |expense| divided by the month's largest daily |expense|, drawn in a translucent `--tone-neg`. Income-only days have no bar.
- **Marks.**
  - The existing card mark for bill due days: 帳單, or 已繳 once paid, on wide screens; a muted dot on narrow ones.
  - A warning-ink marker (`--app-banner-warn`, from the notifications handoff) when an inbox invoice is dated that day: 發票待對帳 on wide screens, a dot on narrow ones.

The cell's `aria-label` reads everything, for example 「10 月 21 日 週三，3 筆，支出 $3,096，發票待對帳」. The heat scale and the dots are decoration and are `aria-hidden`.

A small legend under the wide grid explains the bar, the dots and the card mark.

### D5. Wide layout (≥ 1024 px, 日曆 view)

`AccountingLayoutComponent` gains a calendar mode. It is active when `list` is `timeline`, the view is `calendar`, and the mode is `panes`. In calendar mode:

- **The list pane is the calendar alone.** It is wider than in the list view, about 2 : 1 against the right pane. The month card collapses into the page header: 支出 / 收入 / 結餘 between the month navigator and 清單 / 日曆.
- **The right pane** has a base layer and an overlay layer.
  - **Base, no day: 本月摘要.** This is `MonthOverviewComponent` extended and shown from 1024 px (not only from 1280 px) in calendar mode. Top to bottom:
    1. A search field. Searching switches the base to the 清單-style result list for the month.
    2. A 今天 card (count, names, net). It selects today, and is shown only when the month is the current month.
    3. The category distribution, with a 看報表 › link to `/accounting/reports?month=…`.
    4. 花最多的商家: top five, with count and amount, from D3.
    5. 這個月要處理: inbox invoices dated in the month, and unpaid card bills due in the month. A row selects its day, and an invoice row also opens its entry when the matcher proposed exactly one candidate.
  - **Base, day picked: the day panel** (new `CalendarDayPanelComponent`). It contains:
    - ‹ day label › with count and net under the label, and a 本月摘要 button that clears `day`;
    - the bill line for that day, if any;
    - the day's rows (time · category · merchant · account, the existing pills);
    - an empty state: 這天沒有記錄, or 還沒到這天 for a future day, with 「＋ 在這天記一筆」. That button opens the entry form with the date filled.
  - **Overlay: the entry detail.** The `pane` outlet renders the detail as a drawer covering the right pane, using the sheet styles of the 760–1023 mode and a raised shadow. The day panel stays mounted under it, so closing the drawer is instant and keeps the scroll position.
    - ✕ or Esc navigates back to `/accounting?month&day`.
    - ↑ ↓ move through the month's entries in list order (newest first). The selected day follows the entry.
    - The entry form opened from 編輯 uses the same drawer, widened to 560 px.

The calendar keeps the same cell contents at 1024–1279. Only the right pane narrows, and the merchant card wraps its amounts below the names.

### D6. Narrow layout (< 1024 px, 日曆 view)

- **The month card** becomes one line under the navigator: 支出 $… 收入 $… 結餘 $….
- **No day picked.** The grid fills the screen's width with cells about 76 px tall. Under it sits a 今天 bar: a date disc, 今天 · N 筆, the names, the net and a › chevron. When the month shown is not the current month, the bar reads 回到今天 and moves to today's month and day.
- **Day picked.**
  - The grid collapses to the week that contains the day, with cells about 52 px tall and no dots. A 展開整月 handle under the week clears `day`.
  - Below the week sit the day header (‹ label ›, count and net) and the day's rows.
  - A horizontal swipe on the day area moves one day. A swipe on the week strip moves one week. Both update `day`.
- **Entry detail.** Tapping a row opens the detail as today:
  - below 760 px, its own route (full screen);
  - at 760–1023 px, the right-hand sheet.

  The canvas draws the phone detail under the week strip. The existing full-screen route is kept instead because the detail is long, has its own action bar, and is already reached that way from every other list. D2 restores the day on return.

### D7. Notification center

The invoice marker and 這個月要處理 read the inbox. When `add-notification-center` lands, both read `NotificationCenterService` instead. They show only items dated in the shown month, and they never add to the 通知 count.

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
