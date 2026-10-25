# frontend-app-shell Specification

## Purpose
TBD - created by archiving change redesign-dashboard-handoff. Update Purpose after archive.

## Requirements

### Requirement: Responsive shell — dock ≥760px, mobile nav <760px

The application SHALL render a labelled left sidebar on viewports ≥ 760px and a bottom tab bar plus in-page sub-nav on viewports < 760px. The breakpoint MUST be 760px exactly. The sidebar SHALL be built from `NAV_GROUPS` and SHALL have two widths: **expanded** (248px: logo lockup, collapse button, group headings, icon + label per item) and **rail** (72px: logo icon, expand button, a divider per group, icon per item with a tooltip on hover and focus). At ≥ 1280px the sidebar SHALL use the owner's stored default (expanded unless set to rail); from 760px to 1279px it SHALL always be the rail. The bottom tab bar SHALL have a raised centre "+" that navigates to `/accounting/entry` from any route; it is the primary action of the app on phones. The accounting group's sub-nav SHALL be 記錄 (`/accounting`), 帳戶 (`/accounting/accounts`) and 記帳設定 (`/accounting/settings`); the portfolio group's sub-nav SHALL be horizontally scrolling chips 概覽 / 交易 / 股利 / 已實現 / 現金 / 匯入.

#### Scenario: Desktop renders dock
- **WHEN** the viewport width is 1440px and no sidebar default is stored
- **THEN** the sidebar SHALL be 248px wide with group headings and item labels
- **AND** the bottom tab bar SHALL be hidden

#### Scenario: Narrow desktop renders the rail
- **WHEN** the viewport width is 1100px
- **THEN** the sidebar SHALL be the 72px rail
- **AND** focusing an item SHALL show its label in a tooltip

#### Scenario: Mobile renders bottom nav
- **WHEN** the viewport width is < 760px
- **THEN** the bottom tab bar SHALL be visible with "+" in the centre
- **AND** the current group's sub-nav SHALL appear at the top of the page
- **AND** the sidebar SHALL be hidden

#### Scenario: Centre plus opens the entry form
- **WHEN** the viewport width is < 760px and the "+" in the tab bar is tapped from the 投資 group
- **THEN** the router navigates to `/accounting/entry`

### Requirement: Active route highlighted in indigo

