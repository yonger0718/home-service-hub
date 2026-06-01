## 1. Tokens + appearance foundation

- [x] 1.1 Replace `:root` and `[data-gainloss="asian"|"western"]` token blocks in `frontend/src/styles.scss` with `design_handoff_dashboard/design_refs/colors_and_type.css` verbatim (light)
- [x] 1.2 Replace dark-mode block in `styles.scss` with handoff dark tokens; remove deprecated slate trend values
- [x] 1.3 Confirm `--app-trend-positive`/`--app-trend-negative` resolve only via `[data-gainloss]` attribute selectors; remove hard-coded fallbacks
- [x] 1.4 Copy `design_refs/assets/logo-lockup.svg` and `app-icon.svg` into `frontend/src/assets/`
- [x] 1.5 Add inline pre-paint script to `frontend/src/index.html` that reads `hh-dark` / `hh-gainloss` from `localStorage` (or `prefers-color-scheme` / `'asian'` fallback) and applies `app-dark-mode` class + `data-gainloss` attribute to `document.documentElement`
- [x] 1.6 Create `frontend/src/app/services/appearance.service.ts` with `dark$` / `gainLoss$` `BehaviorSubject`s, `setDark()` / `setGainLoss()` writers, localStorage persistence, OS preference init, and root-attribute application
- [x] 1.7 Wire `AppearanceService` via `APP_INITIALIZER` in `app.config.ts`
- [x] 1.8 Add Vitest spec for `AppearanceService` covering localStorage roundtrip, OS preference fallback, root-attribute application, and observable emissions

## 2. Shell + navigation

- [x] 2.1 Create `frontend/src/app/components/shell/` standalone component wrapping router outlet with frosted top header
- [x] 2.2 Create `frontend/src/app/components/dock/` standalone component (≥760px) — three groups (Supplies / Portfolio / Accounting) with sub-items matching route ids `inventory`, `shopping`, `portfolio`, `transactions`, `dividends`, `import`, `realized-pnl`, `accounting-dash`, `accounting`, `settings`, plus accounting sub-items (`accounting/settings`, `cards`, `categories`, `recurring`)
- [x] 2.3 Create `frontend/src/app/components/mobile-nav/` standalone component (<760px) — bottom tab bar + segmented sub-nav
- [x] 2.4 Add 760px responsive switching via CSS media queries and `BreakpointObserver` for any JS-conditional branches
- [x] 2.5 Highlight active route in indigo (`--app-primary`) using current router state
- [x] 2.6 Rewrite `frontend/src/app/app.html` to mount the shell
- [x] 2.7 Verify all existing routes (including out-of-handoff `realized-pnl`, accounting management routes) navigate correctly

## 3. UI primitives

- [x] 3.1 Create `components/ui/btn/` (variants `primary`/`secondary`/`ghost`, `disabled`, `loading`, `icon`, `click` output, ARIA + focus ring)
- [x] 3.2 Create `components/ui/tag/` (variants `neutral`/`accent`/`success`/`warning`/`danger`/`dividend`)
- [x] 3.3 Create `components/ui/seg-toggle/` (option array, arrow-key nav, `aria-pressed`, `change` output)
- [x] 3.4 Create `components/ui/bento/` (card surface with title slot + content slot, `.b-full` variant)
- [x] 3.5 Create `components/ui/pct-badge/` (signed pct, trend colours via `--app-trend-*`)
- [x] 3.6 Create `components/ui/side-tag/` (variants `buy` indigo / `sell` slate / `cash` violet)
- [x] 3.7 Create `components/ui/timeline/` (date-grouped rows with SideTag, primary, meta, trailing amount slots)
- [x] 3.8 Create `components/ui/file-chip/` (filename + parsed count + remove × emitting `remove`)
- [x] 3.9 Add Vitest specs for each primitive covering render + event emission + ARIA attributes

## 4. Portfolio dashboard (`portfolio`)

- [x] 4.1 Rewrite `components/portfolio/dashboard/dashboard.html` to bento grid with 4 KPI tiles (總市值, 未實現損益, XIRR, 本年度股利)
- [x] 4.2 Apply KPI value colour rules (未實現損益 → `var(--app-trend-positive/negative)`, 本年度股利 → `var(--app-dividend)`)
- [x] 4.3 Add `.b-full` bento tile containing net-worth `p-chart` line, header with title + `PctBadge` + `SegToggle` range tabs
- [x] 4.4 Implement range state (`1M`/`3M`/`YTD`/`1Y`/`5Y`, default `1Y`) — swap chart series, recompute period return, recompute XIRR tile
- [x] 4.5 Set `p-chart` `options.animation = false`; read line/area colours via `getComputedStyle` at draw time
- [x] 4.6 Subscribe to `AppearanceService` `dark$`/`gainLoss$` and call `chart.update('none')` on changes
- [x] 4.7 Implement expandable holdings rows (cost / dividend / XIRR detail)
- [x] 4.8 Update existing Vitest spec selectors for the new template; add tests for range selector and chart update on theme/convention flip

## 5. Stock transactions (`transactions`)

- [x] 5.1 Rewrite `components/portfolio/transaction-list/` template to use `Timeline` + `SideTag` (`buy`/`sell`)
- [x] 5.2 Group rows by trade date descending; format `qty × price` meta and `.tl-amt.buy/.sell` amounts
- [x] 5.3 Update Vitest spec selectors

