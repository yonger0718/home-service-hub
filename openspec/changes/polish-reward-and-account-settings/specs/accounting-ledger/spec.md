## ADDED Requirements

### Requirement: Reward overview usage

`GET /reward-rules` SHALL include `usage` on every account: the rows `GET /accounts/{id}/reward-usage` returns for the window containing today (`rule_ids`, `shared_cap_id`, `name`, `window_start`, `window_end`, `used`, `estimated`, `cap`, `remaining`). Rules sharing a cap SHALL share one row. `usage` SHALL describe the account's active rules whatever the `state` and `account_id` filters select. An account without active rules SHALL have `usage: []`.

#### Scenario: One request for the overview
- **GIVEN** 玉山 Pi 卡 has 一般消費 1% (cap $500, $236 posted, $64 estimated this period)
- **WHEN** `GET /reward-rules` is read
- **THEN** 玉山 Pi 卡's `usage` SHALL contain a row with that rule's id in `rule_ids`, `used` 236, `estimated` 64, `cap` 500 and `remaining` 200, equal to `GET /accounts/{id}/reward-usage`

#### Scenario: Filter does not hide usage
- **WHEN** `GET /reward-rules?state=expired` is read
- **THEN** each listed account's `usage` SHALL still describe its active rules
