## ADDED Requirements

### Requirement: Date-grouped buy/sell timeline

The stock transactions screen SHALL render trades in a `Timeline` grouped by trade date, descending. Each row MUST display a `SideTag` (`buy` indigo / `sell` slate), stock name + code, meta text `qty × price`, and an amount (`.tl-amt.buy` for buys, `.tl-amt.sell` for sells).

#### Scenario: Buy row uses buy styling
- **WHEN** a buy trade renders
- **THEN** the row shows an indigo `買` `SideTag` and a `.tl-amt.buy` amount

#### Scenario: Trades on same date share a heading
- **WHEN** two trades on the same date are rendered
- **THEN** they appear under a single date group heading
