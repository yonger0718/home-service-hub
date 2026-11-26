## ADDED Requirements

### Requirement: Auto-pay switch for the write scope

`PATCH /settings/reconciliation/auto-pay` (write scope) SHALL update `enabled` (boolean) and `enabled_from` (ISO date or null) of the card-statement auto-pay settings. It SHALL merge only the fields it sends into the stored `auto_pay` block, keep the rest of the settings document, bump the settings `version`, and answer `enabled`, `enabled_from` and `version`. `PUT /settings/reconciliation` SHALL stay admin-only.

#### Scenario: Turn on with a start date
- **GIVEN** stored `auto_pay` `{enabled: false, twin_window_days: 9}`
- **WHEN** `PATCH /settings/reconciliation/auto-pay {"enabled": true, "enabled_from": "2026-12-01"}` is sent with a write-scope token
- **THEN** the stored `auto_pay` SHALL be `{enabled: true, enabled_from: "2026-12-01", twin_window_days: 9}`, `version` SHALL increase by one, and every other settings key SHALL be unchanged

#### Scenario: Turn off keeps the start date
- **GIVEN** auto-pay is on from 2026-12-01
- **WHEN** `PATCH … {"enabled": false}` is sent
- **THEN** `enabled` SHALL be false and `enabled_from` SHALL stay 2026-12-01

#### Scenario: Read scope
- **WHEN** a read-only token sends the PATCH
- **THEN** the endpoint SHALL answer 403

### Requirement: Auto-pay switch checks

The merged `auto_pay` block SHALL pass the same checks as `PUT /settings/reconciliation`; `enabled_from` is required while `enabled` is true. A body with neither field, any other key, a non-boolean `enabled` or a non-ISO `enabled_from` SHALL answer 422. A refused PATCH SHALL store nothing.

#### Scenario: On without a start date
- **GIVEN** auto-pay is off with no start date
- **WHEN** `PATCH … {"enabled": true}` is sent
- **THEN** the endpoint SHALL answer 422 naming `auto_pay` and store nothing

#### Scenario: Another setting
- **WHEN** `PATCH … {"max_amount": "1"}` is sent
- **THEN** the endpoint SHALL answer 422 and store nothing

### Requirement: Auto-pay rows

`GET /reconciliation/auto-pay?since=<date>` (read scope) SHALL list the card-statement auto-pay rows (one per statement) whose pay date is on or after `since`, newest pay date first, at most 200. Each row SHALL carry `statement_id`, `card_account_id`, `pay_account_id` (null for a `no_link` skip), `amount`, `due_date`, `pay_date`, `status`, `reason` and `updated_at`. `since` is required.

#### Scenario: A booked and a skipped statement
- **GIVEN** statement A booked with pay date 2026-10-26 and statement B skipped with `no_link`, pay date 2026-11-24
- **WHEN** `GET /reconciliation/auto-pay?since=2026-10-01` is called
- **THEN** it SHALL return B then A, B with `pay_account_id` null and `reason` `no_link`

#### Scenario: Since narrows
- **WHEN** `since` is 2026-11-01
- **THEN** only B SHALL be returned
