## MODIFIED Requirements

### Requirement: Navigation badges

A `NavItem` MAY carry a dot (plus an accessible label) when its page has a counted notification task; counts SHALL appear only on the 通知 entry points (`frontend-notification-center`). A dot SHALL be `--c-red-ink` for overdue or failed work and `--app-primary` otherwise. The phone tab bar SHALL show a dot on a non-active group tab that has any dot. A failing source SHALL hide its dot. No dot or count SHALL render while badges are off in Settings.

#### Scenario: Count in the sidebar, dot on the phone tab
- **GIVEN** two dividends are past their payment date and unconfirmed
- **WHEN** the app renders at 1440px
- **THEN** the 股利 item SHALL show a dot labelled `2 筆待確認收款` and 通知 SHALL include the batch in its count
- **AND** at 390px on the 財務 tab the 投資 tab SHALL show a dot

#### Scenario: Failed badge source
- **WHEN** the reminder sources fail to load
- **THEN** 記帳記錄 and 帳戶 SHALL render without a dot and no error SHALL be shown in the navigation

#### Scenario: Import failure dot
- **WHEN** `GET /api/portfolio/imports/recalc/status` reports the latest import chain as failed
- **THEN** the 匯入 item SHALL show a red dot labelled 最近一次匯入失敗

#### Scenario: Badges off
- **GIVEN** badges are turned off in Settings
- **WHEN** two dividends are pending
- **THEN** no count or dot SHALL render anywhere
