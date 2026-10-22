## MODIFIED Requirements

### Requirement: Appearance toggle row

The Settings screen SHALL include an 外觀 section with a row labelled 主題 (description: 跟隨系統時，會依裝置的深淺色自動切換) and a `SegToggle` with options 淺色 / 深色 / 跟隨系統. Selecting an option MUST call `AppearanceService.setTheme()` and the control MUST reflect the current `theme$` value.

#### Scenario: Toggling appearance updates app
- **WHEN** the user selects 深色 on the theme control
- **THEN** `AppearanceService.setTheme('dark')` is called
- **AND** the application switches to dark mode immediately

#### Scenario: Follow system
- **WHEN** the user selects 跟隨系統 on a device whose OS is dark
- **THEN** `AppearanceService.setTheme('system')` is called and the application stays dark

### Requirement: Settings persist across reload

The theme, the gain/loss convention and every 導覽 setting SHALL persist (through `AppearanceService` and `NavigationPreferencesService`) so that reloading restores the choices.

#### Scenario: Reload restores both toggles
- **WHEN** the user sets 深色 + 綠漲紅跌, turns badges off and reloads
- **THEN** the Settings screen reflects 深色, 綠漲紅跌 and badges off

## ADDED Requirements

### Requirement: Settings layout

The Settings screen SHALL render, in order, the sections 外觀, 投資顯示, 導覽, 記帳, 鍵盤快捷鍵 (≥ 760px only) and 資料更新, each as a small uppercase-tracked heading above a card of rows. Each row SHALL show an icon square, a title, a one-line description and its control on the right. At < 760px the page SHALL open as a pushed screen with a back button and no tab bar highlight, and segmented controls SHALL stack under their titles at full width (switch rows stay inline). Rows in 資料更新 SHALL show a status dot instead of an icon square, and stacked rows MAY omit the icon square. The back button SHALL return to the previous in-app page; when Settings is the router's first successful navigation (for example opened from a bookmark) it SHALL go to the first visible group's default page instead of leaving the app, and it SHALL be hidden when that fallback would be Settings itself. The content column SHALL be at most 860px wide.

#### Scenario: Phone layout
- **WHEN** Settings is opened at 390px
- **THEN** a back button SHALL precede the title 設定
- **AND** the 主題 control SHALL span the card width

### Requirement: Navigation section

The 導覽 section SHALL contain: a 顯示的分組 list with one switch per group (物資, 投資, 財務) plus 系統 marked 固定顯示; a 桌面側欄預設 control (展開 / 圖示欄) with the note that narrower screens always use the rail; a switch 開啟明細時自動收合側欄; and a switch 顯示待處理數字與紅點. Changes SHALL apply immediately to the sidebar and tab bar. At < 760px the section SHALL show a live preview of the resulting tab bar and the sentence explaining the gear rule when 設定 leaves the tab bar.

#### Scenario: Hiding 物資 updates the sidebar immediately
- **WHEN** the owner switches 物資 off
- **THEN** the 物資 group SHALL disappear from the sidebar without a reload

#### Scenario: Tab bar preview on the phone
- **GIVEN** 物資 is off
- **WHEN** the 導覽 section renders at 390px
- **THEN** the preview SHALL show 投資, "+", 財務 and the text explaining that 設定 moved to the gear

### Requirement: Accounting settings link

The 記帳 section SHALL be a single row 記帳設定 (description: 類別、帳戶分組、專案、對象、支出收入顏色、數字鍵盤、匯入 MOZE 備份) linking to `/accounting/settings`. Accounting preferences SHALL NOT be duplicated on this screen.

#### Scenario: Link opens accounting settings
- **WHEN** the owner taps 記帳設定
- **THEN** the router navigates to `/accounting/settings`

### Requirement: Keyboard shortcut reference

At ≥ 760px the 鍵盤快捷鍵 section SHALL list, two columns wide, the shortcuts the app implements: N 記一筆, E 編輯目前的記錄, ↑ ↓ 上一筆／下一筆, Enter 儲存, ⇧ Enter 儲存並續記, Esc 關閉明細或表單, each key drawn as a `<kbd>`.

#### Scenario: Reference matches the handler
- **WHEN** the section renders
- **THEN** every listed key SHALL be handled by the app (`keyboard-shortcuts.ts` or the entry form) and no handled key SHALL be missing

### Requirement: Data refresh schedule

The 資料更新 section SHALL list the portfolio scheduler jobs with their schedule: 盤中報價 (交易時段每 15 分鐘), 淨值快照 (每天 15:30), 收盤價回補（TWSE / TPEx）(每個交易日 17:00). Where an existing endpoint reports the last run (the quote refresh in `GET /api/portfolio/imports/recalc/status`), the row SHALL show it; otherwise only the schedule is shown.

#### Scenario: Last quote refresh shown
- **GIVEN** the status endpoint reports a quote refresh finished at 13:30
- **WHEN** the section renders
- **THEN** the 盤中報價 row SHALL show 13:30
