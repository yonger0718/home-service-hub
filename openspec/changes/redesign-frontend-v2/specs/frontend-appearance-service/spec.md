## MODIFIED Requirements

### Requirement: AppearanceService manages dark mode and gain/loss convention

The frontend SHALL provide an injectable `AppearanceService` (`providedIn: 'root'`) exposing `theme$: BehaviorSubject<'light'|'dark'|'system'>`, `dark$: BehaviorSubject<boolean>` (the effective dark state, derived from `theme$` and the OS preference) and `gainLoss$: BehaviorSubject<'asian'|'western'>`, plus setters `setTheme(value: 'light'|'dark'|'system')`, `setDark(value: boolean)` (equivalent to `setTheme(value ? 'dark' : 'light')`) and `setGainLoss(value: 'asian'|'western')`. While `theme$` is `system`, the service SHALL listen to `prefers-color-scheme` changes and update `dark$` and the root class live.

#### Scenario: Setters update observables and root element
- **WHEN** `appearanceService.setTheme('dark')` is called
- **THEN** `theme$` emits `'dark'` and `dark$` emits `true`
- **AND** `document.documentElement.classList.contains('app-dark-mode')` is `true`
- **AND** `localStorage.getItem('hh-theme')` is `'dark'`

#### Scenario: System theme follows the OS live
- **GIVEN** `theme$` is `'system'` and the OS is light
- **WHEN** the OS switches to dark
- **THEN** `dark$` emits `true` and the root gains `app-dark-mode` without a reload

#### Scenario: Gain/loss setter swaps attribute
- **WHEN** `appearanceService.setGainLoss('western')` is called
- **THEN** `gainLoss$` emits `'western'`
- **AND** `document.documentElement.getAttribute('data-gainloss')` is `'western'`
- **AND** `localStorage.getItem('hh-gainloss')` is `'western'`

### Requirement: Persisted state survives reload

Choices SHALL be persisted in `localStorage` under `hh-theme` (`'light'`/`'dark'`/`'system'`) and `hh-gainloss` (`'asian'`/`'western'`). When `hh-theme` is absent and the legacy key `hh-dark` is present, the service SHALL migrate it once (`'1'` → `'dark'`, `'0'` → `'light'`), write `hh-theme` and remove `hh-dark`.

#### Scenario: Reload restores persisted state
- **WHEN** the user has previously set the theme to `dark` and gain/loss to `western`, then reloads
- **THEN** the application initialises with `app-dark-mode` class present and `data-gainloss="western"` on the root

#### Scenario: Legacy key is migrated
- **GIVEN** `hh-dark` is `'0'` and `hh-theme` is absent
- **WHEN** the application starts
- **THEN** `hh-theme` SHALL be `'light'` and `hh-dark` SHALL be removed

### Requirement: Default theme follows OS, default convention is asian

If neither `hh-theme` nor `hh-dark` is stored, the initial theme SHALL be `system`, so dark mode matches `window.matchMedia('(prefers-color-scheme: dark)').matches`. If no `hh-gainloss` value is stored, the initial convention SHALL be `'asian'`.

#### Scenario: First load on OS-dark system
- **WHEN** no theme key is stored AND the OS reports dark preference
- **THEN** `theme$` is `'system'` and the application initialises in dark mode

#### Scenario: First load uses asian convention
- **WHEN** `localStorage.getItem('hh-gainloss')` returns `null`
- **THEN** the application initialises with `data-gainloss="asian"`

### Requirement: Pre-paint inline script eliminates flash

The frontend SHALL include an inline script in `frontend/src/index.html` that runs BEFORE Angular bootstraps, reads `hh-theme` (falling back to `hh-dark`, then to the OS preference) and `hh-gainloss`, and applies the `app-dark-mode` class plus `data-gainloss` attribute to `document.documentElement`. This script MUST be idempotent with the `AppearanceService` initialization.

#### Scenario: No light-mode flash for dark user
- **WHEN** a user with `hh-theme=dark` loads the application
- **THEN** the very first paint already shows dark theme — no observable flash from light to dark

## ADDED Requirements

### Requirement: Navigation preferences persist alongside appearance

The frontend SHALL provide `NavigationPreferencesService` (`providedIn: 'root'`) holding the navigation preferences `hiddenGroups`, `sidebarDefault`, `autoCollapse`, `showBadges` and `foldedGroups`, each persisted in its own `localStorage` key with the `hh-nav-` prefix. The group `system` SHALL never be accepted into `hiddenGroups`.

#### Scenario: Defaults on first load
- **GIVEN** no `hh-nav-*` key is stored
- **WHEN** the service initialises
- **THEN** `hiddenGroups` SHALL be `['supplies']`, `sidebarDefault` `'expanded'`, `autoCollapse` `true`, `showBadges` `true` and `foldedGroups` `[]`

#### Scenario: Storage keys
- **WHEN** each preference is changed
- **THEN** it SHALL be written to `hh-nav-hidden`, `hh-nav-sidebar`, `hh-nav-autocollapse`, `hh-nav-badges` or `hh-nav-folded` respectively

#### Scenario: Hiding a group persists
- **WHEN** the owner hides 投資 in Settings and reloads
- **THEN** `hiddenGroups` SHALL contain `'portfolio'` and the 投資 group SHALL not render

#### Scenario: System cannot be hidden
- **WHEN** code calls `setHidden('system', true)`
- **THEN** `hiddenGroups` SHALL be unchanged
