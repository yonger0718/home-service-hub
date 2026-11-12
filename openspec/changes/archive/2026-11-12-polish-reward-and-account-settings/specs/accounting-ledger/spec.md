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

### Requirement: Reward overview account header fields

Each account in `GET /reward-rules` SHALL also carry `icon`, `logo` and `logo_hash` (as `GET /accounts` returns them) and `closing_day`: the closing day its reward windows use, which is the account's own, else its combined parent's, else null (calendar months).

#### Scenario: Combined card inherits the closing day
- **GIVEN** a card without its own closing day whose combined parent closes on the 25th
- **WHEN** `GET /reward-rules` is read
- **THEN** that card's `closing_day` SHALL be 25

#### Scenario: Header fields match the accounts list
- **GIVEN** a card with an uploaded logo, the slug `esun` and the icon 💳
- **WHEN** `GET /reward-rules` is read
- **THEN** its `icon`, `logo` and `logo_hash` SHALL equal that account's values in `GET /accounts`

### Requirement: Reward usage unit

Each usage row (`GET /accounts/{id}/reward-usage` and the overview's `usage`) SHALL carry `reward_account_id`, `reward_account_name` and `reward_is_money` for the reward account of the row's first rule in rule order. `reward_is_money` SHALL be true when that account is the card itself or unset, is a credit account, or is marked `cash_equivalent` — archived or not.

#### Scenario: Points account
- **GIVEN** a rule paying into the archived non-credit account 亞洲萬里通
- **THEN** its usage row SHALL have `reward_account_name` 亞洲萬里通 and `reward_is_money` false

#### Scenario: Cash-equivalent points account
- **GIVEN** a rule paying into the non-credit account 測試現金點, marked `cash_equivalent`
- **THEN** its usage row SHALL have `reward_account_name` 測試現金點 and `reward_is_money` true

### Requirement: Cash-equivalent accounts

An account SHALL carry `cash_equivalent` (1 點 = 1 元; default false) on every account read (`GET /accounts`, `GET /accounts/{id}`). `POST /accounts` and `PUT /accounts/{id}` SHALL accept it; a `PUT` that omits it SHALL keep the stored value. MOZE imports SHALL NOT change it.

#### Scenario: PUT without the field
- **GIVEN** an account with `cash_equivalent` true
- **WHEN** it is saved with a body that omits `cash_equivalent`
- **THEN** `cash_equivalent` SHALL still be true
