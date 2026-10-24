## ADDED Requirements

### Requirement: Cost card in neutral text

The 賣出成本費用 summary value SHALL render in `--app-text`, because it is a cost, not a gain or loss.

#### Scenario: Cost value colour
- **WHEN** the summary renders with costs of 1,249
- **THEN** the value SHALL use `--app-text` under both gain/loss conventions
