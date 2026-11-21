# Notification center PR A: one toast, one banner — Implementation Plan

_Rev 2 (2026-11-21): plan review AGENT-549 (CHANGES-REQUESTED) folded with the coordinator's rulings — whole-page read failures become banners, save failures get 重試 on network errors, toast controls join modal focus cycles, the toast's width keeps clear of the floating 記一筆 in both positions, toasts are owned by their page (`injectToasts()`), one pause state, `runAction` by captured id, recalc partial keeps 重試 for retryable steps._

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two toast systems (PrimeNG `<p-toast>` / `MessageService` on portfolio and inventory pages, `AccountingToastService` on accounting pages) with one shell-level toast, and the hand-rolled page alerts and whole-page read failures with one `<app-banner>` primitive. No new features: every message that exists today keeps its trigger; its look, place and lifetime change, and failed saves offer 重試 as the spec requires.

**Architecture:** SPA only (`frontend/`), no API change. `ToastService` (root) holds at most one toast. Components never call it directly: they get an owner-bound `ToastScope` from `injectToasts()`, so a toast's action and a 進行中 toast die with the page that raised them, and a late response from a destroyed page raises nothing. `ToastOutletComponent` is rendered once by the shell inside `.hub-layout`: bottom-left of the content area at ≥ 760 px (width limited so it never reaches the floating 記一筆), above the tab bar below 760 px. While a modal (the 760–1023 px sheet, a bottom sheet, the calendar drawer, a PrimeNG dialog) holds focus, the outlet adds the toast's buttons to that modal's Tab cycle. `BannerComponent` (`components/ui/banner`) renders an 錯誤 / 注意 / 資訊 alert with a heading, one line of detail and one action; only 資訊 with a `dismissId` can be closed, remembered in `localStorage['hh-banner-dismissed']`.

**Tech Stack:** Angular 21.2 standalone / signals / OnPush, vitest (`cd frontend && npx ng test --watch=false --include='<glob>'`, quote the glob), SCSS with the tokens of `frontend/src/styles.scss`.

**Spec:** `openspec/changes/add-notification-center/` — design D6 (toast) and D7 (banner), Migration Plan step 1, tasks §1, `specs/frontend-ui-primitives/spec.md` (Requirements "Toast service" and "Banner primitive"). Artboard `design_handoff_v3/design_refs/FeedbackSpec.dc.html` (sections "Toast · 全站一種" and "頁內橫幅"), colour rules in `design_handoff_v3/README.md`. `openspec/specs/` is not edited in this PR (the main specs change when the whole change is archived).

## Global Constraints

- Colours only through tokens declared in `frontend/src/styles.scss`; no hex, `rgb()` or `rgba()` literal in a component stylesheet or inline style. New tokens are added to `styles.scss` in both theme blocks (`:root` and `.app-dark-mode`).
- No new dependency. PrimeNG stays (dialogs, menus, confirm dialogs); only `primeng/toast` and `MessageService` leave.
- Chinese UI copy exactly as this plan's copy tables give it (taken from the code today, the spec or the artboard; where they differ the ruling says which).
- Tests use fictional names, files and amounts (`範例券商_202610.csv`, `測試銀行`, `$1,234`).
- The bell, the 提醒中心 count, `NavBadgeService` and the navigation badges / dots are not touched.
- `openspec/specs/**` is not edited. `openspec/changes/add-notification-center/tasks.md` ticks 1.1–1.3 in Task 9, with the 1.2 annotation of ruling 16.
- 復原 is not wired to any call site in this PR (ruling 8). Nobody may report the change's "Success with undo" scenario as done; it is verified in PR D.
- Commit per task, message in English, ending with the trailer `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`. No `--amend`, no force-push.
- Never read `~/.config`, `~/.hermes` or any `.env`; never run uvicorn or alembic.

## Rulings (planning + coordinator, 2026-11-21)

Each ruling names what it costs if it is wrong. "(C)" marks a coordinator ruling on a review item.

1. **Variant per message.** success → 完成; error → 失敗; a refusal or validation hint (PrimeNG `warn`, `import_running`, `info` refusals such as 需人工釐清…) → 失敗, because the action did not happen; "something is running" → 進行中. A replay that changed nothing (這次與上次相同，沒有重複執行) → 完成. *If wrong:* a few strings change variant in the copy tables.
2. **One text line.** A toast carries one `text`. PrimeNG's generic summaries (錯誤, 成功, 提醒, 失敗, and 已新增 / 已更新 where the detail already says it) are dropped; a meaningful summary is joined to its detail with a full-width colon through `toastText(summary, detail)`. *If wrong:* one helper and the strings in the tables.
3. **`title` input is named `heading`.** A static `title` attribute on a component host stays in the DOM and shows a native tooltip. *If wrong:* a rename.
4. **(C) One pause state.** A 完成 toast's 5 s timer is paused while the pointer is over it OR focus is inside it, and resumes with the remaining time only when both are gone. 進行中 and 失敗 have no timer. *If wrong:* one timer rule.
5. **(C) Toasts are owned by the page that raised them.** Components use `injectToasts()` (bound to their `DestroyRef`). When the owner is destroyed: its 進行中 toast is removed; its 完成 / 失敗 stays (an unread failure must not vanish) but loses its action button; a call made after the owner is destroyed raises nothing. There is no route-change rule. *Changed 2026-11-21 during execution, after the Task 3 review:* after the owner is destroyed `progress()` is a no-op, but `done()` / `error()` still show, with the action dropped, because the result of a write that lands after the user left must not vanish (see the doc comment on `ToastService.scope`). *If wrong:* the owner sees a stale 失敗 without 重試 on the next page (intended), or a late failure from a page already left is shown without 重試 (see the 2026-11-21 note).
6. **(C) Toast controls join the active modal's Tab cycle.** One capture-phase `keydown` handler in the outlet: when a toast with buttons is shown and focus is in a modal container (`[aria-modal="true"]`, `[role="dialog"]`, `[role="alertdialog"]`) or in the toast after leaving one, Tab / Shift+Tab at the edges move between the modal's focusables and the toast's buttons as one cycle `[modal…, toast…]`; Tab between two controls of the same group is left alone (the modal's own trap or the browser handles it). This covers `trapFocus` users (the 760–1023 px sheet, the calendar drawer, bottom sheets, pickers) and PrimeNG's `pFocusTrap` (dialogs, confirm dialogs) without changing either: the handler runs before both, marks the event handled (`isHandledKey` then skips `trapFocus`), and never lands on PrimeNG's hidden sentinels (`[data-p-hidden-focusable="true"]`). Esc with focus inside the toast closes the toast only (`preventDefault` + `stopPropagation`, so neither the accounting layout's `document:keydown.escape` nor PrimeNG's document Esc listener runs); Esc elsewhere keeps its meaning. When the toast closes with focus inside it, focus returns to the last element focused outside the outlet (the modal control). The toast never takes focus by itself. Stacking: outlet `z-index: 1150` — above the tab bar / FAB / sidebar (1000), the sheet backdrop / pane (1001 / 1002), bottom sheets and popovers (1100), PrimeNG modal masks (1100 + n); below pickers and menus (1200). *If wrong:* the handler is one function in the outlet; the alternative (a per-modal outlet) would touch every modal.
7. **(C) 重試 on failures.**
   - Reads with a no-argument reload get 重試 on any failure (re-reading is harmless).
   - Saves (create / update / delete / upload / trigger) that report through the toast get 重試 only for a network-level failure — `isNetworkFailure(err)`: an `HttpErrorResponse` with status 0 or ≥ 500, defined once in `components/accounting/http-errors.ts`. 重試 re-sends the same request with the inputs captured when it first ran (a `send…(captured)` method), never re-reading a form that may have changed or closed. A 4xx / validation failure shows 失敗 without 重試 (its text says what to fix).
   - Accounting saves that report inline (`writeErrorMessage` lines) stay inline; they do not move to the toast.
   *If wrong:* a 5xx that did commit could be sent twice — every listed write is idempotent or deduplicated server-side except `POST` creates, where a duplicate shows up as a second row the owner can delete.
