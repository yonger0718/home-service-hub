# Redesign v2 PR 7 — Accounting summary category bar + verification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the 支出類別分佈 bar (five largest top-level expense categories + 其他, category colours, segments summing to the month's expense) to the accounting timeline summary, and close the change's section 8 verification (test/build, every route at four widths × two themes, the small-trend-text contrast fix).

**Architecture:** Option A (owner ruling 2026-10-23): `GET /entries/summary` gains an additive `by_category` list computed in the same query family as the month totals (same inclusion rules, same rate conversion, same `missing_rates` exclusion), grouped by the entry's top-level category. The frontend keeps the top-five + 其他 rule and the aria text in a pure helper next to the timeline; the bar reuses the PR 3 allocation-bar markup pattern. Contrast (8.3) adds darker "ink" variants of the trend tokens for small text and a dark-ink primary button in dark mode, gated by computed ratios.

**Tech Stack:** FastAPI + SQLAlchemy (accounting-service, Postgres integration tests via `tests/conftest.py`), Angular 21 standalone/zoneless/signals + Vitest, Playwright (controller, Docker) for the route sweep.

**Rev 3 (owner ruling 2026-10-23 「1 跟 2 都改」, after the final review):** Decision 3 replaces the scale branch with real amounts + a legend-only 退款 line (Σ positives) and draws no bar when the month's expense is ≥ 0; Task 2/3 tests follow.

**Rev 2 (AGENT-231, dev-grok, CHANGES-REQUESTED at 0aa6181):** Decision 3 rewritten (target = |expense|; refund-only month → one 其他; negative remainder → scale the top five to the target, no 其他); invariant is the raw flex sum, labels are display; root colour = the root's own (not the `:433` row coalesce), `outerjoin`, 午餐 gets its own colour in the test; FX quantize note (reuse `_convert`, 4 dp strings; do not recompute `missing_rates`); dark primary hover covered; Task 4 fences (default + `[data-gainloss]` blocks, ≤ 13 px, accounting `--c-*-ink`, portfolio ink pair, spec reads both theme blocks, assert a large selector keeps `--c-red`); route sweep handles the matcher/param routes with object stubs and sets the viewport before `goto`.

**Spec:** `openspec/changes/redesign-frontend-v2/specs/frontend-accounting-ledger/spec.md` (requirement `:5`, scenario `:16-19`), `tasks.md:53-61`, canvas `design_handoff_v2/design_refs/AccountingDesktop.dc.html:93-107` and `TimelinePhone.dc.html:57-82`, contrast note `design_handoff_v2/README.md:50-53`.

**Branch:** `feat/redesign-v2-accounting` from `origin/main` d345da6 (worktree `~/workspace/home-hub-redesign`).

## Global Constraints

- Backend change is additive and response-only (`by_category` on `MonthSummaryOut`); no migration; existing `expense` / `income` / `net` / `missing_rates` values and the daily summary are byte-identical to today (`design.md:18` lists backend changes as out of scope — the owner ruled the additive field in on 2026-10-23, same as PR 5/6).
- The bar's segments SHALL sum to the month's expense (spec `:19`). Satisfied as Σ bar segments − 退款 = |expense| (Decision 3 rev 3): the bar shows real spending per category, the 退款 legend line reconciles it to the header; never a client-side re-sum of entries; no bar in a net-refund month. Legend labels are display (integer rounding) and may differ from the header by rounding.
- Copy verbatim: 支出類別分佈 (aria-label), 其他, 未分類 (uncategorised rows, Decision 2); legend amounts are thousands-grouped integers without currency sign or percentage (canvas `:102-105`).
- Colours come from the category settings: the **root** category's own `Category.color` (the row chip's child-first coalesce at `ledger_service.py:433` is a different rule — a bar segment is per root); no palette of our own except the two fallbacks in Decision 4.
- Phone (`LayoutModeService` `phone`, < 760 px): 8 px bar, no legend (canvas TimelinePhone `:70-74`); sheet/panes: 10 px bar + wrapping legend (AccountingDesktop `:94-106`).
- The summary's existing rules stay: 全月 · 未套篩選 (the bar ignores filters like the totals), `aria-busy` while loading, error block with 重試, no bar while `summary()` is null.
- Specs restore globals (`LayoutModeService.set(null)`, `localStorage`, timers, spies); no `.only`.
- Style budgets: `timeline.scss` stays under its budget; `dashboard.scss` 62 B notice is pre-existing and untouched.
- Deploy order: accounting API first (`homehub-api` checkout + `homehub-api-ctl restart`, no alembic), then the SPA release. Old SPA + new API: field ignored; new SPA + old API: no bar (field absent → `[]`).

