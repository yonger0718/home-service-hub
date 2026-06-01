## ADDED Requirements

### Requirement: Shopping list empty-state placeholder

The shopping list route SHALL render a clean, labelled empty state indicating that the screen is not yet designed. No invented layout, no placeholder data rows.

#### Scenario: Route renders empty state
- **WHEN** the user navigates to `/shopping-list`
- **THEN** the screen displays a centred empty-state with an icon, a title (e.g., 採買清單), and a short caption noting it is not yet designed