8. **(C) 復原 stays out of PR A.** No toast today follows an action with an inverse endpoint (the inbox answers confirm inline; the 已處理 list keeps 撤銷; 記帳設定 › 發票 keeps its row 復原). The API (`done(text, { undo })`) and its tests ship now; the reconciliation answer → 復原 scenario is wired and verified in PR D. *If wrong:* one `done(…, { undo })` call in `inbox-panel.ts`.
9. **The error-logging interceptor stops toasting.** It injects the root `MessageService`, which no `<p-toast>` renders (every `<p-toast>` sits in a component that provides its own), so its toast has never been visible; showing it now would replace each page's own message. It keeps its retry and console logging. *If wrong:* no generic network toast (same as today).
10. **Dashboard 409 on refresh.** Manual 刷新行情 (`loadSummary`) shows 失敗 「另一筆重算進行中，稍候再試」 (full-width comma); the automatic refresh on page load stays silent (`console.warn`). *If wrong:* one call site.
11. **Import commit with a recalc.** One toast: a clean commit with `recalc_scheduled` shows one 進行中 「匯入完成：<counts>；資料重算執行中…」; a commit with row errors / unresolved names shows its 失敗 with the counts and no 進行中 (the page's recalc panel shows 執行中). The recalc result replaces whatever is shown; a settled status that is not a transition dismisses the page's 進行中 toast. *If wrong:* the counts move to a separate toast replaced within one poll.
12. **(C) Recalc partial.** A step is retryable when its status is `failed`, or `partial` without `detail.deferred_events`. Any retryable step → 失敗 with the failed-step list and 重試 (`retryRecalc()`), also when deferred events exist beside it (then the text adds 「；歷史股利權益已記錄，請至股利頁確認實際收款」). Only when every non-ok step is deferred-only: 失敗 「資料重算尚未完成：歷史股利權益已記錄。請至股利頁確認實際收款；重算不會自動入帳。」 without 重試. *If wrong:* one predicate.
13. **Toast colours.** Inverse surface `--app-accent` with `--app-surface` text. New `--app-toast-action` (light `#b3a8ff`, dark `#533afd`), `--app-toast-ok` (light `#5ad19c`, dark `#177a55` — the artboard's `#1f9d6b` is 2.9:1 on `#e6ebf5`), `--app-toast-error` (light `#ff8a8e`, dark `#c9373c`). Shadow `--app-raised-shadow`. *If wrong:* three token values.
14. **(C) Toast width keeps clear of the floating 記一筆.** Rule: limit the width to the free space left of the FAB (not lift). The shell passes `--toast-fab-clear` to the outlet: `calc(<fab right> + 56px + 12px)` while the FAB is shown (`<fab right>` = `PaneStateService.fabRight()` or `30px`), else `24px`. Outlet width `max(240px, min(440px, calc(100vw - var(--sidebar-w) - 24px - var(--toast-fab-clear))))`, left `calc(var(--sidebar-w) + 24px)`, bottom `30px` (FAB's bottom). The sidebar 記一筆 is in the sidebar column, not floating: no clash. Numbers (sidebar 248 px only at ≥ 1280 px with no pane, or with auto-collapse off; else the 72 px rail; 1rem = 16 px):

    | Viewport / state | Sidebar | FAB x | Toast x | Gap |
    |---|---|---|---|---|
    | 1440, no pane | 248 | 1354–1410 | 272–712 | 642 |
    | 1440, list pane (auto-collapse) | 72 | 617–673 | 96–536 | 81 |
    | 1440, calendar pane | 72 | 892–948 | 96–536 | 356 |
    | 1280, list pane, auto-collapse off | 248 | 640–696 | 272–628 (356 wide) | 12 |
    | 1100, list pane | 72 | 463–519 | 96–451 (355 wide) | 12 |
    | 1100, calendar pane | 72 | 665–721 | 96–536 | 129 |
    | 1024, list pane (narrowest panes case) | 72 | 428–484 | 96–416 (320 wide) | 12 |
    | 820, no sheet | 72 | 734–790 | 96–536 | 198 |
    | 820, sheet open (FAB hidden) | 72 | — | 96–536 | — |
    | 390 phone (FAB hidden; tab bar 64 + safe area) | — | — | 12–378, bottom 88 + safe area | — |

    *If wrong:* the toast at 1024–1100 with a list pane is narrower (≥ 320 px) and wraps to more lines; the alternative (lift above the FAB) is one `bottom` rule.
15. **Banner tones → tokens.** 錯誤: `--c-red-soft` / `--c-red-pill-ink` (the README's `--c-red-ink` is 4.0:1 on that tint over `--app-bg`; pill ink 4.6 / 6.3). 注意: `--app-banner-warn-soft` / `--app-banner-warn`. 資訊: `--app-primary-soft`, icon `--app-primary`, heading `--app-text`. Detail `--app-text`; action `--app-surface` on `--app-border`; ✕ `--app-text-muted`. Roles: 錯誤 `alert`, 注意 / 資訊 `status`. *If wrong:* token swaps in `banner.scss`.
16. **(C) No quote-delay banner.** No quote-delay alert exists (`dashboard.html` has none; nothing reads quote freshness); building one needs a staleness rule the spec does not define. tasks.md 1.2 is ticked with the annotation 「(quote delay: no such alert exists; needs a rule — deferred)」. *If wrong:* the owner gives the rule and it lands with PR C's 國泰 run-stale banner.
17. **(C) Which alerts migrate.** Page-level alerts and whole-page read failures become banners (Task 7 and Task 8, 14 sites); everything else stays — see **Not migrated** below. *If wrong:* drop rows from Task 7 / 8.
18. **Banner copy.** Existing copy is kept as the heading; detail and action as listed in Tasks 7–8. Import failure (a) takes the artboard's detail shape `<M/D HH:mm> · <file> · <reason>，沒有寫入任何資料。` (the old 「修正後重新上傳即可。」 is dropped for the one-line rule) and no action (no reason page exists for 查看原因). Failing schedule heading 「這一期入帳失敗」 (the phrase `CATCH_UP_FAILED_TOAST` already uses). *If wrong:* strings only.

### Not migrated (and why)

| Element | File | Reason |
|---|---|---|
| Section read failures: `account-entries.html:82` (list under a loaded account header), `timeline.html:19/47/68/232/277` (summary / list sections), `month-overview.html:19/45/95/117`, `calendar-day-panel.html:33`, reports tabs `plan-tab.html:11/71`, `rank-tab.html:8/46`, `trend-tab.html:12`, `custom-report.html:227` (preview), `accounting-settings.html:165/177/190` (發票 sections), `handled-list.html:2`, `reminders.html:24/113/151/187/195/223` (tab sections), `statement-detail.html:53/133` (帳本 sections), `einvoice-card.html:41`, `reward-reprice-card.html:9`, `einvoice-target-sheet.html:17`, `dividend-list.html:50/111` | — | Scoped to one section of a page that loaded; the page-level banner rule does not apply |
| `schedule-sheet.html:135` | reminders | Inside a bottom sheet, not a page |
| Portfolio whole-page read failures shown as toasts (`account-detail` 帳戶讀取失敗, `accounts-list` 帳戶讀取失敗, `item-list` 無法載入物品清單) | portfolio, inventory | No page block exists today; they stay 失敗 toasts with 重試 (ruling 7). Turning them into page blocks changes the page — follow-up if wanted |
| Form-field and form-level errors (`.form-error`, `fieldErrors`, `.sheet .load-error` refusal lines, `entry-form.html:43`) | accounting | Tied to the field or form the owner is editing |
| `app-lock-banner` | `lock-banner/lock-banner.ts` | Record-state note inside a form, never dismissible, not an alert |
| `.saved-flash` 「已儲存，繼續記下一筆」 | `entry-form.html:35` | In-form confirmation next to the fields being typed into |
| Rules section `liveText` | `rules-section.html:1` | Screen-reader announcement, not visible feedback |
| Timeline 「近 7 天有 N 筆繳費到期」 | `timeline.html:186` | Navigation button; replaced by 通知 in PR B |
| 部分外幣未換算 | `timeline.html:189` | Inline data note |
| Inline 撤銷 / 復原 buttons | `handled-list.html:9`, `accounting-settings.html:202` | Existing undo controls on their rows; no undo strip exists |

## Conflicts between the change spec and shipped behaviour

| Shipped requirement / behaviour | Conflict | Ruling |
|---|---|---|
| `stock-portfolio-import-orchestration` "partial → orange warning toast listing the failed step(s) and a 重試 button that calls `POST /api/portfolio/imports/recalc`" | No orange variant | **Superseded by the change's toast rule (one toast, three variants):** 失敗 with the failed-step list and 重試 whenever a retryable step exists (ruling 12) |
| same, "toast immediately after commit (Recalculation running…)" + `frontend-csv-import` "success toast displays the count" | One toast at a time would let the second replace the first | **Superseded by the change's toast rule:** one 進行中 toast carrying both (ruling 11) |
| `frontend-accounting-ledger` "toast `匯入進行中，請稍後再試`" (3 s today) | 失敗 stays until closed | Stays until closed; copy unchanged; the spec sets no duration |
| `frontend-portfolio-accounts` "toast with severity `error` and message including `刪除失敗`" | Severity names go away | 失敗 toast whose text starts 「刪除失敗：」 |
| `frontend-csv-import` "Last failed import banner … state that the whole batch was not written" | Artboard sentence differs | Artboard 「沒有寫入任何資料。」 (same meaning) |
| `frontend-accounting-ledger` "Expired callout … warning banner" (`--app-state-warning-bg` today) | Look changes | 注意 tone (amber) |
| Inbox 已處理 list labels its undo 撤銷; the change's toast says 復原 | Two words for undo | Both stay in PR A; unify in PR D |
| Change scenario "Success with undo" | Needs a new reconciliation toast | Not done in PR A (ruling 8); verified in PR D |

## Review Focus

- One toast at a time across sources: a page 失敗 followed by another page's 完成 (newest wins; the older 完成 timer never clears the newer toast); a `runAction` callback that raises a new toast keeps it.
- Ownership: leave `/portfolio/import` right after 「無法取得重算狀態」 — the toast stays without 重試; a status or recalc POST answering after leaving raises nothing.
- Keyboard in modals: at 820 px with the sheet open and a 失敗 toast, Tab / Shift+Tab cycle pane ↔ toast, Enter on 重試 runs it, Esc in the toast closes only the toast, Esc in the pane closes the sheet; the same in a PrimeNG edit dialog (transaction-list) after a 500 save.
- Width vs the FAB at 1100 with a list pane and with the calendar (12 px gap); the sheet case where the FAB is hidden.
- Save retry: a 500 save shows 重試 that re-sends the captured payload (also after the dialog was closed); a 422 shows no 重試.
- `grep -rn "p-toast\|primeng/toast\|MessageService\|AccountingToast\|app-accounting-toast" frontend/src` is empty after Task 5.
- Banner dismissal: only 資訊 with a `dismissId` shows ✕; storage that throws still lets the owner close it for the session.

---

### Task 1: `ToastService`, `injectToasts()` and the shell outlet

D6, tasks 1.1 (service + outlet) and 1.3 (one at a time; 失敗 stays).

**Files:**
- Create: `frontend/src/app/services/toast.service.ts`, `frontend/src/app/services/toast.service.spec.ts`
- Create: `frontend/src/app/components/shell/toast-outlet/toast-outlet.ts`, `toast-outlet.scss`, `toast-outlet.spec.ts`
- Modify: `frontend/src/app/components/shell/shell.ts` (import; `toastFabClear` computed), `shell.html` (one `<app-toast-outlet [style.--toast-fab-clear]="toastFabClear()" />` after the `@if (showFab())` block, before `<app-mobile-nav />`), `shell.spec.ts`
- Modify: `frontend/src/styles.scss` (three tokens in `:root` and in `.app-dark-mode`), `frontend/src/app/theme/contrast.spec.ts`

**Interfaces (produced):**

```ts
// services/toast.service.ts
export type ToastKind = 'done' | 'progress' | 'error';
export interface ToastAction { readonly label: '復原' | '重試'; readonly run: () => void; }
export interface Toast { readonly id: number; readonly kind: ToastKind; readonly text: string; readonly action: ToastAction | null; }
export type ToastPause = 'hover' | 'focus';
export const TOAST_DONE_MS = 5000;
/** `summary` alone when `detail` is empty, else `${summary}：${detail}`. */
export function toastText(summary: string, detail?: string | null): string;

/** What a component holds: every toast it raises is owned by its DestroyRef. */
export interface ToastScope {
  done(text: string, options?: { readonly undo?: () => void }): number;    // 5 s; 復原 when undo is given
  progress(text: string): number;                                          // no timer
  error(text: string, options?: { readonly retry?: () => void }): number;  // no timer; 重試 when retry is given
  /** Dismisses `id` only if it is still the current toast. */
  dismiss(id: number): void;
}
/** `inject(ToastService).scope(inject(DestroyRef))`; call in an injection context. */
export function injectToasts(): ToastScope;

@Injectable({ providedIn: 'root' })
export class ToastService {
  readonly current: Signal<Toast | null>;
  /** A scope whose calls return -1 and raise nothing once `owner.destroyed`. On owner destroy: its progress toast is
   *  dismissed; its done / error toast keeps its text and loses its action. Replacing a toast unregisters the hook. */
  scope(owner: DestroyRef): ToastScope;
  /** Dismisses `id` only if it is still current (the outlet's ✕ / Esc). */
  dismiss(id: number): void;
  /** If `id` is current and has an action: dismiss(id) first, then run the captured callback once. Never touches a
   *  toast the callback raises. */
  runAction(id: number): void;
  pause(reason: ToastPause): void;   // timer runs only while no reason is held
  resume(reason: ToastPause): void;
}
```

`ToastOutletComponent` — selector `app-toast-outlet`, host attribute `data-toast-outlet`, OnPush, no inputs. Two steady live regions that never leave the DOM: `div.toast-region[role="status"][aria-live="polite"][aria-atomic="true"]` (完成 / 進行中) and `div.toast-region[role="alert"][aria-atomic="true"]` (失敗). The toast: `div.toast.toast--done|progress|error[data-toast-id]` > `i.toast-icon` (`pi pi-check` / `pi pi-spinner pi-spin` / `pi pi-exclamation-circle`, `aria-hidden`), `span.toast-text`, optional `button.toast-action`, and for 失敗 `button.toast-close[aria-label="關閉"]` (`pi pi-times`). Host listeners: `pointerenter` → `pause('hover')`, `pointerleave` → `resume('hover')`, `focusin` → `pause('focus')`, `focusout` whose `relatedTarget` is outside the host → `resume('focus')`. A `document:focusin` listener remembers the last focused element outside the host (`lastOutside`); when the toast closes (✕, action, Esc) with focus inside, focus goes to `lastOutside` if still connected. Keyboard handling inside modals is Task 2.

Shell: `protected readonly toastFabClear = computed(() => this.showFab() ? \`calc(${this.paneState.fabRight() ?? '30px'} + 68px)\` : '24px')`.

Styles (`toast-outlet.scss`): host `position: fixed; z-index: 1150; left: calc(var(--sidebar-w, 0px) + 24px); bottom: 30px; width: max(240px, min(440px, calc(100vw - var(--sidebar-w, 0px) - 24px - var(--toast-fab-clear, 98px)))); pointer-events: none`; `@media (max-width: 759.98px) { left: 12px; right: 12px; width: auto; bottom: calc(64px + env(safe-area-inset-bottom, 0px) + 12px) }`. `.toast { pointer-events: auto; display: flex; align-items: center; gap: 10px; min-height: 48px; padding: 0 8px 0 14px; border-radius: 14px; background: var(--app-accent); color: var(--app-surface); box-shadow: var(--app-raised-shadow) }`; text 14 px / 600, wraps; action 36 px high, 14 px / 800, `color: var(--app-toast-action)`; close 36 × 36; icons `--app-toast-ok` / `--app-toast-action` / `--app-toast-error`; focus ring `0 0 0 3px var(--app-toast-action)`; `prefers-reduced-motion: reduce` drops the spin.

- [ ] **Step 1: Failing tests** — `toast.service.spec.ts` (fake timers; owners from `TestBed.runInInjectionContext` over a child `EnvironmentInjector` / `createEnvironmentInjector([], parent)` whose `destroy()` destroys its `DestroyRef`):
  ```ts
  it('shows one toast at a time: a newer call replaces the current one', () => { t.done('已確認'); t.error('儲存失敗，網路中斷'); expect(s.current()).toEqual(expect.objectContaining({ kind: 'error', text: '儲存失敗，網路中斷' })); });
  it('完成 disappears after 5 s', () => { t.done('已確認'); vi.advanceTimersByTime(4999); expect(s.current()).not.toBeNull(); vi.advanceTimersByTime(1); expect(s.current()).toBeNull(); });
  it('失敗 stays until it is dismissed', () => { const id = t.error('儲存失敗'); vi.advanceTimersByTime(60_000); expect(s.current()?.id).toBe(id); s.dismiss(id); expect(s.current()).toBeNull(); });
  it('進行中 stays until its result replaces it', ...);
  it('a replaced 完成 timer never clears the newer toast', () => { t.done('A'); vi.advanceTimersByTime(3000); t.error('B'); vi.advanceTimersByTime(5000); expect(s.current()?.text).toBe('B'); });
  it('dismiss(id) of an older toast leaves the current one', ...);
  it('labels: undo → 復原 on 完成, retry → 重試 on 失敗, none otherwise', ...);
  it('runAction runs the callback once and dismisses by its own id', () => { const retry = vi.fn(); const id = t.error('帳戶讀取失敗', { retry }); s.runAction(id); s.runAction(id); expect(retry).toHaveBeenCalledTimes(1); expect(s.current()).toBeNull(); });
  it('runAction keeps a toast the callback raises synchronously', () => { const id = t.error('匯入失敗', { retry: () => t.progress('資料重算執行中…') }); s.runAction(id); expect(s.current()).toEqual(expect.objectContaining({ kind: 'progress', text: '資料重算執行中…' })); });
  it('pause holds while hover OR focus is held; resume only when both are gone', () => {
    t.done('A'); vi.advanceTimersByTime(3000);
    s.pause('hover'); s.pause('focus'); s.resume('hover'); vi.advanceTimersByTime(10_000); expect(s.current()).not.toBeNull();
    s.resume('focus'); vi.advanceTimersByTime(1999); expect(s.current()).not.toBeNull(); vi.advanceTimersByTime(1); expect(s.current()).toBeNull();
  });
  it('the reverse order (focus then hover released) also resumes once', ...);
  it('a resume without a matching pause changes nothing', ...);
  it('an owned 失敗 keeps its text but loses 重試 when the owner is destroyed', () => { t.error('無法取得重算狀態', { retry: vi.fn() }); owner.destroy(); expect(s.current()).toEqual(expect.objectContaining({ text: '無法取得重算狀態', action: null })); });
  it('an owned 進行中 is removed when the owner is destroyed', ...);
  it('a call from a destroyed owner raises nothing and returns -1', () => { owner.destroy(); expect(t.error('x')).toBe(-1); expect(s.current()).toBeNull(); });
  it('a toast replaced before its owner dies is untouched by that destroy', () => { t.error('A', { retry: vi.fn() }); other.error('B', { retry: vi.fn() }); owner.destroy(); expect(s.current()?.action?.label).toBe('重試'); });
  it('toastText joins with a full-width colon and drops an empty detail', ...);
  ```
  `toast-outlet.spec.ts`:
  - `keeps both live regions in the DOM with nothing to show`;
  - `完成 renders in the status region with 復原 and no close`; `進行中 with neither action nor close`; `失敗 in the alert region with 重試 and 關閉`;
  - `復原 / 重試 run their callback once and remove the toast`; `關閉 removes a 失敗 toast`;
  - `pointer and focus hold the timer together` — pointerenter, focusin, pointerleave → still paused after 10 s; focusout to `#outside` → resumes;
  - `a new toast never moves focus`;
  - `closing from inside returns focus to the last element focused outside`;
  - `scss: fixed, z-index 1150, width limited by --toast-fab-clear, above the tab bar below 760 px, no colour literal` — read the file (`readFileSync(join(process.cwd(), 'src/app/components/shell/toast-outlet/toast-outlet.scss'))`) and assert `z-index: 1150`, `var(--toast-fab-clear`, `max(240px, min(440px`, `bottom: calc(64px + env(safe-area-inset-bottom`, and no `#hex` / `rgb(`.
  `shell.spec.ts`:
  - `renders one toast outlet inside .hub-layout at every width` (1440, 820, 390);
  - `--toast-fab-clear follows the floating 記一筆` — no pane: `calc(30px + 68px)`; `paneState.fabRight.set(FAB_RIGHT_BESIDE_PANE)` → `calc(${FAB_RIGHT_BESIDE_PANE} + 68px)`; `FAB_RIGHT_BESIDE_CALENDAR_PANE` likewise; FAB hidden (phone, `sheetOpen`, `formOpen`, `/accounting/reports`) → `24px` (read `outlet.style.getPropertyValue('--toast-fab-clear')`).
  `contrast.spec.ts`: `describe('toast inks on the inverse surface')` — both themes: `--app-surface` and `--app-toast-action` on `--app-accent` ≥ 4.5; `--app-toast-ok` and `--app-toast-error` ≥ 3.
- [ ] **Step 2: Run** `cd frontend && npx ng test --watch=false --include='src/app/services/toast.service.spec.ts'`, `--include='src/app/components/shell/**/*.spec.ts'`, `--include='src/app/theme/contrast.spec.ts'` — Expected: the new cases FAIL.
- [ ] **Step 3: Implement.** Tokens: `:root` `--app-toast-action: #b3a8ff; --app-toast-ok: #5ad19c; --app-toast-error: #ff8a8e;`, `.app-dark-mode` `--app-toast-action: #533afd; --app-toast-ok: #177a55; --app-toast-error: #c9373c;`, with a comment "ink on the inverse toast surface (`--app-accent`)". Owner hook via `owner.onDestroy(...)`, whose returned unregister function is called when the toast is replaced or dismissed; `owner.destroyed` is checked before raising.
- [ ] **Step 4: Run** the three commands — Expected: PASS.
- [ ] **Step 5: Commit** `feat(ui): one shell-level toast with 完成 / 進行中 / 失敗, owned by its page`.

### Task 2: Toast controls reachable inside modals

Ruling 6. Kept apart from Task 1 because it has its own integration tests.

**Files:**
- Modify: `frontend/src/app/components/shell/toast-outlet/toast-outlet.ts`
- Create: `frontend/src/app/components/shell/toast-outlet/toast-focus.ts`, `toast-focus.spec.ts`
- Modify: `frontend/src/app/components/accounting/accounting-layout/accounting-layout.spec.ts`
- Modify (only if the PrimeNG dialog renders under jsdom): `frontend/src/app/components/portfolio/transaction-list/transaction-list.spec.ts`

**Interfaces (produced):**

```ts
// shell/toast-outlet/toast-focus.ts
export const MODAL_SELECTOR = '[aria-modal="true"], [role="dialog"], [role="alertdialog"]';
/** focusables() of accounting-ui.ts minus PrimeNG's hidden sentinels ([data-p-hidden-focusable="true"]). */
export function cycleFocusables(container: HTMLElement): HTMLElement[];
/**
 * The element Tab / Shift+Tab should move to when the cycle [modal…, toast…] crosses a group edge or wraps;
 * null when the move stays inside one group (left to the modal's trap or the browser) or no modal is involved.
 */
export function toastCycleTarget(input: {
  active: HTMLElement; modal: HTMLElement | null; toast: HTMLElement | null; backwards: boolean;
}): HTMLElement | null;
```
Outlet: in its constructor `document.addEventListener('keydown', handler, true)` (removed on destroy). Tab handler: skip if `isHandledKey(event)` or no `.toast` with a button; `modal` = `active.closest(MODAL_SELECTOR)` when focus is outside the toast, else `lastOutside?.closest(MODAL_SELECTOR)` if connected; `target = toastCycleTarget(...)`; when non-null → `preventDefault()`, `target.focus()`. Esc handler on the toast element: `preventDefault()`, `stopPropagation()`, `dismiss(id)`, focus `lastOutside`.

PrimeNG: yes, a toast can sit over a PrimeNG dialog — the portfolio edit dialogs (transaction-list, dividend-list, account-detail, accounts-list, cash-flow-form, item-list) stay open after a failed save and raise a 失敗 toast with 重試 (Task 5). PrimeNG's `pFocusTrap` moves focus only when a hidden sentinel receives it; the capture handler moves focus before the browser reaches a sentinel, and `cycleFocusables` never returns one. PrimeNG's Esc listener is on `document` (bubble), so `stopPropagation` on the toast keeps the dialog open. Confirm dialogs (`p-confirmDialog`) close before their request answers; a toast already shown while one is open joins its cycle the same way.

- [ ] **Step 1: Failing tests** — `toast-focus.spec.ts` (host: a `<section role="dialog" aria-modal="true">` with sentinel `span[tabindex="0"][data-p-hidden-focusable="true"]`, buttons `#d1`, `#d2`, sentinel; its own `(keydown)` calling the real `trapFocus`; plus `<app-toast-outlet>`; a 失敗 toast with 重試):
  - `Tab on the dialog's last control moves to 重試` (event `defaultPrevented`, `activeElement` = `.toast-action`);
  - `Tab from 重試 moves to 關閉, left to the browser` (`toastCycleTarget` null, not prevented);
  - `Tab from 關閉 wraps to the dialog's first control`;
  - `Shift+Tab on the dialog's first control moves to 關閉`; `Shift+Tab from 重試 moves to the dialog's last control`;
  - `Tab between two dialog controls is left to the dialog's trap` (not prevented by the outlet);
  - `no sentinel is ever focused`;
  - `with no toast the dialog's cycle is unchanged` (`trapFocus` wraps `#d2` → `#d1`);
  - `without a modal the outlet leaves Tab alone`;
  - `Enter on 重試 runs it once and focus returns to #d2` (button `click` from Enter);
  - `關閉 returns focus to #d2`;
  - `Esc inside the toast closes the toast only` — a `document` keydown spy and a dialog Esc spy are not called, the dialog stays;
  - `Esc in the dialog keeps its meaning` — the toast stays, the dialog's Esc handler runs.
  `accounting-layout.spec.ts` (sheet mode, 820 px, entry detail open, a 失敗 toast raised): `Tab from the pane's last control reaches the toast and wraps back into the pane`; `Esc in the toast closes the toast and the sheet stays open`; `Esc in the pane closes the sheet`; `the outlet is not inert while the sheet inerts the list, header and sidebar`.
  `transaction-list.spec.ts` (if `p-dialog` renders in jsdom): `a 500 save inside the edit dialog: Tab from the dialog reaches 重試`. If it does not render, say so in the task report; the browser probe in Task 9 covers it.
- [ ] **Step 2: Run** `--include='src/app/components/shell/toast-outlet/*.spec.ts'` and `--include='src/app/components/accounting/accounting-layout/*.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement.** No change to `trapFocus` or any modal.
- [ ] **Step 4: Run** — Expected: PASS; also `--include='src/app/components/accounting/**/*sheet*.spec.ts'` and `--include='src/app/components/ui/picker/*.spec.ts'` unchanged and PASS.
- [ ] **Step 5: Commit** `feat(ui): toast buttons join the open modal's tab cycle; Esc in the toast closes only the toast`.

### Task 3: Accounting pages move off `AccountingToastService`

**Files:**
- Delete: `frontend/src/app/components/accounting/accounting-toast.ts`, `accounting-toast.spec.ts`
- Modify: `frontend/src/app/components/accounting/http-errors.ts`, `http-errors.spec.ts`
- Modify (call sites, under `frontend/src/app/components/accounting/`): `account-entries/account-entries.ts`, `account-settings/account-settings.ts`, `entry-form/entry-form.ts`, `statements/statement-detail/statement-detail.ts`, `reminders/reminders.ts`, `reminders/schedule-sheet/schedule-sheet.ts`, `entry-detail/entry-detail.ts`, `split-group/split-group.ts`
- Modify: `accounting-layout/accounting-layout.ts` (drop `AccountingToastComponent`), `accounting-layout.html` (drop `<app-accounting-toast />`, line 172)
- Modify (specs): `account-entries/account-entries.spec.ts`, `account-settings/account-settings.spec.ts`, `entry-form/entry-form-schedule.spec.ts`, `statements/statement-detail/statement-detail.spec.ts`, `reminders/reminders.spec.ts`, `reminders/schedule-sheet/schedule-sheet.spec.ts`, `entry-detail/entry-detail-schedule.spec.ts`, `accounting-layout/accounting-layout.spec.ts`

**Interfaces (produced, `http-errors.ts`):**
- `isNetworkFailure(err: unknown): boolean` — `err instanceof HttpErrorResponse && (err.status === 0 || err.status >= 500)`.
- `retryOnNetwork(err: unknown, run: () => void): (() => void) | undefined` — `run` when `isNetworkFailure(err)`, else `undefined`.
- `scheduleActionError(err: unknown, toasts: ToastScope): string | null` — `import_running` → `toasts.error(IMPORT_RUNNING_TOAST)`, `null`; else `writeErrorMessage(err)`.
- Each component: `private readonly toasts = injectToasts()`.
- `account-entries.ts`: `onLogoFile(event)` keeps the size check and calls a new `private sendLogo(accountId: number, file: File): void` (the upload + error handling), so 重試 can re-send.

Copy table (22 call sites; text unchanged):

| File:line today | New call |
|---|---|
| `account-entries.ts:132`, `account-settings.ts:960` | `error('圖片太大（上限 5 MB）')` |
| `account-entries.ts:146` | `error(logoUploadErrorMessage(err), { retry: retryOnNetwork(err, () => this.sendLogo(id, file)) })` |
| `account-settings.ts:973` | `error(logoUploadErrorMessage(err), { retry: retryOnNetwork(err, () => this.uploadLogoFile(file)) })` |
| `account-settings.ts:999` | `error(logoUploadErrorMessage(err), { retry: retryOnNetwork(err, () => this.removeLogo()) })` |
| `entry-form.ts:2300`, `:2328` | `error(IMPORT_RUNNING_TOAST)` |
| `entry-form.ts:2321` | `error(CATCH_UP_FAILED_TOAST)` |
| `statement-detail.ts:200`, `:294` | `done('這次與上次相同，沒有重複執行')` |
| `statement-detail.ts:202` | `error('有待確認的變更，這次沒有重跑對帳')` / `error(\`這次略過（${skipped}）\`)` |
| `statement-detail.ts:208` | `done('已重新對帳')` |
| `statement-detail.ts:296` | `done(forced ? '已強制確認' : '已確認')` |
| `reminders.ts:693`, `:798`; `schedule-sheet.ts:232`, `:236`, `:453`, `:457`; `entry-detail.ts:643`, `:832`; `split-group.ts:246` | `scheduleActionError(err, this.toasts)` (import from `../http-errors` / `../../http-errors`) |

- [ ] **Step 1: Failing tests.**
  - `http-errors.spec.ts`: `isNetworkFailure: status 0 and 5xx yes, 4xx and non-HTTP errors no`; `retryOnNetwork returns run only for a network failure`; `scheduleActionError turns import_running into a 失敗 toast and anything else into an error line`.
  - Rewrite each existing toast assertion to read `TestBed.inject(ToastService).current()`: `entry-detail-schedule.spec.ts:317`, `reminders.spec.ts:937`, `schedule-sheet.spec.ts:222`, `:631`, `entry-form-schedule.spec.ts:220`, `:240` (null), `:263`; `statement-detail.spec.ts:497–536` (kinds per the table); `account-settings.spec.ts:2119`, `:2146`; `account-entries.spec.ts:530`, `:589`.
  - New: `account-settings.spec.ts` `a 500 logo upload offers 重試, which uploads the same file again`; `a 422 logo upload offers no 重試`. `account-entries.spec.ts` the same pair for `sendLogo`.
  - `accounting-layout.spec.ts`: `SCREEN` (line 78) becomes `':scope > router-outlet + *'`; new `renders no toast of its own`.
  - `entry-form-schedule.spec.ts`: new `the catch-up 失敗 survives the form closing` (`current()?.text` = `排程已建立；這一期入帳失敗，請到待完成交易處理` after `finish()`; the toast has no action, so ownership changes nothing visible).
- [ ] **Step 2: Run** `--include='src/app/components/accounting/**/*.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement**; `grep -rn "AccountingToast\|accounting-toast" frontend/src` is empty.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `refactor(accounting): toasts go through the shell toast; logo uploads retry on network failures`.

### Task 4: Import page toasts and lifetime

Rulings 5, 7, 11, 12.

**Files:**
- Modify: `frontend/src/app/components/portfolio/import/import.ts`, `import.html` (drop `<p-toast></p-toast>`, line 1), `import.spec.ts`

**Interfaces:**
- `PortfolioImportComponent`: drop `ToastModule` / `MessageService` (keep `ConfirmationService`); `private readonly toasts = injectToasts()`; `private readonly destroyRef = inject(DestroyRef)`; `private recalcToastId: number | null = null`.
- Reads stop with the page: `verifyOverrideSymbol` (`:365`) and `getRecalcStatus` (`:660`) get `takeUntilDestroyed(this.destroyRef)`, so the status chain cannot answer after `ngOnDestroy`.
- Writes keep running (the commit must still record its attempt in `hh-import-history`): `uploadBrokerCsv` / upload (`:519`, `:534`) and `triggerRecalc` (`:479`). Their toasts are no-ops after destroy (owner rule), and `startPolling()` / `scheduleNextPoll()` already return when `destroyed` (`:631`, `:637`), so a late `retryRecalc()` response raises no 進行中 and starts no poll.
- `onUploadError(err: unknown, dryRun: boolean)` (gains `dryRun`, both callers pass it) so 重試 can call `this.upload(dryRun)`.
- `retryableSteps(status: RecalcStatus): RecalcStep[]` (in `import-math.ts`, exported, tested) — steps with status `failed`, or `partial` without `detail?.deferred_events?.length`.

Copy table (14 call sites):

| Line today | New |
|---|---|
| 354 | `error('請先填入代號')` |
| 359 | `error('無樣本日期可驗證')` |
| 383 | `error(toastText('驗證失敗', detail))` |
| 481 | `this.recalcToastId = progress('資料重算執行中…')` |
| 488 | `error(toastText('重算觸發失敗', detail), { retry: retryOnNetwork(err, () => this.retryRecalc()) })` |
| 498 | `error('請先選擇 CSV')` |
| 563 (券商) | success → `done(…)`, warn → `error(…)`; text `toastText(dryRun ? '預覽完成' : '匯入完成', brokerCountSummary(result))` |
| 571 | `error(toastText('匯入失敗', importErrorDetail(err)), { retry: retryOnNetwork(err, () => this.upload(dryRun)) })` |
| 619 + 622 | dry run or no recalc: success → `done(toastText(summary, detail))`, warn → `error(…)`. Commit with `recalc_scheduled`: success → `this.recalcToastId = progress(\`${toastText('匯入完成', detail)}；資料重算執行中…\`)`; warn → `error(toastText('匯入完成', detail))`. Polling starts in both. |
| 676 | `done('資料重算完成')` |
| 684 (partial) | retryable steps → `error(\`資料重算尚未完成（失敗步驟：${labels}）\` + (deferred ? '；歷史股利權益已記錄，請至股利頁確認實際收款' : ''), { retry: () => this.retryRecalc() })`, `labels` = the retryable steps' `RECALC_STEP_LABEL`s joined with `, `; none → `error('資料重算尚未完成：歷史股利權益已記錄。請至股利頁確認實際收款；重算不會自動入帳。')` |
| 694 | `error(toastText('資料重算失敗', firstError))` |
| 714 | `error('無法取得重算狀態', { retry: () => this.startPolling() })` |

Every settled status that is not a transition calls `if (this.recalcToastId !== null) this.toasts.dismiss(this.recalcToastId)`; every settle sets `recalcToastId = null`.

- [ ] **Step 1: Failing tests** (`import.spec.ts`, assertions on `TestBed.inject(ToastService).current()`):
  - rewrite `a partial recalc toast lists the failed steps by label` (`:267`) → `{ kind: 'error', text: '資料重算尚未完成（失敗步驟：淨值回補）', action: { label: '重試' } }`; `runAction` posts one `/api/portfolio/imports/recalc`;
  - new `partial with a failed step and deferred events keeps the step list and 重試` → text `資料重算尚未完成（失敗步驟：淨值回補）；歷史股利權益已記錄，請至股利頁確認實際收款`;
  - new `partial with only deferred events: 失敗 without 重試`;
  - rewrite `a failed 重算資料 toast reads the shared body message` (`:286`) → text `重算觸發失敗：recalc in progress`, no action (409);
  - new `a 503 recalc trigger offers 重試`;
  - rewrite `:643`, `:804`, `:814` to read `current()?.text`;
  - new `a clean commit with a recalc shows one 進行中 toast with the counts`; `a commit with row errors shows 失敗 with the counts and no 進行中`; `running → completed replaces the 進行中 toast with 資料重算完成`; `a first poll that is already settled dismisses the page's 進行中 toast`;
  - new `a 500 commit offers 重試, which uploads the same file again`; `a 400 commit offers no 重試`;
  - new `a status read failure shows 無法取得重算狀態 with 重試, which reads the status again`;
  - new (ownership) `leaving the page after 無法取得重算狀態 keeps the toast without 重試`; `leaving while 進行中 removes it`; `a status response arriving after destroy shows nothing` (destroy, then flush the pending GET → `current()` unchanged, no new GET); `a recalc POST answering after destroy raises no 進行中 and starts no poll`;
  - new `renders no p-toast`.
  `import-math.spec.ts`: `retryableSteps keeps failed steps and partial steps without deferred events`.
- [ ] **Step 2: Run** `--include='src/app/components/portfolio/import/*.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement** per the table.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `refactor(import): import and recalc feedback through the shell toast, owned by the page`.

### Task 5: Other portfolio pages, inventory, the interceptor; PrimeNG toast removed

**Files:**
- Modify: `frontend/src/app/components/portfolio/accounts/account-detail.ts`, `account-detail.html` (line 1), `account-detail.component.spec.ts`
- Modify: `frontend/src/app/components/portfolio/accounts/accounts-list.ts`, `accounts-list.html` (line 1), `accounts-list.component.spec.ts`
- Modify: `frontend/src/app/components/portfolio/accounts/accounts-page.scss` (comment line 29: drop "toast, ")
- Modify: `frontend/src/app/components/portfolio/cash-flow-form/cash-flow-form.ts` (inline `<p-toast>` line 53)
- Modify: `frontend/src/app/components/portfolio/dashboard/dashboard.ts`, `dashboard.spec.ts`
- Modify: `frontend/src/app/components/portfolio/dividend-list/dividend-list.ts`, `dividend-list.html` (line 1), `dividend-list.spec.ts`
- Modify: `frontend/src/app/components/portfolio/realized-pnl/realized-pnl.ts`, `realized-pnl.html` (line 1), `realized-pnl.component.spec.ts`
- Modify: `frontend/src/app/components/portfolio/transaction-list/transaction-list.ts`, `transaction-list.html` (line 1), `transaction-list.spec.ts`
- Modify: `frontend/src/app/components/item-list/item-list.ts`, `item-list.html` (line 1), `item-list.spec.ts`
- Modify: `frontend/src/app/interceptors/error-logging.interceptor.ts`, `error-logging.interceptor.spec.ts`
- Modify: `frontend/src/app/app.config.ts` (drop `MessageService`)

**Interfaces:** each component drops `ToastModule` / `MessageService` (keeps `ConfirmationService`) and holds `private readonly toasts = injectToasts()`. Each save listed below splits into the public handler (validation, dialog / confirm) and a `private send…(captured)` that sends the request and handles its answer; 重試 calls `send…` with the same captured values. New methods: `account-detail` `sendEdit(id, payload)`, `sendTransaction(id, payload)`, `sendDelete(txn, accountId)`; `accounts-list` `sendCreate(payload)`; `cash-flow-form` `send(payload)`; `dividend-list` `sendReceipt(row, body)`, `sendDeductions(row, body)`, `sendDelete(dividend)`, `sendSave(payload, id | null)`; `transaction-list` `sendDelete(transaction)`, `sendSave(payload, id | null)`; `item-list` `sendItem(payload, id | null)`, `sendUpload(id, file)`, `sendDelete(id)`; `adjustQuantity` and the others already go through `submitTransaction(itemId, payload, successMessage, callback?)`, whose 重試 re-calls it with the same arguments. Implementers keep each `send…` a move of the existing subscribe block, not a rewrite. `dashboard.ts`: `notify(msg)` → `this.toasts.error(msg)`; the `ngOnInit` branch (`:349`) → `console.warn('另一筆重算進行中，稍候再試')`.

Copy table (50 call sites; RN = `{ retry: retryOnNetwork(err, () => this.send…(captured)) }`; R = `{ retry: () => reload }` for reads):

| File | Line today → new call |
|---|---|
| `account-detail.ts` (10) | 468 `done('帳戶資料已更新')` · 474 `error('帳戶更新失敗')` RN `sendEdit` · 542 `done('交易已建立')` · 547 `error('新增交易失敗')` RN `sendTransaction` · 583 `done('已刪除')` · 586 `error(toastText('刪除失敗', this.deleteErrorMessage(error)))` RN `sendDelete` · 659 `error(toastText('已取消', ACCOUNT_SWITCHED_DETAIL))` · 690 `error('帳戶讀取失敗')` R `loadAccount()` · 709 `error('餘額走勢讀取失敗')` R `loadBalanceHistory()` · 723 `error('現金交易讀取失敗')` |
| `accounts-list.ts` (3) | 170 `error('帳戶讀取失敗')` R `fetchAccounts()` · 211 `done('帳戶已建立')` · 216 `error('新增帳戶失敗')` RN `sendCreate` |
| `cash-flow-form.ts` (2) | 180 `done(toastText('已新增', \`${BROKER_LABELS[raw.broker]} ${result.currency}\`))` · 194 `error(err?.error?.detail ?? '新增失敗')` RN `send` |
| `dashboard.ts` (1) | 823 `error(msg)` (ruling 10; 409 → no 重試) |
| `dividend-list.ts` (11) | 261 `error(toastText('收款確認未完成', serverReason(err)))` RN `sendReceipt` · 326 `error(toastText('扣款更新未完成', serverReason(err)))` RN `sendDeductions` · 359 `error('無法取得收款帳戶')` · 434 `error('查詢失敗，請檢查篩選條件')` · 632 `error('需人工釐清，不可直接改寫已收款或舊資料')` · 664 `done('紀錄已刪除')` · 668 `error('刪除失敗，請稍後再試')` RN `sendDelete` · 681 `done('紀錄已更新')` · 684 `error('更新失敗，請檢查欄位')` RN `sendSave` · 692 `done('紀錄已新增')` · 695 `error('新增失敗，請檢查欄位')` RN `sendSave` |
| `transaction-list.ts` (7) | 204 `error('查詢失敗，請檢查篩選條件')` · 416 `done('紀錄已刪除')` · 420 `error('刪除失敗，請稍後再試')` RN `sendDelete` · 569 `done('紀錄已更新')` · 572 `error('更新失敗，請檢查欄位')` RN `sendSave` · 580 `done('紀錄已新增')` · 583 `error('新增失敗，請檢查欄位')` RN `sendSave` |
| `realized-pnl.ts` (1) | 237 `error('查詢失敗，請檢查篩選條件')` |
| `item-list.ts` (14) | 108 `error('無法載入物品清單')` R `loadItems()` · 150 `error('請檢查名稱、數量與門檻欄位')` · 170 `done('物品已更新')` · 174 `error(err?.error?.message \|\| '更新失敗')` RN `sendItem` · 181 `done('物品已建立')` · 185 `error(err?.error?.message \|\| '建立失敗')` RN `sendItem` · 195 `done('圖片上傳成功')` · 198 `error('圖片上傳失敗')` RN `sendUpload` · 229 `error(err?.error?.message \|\| '操作失敗')` RN re-run the same request · 273 `error('無法載入異動歷史')` · 281 `done('物品已刪除')` · 284 `error('刪除失敗')` RN `sendDelete` · 331 `done(successMessage)` · 335 `error(err?.error?.message \|\| '操作失敗')` RN `submitTransaction(itemId, payload, successMessage, callback)` |
| `error-logging.interceptor.ts` (1) | 46 removed (ruling 9) |

Save call sites with network 重試 in the whole PR (24): the 18 RN entries above, `import.ts:488` and `:571` (Task 4), `account-entries.ts:146`, `account-settings.ts:973`, `:999` (Task 3). (Item-list 229 and 335 count as two; `saveItem`'s two branches as two.)

- [ ] **Step 1: Failing tests.** Replace every `vi.spyOn(fixture.debugElement.injector.get(MessageService), 'add')` / `vi.spyOn(MessageService.prototype, 'add')` with reads of `TestBed.inject(ToastService).current()` (or a spy on `ToastService.prototype.scope` returning a recording scope when a test needs every call):
  - `account-detail.component.spec.ts:394` (`done('已刪除')`), `:448` (text starts `刪除失敗：`), `:761` helper (`已取消：帳戶已切換，請重新操作`);
  - `dividend-list.spec.ts:316`, `:406`, `:1235` (`收款確認未完成：Synthetic stale revision`), `:1355`, `:1396`, `:1429`, `:1519`, `:1539`/`:1545`, `:1554`/`:1557`, `:1710`, `:1741`, `:1861`;
  - `realized-pnl.component.spec.ts:433`; `transaction-list.spec.ts:539`.
  - New save-retry tests (500 → 重試 re-sends the captured payload once, also after the dialog was closed; 422 / 400 → no 重試): `transaction-list.spec.ts` `saveTransaction` update and create, `deleteTransaction`; `dividend-list.spec.ts` `confirmReceipt`, `saveDividend`, `deleteDividend`; `account-detail.component.spec.ts` `submitEdit`, `executeDelete`; `accounts-list.component.spec.ts` `submitCreate`; `item-list.spec.ts` `saveItem`, `deleteItem`, `adjustQuantity`.
  - New: `item-list.spec.ts` `a failed list load shows 無法載入物品清單 with 重試, which loads again`; `dashboard.spec.ts` `刷新行情 answered 409 shows 失敗 另一筆重算進行中，稍候再試; the load on open does not`.
  - `error-logging.interceptor.spec.ts`: drop the `MessageService` provider; `by default a failing request is retried once and toasts one error` → `…retried once, logged and propagated without a toast`.
  - `toast-outlet.spec.ts`: `no PrimeNG toast remains` — recursive source scan of `src/app` (`.ts` / `.html`, not `*.spec.ts`) for `p-toast`, `primeng/toast`, `MessageService` → empty.
- [ ] **Step 2: Run** `--include='src/app/components/portfolio/**/*.spec.ts'`, `--include='src/app/components/item-list/*.spec.ts'`, `--include='src/app/interceptors/*.spec.ts'`, `--include='src/app/components/shell/toast-outlet/*.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement**; remove `MessageService` from `app.config.ts` last.
- [ ] **Step 4: Run** the four commands — Expected: PASS. `grep -rn "p-toast\|primeng/toast\|MessageService" frontend/src` — Expected: no output.
- [ ] **Step 5: Commit** `refactor(portfolio): drop PrimeNG toast; saves retry on network failures`.

### Task 6: `<app-banner>`

D7, tasks 1.2 (primitive) and 1.3 (dismissal remembered for 資訊 only).

**Files:**
- Create: `frontend/src/app/components/ui/banner/banner.ts`, `banner.scss`, `banner.spec.ts`, `banner-dismissals.ts`, `banner-dismissals.spec.ts`
- Modify: `frontend/src/app/theme/contrast.spec.ts`

**Interfaces (produced):**

```ts
export type BannerTone = 'error' | 'warn' | 'info';
@Component({ selector: 'app-banner', changeDetection: ChangeDetectionStrategy.OnPush, styleUrl: './banner.scss' })
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
export const BANNER_DISMISSALS_KEY = 'hh-banner-dismissed';   // JSON string[]
@Injectable({ providedIn: 'root' })
export class BannerDismissals {
  isDismissed(id: string): boolean;  // signal-backed, read from storage once
  dismiss(id: string): void;         // memory first; storage write in try/catch
}
```
Template: nothing when dismissed; else `div.banner.banner--<tone>[role]` (error `alert`, else `status`) > `i.banner-icon` (`pi pi-exclamation-circle` / `pi pi-exclamation-triangle` / `pi pi-info-circle`, `aria-hidden`), `div.banner-body` > `b.banner-title` + optional `span.banner-detail`, optional `button.banner-action`, and for `info` with `dismissId` `button.banner-close[aria-label="關閉"]`. Styles per the artboard: `display: flex; align-items: flex-start; gap: 12px; padding: 12px 14px; border-radius: 14px`; icon 20 px; title 14 px / 800; detail 13 px, `line-height: 1.5`; action `min-height: 34px; padding: 0 12px; border-radius: 10px; font-size: 13px; font-weight: 700`; `flex-wrap: wrap` with the body `flex: 1 1 220px` so the action drops under the text on a phone. Tokens per ruling 15.

- [ ] **Step 1: Failing tests** (`banner.spec.ts`, host-component pattern as in `tag.spec.ts`):
  - `error renders role alert with heading, detail and action, and no close even with a dismissId`;
  - `warn and info render role status`;
  - `the action emits once per click and honours actionDisabled`;
  - `no detail → no .banner-detail`; `no actionLabel → no .banner-action`;
  - `info with a dismissId shows 關閉; closing hides it and stores the id` (`hh-banner-dismissed` contains `test-info`);
  - `a dismissed info id stays hidden in a new instance; another id still shows`;
  - `info without a dismissId has no close`;
  - `storage that throws: renders, closes, stays closed for the session`;
  - `scss uses tokens only` (`var(--c-red-soft)`, `var(--c-red-pill-ink)`, `var(--app-banner-warn-soft)`, `var(--app-banner-warn)`, `var(--app-primary-soft)`; no colour literal).
  `banner-dismissals.spec.ts`: `reads a stored list once`; `ignores a malformed value`; `dismiss is idempotent`.
  `contrast.spec.ts`: `banner headings on their tints over the page background` — both themes over `--app-bg` and `--app-surface`: `--c-red-pill-ink` on `--c-red-soft`, `--app-banner-warn` on `--app-banner-warn-soft`, `--app-text` on `--app-primary-soft` ≥ 4.5; `--app-primary` on `--app-primary-soft` ≥ 3.
- [ ] **Step 2: Run** `--include='src/app/components/ui/banner/*.spec.ts'` and the contrast spec — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(ui): app-banner with 錯誤 / 注意 / 資訊 tones`.

### Task 7: Page alerts move onto `<app-banner>`

Rulings 17–18. Six alerts in four templates.

**Files:**
- Modify: `frontend/src/app/components/portfolio/import/import-math.ts` (`failureBanner`, `recalcBanner`), `import-math.spec.ts`
- Modify: `frontend/src/app/components/portfolio/import/import.ts`, `import.html` (lines 7–18), `import.scss` (remove `.import-banner`), `import.spec.ts`
- Modify: `frontend/src/app/components/accounting/reminders/reminders.ts` (`billErrorText` → `billErrorDetail`), `reminders.html` (lines 65–70, 199–204), `reminders.scss` (remove `.bill-error`, `.schedule-failing`), `reminders.spec.ts`
- Modify: `frontend/src/app/components/accounting/reward-rules/reward-rules.ts`, `reward-rules.html` (lines 25–31), `reward-rules.scss` (remove `.rr-banner*`), `reward-rules.spec.ts`
- Modify: `frontend/src/app/components/portfolio/accounts/account-detail.ts`, `account-detail.html` (lines 65–78), `account-detail.scss` (remove `.overdraft-banner` and its media rule), `account-detail.component.spec.ts`

**Interfaces:**
- `failureBanner(a): { heading: string; detail: string } | null` — `{ heading: '最近一次匯入失敗', detail: \`${stamp(a.at)} · ${a.file} · ${reason}，沒有寫入任何資料。\` }`, `reason` = `有 ${a.errors} 列錯誤` or `a.reason || '未知錯誤'`; same null conditions.
- `recalcBanner(status): { heading: string; detail: string } | null` — `{ heading: '最近一次匯入的重算失敗', detail: reason }`; same null conditions and fallbacks.
- `AccountingRemindersComponent.billErrorDetail = computed<string | null>` — distinct reasons joined with `、`.
- Markup (host classes keep the old names):
  - `<app-banner class="import-banner" tone="error" [heading]="b.heading" [detail]="b.detail" />` for (a) then (b).
  - `<app-banner class="bill-error" tone="error" heading="帳單讀取失敗" [detail]="billErrorDetail()" actionLabel="重試" (action)="retryBills()" />`.
  - `<app-banner class="schedule-failing" tone="error" heading="這一期入帳失敗" [detail]="failing.text" actionLabel="重試" [actionDisabled]="retryingDefinitions().has(row.id)" (action)="retryDefinition(row)" />`.
  - `<app-banner class="rr-banner" tone="warn" [heading]="count + ' 條規則已過期但仍啟用'" detail="之後的消費不會再估算回饋" actionLabel="只看已過期" (action)="setFilter('expired')" />`.
  - `<app-banner class="overdraft-banner" tone="warn" [heading]="'帳戶餘額為負（' + formatCurrency(account()?.native_balance, account()?.currency || 'TWD') + '）'" detail="可能漏記入金。" actionLabel="新增入金歸零" [actionDisabled]="!accountReady()" (action)="openTopupQuickFix()" />` (`ButtonModule` stays — seven other `p-button`s).

- [ ] **Step 1: Failing tests.**
  - `import-math.spec.ts` `failureBanner` (`:591`–`:611`): `'10/21 22:10 · a.csv · 有 1 列錯誤，沒有寫入任何資料。'`, `'10/21 22:10 · a.csv · HTTP 500，沒有寫入任何資料。'`; `recalcBanner` (`:615`–`:632`): details `TWSE timeout`, `淨值回補`, `等待鎖定`, `未知錯誤`, `future_step`.
  - `import.spec.ts` (`:1017`–`:1139`): the `banner()` helper reads `app-banner.import-banner` heading + detail; expectations `最近一次匯入失敗` / `10/21 22:10 · a.csv · 伺服器錯誤，沒有寫入任何資料。`, `… 有 1 列錯誤，…`, `最近一次匯入的重算失敗` / `TWSE timeout`, `淨值回補`; new `both import banners are 錯誤: role alert, no close, no action`.
  - `reminders.spec.ts` `:350` → `.bill-error .banner-title` `帳單讀取失敗`, no `.banner-detail`, `.banner-action` retries; `:397` → `.banner-detail` `幣別不同`; `:989` → `.schedule-failing .banner-title` `這一期入帳失敗`, `.banner-detail` `lines[0].account_id: 帳戶已封存`, the action posts the catch-up; `:1002` → the action is disabled while retrying.
  - `reward-rules.spec.ts` `:404` → `.rr-banner .banner-title` `1 條規則已過期但仍啟用`, `.banner-detail` `之後的消費不會再估算回饋`, `.banner--warn`, `[role="status"]`, the action selects 已過期; `:422` keeps `.rr-banner` null.
  - `account-detail.component.spec.ts` `:1222` (assertion `:1239`) → `.overdraft-banner .banner-title` `帳戶餘額為負（−$12,345）`, `.banner-detail` `可能漏記入金。`, `.banner--warn`; new `新增入金歸零 opens the top-up form and is disabled until the account is ready`.
- [ ] **Step 2: Run** `--include='src/app/components/portfolio/import/*.spec.ts'`, `--include='src/app/components/accounting/reminders/*.spec.ts'`, `--include='src/app/components/accounting/reward-rules/*.spec.ts'`, `--include='src/app/components/portfolio/accounts/*.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement**; remove the old rules (no orphan selectors).
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `refactor(ui): import, bill, schedule, reward-rule and overdraft alerts use app-banner`.

### Task 8: Whole-page read failures move onto `<app-banner>`

Ruling 17 (coordinator item 1). Eight sites; each becomes `<app-banner class="page-load-error" tone="error" heading="<existing copy>" actionLabel="重試" (action)="<retry>" />` in the place of today's block (same `@if` branch).

**Files and sites:**

| Page | Template:line today | Heading (existing copy) | 重試 |
|---|---|---|---|
| 對帳帳單 | `accounting/statements/statement-list/statement-list.html:14–18` | 帳單讀取失敗 | `retry()` (`statement-list.ts:61`) |
| 對帳明細 | `accounting/statements/statement-detail/statement-detail.html:14–18` | 帳單讀取失敗 | `retry()` (`statement-detail.ts:164`) |
| 報表 | `accounting/reports/reports.html:51–52` | 報表讀取失敗 | `reload()` (`reports.ts:363`) |
| 回饋規則總覽 | `accounting/reward-rules/reward-rules.html:33–35` | `error()` (the `writeErrorMessage` text) | new `retry()`: re-runs `load()` with the last query (stored in a private `lastQuery` by `load`) |
| 帳戶 | `accounting/accounts/accounts.html:45–49` | 帳戶讀取失敗，請稍後再試。 | `retry()` (`accounts.ts:301`) |
| 提醒中心 | `accounting/reminders/reminders.html:15–16` | 提醒讀取失敗，請稍後再試。 | new `retry()`: `this.load()` (private `load` at `reminders.ts:495`) |
| 記錄明細 | `accounting/entry-detail/entry-detail.html:3–10` | 記錄讀取失敗，請稍後再試。 | new `retry()`: `this.load(<last id>)` (store the id the route effect at `:543` loads) |
| 多類別群組 | `accounting/split-group/split-group.html:3–10` | 記錄讀取失敗，請稍後再試。 | new `retry()`: `this.load(<last id>)` (route effect at `:125`) |

The entry-detail / split-group `.error-head` ✕ (`!detail() && !inSheet()`) stays above the banner. Remove the now-unused `.load-error-block` rules from `statement-list.scss` / `statement-detail.scss` / `accounts.scss` only where no other block in the same template still uses them (`accounts.scss:117` has no other user; check each); `.rp-error` / `.rp-retry` / `.rr-error` rules are removed.

**Files (specs):** `statement-list.spec.ts` (`:169`, `:172`), `statement-detail.spec.ts` (new case; `:220` keeps asserting no page banner on a section failure: `.page-load-error` null), `reports.spec.ts` (`:702`, `:704`, `:710`, `:955`), `reward-rules.spec.ts` (new), `accounts.spec.ts` (`:692`–`:717`), `reminders.spec.ts` (`:724`), `entry-detail.spec.ts` (`:584`, `:615`, `:754`, `:1591`, `:1629` — `.load-error` → `.page-load-error`), `split-group.spec.ts` (`:349`).

- [ ] **Step 1: Failing tests.** Per page: `a failed load shows an 錯誤 banner with <heading> and 重試` (`.page-load-error .banner-title` text, `[role="alert"]`, no `.banner-close`) and `重試 reads again and the banner leaves on success` (one more request; banner gone). Statement-detail: `a section read failure (帳本) shows no page banner`. Reward rules: `retry() re-reads with the same query (account, state, archived)`.
- [ ] **Step 2: Run** `--include='src/app/components/accounting/{statements,reports,reward-rules,accounts,reminders,entry-detail,split-group}/**/*.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `refactor(accounting): whole-page read failures use app-banner with 重試`.

### Task 9: Tasks, design index, full suite, evidence

**Files:**
- Modify: `openspec/changes/add-notification-center/tasks.md` — tick 1.1, 1.2, 1.3; append to 1.2 「(quote delay: no such alert exists; needs a rule — deferred)」; append to 1.1 「(復原 API only; the reconciliation 復原 scenario is wired and verified in PR D)」.
- Modify: `design_handoff_v3/README.md` — **Colour mapping** rows: toast surface → `--app-accent` / `--app-surface`; toast inks → `--app-toast-action` / `--app-toast-ok` / `--app-toast-error` (dark ok `#177a55`); error banner title → `--c-red-pill-ink`; info banner → `--app-primary-soft` / `--app-primary`.

- [ ] **Step 1:** Edit both files.
- [ ] **Step 2: Run** `openspec validate add-notification-center --strict` — Expected: valid.
- [ ] **Step 3: Run** `cd frontend && npx ng test --watch=false` — Expected: all pass; `npx ng build` — Expected: success.
- [ ] **Step 4: Evidence (outside the repo).** Before (origin/main) / after builds; Playwright with routed API stubs (fictional data). Screens per the plan below, plus probes:
  - geometry: at 1440 (no pane, list pane, calendar pane), 1100 (list pane, calendar pane), 1024 (list pane), 820 and 390, `toast.getBoundingClientRect().right <= fab.getBoundingClientRect().left - 12` whenever `a.hub-fab` is visible, and the toast's bottom ≤ the tab bar's top at 390;
  - keyboard: at 820 with the sheet open and in the transaction-list PrimeNG edit dialog at 1440 — Tab reaches 重試, Enter runs it, Esc in the toast leaves the modal open, focus returns to the modal; the toast is above the PrimeNG mask (`elementFromPoint` at the toast's centre is inside the toast).
  Screens go into the PR description as a private gallery link.
- [ ] **Step 5: Commit** `docs(openspec): tick add-notification-center 1.1–1.3 with notes; toast and banner colour mapping`.

---

## Screenshot plan

Before / after at 1440 and 390, light and dark, unless the row says otherwise. Stubs are Playwright `page.route` handlers; localStorage seeds go in `addInitScript`.

| Screen | Route / size | Trigger with stubbed data |
|---|---|---|
| Toast 完成 | `/portfolio/transactions` | edit row 1 → 儲存, PUT → 200 → 「紀錄已更新」 |
| Toast 進行中 | `/portfolio/import` | commit `範例券商_202610.csv`, POST → `{parsed: 12, created: 10, skipped_duplicates: 2, errors: [], recalc_scheduled: true}`, status → `{state: 'running'}` |
| Toast 失敗 + 重試 (read) | `/portfolio/import` | after the commit, status → 500 → 「無法取得重算狀態」 |
| Toast 失敗 + 重試 (save, over a PrimeNG dialog) | `/portfolio/transactions` (1440 and 390) | edit → 儲存, PUT → 500 → 「更新失敗，請檢查欄位」 with 重試 over the open dialog; Tab focus on 重試 |
| Toast 失敗 (accounting) | `/accounting/reminders?tab=pending` | queue action → 409 `import_running` |
| Toast with the FAB: no pane | `/accounting` at 1440 | any 失敗 toast (e.g. a stubbed 409 on a delete) |
| Toast with the FAB beside a list pane | `/accounting/entries/:id` at 1100 and 1440 (light / dark) | entry open in the right pane, then the same 失敗 |
| Toast with the FAB beside the calendar pane | `/accounting?month=2026-10&day=2026-10-22` → entry drawer, at 1100 (light / dark) | the same 失敗 |
| Toast over the sheet | `/accounting/entries/:id` at 820 (light / dark) | delete → 409 `import_running`; focus on 關閉 |
| Toast 完成 + 復原 | — | no PR A call site (ruling 8); unit tests only |
| Banner 錯誤 import (a) | `/portfolio/import` | seed `hh-import-history` with `[{at: '2026-10-21T14:10:00Z', file: '範例券商_202610.csv', type: 'transactions', ok: false, created: 0, errors: 1}]` |
| Banner 錯誤 recalc (b) | `/portfolio/import` | status → `{state: 'failed', kind: 'import', steps: [{name: 'networth_backfill', status: 'failed', error: 'TWSE timeout'}]}` |
| Banner 錯誤 帳單讀取失敗 | `/accounting/reminders?tab=cards` | card `測試銀行卡` with a due event; statement GET → 500 |
| Banner 錯誤 failing schedule | `/accounting/reminders?tab=debts` | definition `測試月費` with `failing: {instance_id: 7, last_error: '帳戶已封存'}` |
| Banner 注意 expired rules | `/accounting/reward-rules` | one enabled rule ending 2026-09-30 |
| Banner 注意 overdraft | `/portfolio/accounts/:id` | `native_balance: -12345`, TWD |
| Page banners (8) | `/accounting/accounts/:id/statements`, `/accounting/accounts/:id/statements/:sid`, `/accounting/reports`, `/accounting/reward-rules`, `/accounting/accounts`, `/accounting/reminders`, `/accounting/entries/:id`, `/accounting/entries/:id/group` | the page's main GET → 500 (one shot per page at 1440 light and 390 dark; the full 4-way set for statement-list and reports) |

## Verification

Checked against the worktree at `d5416118` (plan rev 1) and re-checked for this revision:

- **PrimeNG toasts:** 64 `messageService.add` call sites in 10 files (account-detail 10, accounts-list 3, cash-flow-form 2, dashboard 1, dividend-list 11, import 14, item-list 14, realized-pnl 1, transaction-list 7, interceptor 1); 8 `<p-toast>` elements; `MessageService` provided by those 8 components plus root (`app.config.ts:23`). Only `primeng-toast` and `primeng-api` reference `MessageService` in `node_modules/primeng/fesm2022`.
- **Accounting toast:** 22 call sites in 8 files (13 direct, 9 via `scheduleActionError`); one outlet at `accounting-layout.html:172`.
- **Save sites with network 重試:** 24 (list under Task 5). Enclosing handlers confirmed: `submitEdit`, `submitTransaction`, `executeDelete` (account-detail); `submitCreate` (accounts-list); `submit` (cash-flow-form); `confirmReceipt`, `submitDeductions`, `deleteDividend`, `saveDividend` (dividend-list); `deleteTransaction`, `saveTransaction` (transaction-list); `saveItem`, `onUpload`, `adjustQuantity`, `deleteItem`, `submitTransaction` (item-list); `retryRecalc`, `onUploadError` (import); `onLogoFile` (account-entries); `uploadLogoFile`, `removeLogo` (account-settings).
- **Banners:** 6 alerts (Task 7) + 8 whole-page read failures (Task 8) = 14 sites in 12 templates. Review citations confirmed: `statement-list.html:14`, `statement-detail.html:14`, `reports.html:51`, `reward-rules.html:33` with `reward-rules.ts:302–314` (no retry today). Re-survey added `accounts.html:46`, `reminders.html:16`, `entry-detail.html:9`, `split-group.html:9`. No quote-delay alert exists.
- **Modal / focus:** sheet `role="dialog"` + `aria-modal` at `accounting-layout.html:31–32`, drawer at `:153–154`; `onPaneKeydown` → `trapFocus` (`accounting-layout.ts:415`), drawer `:393`; `trapFocus` / `isHandledKey` (`accounting-ui.ts:156`, `:231`; `isHandledKey` honours `defaultPrevented`); layout Esc `document:keydown.escape` (`accounting-layout.ts:112`, `:569`). PrimeNG 21.1: `pFocusTrap` sentinels carry `data-p-hidden-focusable="true"` (`primeng-focustrap.mjs`); dialog Esc is a `document` keydown listener (`primeng-dialog.mjs:890`).
- **FAB / layout:** `FAB_RIGHT_BESIDE_PANE` / `FAB_RIGHT_BESIDE_CALENDAR_PANE` (`accounting-layout.ts:76–78`), set at `:324`; shell FAB `bottom: 30px`, 56 px, `right: var(--fab-right, 30px)`; `--sidebar-w` 248 / 72 (`shell.ts`), expanded only ≥ 1280 px (`layout-mode.service.ts:13`) and rail with a pane open when `hh-nav-autocollapse` is on (default on, `navigation.ts:145–148`); tab bar 64 px + safe area (`mobile-nav.scss:10`, `:19`).
- **Lifetime:** `DestroyRef.destroyed` and `onDestroy(): () => void` exist in Angular 21.2 (`@angular/core` types); import's `destroyed` guards at `import.ts:213`, `:631`, `:637`; un-cancelled subscriptions at `:365`, `:479`, `:519`, `:534`, `:660`.
- **Tokens / contrast** as in ruling 13 / 15 (computed: toast text 15.1 / 14.3, action 7.4 / 5.2, ok 8.2 / 4.5, error 6.9 / 4.3; 錯誤 heading 4.6 / 6.3; 注意 5.0 / 6.4; 資訊 13.8 / 14.1). Task 1 / 6 contrast tests re-compute them from the stylesheet.
- **Could not confirm:** whether `p-dialog` renders under jsdom well enough for the Task 2 transaction-list case (fallback: the Task 9 browser probe); PrimeNG's runtime z-index under nested overlays (browser probe: `elementFromPoint`); the geometry table assumes 1rem = 16 px and the default auto-collapse.
