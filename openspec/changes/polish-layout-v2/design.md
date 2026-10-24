## Context

Findings come from running `main` at #130 locally with seeded data (6 holdings, 2 broker accounts, 8 dividends, 5 accounting accounts, ~140 entries) and screenshotting every route at 1440 × 900, 1100 × 800 and 390 × 844 in light and dark. Each finding was traced to code before it was written down; the evidence sheet is `design_handoff_v2/reviews/2026-10-24-layout-review.md`.

## Decisions

### D1. `.row` collision: rename, don't fight Bootstrap

`styles.scss` imports `bootstrap.min.css`, whose `.row` sets `display:flex; flex-wrap:wrap; margin-inline:-12px`. Six component templates use `.row` as their own class — timeline, reminders, statement-list and statement-detail through the shared `components/accounting/entry-row.scss`, account-entries with its own stylesheet, and the inline template of `portfolio/cash-flow-form` — while four legacy PrimeNG forms (`item-list`, `portfolio/dividend-list`, `portfolio/transaction-list`, `accounting/account-settings`) use the Bootstrap grid (`row g-2`, `col-*`) on purpose; on the accounting timeline (list mode) and the account entry history the negative margin wins and every row sits 12 px left of the gutter (measured: `button.row` left = 4 px while the page content starts at 16 px).

Options: (a) override `.row` margins inside each component, (b) rename the component classes, (c) stop importing the Bootstrap grid. (c) is the long-term answer but touches the inventory pages; (a) is fragile. Bootstrap's child rule `.row > *` (width 100 %, max-width 100 %, flex-shrink 0, 12 px side padding) travels with the class, so a margin-only override would still leave every direct child padded and full-width; the class has to go. **Choose (b)**: rename to a component-scoped name (`entry-row` for the accounting rows, `field-row` for the cash-flow form) in those six templates and add a gate that fails on Bootstrap grid class names (`row`, `col`, `col-*`, `container`) outside an explicit allowlist of the four legacy forms. The repository has no CI workflow, so the gate is a Vitest spec that scans the templates and runs with `ng test` (the same mechanism as `theme/contrast.spec.ts`); the allowlist shrinks as those forms are rebuilt.

### D2. Amount formatting is one function

Today the same value appears as `$3,021,869.34` (hero, 總市值), `3,021,869` (composition legend), `$292,227.2` (holding PnL, one decimal), `+35,205` and `$35,205` (已實現 on two pages). Add `formatMoney(value, { currency, sign?, symbol? })` and `formatPct(value, { sign? })` in `components/ui/format.ts`, with these rules (the unit-test oracle for task 2.1):