## Decisions

1. **Response shape.** `MonthSummaryOut.by_category: list[CategoryExpenseOut] = []` where `CategoryExpenseOut = {category_id: int | None, name: str, color: str | None, expense: Decimal}`; `expense` is negative like the month `expense` (4 dp, main currency), one item per top-level category with spending rows in the month, sorted by `expense` ascending (largest spend first), ties by name. Only expense-side rows count (`_spend_filter()`: negative expense/fee amounts + refunds), so Σ `by_category[].expense` == `expense` whenever every currency has a rate; rows in a currency without a rate are left out of both, exactly as today.
2. **Top-level grouping.** `root = coalesce(parent.id, category.id)` via the same `aliased(Category)` join as `list_entries` (`ledger_service.py:424-441`); name/colour are the root's (`parent.name`/`parent.color` when the entry sits on a subcategory, else the category's own). Rows with `category_id IS NULL` form one item `{category_id: null, name: '未分類', color: null}`.
3. **Top five + 其他 + 退款 (frontend, pure) — rev 3, owner ruling 2026-10-23 (1 跟 2 都改).** `expense = Number(summary.expense)`; `expense >= 0` (no spending, or a net-refund month) or `by_category` absent/empty → `[]` (no bar). Otherwise: negatives = roots with `expense < 0` (magnitudes, real amounts), positives = roots with `expense > 0` (net-refund roots). Rank negatives by magnitude desc (ties by name), take five → `top` with their **real** amounts; `其他 = Σ negatives − Σ top` (the tail incl. 未分類), shown when ≥ 0.01; `退款 = Σ positives`, shown as a **legend-only** line (`kind: 'refund'`, no bar segment, no swatch) when ≥ 0.01. Arithmetic shown to the user: Σ top + 其他 − 退款 = |expense| (up to the per-bucket FX quantising, ≤ 0.0001 × buckets — never surfaced as a segment). The bar draws `top` + 其他 proportionally to the real amounts (nothing is scaled any more). Segments carry `{name, amount, label, color, kind: 'category' | 'other' | 'refund'}`; aria text lists every legend line in order (`…、其他 7,460、退款 300`).

4. **Colour fallbacks.** A root without a colour → `var(--app-text-muted)` (not `--app-surface-soft`, which is invisible on the soft summary background); 其他 → `#8a94a6` (canvas `C.other`, TimelinePhone `:165`) exposed as `--app-category-other` in `styles.scss` (same value in both themes). FX note: the sibling query converts per (root, currency) with `_convert` (4 dp HALF_UP), so Σ `by_category` may differ from `expense` by 0.0001 per extra bucket at tiny rates; a 其他 tail below 0.01 is not shown and the 退款 line reconciles the rest (Decision 3 rev 3); `missing_rates` is never recomputed from the spend query (an income-only unrated currency stays listed).
5. **Accessibility.** The bar is `role="img"` with `aria-label="支出類別分佈：餐飲 9,820、居家 8,150、…、其他 7,460"` built by `distributionAriaText(segments)`; the legend is plain text (phone has none, so the aria text is the only legend there). Segments narrower than 2 px are still rendered (flex) — no min-width hack.
6. **Where it lives.** Inside the existing `.sum` block, as a full-width row (`flex: 1 1 100%`) after the three figures, so the `aria-busy` / error / 全月 rules apply unchanged; markup mirrors `dashboard.html:192-205` (`.dist-bar` / `.dist-list` / `.dist-row` / `.dist-swatch`).
7. **Contrast (8.3), full scope (reviewer recommendation).** New tokens `--c-red-ink` / `--c-green-ink` (light `#c53a3f` = 5.04 / 4.57:1 on `--app-surface` / `--app-surface-soft`, `#177a55` = 5.17 / 4.69; dark: the existing `--c-red` `#ff6369` (5.90 / 5.17) and `--c-green` `#43c98a` (8.11 / 7.12) — keep the dark hues) plus `--app-trend-positive-ink` / `--app-trend-negative-ink` defined on the default pair (`styles.scss:61-62`) **and** in both `[data-gainloss]` blocks (`:305-312`; production sets the attribute from `index.html`, the default block applies when absent). Applied only to trend-coloured **text** at `font-size ≤ 13 px` (the README says 12–13 px): accounting small text (`--tone-neg` / `--c-red` users in `accounting-layout.scss` and the audited accounting stylesheets) → `--c-red-ink` / `--c-green-ink`; portfolio `pct-badge` (`0.78rem`) → the convention ink pair; `.sum b` (`timeline.scss:92`, 0.95rem) and every large figure keep the bright tokens. Dark-mode primary `app-btn`: fill `--app-primary-ink-bg` = `#6c5ce7` (4.86:1 with white) for rest **and hover** (`btn.scss:40-42` hover uses `--app-primary-hover` `#9a8ffd` = 2.72:1 → the dark override covers `:hover` too; `:active` keeps the fill), consumed by `btn.scss` only.
8. **Route sweep (8.2).** A controller-run Playwright script visits every SPA **screen** at 390 / 820 / 1280 / 1440 × light / dark: the literal `app.routes.ts` paths plus concrete instances of the parameterised / matcher routes (`portfolio/accounts/7`, `accounting/entries/1`, statement `:sid` / `:eid` samples) — redirects (`accounting/dashboard`, `import-broker`, `**`) are not screens. The viewport is set before `goto` (`canMatch` picks the phone table below 760). The catch-all API stub answers list GETs with empty lists / zeros and detail GETs with a minimal object (plus the PR 3–6 fixture stubs where a page needs data to render its redesign); assert no page error and `scrollWidth === innerWidth`. The per-PR stub scripts already cover 390/820/1440 with data, so the sweep adds 1280 everywhere (the sidebar expands at `min-width: 1280px`) and the empty states at all widths.

