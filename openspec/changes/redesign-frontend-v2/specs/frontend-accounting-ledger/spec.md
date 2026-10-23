## MODIFIED Requirements

### Requirement: Timeline home page

The SPA SHALL provide `/accounting` as the accounting home, replacing the redirect to the accounts list. It SHALL show a month navigator with the month's expense, income and net totals from the summary endpoint, a category distribution bar under the totals (the month's expense split by top-level category: the five largest plus 其他, each segment in the category's colour, with a legend of name and amount), a filter row (accounts, kinds, text search; behind a ⌕ button below 760 px), and entries from `GET /api/accounting/entries` grouped by day with a per-day net. Each row SHALL show category icon and colour, name or category, merchant and tags, amount (red for outflow, green for inflow per the preference), the original-currency amount inline when converted, and project and account pills. Reward entries SHALL be hidden when the preference `hide_rewards_on_timeline` is true. A split group SHALL render as one row with the group's name, the sum and a count badge. Tapping a row opens the entry detail. The page SHALL load more on scroll.

#### Scenario: Foreign-currency amount visible without hover
- **GIVEN** an expense converted from `¥5,390` at `0.2163`
- **WHEN** the timeline renders at 390 px
- **THEN** the row SHALL show `¥5,390` and the rate beneath the TWD amount

#### Scenario: Month totals
- **WHEN** the owner moves the navigator to 2026-09
- **THEN** the header SHALL show September's expense, income and net from the summary endpoint and the list SHALL show September's entries

#### Scenario: Category distribution bar
- **GIVEN** the month's expenses span eight top-level categories
- **WHEN** the summary renders
- **THEN** the bar SHALL show the five largest categories plus 其他, and the segments SHALL sum to the month's expense

### Requirement: Entry shortcuts

The web manifest SHALL declare a shortcut `記一筆` to `/accounting/entry`. The floating "+" and the `N` shortcut SHALL be provided app-wide by the shell (`frontend-app-shell`, App-wide 記一筆 floating button); on accounting pages at ≥ 1024 px the entry form opens in the right pane. On the entry page ⏎ saves, ⇧⏎ saves and continues, Esc cancels; on list pages E edits the selected entry.

#### Scenario: Home-screen shortcut
- **WHEN** the installed app's shortcut `記一筆` is launched
- **THEN** the app SHALL open directly on `/accounting/entry`

#### Scenario: Floating button opens the form in the pane
- **WHEN** the owner presses the floating "+" on `/accounting` at 1280 px
- **THEN** the entry form SHALL open in the right pane

## Implementation notes

- `GET /entries/summary` gains `by_category`: top-level root category id, name, colour and expense in the main currency, built from the same spend rows and rate conversion as the totals. Currencies with a missing rate are excluded, uncategorised rows group as 未分類, and the list is sorted largest spend first. Additive, no migration.
- Client rule (rev 3): no bar when the month's expense is ≥ 0 (no spending, a refund-only or net-refund month) or `by_category` is absent. Otherwise the bar shows the five largest spending roots plus 其他 (the rest of the spending roots, when ≥ 0.01) at their real amounts, nothing scaled; net-refund roots sum into a legend-only 退款 line (no bar segment, no swatch, when ≥ 0.01). The legend reconciles to the header as Σ top + 其他 − 退款 = |expense|, up to per-bucket FX rounding; legend labels are rounded display values.
- Colours are the root category's own colour, with fallbacks `--app-text-muted` and `--app-category-other` (#8a94a6).
- The bar sits inside the summary card after the three figures: 10 px with a wrapping legend at ≥ 760 px, 8 px without legend on phone. It has `role="img"` with aria text listing every legend line, 退款 included (phone has no legend, so 退款 is only in the aria text there).
- The bar follows the summary's 全月 · 未套篩選 rule and is hidden while loading, on error, and when the month has no spending.
- Contrast (8.3): `--c-red-ink` / `--c-green-ink`, the convention ink pair and `--tone-*-ink` are used for trend text ≤ 13 px; the dark primary button fill is `#6c5ce7` (rest and hover).
- Known limit: small text on tinted pills in light mode sits at 4.1–4.5:1.
