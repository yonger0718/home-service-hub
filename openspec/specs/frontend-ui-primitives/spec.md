# frontend-ui-primitives Specification

## Purpose
TBD - created by archiving change redesign-dashboard-handoff. Update Purpose after archive.

## Requirements

### Requirement: Btn primitive

The frontend SHALL provide a `Btn` standalone component with variants `primary` (indigo solid), `secondary` (slate outline), and `ghost` (transparent). It MUST accept `disabled`, `loading`, and an `icon` PrimeIcon class, and emit `click` only when not disabled or loading.

#### Scenario: Primary variant uses indigo tokens
- **WHEN** `Btn` renders with `variant="primary"`
- **THEN** its background is `var(--app-primary)` and hover background is `var(--app-primary-hover)`

#### Scenario: Disabled button does not emit click
- **WHEN** `Btn` is `disabled` and the user clicks it
- **THEN** no `click` event is emitted

### Requirement: SegToggle primitive

The frontend SHALL provide a `SegToggle` standalone component rendering a segmented control over an array of `{ value, label }` options, with the selected option highlighted in indigo on a `--app-surface-soft` track. It MUST support keyboard navigation (Left/Right arrow keys) and emit `change` on selection.

#### Scenario: Click selects option
- **WHEN** the user clicks an option
- **THEN** `change` emits the option's `value`
- **AND** the clicked segment gains the active highlight

#### Scenario: Arrow keys navigate
- **WHEN** the segment has focus and the user presses Right arrow
- **THEN** focus moves to the next option and `change` emits its value

### Requirement: Bento primitive

The frontend SHALL provide a `Bento` standalone component rendering a card surface with `--radius-xl`, `--app-card-shadow`, `--app-inset-line` top highlight, optional UPPERCASE card title (`--fs-card-title`), and a content slot. A `.b-full` variant SHALL span the full bento grid width.

#### Scenario: Bento renders title + content
- **WHEN** `Bento` is used with a title and projected content
- **THEN** the title renders in uppercase tracked label style and the content sits below it inside the card surface

### Requirement: PctBadge primitive

The frontend SHALL provide a `PctBadge` standalone component rendering a small pill displaying a signed percentage. The pill MUST use `var(--app-trend-positive)` for non-negative values and `var(--app-trend-negative)` for negative values, with an `*-soft` background.

#### Scenario: Positive pct shows trend-positive colour
- **WHEN** `PctBadge` receives `value=4.2`
- **THEN** the pill text is `+4.20%` in `var(--app-trend-positive)` on `var(--app-trend-positive-soft)` background

### Requirement: Tag primitive

The frontend SHALL provide a `Tag` standalone component for small inline labels with variants `neutral`, `accent`, `success`, `warning`, `danger`, `dividend`, each backed by the corresponding `--app-*-soft` token. Default radius is `--radius-sm`.

#### Scenario: Tag variant uses correct soft background
- **WHEN** `Tag` renders with `variant="dividend"`
- **THEN** its background is `var(--app-state-dividend-bg)` and text is `var(--app-dividend)`

### Requirement: SideTag primitive

The frontend SHALL provide a `SideTag` standalone component used in timelines with variants `buy` (indigo), `sell` (slate), `cash` (violet for dividends). The tag renders a short label (e.g., 買 / 賣 / 息).

#### Scenario: Buy tag uses indigo
- **WHEN** `SideTag` renders with `variant="buy"`
- **THEN** background uses `var(--app-buy)` family tokens

### Requirement: Timeline primitive

The frontend SHALL provide a `Timeline` standalone component that renders date-grouped rows. Each row accepts a `SideTag`, primary text, meta text, and a trailing amount (`.tl-amt` with positive/negative trend or `.dividend` violet variant).

#### Scenario: Timeline groups by date
- **WHEN** two transactions on the same date are passed
- **THEN** they render under a single date heading with two rows

### Requirement: FileChip primitive

The frontend SHALL provide a `FileChip` standalone component used in the CSV import dropzone, displaying file name, parsed row count, and a remove (×) button. It MUST emit `remove` when the × is clicked.

#### Scenario: Remove emits event
- **WHEN** the user clicks the × on `FileChip`
- **THEN** `remove` event is emitted

### Requirement: Accessibility for interactive primitives

`Btn`, `SegToggle`, and qty steppers SHALL include appropriate ARIA attributes (`role`, `aria-pressed`, `aria-label`), keyboard handlers, and visible focus ring (`--app-focus-ring`, 3–4px outer glow).

#### Scenario: SegToggle exposes aria-pressed
- **WHEN** `SegToggle` renders three options with the second selected
- **THEN** the second segment has `aria-pressed="true"` and the others have `aria-pressed="false"`

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

#### Scenario: Foreign-currency KPI value fits its card
- **WHEN** the dashboard market filter selects a non-TWD market and a KPI value carries an ISO code (`+12,345.67 USD`)
- **THEN** the value SHALL use `clamp(16px, 9cqi, var(--fs-value))` (class `foreign`) and SHALL still neither wrap nor clip at 360 / 390 / 1100 / 1440 px

### Requirement: Line icons for interface chrome

Buttons, badges, markers and navigation SHALL use the app's line icon set (PrimeIcons). Emoji SHALL appear only in icon slots that show owner-chosen category, account or entry-kind icons; such a slot MAY fall back to a default emoji when the owner chose none, so it stays visually consistent with its neighbours. The text glyphs ✕ ✓ ‹ › remain allowed. A unit test SHALL fail when a template places an emoji outside an interpolation.

#### Scenario: Entry detail actions
- **WHEN** an entry detail renders its 編輯, 複製, 退款 and 刪除 actions
- **THEN** each SHALL use a line icon, not a Unicode glyph or emoji

#### Scenario: Template gate
- **WHEN** a template adds a static emoji (for example `<span>🗑</span>`) outside an interpolation or comment
- **THEN** the unit suite SHALL fail and name the file and line

### Requirement: Component classes do not collide with Bootstrap

Component templates SHALL NOT use Bootstrap grid class names (`row`, `col`, `col-*`, `container`) for their own elements, except in the allowlisted legacy forms that use the Bootstrap grid on purpose (`item-list`, `portfolio/dividend-list`, `portfolio/transaction-list`). A test in the unit suite SHALL fail when one is introduced elsewhere, and SHALL fail when an allowlisted file no longer exists.

#### Scenario: Gate
- **WHEN** a template outside the allowlist adds a `row` class token (for example `class="row entry"`)
- **THEN** the unit suite SHALL fail and name the file and line