## Review Focus

1. A month where one category's refunds exceed its spending (net ≥ 0) so the remainder goes negative (scale branch, −800/−200/+300 → 560 + 140), the absorb case (其他 50), and a refund-only month (one 其他 = target) — Σ amounts == |expense| in every branch, never a negative/zero segment (Task 2 tests).
2. Rows in a currency without a rate — excluded from both the totals and `by_category`, `missing_rates` unchanged (Task 1 test).
3. Subcategory spending rolls up to the parent (name, colour, id of the root), uncategorised rows → 未分類 (Task 1 tests).
4. Phone vs sheet/panes rendering and the 全月 / error / loading states — bar hidden on error and while loading, legend absent on phone (Task 3 tests).
5. Contrast: the computed ratios for every (ink token, surface) pair in both themes ≥ 4.5:1, and no large-figure selector switched to the ink tokens by mistake (Task 4 test + review).

---

### Task 1: Backend — `by_category` on the month summary

**Files:**
- Modify: `services/accounting-service/app/schemas/ledger.py:203-209` (`MonthSummaryOut` + new `CategoryExpenseOut`)
- Modify: `services/accounting-service/app/services/ledger_service.py:577-634` (`_converted_totals` gains an optional category grouping, or a sibling `_category_expense(db, month, main, rates)`; `month_summary` fills `by_category`)
- Test: `services/accounting-service/tests/integration/test_entries_api.py` (next to `test_month_summary_totals_in_main_currency` `:370`)

**Interfaces:**
- Produces: `GET /entries/summary` → `{…, by_category: [{category_id, name, color, expense}]}` (Decision 1/2). Daily summary untouched.

