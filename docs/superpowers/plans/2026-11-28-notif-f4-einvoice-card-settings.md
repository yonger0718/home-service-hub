# Notification center F4: entry invoice card, 記帳設定 › 發票, inbox removal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship OpenSpec tasks 6.5 (D12 entry invoice card and the 記帳設定 › 發票 section) and 6.6 (remove the inbox panel's 發票待對帳 section and the `ask + einvoice_ask` sums), with 6.7 tests, so 通知 owns e-invoice work end to end.

**Architecture:** One small API addition: the entry's invoice gains `recorded_amount`, the amount the linked entry or group records in the invoice's terms, computed with the matcher's own net rule. The SPA uses it for the match chip. Everything else is SPA: a restyled `app-entry-einvoice-card`, a reworked 發票 block in `accounting-settings`, and the removal of `app-einvoice-section` from the inbox panel. One PR (API change is additive and the SPA tolerates its absence), API deployed first.

**Tech Stack:** FastAPI + SQLAlchemy (accounting-service), Angular 21 signals, Vitest via `npx ng test`.

**Spec:** `openspec/changes/add-notification-center/` design D12; `specs/frontend-accounting-ledger/spec.md` (Entry invoice card, Entry invoice card body, E-invoice settings section, REMOVED Reminder bell count); `specs/accounting-einvoices/spec.md` (E-invoice preferences). Artboard: `design_handoff_v3/design_refs/InvoiceSpec.dc.html`.

## Global Constraints

