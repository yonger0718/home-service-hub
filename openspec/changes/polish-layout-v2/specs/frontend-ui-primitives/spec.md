## ADDED Requirements

### Requirement: FilterBar primitive

The UI library SHALL provide `<app-filter-bar>` with a search field, up to two primary controls supplied by the page, and a 篩選 button that opens the page's secondary filters (a popover at ≥ 760 px, a bottom sheet below). The 篩選 button SHALL show the number of active secondary filters. Active filters SHALL render as removable chips under the bar, with 清除全部 when two or more are active.

#### Scenario: Secondary filter count
- **GIVEN** the owner set a date range and a broker on 交易紀錄
- **WHEN** the filter bar renders
- **THEN** the 篩選 button SHALL read `篩選 2` and two chips SHALL appear under the bar

#### Scenario: Removing a chip
- **WHEN** the owner removes the broker chip
- **THEN** the broker filter SHALL clear and the list SHALL refresh

#### Scenario: Phone sheet
- **WHEN** the owner taps 篩選 at 390 px
- **THEN** the secondary filters SHALL open in a bottom sheet with 套用 and 重設

### Requirement: Values fit their container

KPI and summary values (the dashboard KPI cards; the 交易紀錄, 股利 and 已實現損益 summary cards; the 現金帳戶 header figures) SHALL never wrap a number across lines or clip it. Each card SHALL be an inline-size query container and the value's font size SHALL scale with the card's inline size between 18 px and `--fs-value` (`clamp(18px, 12cqi, var(--fs-value))`), with `white-space: nowrap` and tabular digits. The floor SHALL fit an 8-digit TWD amount (`$12,345,678`) in a 2 × 2 phone cell at 360 px.

#### Scenario: Narrow desktop
- **WHEN** the 投資概覽 KPI row renders at 1100 px with a market value of 3,021,869
- **THEN** the 總市值 value SHALL be fully visible inside its card (`scrollWidth ≤ clientWidth`)

#### Scenario: Phone summary card
- **WHEN** the 交易紀錄 summary renders at 390 px with 買進金額 2,871,432
- **THEN** the value SHALL render on one line

#### Scenario: Eight digits in a phone KPI cell
- **WHEN** the dashboard KPI grid renders at 360 px with 總市值 12,345,678
- **THEN** `$12,345,678` SHALL be fully visible in its cell

### Requirement: Line icons for interface chrome

Buttons, badges, markers and navigation SHALL use the app's line icon set. Emoji SHALL appear only where the owner chose them (category and account icons).

#### Scenario: Entry detail actions
- **WHEN** an entry detail renders its 編輯, 複製, 退款 and 刪除 actions
- **THEN** each SHALL use a line icon, not a Unicode glyph or emoji

### Requirement: Component classes do not collide with Bootstrap

Component templates SHALL NOT use Bootstrap grid class names (`row`, `col`, `col-*`, `container`) for their own elements, except in the allowlisted legacy forms that use the Bootstrap grid on purpose (`item-list`, `portfolio/dividend-list`, `portfolio/transaction-list`). A test in the unit suite SHALL fail when one is introduced elsewhere, and SHALL fail when an allowlisted file no longer exists.

#### Scenario: Gate
- **WHEN** a template outside the allowlist adds a `row` class token (for example `class="row entry"`)
- **THEN** the unit suite SHALL fail and name the file and line
