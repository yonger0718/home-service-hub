# Redesign v2 — PR 6 (匯入) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One import page at `/portfolio/import` with a 匯入類型 switch (交易 / 股利 / 券商對帳單) over the three existing endpoints, a three-step flow (選擇檔案 → 預覽與確認 → 匯入並重算), the canvas' cards (檔案 / 預覽 / 需要修正的列 / 確認股票代號), a last-failure banner + device-local recent-imports list that also drive the 匯入 navigation dot, and `/portfolio/import-broker` redirecting to the unified page.

**Architecture:** Keep `PortfolioImportComponent` as the page and fold the broker path into it (the separate `broker-import` component goes away). Pure helpers in `import/import-math.ts` (step, counts, row results, labels) and a small root `ImportHistoryService` (`localStorage` `hh-import-history`, signal-based) shared by the page and `NavBadgeService`. Templates follow PR 4/5 patterns (`app-bento` cards, `app-seg-toggle`, `app-file-chip`, `app-tag`, `app-side-tag`, `app-btn`); the existing recalc-status panel moves into step 3 unchanged in behaviour. Frontend only; no API change.

**Tech Stack:** Angular 21 standalone / signals / OnPush, Vitest (`cd frontend && npx ng test --watch=false --include <spec>`), existing `PortfolioService.uploadCsv(kind, file, { dryRun, hasHeader, nameOverrides, confirmedOverrides })`, `uploadBrokerCsv(file, dryRun)`, `getRecalcStatus()`, `triggerRecalc()`, `verifySymbol` (`services/portfolio.service.ts:160-230`).

**Spec:** `openspec/changes/redesign-frontend-v2/specs/frontend-csv-import/spec.md`, `specs/frontend-broker-import/spec.md`, `design.md` D4 (匯入 dot row) + D9 (`:86`), `design_handoff_v2/design_refs/PfImport.dc.html` (copy + sample data), tasks 6.1–6.3.

## Decisions this plan makes

