## ADDED Requirements

### Requirement: Pane state belongs to two-pane layouts

Only layouts that open and close a right pane SHALL write the shell's pane state. A layout that writes it SHALL also provide the floating button's right offset; the shell SHALL NOT assume a column ratio.

#### Scenario: Cash account page
- **WHEN** the owner opens `/portfolio/accounts/1` at 1440 px
- **THEN** the sidebar SHALL keep its stored width and the floating button SHALL sit at the page's bottom-right

#### Scenario: Accounting pane
- **WHEN** an entry is open in the accounting pane at 1440 px
- **THEN** the floating button SHALL sit at the bottom-right of the list column, at the offset the accounting layout provides

### Requirement: Phone page headers keep one row

Below 760 px a page header SHALL hold the title, at most one primary action and the shell's icon buttons; any further actions SHALL move into a ⋯ menu button. Controls that filter the page (such as the market toggle) SHALL render below the header at full width.

#### Scenario: Portfolio dashboard on a phone
- **WHEN** `/portfolio` renders at 390 px
- **THEN** the header SHALL be one row with 投資概覽, a ⋯ menu holding 匯入券商 and 刷新行情, and the gear
- **AND** the 全部 / 台股 / 美股 toggle SHALL render under the header

## MODIFIED Requirements

### Requirement: App-wide 記一筆 floating button

On viewports ≥ 760px every route SHALL show one 56px `--app-primary` floating button at the bottom-right, tooltip `記一筆` with key `N`, that opens the entry form; it SHALL hide while the form is open and, while a pane is open, sit at the right offset the pane layout provides. While the page's scrolling ancestor (the document on ordinary pages, the accounting list pane in panes mode) scrolls down the button SHALL slide out (translate and fade, 160 ms; instant under `prefers-reduced-motion`) and end in `visibility: hidden`, leaving the tab order; it SHALL return on any upward scroll, when the scroll reaches the end, or after 1.2 s without scrolling, and SHALL NOT hide while it has focus. `N` SHALL open the form from any route outside text fields, also while the button is hidden. The sidebar and page headers SHALL NOT contain a 記一筆 button.

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
