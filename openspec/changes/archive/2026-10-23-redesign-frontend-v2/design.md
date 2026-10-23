## Context

The owner reviewed a redesign of the shell, the portfolio pages, accounting and settings on a design canvas (Claude Design, 2026-10-22). The canvas source files are copied into `design_handoff_v2/design_refs/` for reference; `design_handoff_v2/README.md` lists every artboard. The canvas uses illustrative numbers, but they are internally consistent (dashboard totals = holdings + cash, 已記錄股利 = 已確認收款 + 舊資料, realized rows match the trades).

Direction: MOZE-like information density and list rhythm (coloured category squares, grouped lists, one big number per card, a fixed "+" for 記一筆), rendered **only with the existing tokens** of `frontend/src/styles.scss` — slate chrome, indigo `--app-primary`, `--c-red` / `--c-green` resolved through `data-gainloss`, `.app-dark-mode` for dark. No token is added or changed.

What already exists and is kept: the 760 / 1024 px breakpoints of the accounting layout, the accounting FAB and `N` shortcut (`accounting-layout`, `keyboard-shortcuts.ts`), the centre "+" on the phone tab bar, the two-dataset net-worth chart (總資產 filled to 總市值), `AppearanceService` (`hh-dark`, `hh-gainloss`), `NAV_GROUPS` in `shell/navigation.ts`.

## Goals / Non-Goals

**Goals**
- One navigation model driven by `NAV_GROUPS` for both desktop and phone, with badges and hideable groups.
- Exactly one 記一筆 entry point per device class.
- Make pending work visible from anywhere (counts and dots), without new endpoints.
- Bring 已實現損益, 現金帳戶 and 匯入 to the same visual language as the rest.

**Non-Goals**
- Backend, API, schema or migration changes. A badge whose count no existing endpoint can provide is omitted, not added to an API.
- 物資 screens (hidden only).
- New tokens, fonts, icon set.

## Decisions

### D1: Sidebar with three widths, labelled, built from `NAV_GROUPS`

| Viewport | Sidebar |
|---|---|
| ≥ 1280 px | expanded 248 px: logo, collapse button, groups with headings, labelled items |
| 760–1279 px | icon rail 72 px: logo, expand button, divider per group, icons, tooltip on hover/focus |
| < 760 px | hidden; bottom tab bar + in-page sub-nav |

A manual collapse at ≥ 1280 px is persisted. The frosted top header is removed: the logo lives in the sidebar and each page owns its `<h1>` and actions, which frees ~60 px of height and removes a duplicated title.

*Alternative:* keep the icon-only dock. Rejected — labels were the main readability complaint and the dock cannot show counts.

### D2: Auto-collapse on two-pane pages

When a two-pane page (accounting at ≥ 1024 px) opens its right pane, the sidebar renders as the rail; closing the pane restores the previous width. If the owner expands the sidebar while the pane is open, that wins until the pane closes. Rationale: sidebar 248 + list + 340 px pane does not fit comfortably at 1280–1440 px. Can be turned off in Settings.

### D3: Foldable groups; the active group never folds

Folded state is persisted per group. A folded group shows the sum of its children's counts, or a dot if any child has a dot, so folding never hides pending work.

### D4: Badges — counts on desktop and sub-nav, dots on the phone tab bar

`NavItem` gets an optional `badge: { count?: number; dot?: boolean; label: string }` (label is the accessible text). Sources, all existing:

| Item | Badge | Source |
|---|---|---|
| 記帳記錄 | count | reminder count already shown on the timeline bell |
| 記帳 · 帳戶 | count | 待確認 count from the latest MOZE import (`accounts` page) |
| 股利 | count | pending (有效待收) dividends from the dividends API |
| 匯入 | dot | latest import chain from `GET /imports/recalc/status` is failed, or the last import attempt on this device failed (kept in `localStorage`) |
| 採買清單 | count | open shopping-list items (only when 物資 is visible) |

Phone tab bar: a dot on a group tab when any child has a badge, never on the active tab. Numbers appear on the in-page sub-nav (財務: 記錄 / 帳戶 / 記帳設定 segmented control; 投資: horizontally scrolling chips). One switch in Settings hides all badges.

### D5: One 記一筆 per device

