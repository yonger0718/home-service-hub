# Updates keep a stored fee / discount unless the owner confirms (#396) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An update that would delete a stored fee / discount child is refused unless `?drop_children=true`, and that parameter (like `?cascade=true` of #390) needs the `admin` scope.

**Architecture:** `sync_children` refuses the drop before it writes; the four update services pass a `drop_children` flag down to it. The routers check `admin` when a confirming parameter is true. The SPA remembers which child kinds each loaded entry had and sends the flag only when the form drops one of them.

**Tech Stack:** FastAPI, SQLAlchemy, pytest (accounting service); Angular, Vitest (frontend).

**Spec:** `docs/superpowers/specs/2026-11-19-update-keeps-children-design.md`

## Global Constraints

- What an allowed update does to the children is unchanged (#313).
- A stored child of a kind the payload carries is never a drop (also several rows becoming one).
- Removing a member from a split, `DELETE /splits/{id}` and every `POST` are unchanged.
- Refusal: `409`, code `children_unconfirmed`, error body key `message`. The detail names each child as `entry <id> (<kind>) of entry <parent id>` and does not contain the parameter name.
- Lock: a true `drop_children` or `cascade` needs `auth.require("admin")` (passes for `admin`, `legacy`, or unconfigured scopes); otherwise `403` before the service runs.
- The `cascade_unconfirmed` detail no longer contains `=true`.
- Fixtures are fictional.

## Review Focus

- A refused update leaves nothing behind: the entry's own columns, rule links, reward rows, the other transfer leg, the other split members, a new split group (convert). (Task 1 tests assert the unchanged state from a fresh read.)
- A split member that is unchanged or changed only in its meta fields while the payload omits its stored fee: is the omission seen as a change at all, and is it refused? (Task 1 test.)
- An entry with a fee and a discount where only one is omitted: the detail names only that one. (Task 1 test.)
- A `write`-only caller with `drop_children=false` written out, or `cascade=false`: no `403`. (Task 2 test.)
- The form drops a fee it loaded, the save fails for another reason, the owner saves again: the flag is still sent. A copy of an entry (new record) never sends it. (Task 3 tests.)

---

### Task 1: API refusal on the four update routes

**Files:**
- Modify: `services/accounting-service/app/services/entry_write_service.py` (`sync_children`, `apply_prepared_update`, `update_entry`)
- Modify: `services/accounting-service/app/services/transfer_service.py` (`_write_leg_extras`, `update_transfer`)
- Modify: `services/accounting-service/app/services/split_service.py` (`update_split`, `convert_to_split`)
- Modify: `services/accounting-service/app/routers/entries.py` (`put_entry`, `put_entry_split`), `routers/transfers.py` (`put_transfer`), `routers/splits.py` (`put_split`)
- Test: `services/accounting-service/tests/integration/test_update_keeps_children.py` (new)
- Modify (mechanical): existing tests that drop a child through an update

**Interfaces:**
- Produces:
  - `entry_write_service.CHILDREN_UNCONFIRMED = "children_unconfirmed"`
  - `sync_children(db, parent, fee, discount, *, source="manual", drop_children: bool = False) -> None`
  - `apply_prepared_update(db, entry, prepared, *, drop_children: bool = False)`
  - `update_entry(db, entry_id, payload, *, http_get=None, drop_children: bool = False)`
  - `transfer_service.update_transfer(db, group_id, payload, *, drop_children: bool = False)`
  - `split_service.update_split(db, group_id, payload, *, http_get=None, drop_children: bool = False)`
  - `split_service.convert_to_split(db, entry_id, payload, *, http_get=None, source=..., drop_children: bool = False)`
  - query parameter `drop_children: bool = False` on `PUT /entries/{entry_id}`, `PUT /entries/{entry_id}/split`, `PUT /transfers/{group_id}`, `PUT /splits/{group_id}`

- [ ] **Step 1: Write the failing tests** in the new file (helpers of `test_entry_writes.py` / `test_transfers.py` / `test_splits.py` for payloads):
  - `test_put_without_the_stored_fee_is_refused_until_confirmed`: expense −100 named `早餐` with a fee −15; `PUT` with name `午餐` and no `fee` → `409`, `message` starts with `children_unconfirmed: ` and contains `entry <fee id> (fee) of entry <entry id>`; fresh read: name still `早餐`, the fee row exists. Same request with `?drop_children=true` → `200`, name `午餐`, no child row.
  - `test_put_with_the_fee_sent_back_keeps_its_row`: same entry, payload with `fee: {"amount": "15"}` → `200`, the fee row has the same id.
  - `test_put_with_another_fee_amount_is_an_edit`: `fee: {"amount": "20"}` → `200` without the flag, same row id, amount `-20`.
  - `test_put_names_only_the_omitted_kind`: fee and discount stored, payload with the fee only → `409`; the message contains `(discount)` and not `(fee)`.
  - `test_put_without_children_on_a_childless_entry_passes`: `200` without the flag.
  - `test_transfer_put_without_the_stored_leg_fee_is_refused`: transfer with `out_fee` 10; `PUT /transfers/{gid}` with another amount and no `out_fee` → `409`; both legs keep their amounts and the fee exists; with the flag → `200`, fee gone.
  - `test_split_put_without_a_member_fee_is_refused`: split of two members, the first with a fee; `PUT /splits/{gid}` changing the first member's amount without its fee → `409`, both members unchanged.
  - `test_split_put_omitting_the_fee_of_an_otherwise_unchanged_member_is_refused`: the same split, the payload repeats every stored value of the first member except its fee → `409`.
  - `test_split_put_removing_a_member_still_deletes_its_fee`: three members, the payload omits the member that has the fee → `200` without the flag; the member and its fee are gone.
  - `test_convert_to_split_without_the_anchor_fee_is_refused`: single expense with a fee; `PUT /entries/{id}/split` whose anchor member has no `fee` → `409`; the entry has no `group_id` and no `entry_group` row was added.

- [ ] **Step 2: Run them, expect FAIL** (`200` where `409` is expected; the pass-through cases already pass).

  Run (from `services/accounting-service`, DB + OTEL env loaded): `PYTHONPATH=$PWD/../shared-python-lib .venv/bin/python -m pytest tests/integration/test_update_keeps_children.py -q -p no:cacheprovider -W ignore::DeprecationWarning`

- [ ] **Step 3: Implement.**
  - `sync_children`: after `stored` is read and before any write, collect the stored rows whose kind has no payload child (`wanted.get(kind) is None`, which also covers a kind outside fee / discount); when there are any and `not drop_children`, raise `CodedConflictError(CHILDREN_UNCONFIRMED, …)`. Detail: `updating entry <parent id> would delete entry <id> (<kind>) of entry <parent id>[, …]; send the fee / discount back, or the owner confirms the drop in the app`.
  - Pass `drop_children` from each router through its service to `sync_children` (signatures above). `_write_leg_extras` passes it only on the update branch (`write_children` takes no such argument).

- [ ] **Step 4: Run Step 1's tests, expect PASS.**

- [ ] **Step 5: Run every test file that calls an update route; add `?drop_children=true` where a test means to drop a child** (the test's name or comment says so), nowhere else.

  Run: `… -m pytest tests/integration -q … -k "put or update or split or transfer or children or sync"` then the files it reports.
  Expected: all pass. Ledger the count of changed requests.

- [ ] **Step 6: Commit** `fix(entries): an update does not drop a stored fee / discount unless confirmed (#396)`.

### Task 2: The confirmation needs `admin`

**Files:**
- Modify: `services/accounting-service/app/routers/entries.py` (`put_entry`, `put_entry_split`, `remove_entry`), `routers/transfers.py`, `routers/splits.py`
- Modify: `services/accounting-service/app/auth.py` (one helper)
- Modify: `services/accounting-service/app/services/entry_write_service.py` (the `cascade_unconfirmed` detail)
- Modify: `services/accounting-service/README.md` (the Writes lines of the four routes and of DELETE)
- Test: `services/accounting-service/tests/integration/test_confirmation_scope.py` (new); `tests/integration/test_entry_writes.py` (the #390 message assertions)

**Interfaces:**
- Consumes: Task 1's query parameters; `auth.require`, `auth.TOKENS_ENV`, `auth.SCOPES_ENV`.
- Produces: `auth.require_admin_to_confirm(request: Request, confirmed: bool) -> None` — returns when `confirmed` is false, else runs `require("admin")(request)`.

- [ ] **Step 1: Write the failing tests.** Fixture `scoped` as in `test_accounts_api.py`: tokens `adm:adm-token,wr:wr-token,spa:spa-token`, scopes `adm=read,write,admin; wr=read,write; spa=legacy`.
  - `test_write_scope_cannot_confirm_a_drop` (parametrised over the four routes, each on an entry / transfer / split that has a stored fee, the payload omitting it, `?drop_children=true`, the `wr` token) → `403`; the fee still exists.
  - `test_write_scope_cannot_confirm_a_cascade`: #390's foreign-child shape, `DELETE …?cascade=true` with `wr` → `403`; both rows exist.
  - `test_write_scope_still_updates_and_deletes_without_the_parameter`: `wr` → `PUT` with the fee sent back `200`; `PUT …?drop_children=false` `200`; `DELETE` of a plain entry `200`; `DELETE …?cascade=false` of a plain entry `200`.
  - `test_admin_and_legacy_scopes_confirm`: `adm` and `spa` → the drop `200`, the cascade `200`.
  - `test_refusal_details_do_not_name_the_parameter`: the `children_unconfirmed` and the `cascade_unconfirmed` messages contain neither `=true` nor `drop_children` / `cascade=`.

- [ ] **Step 2: Run, expect FAIL** (`200` where `403` is expected; the `cascade_unconfirmed` text still has `?cascade=true`).

  Run: `… -m pytest tests/integration/test_confirmation_scope.py -q …`

- [ ] **Step 3: Implement.** Each of the five handlers takes `request: Request` and calls `require_admin_to_confirm(request, <flag>)` as its first statement. `cascade_unconfirmed` detail ends `…: <list>; the owner confirms this delete in the app`. README: the five route lines say the parameter, the `409` code and that the parameter needs `admin`.

- [ ] **Step 4: Run Step 1's tests and `tests/integration/test_entry_writes.py`, expect PASS** (adjust the #390 assertion that checked the old wording, if one did).

- [ ] **Step 5: Commit** `fix(auth): confirming a child drop or a delete cascade needs the admin scope (#396, #390)`.

### Task 3: SPA sends the confirmation only for a child it loaded

**Files:**
- Modify: `frontend/src/app/services/accounting.service.ts` (`updateEntry`, `updateTransfer`, `updateSplit`, `convertEntryToSplit`)
- Modify: `frontend/src/app/components/accounting/entry-form/split-draft.ts` (`ChildDraft.loaded`), `split-load.ts`, `split-save.ts` (`EntrySavePlan`, `planEntrySave`, `executeEntrySave`), `entry-form.ts` (the single-entry load)
- Modify: `frontend/src/app/components/accounting/transfer-panel/transfer-panel.ts` (`applyEdit`, `submit`, `reset`)
- Modify: `frontend/src/app/components/accounting/http-errors.ts`
- Test: the specs beside each (`accounting.service.spec.ts`, `split-save.spec.ts`, `entry-form.spec.ts` or the nearest existing spec that covers a save, `transfer-panel.spec.ts`, `http-errors.spec.ts`)

**Interfaces:**
- Consumes: `?drop_children=true` from Task 1.
- Produces:
  - `updateEntry(id, input, dropChildren = false)`, `updateTransfer(groupId, input, dropChildren = false)`, `updateSplit(groupId, input, dropChildren = false)`, `convertEntryToSplit(entryId, input, dropChildren = false)`
  - `ChildDraft.loaded.childKinds: readonly ('fee' | 'discount')[]` (the kinds the stored entry had; a copy has `loaded: null`)
  - `dropChildren: boolean` on the `update`, `update-split` and `convert-split` variants of `EntrySavePlan`: true when a draft with `loaded` lacks (`fee` / `discount` null) a kind in `loaded.childKinds`
  - `CONFLICT_MESSAGES.children_unconfirmed = '這筆記錄的手續費或折扣是在你開啟後才加上的；請重新載入後再編輯'`

- [ ] **Step 1: Write the failing tests.**
  - Service table: each of the four methods with `true` → the URL carries `?drop_children=true`; without → no query.
  - `planEntrySave`: single loaded entry with `childKinds: ['fee']` and `fee` kept → `dropChildren` false; `fee` null → true; `childKinds: []` and no fee → false; a copy (`loaded: null`, no `entryId`) → plan `create`; update-split where one loaded member lost its discount → true; a loaded member removed from the list → false; convert-split whose anchor lost its fee → true.
  - `executeEntrySave` passes `plan.dropChildren` to the service for the three variants.
  - Transfer panel: edit loaded with an out fee; fee kept → `updateTransfer(gid, body, false)`; fee cleared → `true`; no fee loaded → `false`; a new transfer → `createTransfer`.
  - `http-errors.spec.ts`: a row for `children_unconfirmed: updating entry 7 would delete entry 8 (fee) of entry 7; …`.

- [ ] **Step 2: Run, expect FAIL.**

  Run (from `frontend`): `npx ng test --watch=false --include '<each spec>'`

- [ ] **Step 3: Implement.** The kinds come from the detail's `children` at load (`split-load.ts`, and the single-entry load that builds the same draft). The transfer panel keeps the loaded kinds per side in a private field set in `applyEdit` and cleared in `reset`.

- [ ] **Step 4: Run the specs, expect PASS; `npx tsc --noEmit -p tsconfig.app.json` clean.**

- [ ] **Step 5: Commit** `fix(entry-form): confirm dropping a fee or discount the form loaded (#396)`.