- [ ] **Step 1: Failing tests** (`tests/integration/test_entries_api.py`, Postgres via `tests/conftest.py`):
  - `test_month_summary_by_category_rolls_up_to_root`: categories 餐飲 (colour `#ff8800`) with child 午餐 (its **own** colour `#00ccff`, so a group-by of the row-level coalesced colour would split the root — expect 餐飲's `#ff8800`), 居家 (no colour), all `Category(kind='expense', …)` as `test_entries_api.py:259` does (one level only: `settings_service.py:713` rejects deeper nesting, so `coalesce(parent.id, category.id)` is the root), (create the `Category` rows directly — `tests/helpers.py` has no category helper; `make_entry(…, category_id=…)` passes extra columns through) entries −300 on 午餐, −200 on 餐飲, −500 on 居家, −50 with `category_id=None`, a refund +100 on 午餐, income +1000 → `by_category == [{居家 −500, color None}, {餐飲 −400, color '#ff8800', category_id = 餐飲.id}, {未分類 −50, category_id None, color None}]`; `expense == -950`; Σ by_category == expense.
  - `test_month_summary_by_category_skips_unrated_currency`: a JPY entry without a rate → not in `by_category`, `missing_rates == ['JPY']`, totals as today.
  - Existing `test_month_summary_totals_in_main_currency` gains `by_category` in its expected body (the −1000/−15/+570 rows all uncategorised → one 未分類 item `-445.0000`, plus the JPY −5000 × 0.2 = −1000 → still 未分類 → single item `-1445.0000`; adjust to whatever `make_entry` assigns — read `tests/helpers.py`).
- [ ] **Step 2: Run** `cd services/accounting-service && .venv/bin/python -m pytest tests/integration/test_entries_api.py -q -k summary` → FAIL (`by_category` missing). If no `.venv`, build one from the live accounting venv freeze as the PR 6 backend task did for the portfolio service (ask the controller for the pins path).
- [ ] **Step 3: Implement.** Schema: `class CategoryExpenseOut(BaseModel): category_id: int | None; name: str; color: str | None; expense: Decimal` and `by_category: list[CategoryExpenseOut] = []` on `MonthSummaryOut`. Service: one extra grouped query over `_spend_filter()` rows in `[start, end)` **outer-joined** to `Category` and to `aliased(Category)` parent (an inner join would drop `category_id IS NULL`, so 未分類 would never appear): group by `(coalesce(parent.id, Category.id), coalesce(parent.name, Category.name), coalesce(parent.color, Category.color) — i.e. the root's own colour, not the row chip's child-first coalesce — , currency)`, convert per (root, currency) with `_convert` and the same `rates` map (reuse `latest_rates(db, currencies, main)`; do not recompute `missing_rates` from this query), skip missing-rate currencies, sum per root, drop roots whose converted sum is 0, sort by `expense` asc then `name`; values stay 4 dp Decimals (JSON `"-500.0000"` like `test_entries_api.py:395-398`). Keep `_converted_totals` untouched (daily summary parity).
- [ ] **Step 4: Run** the three tests → PASS; then `pytest tests -q -x` (unit + integration) → green.
- [ ] **Step 5: Commit** `feat(api,accounting): by_category on the month summary`.

### Task 2: Frontend model + pure distribution helper

**Files:**
- Modify: `frontend/src/app/models/accounting.model.ts:244-253` (`MonthSummary.by_category?: CategoryExpense[]`, `CategoryExpense`)
- Create: `frontend/src/app/components/accounting/timeline/category-distribution.ts` (+ `.spec.ts`)
- Modify: `frontend/src/styles.scss` (`--app-category-other: #8a94a6` in `:root` and `.app-dark-mode`)

**Interfaces:**
- Produces: `interface DistributionSegment { name: string; amount: number; label: string; color: string; other: boolean }`; `categoryDistribution(summary: MonthSummary | null): DistributionSegment[]` (Decision 3/4); `distributionAriaText(segments: DistributionSegment[]): string` (Decision 5); `OTHER_COLOR = 'var(--app-category-other)'`, `FALLBACK_COLOR = 'var(--app-text-muted)'`.

- [ ] **Step 1: Failing tests** (`category-distribution.spec.ts`): canvas fixture (餐飲 9,820 … 娛樂 2,860 + three small categories summing to 7,460, expense −38,460) → five named segments in order + 其他 7,460, labels `9,820` … `7,460`, Σ amounts == 38,460; a root with `color: null` → `FALLBACK_COLOR`; 其他 → `OTHER_COLOR` with `other: true`; **absorb**: 500/400/300/200/100/80 + a refund-only root `+30` (expense −1550) → top five raw + 其他 50; **scale**: 餐飲 −800, 居家 −200, 交通 +300 (expense −700) → 560 + 140, no 其他, Σ == 700; **refund-only month**: two positive roots, expense `150.0000` → one 其他 150; exactly five negative roots and no positive root → no 其他; five negative roots plus a net-positive root with Σ top > target → scale branch (no 其他); four categories whose sum is below |expense| (未分類 / rounding) → 其他 = remainder; a sub-cent negative remainder (FX quantize, e.g. target 999.9999 vs Σ top 1000.0000) → scale branch, Σ == target; `expense '0'` or `by_category` absent → `[]`; aria text `支出類別分佈：餐飲 9,820、…、其他 7,460`; legend labels are `Math.round` + `toLocaleString('en-US')` and may differ from the header's whole-unit TWD by rounding (invariant = raw amounts).
- [ ] **Step 2: Run** `cd frontend && npx ng test --watch=false --include src/app/components/accounting/timeline/category-distribution.spec.ts` → FAIL.
- [ ] **Step 3: Implement** the helper and the model fields; add the token to `styles.scss` (both themes).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(accounting): category distribution helper and summary model fields`.

### Task 3: Timeline summary bar + legend

**Files:**
- Modify: `frontend/src/app/components/accounting/timeline/timeline.html:24-38`, `timeline.ts` (one `computed` `distribution = computed(() => categoryDistribution(this.summary()))`, `distributionAria = computed(...)`; reuse the existing `isPhone` computed at `timeline.ts:442`), `timeline.scss:66-80, 275-287`
- Test: `timeline.spec.ts` (fixtures via `flushSummary(month, { by_category: […] })` `:73-77`)

**Interfaces:** consumes Task 2.

- [ ] **Step 1: Failing tests** (`timeline.spec.ts`, keep every existing test green): summary with six categories at the default (sheet) layout → `.dist-bar[role=img]` with `aria-label` from the helper, six `.dist-bar > span` whose `flex-grow` equals the raw amounts and `background` the colours (`--app-category-other` for the last), `.dist-row` legend rows `餐飲 9,820` … `其他 7,460`; `LayoutModeService.set('phone')` → bar present, no `.dist-list`; `LayoutModeService.set('panes')` → legend present; summary with `expense '0'` → no `.dist-bar`; summary error → no bar (the error block replaces the figures); while loading (`summary()` null) → no bar; `filtersActive()` → bar still shows the full-month data (the 全月 · 未套篩選 scope line stays first); the three figures (`.sum-expense` etc.) unchanged; `afterEach` resets `LayoutModeService.set(null)`.
- [ ] **Step 2: Run** `npx ng test --watch=false --include src/app/components/accounting/timeline/timeline.spec.ts` → FAIL.
- [ ] **Step 3: Implement.** Markup after the three figure `<div>`s inside the `@else` branch: `@if (distribution().length) { <div class="dist"> <div class="dist-bar" role="img" [attr.aria-label]="distributionAria()"> @for … <span [style.flex-grow]="seg.amount" [style.background]="seg.color"> } </div> @if (!isPhone()) { <div class="dist-list"> @for … <span class="dist-row"><span class="dist-swatch" aria-hidden="true" [style.background]="seg.color"></span><span class="dist-name">{{ seg.name }}</span><b>{{ seg.label }}</b></span> } </div> } </div> }`. SCSS: `.dist { flex: 1 1 100%; display: flex; flex-direction: column; gap: 10px; }`, `.dist-bar { display: flex; gap: 2px; height: 10px; border-radius: 999px; overflow: hidden; span { flex-basis: 0; flex-shrink: 1; } }`, phone media query → `height: 8px`, `.dist-list { display: flex; flex-wrap: wrap; gap: 6px 16px; font-size: 13px; }`, `.dist-row { display: flex; align-items: center; gap: 6px; }`, `.dist-swatch { width: 8px; height: 8px; border-radius: 3px; }`, `.dist-name { color: var(--app-text-muted); }`.
- [ ] **Step 4: Run** the timeline spec + `npx ng build --configuration production 2>&1 | grep -iE "error|budget"` (no new budget warning) → PASS.
- [ ] **Step 5: Commit** `feat(accounting): 支出類別分佈 bar in the timeline summary`.

### Task 4: Contrast (8.3) — ink tokens for small trend text and the dark primary button

**Files:**
- Create: `frontend/src/app/theme/contrast.spec.ts` (pure WCAG ratio test over the token hex values; parse them from `styles.scss` with a small regex so the test fails when a token drifts)
- Modify: `frontend/src/styles.scss:54-62, 173-180, 303-312` (new `--c-red-ink`, `--c-green-ink`, `--app-trend-positive-ink`, `--app-trend-negative-ink` in the default pair and both `[data-gainloss]` blocks, `--app-primary-ink-bg` + its hover), `frontend/src/app/components/ui/pct-badge/pct-badge.scss`, `frontend/src/app/components/ui/btn/btn.scss` (dark primary fill, rest + hover), `accounting-layout.scss` (`--tone-neg` / `--tone-pos` small-text users) and every stylesheet the audit lists: run `grep -rn -e trend-positive -e trend-negative -e tone-neg -e tone-pos -e "--c-red" -e "--c-green" frontend/src/app --include='*.scss'` (quote the glob), open each hit, and switch only selectors whose computed font-size is ≤ 13 px and whose property is `color`.
- Modify: `design_handoff_v2/README.md:50-53` (append the measured result).

**Interfaces:** none downstream.

- [ ] **Step 1: Failing test** (`contrast.spec.ts`): `ratio(fg, bg)` per WCAG 2.x; the test slices `styles.scss` into the `:root` block and the `.app-dark-mode` block **separately** (a first-match regex would return the light hex for the dark assertion) and asserts: light `--c-red-ink` / `--c-green-ink` ≥ 4.5 on `--app-surface` and `--app-surface-soft`; dark likewise; `#ffffff` on the dark `--app-primary-ink-bg` ≥ 4.5 for rest and for the hover fill; the ink trend pair exists in the default block and in both `[data-gainloss]` blocks; and one large selector still references the bright token (e.g. `timeline.scss` `.sum b` → `--c-red`, read from the file) so a selector swap is caught.
- [ ] **Step 2: Run** → FAIL (tokens missing).
- [ ] **Step 3: Implement.** Pick hex values by computation (start from `#c53a3f` / `#177a55` light; keep the dark values if they already pass, else darken/lighten until ≥ 4.5 on both surfaces); add the tokens; apply to the audited small-text selectors; `btn.scss` dark primary uses `--app-primary-ink-bg`. Write the audit (selector → file:line → before/after ratio) into the task report and a two-line note in `design_handoff_v2/README.md`.
- [ ] **Step 4: Run** `contrast.spec.ts` + the specs of every component touched + `npx ng build … | grep budget` → PASS, no new budget warning.
- [ ] **Step 5: Commit** `style(theme): ink variants for small trend-coloured text; dark primary button contrast`.

