## Context

The Angular dashboard under `frontend/` currently uses muted slate trend colours flagged for deprecation in `frontend/src/styles.scss`, lacks dark-mode support, has no gain/loss colour convention, and does not match the handoff structure (no bento KPI grid, no dock/mobile-nav shell, no settings hub). The handoff in `design_handoff_dashboard/` is high-fidelity and self-sufficient: `design_refs/colors_and_type.css` is the authoritative token file, `design_refs/ui_kits/hub-dashboard/kit.css` is the compiled component CSS, and the `.jsx` files (`screens-*.jsx`, `components.jsx`) are React mockups whose structure must be translated into Angular + PrimeNG.

Existing routes already cover 8 of 9 handoff screens (only `/settings` is new). Out-of-handoff routes exist for `/portfolio/realized-pnl`, `/accounting/settings` (management-center), `/accounting/cards`, `/accounting/categories`, `/accounting/recurring` and must keep working — they are restyled via token inheritance only. There is no auth, no marketing page, and no backend API changes in scope.

Backend Python services (`accounting-service`, `stock-portfolio-service`, `inventory-api`) and existing services in `frontend/src/app/services/` are untouched.

## Goals / Non-Goals

**Goals:**
- Lift `colors_and_type.css` tokens verbatim into `styles.scss` (light + dark blocks).
- Resolve gain/loss colours via `[data-gainloss="asian"|"western"]` attribute selectors so a single root-attribute flip recolours every trend element in the app.
- Provide an `AppearanceService` that initializes from OS `prefers-color-scheme` (when no stored value), persists user choice in `localStorage`, and exposes observable streams so components and charts react to changes.
- Eliminate dark-mode flash via inline pre-paint script in `index.html`.
- Build a new shell (top frosted header + left dock ≥760px / bottom mobile nav <760px + segmented sub-nav) used by every route.
- Ship a stateless UI primitive set (`Btn`, `Tag`, `SegToggle`, `Bento`, `PctBadge`, `SideTag`, `Timeline`, `FileChip`) so screens compose primitives instead of duplicating markup.
- Recreate the 9 handoff screens pixel-faithfully using PrimeNG (`p-chart`, PrimeIcons, p-dialog).
- Keep cashflow colours (income/expense) decoupled from `data-gainloss`.
- Keep out-of-handoff routes (`realized-pnl`, accounting `cards`/`categories`/`recurring`/`management-center`) reachable and visually consistent via token inheritance — no structural redesign.

**Non-Goals:**
- No authentication, login, sign-up, route guards, or session flows.
- No marketing / landing / pricing page.
- No new backend endpoints, no API schema changes, no DB migrations.
- No third-party design or icon library (PrimeIcons only).
- No web fonts (system stack per handoff).
- No structural redesign of out-of-handoff routes — token inheritance only.
- No shopping-list visual design — empty-state placeholder only.

## Decisions

### D1: Lift `colors_and_type.css` verbatim, not adapt

Copy `design_refs/colors_and_type.css` directly into `frontend/src/styles.scss`'s `:root`, `[data-gainloss="asian"|"western"]`, and dark-mode blocks. Names already align with the production `--app-*` vars; only values change. Drop the deprecated slate trend tokens.

**Alternative considered:** keep current variable names and remap values. Rejected — risks subtle drift and a second source of truth.

### D2: Gain/loss via attribute selector on `<html>`, single observable for switch

`[data-gainloss="asian"]` resolves `--app-trend-positive: var(--c-red)` / `--app-trend-negative: var(--c-green)`; `[data-gainloss="western"]` swaps them. Every component reads `var(--app-trend-positive/negative)` — there are zero hard-coded gain/loss colours in component CSS. `AppearanceService.gainLoss$` is the only writer.

**Alternative considered:** `:host-context()` per-component class. Rejected — every component then has to opt in; attribute-on-root flips the whole tree in one paint.

