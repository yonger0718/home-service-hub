## ADDED Requirements

### Requirement: 國泰每日對帳單 status card

The import page SHALL show a 國泰每日對帳單 card with the latest worker run (time, result, files seen, posted) from `GET /api/portfolio/imports/cathay-statement/runs/latest` and the dates that need the owner from `…/pending` (date, status, reason), each with 已處理 calling `POST …/days/{date}/ack` (it records the date as known and imports nothing). A failed or latched run SHALL show an 錯誤 banner, a stale one a 注意 banner. When `pending` is truncated the card SHALL say how many more dates exist.

#### Scenario: Healthy run, nothing pending
- **WHEN** the latest run is `ok` today at 06:41 with 1 file posted and no pending dates
- **THEN** the card SHALL read 「今天 06:41 · 已匯入 1 份」 and list no dates

#### Scenario: Failed run
- **WHEN** the latest run is `failed` with code `password_file`
- **THEN** the card SHALL show an 錯誤 banner reading 「讀不到對帳單密碼檔」

#### Scenario: Failed run with an unknown code
- **WHEN** the latest run is `failed` with a code the SPA does not know
- **THEN** the banner SHALL read 「錯誤代碼 <code>」

#### Scenario: Latched run
- **WHEN** the latest run is `latched`
- **THEN** the card SHALL show an 錯誤 banner and the line 「排除原因後，在主機執行：python -m worker cathay --unlatch」

#### Scenario: Truncated pending dates
- **WHEN** `pending` lists 50 dates with `total` 73 and `truncated` true
- **THEN** the card SHALL read 「還有 23 天未列出，處理完上面的會接著顯示」