| Rule | Value |
|---|---|
| Scope | Amounts: totals, KPI and summary values, row amounts, PnL, fees and taxes, cash balances, TWD ledger totals. **Not** prices or per-share values, **not** the `nativeAmount` pipe (holdings and realized native columns keep its 2 / 4-decimal output, `frontend-foreign-markets-display`), **not** the ledger's foreign-currency row display (`¥5,390` + rate, already whole-unit). |
| Precision | TWD and JPY whole units; every other currency two decimals. Rounding half away from zero (the ledger's rule). |
| TWD marker | `$3,021,869`. `symbol: false` only where the element's own label or column header already names TWD (the hero total under 總資產 · TWD and its composition legend; table columns headed TWD) → `3,021,869`. |
| Other currencies | Value, space, ISO code: `1,326.00 USD`, `5,390 JPY`. |
| Sign | `sign: 'change'` (PnL, day change, ledger entries): `+$4,921` / `−$4,076` (U+2212), zero unsigned. Otherwise no `+`; negatives still `−`. |
| Percent | `formatPct`: one decimal when |v| < 100, none at or above (`12.3%`, `123%`); half away from zero; `+` / `−` with `sign: 'change'`. |
| Colour | Trend colours only for a gain or loss. The 賣出成本費用 summary value uses `--app-text`; row-level 手續費 / 交易稅 use `--app-text-muted`. |

Totals and group headers in a foreign market show TWD (whole dollars); native amounts beside them keep two decimals. The cash-account total `NT$150,000` in the legacy accounts spec becomes `$150,000` (MODIFIED there).

### D3. Values fit their card

KPI and summary values (the dashboard's four KPI cards; the 交易紀錄, 股利 and 已實現損益 summary cards; the 現金帳戶 header figures) sit in a card with `container-type: inline-size` and use `font-size: clamp(18px, 12cqi, var(--fs-value))`, `white-space: nowrap`, `font-variant-numeric: tabular-nums`. The ceiling is the token (`--fs-value`, 32 px), not 44 px. Below 760 px the KPI grid is two columns (`repeat(2, minmax(0, 1fr))`, card padding `--space-4`, gap 12 px) instead of `auto-fit` 220 px, so the live "Bento KPI grid" requirement is MODIFIED, not duplicated. An inline-size container cannot grow from its contents, so the earlier "the card grows" clause is dropped: the floor is chosen so an 8-digit TWD amount (`$12,345,678`) fits a 2 × 2 cell at 360 px and `$3,021,869` fits a four-column card at 1100 px; the browser check asserts `scrollWidth ≤ clientWidth` on every value at 360 / 390 / 1100. The hero total stays at `--fs-value` in its full-width card (it already fits). Combined with D2 this removes the 1100 px clip (`$3,021,869.3`) and the 390 px wrap (`$2,871,43 / 2`).

### D4. Pane state belongs to two-pane layouts only

`PortfolioAccountsPageComponent` sets `PaneStateService.paneOpen` so the shell auto-collapses the sidebar, and the FAB then uses the accounting formula (`5fr / 6fr` columns) and lands mid-page. The accounts page is not a pane layout: its detail is always shown. **Remove the accounts page's `paneOpen` writes.** The FAB position for pane layouts moves to a CSS custom property (`--fab-right`) the layout sets, so the shell no longer hard-codes accounting's column ratio.

### D5. The floating button yields while scrolling

The 56 px button covers right-aligned content mid-scroll (row ⋯ buttons on 交易 and 股利, 待收股利 amounts, 持股配置 percentages). It slides out (translateY + fade, 160 ms, `prefers-reduced-motion`: instant) while the page's scrolling ancestor scrolls down — the document / `.hub-main` on ordinary pages, the accounting list pane in panes mode — and returns on any upward scroll, at the end of the page, or after 1.2 s idle. The hidden state ends in `visibility: hidden`, so the button leaves the tab order and cannot keep focus; it never hides while it has focus. `N` always works, hidden or not. The existing `--fab-clear` bottom padding stays. The live "App-wide 記一筆 floating button" requirement is MODIFIED to say all of this (and to take the pane offset from the layout, D4).

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

The live requirements that fix today's controls (realized filter card, the timeline filter row with its ⌕ button below 760 px) are MODIFIED by the matching deltas; the filter bar replaces them at every width.

### D7. Empty right pane shows the month

At ≥ 1280 px with no selection, the accounting pane shows the month overview instead of one line of copy: category distribution with amounts and the next three due items (bills, schedules); no extra links (owner 2026-10-24: simplified version). Below 1280 px the pane keeps its current one-line empty state. On 帳戶 the empty copy becomes 選擇帳戶查看明細，或按 ＋ 新增 and the selected account card gets the same indigo ring as 現金帳戶.

### D8. Net-worth chart and cash-only rows

`refresh_snapshot_cash_range` inserts a cash-only row on a date with cash activity but no snapshot, e.g. a dividend receipt on a Saturday. Its shape is the backend's `_is_cash_only_row`: `total_market_value`, `total_cost`, `total_unrealized_pnl`, `total_dividends` and `total_realized_pnl` all 0 and `portfolio_xirr` null. The chart draws those as a drop to 0, and because the API serialises `total_assets_twd = total_market_value + total_cash_twd`, 帳戶總值 drops to the cash balance on the same day.

Client-side rule (backend rows untouched): a point is **cash-only** when it has exactly that shape. For every run of consecutive cash-only points that follows a point with holdings, the chart **carries** the last non-cash-only point's `total_market_value`, `total_cost`, `total_unrealized_pnl`, `total_dividends` and `total_realized_pnl` onto each point of the run (the dates stay; nothing is skipped), and plots 帳戶總值 for those points as the carried `total_market_value` plus the point's own `total_cash_twd`, ignoring the serialised `total_assets_twd`. A liquidation snapshot (market value and cost 0 with a non-zero `total_realized_pnl`) is not cash-only and plots as written. Cash-only points before the first holdings point plot as written.

### D9. Phone header actions

Header = title, at most one primary action, then the shell's icon buttons (gear today). Secondary actions (匯入券商, 刷新行情, 匯入 CSV) move into a ⋯ menu button. The market `SegToggle` on 投資概覽 moves under the header at full width.

## Risks / Trade-offs

- Renaming classes touches many templates; risk of missed selectors in specs. Mitigation: the Vitest gate (D1).
- Hiding the FAB on scroll can surprise; `N` and the phone "+" stay, and the button returns on any upward scroll.
- Dropping decimals hides cents that do not exist in TWD or JPY; other currencies and the `nativeAmount` pipe are untouched.
- The 18 px value floor is sized for 8-digit TWD amounts; a 9-digit total would overflow a 2 × 2 phone cell (browser check at 360 px).

## Migration Plan

1. PR A — D1 class rename + lint gate; dark logo; D4 pane ownership. (Defects, no visual change elsewhere.)
2. PR B — D2 formatter + D3 value fitting across dashboard, 交易, 股利, 已實現, 現金.
3. PR C — D6 filter bar on the five pages.
4. PR D — D5 FAB behaviour, D9 phone headers, phone KPI grid and holdings rows.
5. PR E — D7 empty pane, D8 chart, balance chart, small items (股利 empty card, 匯入 footer, neutral cost colour, icon set, title placement).

## Resolved Questions

- 現金帳戶 keeps its always-visible detail (no accounting-style two-pane collapse); only the stray `paneOpen` writes go (D4). Owner ruling 2026-10-24.
- Owner rulings 2026-10-24: D2 accepted as written; D5 accepted; D6 accepted with the 已實現損益 date range kept behind 篩選; D7 accepted in the simplified form (distribution + three due items, no links).
