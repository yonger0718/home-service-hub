## ADDED Requirements

### Requirement: Footer after a file is chosen

The 取消 / 重新預覽 / 確認匯入 footer SHALL render only after a file is chosen. 首列為欄位名稱 SHALL be a switch control.

#### Scenario: Fresh import screen
- **WHEN** `/portfolio/import` opens with no file chosen
- **THEN** no 確認匯入 button SHALL render

#### Scenario: File chosen
- **WHEN** the owner chooses a CSV file
- **THEN** the footer SHALL render with 確認匯入 and the parsed count
