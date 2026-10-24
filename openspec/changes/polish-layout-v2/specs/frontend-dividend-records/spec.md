## ADDED Requirements

### Requirement: Empty summary items stay out of the way

When 舊資料帳面 is 0 the summary SHALL show three cards (已確認收款, 有效待收, 已記錄股利). When no ex-dividend event is upcoming the 即將除權息提醒 row SHALL NOT render.

#### Scenario: No legacy receipts
- **GIVEN** no legacy dividend rows
- **WHEN** the summary renders at 1440 px
- **THEN** three summary cards SHALL render

#### Scenario: No upcoming events
- **WHEN** the upcoming ex-dividend list is empty
- **THEN** no 即將除權息提醒 row SHALL render
