## ADDED Requirements

### Requirement: Balance chart fills its card

The account balance chart SHALL fill its card's plot height, label the x-axis with `MM/DD` ticks (at most 8), draw no point markers, and take its line and fill colours from theme tokens.

#### Scenario: Three-month range
- **WHEN** the 3M range is selected on `/portfolio/accounts/1`
- **THEN** the chart SHALL show at most 8 horizontal `MM/DD` labels and the line SHALL span the plot height between the range's minimum and maximum

#### Scenario: Dark mode line
- **GIVEN** dark mode is on
- **WHEN** the chart renders
- **THEN** the line SHALL use the dark `--app-primary`
