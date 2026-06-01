## Why

The Angular dashboard ships with a muted slate visual system whose tokens carry a deprecation comment in `frontend/src/styles.scss` and whose components do not satisfy the redesign delivered in `design_handoff_dashboard/` (calm slate chrome, indigo action accent, market-convention gain/loss colours, bento KPI tiles, dark mode, settings hub). The handoff is high-fidelity and pixel-faithful — recreating it now unblocks the redesigned look-and-feel across portfolio, accounting, and inventory workflows in a single coherent update rather than per-screen drift.

## What Changes

- **BREAKING**: Replace token values in `frontend/src/styles.scss` with `design_refs/colors_and_type.css` verbatim — light + dark blocks, indigo `--app-primary` family, market `--c-red`/`--c-green`, `--app-buy`/`--app-sell`/`--app-dividend`, `--app-trend-*` resolved via `[data-gainloss]` attribute selectors. Drop deprecated slate trend values.
- Add `AppearanceService` (Angular injectable) holding `dark$` and `gainLoss$` `BehaviorSubject`s. Persist to `localStorage` (`hh-dark`, `hh-gainloss`). Default theme follows OS `prefers-color-scheme`; default convention is `asian` (紅漲綠跌). Apply `.app-dark-mode` class + `data-gainloss` attribute on `<html>` via `APP_INITIALIZER` plus pre-paint inline script in `index.html` to eliminate flash.
- Replace `app.html` chrome with new shell: frosted top header + left `Dock` (≥760px) / bottom `MobileNav` + segmented sub-nav (<760px). Active route highlighted in indigo. Route ids match handoff `NAV` array.
- Introduce stateless UI primitives consumed by every screen: `Btn`, `Tag`, `SegToggle`, `Bento`, `PctBadge`, `SideTag`, `Timeline`, `FileChip`.
- Redesign 9 screens to match handoff structure:
  - `portfolio` — bento KPI grid (總市值, 未實現損益, XIRR, 本年度股利) + `p-chart` net-worth line with 1M/3M/YTD/1Y/5Y range selector (default 1Y) + `.pct-badge` period return + expandable holdings rows.
  - `transactions` — date-grouped buy/sell timeline.
  - `dividends` — summary row + 即將除權息 grid + violet timeline.
  - `import` — 3-step CSV card (broker select → dropzone+file-chip → preview table).
  - `accounting-dash` — expense doughnut + custom legend + category-change list + credit-card limit monitor. **Cashflow colours hard-coded, decoupled from `data-gainloss`.**
  - `accounting` — month navigator + summary + type-pill filter + search + date-grouped timeline + `TxnDialog` modal.
  - `inventory` — card grid + +/− qty steppers + 低庫存 filter pill.
  - `shopping` — labelled empty state (no designed layout yet).
- Add top-level `/settings` route (handoff screen #7) with appearance + gain/loss seg-toggles and live preview chip pair.
- Keep out-of-handoff routes (`/portfolio/realized-pnl`, `/accounting/settings` management-center, `/accounting/cards`, `/accounting/categories`, `/accounting/recurring`) — restyled via token inheritance only, no layout redesign.
- `p-chart` (Chart.js) instances set `animation: false`, read colours via `getComputedStyle(documentElement).getPropertyValue('--app-...')` at draw time, and subscribe to `AppearanceService` to call `chart.update()` on theme/convention flips.
- Icons: PrimeIcons only — no emoji, no custom SVG icon set.
- **Explicit non-goals**: no auth/login scaffolding, no marketing/landing page, no new backend API endpoints.

## Capabilities

### New Capabilities

- `frontend-design-system`: Design tokens (colors_and_type.css lifted verbatim), light/dark theming, gain/loss convention attribute selectors, typography, spacing, radius, elevation, motion.
- `frontend-app-shell`: Top frosted header, left dock (≥760px), bottom mobile nav (<760px), segmented sub-nav, active-route highlighting, responsive breakpoint behaviour.
- `frontend-appearance-service`: Dark mode + gain/loss state, localStorage persistence, OS preference initialization, pre-paint flash prevention, observable streams for components.
- `frontend-ui-primitives`: Reusable stateless components (Btn, Tag, SegToggle, Bento, PctBadge, SideTag, Timeline, FileChip) keyed to tokens.
- `frontend-portfolio-dashboard`: Bento KPI tiles, net-worth Chart.js line with range selector (1M/3M/YTD/1Y/5Y default 1Y), period return badge, expandable holdings rows.
- `frontend-stock-transactions`: Date-grouped buy/sell timeline view of stock trades.
- `frontend-dividend-records`: Summary row, upcoming ex-dividend grid, violet dividend timeline.
- `frontend-csv-import`: 3-step interactive card (broker select, dropzone with file-chip, preview table) wired to existing importer.
- `frontend-accounting-analytics`: Expense doughnut + custom legend + category-change list + credit-card limit monitor with cashflow-convention-decoupled colours.
- `frontend-accounting-transactions`: Month navigator, summary, type-pill filter, search, date-grouped timeline, TxnDialog modal.
- `frontend-settings`: Top-level settings screen with appearance and gain/loss seg-toggles plus live preview.
- `frontend-inventory`: Card grid with stock-status badges, +/− quantity steppers recomputing low-stock state, 低庫存 filter pill.
- `frontend-shopping-list`: Labelled empty-state placeholder route.

### Modified Capabilities

(none — no existing spec covers the frontend; out-of-handoff routes inherit tokens implicitly and are not spec-tracked)

## Impact

- **Code**:
  - `frontend/src/styles.scss` (token rewrite + dark block + `[data-gainloss]` selectors)
  - `frontend/src/index.html` (pre-paint inline script)
  - `frontend/src/app/app.html` (shell wrapper)
  - `frontend/src/app/app.routes.ts` (+`/settings`)
  - new `frontend/src/app/services/appearance.service.ts`
  - new `frontend/src/app/components/shell/`, `components/dock/`, `components/mobile-nav/`
  - new `frontend/src/app/components/ui/` directory (primitives)
  - rewrite of templates + scss for 9 existing screen components
- **Dependencies**: none added — Chart.js arrives via existing `p-chart`/PrimeNG; PrimeIcons already present.
- **APIs**: none — data flows reuse existing services (`PortfolioService`, `AccountingService`, `InventoryService`, etc.).
- **Tests**: Vitest component specs will need selector + template-shape updates; logic unchanged. No backend test impact.
- **Assets**: copy `design_refs/assets/logo-lockup.svg` + `app-icon.svg` into `frontend/src/assets/` for dock + header logo.