### D3: AppearanceService = `BehaviorSubject` pair + `APP_INITIALIZER` + pre-paint inline script

- Inline script in `index.html` reads `localStorage.getItem('hh-dark')` and `localStorage.getItem('hh-gainloss')`, falls back to `prefers-color-scheme` for dark and `'asian'` for gain/loss, then sets `documentElement.classList.toggle('app-dark-mode', …)` and `documentElement.setAttribute('data-gainloss', …)` BEFORE Angular bootstraps. Kills first-paint flash.
- `AppearanceService` is `providedIn: 'root'`, holds `dark$: BehaviorSubject<boolean>` and `gainLoss$: BehaviorSubject<'asian'|'western'>`, with `setDark()` / `setGainLoss()` methods that update the subject, write `localStorage`, and toggle the same root attributes.
- `APP_INITIALIZER` injects the service so its constructor runs early (idempotent with the inline script).

**Alternative considered:** Angular Signals instead of `BehaviorSubject`. Rejected for now — codebase already uses RxJS in services; adding signals here is scope creep.

### D4: Stateless UI primitives, not PrimeNG wrappers

`Btn`, `Tag`, `SegToggle`, `Bento`, `PctBadge`, `SideTag`, `Timeline`, `FileChip` are thin Angular components reading tokens. `Btn` does NOT wrap `p-button` — handoff visuals differ enough that adapting the PrimeNG theme is more brittle than a 30-line component. `p-chart`, `p-dialog`, `p-select`, `p-inputtext` remain PrimeNG where they pull weight.

**Alternative considered:** override PrimeNG theme variables. Rejected — handoff visuals (bento tile, side-tag pill, segmented toggle) have no PrimeNG counterpart of equivalent shape.

### D5: Chart.js — `animation: false`, read CSS vars at draw time, subscribe to AppearanceService

Every `p-chart` instance:
1. Sets `options.animation = false` (handoff note — throttled iframes leave animated canvases blank).
2. Reads colours via `getComputedStyle(document.documentElement).getPropertyValue('--app-...')` inside dataset config so theme + convention are always current.
3. Injects `AppearanceService`, subscribes to `combineLatest(dark$, gainLoss$)`, and calls `chart.update('none')` on change.

### D6: Net-worth chart range = component-local state, default `1Y`

Range selector (1M/3M/YTD/1Y/5Y) lives on `PortfolioDashboardComponent`. Selecting a range:
- Slices the same source series (or calls a range param on existing service if available).
- Recomputes the `.pct-badge` period return.
- Updates the XIRR tile to the chosen window's annualized rate.

Default `'1Y'`. State is local; not persisted.

### D7: Cashflow colours hard-coded, NOT bound to `data-gainloss`

Per handoff: income always green, expense neutral, spending-increase always red — regardless of stock convention. Accounting components use `var(--c-green)` / `var(--app-text-muted)` / `var(--c-red)` directly, NOT `--app-trend-*`. This is a load-bearing invariant; document it inline.

### D8: Out-of-handoff routes keep current layout, restyle via token cascade only

`/portfolio/realized-pnl`, `/accounting/settings`, `/accounting/cards`, `/accounting/categories`, `/accounting/recurring` already read `--app-*` vars. Token rewrite makes them visually consistent automatically. **No template changes** for these routes in this change. They remain reachable via dock sub-items (Portfolio group keeps `realized-pnl`; Accounting group keeps management routes).

### D9: New `/settings` is a sibling of existing routes, not a replacement

