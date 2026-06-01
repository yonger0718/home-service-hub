# frontend-settings Specification

## Purpose
TBD - created by archiving change redesign-dashboard-handoff. Update Purpose after archive.

## Requirements
### Requirement: Top-level /settings route

The application SHALL register a new top-level route `/settings` whose component is the Settings screen described below. This route MUST NOT conflict with the existing `/accounting/settings` (management-center) route.

#### Scenario: Navigation to /settings renders Settings
- **WHEN** the user navigates to `/settings`
- **THEN** the Settings component renders inside the shell

### Requirement: Appearance toggle row

The Settings screen SHALL include a `.set-card` with a `.set-row` containing label 外觀模式 and a `SegToggle` with options 淺色 / 深色. Toggling the control MUST call `AppearanceService.setDark()` and reflect the current `dark$` value.

#### Scenario: Toggling appearance updates app
- **WHEN** the user selects 深色 on the appearance toggle
- **THEN** `AppearanceService.setDark(true)` is called
- **AND** the application body switches to dark mode immediately

### Requirement: Gain/loss toggle row with live preview

The Settings screen SHALL include a `.set-row` containing label 漲跌顏色 and a `SegToggle` with options 紅漲綠跌 / 綠漲紅跌, plus a live preview chip pair showing 上漲 / 下跌 chips that recolour according to the selected convention. Caption text MUST explain the 台股 vs 歐美 difference.

#### Scenario: Preview chips recolour live
- **WHEN** the user selects 綠漲紅跌
- **THEN** the preview 上漲 chip turns green and 下跌 chip turns red immediately
- **AND** `AppearanceService.setGainLoss('western')` is called

### Requirement: Settings persist across reload

Both toggles SHALL persist through `AppearanceService` so that reloading restores the choices.

#### Scenario: Reload restores both toggles
- **WHEN** the user sets dark + western, reloads
- **THEN** the Settings screen reflects 深色 and 綠漲紅跌 selected