The shell SHALL highlight the active sidebar item, rail item, phone tab and sub-nav entry using `var(--app-primary)` (indigo text on `--app-primary-soft`; the expanded item's icon square filled with `--app-primary`). Active state is driven by the current Angular route via `navItemForUrl`. Route ids MUST match `NAV_GROUPS` in `shell/navigation.ts`. The `accounts` id SHALL cover `/portfolio/accounts` and `/portfolio/accounts/:id`; the accounting `accounting` id SHALL cover `/accounting/accounts` and `/accounting/accounts/:id`. On phones the pushed `/settings` screen SHALL highlight no tab.

#### Scenario: Navigating updates active highlight
- **WHEN** the user navigates from `/portfolio` to `/portfolio/transactions`
- **THEN** the 交易紀錄 item SHALL gain the indigo highlight
- **AND** the 投資概覽 item SHALL lose it

#### Scenario: Account history keeps Accounting highlighted
- **WHEN** the user navigates to `/accounting/accounts/5`
- **THEN** the accounting 帳戶 item SHALL be highlighted

### Requirement: Out-of-handoff routes remain reachable

The shell SHALL keep `/portfolio/realized-pnl` reachable via its group item (its layout is specified by `frontend-realized-pnl`). The former accounting sub-routes (`/accounting/dashboard`, `/accounting/transactions`, `/accounting/cards`, `/accounting/categories`, `/accounting/recurring`) SHALL redirect to `/accounting`. `/portfolio/import-broker` SHALL redirect to `/portfolio/import?type=broker`.

#### Scenario: Realized PnL still navigable from Portfolio group
- **WHEN** the 投資 group is open in the sidebar
- **THEN** an item 已實現損益 SHALL link to `/portfolio/realized-pnl`

#### Scenario: Old accounting links redirect
- **WHEN** the user opens a bookmarked `/accounting/cards`
- **THEN** the SPA SHALL redirect to `/accounting`

#### Scenario: Old broker import link redirects
- **WHEN** the user opens `/portfolio/import-broker`
- **THEN** the SPA SHALL redirect to `/portfolio/import?type=broker`

### Requirement: Sidebar collapse and auto-collapse

At ≥ 1280px the sidebar SHALL offer a collapse / expand button whose choice is stored as the sidebar default. On a two-pane page (accounting at ≥ 1024px) the sidebar SHALL render as the rail while the right pane is open and return to the stored default when it closes, unless auto-collapse is off in Settings. A manual expand while the pane is open SHALL win until that pane closes.

#### Scenario: Opening an entry collapses the sidebar
- **GIVEN** the viewport is 1440px and the sidebar is expanded
- **WHEN** the owner opens an entry on `/accounting`
- **THEN** the sidebar SHALL render as the rail
- **AND** closing the entry SHALL restore the expanded sidebar

#### Scenario: Manual expand wins while the pane is open
- **GIVEN** the sidebar auto-collapsed because a pane is open
- **WHEN** the owner presses the expand button
- **THEN** the sidebar SHALL stay expanded until the pane closes

#### Scenario: Auto-collapse turned off
- **GIVEN** auto-collapse is off in Settings
- **WHEN** the owner opens an entry at 1440px
- **THEN** the sidebar SHALL stay expanded

### Requirement: Foldable sidebar groups

In the expanded sidebar each group heading SHALL be a button with `aria-expanded` that folds or unfolds its items; folded groups SHALL be remembered. The group containing the active route SHALL never fold and SHALL show 目前 instead of a chevron. A folded group SHALL show the sum of its items' counts, or a dot when any item has a dot.

#### Scenario: Folded group keeps pending work visible
- **GIVEN** the 投資 group is folded and 股利 has a count of 2 and 匯入 has a dot
- **WHEN** the sidebar renders
- **THEN** the 投資 heading SHALL show the count 2 and a dot

#### Scenario: Active group cannot fold
- **WHEN** the owner clicks the 財務 heading while on `/accounting`
- **THEN** the group SHALL stay open

### Requirement: Navigation badges

A `NavItem` MAY carry a badge (count or dot, plus an accessible label) fed only by existing endpoints (sources listed in `design.md` D4); a failing source SHALL hide its badge. Counts SHALL show in the sidebar, the rail and the in-page sub-nav. The phone tab bar SHALL show only a dot on a non-active group tab that has any badge. No badge SHALL render while badges are off in Settings.

#### Scenario: Count in the sidebar, dot on the phone tab
- **GIVEN** two dividends are pending
- **WHEN** the app renders at 1440px
- **THEN** the 股利 item SHALL show `2` with the accessible label `2 筆待收股利`
- **AND** at 390px on the 財務 tab the 投資 tab SHALL show a dot and the 投資 sub-nav chip 股利 SHALL show `2`

#### Scenario: Failed badge source
- **WHEN** the reminder count request fails
- **THEN** 記帳記錄 SHALL render without a badge and no error SHALL be shown

#### Scenario: Import failure dot
- **WHEN** `GET /api/portfolio/imports/recalc/status` reports the latest import chain as failed
- **THEN** the 匯入 item SHALL show a dot labelled 最近一次匯入失敗

#### Scenario: Badges off
- **GIVEN** badges are turned off in Settings
- **WHEN** two dividends are pending
- **THEN** no count or dot SHALL render anywhere

### Requirement: Hideable groups and tab bar parity

Every group except 系統 SHALL be hideable from Settings; a hidden group SHALL disappear from the sidebar, the rail and the tab bar, and its routes SHALL stay reachable by URL. The tab bar SHALL be built as `[visible groups…, 設定]` with "+" inserted in the middle; when that list has an odd length, 設定 SHALL be removed from the tab bar and a gear button linking to `/settings` SHALL appear at the top-right of every page header, so "+" stays centred. The gear SHALL be omitted on `/settings` itself.

#### Scenario: 物資 hidden
- **GIVEN** 物資 is hidden and 投資 and 財務 are visible
- **WHEN** the tab bar renders at 390px
- **THEN** it SHALL show 投資, "+", 財務 in that order
- **AND** every page header except `/settings` SHALL show a gear linking to `/settings`

#### Scenario: All groups visible
- **GIVEN** 物資, 投資 and 財務 are visible
- **WHEN** the tab bar renders
- **THEN** it SHALL show 物資, 投資, "+", 財務, 設定 and no header gear

### Requirement: App-wide 記一筆 floating button

On viewports ≥ 760px every route SHALL show one 56px `--app-primary` floating button at the bottom-right, tooltip `記一筆` with key `N`, that opens the entry form; it SHALL hide while the form is open and, while a pane is open, sit at the right offset the pane layout provides. While the page's scrolling ancestor (the document or `.hub-main` on ordinary pages, the accounting list pane in panes mode) scrolls down the button SHALL slide out (translate and fade, 160 ms; instant under `prefers-reduced-motion`) and end in `visibility: hidden`, leaving the tab order; it SHALL return on any upward scroll, when the scroll reaches the end, or after 1.2 s without scrolling, and SHALL NOT hide while it has focus. `N` SHALL open the form from any route outside text fields, also while the button is hidden. The sidebar and page headers SHALL NOT contain a 記一筆 button.

#### Scenario: Floating button on a portfolio page
- **WHEN** the owner is on `/portfolio/dividends` at 1440px and presses the floating button
- **THEN** the router SHALL open the entry form

#### Scenario: Button moves beside the pane
- **GIVEN** an entry detail is open in the right pane at 1440px
- **WHEN** the page renders
- **THEN** the floating button SHALL sit at the bottom-right of the list column and SHALL NOT overlap the pane

#### Scenario: Scrolling a long list
- **WHEN** the owner scrolls down on `/portfolio/transactions`
- **THEN** the floating button SHALL be hidden (`visibility: hidden`) and no row action button SHALL be covered

#### Scenario: Scrolling back up
- **WHEN** the owner scrolls up by any amount
- **THEN** the floating button SHALL be visible again

#### Scenario: Focused button stays
- **GIVEN** the floating button has keyboard focus
- **WHEN** the page scrolls down
- **THEN** the button SHALL stay visible

#### Scenario: N from anywhere
- **WHEN** the owner presses `N` on `/portfolio` with no text field focused, the button hidden or not
- **THEN** the entry form SHALL open

#### Scenario: Page create actions stay secondary
- **WHEN** `/portfolio/transactions` renders
- **THEN** its 新增交易 button SHALL use the soft-primary style and no 記一筆 button SHALL appear in the page header or the sidebar

### Requirement: Pane state belongs to two-pane layouts

Only layouts that open and close a right pane SHALL write the shell's pane state. A layout that writes it SHALL also provide the floating button's right offset; the shell SHALL NOT assume a column ratio.

#### Scenario: Cash account page
- **WHEN** the owner opens `/portfolio/accounts/1` at 1440 px
- **THEN** the sidebar SHALL keep its stored width and the floating button SHALL sit at the page's bottom-right

#### Scenario: Accounting pane
- **WHEN** an entry is open in the accounting pane at 1440 px
- **THEN** the floating button SHALL sit at the bottom-right of the list column, at the offset the accounting layout provides

### Requirement: Phone page headers keep one row

Below 760 px a page header SHALL hold the title, at most one primary action and the shell's icon buttons (the gear, when the tab bar omits 設定); any further actions SHALL move into a ⋯ menu button. Controls that filter the page (such as the market toggle) SHALL render below the header at full width.

#### Scenario: Portfolio dashboard on a phone
- **WHEN** `/portfolio` renders at 390 px
- **THEN** the header SHALL be one row with 投資概覽, a ⋯ menu holding 匯入券商 and 刷新行情, and the gear (only when the tab bar omits 設定, per the live Hideable groups and tab bar parity rule)
- **AND** the 全部 / 台股 / 美股 toggle SHALL render under the header

#### Scenario: Transactions on a phone
- **WHEN** `/portfolio/transactions` renders at 390 px
- **THEN** the header SHALL be one row with 交易紀錄, the 新增交易 button and a ⋯ menu holding 匯入 CSV

#### Scenario: Cash accounts on a phone
- **WHEN** `/portfolio/accounts` renders at 390 px
- **THEN** the header SHALL be one row with 現金帳戶, the 記錄入出金 button and a ⋯ menu holding 新增帳戶

#### Scenario: Header menu is labelled and keyboard-operable
- **WHEN** the ⋯ button on a phone page header is opened
- **THEN** the button SHALL be labelled 更多動作 and open a menu (`role="menu"`) of the moved actions, the first item SHALL take focus, arrow keys SHALL move between items, and Escape SHALL close the menu and return focus to the button
- **AND** from 760 px the actions SHALL stay inline and no ⋯ button SHALL render
