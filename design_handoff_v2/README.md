# Handoff: Home Hub redesign v2

Design reference for OpenSpec change `openspec/changes/redesign-frontend-v2/` (read its `design.md` for the decisions and `specs/` for the requirements). Approved by the owner on 2026-10-22. **Nothing in this folder is production code.**

## What this is

A MOZE-inspired pass over the shell, the portfolio pages, accounting and settings, drawn on a Claude Design canvas. It keeps the first handoff's visual system (`design_handoff_dashboard/`, now `frontend/src/styles.scss`) and changes layout, navigation and information density.

`design_refs/*.dc.html` are the canvas artboards' source. They need the canvas runtime (`support.js`) to render, so they will not open as plain pages; read them for structure, spacing, copy and the sample data in each file's `renderVals()`. Every colour in them is written as a theme lookup (`{{t.*}}`) whose light and dark values are copied from `styles.scss`. Each artboard has a `dark` prop; the canvas also shows a dark copy of every screen. Ask the owner for the canvas link to see them rendered.

## Artboards

| File | Screen | Route | Size |
|---|---|---|---|
| `Main.dc.html` | 投資概覽 + sidebar (expanded) | `/portfolio` | 1440 fluid |
| `PortfolioPhone.dc.html` | 投資概覽 | `/portfolio` | 390 |
| `PfTransactions.dc.html` / `PfTransactionsPhone.dc.html` | 交易紀錄 | `/portfolio/transactions` | 1440 / 390 |
| `PfDividends.dc.html` / `PfDividendsPhone.dc.html` | 股利 (inline 確認收款) | `/portfolio/dividends` | 1440 / 390 |
| `PfRealized.dc.html` | 已實現損益 | `/portfolio/realized-pnl` | 1440 |
| `PfCash.dc.html` | 現金帳戶 (master-detail) | `/portfolio/accounts/:id` | 1440 |
| `PfImport.dc.html` | 匯入 (unified) | `/portfolio/import` | 1440 |
| `AccountingDesktop.dc.html` | 記帳 timeline + detail pane, sidebar auto-collapsed | `/accounting/entries/:id` | 1440 |
| `TimelinePhone.dc.html` | 記帳 timeline | `/accounting` | 390 |
| `AccountsPhone.dc.html` | 記帳帳戶 | `/accounting/accounts` | 390 |
| `EntryPhone.dc.html` | 記一筆 (working keypad) | `/accounting/entry` | 390 |
| `SettingsDesktop.dc.html` / `SettingsPhone.dc.html` | 設定 | `/settings` | 1440 / 390 |
| `SidebarProto.dc.html` | Sidebar prototype: collapse, fold, auto-collapse, FAB move | — | 1280 |
| `SidebarStates.dc.html` | Expanded / folded / rail side by side | — | 1000 |
| `MobileNavPhone.dc.html` | Phone sub-nav and tab dots | — | 390 |

## Global rules

- **Tokens only.** Ground `--app-bg`, cards `--app-surface` + `--app-card-shadow`, lines `--app-border`, text `--app-text` / `--app-text-muted`, action `--app-primary`, structure `--app-accent`, gains/losses `--app-trend-*` via `data-gainloss`, income/expense via the 支出收入顏色 preference, dividends `--app-dividend`, buy/sell `--app-buy` / `--app-sell`.
- **Type.** System stack (the canvas adds Noto Sans TC only so CJK renders the same everywhere). Page title 28/800 −0.03em; card title 18/800; big numbers 22–44/800; labels 12/700 +0.08em; body 15/500; all numbers `tabular-nums`.
- **Radius.** Cards 20, list cards 16, controls 12, segmented thumbs 9, pills 999.
- **Lists.** 40 px coloured square + title/subtitle + right-aligned amount, ≥ 56 px rows, hairline dividers. Category squares use the owner's category colours; stock squares a fixed five-colour series starting at `--app-primary`.
- **Controls.** Segmented control = `--app-surface-soft` track, raised surface thumb. Switch 52×32, `--app-primary` when on. Page-specific create buttons use the soft-primary style; 記一筆 is the only solid floating action.
- **Badges.** Counts: `--app-primary` pill with white text. Attention: 8 px `--c-red` dot. Phone tab bar: dots only.

## Interaction notes

- Sidebar: expanded ≥ 1280, rail 760–1279, auto-collapse while a two-pane page has its pane open (see `SidebarProto`).
- 記一筆: phone centre "+"; ≥ 760 a floating button on every page, tooltip `記一筆 N`, slides to the list column when a pane is open.
- Hidden groups and the tab bar parity rule: see `SettingsPhone` (tab bar preview).
- Net-worth chart (`Main`): two series, shaded gap = cash, hover tooltip.

## Illustrative content

All numbers, names and dates are sample data chosen to be internally consistent. The chart range options on the canvas include 6M; the implementation keeps 1M / 3M / YTD / 1Y / 5Y. The canvas shows a 跟隨系統 theme and a 導覽 settings section that do not exist yet; both are specified in the change.

## Contrast

On the light surface `#fafcff`: `--c-red` 3.8:1, `--c-green` 3.4:1, `--app-dividend` 4.0:1 — fine for large numbers, below 4.5:1 for 12–13 px text. In dark mode white on `--app-primary` (`#8b7dff`) is 3.2:1. The canvas keeps the existing tokens; the implementation should consider darker text variants for small trend-coloured text and dark text (or a darker fill) on dark-mode primary buttons. Tracked as task 8.3 of the change.

Measured (PR 7, WCAG 2.x, gated by `frontend/src/app/theme/contrast.spec.ts`): small (≤ 13 px) trend-coloured text now uses ink tokens — light `--c-red-ink` `#c53a3f` 5.04 / 4.57:1 and `--c-green-ink` `#177a55` 5.17 / 4.69:1 on `#fafcff` / `#edf1f6`; dark keeps `#ff6369` 5.90 / 5.17:1 and `#43c98a` 8.11 / 7.12:1 on `#171c24` / `#202733`; large figures keep `--c-red` / `--c-green`.
Dark-mode primary `app-btn` fills with `--app-primary-ink-bg` `#6c5ce7` at rest and on hover: white on it is 4.86:1 (was 3.24:1 on `#8b7dff`, 2.72:1 on the `#9a8ffd` hover).
