# Inbox part-of hint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The inbox names the settlement entry an unmatched bank line may be part of, on its own card without a rule offer (#392).

**Architecture:** The inbox item gains `part_of`, filled from the #389 predicate through a small public helper and the per-build cache. The SPA words it in `questionText`.

**Tech Stack:** Python 3.13 / FastAPI / pytest; Angular 21 / Vitest.

**Spec:** `docs/superpowers/specs/2026-11-19-inbox-part-of-hint-design.md`

## Global Constraints

- `part_of` is a list of `InboxEntry` (`id`, `date`, `title`, `amount`, `account_id`), `[]` unless the item is a `bank_only` question on a bank statement with a part-of-a-settlement twin.
- A non-empty `part_of` forces `group_key = None` and `rule_hint = None`. `actions` are untouched.
- One claimed-entry snapshot per inbox build; one population read per statement.
- SPA text, exact: `可能是「{title} {amount}」的一部分 — 先把那筆改成分筆，別直接記為收入` (inflow) / `…別直接記為支出` (outflow); several entries: `可能是「{title} {amount}」等 {n} 筆的一部分 — …`.
- Never run `npm run config`, `set-env.js`, `npm run build` or `npm start` in the worktree. Frontend tests: `npx ng test --watch=false` from `frontend/`.
- Backend test recipe as in the #389 plan.

## Review Focus

- **Older API.** The SPA must not break when `part_of` is absent. Covered by the "field absent" case in `inbox-view.spec.ts`.
- **Counts.** Ungrouping changes how many cards the SPA builds, not `counts.ask` (items are counted). Covered by the API test's `counts.ask == 2`.
- **Card statements.** No card line is eligible. Covered by the API test's card case.
- **Batch apply.** `_check_batch` groups by what the client sends, not by `group_key`; a client could still batch two held cases. Not tested; the reviewer checks nothing on the server relies on `group_key` for safety.
- **Outflow wording.** A withdrawal beside a larger unclaimed repayment says 支出. Covered by the SPA test.

---

### Task 1: API — `part_of` on the inbox item

**Files:**
- Modify: `services/accounting-service/app/services/stand_ins.py` (after `_partial_settlement_twins`)
- Modify: `services/accounting-service/app/services/inbox_service.py` (`_item` ~:375)
- Modify: `services/accounting-service/app/schemas/inbox.py` (`InboxItem` ~:95)
- Test: `services/accounting-service/tests/integration/test_inbox_api.py`

**Interfaces:**
- Produces: `stand_ins.settlement_part_twins(db, statement, line, rules, cache: dict) -> list[int]` (ids ascending; `cache` holds the claimed snapshot and the per-statement population); inbox item key `part_of`.

- [ ] **Step 1: Write the failing tests** `test_bank_line_names_the_settlement_entry_it_may_be_part_of`, `test_without_the_receipt_equal_lines_merge_as_before`, `test_a_card_bank_only_item_has_no_part_of` with the assertions of the spec's Tests section.
- [ ] **Step 2: Run them.** Expected: FAIL (`part_of` missing; items share a `group_key`).
- [ ] **Step 3: Implement** the helper, the `_item` change and the schema field.
- [ ] **Step 4: Run `tests/integration/test_inbox_api.py tests/integration/test_einvoice_inbox.py`.** Expected: all pass.
- [ ] **Step 5: Commit** `feat(inbox): name the settlement entry a bank line may be part of (#392)`.

### Task 2: SPA — word the hint

**Files:**
- Modify: `frontend/src/app/components/accounting/inbox/inbox.model.ts`, `inbox-view.ts` (`questionText`)
- Test: `frontend/src/app/components/accounting/inbox/inbox-view.spec.ts`

**Interfaces:**
- Consumes: item key `part_of` (optional) from Task 1.

- [ ] **Step 1: Write the failing tests** in the `questionText` describe: inflow, outflow, several entries, field absent.
- [ ] **Step 2: Run the inbox specs.** Expected: the three hint cases FAIL.
- [ ] **Step 3: Implement** the model field and the `bank_only` branch.
- [ ] **Step 4: Run the inbox specs, then the whole frontend suite and `npx tsc --noEmit -p tsconfig.app.json`.** Expected: all pass.
- [ ] **Step 5: Commit** `feat(inbox): say when a bank line may be part of a settlement entry (#392)`.
