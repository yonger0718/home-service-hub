## 0. Spec

- [x] 0.1 Design canvas (CalendarDesktop, CalendarPhone) and `design_handoff_v3/design_refs/`
- [x] 0.2 Proposal, design (D1–D8), spec delta
- [x] 0.3 `openspec validate add-ledger-calendar-view --strict`

## 1. State and data (PR A)

- [ ] 1.1 `day` query parameter
  - read it into `selectedDay`, write it on pick, clear and ‹ ›
  - month edges; navigator clears it
  - `day` implies 日曆 without overwriting the stored view
- [ ] 1.2 Carry `month` and `day` on entry links opened from the calendar; restore them on ✕, back and delete
- [ ] 1.3 `CalendarDataService`
  - per-month cache of breakdown day × category and breakdown by merchant
  - invalidate on `entriesChanged`
  - per-part failure flags
- [ ] 1.4 Invoice markers: inbox `einvoices` grouped by `issue_date` (later from `NotificationCenterService`, D7)
- [ ] 1.5 Tests: URL round-trip, month crossing, navigator clears, cache invalidation, a failing read leaves the other parts

## 2. Day cells (PR A)

- [ ] 2.1 `calendar-month` inputs
  - dots per day, heat ratio, invoice days
  - `compact` (narrow figures)
  - `weekOf` (week mode)
- [ ] 2.2 Cell layout: date disc, net, dots and +N, heat bar, bill and invoice marks; dimmed future; wide legend
- [ ] 2.3 Accessible names; roving tabindex with ← → ↑ ↓ Home End
- [ ] 2.4 Tests
  - the heaviest day spans the full width
  - compact numbers (−3.1k, +71.7k, −250)
  - dots omitted on failure
  - the accessible name includes 發票待對帳

## 3. Wide layout (PR B)

- [ ] 3.1 Calendar mode in `AccountingLayoutComponent` (timeline, 日曆, panes)
  - calendar-only list pane with its own flex basis
  - 支出 / 收入 / 結餘 in the header
- [ ] 3.2 本月摘要
  - extend `MonthOverviewComponent` (search, 今天, distribution, 花最多的商家, 這個月要處理)
  - shown from 1024 px in calendar mode
- [ ] 3.3 `CalendarDayPanelComponent`
  - header with ‹ › and 本月摘要
  - bill line, rows with time
  - empty and future states; 在這天記一筆 prefilled date
- [ ] 3.4 Entry drawer over the right pane
  - day panel stays mounted underneath
  - ✕ and Esc close it
  - ↑ ↓ through the month move the selected day
  - focus trap and return
  - form at 560 px
- [ ] 3.5 Search from 本月摘要 switches the base layer to the month's result list
- [ ] 3.6 Tests
  - default 本月摘要
  - pick a day
  - drawer URL
  - ↓ across a day edge
  - merchant failure line
  - 清單 view unchanged

## 4. Narrow layout (PR C)

- [ ] 4.1 One-line month figures; grid with dots; 今天 bar and 回到今天
- [ ] 4.2 Week collapse on pick; 展開整月 handle
- [ ] 4.3 Day header with ‹ ›; swipes on the day area (day) and the week strip (week); `[` `]` keys
- [ ] 4.4 Tests at 390 px: default, pick, swipe, back from an entry restores the week and day

## 5. Wrap-up

- [ ] 5.1 Light and dark screenshots at 390 / 820 / 1100 / 1440 against the artboards
- [ ] 5.2 Move the spec delta into `openspec/specs/frontend-accounting-ledger/spec.md` and archive the change
