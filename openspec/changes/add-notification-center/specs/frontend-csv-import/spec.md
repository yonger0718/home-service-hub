## ADDED Requirements

### Requirement: 國泰每日對帳單 status card

The import page SHALL show a 國泰每日對帳單 card with the latest worker run (time, result, files seen, posted) from `GET /api/portfolio/imports/cathay-statement/runs/latest` and the dates that need the owner from `…/pending` (date, status, reason), each with 確認 calling `POST …/days/{date}/ack`. A stale or failed latest run SHALL show as a 注意 or 錯誤 banner in the card. When `pending` is truncated the card SHALL say how many more dates exist.

#### Scenario: Healthy run, nothing pending
- **WHEN** the latest run is `ok` today at 06:41 with 1 file posted and no pending dates
- **THEN** the card SHALL read 「今天 06:41 · 已匯入 1 份」 and list no dates

#### Scenario: Failed run
- **WHEN** the latest run is `failed` with code `password_file`
- **THEN** the card SHALL show an 錯誤 banner naming the code
