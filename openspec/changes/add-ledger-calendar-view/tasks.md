## 0. Spec

- [x] 0.1 Design canvas (CalendarDesktop, CalendarPhone) and `design_handoff_v3/design_refs/`
- [x] 0.2 Proposal, design (D1–D8), spec delta
- [x] 0.3 `openspec validate add-ledger-calendar-view --strict`

## 1. State and data (PR A)

- [x] 1.1 `day` query parameter
  - read it into `selectedDay`, write it on pick, clear and ‹ ›
  - month edges; navigator clears it
  - `day` implies 日曆 without overwriting the stored view
  - write `month` and `day` with `replaceUrl`, merged into the existing query (drill-through filters kept); follow query changes (Back, Forward) instead of the construction snapshot
  - 清單 removes `day` and stores 清單; 近 7 天 writes `month`/`day` and stores 日曆
- [x] 1.2 Carry `month` and `day` on entry links opened from the calendar; restore them on ✕, back and delete
  - every exit: layout close, `EntryDetail.close()` / `parentUrl()`, row open, next-entry navigation, after delete, entry form `leave()` fallback and the split-save fallback
  - entry form reads a `date=` prefill param (for 「＋ 在這天記一筆」)
- [x] 1.3 `CalendarDataService`
  - per-month cache of breakdown day × category and breakdown by merchant, both with `include_excluded_projects=true` and the month's `date_from` / `date_to`; dot colours joined from split `key` to the category list
  - invalidate on `entriesChanged`
  - per-part failure flags
- [x] 1.4 Invoice markers: inbox `einvoices` grouped by `issue_date` via `InboxService.get()`, refreshed on `InboxService.changed` and `entriesChanged` (later from `NotificationCenterService`, D7) (read lives in `CalendarDataService`; D7 swap point)
- [x] 1.5 Tests: URL round-trip, month crossing, navigator clears, Back leaves the calendar (replaced history), drill-through filters kept, 清單 clears `day`, 近 7 天, cache invalidation, a failing read leaves the other parts

## 2. Day cells (PR A)

- [x] 2.1 `calendar-month` inputs
  - dots per day, heat ratio, invoice days
  - `compact` (narrow figures)
  - `weekOf` (week mode)
- [x] 2.2 Cell layout: date disc, net, dots and +N, heat bar, bill mark (帳單 / 已繳 replaces the card icon badge; `paid` from the timeline's statement data) and invoice mark (`--app-warning` until `--app-banner-warn` lands); dimmed future; wide legend
- [x] 2.3 Accessible names; roving tabindex with ← → ↑ ↓ Home End
- [x] 2.4 Tests
  - the heaviest day spans the full width; a refund-heavy day (expense > 0) has no bar; net = income + expense
  - a quiet past day shows — wide and nothing narrow
  - compact numbers (−3.1k, +71.7k, −250)
  - dots omitted on failure
  - the accessible name includes 發票待對帳

## 3. Wide layout (PR B)

- [x] 3.1 Calendar mode in `AccountingLayoutComponent` (timeline, 日曆, panes)
  - the timeline publishes its view via `AccountingOverviewService.calendar`
  - calendar-only list pane with its own flex basis
  - 支出 / 收入 / 結餘 in the timeline monthbar (between the month navigator and the view toggle)
- [x] 3.2 本月摘要
  - extend `MonthOverviewComponent` (search, 今天, distribution, 花最多的商家, 這個月要處理)
  - single-candidate invoice open: entry, or group via a member id from the day's `GET /entries`
  - shown from 1024 px in calendar mode
- [x] 3.3 `CalendarDayPanelComponent`
  - header with ‹ › and 本月摘要
  - bill line, rows with time
  - empty and future states; 在這天記一筆 prefilled date
- [x] 3.4 Entry drawer over the right pane
  - day panel stays mounted underneath
  - ✕ and Esc close it
  - ↑ ↓ through the month move the selected day; `moveSelection` stands down in calendar mode
  - focus trap, Esc and focus return with drawer-own handlers; `sheetOpen` stays the 760–1023 sheet
  - form at 560 px
- [x] 3.5 Search from 本月摘要 switches the base layer to the month's result list
- [x] 3.6 Tests
  - default 本月摘要
  - pick a day
  - drawer URL
  - ↓ across a day edge
  - Esc returns focus to the row
  - grid roving tabindex (← → ↑ ↓ Home End Enter)
  - merchant failure line
  - 清單 view unchanged

## 4. Narrow layout (PR C)

- [x] 4.1 One-line month figures; grid with dots; 今天 bar and 回到今天
- [x] 4.2 Week collapse on pick; 展開整月 handle
- [x] 4.3 Day header with ‹ ›; swipes on the day area (day) and the week strip (7 days, same weekday) only when horizontal movement dominates; `[` `]` keys
- [x] 4.4 Tests at 390 px: default, pick, swipe, vertical scroll not swallowed, 回到今天, back from an entry restores the week and day

## 5. Wrap-up

- [ ] 5.1 Light and dark screenshots at 390 / 820 / 1100 / 1440 against the artboards
- [ ] 5.2 Move the spec delta into `openspec/specs/frontend-accounting-ledger/spec.md` and archive the change
