# #433 SPA: transfer answers name the accounts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The review flow's transfer answers use the API fields shipped in #435: the 照帳單改轉帳… picker rows read 「日期 來源 → 目的 原金額 改成 銀行金額」, the one-leg note names the other account, and the 已照帳單改轉帳 toast adds 「另一個帳戶的那一題也處理了」 when the peer question was resolved too.

**Architecture:** Angular SPA only (`frontend/`). Two optional fields on the inbox models, three pure helpers in `recon-flow-view.ts`, two call-site changes in `recon-flow.ts`, OpenSpec text. No API change (#435 is live at 2a06f39f).

**Tech Stack:** Angular 21 standalone + signals, Vitest (`npx ng test --watch=false`), OpenSpec CLI 1.15.

**Spec:** issue #433 + owner rulings 2026-11-25 (below) + `openspec/changes/add-notification-center/` (design D5 Transfers bullet, spec requirement "Transfer and amount answers").

## Global Constraints

- Owner copy (exact):
  - Leg picker row, counter known: 「<YYYY/MM/DD> <來源> → <目的> <|原金額|> 改成 <|銀行金額|>」, amounts without sign (the arrow says the direction). Owner choice 2026-11-25 (AskUserQuestion: 帳戶取代名稱，金額用「改成」).
  - One-leg note, counter known: 「會同時改<counter>的那一筆。兩邊金額都改成 <|bank|>；日期各自跟自己的帳單，另一邊沒有帳單就不動。」 (the E1 sentence with 另一個帳戶 replaced by the name).
  - Toast after `adjust_transfer` when `peer_case_id` is set: 「已照帳單改轉帳。另一個帳戶的那一題也處理了」; otherwise 「已照帳單改轉帳」 (owner choice: 句號接在後面).
- Leg picker heading/sub unchanged: 「哪一筆是這筆轉帳？」 / 「兩邊一起改金額，日期跟各自的帳單」. 改金額… picker unchanged (「日期 名稱 原金額 → 銀行金額」, signed). 配成轉帳… unchanged.
- Fallbacks (my call): a leg without `counter_account_name`, or an item without `legs` (older payload) → row 「<YYYY/MM/DD> <title> <|原金額|> 改成 <|銀行金額|>」, note keeps 「另一個帳戶」; a response without `peer_case_id` → 「已照帳單改轉帳」.
- Every SPA build carries PORTFOLIO_SPA_TOKEN (copied `environment.ts`); never print it. Test data fictional.

## Code facts (verified at origin/main 2a06f39f)

- API (#435): `item.transfer.adjust_transfer.legs: [{entry_id, account_name, counter_account_name: str|null, direction: "out"|"in"}]`, same order as `candidate_entry_ids` (`services/accounting-service/app/schemas/inbox.py`, `inbox_service._transfer_leg_out`); `ActionOut.peer_case_id: int|null` (`routers/reconciliation._action_out`). For `out` 來源 → 目的 = account → counter; for `in` = counter → account.
- `frontend/src/app/components/accounting/inbox/inbox.model.ts:83` `adjust_transfer: { entry_id; line_id; candidate_entry_ids } | null`; `:182-202` `ReconciliationAction` (no `peer_case_id`).
- `recon-flow-view.ts:100-122` `flowNote`: one-leg sentence at `:117-119`; `:148-155` `transferLegs(item)` → the item's candidates that are legs (ambiguous only); `peerRowLabel` `:124-131` (unsigned, `slashDate`).
- `recon-flow.ts:732-743` `sendCaseAction(item, action, params, answer, text?)` → `undo.offer({answer, text, …})` with `out: ReconciliationAction`; `:745-748` `adjustTransfer` passes `'已照帳單改轉帳'`; `:809-823` `caseAnswer('adjust_transfer')` builds leg rows with `entryRows` = `${matchRowLabel(c, currency)} → ${bank}` (bank signed).
- `match-sheet/match-sheet.ts` `matchRowLabel(candidate, currency)` = `${slashDate(date)} ${title} ${formatMoney(amount, currency)}`.

## Review Focus

1. Leg rows when `legs` is absent or shorter than the candidates (older cached payload): no crash, title fallback.
2. Direction mapping: `in` leg on the card (bank → card) reads 「測試銀行 → 測試銀行卡」; `out` reads the reverse.
3. Note when the item's own `note` exists: named sentence first, item note on the next line (as E1).
4. Toast uses the response (not the request): a replayed 200 with `peer_case_id` shows the long text too.
5. 改金額… and 配成轉帳… rows/toasts unchanged (regression).

---

### Task 1: Models and pure helpers

**Files:** Modify `frontend/src/app/components/accounting/inbox/inbox.model.ts`, `frontend/src/app/components/notifications/recon-flow/recon-flow-view.ts`; Test `recon-flow-view.spec.ts`.

**Interfaces (produces):**
- `InboxTransferLeg { entry_id: number; account_name: string; counter_account_name: string | null; direction: 'out' | 'in' }`; `adjust_transfer.legs?: InboxTransferLeg[]`; `ReconciliationAction.peer_case_id?: number | null`.
- `transferLeg(item, entryId): InboxTransferLeg | undefined`.
- `legRowLabel(candidate: InboxCandidate, leg: InboxTransferLeg | undefined, bank: string | number, currency: string): string` (bank = the line amount; both amounts shown with `Math.abs`).
- `flowNote` one-leg sentence names `transferLeg(item, entry_id)?.counter_account_name` when set.
- `adjustTransferToast(out: ReconciliationAction): string`.

- [ ] **Step 1: Tests** (`recon-flow-view.spec.ts`): `legRowLabel` in-leg with counter → `'2026/09/30 測試銀行 → 測試銀行卡 $19,970 改成 $20,000'`; out-leg → `'2026/09/30 測試銀行卡 → 測試銀行 …'`; no counter → `'2026/09/30 測試轉帳 甲 $19,970 改成 $20,000'`; leg undefined → same title form. `flowNote` one-leg with counter 測試銀行 → `'會同時改測試銀行的那一筆。兩邊金額都改成 $20,000；日期各自跟自己的帳單，另一邊沒有帳單就不動。'`; with item note → that + `\n` + note; counter null → E1 sentence unchanged. `adjustTransferToast({peer_case_id: 7})` → long text; `null` / missing → `'已照帳單改轉帳'`.
- [ ] **Step 2:** run `npx ng test --watch=false --include src/app/components/notifications/recon-flow/recon-flow-view.spec.ts` → new tests FAIL (helpers missing).
- [ ] **Step 3:** implement; existing E1 tests unchanged.
- [ ] **Step 4:** same command → PASS.
- [ ] **Step 5: Commit** `feat(notifications): transfer helpers name the accounts (#433)`.

### Task 2: Flow wiring

**Files:** Modify `recon-flow.ts`; Test `recon-flow.spec.ts`.

- [ ] **Step 1: Tests:** ambiguous item with `legs` → pick sheet rows exactly `legRowLabel` strings (sub unchanged); one-leg item with counter → note text in the note box; posting `adjust_transfer` with a response carrying `peer_case_id: 9` → toast `'已照帳單改轉帳。另一個帳戶的那一題也處理了'` with 復原; `peer_case_id: null` → `'已照帳單改轉帳'`; 改金額… rows and 配成轉帳 toast unchanged (existing tests stay green).
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** `sendCaseAction`'s `text` becomes `string | ((out: ReconciliationAction) => string)`, resolved inside the submit callback; `adjustTransfer` passes `adjustTransferToast`; `caseAnswer('adjust_transfer')` maps `transferLegs(item)` through `legRowLabel(c, transferLeg(item, c.entry_id), item.line?.amount, currency)`.
- [ ] **Step 4:** flow specs + full suite (`npx ng test --watch=false`) + `tsc --noEmit -p tsconfig.app.json` and `tsconfig.spec.json` → green.
- [ ] **Step 5: Commit** `feat(notifications): leg picker shows 來源 → 目的, note and toast name the other side (#433)`.

### Task 3: OpenSpec, evidence

- [ ] **Step 1:** OpenSpec `add-notification-center`: design D5 Transfers bullet (rows, named note, toast), spec "Transfer and amount answers" scenarios "Transfer leg known" (named note, toast with the peer sentence when `peer_case_id`) and "Ambiguous transfer leg" (row format), tasks.md note under 5.1 (#433 follow-up done). Requirement ≤ 500 chars; `openspec validate add-notification-center --strict` valid.
- [ ] **Step 2: Commit** `docs(openspec): #433 transfer answers name the accounts`.
- [ ] **Step 3: Evidence** (kit `~/reports/home-hub-playwright-stubs/notif_e1_*`, fictional data, clock fixed): stubs gain `legs` and `peer_case_id`; three owner screenshots at 390/1100/1440 light+dark — leg picker with 「來源 → 目的 … 改成 …」, one-leg note naming 測試銀行, toast 「已照帳單改轉帳。另一個帳戶的那一題也處理了」 — plus probes for the fallbacks (no counter, `peer_case_id` null). Gallery for the owner.

## Plan review fold (AGENT-598, PASS-with-minor, 2026-11-25)

1. View tests pin the live card leg in full: account 測試銀行卡, counter 測試銀行, `out`, 19970 / 20000 → `'2026/09/30 測試銀行卡 → 測試銀行 $19,970 改成 $20,000'`; the flow's named-note test asserts the whole sentence (and the `\n` + item note form in the view spec).
2. The two E1 leg-picker tests without `legs` now expect the title fallback (`'2026/09/05 測試轉帳 甲 $19,970 改成 $20,000'`, …); a flow test with `legs` shorter than the candidates pins one named row + one title row; the adjust_transfer options use `legRowLabel` only and `entryRows` stays for 改金額… (its signed `→` rows unchanged, existing test).
3. `adjustTransferToast` is pinned for `{replayed: true, peer_case_id}` (long text), and the flow flushes a replayed 200 with `peer_case_id` → long toast with 復原.