1. **One component, three types.** `ImportType = 'transactions' | 'dividends' | 'broker'` (`kind` signal becomes `type`); 交易 / 股利 keep `uploadCsv(kind, …)`; 券商對帳單 uses `uploadBrokerCsv(file, dryRun)` and renders `BrokerCsvImportResult` (`models/portfolio.model.ts:136-150`: `detected_broker`, `transactions`, `cash_flows`, `counts {created, skipped, rejected}`, `errors [{row_index, reason}]`). `components/portfolio/broker-import/*` is deleted; its spec intents move into `import.spec.ts` (dry-run renders rows + cash flows without a created count; commit shows counts and clears the file; row errors keep the file; unknown header → `manual`/none with the generic-CSV warning). Route `portfolio/import-broker` → `{ redirectTo: '/portfolio/import?type=broker', pathMatch: 'full' }`; the page reads `?type=` on init (`broker` | `dividends` | `transactions`, default transactions) and keeps the query param in sync when the toggle changes (`router.navigate([], { queryParams: { type }, replaceUrl: true })`).
2. **Steps.** `step = computed(() => committing() || committed() ? 3 : result() ? 2 : 1)`: a `.steps` strip 選擇檔案 → 預覽與確認 → 匯入並重算 with the current step highlighted and finished steps ticked; step 3 subtitle `寫入後自動重算`. After a successful commit the page stays on step 3 showing the recalc panel until 取消 (reset) or a new file.
3. **檔案 card** (`app-bento` title 檔案): 匯入類型 `app-seg-toggle` (`ariaLabel="匯入類型"`, 交易 / 股利 / 券商對帳單; changing it clears result/overrides and re-previews when a file is present, as today's `onImportOptionsChange`), the 首列為欄位名稱 checkbox (hidden for 券商對帳單 — the broker parser detects its own header), hint line per type (交易/股利: `通用欄位格式（日期、名稱、買賣、股數、價格）` + `支援自動偵測：國泰證券`; 券商對帳單: `券商對帳單（交易＋現金流）`), the dropzone (unchanged behaviour) which on selection becomes `app-file-chip` + a meta line `{rows} 列 · 偵測格式：{format}` and two actions 更換檔案 (re-opens the picker) / 移除檔案 (= 取消). Detected format label: 交易/股利 from `detectedFormatLabel()` as today (`國泰證券` / `通用 CSV`), 券商對帳單 from `detected_broker` (`FIRSTRADE`, `IB`, … or `manual` with the warning `將以通用手動 CSV 解析`). A broker-format selector is never rendered.
4. **預覽 card** (shown when `result()`): count tags `解析 {parsed}` · `重複 {dup}` · `錯誤 {errors}` (交易/股利: `parsed`, `would_skip_duplicate ?? skipped_duplicates`, `errors.length`; 券商: `transactions.length + cash_flows.length`, `counts.skipped`, `errors.length`), then the grid 日期 / 股票 / 買賣 / 股數 / 價格 / 結果 (股利 swaps 買賣·股數·價格 for 股利金額 · 入帳日 as today) with the row result from `rowResult(row, result)`: `新增` (`app-tag` neutral), `重複 · 略過` (row dimmed `.dimmed`, tag neutral), `代號待確認` (tag warning) when the row's name is in `unresolved_names`, `錯誤` (tag danger) when `errors` has its `row_index`. Duplicate detection per row: the API does not flag rows individually → **rows are not individually marked 重複**; the tag count shows the duplicate total and the grid shows 新增 for parsed rows (spec note). 券商對帳單 adds a second table 現金流 (日期 / 類型 / 金額 / 幣別 from `cash_flows` via the existing `field()` lookups).
5. **需要修正的列 card** (when errors): one line per error `第 {row_index} 列 · {message|reason} · 此列會略過`.
6. **確認股票代號 card** (when `unresolved_names`): one block per name — title `{name} 的股票代號`, meta `{occurrences} 次 · {sample_dates joined by 、}`, code input + 驗證代號 button (`verifySymbol`, existing status icons/texts), the 確認使用此代號 checkbox; behaviour unchanged from today's override rows (`setOverride`, `verifyName`, `toggleConfirm`, `pendingOverrideCount`).
7. **Footer:** 取消 (ghost; clears file, result, overrides, history banner state untouched), 重新預覽 (secondary; disabled without a file or while busy), 確認匯入 {N} 筆 (primary; `N = confirmCount(result)` = 交易/股利: `would_insert ?? parsed − dup − errors.length` (never below 0); 券商: `transactions.length + cash_flows.length − errors.length`; disabled while busy, without a result, or with pending unverified overrides as today's `commit()` guard; the existing confirm dialog stays).
8. **Device-local history.** `ImportHistoryService` (root, `services/import-history.service.ts`): `attempts = signal<ImportAttempt[]>` loaded from `localStorage['hh-import-history']` (JSON array, newest first, max 10; `{ at: ISO, file, type, ok, created?, errors?, reason? }`), `record(attempt)`, `lastFailed = computed(() => attempts()[0]?.ok === false)`, `clear()`; `storage` event listener for other tabs; every read/write in try/catch (private mode). The page records an attempt on every **commit** outcome (success: `ok: true, created`; HTTP error or `errors.length > 0` with nothing created: `ok: false, errors, reason`); dry-runs are not recorded.
9. **Banner.** At the top of the page when `recalcStatus().kind !== 'quotes' && state === 'failed'` (existing) **or** `history.lastFailed()`: `app-bento` variant danger-ish (`.import-banner`, `--app-danger-soft`/`--app-danger` tokens if present, else `--c-red`-based tokens already used by the nav dot) with `最近一次匯入失敗`, line `{MM/DD HH:mm} · {file} · {reason or '{n} 列錯誤'}`, and `沒有寫入任何資料。`; it disappears when the next commit succeeds (history head becomes ok) and the recalc status is no longer failed.
10. **Nav dot.** `NavBadgeService` adds `inject(ImportHistoryService).lastFailed()` to the 匯入 dot condition (`services/nav-badge.service.ts:65-68`): dot when the recalc chain failed **or** the device's last attempt failed; label stays `最近一次匯入失敗`.
11. **Side column** (`panes`): `app-bento` 匯入後會發生什麼 with three rows — 重算持股與損益 / `依新交易重建成本、已實現損益與當沖標記`; 抓取除權息事件 / `新股票的除權息會記為待收股利，需到股利頁確認收款`; 更新報價 / `回補收盤價，不含股利` — and `app-bento` 最近匯入 listing `attempts()` newest first: `{MM/DD HH:mm} · {成功|失敗} · {新增 n 筆 | n 列錯誤 | reason}` with a green/red dot (`--app-trend-positive`/`--c-red` tokens as the nav uses), file name beneath, `尚無匯入紀錄` when empty. Below `panes` (sheet + phone) the same two cards render under the footer.
12. **Step 3 / recalc panel.** The existing recalc status block (steps, deferred dividends, quote refresh, the 重新重算 button and polling) moves into the 匯入並重算 section and keeps every current behaviour and test intent (`import.spec.ts:39-91`: no duplicate recalc submissions, deferred historical amounts, quote-only run not labelled as a completed import, running quote status keeps a partial import). It is visible when `step() === 3` or when a recalc is running/failed on load (so the failure banner has its detail nearby).
13. **Header:** `app-page-header` keeps the nav title (匯入 CSV) with no actions; phone layout stacks the cards; the steps strip wraps.
14. **Copy (verbatim):** 匯入步驟; 選擇檔案 / 預覽與確認 / 匯入並重算 / 寫入後自動重算; 匯入類型, 交易, 股利, 券商對帳單; 首列為欄位名稱; `{n} 列 · 偵測格式：{format}`; 更換檔案, 移除檔案; 預覽; 解析 {n} / 重複 {n} / 錯誤 {n}; 新增 / 重複 · 略過 / 代號待確認 / 錯誤; 需要修正的列; `第 {n} 列 · {msg} · 此列會略過`; 確認股票代號; `{name} 的股票代號`; 驗證代號; 確認使用此代號; 取消 / 重新預覽 / 確認匯入 {n} 筆; 最近一次匯入失敗; 沒有寫入任何資料。; 匯入後會發生什麼 + the three pairs in Decision 11; 最近匯入; 成功 / 失敗; 新增 {n} 筆; {n} 列錯誤; 尚無匯入紀錄; 通用 CSV / 國泰證券 / `將以通用手動 CSV 解析`.
15. **Spec notes (Task 6):** rows are not individually flagged 重複 (API has no per-row duplicate flag); the history is device-local (`localStorage`, 10 entries) and the dot clears on the next successful commit on that device; dry-runs are not recorded; the broker type hides the header checkbox.

## Global Constraints

- Frontend only; no API or model change (type-only additions allowed).
- Tokens only; copy verbatim (Decision 14); `app-*` kit over PrimeNG where one exists (keep `p-confirmDialog`, `p-toast`, `p-checkbox` if used today).
- Breakpoints via `LayoutModeService` (`phone` / `sheet` / `panes`); tests use `set(...)` + `set(null)` in `afterEach`; every spec restores `localStorage` (`hh-import-history`) and timers.
- Existing behaviours kept: upload payloads (`dry_run`, `has_header`, overrides), confirm dialog before commit, recalc polling + `重新重算` guard, deferred-dividend rendering, symbol verification flow.
- Style budgets 10 kB; no new build warning beyond the known notices.

## Review Focus

1. **Type switch with a file loaded** — switching 交易 → 券商對帳單 must re-preview through the broker endpoint and drop transaction overrides; back again must not keep broker state. → T2 tests.
2. **Commit error classification** — an HTTP 4xx/5xx and a 200 with `created 0` + errors must both record `ok: false` with a reason and show the banner; a 200 with `created > 0` and some errors is a success (banner clears, counts shown). → T4 tests.
3. **Storage failures** — `localStorage` throwing (private mode) must not break the page or the nav badge. → T1 tests.
4. **Confirm count never negative / never counts errors** — `would_insert` preferred; fallback math clamped. → T1 tests.
5. **Redirect keeps deep links** — `/portfolio/import-broker` lands on 券商對帳單 and `?type=dividends` on 股利; an unknown type falls back to 交易. → T2 tests.

---

### Task 1: Import math + history service

**Files:**
- Create: `frontend/src/app/components/portfolio/import/import-math.ts` (+ spec), `frontend/src/app/services/import-history.service.ts` (+ spec)

**Interfaces:**
```ts
export type ImportType = 'transactions' | 'dividends' | 'broker';
export type RowResult = 'new' | 'duplicate' | 'unresolved' | 'error';
export const ROW_RESULT_LABEL: Record<RowResult, string>; // 新增 / 重複 · 略過 / 代號待確認 / 錯誤
export function importCounts(type: ImportType, r: ImportResult | BrokerCsvImportResult | null): { parsed: number; duplicates: number; errors: number };
export function confirmCount(type: ImportType, r: ImportResult | BrokerCsvImportResult | null): number;           // Decision 7, ≥ 0
export function rowResult(row: ImportRow, r: ImportResult): RowResult;                                           // Decision 4
export function detectedFormat(type: ImportType, r: ImportResult | BrokerCsvImportResult | null, fallback: string | null): { label: string; warning: string | null }; // 國泰證券 / 通用 CSV / FIRSTRADE … / manual → warning
export function stepFor(hasResult: boolean, committing: boolean, committed: boolean): 1 | 2 | 3;
export function typeFromQuery(q: string | null): ImportType;                                                      // 'broker' | 'dividends' | else 'transactions'
export interface ImportAttempt { at: string; file: string; type: ImportType; ok: boolean; created?: number; errors?: number; reason?: string | null }
export function attemptLine(a: ImportAttempt): string;  // '10/21 22:10 · 失敗 · 1 列錯誤' | '10/01 21:42 · 成功 · 新增 18 筆' | '… · 失敗 · {reason}'
export function classifyCommit(res: ImportResult | BrokerCsvImportResult | null, httpError: unknown | null, file: string, type: ImportType, now: Date): ImportAttempt; // Review Focus 2
@Injectable({ providedIn: 'root' }) export class ImportHistoryService { readonly attempts: Signal<ImportAttempt[]>; readonly lastFailed: Signal<boolean>; record(a: ImportAttempt): void; clear(): void } // key 'hh-import-history', max 10, try/catch, storage event
```

- [ ] **Step 1: Failing tests** — `import-math.spec`: counts for 交易 (`parsed 41`, `would_skip_duplicate 3`, 1 error → `{41, 3, 1}`) and 券商 (`transactions 5 + cash_flows 2`, `skipped 1`, 1 error → `{7, 1, 1}`); `confirmCount` prefers `would_insert` (38), falls back to `41 − 3 − 1 = 37`, clamps at 0, 券商 `7 − 1 = 6`; `rowResult`: error row → `error`, unresolved name → `unresolved`, else `new`; `detectedFormat` three branches + `manual` warning + null result → fallback; `stepFor` 1/2/3; `typeFromQuery` four cases; `attemptLine` the two canvas strings; `classifyCommit`: HTTP error → `ok false` + reason from `error.message`/`detail`/status, 200 with `created 0` + errors → `ok false`, `errors n`, 200 with `created 18` → `ok true, created 18` even with errors, broker `counts.created`. `import-history.service.spec`: records newest first, caps at 10, `lastFailed` follows the head, survives a throwing `localStorage` (stub `getItem` to throw → empty list, `record` no-throw), `storage` event reloads.
- [ ] **Step 2–4:** run → FAIL; implement; run → PASS.
- [ ] **Step 5: Commit** `feat(import): import math, row results and device-local import history`

---

### Task 2: Unified page — type switch, steps, 檔案 card, redirect

**Files:** `components/portfolio/import/import.{ts,html,scss}`, `app.routes.ts:87-88` (broker route → redirect), `import.spec.ts` (+ query/redirect tests).

**Interfaces (consumes Task 1):** `ImportType`, `typeFromQuery`, `stepFor`, `detectedFormat`, `importCounts`. Component: `type = signal<ImportType>` (replaces `kind`), `brokerResult = signal<BrokerCsvImportResult | null>`, `result` stays for 交易/股利; `upload(dryRun)` branches on type; `onTypeChange(type)` resets result/overrides, syncs `?type=`, re-previews when a file exists; `replaceFile()` opens the native input.

- [ ] **Step 1: Failing tests** — `?type=broker` → 券商對帳單 selected, header checkbox hidden, hint `券商對帳單（交易＋現金流）`; `?type=dividends` → 股利; `?type=x` → 交易; `/portfolio/import-broker` resolves to `/portfolio/import?type=broker` (router test on the redirect config); steps strip shows 1 before a file, 2 after a dry-run result, 3 while committing; selecting a file with type 交易 posts `uploadCsv('transactions', …, dry_run=true, has_header=true)` and shows the chip + `{n} 列 · 偵測格式：國泰證券` (from the response, no selector rendered); with type 券商對帳單 posts `uploadBrokerCsv(file, true)` and shows `偵測格式：FIRSTRADE`; unknown header → `manual` + the warning; switching type with a file re-previews on the new endpoint and clears overrides; 更換檔案 opens the input; 移除檔案 clears everything; existing intents from `import.spec.ts:17-38` (dividend columns + fresh overrides; override change invalidates verification) kept.
- [ ] **Step 2–4:** run → FAIL; implement; run `import.spec.ts` + `app.routes.spec.ts` → PASS.
- [ ] **Step 5: Commit** `feat(import): unified type switch with broker statements, steps strip and file card`

---

### Task 3: 預覽 / 需要修正的列 / 確認股票代號 cards + footer

**Files:** `import.{ts,html,scss}`, `import.spec.ts`. **Consumes:** `rowResult`, `ROW_RESULT_LABEL`, `confirmCount`.

- [ ] **Step 1: Failing tests** — 預覽 card with tags `解析 41` / `重複 3` / `錯誤 1`, grid heads 日期 / 股票 / 買賣 / 股數 / 價格 / 結果 (股利: 股利金額 / 入帳日), a row with `app-side-tag` 買進, result tags 新增 / 代號待確認 / 錯誤, error rows `.dimmed`? (no — only duplicates would be dimmed; since rows aren't individually flagged, assert no `.dimmed` and note); 券商: transactions table + 現金流 table, no created count on dry-run; 需要修正的列 lines `第 3 列 · missing FX rate for 2026-06-02 USD · 此列會略過`; 確認股票代號 block title `台灣50正2 的股票代號`, meta `{n} 次 · {dates}`, 驗證代號 + checkbox (existing behaviour tests kept); footer `確認匯入 38 筆` (with `would_insert 38`), `確認匯入 37 筆` fallback, disabled with pending overrides; 取消 resets chip/format/preview/errors/blocks (spec scenario); commit success for 券商 shows `created / skipped / rejected` counts and clears the file; row errors keep the file.
- [ ] **Step 2–4:** run → FAIL; implement (drop the old `.summary`/`.badge` markup and the `broker-import` tables; one table component-less markup with scss copied from the dividends grid where useful); run → PASS; budget check.
- [ ] **Step 5: Commit** `feat(import): preview grid with row results, error list, symbol confirmation and footer`

---

### Task 4: Banner, recent imports, side column, nav dot

**Files:** `import.{ts,html,scss}`, `services/nav-badge.service.ts:60-70` (+ `nav-badge.service.spec.ts`), `import.spec.ts`. **Consumes:** `ImportHistoryService`, `classifyCommit`, `attemptLine`.

- [ ] **Step 1: Failing tests** — commit HTTP 500 → `record({ok:false, reason})`, banner `最近一次匯入失敗` with `MM/DD HH:mm · file · reason` and `沒有寫入任何資料。`; commit 200 `created 0` + 1 error → banner `1 列錯誤`; next successful commit → banner gone, history head ok; recalc status `failed` alone → banner (existing dot condition) with the recalc reason; side column in `panes`: 匯入後會發生什麼 three rows verbatim, 最近匯入 lists three attempts newest first with dots (`.ok`/`.failed`), `尚無匯入紀錄` when empty; `sheet`/`phone` → the two cards under the footer; `NavBadgeService`: dot when `lastFailed()` even with a healthy recalc status, cleared when the head attempt is ok and recalc not failed (reuse the service's existing spec harness).
- [ ] **Step 2–4:** run → FAIL; implement; run `import.spec.ts` + `nav-badge.service.spec.ts` + `shell.spec.ts` → PASS.
- [ ] **Step 5: Commit** `feat(import): last-failure banner, device-local recent imports and 匯入 nav dot`

---

### Task 5: Step 3 recalc panel, broker-import removal, spec merge

**Files:** `import.{ts,html,scss}`, `import.spec.ts`; delete `components/portfolio/broker-import/*` (after moving its spec intents into `import.spec.ts` in Tasks 2–3 — verify each of the four scenarios of `frontend-broker-import/spec.md` has a test); `app.routes.spec.ts` if it names the broker component.

- [ ] **Step 1: Failing tests** — the recalc panel renders inside the 匯入並重算 section after a commit (steps, deferred dividends, quote status, 重新重算 guard — the four existing tests `import.spec.ts:39-91` pass against the new markup); on load with a running/failed recalc the panel is shown without a file; `PortfolioBrokerImportComponent` no longer exists (grep) and `/portfolio/import-broker` is a redirect only.
- [ ] **Step 2–4:** run → FAIL; implement; run the import specs + `app.routes.spec.ts` + the full suite once → PASS; `npm run build` budget check.
- [ ] **Step 5: Commit** `feat(import): recalc panel as step 3; remove the separate broker-import page`

---

### Task 6: Verification and bookkeeping

- [ ] **Step 1:** full suite twice + `npm run build` (no new warning); `npx -y @fission-ai/openspec validate redesign-frontend-v2 --strict`.
- [ ] **Step 2 (controller):** Playwright on a preview build (stop it before any SPA publish) with stubbed `/imports/transactions?dry_run=true` (41 parsed, 3 dup, 1 error, one unresolved name), `/imports/csv?dry_run=true` (FIRSTRADE, 5 tx + 2 cash flows), `/imports/recalc/status`, `/imports/verify-symbol`: 390 / 1440 light / dark, each type with a file selected, the banner (seeded `localStorage`), the side column.
- [ ] **Step 3:** tick 6.1–6.3 in `tasks.md`; spec notes per Decision 15; commit `docs(openspec): import implementation notes; tick tasks 6.x`.

**Order:** 1 → 2 → 3 → 4 → 5 (same files, sequential); 6 last. One worktree.

## Deploy
SPA only (sandboxed build → owner approve → publish → live probe). No API change.
