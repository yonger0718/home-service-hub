## ADDED Requirements

### Requirement: Business-day calendar endpoint

The system SHALL expose `GET /calendar/business-days?from=YYYY-MM-DD&to=YYYY-MM-DD` (read scope) returning `holidays` (the built-in Taiwan holiday table united with `auto_pay.extra_holidays`, within the range, sorted), `workdays` (`auto_pay.extra_workdays` within the range, sorted) and `last_known_day`. Weekends SHALL NOT be listed; a listed workday wins, as the auto-pay job's business-day rule decides.

#### Scenario: Make-up holidays within the month

- **GIVEN** no owner overrides
- **WHEN** the client asks for 2026-10-01 to 2026-10-31
- **THEN** `holidays` SHALL be `["2026-10-09", "2026-10-26"]`, `workdays` `[]`, and `last_known_day` the table's last day

#### Scenario: Owner overrides are merged

- **GIVEN** `auto_pay.extra_holidays` `["2026-10-27"]` and `extra_workdays` `["2026-10-26"]`
- **WHEN** the client asks for 2026-10-20 to 2026-10-31
- **THEN** `holidays` SHALL be `["2026-10-26", "2026-10-27"]` and `workdays` `["2026-10-26"]`

#### Scenario: Range validation

- **WHEN** `to` precedes `from`, or the span exceeds 1100 days, or either is missing
- **THEN** the endpoint SHALL answer 422