- < 760 px: the centre "+" of the tab bar (exists).
- ≥ 760 px: a 56 px floating button bottom-right on **every** route, tooltip `記一筆 N`, hidden while the entry form is open (current `showFab` rule). On a two-pane page with the pane open it anchors to the bottom-right of the list column so it never covers the pane.
- `N` opens the entry form from any page (today only from accounting list pages).
- No 記一筆 button in the sidebar or in page headers. Page-specific actions (新增交易, 新增股利, 新增帳戶) stay in page headers in the soft-primary style so they never compete with the FAB.

*Alternatives considered:* sidebar button (far from the list, tiny in rail mode) and header button (scrolls away, accounting only). The owner picked the floating button.

### D6: Hideable groups and the phone tab bar

`系統` is always visible; other groups can be hidden in Settings (`物資` hidden by default for this owner). The tab bar is `[groups…, 設定]` with "+" in the middle; when that list has an odd length, 設定 leaves the tab bar and becomes a gear button at the top-right of each page, keeping "+" centred. With 物資 hidden the tab bar is `投資 · ＋ · 財務`.

### D7: Theme `system`

`AppearanceService` gains `theme$: 'light' | 'dark' | 'system'`; `dark$` stays (derived). Stored as `hh-theme`; a stored `hh-dark` is migrated once (`'1'` → `dark`, `'0'` → `light`). `system` follows `prefers-color-scheme` live. The pre-paint script reads `hh-theme` first.

### D8: Navigation preferences live next to appearance

New `NavigationPreferencesService` (`providedIn: 'root'`) with signals for `hiddenGroups`, `sidebarDefault` (`expanded` | `rail`), `autoCollapse`, `showBadges`, `foldedGroups`, persisted under `hh-nav-*` keys. Device-local, like appearance.

### D9: Portfolio screens

- **Dashboard**: 總資產 hero (number, 今日 change pill, 股票/現金 composition bar, 待收股利 listed separately — it is not cash). KPI row: 總市值 (成本 below), 預估含息損益 (價差 · 股利 · 待收 breakdown line), 年化報酬率 XIRR, 已記錄股利 (`含舊資料 · 不代表已核實收款`). Chart: series names 帳戶總值 (`--app-accent`) and 持股淨值 (trend colour), gap shaded `--app-accent-soft` and labelled 交割戶現金 in the legend, hover tooltip with both values and cash. Beside it: 持股配置 bar + 待收股利 list. Holdings rows unchanged in data, restyled as list rows.
- **交易紀錄**: summary cards for the active filter; rows grouped by month (month heading shows 買 / 賣 totals), day column on the left.
- **股利**: vocabulary from `CONTEXT.md`. Pending rows have a 確認收款 button that expands an inline form (實際收款日, 收款帳戶, 確認已收到並入帳) with the existing warning text; it calls the existing `confirm-receipt` endpoint.
- **已實現損益**: monthly bars computed client-side from the realized events already returned for the selected range; future months greyed.
- **現金帳戶**: ≥ 1024 px list (left, ≤ 360 px) + detail (right) on `/portfolio/accounts/:id`; `/portfolio/accounts` selects the first account. Running balance is computed client-side from the paged rows when the API does not return it; if paging makes that impossible the column is omitted.
- **匯入**: one page at `/portfolio/import` with a type switch (交易 → `/imports/transactions`, 股利 → `/imports/dividends`, 券商對帳單 → `/imports/csv`). `/portfolio/import-broker` redirects to `/portfolio/import?type=broker`.

### D10: Category colours

Category squares use the colours the owner already set in 記帳設定; the canvas uses muted placeholders. Stock squares on portfolio pages use a fixed five-colour series that starts with `--app-primary`; in dark mode the lighter variants from the canvas.

## Risks / Trade-offs

- Removing the top header changes every page's layout → done in the shell PR first, with every page getting its `<h1>`; screenshots per route in that PR.
- Badge counts add requests on app start → fetched once on start and on focus, cached in the navigation service; failures hide the badge.
- The import-failure dot is partly device-local → acceptable for a single-owner app; documented in the settings text.

## Migration Plan

Follow-up PRs, each releasable alone:
1. Shell: sidebar, rail, auto-collapse, FAB + `N` app-wide, header removal, `NavigationPreferencesService`, badges.
2. Appearance `system` + Settings page.
3. Dashboard.
4. 交易紀錄 + 股利.
5. 已實現損益 + 現金帳戶.
6. Unified 匯入 + broker-import redirect.
7. Accounting timeline category bar.

## Open Questions

- Should the 匯入 dot also cover the IB Flex import? (Proposed: yes, same status endpoint.)
