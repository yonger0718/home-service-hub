# Notification center PR E2: snooze (稍後提醒) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every counted task on `/notifications` offers 稍後提醒 (3 小時後 / 明天 09:00 / 下週一 09:00). A snoozed task leaves the list, the chip / section counts, the subtitle and the 通知 badge until its time passes or it wakes early; snoozes live in this device's `localStorage` only.

**Architecture:** Angular SPA only (`frontend/`), no API change. The tasks' unused `fingerprint: string` becomes a structured `wake` value; a pure `snooze.ts` (option times, wake rule, store parsing); a root `NotifSnoozeStore` (localStorage + `storage` event + expiry timer); `NotificationCenterService.tasks` filters snoozed tasks; the row gets a 稍後提醒 picker; the page gets the toast with 復原 and the 「另有 N 件稍後提醒 · 現在顯示」 line.

**Tech Stack:** Angular 21 standalone + signals, Vitest (`npx ng test --watch=false`), OpenSpec CLI 1.15.

**Spec:** `openspec/changes/add-notification-center/` design D4 + spec requirement "Snooze on this device" + tasks 5.3 / 5.4, amended by the owner rulings below.

## Global Constraints

- Owner rulings:
  - 2026-11-25 (earlier): a snoozed task wakes early only when its count GROWS or its amount / due date changes; a lower count does not wake.
  - 2026-11-25 (AskUserQuestion): a task that becomes 已逾期 while snoozed wakes (「變成已逾期就跳回來」).
  - 2026-11-25 (AskUserQuestion): a muted line at the bottom 「另有 N 件稍後提醒」 with 「現在顯示」 (clears every snooze); the snooze toast has 復原.
- Copy (exact):
  - Trigger label 稍後提醒; options 3 小時後, 明天 09:00, 下週一 09:00 (spec). Each option's hint is the absolute time 「M/D HH:mm」 (e.g. 「10/24 13:00」).
  - Toast `已延後到 <M/D HH:mm>：<task title>` with 復原 (done toast; 復原 removes that snooze).
  - Bottom line `另有 N 件稍後提醒` + button `現在顯示`.
- Times use the device clock (`NOTIF_CLOCK`) and local time zone: 3 小時後 = now + 3 h; 明天 09:00 = next calendar day 09:00; 下週一 09:00 = the first Monday strictly after today, 09:00.
- Storage: key `hh-notif-snooze`, value `{ [taskId]: { until: ISO string, tier, from, wake } }`; every read / write in try/catch; a corrupt value reads as `{}`; never sent to the server. Entries whose `until` has passed are dropped on read.
- Only counted tasks (已逾期 / 今天 / 要你確認 / 系統) can be snoozed; tracking rows unchanged. The review flow's own 略過，稍後再看 is unrelated and unchanged.
- Test data fictional; every SPA build carries the copied `environment.ts` (never printed).

## Decisions (my calls, recorded for review)