### Task 5: Verification and bookkeeping (8.1–8.3, 7.1)

**Files:** `openspec/changes/redesign-frontend-v2/tasks.md:55-61`, `specs/frontend-accounting-ledger/spec.md` (implementation notes), controller script `pr7sweep.py` (scratchpad → report dir).

- [ ] **Step 1 (implementer):** `npx ng test --watch=false` twice and `npm run build` (no new warning); `cd services/accounting-service && pytest tests -q`; `npx -y @fission-ai/openspec validate redesign-frontend-v2 --strict`.
- [ ] **Step 2 (controller):** Playwright route sweep (Decision 8) on a preview build on 127.0.0.1:18081 — stop it before any SPA publish — at 390 / 820 / 1280 / 1440 × light / dark: every screen (literal paths + concrete parameterised/matcher instances, viewport set before `goto`, detail GETs answered with objects) with the catch-all stub + the accounting timeline with a `by_category` fixture (both layouts, legend/no-legend, scale and refund-only fixtures), no page errors, no horizontal scroll; screenshots to `~/reports/home-hub-redesign-v2-accounting-<TS>/`.
- [ ] **Step 3 (implementer):** tick 7.1, 8.1, 8.2, 8.3 in `tasks.md`; implementation notes in `frontend-accounting-ledger/spec.md` (Decisions 1–5, 7); commit `docs(openspec): accounting summary bar notes; tick tasks 7.1, 8.x`.