## 6. Dividend records (`dividends`)

- [x] 6.1 Rewrite `components/portfolio/dividend-list/` template to include summary row (本年度累計股利 violet, 平均殖利率, 領取筆數)
- [x] 6.2 Add 即將除權息提醒 card grid above the timeline
- [x] 6.3 Render dividends in `Timeline` with `SideTag` variant `cash` and `.tl-amt.dividend` violet amounts
- [x] 6.4 Update Vitest spec selectors

## 7. CSV import (`import`)

- [x] 7.1 Rewrite `components/portfolio/import/` to `.imp-card` with 2 steps (dropzone → `FileChip`, `.preview-table`); no broker selector (backend auto-detects)
- [x] 7.2 Backend `_serialize_result` returns `csv_format` field (`cathay`|`generic`); frontend renders detected-format chip
- [x] 7.3 Wire dropzone parse to existing parser; show parsed-row count in FileChip
- [x] 7.4 Render preview table with buy/sell tags
- [x] 7.5 Footer with 取消 (resets card) and 確認匯入 N 筆 (calls existing importer + toast)
- [x] 7.6 Update Vitest spec selectors

## 8. Accounting analytics (`accounting-dash`)

- [x] 8.1 Rewrite `components/accounting/dashboard/` with expense doughnut `p-chart` + custom legend (colour swatch + category + pct + amount)
- [x] 8.2 Add category month-over-month change list with `PctBadge` deltas
- [x] 8.3 Add credit-card limit monitor block; over-limit uses `var(--c-red)` (cashflow, NOT `--app-trend-*`)
- [x] 8.4 Set doughnut `animation: false`; subscribe only to `dark$` (NOT `gainLoss$`) for chart updates
- [x] 8.5 Confirm no `--app-trend-*` tokens are used on this screen; cashflow colours are direct (`--c-green` / `--app-text-muted` / `--c-red`)
- [x] 8.6 Update Vitest spec selectors

## 9. Accounting transactions (`accounting`)

- [x] 9.1 Rewrite `components/accounting/transaction-list/` template — month navigator + summary row
- [x] 9.2 Add type-pills `SegToggle` filter + live search input
- [x] 9.3 Render `Timeline` grouped by date with cashflow colours (income green, expense neutral)
- [x] 9.4 Implement `TxnDialog` modal (`p-dialog`) with type `SegToggle` (支出/收入/信用卡) + amount/date/category/notes fields, required-field validation, calls existing accounting service on save
- [x] 9.5 Wire 新增交易 button to open `TxnDialog`
- [x] 9.6 Update Vitest spec selectors and add dialog open/save test

## 10. Settings (`settings`) — new route

- [x] 10.1 Create `components/settings/settings.ts` standalone component with `.set-card` shell
- [x] 10.2 Add appearance `.set-row` (label 外觀模式 + `SegToggle` 淺色/深色) bound to `AppearanceService.dark$` / `setDark()`
- [x] 10.3 Add gain/loss `.set-row` (label 漲跌顏色 + `SegToggle` 紅漲綠跌/綠漲紅跌 + live preview chip pair) bound to `AppearanceService.gainLoss$` / `setGainLoss()`; caption explains 台股 vs 歐美
- [x] 10.4 Register `/settings` route in `app.routes.ts`
- [x] 10.5 Add Vitest spec covering toggle interactions and `AppearanceService` calls

## 11. Inventory (`inventory`)

- [x] 11.1 Rewrite `components/item-list/` template to card grid (responsive, reflows to 1-col <760px)
- [x] 11.2 Add stock-status badges using `--app-success`/`--app-warning`/`--app-danger` (muted slate/violet — NOT trend red/green)
- [x] 11.3 Implement +/− quantity steppers with live low-stock recomputation; − disabled at 0
- [x] 11.4 Persist quantity changes via existing inventory service
- [x] 11.5 Add 只看低庫存 filter pill
- [x] 11.6 Update Vitest spec selectors and add stepper + filter tests

## 12. Shopping list (`shopping`)

- [x] 12.1 Rewrite `components/shopping-list/` to a centred labelled empty state (icon + title 採買清單 + caption noting not yet designed)
- [x] 12.2 Update Vitest spec selectors

## 13. Out-of-handoff route audit

- [x] 13.1 Visually verify `/portfolio/realized-pnl` with new tokens + shell; fix any hard-coded colour drift in its component scss
- [x] 13.2 Visually verify `/accounting/settings` (management-center)
- [x] 13.3 Visually verify `/accounting/cards`, `/accounting/categories`, `/accounting/recurring`
- [x] 13.4 Where any of these hard-codes old slate trend hex values, swap to `--app-success` / `--app-text-muted` (non-trend slate) to keep the original look

## 14. Validation + smoke

- [x] 14.1 Run `cd frontend && npm test` and resolve spec failures
- [x] 14.2 Run `cd frontend && npm run build` and confirm clean build
- [x] 14.3 Manual smoke: dark toggle, gain/loss toggle (verify chart + chips repaint, cashflow does NOT), range selector, TxnDialog open/save, qty steppers, CSV import flow
- [x] 14.4 Verify pre-paint script: hard reload with `hh-dark=1` and confirm no light-mode flash
- [x] 14.5 Verify responsive switch at 760px (dock ↔ mobile nav)
- [x] 14.6 Run `openspec validate redesign-dashboard-handoff --strict` and resolve any findings
