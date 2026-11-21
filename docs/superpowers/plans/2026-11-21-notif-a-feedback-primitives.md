# Notification center PR A: one toast, one banner — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two toast systems (PrimeNG `<p-toast>` / `MessageService` on portfolio and inventory pages, `AccountingToastService` on accounting pages) with one shell-level `ToastService`, and the hand-rolled page alerts with one `<app-banner>` primitive. No new features: every message that exists today keeps its trigger; only its look, place and lifetime change.

**Architecture:** SPA only (`frontend/`), no API change. `ToastService` (root) holds at most one toast; `ToastOutletComponent` is rendered once by the shell, inside `.hub-layout`, and draws it at the bottom-left of the content area (≥ 760 px) or above the tab bar (< 760 px). `BannerComponent` (`components/ui/banner`) renders an 錯誤 / 注意 / 資訊 alert with a heading, one line of detail and one action; only 資訊 with a `dismissId` can be closed, and the close is remembered in `localStorage['hh-banner-dismissed']`. Call sites move straight to the new APIs; no compatibility shim is left behind.

**Tech Stack:** Angular 21 standalone / signals / OnPush, vitest (`cd frontend && npx ng test --watch=false --include='<glob>'`, quote the glob), SCSS with the tokens of `frontend/src/styles.scss`.

**Spec:** `openspec/changes/add-notification-center/` — design D6 (toast) and D7 (banner), Migration Plan step 1, tasks §1, `specs/frontend-ui-primitives/spec.md` (Requirements "Toast service" and "Banner primitive"). Artboard `design_handoff_v3/design_refs/FeedbackSpec.dc.html` (sections "Toast · 全站一種" and "頁內橫幅"), colour rules in `design_handoff_v3/README.md`. `openspec/specs/` is not edited in this PR (the main specs change when the whole change is archived).

## Global Constraints

- Colours only through tokens declared in `frontend/src/styles.scss`; no hex, `rgb()` or `rgba()` literal in a component stylesheet or inline style. New tokens are added to `styles.scss` in both theme blocks (`:root` and `.app-dark-mode`).
- No new dependency. PrimeNG stays (dialogs, menus, confirm dialogs); only `primeng/toast` and `MessageService` leave.
- Chinese UI copy exactly as this plan's copy tables give it (taken from the code today, the spec or the artboard; where they differ the ruling says which).
- Tests use fictional names, files and amounts (`範例券商_202610.csv`, `測試銀行`, `$1,234`).
- The bell, the 提醒中心 count, `NavBadgeService` and the navigation badges / dots are not touched.
- `openspec/specs/**` is not edited. `openspec/changes/add-notification-center/tasks.md` ticks 1.1–1.3 in Task 7.
- Commit per task, message in English, ending with the trailer `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`. No `--amend`, no force-push.
- Never read `~/.config`, `~/.hermes` or any `.env`; never run uvicorn or alembic.

## Rulings made while planning (owner may overrule)

Each ruling names what it costs if it is wrong.

