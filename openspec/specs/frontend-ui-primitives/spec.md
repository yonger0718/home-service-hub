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
