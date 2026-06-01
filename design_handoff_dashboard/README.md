# Handoff: Home Hub Dashboard (HH)

## Overview

This package documents the **Home Hub** web dashboard — a personal finance + household
helper that tracks a stock portfolio, day-to-day income/expenses, and household inventory.
It is the redesigned look-and-feel for the app that lives in
**`github.com/yonger0718/home-service-hub`** (the Angular app under `frontend/`).

The goal of this handoff is to **recreate these designs in the existing Angular codebase**,
upgrading the current screens to the new visual system (calm slate chrome, indigo action
accent, market-convention gain/loss colours, clean KPI tiles, dark mode + settings).

### ⚠️ Scope — read first

**IN scope** (the only pages that exist in the repo and that we are shipping):

| # | Screen | Route id | Title (zh-TW) |
|---|--------|----------|---------------|
| 1 | Portfolio dashboard | `portfolio` | 投資概覽 |
| 2 | Stock transactions | `transactions` | 股票交易紀錄 |
| 3 | Dividend records | `dividends` | 股利領取紀錄 |
| 4 | CSV import | `import` | 匯入 CSV |
| 5 | Accounting analytics | `accounting-dash` | 記帳分析 |
| 6 | Transactions (cashflow) | `accounting` | 交易紀錄 |
| 7 | Settings | `settings` | 設定 |
| 8 | Inventory | `inventory` | 庫存管理 |
| 9 | Shopping list | `shopping` | 採買清單 *(placeholder — not yet designed)* |

**OUT of scope — do NOT build:**
- ❌ **Marketing / landing / pricing ("selling") page.** It exists as a design exploration
  (`site/index.html` in the design system) but the product owner is **not adding it**. Ignore it.
- ❌ **Login / authentication / sign-up.** There is **no plan to add auth** to this system.
  Do not scaffold a login screen, guards, or session flows. The app opens straight to the
  dashboard shell.

## About the Design Files

The files in `design_refs/` are **design references created in HTML/React (inline Babel)** —
prototypes that show the intended look, layout, and interaction. **They are not production code
to copy verbatim.** The task is to **recreate them in the app's existing Angular + PrimeNG
environment**, using its established component patterns (`p-chart`, PrimeIcons, Angular
templates/services). The `.jsx` files are convenience mock-ups; translate their structure and
styling into Angular components.

The design tokens in `design_refs/colors_and_type.css` and the compiled styles in
`design_refs/ui_kits/hub-dashboard/kit.css` **are meant to be lifted directly** (as CSS custom
properties / SCSS) — they are the source of truth for colour, type, spacing, radius, and shadow.

## Fidelity

**High-fidelity (hifi).** Final colours, typography, spacing, radii, shadows, and interactions
are all specified. Recreate the UI pixel-faithfully using the codebase's PrimeNG components and
the tokens provided. Where a value isn't documented here, read it from `kit.css`.

---

## Design Tokens