1. **Variant per message.** success → 完成; error → 失敗; a refusal or validation hint (PrimeNG `warn`, `import_running`, `info` refusals such as 需人工釐清…) → 失敗, because the action did not happen; "something is running" (`info` 重新觸發重算 / 資料重算執行中…) → 進行中. A replay that changed nothing (這次與上次相同，沒有重複執行) → 完成. *If wrong:* a few strings change variant in the copy tables.
2. **One text line.** A toast carries one `text`. PrimeNG's generic summaries (錯誤, 成功, 提醒, 失敗, and 已新增 / 已更新 where the detail already says it) are dropped; a meaningful summary is joined to its detail with a full-width colon through `toastText(summary, detail)` (`收款確認未完成：<原因>`; just the summary when the detail is empty). *If wrong:* change one helper and the strings in the tables.
3. **`title` input is named `heading`.** D7 writes `<app-banner title=…>`; a static `title` attribute on a component host stays in the DOM and shows a native tooltip, so the input is `heading`. *If wrong:* a rename.
4. **Toast timing.** 完成 lives 5 s; the timer pauses while the pointer is over the toast or focus is inside it and resumes with the remaining time. 進行中 and 失敗 have no timer. *If wrong:* one timer rule.
5. **Route changes.** A navigation to another path (query-only changes ignored) removes a 進行中 toast (its page stops polling); 完成 and 失敗 stay. Today's accounting toast was cleared when leaving the accounting layout and PrimeNG toasts died with their page; now a 失敗 raised just before the page navigates (e.g. CATCH_UP_FAILED_TOAST, then the form closes) is still seen. *If wrong:* one `filter` in the service.
6. **Stacking and focus.** The outlet sits at `z-index: 1150`: above the tab bar / FAB / sidebar (1000), the 760–1023 px sheet (backdrop 1001, pane 1002), accounting bottom sheets and popovers (1100) and PrimeNG modal masks (1100 + n), below pickers and menus that open on top (1200). The toast never takes focus, never joins a focus trap, and is not made `inert` by the sheet (it is outside the list / header / sidebar the sheet inerts). Inside an open modal it is reachable by pointer; by keyboard after the modal closes (失敗 stays until closed). It may cover the lower-left of a sheet; a 失敗 has ✕. When the toast closes while focus is inside it, focus returns to the last element focused outside the outlet (if still connected). *If wrong:* keyboard users who need 重試 while a sheet is open must close the sheet first; extending `trapFocus` to include the outlet is a later one-helper change.
7. **重試 only where the page can re-issue the same call with no form state:** item list load, portfolio accounts list load, account detail account / balance-history loads, recalc trigger, recalc status read, partial recalc without deferred events (the orchestration spec's 重試 that calls `POST /imports/recalc`). Failed saves keep their dialog or form open with its own submit button and get no 重試. *If wrong:* the spec scenario "save fails → 失敗 offers 重試" is only shown by the primitive's tests; wiring 重試 at the ~10 save sites is a follow-up.
8. **復原 is not wired to any call site in PR A.** No toast today follows an action with an inverse endpoint (the inbox answers confirm inline; the 已處理 list keeps its 撤銷 button; 記帳設定 › 發票 keeps its row 復原). The API (`done(text, { undo })`) and its tests ship now; the reconciliation answer → 復原 scenario lands with the review flow (PR D). *If wrong:* one `done(..., { undo })` call in `inbox-panel.ts`.
9. **The error-logging interceptor stops toasting.** It injects the root `MessageService`, which no `<p-toast>` renders (every `<p-toast>` sits in a component that provides its own `MessageService`), so its toast has never been visible. Showing it now would add a generic 失敗 to every failed request and, with one toast at a time, replace the page's own message. It keeps its retry and console logging. *If wrong:* the owner gets no generic network toast (same as today).
10. **Dashboard 409 on refresh.** `notify()` also used the unrendered root `MessageService`. The manual 刷新行情 (`loadSummary`) now shows 失敗 「另一筆重算進行中，稍候再試」 (full-width comma, the house punctuation); the automatic refresh on page load stays silent (`console.warn`). *If wrong:* one call site.
11. **Import commit with a recalc.** One toast, not two: a clean commit with `recalc_scheduled` shows one 進行中 「匯入完成：<counts>；資料重算執行中…」 (keeps csv-import's "success toast displays the count" and the orchestration spec's "toast immediately after commit"); a commit with row errors / unresolved names shows its 失敗 with the counts and no 進行中 (the page's recalc panel shows 執行中). The recalc result (完成 / 失敗) replaces whatever is shown. A settled status that is not a transition dismisses the page's 進行中 toast so it is never left behind. *If wrong:* the counts move to a separate toast that is replaced within one poll.
12. **Toast colours.** Inverse surface `--app-accent` with `--app-surface` text (the artboard's `toastBg` / `toastFg`; the FAB tip already uses this pair). Three new tokens for inks on that inverse surface: `--app-toast-action` (light `#b3a8ff`, dark `#533afd`), `--app-toast-ok` (light `#5ad19c`, dark `#177a55` — the artboard's `#1f9d6b` is 2.9:1 on `#e6ebf5`, below 3:1 for an icon), `--app-toast-error` (light `#ff8a8e`, dark `#c9373c`). Shadow `--app-raised-shadow`. *If wrong:* three token values.
13. **Banner tones → tokens.**
    - 錯誤: background `--c-red-soft`, icon and heading `--c-red-pill-ink`. The README maps failure text to `--c-red-ink`, but on its tint over `--app-bg` that is 4.0:1 in light; the pill ink (the repo's ink-on-tint rule, #112) is 4.6:1 light / 6.3:1 dark.
    - 注意: `--app-banner-warn-soft` / `--app-banner-warn` (4.97:1 light, 6.4:1 dark).
    - 資訊: `--app-primary-soft`, icon `--app-primary`, heading `--app-text`, as the artboard draws (`--app-warning` is blue-grey and is not used).
    - Detail text `--app-text`; action button `--app-surface` on `--app-border`, text `--app-text`; ✕ `--app-text-muted`.
    *If wrong:* token swaps in `banner.scss`.
14. **Banner roles.** 錯誤 `role="alert"`, 注意 and 資訊 `role="status"` (artboard).
15. **Which page alerts migrate.** The four named in the spec where they exist, plus two more that already are page-level, condition-driven alerts with one action:
    - Named: import failure (a) and recalc failure (b) on `/portfolio/import`, 帳單讀取失敗 in 提醒中心, a failing schedule row in 提醒中心 › 借還款追蹤.
    - Extra: the expired reward rules banner (`.rr-banner`, already specified as a warning banner) and the portfolio overdraft banner (`.overdraft-banner`, which today paints with `--app-trend-negative`, a gain/loss token that flips with 紅漲綠跌).
    - Not migrated: the lock note (`app-lock-banner`, a record-state note inside a form, never dismissible); the timeline's 「近 7 天有 N 筆繳費到期」 button (navigation, replaced by 通知 in PR B); 部分外幣未換算 and every section-level 「…讀取失敗」 line (inline, scoped to one section); the entry form's in-form 「已儲存，繼續記下一筆」 flash and the rules section's screen-reader `liveText` (inline feedback, not toasts).
    *If wrong:* drop Task 6 steps 5–6.
16. **No quote-delay banner.** The SPA has no quote-delay alert today (`dashboard.html` has none; nothing reads quote freshness). Building one needs a staleness rule the spec does not define, so it is a new feature; the primitive is ready for it (PR C, with 國泰 run stale). Task 7 notes this beside task 1.2. *If wrong:* the owner wants it in this PR and must give the rule (e.g. "no TWSE quote since 15:45 on a trading day").
17. **Banner copy.**
    - Import failure (a): heading 「最近一次匯入失敗」, detail in the artboard's shape `<M/D HH:mm> · <file> · <reason>，沒有寫入任何資料。` (reason `有 N 列錯誤` or the stored reason; the old 「修正後重新上傳即可。」 is dropped for the one-line rule), no action (there is no reason page to send 查看原因 to).
    - Recalc failure (b): heading 「最近一次匯入的重算失敗」, detail = the reason.
    - Bill read error: heading 「帳單讀取失敗」, detail = the distinct reasons joined with 、 (none → no detail), action 重試.
    - Failing schedule: heading 「這一期入帳失敗」 (the phrase `CATCH_UP_FAILED_TOAST` already uses), detail = the server's `last_error`, action 重試.
    - Reward rules: heading 「N 條規則已過期但仍啟用」, detail 「之後的消費不會再估算回饋」, action 只看已過期.
    - Overdraft: heading 「帳戶餘額為負（<amount>）」, detail 「可能漏記入金。」, action 新增入金歸零.
    *If wrong:* strings only.

## Conflicts between the change spec and shipped behaviour

| Shipped requirement / behaviour | Conflict | Ruling |
|---|---|---|
| `stock-portfolio-import-orchestration` "Import page surfaces recalc result": partial → "orange warning toast … and a 重試 button that calls `POST /api/portfolio/imports/recalc`" | No orange variant; today's toast has no button | 失敗 variant with 重試 → `retryRecalc()` (fulfils the 重試 the old spec asked for); with deferred dividend events, 失敗 without 重試 (a retry cannot help) |
| same, "toast immediately after commit (Recalculation running…)" + `frontend-csv-import` "success toast displays the count" | One toast at a time would let the second replace the first at once | Ruling 11: one 進行中 toast carrying both |
| `frontend-accounting-ledger` "toast `匯入進行中，請稍後再試`" (3 s today) | 失敗 stays until closed | Stays until closed; copy unchanged; still "page state unchanged" |
| `frontend-portfolio-accounts` "a toast appears with severity `error` and message including `刪除失敗`" | Severity names go away | 失敗 toast whose text starts 「刪除失敗：」 |
| `frontend-csv-import` "Last failed import banner … state that the whole batch was not written" | Artboard sentence differs | Artboard 「沒有寫入任何資料。」 (same meaning) |
| `frontend-accounting-ledger` "Expired callout … warning banner" (`--app-state-warning-bg` today) | Look changes | 注意 tone (amber) — closer to "warning" than today's blue-grey |
| Inbox 已處理 list labels its undo 撤銷; the spec's toast says 復原 | Two words for undo | Both stay in PR A (ruling 8); unify when PR D adds the toast |
| Spec scenario "Success with undo: a reconciliation answer is applied → 完成 toast offers 復原" | The inbox answers have no toast today; adding one is a feature | Deferred to PR D (ruling 8) |

## Review Focus

- One toast at a time across sources: a page 失敗 followed by another page's 完成 (newest wins; the older 完成 timer never clears the newer toast).
- 760–1023 px with the detail sheet open: an `import_running` delete inside the sheet shows the 失敗 toast above the backdrop, Tab stays inside the sheet, ✕ by pointer closes the toast, the sheet's Esc still closes the sheet.
- Phone: the toast sits above the tab bar, including in a bottom sheet (`sheet.scss`, z 1100) and with the safe-area inset.
- Import page: commit → 進行中 → 完成 / 失敗 replacement, leaving the page while 進行中 (toast removed), a first poll that is already settled (no 進行中 left behind).
- `grep -rn "p-toast\|primeng/toast\|MessageService\|AccountingToast\|app-accounting-toast" frontend/src` is empty after Task 4.
- Banner dismissal: only 資訊 with a `dismissId` shows ✕; 錯誤 / 注意 ignore `dismissId`; storage that throws still lets the owner close it for the session.

---

### Task 1: `ToastService` and the shell outlet

D6, tasks 1.1 (service + outlet) and 1.3 (one at a time; 失敗 stays).

**Files:**
- Create: `frontend/src/app/services/toast.service.ts`, `frontend/src/app/services/toast.service.spec.ts`
- Create: `frontend/src/app/components/shell/toast-outlet/toast-outlet.ts`, `toast-outlet.scss`, `toast-outlet.spec.ts`
- Modify: `frontend/src/app/components/shell/shell.ts` (import), `shell.html` (one `<app-toast-outlet />` after the `@if (showFab())` block, before `<app-mobile-nav />`), `shell.spec.ts`
- Modify: `frontend/src/styles.scss` (three tokens in `:root` and in `.app-dark-mode`), `frontend/src/app/theme/contrast.spec.ts`

**Interfaces (produced):**

```ts
// services/toast.service.ts
export type ToastKind = 'done' | 'progress' | 'error';
export interface ToastAction { readonly label: '復原' | '重試'; readonly run: () => void; }
export interface Toast { readonly id: number; readonly kind: ToastKind; readonly text: string; readonly action: ToastAction | null; }
export const TOAST_DONE_MS = 5000;
/** `summary` alone when `detail` is empty, else `${summary}：${detail}` (ruling 2). */
export function toastText(summary: string, detail?: string | null): string;

@Injectable({ providedIn: 'root' })
export class ToastService {
  readonly current: Signal<Toast | null>;
  done(text: string, options?: { undo?: () => void }): number;   // 5 s; 復原 when undo is given
  progress(text: string): number;                                 // no timer
  error(text: string, options?: { retry?: () => void }): number;  // no timer; 重試 when retry is given
  /** No id: whatever is shown. With an id: only if that toast is still the current one. */
  dismiss(id?: number): void;
  /** Runs the current toast's action once (if `id` is current), then dismisses it. */
  runAction(id: number): void;
  pause(): void;   // freezes a 完成 timer
  resume(): void;  // restarts it with the remaining time
}
```
Each call replaces the current toast and clears its timer (ids increase). The service injects `Router` and, on `NavigationEnd` whose path (URL without `?` / `#`) differs from the previous one, dismisses a `progress` toast (ruling 5).

`ToastOutletComponent` — selector `app-toast-outlet`, OnPush, no inputs. Template: two steady live regions that never leave the DOM, `div.toast-region[role="status"][aria-live="polite"][aria-atomic="true"]` (完成 / 進行中) and `div.toast-region[role="alert"][aria-atomic="true"]` (失敗). The toast: `div.toast.toast--done|progress|error[data-toast-id]` > `i.toast-icon` (`pi pi-check` / `pi pi-spinner pi-spin` / `pi pi-exclamation-circle`, `aria-hidden`), `span.toast-text`, optional `button.toast-action` (label from the action), and for 失敗 only `button.toast-close[aria-label="關閉"]` (`pi pi-times`). Host listeners: `pointerenter` / `focusin` → `pause()`, `pointerleave` / `focusout` leaving the host → `resume()`; a `document:focusin` listener remembers the last focused element outside the host for ruling 6.

Styles (`toast-outlet.scss`, per the artboard): host `position: fixed; z-index: 1150; left: calc(var(--sidebar-w, 0px) + 24px); bottom: 30px; width: min(440px, calc(100vw - var(--sidebar-w, 0px) - 134px)); pointer-events: none` (134 px keeps the 56 px FAB at right 30 px clear); `@media (max-width: 759.98px) { left: 12px; right: 12px; width: auto; bottom: calc(64px + env(safe-area-inset-bottom, 0px) + 12px) }` (the 64 px tab bar). `.toast { pointer-events: auto; display: flex; align-items: center; gap: 10px; min-height: 48px; padding: 0 8px 0 14px; border-radius: 14px; background: var(--app-accent); color: var(--app-surface); box-shadow: var(--app-raised-shadow) }`; text 14 px / 600, wraps; action 36 px high, 14 px / 800, `color: var(--app-toast-action)`, transparent; close 36 × 36. Icon colours: done `--app-toast-ok`, progress `--app-toast-action`, error `--app-toast-error`. `@media (prefers-reduced-motion: reduce)` drops the spin and any entry transition.

- [ ] **Step 1: Failing tests** — `toast.service.spec.ts` (fake timers, `provideRouter([{ path: 'a', children: [] }, { path: 'b', children: [] }])`):
  ```ts
  it('shows one toast at a time: a newer call replaces the current one', () => {
    s.done('已確認'); s.error('儲存失敗，網路中斷');
    expect(s.current()).toEqual(expect.objectContaining({ kind: 'error', text: '儲存失敗，網路中斷' }));
  });
  it('完成 disappears after 5 s', () => { s.done('已確認'); vi.advanceTimersByTime(4999); expect(s.current()).not.toBeNull(); vi.advanceTimersByTime(1); expect(s.current()).toBeNull(); });
  it('失敗 stays until it is dismissed', () => { const id = s.error('儲存失敗'); vi.advanceTimersByTime(60_000); expect(s.current()?.id).toBe(id); s.dismiss(id); expect(s.current()).toBeNull(); });
  it('進行中 stays until its result replaces it', () => { s.progress('資料重算執行中…'); vi.advanceTimersByTime(60_000); expect(s.current()?.kind).toBe('progress'); s.done('資料重算完成'); expect(s.current()?.kind).toBe('done'); });
  it('a replaced 完成 timer never clears the newer toast', () => { s.done('A'); vi.advanceTimersByTime(3000); s.error('B'); vi.advanceTimersByTime(5000); expect(s.current()?.text).toBe('B'); });
  it('dismiss(id) of an older toast leaves the current one', () => { const a = s.done('A'); s.error('B'); s.dismiss(a); expect(s.current()?.text).toBe('B'); });
  it('labels: undo → 復原 on 完成, retry → 重試 on 失敗, none otherwise', ...);   // action?.label; progress never has one
  it('runAction runs the callback once and dismisses', () => { const retry = vi.fn(); const id = s.error('帳戶讀取失敗', { retry }); s.runAction(id); s.runAction(id); expect(retry).toHaveBeenCalledTimes(1); expect(s.current()).toBeNull(); });
  it('pause keeps a 完成 past 5 s; resume continues with the remaining time', () => { s.done('A'); vi.advanceTimersByTime(3000); s.pause(); vi.advanceTimersByTime(10_000); expect(s.current()).not.toBeNull(); s.resume(); vi.advanceTimersByTime(1999); expect(s.current()).not.toBeNull(); vi.advanceTimersByTime(1); expect(s.current()).toBeNull(); });
  it('a navigation to another path clears 進行中 only', async () => { /* at /a: progress → navigate /b → null; error → navigate /a → still there; progress → navigate /a?x=1 → still there */ });
  it('toastText joins with a full-width colon and drops an empty detail', () => { expect(toastText('匯入失敗', '伺服器錯誤')).toBe('匯入失敗：伺服器錯誤'); expect(toastText('無法取得收款帳戶', '')).toBe('無法取得收款帳戶'); });
  ```
  `toast-outlet.spec.ts`:
  - `keeps both live regions in the DOM with nothing to show` — `[role="status"][aria-live="polite"]` and `[role="alert"]` exist with empty text;
  - `完成 renders in the status region with 復原 and no close` — `.toast--done .toast-text` = `已入帳 測試 3 期`, `.toast-action` = `復原`, no `.toast-close`, nothing in the alert region;
  - `進行中 renders in the status region with neither action nor close`;
  - `失敗 renders in the alert region with 重試 and a 關閉 button` — `[role="alert"] .toast--error`, `.toast-close[aria-label="關閉"]`;
  - `復原 / 重試 run their callback once and remove the toast`; `關閉 removes a 失敗 toast`;
  - `pointerenter pauses and pointerleave resumes the 5 s timer`; `focusin pauses; focusout to an outside element resumes`;
  - `a new toast never moves focus` — focus a fixture button, `error(...)`, `document.activeElement` is still that button;
  - `closing from inside returns focus to the last element focused outside` — focus `#outside`, focus `.toast-close`, click it → `document.activeElement.id === 'outside'`;
  - `scss: fixed, z-index 1150, bottom-left of the content area, above the tab bar below 760 px, no colour literal` — read `toast-outlet.scss` with `readFileSync(join(process.cwd(), 'src/app/components/shell/toast-outlet/toast-outlet.scss'))` and assert `z-index: 1150`, `left: calc(var(--sidebar-w`, `bottom: calc(64px + env(safe-area-inset-bottom`, and `!/#[0-9a-f]{3,8}\b|rgba?\(/i`.
  `shell.spec.ts`: `renders one toast outlet inside .hub-layout at every width` (1440, 820, 390: `.hub-layout > app-toast-outlet` count 1).
  `contrast.spec.ts`: `describe('toast inks on the inverse surface')` — in both theme blocks, `--app-surface` on `--app-accent` ≥ 4.5, `--app-toast-action` on `--app-accent` ≥ 4.5, `--app-toast-ok` and `--app-toast-error` on `--app-accent` ≥ 3.
- [ ] **Step 2: Run** `cd frontend && npx ng test --watch=false --include='src/app/services/toast.service.spec.ts'` and the same for `'src/app/components/shell/**/*.spec.ts'` and `'src/app/theme/contrast.spec.ts'` — Expected: the new cases FAIL.
- [ ] **Step 3: Implement** as specified. Tokens: `:root` `--app-toast-action: #b3a8ff; --app-toast-ok: #5ad19c; --app-toast-error: #ff8a8e;` and `.app-dark-mode` `--app-toast-action: #533afd; --app-toast-ok: #177a55; --app-toast-error: #c9373c;`, each with a one-line comment "ink on the inverse toast surface (`--app-accent`)".
- [ ] **Step 4: Run** the three commands — Expected: PASS.
- [ ] **Step 5: Commit** `feat(ui): one shell-level toast with 完成 / 進行中 / 失敗`.

### Task 2: Accounting pages move off `AccountingToastService`

**Files:**
- Delete: `frontend/src/app/components/accounting/accounting-toast.ts`, `accounting-toast.spec.ts`
- Modify: `frontend/src/app/components/accounting/http-errors.ts` (gains `scheduleActionError`), `http-errors.spec.ts`
- Modify (call sites): `account-entries/account-entries.ts`, `account-settings/account-settings.ts`, `entry-form/entry-form.ts`, `statements/statement-detail/statement-detail.ts`, `reminders/reminders.ts`, `reminders/schedule-sheet/schedule-sheet.ts`, `entry-detail/entry-detail.ts`, `split-group/split-group.ts` (all under `frontend/src/app/components/accounting/`)
- Modify (outlet removal): `accounting-layout/accounting-layout.ts` (drop `AccountingToastComponent` from `imports`), `accounting-layout.html` (drop `<app-accounting-toast />`, last line)
- Modify (specs): `account-entries/account-entries.spec.ts`, `account-settings/account-settings.spec.ts`, `entry-form/entry-form-schedule.spec.ts`, `statements/statement-detail/statement-detail.spec.ts`, `reminders/reminders.spec.ts`, `reminders/schedule-sheet/schedule-sheet.spec.ts`, `entry-detail/entry-detail-schedule.spec.ts`, `accounting-layout/accounting-layout.spec.ts`

**Interfaces:**
- Consumes: `ToastService`, `toastText` (Task 1).
- Produces: `scheduleActionError(err: unknown, toast: ToastService): string | null` in `http-errors.ts` — `import_running` → `toast.error(IMPORT_RUNNING_TOAST)` and `null`; anything else → `writeErrorMessage(err)`. Each component replaces `inject(AccountingToastService)` with `inject(ToastService)`.

Copy table (22 call sites in 8 files; text unchanged, variant per ruling 1):

| File:line today | Call | New call |
|---|---|---|
| `account-entries.ts:132`, `account-settings.ts:960` | `show('圖片太大（上限 5 MB）')` | `error('圖片太大（上限 5 MB）')` |
| `account-entries.ts:146`, `account-settings.ts:973`, `:999` | `show(logoUploadErrorMessage(err))` | `error(logoUploadErrorMessage(err))` |
| `entry-form.ts:2300`, `:2328` | `show(IMPORT_RUNNING_TOAST)` | `error(IMPORT_RUNNING_TOAST)` |
| `entry-form.ts:2321` | `show(CATCH_UP_FAILED_TOAST)` | `error(CATCH_UP_FAILED_TOAST)` |
| `statement-detail.ts:200`, `:294` | `show('這次與上次相同，沒有重複執行')` | `done(…)` |
| `statement-detail.ts:202` | skipped: `有待確認的變更，這次沒有重跑對帳` / `這次略過（${skipped}）` | `error(…)` same strings |
| `statement-detail.ts:208` | `show('已重新對帳')` | `done('已重新對帳')` |
| `statement-detail.ts:296` | `show(forced ? '已強制確認' : '已確認')` | `done(…)` |
| `reminders.ts:693`, `:798`; `schedule-sheet.ts:232`, `:236`, `:453`, `:457`; `entry-detail.ts:643`, `:832`; `split-group.ts:246` | `scheduleActionError(err, this.toast)` | unchanged call; `this.toast` is now `ToastService` and the import moves to `../http-errors` / `../../http-errors` |

- [ ] **Step 1: Failing tests.**
  - `http-errors.spec.ts`: `scheduleActionError turns import_running into a 失敗 toast and anything else into an error line` — 409 `{message:'import_running'}` → returns `null`, `toast.current()` = `{ kind: 'error', text: '匯入進行中，請稍後再試' }`; 409 `只有進行中的排程可以暫停（目前 paused）` → returns that text, `current()` null.
  - Rewrite each existing toast assertion to read `TestBed.inject(ToastService)`: `entry-detail-schedule.spec.ts:317`, `reminders.spec.ts:937`, `schedule-sheet.spec.ts:222` and `:631`, `entry-form-schedule.spec.ts:220` / `:240` (`current()` null) / `:263` → `expect(toast.current()).toEqual(expect.objectContaining({ kind: 'error', text: '…' }))`; `statement-detail.spec.ts:498–536` → spies on `done` and `error` (replays and 已重新對帳 / 已確認 on `done`, the skip on `error`); `account-settings.spec.ts:2122`, `:2149` and `account-entries.spec.ts:530`, `:589` → `vi.spyOn(TestBed.inject(ToastService), 'error')`.
  - `accounting-layout.spec.ts`: `SCREEN` (line 78) becomes `':scope > router-outlet + *'`; new `renders no toast of its own` — no `app-accounting-toast` and no `.accounting-toast` in the layout.
  - `entry-form-schedule.spec.ts`: new `the catch-up 失敗 survives the form closing` — after the catch-up `failed` result and `finish()`, `toast.current()?.text` is `排程已建立；這一期入帳失敗，請到待完成交易處理`.
- [ ] **Step 2: Run** `--include='src/app/components/accounting/**/*.spec.ts'` — Expected: the rewritten cases FAIL (compile errors count).
- [ ] **Step 3: Implement** per the table; delete the two files; `grep -rn "AccountingToast\|accounting-toast" frontend/src` is empty.
- [ ] **Step 4: Run** the same command — Expected: PASS.
- [ ] **Step 5: Commit** `refactor(accounting): toasts go through the shell ToastService`.

### Task 3: Import page toasts

The import page has 14 of the 64 PrimeNG call sites and the recalc polling, so it is its own task (ruling 11).

**Files:**
- Modify: `frontend/src/app/components/portfolio/import/import.ts`, `import.html` (drop `<p-toast></p-toast>`, line 1), `import.spec.ts`

**Interfaces:**
- `PortfolioImportComponent`: drop `ToastModule` from `imports` and `MessageService` from `providers` (keep `ConfirmationService`); `private readonly toast = inject(ToastService)`; `private recalcToastId: number | null = null` — the id of the 進行中 toast this page raised; every settled status clears it: a transition shows its result (which replaces the toast), a non-transition calls `this.toast.dismiss(this.recalcToastId)`.

Copy table (14 call sites):

| Line today | New |
|---|---|
| 354 | `error('請先填入代號')` |
| 359 | `error('無樣本日期可驗證')` |
| 383 | `error(toastText('驗證失敗', detail))` |
| 481 | `recalcToastId = progress('資料重算執行中…')` |
| 488 | `error(toastText('重算觸發失敗', detail), { retry: () => this.retryRecalc() })` |
| 498 | `error('請先選擇 CSV')` |
| 563 (券商) | success → `done(…)`, warn → `error(…)`; text `toastText(dryRun ? '預覽完成' : '匯入完成', brokerCountSummary(result))` |
| 571 | `error(toastText('匯入失敗', importErrorDetail(err)))` |
| 619 + 622 | dry run or no recalc: success → `done(toastText(summary, detail))`, warn → `error(…)`. Commit with `recalc_scheduled`: success → `recalcToastId = progress(\`${toastText('匯入完成', detail)}；資料重算執行中…\`)`; warn → `error(toastText('匯入完成', detail))` and no 進行中. Polling starts in both cases. |
| 676 | `done('資料重算完成')` |
| 684 (partial) | with deferred events: `error('資料重算尚未完成：歷史股利權益已記錄。請至股利頁確認實際收款；重算不會自動入帳。')`; else `error(\`資料重算尚未完成（失敗步驟：${failed \|\| '未知'}）\`, { retry: () => this.retryRecalc() })` |
| 694 | `error(toastText('資料重算失敗', firstError))` |
| 714 | `error('無法取得重算狀態', { retry: () => this.startPolling() })` |

- [ ] **Step 1: Failing tests** (`import.spec.ts`; spy with `vi.spyOn(TestBed.inject(ToastService), 'done' | 'progress' | 'error')` or read `current()`):
  - rewrite `a partial recalc toast lists the failed steps by label` (`:267`) → `current()` = `{ kind: 'error', text: '資料重算尚未完成（失敗步驟：淨值回補）', action: { label: '重試' } }`; its `runAction` posts one `/api/portfolio/imports/recalc`;
  - rewrite `a failed 重算資料 toast reads the shared body message` (`:286`) → text `重算觸發失敗：recalc in progress` with 重試;
  - rewrite `a Cathay dry-run toast reads the preview counts…` (`:643`), `the commit toast says 更新 {rehashed} 筆` (`:804`), `…對帳單已涵蓋 {covered} 筆…` (`:814`) to read `current()?.text`;
  - new `a clean commit with a recalc shows one 進行中 toast with the counts` — text starts `匯入完成：已解析 ` and ends `；資料重算執行中…`; `progress` called once, `done` not called;
  - new `a commit with row errors shows 失敗 with the counts and no 進行中`;
  - new `running → completed replaces the 進行中 toast with 資料重算完成`;
  - new `partial with deferred events: 失敗 without 重試`;
  - new `a status read failure shows 無法取得重算狀態 with 重試, which reads the status again` (one more GET `/recalc/status`);
  - new `a first poll that is already settled dismisses the page's 進行中 toast` (prev state `completed`, commit, status `completed` → `current()` null);
  - new `leaving the page while 進行中 removes the toast` (navigate away in a `provideRouter` fixture);
  - new `renders no p-toast` — no `p-toast` element in the fixture.
- [ ] **Step 2: Run** `--include='src/app/components/portfolio/import/*.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement** per the table.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `refactor(import): import and recalc feedback through the shell toast`.

### Task 4: The other portfolio pages, inventory, the interceptor; PrimeNG toast removed

**Files:**
- Modify: `frontend/src/app/components/portfolio/accounts/account-detail.ts`, `account-detail.html` (line 1), `account-detail.component.spec.ts`
- Modify: `frontend/src/app/components/portfolio/accounts/accounts-list.ts`, `accounts-list.html` (line 1)
- Modify: `frontend/src/app/components/portfolio/accounts/accounts-page.scss` (comment at line 29: drop "toast, ")
- Modify: `frontend/src/app/components/portfolio/cash-flow-form/cash-flow-form.ts` (inline `<p-toast>` at line 53)
- Modify: `frontend/src/app/components/portfolio/dashboard/dashboard.ts`, `dashboard.spec.ts`
- Modify: `frontend/src/app/components/portfolio/dividend-list/dividend-list.ts`, `dividend-list.html` (line 1), `dividend-list.spec.ts`
- Modify: `frontend/src/app/components/portfolio/realized-pnl/realized-pnl.ts`, `realized-pnl.html` (line 1), `realized-pnl.component.spec.ts`
- Modify: `frontend/src/app/components/portfolio/transaction-list/transaction-list.ts`, `transaction-list.html` (line 1), `transaction-list.spec.ts`
- Modify: `frontend/src/app/components/item-list/item-list.ts`, `item-list.html` (line 1), `item-list.spec.ts`
- Modify: `frontend/src/app/interceptors/error-logging.interceptor.ts`, `error-logging.interceptor.spec.ts`
- Modify: `frontend/src/app/app.config.ts` (drop the `MessageService` import and provider)

**Interfaces:** each component drops `ToastModule` from `imports`, `MessageService` from `providers` (keeps `ConfirmationService` where present), and injects `ToastService`. The interceptor keeps `retry(1)`, `SILENT_HTTP_ERRORS` and its console group, and no longer injects anything for toasts (ruling 9). `dashboard.ts` `notify(msg)` becomes `this.toast.error(msg)`; the `ngOnInit` branch (line 349) calls `console.warn('另一筆重算進行中，稍候再試')` instead of `notify` (ruling 10).

Copy table (50 call sites; R = with `{ retry }`):

| File | Line today → new call |
|---|---|
| `account-detail.ts` (10) | 468 `done('帳戶資料已更新')` · 474 `error('帳戶更新失敗')` · 542 `done('交易已建立')` · 547 `error('新增交易失敗')` · 583 `done('已刪除')` · 586 `error(toastText('刪除失敗', this.deleteErrorMessage(error)))` · 659 `error(toastText('已取消', ACCOUNT_SWITCHED_DETAIL))` · 690 `error('帳戶讀取失敗')` R `loadAccount()` · 709 `error('餘額走勢讀取失敗')` R `loadBalanceHistory()` · 723 `error('現金交易讀取失敗')` |
| `accounts-list.ts` (3) | 170 `error('帳戶讀取失敗')` R `fetchAccounts()` · 211 `done('帳戶已建立')` · 216 `error('新增帳戶失敗')` |
| `cash-flow-form.ts` (2) | 180 `done(toastText('已新增', \`${BROKER_LABELS[raw.broker]} ${result.currency}\`))` · 194 `error(err?.error?.detail ?? '新增失敗')` |
| `dashboard.ts` (1) | 823 `error(msg)`; 349 / 472 copy `另一筆重算進行中，稍候再試` (ruling 10) |
| `dividend-list.ts` (11) | 261 `error(toastText('收款確認未完成', serverReason(err)))` · 326 `error(toastText('扣款更新未完成', serverReason(err)))` · 359 `error('無法取得收款帳戶')` · 434 `error('查詢失敗，請檢查篩選條件')` · 632 `error('需人工釐清，不可直接改寫已收款或舊資料')` · 664 `done('紀錄已刪除')` · 668 `error('刪除失敗，請稍後再試')` · 681 `done('紀錄已更新')` · 684 `error('更新失敗，請檢查欄位')` · 692 `done('紀錄已新增')` · 695 `error('新增失敗，請檢查欄位')` |
| `transaction-list.ts` (7) | 204 `error('查詢失敗，請檢查篩選條件')` · 416 `done('紀錄已刪除')` · 420 `error('刪除失敗，請稍後再試')` · 569 `done('紀錄已更新')` · 572 `error('更新失敗，請檢查欄位')` · 580 `done('紀錄已新增')` · 583 `error('新增失敗，請檢查欄位')` |
| `realized-pnl.ts` (1) | 237 `error('查詢失敗，請檢查篩選條件')` |
| `item-list.ts` (14) | 108 `error('無法載入物品清單')` R `loadItems()` · 150 `error('請檢查名稱、數量與門檻欄位')` · 170 `done('物品已更新')` · 174 `error(err?.error?.message \|\| '更新失敗')` · 181 `done('物品已建立')` · 185 `error(err?.error?.message \|\| '建立失敗')` · 195 `done('圖片上傳成功')` · 198 `error('圖片上傳失敗')` · 229 `error(err?.error?.message \|\| '操作失敗')` · 273 `error('無法載入異動歷史')` · 281 `done('物品已刪除')` · 284 `error('刪除失敗')` · 331 `done(successMessage)` · 335 `error(err?.error?.message \|\| '操作失敗')` |
| `error-logging.interceptor.ts` (1) | 46 removed |

- [ ] **Step 1: Failing tests.** Replace every `vi.spyOn(fixture.debugElement.injector.get(MessageService), 'add')` / `vi.spyOn(MessageService.prototype, 'add')` with `vi.spyOn(TestBed.inject(ToastService), '<done|error>')` and the new text:
  - `account-detail.component.spec.ts:394` (`accepting confirmation deletes…` → `done('已刪除')`), `:448` (`surfaces delete errors…` → `error` text starting `刪除失敗：`), `:761` (`toasts` helper → `error` with `已取消：帳戶已切換，請重新操作`);
  - `dividend-list.spec.ts:316`, `:406`, `:1235` (`收款確認未完成：Synthetic stale revision`), `:1355`, `:1396`, `:1429`, `:1519`, `:1539` / `:1545` and `:1554` / `:1557` (`需人工釐清，不可直接改寫已收款或舊資料` on `error`), `:1710`, `:1741`, `:1861`;
  - `realized-pnl.component.spec.ts:433`; `transaction-list.spec.ts:539`.
  - New: `item-list.spec.ts` `a failed list load shows 失敗 無法載入物品清單 with 重試, which loads again` (`getAllFiltered` called twice).
  - New: `dashboard.spec.ts` `刷新行情 answered 409 shows 失敗 另一筆重算進行中，稍候再試; the load on open does not`.
  - `error-logging.interceptor.spec.ts`: drop the `MessageService` provider; `by default a failing request is retried once and toasts one error` becomes `…is retried once, logged, and propagated without a toast` (`TestBed.inject(ToastService).current()` stays null); the `SILENT_HTTP_ERRORS` cases keep their assertions minus `add`.
  - New in `toast-outlet.spec.ts`: `no PrimeNG toast remains` — a source scan (`readdirSync` recursion over `src/app`, `.ts` / `.html`, excluding `*.spec.ts`) for `p-toast`, `primeng/toast` or `MessageService` → empty list.
- [ ] **Step 2: Run** `--include='src/app/components/portfolio/**/*.spec.ts'`, `--include='src/app/components/item-list/*.spec.ts'`, `--include='src/app/interceptors/*.spec.ts'`, `--include='src/app/components/shell/toast-outlet/*.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement** per the table; remove `MessageService` from `app.config.ts` last.
- [ ] **Step 4: Run** the four commands — Expected: PASS. `grep -rn "p-toast\|primeng/toast\|MessageService" frontend/src` — Expected: no output.
- [ ] **Step 5: Commit** `refactor(portfolio): drop PrimeNG toast; every page uses the shell toast`.

### Task 5: `<app-banner>`

D7, tasks 1.2 (primitive) and 1.3 (dismissal remembered for 資訊 only).

**Files:**
- Create: `frontend/src/app/components/ui/banner/banner.ts`, `banner.scss`, `banner.spec.ts`, `banner-dismissals.ts`, `banner-dismissals.spec.ts`
- Modify: `frontend/src/app/theme/contrast.spec.ts`

**Interfaces (produced):**

```ts
// components/ui/banner/banner.ts
export type BannerTone = 'error' | 'warn' | 'info';
@Component({ selector: 'app-banner', changeDetection: OnPush, styleUrl: './banner.scss' })
export class BannerComponent {
  readonly tone = input.required<BannerTone>();
  readonly heading = input.required<string>();
  readonly detail = input<string | null>(null);
  readonly actionLabel = input<string | null>(null);
  readonly actionDisabled = input(false);
  /** Only for tone 'info': makes it dismissible and is the remembered key. Ignored for error / warn. */
  readonly dismissId = input<string | null>(null);
  readonly action = output<void>();
}
// components/ui/banner/banner-dismissals.ts
export const BANNER_DISMISSALS_KEY = 'hh-banner-dismissed';   // JSON string[]
@Injectable({ providedIn: 'root' })
export class BannerDismissals {
  isDismissed(id: string): boolean;  // signal-backed, read from storage once
  dismiss(id: string): void;         // updates memory first; storage write in try/catch
}
```
Template: nothing when dismissed; else `div.banner.banner--<tone>[role]` (error `alert`, else `status`) > `i.banner-icon` (`pi pi-exclamation-circle` / `pi pi-exclamation-triangle` / `pi pi-info-circle`, `aria-hidden`), `div.banner-body` > `b.banner-title` + optional `span.banner-detail`, optional `button.banner-action` (`type="button"`, `[disabled]`), and for `info` with `dismissId` `button.banner-close[aria-label="關閉"]` (`pi pi-times`). Styles per the artboard: `display: flex; align-items: flex-start; gap: 12px; padding: 12px 14px; border-radius: 14px`; icon 20 px; title 14 px / 800; detail 13 px, `line-height: 1.5`; action `min-height: 34px; padding: 0 12px; border-radius: 10px; font-size: 13px; font-weight: 700`; below 480 px the action wraps under the text (`flex-wrap: wrap`, body `flex: 1 1 220px`). Tokens per ruling 13.

- [ ] **Step 1: Failing tests** (`banner.spec.ts`, host component pattern as in `tag.spec.ts`):
  - `error renders role alert with heading, detail and action, and no close even with a dismissId`;
  - `warn and info render role status` (`.banner--warn`, `.banner--info`);
  - `the action button emits once per click and honours actionDisabled`;
  - `no detail → no .banner-detail`; `no actionLabel → no .banner-action`;
  - `info with a dismissId shows 關閉; closing hides it and stores the id` — `JSON.parse(localStorage.getItem('hh-banner-dismissed')!)` contains `'test-info'`;
  - `a dismissed info id stays hidden in a new instance; another id still shows`;
  - `info without a dismissId has no close`;
  - `storage that throws: the banner renders, closes, and stays closed for the session` (`vi.spyOn(Storage.prototype, 'getItem' | 'setItem').mockImplementation(() => { throw new Error('blocked'); })`);
  - `scss uses tokens only` — `banner.scss` matches `var(--c-red-soft)`, `var(--c-red-pill-ink)`, `var(--app-banner-warn-soft)`, `var(--app-banner-warn)`, `var(--app-primary-soft)` and has no colour literal.
  `banner-dismissals.spec.ts`: `reads a stored list once`; `ignores a malformed value`; `dismiss is idempotent`.
  `contrast.spec.ts`: `describe('banner headings on their tints over the page background')` — both themes, composited over `--app-bg` and `--app-surface`: `--c-red-pill-ink` on `--c-red-soft` ≥ 4.5, `--app-banner-warn` on `--app-banner-warn-soft` ≥ 4.5, `--app-text` on `--app-primary-soft` ≥ 4.5, `--app-primary` (icon) on `--app-primary-soft` ≥ 3.
- [ ] **Step 2: Run** `--include='src/app/components/ui/banner/*.spec.ts'` and the contrast spec — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(ui): app-banner with 錯誤 / 注意 / 資訊 tones`.

### Task 6: Page alerts move onto `<app-banner>`

Rulings 15 and 17. Six alerts in four templates.

**Files:**
- Modify: `frontend/src/app/components/portfolio/import/import-math.ts` (`failureBanner`, `recalcBanner`), `import-math.spec.ts`
- Modify: `frontend/src/app/components/portfolio/import/import.ts` (imports `BannerComponent`), `import.html` (lines 7–18), `import.scss` (remove `.import-banner`), `import.spec.ts`
- Modify: `frontend/src/app/components/accounting/reminders/reminders.ts` (`billErrorText` → `billErrorDetail`), `reminders.html` (lines 65–70 and 199–204), `reminders.scss` (remove `.bill-error`, `.schedule-failing` rules), `reminders.spec.ts`
- Modify: `frontend/src/app/components/accounting/reward-rules/reward-rules.ts` (imports), `reward-rules.html` (lines 25–31), `reward-rules.scss` (remove `.rr-banner*`), `reward-rules.spec.ts`
- Modify: `frontend/src/app/components/portfolio/accounts/account-detail.ts` (imports), `account-detail.html` (lines 65–78), `account-detail.scss` (remove `.overdraft-banner` and its media rule), `account-detail.component.spec.ts`

**Interfaces:**
- `failureBanner(a: ImportAttempt | undefined): { heading: string; detail: string } | null` — `{ heading: '最近一次匯入失敗', detail: \`${stamp(a.at)} · ${a.file} · ${reason}，沒有寫入任何資料。\` }`, `reason` = `有 ${a.errors} 列錯誤` when `errors > 0`, else `a.reason || '未知錯誤'`. Same null conditions as today.
- `recalcBanner(status: RecalcStatus): { heading: string; detail: string } | null` — `{ heading: '最近一次匯入的重算失敗', detail: reason }`, same null conditions and reason fallbacks as today.
- `AccountingRemindersComponent.billErrorDetail = computed<string | null>` — distinct reasons joined with `、`, `null` when none.
- Markup (host classes keep the old names so the surrounding layout and tests can find them):
  - `<app-banner class="import-banner" tone="error" [heading]="b.heading" [detail]="b.detail" />` for (a) and (b), in that order.
  - `<app-banner class="bill-error" tone="error" heading="帳單讀取失敗" [detail]="billErrorDetail()" actionLabel="重試" (action)="retryBills()" />`.
  - `<app-banner class="schedule-failing" tone="error" heading="這一期入帳失敗" [detail]="failing.text" actionLabel="重試" [actionDisabled]="retryingDefinitions().has(row.id)" (action)="retryDefinition(row)" />`.
  - `<app-banner class="rr-banner" tone="warn" [heading]="count + ' 條規則已過期但仍啟用'" detail="之後的消費不會再估算回饋" actionLabel="只看已過期" (action)="setFilter('expired')" />`.
  - `<app-banner class="overdraft-banner" tone="warn" [heading]="'帳戶餘額為負（' + formatCurrency(account()?.native_balance, account()?.currency || 'TWD') + '）'" detail="可能漏記入金。" actionLabel="新增入金歸零" [actionDisabled]="!accountReady()" (action)="openTopupQuickFix()" />` (the PrimeNG `p-button` goes; `ButtonModule` stays — the template has seven other `p-button`s).

- [ ] **Step 1: Failing tests.**
  - `import-math.spec.ts` `failureBanner` (`:591`–`:611`): `{ heading: '最近一次匯入失敗', detail: '10/21 22:10 · a.csv · 有 1 列錯誤，沒有寫入任何資料。' }` and `'10/21 22:10 · a.csv · HTTP 500，沒有寫入任何資料。'`; `recalcBanner` (`:615`–`:632`): `detail` `TWSE timeout`, `淨值回補`, `等待鎖定`, `未知錯誤`, `future_step`.
  - `import.spec.ts` banner block (`:1017`–`:1139`): the `banner()` helper reads `app-banner.import-banner` heading + detail; expectations become `最近一次匯入失敗` / `10/21 22:10 · a.csv · 伺服器錯誤，沒有寫入任何資料。`, `… 有 1 列錯誤，…`, `最近一次匯入的重算失敗` / `TWSE timeout`, `淨值回補`; add `both import banners are 錯誤: role alert, no close, no action`.
  - `reminders.spec.ts`: `shows 帳單讀取失敗 with 重試…` (`:350`) → `.bill-error .banner-title` = `帳單讀取失敗`, no `.banner-detail`, `.bill-error .banner-action` click retries; `:397` → `.banner-detail` = `幣別不同`; `retries a failing schedule with catch-up` (`:989`) → `.schedule-failing .banner-title` = `這一期入帳失敗`, `.banner-detail` = `lines[0].account_id: 帳戶已封存`, `.banner-action` click posts the catch-up; `sends one catch-up for a double click on 重試` (`:1002`) → the action is disabled while retrying.
  - `reward-rules.spec.ts` `expired banner…` (`:404`): `.rr-banner .banner-title` = `1 條規則已過期但仍啟用`, `.banner-detail` = `之後的消費不會再估算回饋`, `.banner--warn`, `[role="status"]`, `.banner-action` 只看已過期 selects the chip; the two "no banner" cases keep `.rr-banner` null.
  - `account-detail.component.spec.ts` `header card: … overdraft banner…` (`:1222`, assertion `:1239`): `.overdraft-banner .banner-title` = `帳戶餘額為負（−$12,345）`, `.banner-detail` = `可能漏記入金。`, `.banner--warn`; new `新增入金歸零 opens the top-up form and is disabled until the account is ready`.
- [ ] **Step 2: Run** `--include='src/app/components/portfolio/import/*.spec.ts'`, `--include='src/app/components/accounting/reminders/*.spec.ts'`, `--include='src/app/components/accounting/reward-rules/*.spec.ts'`, `--include='src/app/components/portfolio/accounts/*.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement**; remove the old banner rules from each stylesheet (no orphan selectors).
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `refactor(ui): import, bill, schedule, reward-rule and overdraft alerts use app-banner`.

### Task 7: Tasks, design index, full suite, evidence

**Files:**
- Modify: `openspec/changes/add-notification-center/tasks.md` — tick 1.1, 1.2, 1.3; after 1.2 append "(no quote-delay alert exists in the SPA; the banner is ready for it and for 國泰 run stale in PR C)".
- Modify: `design_handoff_v3/README.md` — under **Colour mapping** add rows: toast surface `#1d2433` / `#e6ebf5` → `--app-accent` with `--app-surface` text; toast action / ok / error → new `--app-toast-action` / `--app-toast-ok` / `--app-toast-error` (dark ok is `#177a55`: the artboard's `#1f9d6b` is 2.9:1 on the light toast); error banner title on its tint → `--c-red-pill-ink` (the artboard `#c9373c` is 4.0:1 there); info banner → `--app-primary-soft` / `--app-primary`.

- [ ] **Step 1:** Edit both files.
- [ ] **Step 2: Run** `openspec validate add-notification-center --strict` — Expected: valid.
- [ ] **Step 3: Run** `cd frontend && npx ng test --watch=false` — Expected: all files pass; `npx ng build` — Expected: success, and no `primeng/toast` chunk in the build stats.
- [ ] **Step 4: Evidence (outside the repo).** Build origin/main (before) and the branch (after); Playwright with routed API stubs (fictional data) at 1440 × 900 and 390 × 844, light and dark, plus 820 × 1000 for the sheet case. Screens in the PR description as a private gallery link. See the screenshot plan below.
- [ ] **Step 5: Commit** `docs(openspec): tick add-notification-center 1.1–1.3; toast and banner colour mapping`.

---

## Screenshot plan

Each screen before / after at 1440 and 390, light and dark (8 shots per row), unless noted. Stubs are Playwright `page.route` handlers; localStorage seeds go in `addInitScript`.

| Screen | Route | Trigger with stubbed data |
|---|---|---|
| Toast 完成 | `/portfolio/transactions` | list GET → two fictional rows; edit row 1 → 儲存 with PUT → 200 → 「紀錄已更新」 (before: PrimeNG top-right) |
| Toast 進行中 | `/portfolio/import` | choose `範例券商_202610.csv`; commit POST → `{parsed: 12, created: 10, skipped_duplicates: 2, errors: [], recalc_scheduled: true}`; status GET → `{state: 'running'}` → 「匯入完成：已解析 12 筆；…；資料重算執行中…」 (before: two stacked PrimeNG toasts) |
| Toast 失敗 + 重試 | `/portfolio/import` | after the commit above, status GET → 500 → 「無法取得重算狀態」 with 重試 |
| Toast 失敗 (accounting) | `/accounting/reminders?tab=pending` | a queue row action POST → 409 `{message: 'import_running'}` → 「匯入進行中，請稍後再試」 (before: bottom-centre pill) |
| Toast over the sheet (820 only, light / dark) | `/accounting/entries/:id` at 820 × 1000 | entry with a schedule; 刪除 → DELETE 409 `import_running`: toast above the backdrop, sheet still trapping Tab |
| Toast 完成 + 復原 | — | no PR A call site (ruling 8); covered by `toast-outlet.spec.ts` only |
| Banner 錯誤 import (a) | `/portfolio/import` | seed `hh-import-history` with `[{at: '2026-10-21T14:10:00Z', file: '範例券商_202610.csv', type: 'transactions', ok: false, created: 0, errors: 1}]` |
| Banner 錯誤 recalc (b) | `/portfolio/import` | status GET → `{state: 'failed', kind: 'import', steps: [{name: 'networth_backfill', status: 'failed', error: 'TWSE timeout'}]}` |
| Banner 錯誤 帳單讀取失敗 | `/accounting/reminders?tab=cards` | one credit account `測試銀行卡` with a due event; its statement GET → 500 |
| Banner 錯誤 failing schedule | `/accounting/reminders?tab=debts` | definitions GET → one active definition `測試月費` with `failing: {instance_id: 7, last_error: '帳戶已封存'}` |
| Banner 注意 expired rules | `/accounting/reward-rules` | one credit account with one enabled rule whose end date is 2026-09-30 |
| Banner 注意 overdraft | `/portfolio/accounts/:id` | account GET → `native_balance: -12345`, currency TWD |

## Verification

Checked against the worktree at `d5416118` while writing this plan:

- **PrimeNG toasts:** 64 `messageService.add` call sites in 10 files (account-detail 10, accounts-list 3, cash-flow-form 2, dashboard 1, dividend-list 11, import 14, item-list 14, realized-pnl 1, transaction-list 7, error-logging interceptor 1); 8 `<p-toast>` elements (item-list, account-detail, accounts-list, cash-flow-form inline, dividend-list, import, realized-pnl, transaction-list); `MessageService` provided by those 8 components plus root in `app.config.ts`. Only `primeng-toast` and `primeng-api` in `node_modules/primeng/fesm2022` reference `MessageService`, so removing the root provider breaks nothing else.
- **Accounting toast:** 22 call sites in 8 files (13 direct `show`, 9 through `scheduleActionError`), one outlet `<app-accounting-toast />` in `accounting-layout.html`; 8 spec files reference it, plus `accounting-toast.spec.ts`.
- **Undo strips:** none exist; inline undo buttons stay (`handled-list.html` 撤銷, `accounting-settings.html` 復原).
- **Banners:** 6 sites migrate (import ×2, reminders ×2, reward-rules, account-detail); not migrated per ruling 15: `lock-banner.ts`, timeline `.bill-banner`, `.missing-rates`, section `…讀取失敗` lines, `saved-flash`, rules-section `liveText`. No quote-delay alert exists anywhere in `frontend/src`.
- **Tokens confirmed in `styles.scss`:** `--app-accent`, `--app-surface`, `--app-bg`, `--app-text`, `--app-text-muted`, `--app-border`, `--app-primary`, `--app-primary-soft`, `--app-banner-warn`, `--app-banner-warn-soft`, `--c-red-soft`, `--c-red-pill-ink`, `--app-raised-shadow` in both theme blocks. To create: `--app-toast-action`, `--app-toast-ok`, `--app-toast-error`. Contrast computed: toast text 15.1 / 14.3; action 7.4 / 5.2; ok 8.2 / 4.5 (with `#177a55`); error 6.9 / 4.3; 錯誤 title with pill ink 4.6 / 6.3 over `--app-bg`; 注意 5.0 / 6.4; 資訊 text 13.8 / 14.1.
- **Layout:** `--sidebar-w` is set on `.hub-layout` (`shell.ts`, 248 / 72 px); FAB `bottom: 30px`, `right: var(--fab-right, 30px)`, 56 px, z 1000; tab bar `.m-tabbar` 64 px + safe area, z 1000; sheet backdrop 1001 / pane 1002 (`accounting-layout.scss`); `sheet.scss`, filter-bar and page-header popovers 1100; pickers 1200; the 760–1023 sheet inerts the list, page header and `app-sidebar` only (`setOutsideInert`); `trapFocus` in `accounting-ui.ts` has 12 callers (none changes).
- **Names confirmed:** `IMPORT_RUNNING_TOAST` (`schedule-math.ts`), `CATCH_UP_FAILED_TOAST` (`entry-form.ts`), `logoUploadErrorMessage`, `writeErrorMessage`, `failureBanner` / `recalcBanner` / `stamp` (`import-math.ts`), `retryRecalc`, `startPolling`, `fetchRecalcStatus`, `billsFailed`, `billErrorText`, `retryBills`, `retryDefinition`, `retryingDefinitions`, reward-rules `banner` / `setFilter`, account-detail `isOverdraft` / `openTopupQuickFix` / `accountReady` / `loadAccount` / `loadBalanceHistory`, accounts-list `fetchAccounts`, item-list `loadItems`, `BROKER_LABELS`, `ACCOUNT_SWITCHED_DETAIL`, `serverReason`, `hh-import-history`, reminders tab keys `all / todo / cards / debts / pending`.
- **Could not confirm:** PrimeNG's runtime modal z-index on this config (assumed 1100 + n from the library defaults; checked visually in the 820 evidence shot); whether jsdom fills `relatedTarget` on `focusout` — the outlet tests dispatch `FocusEvent`s with an explicit `relatedTarget` rather than relying on it.