`/settings` handles appearance + gain/loss only (handoff screen #7). Existing `/accounting/settings` (management-center) stays at its current path; it is not an "appearance settings" hub. Avoids breaking deep links and keeps the two concerns separated.

### D10: Shell breakpoint at 760px

Dock visible ≥760px; mobile nav visible <760px. Implemented via CSS media query plus `BreakpointObserver` for any JS that needs to know (e.g., conditional template branches via `@if`).

### D11: Assets — copy SVG into `frontend/src/assets/`

Copy `design_refs/assets/logo-lockup.svg` + `app-icon.svg` into `frontend/src/assets/` and reference from dock + header. Do not load from `design_handoff_dashboard/` at runtime — that path is handoff documentation, not a runtime asset folder.

### D12: Test strategy

Existing Vitest specs (`*.spec.ts`) test logic; most logic is unchanged. Selector-based DOM assertions will break for any rewritten template — update selectors to match the new structure. Add new specs for:
- `AppearanceService`: localStorage roundtrip, OS preference fallback, root-attribute application, observable emissions.
- UI primitives: render + emit (`SegToggle.change`, `Btn.click`, qty stepper inc/dec).

No backend test changes.

## Risks / Trade-offs

- **Risk**: Token rewrite changes every screen's visual at once — out-of-handoff routes (`realized-pnl`, management-center, etc.) may look worse if their custom CSS expected the old slate trend values. **Mitigation**: audit these route components after token swap; if any hard-codes hex values, replace with the new tokens; if it relies on the old slate `--app-trend-*`, swap to `--app-success`/`--app-text-muted` to keep the non-trend slate look.
- **Risk**: `p-chart` colour cache — reading CSS vars at draw time is fine for first paint, but Chart.js caches dataset config; theme/convention flip needs `chart.update()` to repaint. **Mitigation**: subscribe each chart to `AppearanceService` and call `chart.update('none')`; documented in D5.
- **Risk**: Inline pre-paint script in `index.html` runs before Angular and CSP could block it. **Mitigation**: existing app has no CSP nonce policy; if introduced later, replace with a CSP-allowed bootstrap. Document the dependency in `index.html` comment.
- **Risk**: Vitest spec churn — every rewritten template breaks selector-based assertions. **Mitigation**: scope test updates per-screen alongside template rewrite; do not batch.
- **Risk**: Stateless primitives diverge from PrimeNG accessibility (keyboard nav, ARIA). **Mitigation**: `SegToggle`, `Btn`, qty stepper get explicit `role`, `aria-pressed`, `aria-label`, keyboard handlers (arrow keys for seg-toggle). Document in UI primitives spec.
- **Trade-off**: Big bang vs incremental. Chose big bang per user direction — short-term review burden; long-term coherent visual ship.
- **Trade-off**: Cashflow decoupled from convention. Two parallel colour systems (market trend vs cashflow). Documented as load-bearing in D7 to prevent future "unification" refactor.
- **Trade-off**: 760px breakpoint is a hard switch (no tablet layer). Matches handoff; keeps shell simple.

## Migration Plan

1. **Tokens** — Replace `styles.scss` token blocks. Verify existing screens still render (will look slightly off until shell + primitives land — acceptable mid-PR state on a feature branch).
2. **AppearanceService + index.html script** — Land service and pre-paint script. Add Settings stub route so the toggles can be exercised manually.
3. **Shell + dock + mobile-nav** — Replace `app.html`; verify all existing routes navigate correctly.
4. **UI primitives** — Land each as a standalone component with a spec.
5. **Screens** — Migrate in this order: `portfolio` → `transactions` → `dividends` → `import` → `accounting-dash` → `accounting` → `inventory` → `settings` → `shopping` (empty state). Each screen: template rewrite, scss using new primitives, update affected specs.
6. **Out-of-handoff route audit** — Visually verify `realized-pnl` and accounting management routes after token + shell land; fix any hard-coded colour drift.
7. **Manual smoke** — exercise dark toggle, gain/loss toggle, range selector, dialog open/close, qty steppers, CSV import flow.

**Rollback**: revert the branch / PR. No DB migrations, no backend changes — rollback is cheap.

## Open Questions

- None blocking. Real net-worth series source (existing API vs new range-aware endpoint) will be confirmed during portfolio screen implementation; mocks in `screens-finance.jsx` show client-side slicing is acceptable for v1.
