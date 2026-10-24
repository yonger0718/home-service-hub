## ADDED Requirements

### Requirement: Floating button yields while scrolling

At ≥ 760 px the 記一筆 floating button SHALL slide out while the page scrolls down and SHALL return when the page scrolls up, reaches its end, or stays still for 1.2 s. With `prefers-reduced-motion` it SHALL hide and show without animation. The `N` shortcut SHALL work while the button is hidden.

#### Scenario: Scrolling a long list
- **WHEN** the owner scrolls down on `/portfolio/transactions`
- **THEN** the floating button SHALL be hidden and no row action button SHALL be covered

#### Scenario: Scrolling back up
- **WHEN** the owner scrolls up by any amount
- **THEN** the floating button SHALL be visible again

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