- Tests use fictional data only (sellers 測試…, numbers AB0000000N, tax ids 0000000N); no owner data anywhere.
- Never log invoice payloads (matcher module rule).
- Copy (verbatim): 發票 / 發票 · 整組; ✓ 金額相符 / 差 $N; 自動連結 / 手動連結; ⋯ menu 換一張發票 / 解除連結 with 「之後不會再自動連回」; 「… 另外 N 項」; 合計 N 項; 看 N 項明細; settings 提醒沒有帳的發票, 對帳日期範圍 (±0–7 天), 同一家標幾次後問要不要不提醒 (1–10 次), 不提醒的商家, 移除, 已標成不用記帳, 看全部 N 張, 復原, 你標的 / 規則自動標記.
- The unlinked row stays exactly as `Calendar entry drawer invoice link` (#408) defines it.
- Prettier is not applied repo-wide; do not reformat untouched code.

## Review Focus

1. Foreign-currency entry linked by original amount: the chip compares the original amount, never the TWD amount.
2. Mixed-currency group: no match chip (`recorded_amount` null), the card still renders.
3. 換一張發票 when the picker then finds nothing or fails: the entry is left unlinked and the card says so; the old invoice is in 已標成不用記帳 with 復原.
4. Stepper bounds and rapid taps: never PATCH outside 0–7 / 1–10; one PATCH in flight per field, the last value wins; a response merges only the field it sent (plus the read-only status and the higher `version`), so answers arriving out of order across fields never undo each other; a failed PATCH reverts that field only.
5. Removing the inbox section must not change the 提醒中心 counts other than dropping `einvoice_ask` from the 要你決定 sum.

---

### Task 1: API `recorded_amount` on the entry's invoice

**Files:**
- Modify: `services/accounting-service/app/services/einvoice_read.py` (`entry_einvoice`)
- Modify: `services/accounting-service/app/services/einvoice_matcher.py` (new `linked_recorded_amount`)
- Modify: `services/accounting-service/app/schemas/ledger.py` (`EntryEinvoiceOut.recorded_amount: Decimal | None`)
- Test: `services/accounting-service/tests/integration/test_einvoice_entry_read.py`
- Spec: `specs/accounting-einvoices/spec.md` new requirement 「Entry invoice recorded amount」.

**Interfaces:**
- Produces: `linked_recorded_amount(db, *, entry_id: int | None, group_id: int | None) -> Decimal | None`. Entry: `abs(original_amount)` when `original_currency` is set and not the main currency (matcher :172), else `-(amount + fee/discount children)` (the matcher's `net`, sign flipped to invoice terms). Group: `-(sum over top-level GROUP_NET_KINDS members of amount + fee/discount children)` when those members share one currency, else `None`; other kinds count toward neither the net nor the currency check. Unlike `entry_target` / `group_target` it does not filter out linked targets.
- Boundary: the chip states numeric equality only, not matcher eligibility. An exact-number link (§7.3) may differ in amount (自動連結 + 差 $N is legal); a whole-number foreign original amount may equal an invoice the integral rule would not take. A mixed-currency group is a display fallback for data `group_target` already refuses (a later edit made it mixed).
- `EntryEinvoiceOut.recorded_amount` (JSON string, as other Decimals).

- [x] Tests (fictional): plain entry; fee + discount children counted, a reward child not; foreign original amount; split group net with a fee child on every member's read; mixed-currency group → `null`; `original_currency` = main uses the net; a group member outside the net kinds (USD transfer) changes neither the net nor the mixed check. Run, see them fail (the last two are regression pins added after review).
- Existing F1 coverage kept as regression (not re-written): preferences read / partial update / out-of-range 422 and link status (`tests/integration/test_einvoice_actions.py:398`, `:420`, `:434–446`, `:449`).
- [ ] Implement with `_child_sums` and `GROUP_NET_KINDS` (reuse, no copy of the rule).
- [ ] Run the e-invoice tests, then the full accounting suite (scratchpad runner; never two at once).
- [ ] Commit `feat(accounting): recorded_amount on the entry's e-invoice (D12 match chip)`.

### Task 2: Entry invoice card (D12)

**Files:**
- Modify: `frontend/src/app/components/accounting/entry-detail/einvoice-card.{ts,html}` (styles move to `einvoice-card.scss`)
- Modify: `frontend/src/app/models/accounting.model.ts` (`EntryEinvoice.recorded_amount?: string | null`)
- Test: `entry-detail/einvoice-card.spec.ts`

**Behaviour:**
- Header: title 發票 (entry) / 發票 · 整組 (group); match chip ✓ 金額相符 (success) when `recorded_amount` equals `total_amount`, 差 $N (warning ink, N = |difference|, `invoiceAmount` format) otherwise, none when `recorded_amount` is null/absent; source chip 自動連結 / 手動連結; ⋯ button (`aria-label="發票動作"`, `aria-haspopup="menu"`) opening a menu with 換一張發票 and 解除連結 (sub line 「之後不會再自動連回」). Esc and outside click close it; focus returns to ⋯.
- Entry body: 「seller · M/D · number」; items: the first three rows, then 「… 另外 N 項」 with the sum of the rest; total row 合計 N 項 $total.
- Group body: 「seller · M/D · N 項 · $total（這組記了 $recorded）」 (the parenthesis only when they differ and recorded is known); 看 N 項明細 › toggles the full item list (all N rows, then 合計; no 「另外」 summary).
- 解除連結: `unlink` as today (the card reloads to the unlinked row). 換一張發票: `unlink`, then, once the reloaded detail has no invoice, opens the picker automatically. The pending step is `{ entryId, reopen }`, set by the menu item for the current entry and consumed by the next detail change: only when that change is the same entry with no invoice does it act; any other entry, or a destroy, drops it. A failed unlink shows the existing inline error, opens nothing and returns focus to ⋯.
- Focus: the menu opens with focus on its first item; ↑/↓ move between items; Esc closes it, is marked handled (`preventDefault` + `stopPropagation`, so the drawer / sheet Esc does not also fire) and returns focus to ⋯; an outside click closes it without moving focus. After a successful menu unlink ⋯ is gone: focus goes to the first picker choice (換一張) or to 連結發票… (解除連結, or a failed picker read).
- 換一張 is not an atomic swap: unlink dismisses the old invoice (owner); the picker excludes only void / linked invoices, so the old one may be listed again and can be picked back. Leaving the picker (or an empty / failed one) leaves the entry unlinked; the old invoice is in 已標成不用記帳 with 復原. A detail reload that fails clears the detail (entry-detail), so the card is gone and nothing resumes.
- Unlinked: unchanged (the dashed drawer row with the ±7 day hint; the plain 連結發票… button on the full page).

- [ ] Tests: auto entry match (✓ 金額相符, 自動連結, 3 items, 「… 另外 4 項 $701」, 合計 7 項 $1,286); group off by $3 (發票 · 整組, 差 $3, 手動連結, 「（這組記了 $2,460）」, 看 12 項明細 toggles 12 rows); null recorded → no chip; ≤ 3 items → no 另外 row; menu opens/closes (Esc, outside, focus back); ↑/↓; Esc not seen by a document listener; 解除連結 posts unlink and focuses 連結發票…; 換一張發票 posts unlink then opens the picker after the reload and focuses its first choice; empty picker / failed picker (focus 連結發票…); 換一張 then another entry opens no picker; failed unlink → error, no picker, focus ⋯; unlinked row unchanged (existing tests stay green).
- [ ] Implement; tokens only (`--app-success` for ✓, `--app-banner-warn` ink for 差 $N, `--app-text-muted`).
- [ ] Commit `feat(accounting): entry invoice card — match/source chips, ⋯ menu, item summary (D12)`.

### Task 3: 記帳設定 › 發票 (D12)

**Files:**
- Modify: `frontend/src/app/components/accounting/accounting-settings/accounting-settings.{ts,html,scss}`
- Modify: `frontend/src/app/components/accounting/inbox/einvoice.service.ts`: generalise `setRemindUnmatched(on)` to `patchPreferences(change: Partial<Pick<EinvoicePreferences, 'remind_unmatched' | 'date_window_days' | 'mute_prompt_after'>>)` (keeps the `changed()` bump); `EinvoicePreferences` model gains the F1 fields if missing
- Test: `accounting-settings.spec.ts`

**Behaviour (section shown as today: when enabled, or when the read failed):**
- Status line: 「電子發票對帳已開啟 · 最近匯入 M/D（自動連結 N）」; 「還沒有匯入」 when `last_import_at` is null. (The artboard's imported-invoice count has no API field; ruling: omitted.)
- 提醒沒有帳的發票 switch (`remind_unmatched`) with hint 「關掉後「有發票沒有帳」不再出現；對不太上的發票照樣會問」.
- Steppers (`app-stepper`, shared since #349): 對帳日期範圍 `±N 天`, 0–7, hint 「發票日期前後幾天內的帳都算候選（0–7）」; 同一家標幾次後問要不要不提醒 `N 次`, 1–10, hint 「標「不用記帳」到這個次數，下次就會出現「這家以後都不提醒」（1–10）」. Each change PATCHes `{field: value}` (the endpoint merges any subset and bumps `version` itself; it takes no `version`). One PATCH in flight per field: taps while it runs update the shown value and send once more with the last value; the response replaces the stored preferences. An error shows `einvoiceErrorText` inline and reverts to the last server value.
- 不提醒的商家 · N: name (or 統編 when no name), meta 「統編 X · M/D 起不提醒」, 移除 (`DELETE /mutes/{tax_id}`; toast 「已恢復提醒：name」). (The artboard's 「收起 N 張」 has no API field; ruling: omitted.)
- 已標成不用記帳 · N (N = rows returned, up to the API's 100): the newest three, then 看全部 N 張 › expanding the list in place; each row 「seller · $amount」, meta 「M/D · first item[、second] · 你標的 | 規則自動標記」, 復原 (`undismiss`, row leaves, toast 「已復原：seller $amount」).
- The section anchor id `einvoice-settings` so 有發票沒有帳 can deep-link.

- [x] Tests: status line both ways; switch PATCH payload; stepper PATCH `date_window_days: 5` from 3 (spec scenario) and `mute_prompt_after`; bounds (− disabled at 0 / 1, + at 7 / 10); in-flight coalescing (two quick + taps → two PATCHes at most, the last with the final value; the first answer leaves the pending draft shown); cross-field answers in reverse order; one field failing while the other saves; a failed PATCH reverts; mute 移除; dismissed top three + 看全部 + 復原; 規則自動標記 label; read failure line.
- [ ] Implement; commit `feat(accounting): 記帳設定 › 發票 — status, steppers, mutes, dismissed list (D12)`.

### Task 4: 有發票沒有帳 footer on

**Files:** `frontend/src/app/components/notifications/einvoice-unmatched/einvoice-unmatched.{ts,html,spec.ts}`

- `settingsLink = true`; the link goes to `/accounting/settings` with fragment `einvoice-settings`; still hidden while reminders are off. Test: shown with the right href (path + fragment); hidden when off.
- [ ] Commit `feat(notifications): 有發票沒有帳 footer links to 記帳設定 › 發票`.

### Task 5: Remove the inbox panel's e-invoice section and the bell sums (6.6)

**Files:**
- Delete: `frontend/src/app/components/accounting/inbox/einvoice-section/einvoice-section.{ts,html,scss,spec.ts}`; move `einvoiceErrorText` to `inbox/einvoice-format.ts` and update its four importers.
- Modify: `inbox/inbox-panel/inbox-panel.{ts,html}` (no `app-einvoice-section`, no `einvoiceAsk`, the preview overflow without e-invoices, 要你決定 = `counts.ask`); `reminders/reminders.ts` (`inboxBellCount` call sites → `counts.ask`, and the 待處理 empty-state sum drops `einvoices.length` and its comment); delete `inboxBellCount` from `inbox-view.ts` and its test + import in `inbox-view.spec.ts`; `inbox.model.ts` doc comment. Keep the 通知 consumers of `counts.einvoice_ask` (`sources/recon.ts`, `einvoice-flow.ts`, `einvoice-unmatched.ts`) and the calendar's `einvoices` read.
- Tests: inbox-panel / reminders specs updated (e-invoices no longer counted or listed there); the panel renders no e-invoice card even when `einvoices` has ask items; 待處理 with only e-invoice questions, or only unmatched reminders, shows the empty state; proposed-only stays non-empty.

- [ ] Commit `refactor(accounting): 通知 owns e-invoice questions — drop the inbox section and bell sums (6.6)`.

### Task 6: OpenSpec, full suites, screenshots

- [ ] Tick 6.5 / 6.6 / 6.7 with notes; add the `recorded_amount` requirement; `openspec validate add-notification-center --strict`.
- [ ] Frontend suite + production build; accounting suite.
- [ ] Playwright stub screenshots (fictional): card auto/group/no-chip, menu open, settings section (1440 / 390, light / dark), stepper error, dismissed expanded, inbox panel before/after, 有發票沒有帳 footer.

## Plan review AGENT-635 (CHANGES) — resolutions

1. P2 cross-field PATCH order → Task 3 behaviour + tests above (merge only the sent field; per-field draft / in-flight; failure reverts that field; a same-field queued value is re-sent).
2. P2 reminders 待處理 empty state → Task 5 drops `einvoices.length`; tests assert the empty state.
3. P2 換一張 lifecycle / focus → Task 2 `{ entryId, reopen }`, focus contract, handled Esc, non-atomic swap note, tests.
4. P3 `inboxBellCount` test cleanup → Task 5 file list.
- recorded_amount boundary written into Task 1 (numeric equality, not eligibility); extra tests; F1 regression refs.
- Group expand: all rows (Task 2).