- `fingerprint: string` (only consumer was this PR, see `notif-task.ts:43` "R10") is replaced by `wake: NotifWake = { count?: number; amount?: string; due?: string; members?: readonly string[]; key?: string }`. Wake rule vs the stored entry: until passed; OR tier escalates to `overdue` from a non-overdue stored tier; OR `count` > stored; OR `amount` ≠ stored; OR `due` ≠ stored; OR some current `members` id not in stored; OR `key` ≠ stored. Absent fields never wake.
- Per source (from today's fingerprints):
  - bills `{ amount: remaining, due: payDate }` (was `remaining|payDate`).
  - schedules `{ count: n }` only (was `n|oldestDue`): posting the oldest instance lowers the count and moves the oldest due later; that must not wake (owner: lower count does not wake). A new instance due raises the count → wakes.
  - recon / einvoice / importReview / recon overflow `{ count: n }` (overflow 'more' → `{ key: 'more' }`).
  - dividends batch `{ members: ids }` (a new due dividend wakes; one received does not).
  - import failure `{ key }` (a new failure wakes); cathay tasks `{ key: <today's fingerprint string> }` (status / reason / run change wakes).
- A snooze whose task vanished in a settled round where its source (`from`) read ok is dropped (so a statement that is cleared and later gets new questions is not hidden by a stale lower count). Failed sources keep their snoozes.
- Expiry without a reload: the store arms one `setTimeout` to the earliest `until` (capped at 24 h, re-armed), bumping a `tick` signal the service reads; a `storage` event from another tab reloads the entries.
- `NotificationCenterService.tasks` = counted AND not snoozed (so `summary`, chips, sections, subtitle, nav badge follow). New `countedTasks` (incl. snoozed) for `recon-flow.ts:273` (its 已對上 k / n must not vanish when its row is snoozed). New `snoozedCount`.
- UI: the row's actions get `<app-picker label="稍後提醒" value="">` (no option has value '', so the trigger reads 稍後提醒; popover ≥ phone, bottom sheet on phone; existing keyboard / Escape behaviour). Restyled in `notif-row.scss` to read like the secondary links (no select chrome beyond the chevron).

## Code facts (verified at origin/main 32e5edcd)

- `notif-task.ts:22-45` `NotifTask` (`fingerprint` at :44), `COUNTED_TIERS`, `summarize`, `chipCounts`.
- Fingerprints: `sources/bills.ts:61`, `schedules.ts:88`, `recon.ts:69,91,126`, `ledger-misc.ts:35`, `portfolio.ts:59,126`, `cathay.ts:145,164`; specs assert them (`*.spec.ts`).
- `services/notification-center.service.ts`: `allTasks` :233-270, `tasks` :305-308, `summary` :322, `status` per source, `settledRound`, `NOTIF_CLOCK` :55.
- Consumers of `center.tasks()`: `notifications.ts:140,146,170,246`, `recon-flow.ts:273`; nav badge `navigation-state.service.ts:62` via `summary()`.
- `notif-row/notif-row.{ts,html}`: actions block = primary + `secondary` links; `run` output.
- `notifications.html`: sections, overflow notes, loading, `notif-none`, tracking `<details>`, foot links, failure banner; empty state `state() === 'empty'`.
- `ui/picker/picker.ts`: `options: PickerOption{value,label,hint?}`, `value`, `label`, `change` output; `selectedLabel` falls back to `label()`.
- Toasts: `injectToasts().done(text, { undo })` (`services/toast.service.ts:25`).
- localStorage pattern: `services/import-history.service.ts:60-90` (try/catch, `storage` getter).

## Review Focus

1. Lower count does not wake; count growth, amount change, due change, new dividend member and 已逾期 escalation do (one test each).
2. Clock edges: 明天 09:00 at 23:30; 下週一 09:00 on a Monday (→ next week) and on a Sunday (→ tomorrow); expiry returns the task without a page reload (fake timers).
3. Corrupt / blocked storage: page works, nothing snoozed, no throw.
4. The 通知 badge and chip / section counts drop by the snoozed tasks; the recon flow's 已對上 k / n survives its row being snoozed.
5. A vanished task's snooze is dropped only when its source read ok (a failed source keeps it).

---

### Task 1: Wake model, pure snooze logic

**Files:** Modify `notif-task.ts`, every `sources/*.ts` (+ specs: fingerprint expectations → wake); Create `components/notifications/snooze.ts`, `snooze.spec.ts`.

**Interfaces (produces):**
- `NotifWake` (above); `NotifTask.wake: NotifWake` replaces `fingerprint`.
- `type SnoozeOption = '3h' | 'tomorrow' | 'monday'`; `SNOOZE_OPTIONS: readonly { value: SnoozeOption; label: string }[]` (labels as Global Constraints).
- `snoozeUntil(option: SnoozeOption, now: Date): Date`.
- `interface SnoozeEntry { until: string; tier: NotifTier; from: NotifSourceKey; wake: NotifWake }`; `snoozeEntry(task, until): SnoozeEntry`.
- `wakes(entry: SnoozeEntry, task: NotifTask, now: Date): boolean` (the rule above).
- `parseSnoozes(raw: string | null, now: Date): Record<string, SnoozeEntry>` (corrupt → `{}`, drops expired and malformed entries).
- `untilText(until: Date): string` → 「10/24 13:00」.

- [ ] **Step 1: Tests** (`snooze.spec.ts`, clock `2026-10-24T10:00` local Sat): `snoozeUntil('3h')` → 13:00 same day; `'tomorrow'` → 10/25 09:00, and at 23:30 → next day 09:00; `'monday'` → 10/26 09:00, from a Monday 10:00 → the following Monday, from a Sunday → the next day. `wakes`: until passed → true; same wake → false; count 3→4 true, 3→2 false; amount '1000'→'900' true; due change true; members [1,2]→[1,2,3] true, →[1] false; key change true; tier today→overdue true, overdue→overdue false, confirm stays false. `parseSnoozes`: null / '{' / '[]' / entry with bad `until` → `{}` (or entry dropped); expired dropped. `untilText`. Source specs: each former fingerprint assertion becomes the `wake` value per Decisions.
- [ ] **Step 2:** run `npx ng test --watch=false --include src/app/components/notifications/snooze.spec.ts --include 'src/app/components/notifications/sources/*.spec.ts'` → FAIL.
- [ ] **Step 3:** implement; delete `fingerprint`.
- [ ] **Step 4:** same command → PASS; `npx tsc --noEmit -p tsconfig.app.json` and `tsconfig.spec.json` clean.
- [ ] **Step 5: Commit** `feat(notifications): structured wake values and snooze rules (PR E2)`.

### Task 2: Store and center integration

**Files:** Create `services/notif-snooze.store.ts` (+ spec); Modify `services/notification-center.service.ts` (+ spec), `recon-flow.ts:273`.

**Interfaces:**
- `NotifSnoozeStore` (root): `entries: Signal<Readonly<Record<string, SnoozeEntry>>>`; `set(id, entry)`; `remove(id)`; `clear()`; `drop(ids: readonly string[])`; reads `hh-notif-snooze` at construction, writes on every change (try/catch), `window` `storage` event reloads, timer to the earliest `until` (≤ 24 h) bumps an internal tick so `entries` re-filters expired ones.
- Center: `tasks` = counted, not snoozed (`wakes(...)` false while an entry exists → hidden); `countedTasks`; `snoozedCount: Signal<number>` (counted tasks currently hidden); `snooze(task, option): Date` (stores `snoozeEntry(task, snoozeUntil(option, clock()))`, returns until); `unsnooze(id)`; `showSnoozed()` (= clear). A woken entry is removed from the store (so it does not re-hide after the wake condition passes). After each settled round: drop entries whose `from` source is `ok` and whose id is absent from `allTasks()`.

- [ ] **Step 1: Tests:** store — round-trip through a fake `localStorage`; throwing `getItem` / `setItem` → empty and no throw; `storage` event reload; fake timers: entry expiring at T disappears at T. Center — a snoozed bill leaves `tasks()` and `summary().total` drops by 1, `snoozedCount()` 1; schedule count 3→2 stays hidden, 3→4 returns (and the entry is gone); bill 今天→已逾期 returns; settled round with `recon` ok and the task gone drops its entry, with `recon` failed keeps it; `countedTasks()` keeps the snoozed task; badges via `summary()`.
- [ ] **Step 2:** run the two specs → FAIL.
- [ ] **Step 3:** implement; `recon-flow.ts:273` reads `countedTasks()`.
- [ ] **Step 4:** specs + `recon-flow.spec.ts` → PASS; both tsc clean.
- [ ] **Step 5: Commit** `feat(notifications): snooze store and hidden tasks in the center (PR E2)`.

### Task 3: Row picker, toast, bottom line

**Files:** Modify `notif-row/notif-row.{ts,html,scss}` (+ spec), `notifications.{ts,html,scss}` (+ `notifications.spec.ts`, `notifications.integration.spec.ts`).

**Interfaces:** row input `snoozable = input(true)`, output `snooze = output<SnoozeOption>()`; picker options = `SNOOZE_OPTIONS` with `hint: untilText(snoozeUntil(value, now))` (now from `NOTIF_CLOCK` when the row renders); accessible name `稍後提醒：<title>`. Page: `onSnooze(task, option)` → `center.snooze` → `toasts.done(\`已延後到 ${untilText(until)}：${task.title}\`, { undo: () => center.unsnooze(task.id) })`; focus moves to the next row's primary control (or the section heading / empty state when none, same as after a command success). Bottom line `<p class="notif-snoozed">另有 {{ n }} 件稍後提醒 · <button type="button">現在顯示</button></p>` after the overflow notes, shown when `snoozedCount() > 0`, also in the empty state.

- [ ] **Step 1: Tests:** row — trigger text 稍後提醒, three options with labels and hints, picking emits the option; page — snoozing removes the row, section count and chip count drop, toast text exact with 復原, 復原 brings it back; bottom line text `另有 1 件稍後提醒`, 現在顯示 restores every row and hides the line; all snoozed → 都處理好了 plus the line; integration spec (real center, fake storage) — snooze 測試房租 until 明天 09:00, advance fake time to 09:00 → row returns.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** notification specs, full suite `npx ng test --watch=false`, both tsc → green.
- [ ] **Step 5: Commit** `feat(notifications): 稍後提醒 on every counted task (PR E2)`.

### Task 4: OpenSpec, evidence

- [ ] **Step 1:** design D4 (stored entry `{until, tier, from, wake}`, wake rule incl. 已逾期 escalation and lower count, bottom line, toast), spec "Snooze on this device" (requirement text + scenarios "Lower count keeps the snooze", "Overdue wakes a snoozed task", "Show snoozed tasks"), tasks 5.3 / 5.4 checked with notes. `openspec validate add-notification-center --strict` valid.
- [ ] **Step 2: Commit** `docs(openspec): PR E2 snooze rules`.
- [ ] **Step 3: Evidence** (kit `~/reports/home-hub-playwright-stubs`, fictional data, clock fixed Sat 2026-10-24 10:00): screens at 390 / 1100 / 1440 light + dark — row with 稍後提醒 open (popover / phone sheet), after snoozing (toast with 復原 + bottom line), all snoozed (都處理好了 + line); probes: badge count drops, 現在顯示 restores, reload keeps the snooze, corrupt storage value, expiry by clock. Gallery for the owner.

## Plan review fold (AGENT-603, PASS-with-minor, 2026-11-26)

1. Schedules: the source spec pins `wake: { count: n }` (no `due`); the center test moves 3 → 2 with the oldest due moving later (10/20 → 10/21) and stays hidden with the entry kept; 3 → 4 wakes and removes the entry.
2. Ruling: `amount` is the same string as the old fingerprint (`${remaining}`); dividend `members` are the ids as strings (`['2', '10']`, numeric order) — one string type for every wake field; the set comparison is what matters — cost if wrong: none (internal).
3. Picker gets an optional `ariaLabel` input (default unchanged); the row passes `稍後提醒：<title>`; `value=""` so a repeat pick emits.
4. Focus goes through the command path (`answered` + `restoreFocus`): next row's primary, else tracking summary, else chips; page test asserts it.
5. Code-fact anchors drifted by a few lines (allTasks :243-280, tasks :302-305, NOTIF_CLOCK :54, import-history :47-81); no behaviour change.
6. Store: `getItem` throw / corrupt → `{}`; `setItem` throw keeps the in-memory map (test).
7. `recon-flow.spec.ts` now stubs `countedTasks` (with progress) and an empty visible `tasks`, so the existing 已對上 17 / 28 test pins the snoozed-row case.
8. Center tests: same-day 3 小時後 10:00 → 13:00 returns by the timer alone with the tier still 今天; a 下週一 snooze survives the 24 h timer tick (the timer effect re-arms whenever the entries or the snooze clock change).