**Order:** 1 ∥ 2 (backend vs frontend helper) → 3 → 4 → 5. One worktree.

## Deploy

1. **Accounting API first** (no alembic): `sudo -u homehub-api -H git -C /home/homehub-api/workspace/home-hub fetch origin && … checkout --detach <sha>`, `sudo /usr/local/bin/homehub-api-ctl restart`, probe `/health` and `GET /entries/summary?month=<this month>` → `by_category` present (owner data: look at keys only, never quote amounts in reviews).
2. **SPA** release as PR 1–6 (sandboxed build → owner approve → publish → live probe).

## Self-review

- Spec coverage: `:5` requirement → Tasks 1–3; scenario `:16-19` (eight categories → five + 其他, sum = expense) → Task 2 fixture + Task 3 render test; `tasks.md` 7.1 and 8.1–8.3 → Tasks 3/5, 5, 5, 4.
- Type consistency: `CategoryExpense` (model) ↔ `CategoryExpenseOut` (schema) field names identical; `DistributionSegment.amount` is the raw number used for `flex-grow`, `label` the formatted string.
- Review Focus lines each have an owning test (1 → Task 2 refund-only + remainder cases; 2, 3 → Task 1; 4 → Task 3; 5 → Task 4).
- Proportion: plan ≈ spec × 3; code blocks limited to the Task 3 markup skeleton.
