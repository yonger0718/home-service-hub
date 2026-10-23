# Redesign follow-ups Batch 1 (#111 #112 #113 #114) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close four frontend-only follow-ups left by the redesign: Chinese labels on the import recalc card (#111), AA contrast for small ink text on tinted pills (#112), a distinct dark-mode primary hover that still clears 4.5:1 (#113), one fee term (匯費) across the dividend / cash surfaces plus a readable long-name layout on phone dividend rows (#114).

**Architecture:** Pure copy/label maps in `import-math.ts`; three new theme tokens (pill ink pair + dark hover fill) mirrored through the gain/loss convention blocks; selector-level colour switches; one flex tweak on the compact dividend row. No API change, no model change, SPA-only deploy.

**Tech Stack:** Angular 21 standalone/zoneless/signals + Vitest; `frontend/src/app/theme/contrast.spec.ts` (WCAG ratios from `styles.scss` per theme block).

**Spec:** GitHub issues #111–#114 (`gh issue view N`); contrast note `design_handoff_v2/README.md` "## Contrast".

**Rev 2 (AGENT-236, dev-grok, CHANGES-REQUIRED at cd8fc10):** Decision 5 rewritten (the floor goes on `.primary-name` with `flex: 1 1 100%` so the status tag wraps under the name; `.c-stock min-width` dropped; the Task 4 fixture is the page's own status tag 待收, not 美股/當沖); Decision 3 adds the five ≤ 13 px warning pills via a new `--tone-*-pill-ink` pair (accounting swap, not `[data-gainloss]`), pct-badge / `.pct-pill` move to `--app-trend-*-pill-ink`, `account-entries.scss:93` / `entry-row.scss:122-124` / `entry-form.scss:272-275` to `--c-*-pill-ink`, and the dark red pill ink is computed by the spec (the current dark `--c-red` fails on `--app-surface-soft` tints); Task 4's 手續費 grep now allows the four legitimate trade-fee hits; `cash-rows.ts:132-133` replace is deleted, not retargeted; Task 1 notes the stale fixture step names and the 失敗 label.

**Branch:** `fix/redesign-followups-batch1` from `origin/main` eca0f57 (worktree `~/workspace/home-hub-redesign`).

## Global Constraints

- No backend, API or model changes; no `.env`/config changes; SPA-only release.
- Every label map falls back to the raw value (unknown state/step names must still render) and keeps the raw value reachable (`title` attribute) for debugging.
- Large figures and generic `.neg` / `.pos` utilities keep the bright trend tokens (PR 7 fence); only the pill/badge selectors named in Task 2 move to the pill ink.
- `--tone-neg` / `--tone-pos` and the `[data-gainloss]` swap stay mirrored for every new trend token (default pair + `asian` + `western` blocks).
- Copy: the TW dividend handling fee is **匯費** everywhere on the dividend row, the 修改扣款 form, cash rows and account-detail type labels; a real trade fee stays 手續費 (`cash-rows.ts:50`, transaction/realized pages untouched).
- Specs restore globals; no `.only`; style budgets unchanged (`dashboard.scss` 62 B notice is pre-existing).

## Decisions

1. **#111 label maps (`import-math.ts`).** `RECALC_STATE_LABEL: Record<string,string> = { idle: '閒置', running: '執行中', completed: '完成', partial: '部分完成', failed: '失敗' }`; `RECALC_STEP_LABEL = { symbol_map_backfill: '代號對照補齊', dividend_auto_record: '歷史股利核對', networth_backfill: '淨值回補', awaiting_lock: '等待鎖定' }` (the orchestrator's real names, `post_import_orchestrator.py:284-295, :279/:334, :339`); `STEP_STATUS_LABEL = { ok: '完成', failed: '失敗', skipped: '略過', partial: '部分完成' }`; `labelOf(map, raw)` returns `map[raw] ?? raw`. Template (`import.html:217, :224, :240, :242`) renders the labels with `[title]="raw"`; the quote-refresh step rows gain the step label too (`{{ stepLabel(step.name) }}：{{ statusLabel(step.status) }}`). `recalcBanner` (`import-math.ts:189`) maps `current_step` through `RECALC_STEP_LABEL` before falling back to 未知錯誤.
2. **#113 dark hover.** New dark-only token `--app-primary-ink-bg-hover` (start `#5a4ad1`; Task 2 computes and adjusts until ≥ 4.5:1 with `#ffffff` and visibly darker than `#6c5ce7`); `btn.scss` hover → `var(--app-primary-ink-bg-hover, var(--app-primary-hover))`; light theme unchanged (token undefined there). `contrast.spec.ts` asserts the hover token's ratio and that it differs from the rest token.
3. **#112 pill ink.** New tokens `--c-red-pill-ink` / `--c-green-pill-ink` (light `#b5353a` / `#146b4b`: 4.94 / 4.53 on red-soft over surface / surface-soft, 5.56 / 5.06 green, 5.12 / 4.66 on the warning tint; **dark values are chosen by computation** — the current dark `--c-red` `#ff6369` is 4.14:1 on red-soft over `#202733` and 3.94:1 on the warning tint, so the red needs a lighter/more saturated value that clears 4.5 on both surfaces; dark green `#43c98a` passes 5.96 / 5.20) plus `--app-trend-positive-pill-ink` / `--app-trend-negative-pill-ink` in the default pair and both `[data-gainloss]` blocks, plus `--tone-neg-pill-ink` / `--tone-pos-pill-ink` in `accounting-layout.scss` next to the tone ink pair (swapped under `:host(.green-red)` like the others). Applied to: trend pills `pct-badge.scss:5,16` and `realized-pnl.scss:380-388 .pct-pill.up/.down` → `--app-trend-*-pill-ink` (`.pct-badge.neutral` is accent — untouched); absolute-colour accounting pills `account-entries.scss:93 .pill.rw` (also update its `#177a55` fallback), `entry-row.scss:122-124`, `entry-form.scss:272-275 .chip.fee` → `--c-*-pill-ink`; the five 0.72 rem warning pills coloured by `--tone-neg-ink` on `--app-state-warning-bg` → `--tone-neg-pill-ink`: `accounts/accounts.scss:83-86 .pill.warn.overdue`, `reminders/reminders.scss:181-184 .countdown.overdue`, `:363-366 .queue-item .due.overdue`, `:525-528 .badge.check`, `reminders/schedule-sheet/schedule-sheet.scss:74-77 .badge.check`. Left bright on purpose: `entry-form.scss:422-435` (`--fs-sm` 13.6 px), `settings.scss:165-187 .pill-preview` (13.1 px, `.pos/.neg` fence), `.rz-amount`, `account-entries.scss:97-98 .neg/.pos`. `contrast.spec.ts` gains an rgba-over-surface compositing helper and asserts, per theme, each pill ink ≥ 4.5 on its tint (`--c-red-soft`, `--c-green-soft`, `--app-state-warning-bg`) composited over `--app-surface` **and** over `--app-surface-soft`, and the three swap mechanisms (default, `asian`, `western`, `.green-red`) mirror the pill pair.
4. **#114 copy.** 手續費 → 匯費 at `dividend-rows.ts:72` (`deductionLine`), `dividend-list.ts:380` (preview), `dividend-list.html:250` (manual-add label); `cash-rows.ts:132-133` replace rule → the cash row shows `匯費 10` (no 扣 prefix); `account-detail.ts:81` `wire_fee` → 匯費. Specs updated accordingly (`dividend-rows.spec:43,45`, `dividend-list.spec:818`, `cash-rows.spec:115,130,135,145`, `account-detail.component.spec:994`).
5. **#114 phone names.** The 2-line clamp exists (`dividend-list.scss:370-376`). The squeeze: the name has `overflow: hidden` (flex minimum 0) and sits beside the **status tag** (`dividend-list.html:113`; `待收` / `已收款` / `待人工釐清` / `舊資料 · 未核實`, `dividend-math.ts:46-51`) which is `flex: none` on the same line, while `.c-net` takes up to 60 %. Change: `.compact .c-net { max-width: 45%; }` (returns width to the name) and `.compact .primary-name { flex: 1 1 100%; min-width: min(9ch, 100%); }` so the status tag wraps under the name and the floor can never force horizontal scroll (9ch ≈ 5 CJK per line at 0.875 rem; two clamped lines cover 元大台灣50正2). No `.c-stock` floor. Pending rows keep 確認收款 on its own line (`:387-389`). Verified at 360 px (Task 4): the tag's top is below the name's bottom, no horizontal scroll, `.net` unclipped.

## Review Focus

1. Unknown recalc state/step names (future orchestrator steps) still render, raw, with no exception (Task 1 test with `state: 'weird'`, `name: 'future_step'`).
2. The gain/loss convention swap: under `[data-gainloss="western"]` the pill ink pair swaps exactly like the bright pair (Task 2 spec reads all three blocks).
3. Dark hover token darker than rest and ≥ 4.5 with white; light theme button byte-identical (Task 2 spec).
4. Cash-row copy: a real trade `fee` leg still reads 手續費 (already pinned at `cash-rows.spec.ts:78` and `account-detail.component.spec.ts:312` — no duplicate test) while a dividend fee reads 匯費 (Task 3 tests).
5. Phone dividend row at 360 px with a long name and the 待收 tag: the tag wraps under the name, no horizontal scroll, `.net` unclipped (Task 4 measurement).

---

### Task 1: #111 — Chinese labels on the recalc card and banner

**Files:**
- Modify: `frontend/src/app/components/portfolio/import/import-math.ts` (maps + `labelOf`, `recalcBanner` mapping), `import.ts` (expose `stateLabel`/`stepLabel`/`statusLabel` or import the maps), `import.html:215-245`
- Test: `import-math.spec.ts`, `import.spec.ts` (assertions at `:143, :144, :156, :166, :167, :179, :206, :208, :209, :212, :1030, :1044`; `import-math.spec.ts:596, :605`)

**Interfaces:**
- Produces: `RECALC_STATE_LABEL`, `RECALC_STEP_LABEL`, `STEP_STATUS_LABEL`, `labelOf(map: Record<string,string>, raw: string | null | undefined): string` (raw when unknown, `''` when null/undefined → caller shows nothing).

- [ ] **Step 1: Failing tests** — `import-math.spec.ts`: `labelOf(RECALC_STATE_LABEL, 'running') === '執行中'`, unknown `'weird'` → `'weird'`, `null` → `''`; `recalcBanner({state:'failed', current_step:'networth_backfill'})` → `最近一次匯入的重算失敗：淨值回補`, unknown step → raw; keep `:596` expecting the step error (`TWSE timeout`) — the step error wins over `current_step`. `import.spec.ts`: rewrite the pinned assertions to the labels (`匯入重算：部分完成` / `執行中` / `失敗` / `閒置`, `報價更新（不含股利）：完成`, `代號對照補齊：完成`, `歷史股利核對：部分完成`, banner `…失敗：淨值回補`), **and rename the stale fixture step names to the real ones** (`recalc_holdings` → `symbol_map_backfill` at `:194`, `quotes`/`dividends`/`positions` at `:192`, `:1010`, `:1021`, `import-math.spec.ts:596-605` → `networth_backfill` / `dividend_auto_record` as the assertion needs); the maps never contain the stale names; a future step `future_step` with status `ok` renders `future_step：完成`; each label element carries `title` = raw value (check one).
- [ ] **Step 2: Run** `cd frontend && npx ng test --watch=false --include src/app/components/portfolio/import/import.spec.ts --include src/app/components/portfolio/import/import-math.spec.ts` → FAIL.
- [ ] **Step 3: Implement** per Decision 1 (template: `<span class="recalc-state" [title]="recalcStatus().state">匯入重算：{{ stateLabel(...) }}</span>` etc.; quote steps show `stepLabel(step.name)：statusLabel(step.status)`).
- [ ] **Step 4: Run** → PASS; `npx ng build --configuration production 2>&1 | grep -iE "error|budget"` unchanged.
- [ ] **Step 5: Commit** `fix(import): Chinese labels for recalc states, steps and statuses (#111)`.

### Task 2: #113 + #112 — theme tokens, pill ink, dark hover, contrast spec

**Files:**
- Modify: `frontend/src/styles.scss` (`:root` `:54-67`, `.app-dark-mode` `:185-190`, `[data-gainloss]` `:317-323`), `components/ui/btn/btn.scss:36-44`, `components/ui/pct-badge/pct-badge.scss`, `components/portfolio/realized-pnl/realized-pnl.scss:380-388`, `components/accounting/account-entries/account-entries.scss:93`, audited `entry-row.scss:122` / `entry-form.scss:273,423,433`
- Test: `frontend/src/app/theme/contrast.spec.ts` (+ `pct-badge.spec.ts`, `btn.spec.ts` stay green)

**Interfaces:** none downstream.

- [ ] **Step 1: Failing tests** (`contrast.spec.ts`): add `composite(rgba, hexBg)` (alpha-blend) + `rgba()` parser; per theme: `--c-red-pill-ink` on `--c-red-soft` composited over `--app-surface` and over `--app-surface-soft` ≥ 4.5; same for green; `--c-red-pill-ink` on `--app-state-warning-bg` ≥ 4.5 (both surfaces); the pill trend pair present in default + `asian` + `western` blocks and swapped under `western`; dark `--app-primary-ink-bg-hover` ≥ 4.5 with `#ffffff` and `!== --app-primary-ink-bg`; light has neither hover token; rewrite the btn hover regex test to `var(--app-primary-ink-bg-hover, var(--app-primary-hover))`.
- [ ] **Step 2: Run** `npx ng test --watch=false --include src/app/theme/contrast.spec.ts` → FAIL.
- [ ] **Step 3: Implement** Decisions 2–3; pick hex values by computation (the spec is the gate); audit the four accounting rules and switch only ≤ 13 px `color` on a tinted background; write the before/after table into the task report.
- [ ] **Step 4: Run** contrast + pct-badge + btn + touched accounting specs → PASS; build budget unchanged.
- [ ] **Step 5: Commit** `style(theme): pill ink tokens for tinted small text; distinct dark primary hover (#112, #113)`.

### Task 3: #114 — one fee term, phone name width

**Files:**
- Modify: `components/portfolio/dividend-list/dividend-rows.ts:72`, `dividend-list.ts:380`, `dividend-list.html:250`, `dividend-list.scss:355-390`, `components/portfolio/accounts/cash-rows.ts:132-133`, `components/portfolio/accounts/account-detail.ts:81`
- Test: `dividend-rows.spec.ts:43,45`, `dividend-list.spec.ts:818`, `cash-rows.spec.ts:115,130,135,145,204`, `components/portfolio/accounts/account-detail.component.spec.ts:994`

- [ ] **Step 1: Failing tests** — update the pinned strings to 匯費 (`匯費 10 · 補充保費 202`, cash row `匯費 10`), add: a `fee`-type cash leg still titles 手續費 (cash-rows), `wire_fee` type label 匯費 (account-detail); `dividend-list.spec`: the manual-add form label reads 匯費.
- [ ] **Step 2: Run** the four specs → FAIL.
- [ ] **Step 3: Implement** Decisions 4–5 (scss: `.compact .c-net { max-width: 45% }`, `.compact .primary-name { flex: 1 1 100%; min-width: min(9ch, 100%) }`; `cash-rows.ts:132-133`: **delete** the `手續費 → 扣匯費` replace rather than retargeting it).
- [ ] **Step 4: Run** → PASS; build budget unchanged.
- [ ] **Step 5: Commit** `fix(portfolio): 匯費 as the one dividend fee term; wider phone name column (#114)`.

### Task 4: Verification

- [ ] **Step 1 (implementer):** `npx ng test --watch=false` (full, once) + `npm run build`; grep for leftover `手續費` in the dividend/cash surfaces (`grep -rn 手續費 frontend/src/app/components/portfolio/dividend-list frontend/src/app/components/portfolio/accounts`) → exactly the trade-fee hits: `cash-rows.ts:50`, `account-detail.ts:76`, `cash-rows.spec.ts:78`, `account-detail.component.spec.ts:312` (plus comments); nothing else.
- [ ] **Step 2 (controller):** Playwright on a preview build (127.0.0.1:18081, stop before any SPA publish): dividends page at 360 / 390 light with a long-name pending row (元大台灣50正2, status 待收) → the tag's box top ≥ the name's box bottom, no h-scroll, `.net` unclipped; import page step-3 card with a running status (labels visible); realized-pnl pct pills, an overdue reminder pill and a dark-mode primary button hover (`page.hover`) screenshots; report dir `~/reports/home-hub-followups-batch1-<TS>/`.
- [ ] **Step 3:** update the four issues with a comment pointing at the PR (controller).

**Order:** 1 ∥ 2 ∥ 3 (disjoint files; Task 2 and 3 both touch no shared file — `styles.scss` is Task 2 only) → 4.

## Deploy

SPA only: sandboxed build → prepare over the installed `main-5031b08-a84e6cae5490` publisher → owner approve → publish → live probe.

## Self-review

- Spec coverage: #111 → Task 1; #112/#113 → Task 2; #114 (copy + truncation) → Task 3; verification → Task 4.
- Type consistency: `labelOf` signature used by both `import.ts` and `recalcBanner`; token names identical across `styles.scss`, `btn.scss`, pill scss and the spec.
- Review Focus 1–5 each have an owning test/measurement.
- Proportion: plan ≈ 2× the four issues; one code snippet only (the hover `var()` chain).
