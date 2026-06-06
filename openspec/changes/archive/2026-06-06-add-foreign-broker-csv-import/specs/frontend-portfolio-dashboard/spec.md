## ADDED Requirements

### Requirement: Dashboard renders per-broker cash tile from broker_cash_flows

The portfolio dashboard SHALL render a per-broker cash tile that reads from `GET /api/portfolio/broker-cash-flows`. The tile SHALL show one row per broker returned by the API, each with `broker`, `currency`, and `balance`. The existing aggregate cash tile SHALL remain for the ALL-broker total in TWD.

#### Scenario: Tile renders one row per active broker
- **GIVEN** `GET /api/portfolio/broker-cash-flows` returns three rows for IB, FIRSTRADE, SCHWAB
- **WHEN** the dashboard mounts
- **THEN** the per-broker cash tile SHALL display three rows with the matching broker labels and balances

#### Scenario: Empty broker cash flows hides the tile
- **GIVEN** the endpoint returns an empty array
- **WHEN** the dashboard mounts
- **THEN** the per-broker cash tile SHALL NOT render

#### Scenario: Aggregate cash tile still shows ALL-broker TWD total
- **GIVEN** non-empty broker cash flows
- **WHEN** the dashboard mounts
- **THEN** the existing aggregate cash tile SHALL continue to render in TWD with no behaviour change