All tokens live in `design_refs/colors_and_type.css` as CSS custom properties. Lift them as-is
(they already map to the production app's `--app-*` variable names in `frontend/src/styles.scss`).

### Colour — neutral foundation (light)
| Token | Value | Use |
|---|---|---|
| `--app-bg` | `#f1f3f6` | Page background (cool off-white) |
| `--app-surface` | `#fafcff` | Card / panel surface |
| `--app-surface-glass` | `rgba(250,252,255,0.8)` | Frosted nav & header (with `backdrop-filter: blur`) |
| `--app-surface-soft` | `#edf1f6` | Inset / secondary surface, segmented-control track |
| `--app-border` | `#d8dee7` | Hairline borders |
| `--app-text` | `#151821` | Primary text (near-black navy) |
| `--app-text-muted` | `#6b7280` | Secondary / label text |

### Colour — accents
| Token | Value | Use |
|---|---|---|
| `--app-accent` | `#1d2433` | **Neutral structural** emphasis only (logo, KPI borders). NOT interactive. |
| `--app-primary` | `#533afd` | **The one action accent (indigo).** Primary buttons, active nav, focus, links. Use sparingly. |
| `--app-primary-hover` | `#4434d4` | Primary hover |
| `--app-primary-press` | `#2e2b8c` | Primary pressed |
| `--app-primary-soft` | `rgba(83,58,253,0.10)` | Indigo soft fill |
| `--app-focus-ring` | `rgba(83,58,253,0.22)` | Focus ring (3–4px outer glow) |

### Colour — trend / market (⚠️ convention-driven — see below)
| Token | Value | Use |
|---|---|---|
| `--c-red` | `#e5484d` | Base red |
| `--c-green` | `#1f9d6b` | Base green |
| `--app-trend-positive` | = red (default) | Gains. Resolves via convention. |
| `--app-trend-negative` | = green (default) | Losses. Resolves via convention. |
| `--app-buy` | `#533afd` | Stock **purchases** = indigo |
| `--app-sell` | `#51607a` | Sell **proceeds** = neutral slate |
| `--app-dividend` | `#7e78a8` | Dividends = muted violet |

Each colour has an `*-soft` companion (~8–16% alpha) for chip/badge backgrounds.

`--app-success #5f7f98` / `--app-warning #7b87a8` / `--app-danger #7b6c9c` are muted slate/violet
**non-trend status** colours (inventory stock state, etc.) — keep them muted; they are not red/green.

### Radius
`--radius-sm 8px` (pills/tags) · `--radius-md 12px` (cards, logo, mini-selects) ·
`--radius-lg 16px` (list items, search) · `--radius-xl 20px` (bento tiles) ·
`--radius-sheet 24px` (mobile sheets) · `--radius-pill 999px`.

### Spacing (rem scale)
`--space-1 .25` · `-2 .5` · `-3 .75` · `-4 1` · `-5 1.25` · `-6 1.5` · `-8 2`.

### Elevation
`--app-card-shadow: 0 10px 30px rgba(18,26,40,.045)` (default card) ·
`--app-raised-shadow: 0 16px 36px rgba(18,26,40,.09)` (dialogs/hover) ·
`--app-inset-line: rgba(255,255,255,.62)` — a `box-shadow: inset 0 1px 0` top highlight on every card.

### Typography (system stack — no web font)
`--font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, …`. Numbers use
`font-variant-numeric: tabular-nums`.

| Token | Size | Weight | Use |
|---|---|---|---|
| `--fs-display` | 28px | 800 | Page hero title, tracking −0.03em |
| `--fs-value` | 32px | 800 | Big KPI numbers |
| `--fs-h2` | 20px | 800 | Section titles, tracking −0.02em |
| `--fs-h3` | 18px | 700 | Header title / list main text |
| `--fs-card-title` | 16px | 700 | UPPERCASE card titles, tracking .05em |
| `--fs-body` | ~15px | 500 | Body (baseline leans medium) |
| `--fs-sm` | ~13.6px | — | Secondary |
| `--fs-label` | 12px | 700 | UPPERCASE tracked labels (.08em) |
| `--fs-micro` | 11px | — | Tags, captions |

### Motion
`--ease-ios: cubic-bezier(.2,.8,.2,1)` (springy, for toggles/cards) ·
`--ease-standard: cubic-bezier(.4,0,.2,1)` · `--dur-fast .2s` · `--dur-base .3s`.

---

## Two cross-cutting features (implement as global app state)

These touch every screen — build them once and let screens consume tokens.

### 1. Dark mode
Toggle class `app-dark-mode` on a top-level wrapper (or `<html>`). The dark block in
`colors_and_type.css` re-defines every `--app-*` token; **components never hard-code colour**, so
they flip automatically. Persist the choice (`localStorage` key `hh-dark`, `"1"`/`"0"`) and apply
it **before first paint** to avoid a flash. In Angular, do this in an `AppearanceService` +
`APP_INITIALIZER` (or an inline script in `index.html`).

### 2. Gain/loss colour convention (台股 vs 歐美)
Set `data-gainloss` on the same wrapper:
- `data-gainloss="asian"` → **紅漲綠跌** (red up / green down) — **the DEFAULT** (Taiwan/Asian market).
- `data-gainloss="western"` → **綠漲紅跌** (green up / red down).

`--app-trend-positive/negative` resolve from this attribute, so **all gains/losses across portfolio,
holdings, stock trades, dividends, and analytics recolour from one switch.** Persist as `hh-gainloss`.

> **Important nuance:** cashflow (income vs expense in 記帳/分析) is **decoupled** from this market
> toggle. Income is always green, expense neutral, and a *spending increase* always reads red —
> regardless of the stock convention. Don't wire cashflow colour to `data-gainloss`.

Both controls are exposed on the **Settings** screen (#7).

---

## Screens / Views

> Shell for all screens: a fixed left **dock** (desktop ≥760px) grouped Supplies / Portfolio /
> Accounting with sub-items, and a frosted glass **top header**. Below 760px the dock becomes a
> **bottom tab bar + segmented sub-nav**. See `components.jsx` (`Dock`, `MobileNav`) and the
> `.hub-*`, `.dock-*`, `.mobile-nav-*` rules in `kit.css`.

### 1. Portfolio dashboard — 投資概覽 (`portfolio`)
- **Purpose:** at-a-glance net worth, returns, and holdings.
- **Layout:** responsive bento grid (`.bento-grid`, 6-col → 4-col → 1-col). Top row = **clean white
  KPI tiles** (`.bento`): 總市值, 未實現損益, 年化報酬率 XIRR, 本年度股利. **Colour lives in the
  numbers only** (`.value.up` = trend-positive, `.value.dividend` = violet) — the tiles themselves
  stay white, no gradient fills, no coloured left-borders.
- **淨值走勢 net-worth chart** (`.b-full`): a Chart.js line chart with a header (`.chart-head`)
  containing the title, a period-return **`.pct-badge`**, and a **time-range segmented selector**
  (`.seg-toggle.range-tabs`): **1M / 3M / YTD / 1Y / 5Y** (default **1Y**). Selecting a range
  swaps the chart series **and** updates the XIRR tile + period badge together. The line colour =
  `--app-trend-positive/negative` depending on whether the period gained or lost; soft matching
  area fill. See `PortfolioDashboard` in `screens-finance.jsx` (`NW_RANGES` holds per-range series).
- **Holdings list:** expandable rows — click a stock to reveal cost / dividend / XIRR detail.
- **Chart.js note:** set `animation: false` (PrimeNG `p-chart` is a Chart.js wrapper; throttled
  iframes/SSR leave animated canvases blank). Read chart colours from the CSS vars at draw time so
  they respect theme + convention.

### 2. Stock transactions — 股票交易紀錄 (`transactions`)
- Date-grouped **timeline** (`.timeline` / `.tl-*`) of buy/sell trades. Each row: a `.side-tag`
  (`.buy` indigo / `.sell` slate), stock name + code, qty × price meta, and amount
  (`.tl-amt.buy` / `.tl-amt.sell`). See `StockTransactionList` in `screens-records.jsx`.

### 3. Dividend records — 股利領取紀錄 (`dividends`)
- **Summary row** (本年度累計股利 in violet, 平均殖利率, 領取筆數).
- **即將除權息提醒** grid of upcoming ex-dividend cards.
- Dividend **timeline**: `.side-tag.cash` (violet) + per-share × qty meta + `.tl-amt.dividend`
  (violet) amount. See `DividendList` in `screens-dividends-import.jsx`.

### 4. CSV import — 匯入 CSV (`import`)
- A **3-step card** (`.imp-card`): (1) broker-format `<select>` (永豐金/元大/國泰/富邦/通用),
  (2) dropzone → on file, a `.file-chip` showing filename + parsed count, (3) a **preview table**
  (`.preview-table`) of parsed rows with buy/sell tags. Footer has 取消 / 確認匯入 N 筆. The flow is
  interactive in the mock (`ImportCSV` in `screens-dividends-import.jsx`); wire to the real importer.

### 5. Accounting analytics — 記帳分析 (`accounting-dash`)
- Expense **doughnut** (Chart.js) with a custom legend, a category-change list, and a credit-card
  limit monitor. `AccountingDash` in `screens-finance.jsx`. **Cashflow colours are convention-
  independent** (see note above).

### 6. Transactions (cashflow) — 交易紀錄 (`accounting`)
- Month navigator, summary row, **type-pills** segmented filter + live search, and a date-grouped
  expense/income timeline. **新增交易** opens a working modal dialog (`TxnDialog`, segmented control
  + form fields). `StockTransactionList`/`TransactionList` + `TxnDialog` in `screens-records.jsx`.

### 7. Settings — 設定 (`settings`)
- `.set-card` with `.set-row`s, each a label (icon + title + description) and a `.seg-toggle`:
  - **外觀模式:** 淺色 / 深色 → dark mode (feature #1).
  - **漲跌顏色:** 紅漲綠跌 / 綠漲紅跌 → gain/loss convention (feature #2), with a **live preview**
    chip pair. Caption explains the 台股 vs 歐美 difference.
  - See `Settings` in `screens-settings.jsx`. Both persist and apply app-wide immediately.

### 8. Inventory — 庫存管理 (`inventory`)
- Responsive **card grid** with stock-status badges and working **+/− quantity steppers** that
  recompute low-stock (低庫存) state live; a "只看低庫存" filter pill. `InventoryGrid` in
  `screens-records.jsx`.

### 9. Shopping list — 採買清單 (`shopping`)
- **Placeholder only** — resolves to a labelled empty state in the mock. Not yet designed; leave a
  clean empty state or skip until designs exist. (Do not invent a design.)

---

## Interactions & Behavior

- **Nav:** dock/sub-nav highlights the active route in indigo (`.dock-item.active`,
  `.m-tab.active`). Routing is by `id` (see the `NAV` array in `components.jsx`).
- **Range selector:** click a range → swap chart dataset + XIRR + period badge. State lives on the
  portfolio component (`range`, default `"1Y"`).
- **Holdings rows / inventory steppers / txn dialog / CSV stepper:** all interactive; recreate the
  state transitions shown in the `.jsx` files.
- **Hover:** cards lift slightly (`translateY(-1px/-2px)`) and tighten border; buttons darken to
  the `-hover` token. **Focus:** 3–4px `--app-focus-ring` glow + indigo border. Transitions use
  `--ease-ios`/`--ease-standard` at `--dur-fast`.
- **Responsive:** desktop dock ↔ mobile bottom-nav swaps at **760px**; bento grid reflows 6→4→1.

## State Management

- **Global:** `dark: boolean` (`hh-dark`) and `gainLoss: "asian"|"western"` (`hh-gainloss`),
  persisted to `localStorage`, applied to the root wrapper as `app-dark-mode` class + `data-gainloss`
  attribute. In Angular, an injectable `SettingsService` with `BehaviorSubject`s is the natural fit.
- **Portfolio:** `range` (selected time range), `expandedHolding` (which row is open).
- **Per-screen:** transaction filters/search, dialog open state, CSV step + parsed rows, inventory
  quantities. Data-fetching is faked in the mocks — wire to the app's existing services/API.

## Assets

- `design_refs/assets/logo-lockup.svg` — full HH logo lockup.
- `design_refs/assets/app-icon.svg` — square app icon (use for the dock logo, `--radius-md`).
- **Icons:** **PrimeIcons** (`pi pi-*`) — already used by the app (PrimeNG). Classes referenced:
  `pi-box, pi-shopping-cart, pi-chart-line, pi-list, pi-percentage, pi-upload, pi-chart-pie,
  pi-wallet, pi-cog, pi-sun, pi-moon, pi-arrow-up, pi-arrow-down, pi-cloud-upload, pi-file,
  pi-times, pi-check, pi-plus, pi-calendar`. No emoji. No custom SVG icon set.
- **Charts:** Chart.js (via PrimeNG `p-chart`).

## Files in this bundle

```
design_handoff_dashboard/
├── README.md                         ← this file (self-sufficient spec)
└── design_refs/
    ├── colors_and_type.css           ← design tokens (lift directly)
    ├── assets/                        ← logo + app icon
    └── ui_kits/hub-dashboard/
        ├── index.html                ← interactive app shell (open in a browser to explore)
        ├── kit.css                   ← compiled component styles, keyed to tokens
        ├── components.jsx            ← Dock, MobileNav, Btn/Tag, ntd()/pct() formatters, NAV array
        ├── screens-finance.jsx       ← PortfolioDashboard (+ range selector), AccountingDash, ChartJS
        ├── screens-records.jsx       ← StockTransactionList, InventoryGrid, TxnDialog
        ├── screens-dividends-import.jsx ← DividendList, ImportCSV
        └── screens-settings.jsx      ← Settings (appearance + gain/loss)
```

**To explore the prototype:** open `design_refs/ui_kits/hub-dashboard/index.html` in a browser and
click through the dock. Resize below 760px to see the mobile shell.

## Source repo

Original app: **https://github.com/yonger0718/home-service-hub** (Angular, under `frontend/`).
The production token names already match (`frontend/src/styles.scss` defines the `--app-*` vars) —
this redesign updates their *values* and adds the convention + dark-mode layers. Note that
`styles.scss` still carries the **old** non-red/green trend values with a deprecation comment;
use `colors_and_type.css` here as the source of truth.
