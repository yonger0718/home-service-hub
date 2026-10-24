## Context

Findings come from running `main` at #130 locally with seeded data (6 holdings, 2 broker accounts, 8 dividends, 5 accounting accounts, ~140 entries) and screenshotting every route at 1440 × 900, 1100 × 800 and 390 × 844 in light and dark. Each finding was traced to code before it was written down; the evidence sheet is `design_handoff_v2/reviews/2026-10-24-layout-review.md`.

## Decisions

### D1. `.row` collision: rename, don't fight Bootstrap

`styles.scss` imports `bootstrap.min.css`, whose `.row` sets `display:flex; flex-wrap:wrap; margin-inline:-12px`. Nine component templates use `.row` as their own class (a tenth, `item-list`, uses the Bootstrap grid on purpose); on the accounting timeline (list mode) and the account entry history the negative margin wins and every row sits 12 px left of the gutter (measured: `button.row` left = 4 px while the page content starts at 16 px).

Options: (a) override `.row` margins inside each component, (b) rename the component classes, (c) stop importing the Bootstrap grid. (c) is the long-term answer but touches the inventory pages; (a) is fragile. **Choose (b)**: rename to a component-scoped name (`entry-row`, `cash-row`, `div-row`, …) in those nine templates and add a lint check that fails on `class="row"` outside `components/item-list` (the only Bootstrap grid user).

### D2. Amount formatting is one function

Today the same value appears as `$3,021,869.34` (hero, 總市值), `3,021,869` (composition legend), `$292,227.2` (holding PnL, one decimal), `+35,205` and `$35,205` (已實現 on two pages). Add `formatMoney(value, { currency, sign })` in `components/ui/format.ts`:

| Case | Output |
|---|---|
| TWD, `|v|` ≥ 1,000 | `$3,021,869` |
| TWD, `|v|` < 1,000 | `$448` (amounts round to whole dollars for display; prices and per-share values keep their own precision) |
| Foreign | `1,326.00 USD`, `¥5,390` |
| `sign: 'change'` (PnL, day change, entries) | `+$4,921` / `−$4,076` (U+2212) |
| Percent | one decimal under 100 %, none above |

Totals and group headers in a foreign market show TWD with no decimals; native amounts keep two.

### D3. Values fit their card

KPI and hero values use `font-size: clamp(22px, 7cqi, 44px)` inside a `container-type: inline-size` card, `white-space: nowrap`. Combined with D2 this removes the 1100 px clip (`$3,021,869.3`) and the 390 px wrap (`$2,871,43 / 2`). The value never truncates; if the clamp floor is reached, the card grows.

### D4. Pane state belongs to two-pane layouts only

`PortfolioAccountsPageComponent` sets `PaneStateService.paneOpen` so the shell auto-collapses the sidebar, and the FAB then uses the accounting formula (`5fr / 6fr` columns) and lands mid-page. The accounts page is not a pane layout: its detail is always shown. **Remove the accounts page's `paneOpen` writes.** The FAB position for pane layouts moves to a CSS custom property (`--fab-right`) the layout sets, so the shell no longer hard-codes accounting's column ratio.

### D5. The floating button yields while scrolling

The 56 px button covers right-aligned content mid-scroll (row ⋯ buttons on 交易 and 股利, 待收股利 amounts, 持股配置 percentages). It slides out (translateY + fade, 160 ms, `prefers-reduced-motion`: instant) while the page scrolls down and returns on scroll up, at the end of the page, or after 1.2 s idle. `N` always works. The existing `--fab-clear` bottom padding stays.

### D6. Shared filter bar

Three patterns exist (inline rows on 交易, a titled card on 已實現, nine wrapping controls on the accounting timeline). New primitive `<app-filter-bar>`:

- Row: search field (flex), up to two primary controls (each page names them), a 篩選 button with the count of active secondary filters.
- Secondary filters open in a popover at ≥ 760 px and a bottom sheet below.
- Active filters render as removable chips under the bar; 清除全部 when two or more.

| Page | Primary inline | Behind 篩選 |
|---|---|---|
| 交易紀錄 | 全部／買進／賣出 | 日期範圍, 券商, 排序 |
| 股利 | 狀態 | 日期範圍, 來源, 排序 |
| 已實現損益 | 年度 (YTD / 年份 / 全部) | 日期範圍, 市場, 券商, 僅當沖, 排序 (owner 2026-10-24: the date range stays, behind 篩選) |
| 現金帳戶 | 類型 | 日期範圍, 排序 |
| 記帳時間軸 | 帳戶 | 類型, 類別, 專案, 標籤, 商家, 金額範圍 |

### D7. Empty right pane shows the month

At ≥ 1280 px with no selection, the accounting pane shows the month overview instead of one line of copy: category distribution with amounts and the next three due items (bills, schedules); no extra links (owner 2026-10-24: simplified version). Below 1280 px the pane keeps its current one-line empty state. On 帳戶 the empty copy becomes 選擇帳戶查看明細，或按 ＋ 新增 and the selected account card gets the same indigo ring as 現金帳戶.

### D8. Net-worth chart and cash-only rows

`refresh_snapshot_cash_range` inserts a cash-only row (`total_market_value = 0`) on a date with cash activity but no snapshot, e.g. a dividend receipt on a Saturday. The chart draws those as a drop to 0. The chart skips points where `total_market_value = 0 and total_cost = 0` while the previous point has holdings, and carries 持股淨值 forward for that date; 帳戶總值 uses the carried value plus that day's cash. Backend behaviour is unchanged.

### D9. Phone header actions

Header = title, at most one primary action, then the shell's icon buttons (gear today). Secondary actions (匯入券商, 刷新行情, 匯入 CSV) move into a ⋯ menu button. The market `SegToggle` on 投資概覽 moves under the header at full width.

## Risks / Trade-offs

- Renaming classes touches many templates; risk of missed selectors in specs. Mitigation: grep gate in CI (D1).
- Hiding the FAB on scroll can surprise; `N` and the phone "+" stay, and the button returns on any upward scroll.
- Dropping decimals hides cents that do not exist in TWD; foreign values are untouched.

## Migration Plan

1. PR A — D1 class rename + lint gate; dark logo; D4 pane ownership. (Defects, no visual change elsewhere.)
2. PR B — D2 formatter + D3 value fitting across dashboard, 交易, 股利, 已實現, 現金.
3. PR C — D6 filter bar on the five pages.
4. PR D — D5 FAB behaviour, D9 phone headers, phone KPI grid and holdings rows.
5. PR E — D7 empty pane, D8 chart, balance chart, small items (股利 empty card, 匯入 footer, neutral cost colour, icon set, title placement).

## Resolved Questions

- 現金帳戶 keeps its always-visible detail (no accounting-style two-pane collapse); only the stray `paneOpen` writes go (D4). Owner ruling 2026-10-24.
- Owner rulings 2026-10-24: D2 accepted as written; D5 accepted; D6 accepted with the 已實現損益 date range kept behind 篩選; D7 accepted in the simplified form (distribution + three due items, no links).
