## MODIFIED Requirements

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

The shell SHALL highlight the active sidebar item, rail item, phone tab and sub-nav entry using `var(--app-primary)` (indigo text on `--app-primary-soft`; the expanded item's icon square filled with `--app-primary`). Active state is driven by the current Angular route via `navItemForUrl`. Route ids MUST match `NAV_GROUPS` in `shell/navigation.ts`. The `accounts` id SHALL cover `/portfolio/accounts` and `/portfolio/accounts/:id`; the accounting `accounting` id SHALL cover `/accounting/accounts` and `/accounting/accounts/:id`.

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

## REMOVED Requirements

### Requirement: Frosted top header

**Reason**: The logo moves into the sidebar and every page renders its own title and actions; the header duplicated the page title and cost ~60px of height on every screen.

**Migration**: Each routed page renders an `<h1>` (28px, weight 800) and its page actions in its own header row. The logo lockup is rendered at the top of the expanded sidebar; the rail shows the app icon.

## ADDED Requirements

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

Every group except 系統 SHALL be hideable from Settings; a hidden group SHALL disappear from the sidebar, the rail and the tab bar, and its routes SHALL stay reachable by URL. The tab bar SHALL be built as `[visible groups…, 設定]` with "+" inserted in the middle; when that list has an odd length, 設定 SHALL be removed from the tab bar and a gear button linking to `/settings` SHALL appear at the top-right of every page header, so "+" stays centred.

#### Scenario: 物資 hidden
- **GIVEN** 物資 is hidden and 投資 and 財務 are visible
- **WHEN** the tab bar renders at 390px
- **THEN** it SHALL show 投資, "+", 財務 in that order
- **AND** every page header SHALL show a gear linking to `/settings`

#### Scenario: All groups visible
- **GIVEN** 物資, 投資 and 財務 are visible
- **WHEN** the tab bar renders
- **THEN** it SHALL show 物資, 投資, "+", 財務, 設定 and no header gear

### Requirement: App-wide 記一筆 floating button

On viewports ≥ 760px every route SHALL show one 56px `--app-primary` floating button at the bottom-right, tooltip `記一筆` with key `N`, that opens the entry form; it SHALL hide while the form is open and anchor to the list column while a pane is open. `N` SHALL open the form from any route outside text fields. The sidebar and page headers SHALL NOT contain a 記一筆 button.

#### Scenario: Floating button on a portfolio page
- **WHEN** the owner is on `/portfolio/dividends` at 1440px and presses the floating button
- **THEN** the router SHALL open the entry form

#### Scenario: Button moves beside the pane
- **GIVEN** an entry detail is open in the right pane at 1440px
- **WHEN** the page renders
- **THEN** the floating button SHALL sit at the bottom-right of the list column and SHALL NOT overlap the pane

#### Scenario: N from anywhere
- **WHEN** the owner presses `N` on `/portfolio` with no text field focused
- **THEN** the entry form SHALL open

#### Scenario: Page create actions stay secondary
- **WHEN** `/portfolio/transactions` renders
- **THEN** its 新增交易 button SHALL use the soft-primary style and no 記一筆 button SHALL appear in the page header or the sidebar
