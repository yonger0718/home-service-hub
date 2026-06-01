# frontend-appearance-service Specification

## Purpose
TBD - created by archiving change redesign-dashboard-handoff. Update Purpose after archive.

## Requirements
### Requirement: AppearanceService manages dark mode and gain/loss convention

The frontend SHALL provide an injectable `AppearanceService` (`providedIn: 'root'`) exposing two `BehaviorSubject` streams: `dark$: BehaviorSubject<boolean>` and `gainLoss$: BehaviorSubject<'asian'|'western'>`, plus setter methods `setDark(value: boolean)` and `setGainLoss(value: 'asian'|'western')`.

#### Scenario: Setters update observables and root element
- **WHEN** `appearanceService.setDark(true)` is called
- **THEN** `dark$` emits `true`
- **AND** `document.documentElement.classList.contains('app-dark-mode')` is `true`
- **AND** `localStorage.getItem('hh-dark')` is `'1'`

#### Scenario: Gain/loss setter swaps attribute
- **WHEN** `appearanceService.setGainLoss('western')` is called
- **THEN** `gainLoss$` emits `'western'`
- **AND** `document.documentElement.getAttribute('data-gainloss')` is `'western'`
- **AND** `localStorage.getItem('hh-gainloss')` is `'western'`

### Requirement: Persisted state survives reload

Choices SHALL be persisted in `localStorage` under keys `hh-dark` (values `'1'`/`'0'`) and `hh-gainloss` (values `'asian'`/`'western'`).

#### Scenario: Reload restores persisted state
- **WHEN** the user has previously set dark to `true` and gain/loss to `western`, then reloads
- **THEN** the application initialises with `app-dark-mode` class present and `data-gainloss="western"` on the root

### Requirement: Default theme follows OS, default convention is asian

If no `hh-dark` value is stored, the initial dark mode SHALL match `window.matchMedia('(prefers-color-scheme: dark)').matches`. If no `hh-gainloss` value is stored, the initial convention SHALL be `'asian'`.

#### Scenario: First load on OS-dark system
- **WHEN** `localStorage.getItem('hh-dark')` returns `null` AND the OS reports dark preference
- **THEN** the application initialises in dark mode

#### Scenario: First load uses asian convention
- **WHEN** `localStorage.getItem('hh-gainloss')` returns `null`
- **THEN** the application initialises with `data-gainloss="asian"`

### Requirement: Pre-paint inline script eliminates flash

The frontend SHALL include an inline script in `frontend/src/index.html` that runs BEFORE Angular bootstraps, reads the same `localStorage` keys (or OS preference fallback), and applies the `app-dark-mode` class plus `data-gainloss` attribute to `document.documentElement`. This script MUST be idempotent with the `AppearanceService` initialization.

#### Scenario: No light-mode flash for dark user
- **WHEN** a user with `hh-dark=1` loads the application
- **THEN** the very first paint already shows dark theme — no observable flash from light to dark

### Requirement: Observable streams drive component reactivity

Components and charts SHALL subscribe to `dark$` and `gainLoss$` to react to changes within the same session (e.g., `chart.update('none')`, conditional templates, recompute derived colours).

#### Scenario: Chart updates on convention flip
- **WHEN** a chart component is subscribed to `gainLoss$` and the user toggles the convention in Settings
- **THEN** the chart re-reads its dataset colours from CSS vars and calls `chart.update('none')` to repaint
