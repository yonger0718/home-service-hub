# #433 Transfer counter account + peer case in the action response — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The inbox's `transfer.adjust_transfer` names each candidate leg's own and counter account plus its direction, and the action response says which peer case a transfer action resolved, so the SPA can show 「來源帳戶 → 目的帳戶」, name the other account in the one-leg note and add 「另一個帳戶的那一題也處理了」.

**Architecture:** Two additive read-side fields in `accounting-service`; no table, no migration, no change to what any action does. (1) `inbox_service._transfer_out` adds `legs` next to `candidate_entry_ids`, built with the existing `transfer_support.peer_leg`. (2) `routers/reconciliation._action_out` adds `peer_case_id`, read from the row's stored `inverse` for `adjust_transfer` / `pair_transfer`.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, SQLAlchemy; pytest integration tests (real Postgres).

**Spec:** issue #433 (owner design review of #431, 2026-11-25) + `docs/superpowers/specs/2026-10-25-reconciliation-r6c-transfers-design.md` §7 (the `transfer` field) and §5.1 step 5 (`resolve_peer_case`, `inverse.peer_case_id`).

## Global Constraints

- Additive only: every existing key keeps its name, type and value (`candidate_entry_ids` stays; Hermes and the SPA read it).
- Test data fictional (fixture account names 銀行 / 卡, fictional amounts); no real account ids.
- No alembic, no uvicorn from the worktree. Backend tests: `PYTHONPATH=$PWD/../shared-python-lib .venv/bin/python -m pytest` with the DB / OTEL vars exported (pytest creates its own DBs).

## Code facts (verified at origin/main 496ff82c)

- `app/services/inbox_service.py:379-401` `_transfer_out`: `adjust = {"entry_id", "line_id", "candidate_entry_ids": legs}`; ambiguous → `entry_id None`, legs = candidates passing `reconciliation_actions._transfer_leg` (`__init__.py:112-115`: entry has a `transfer_group_id`); else `legs = [entry_id]`. Pair part already resolves account names with a per-call `names` cache.
- `app/schemas/inbox.py:68-72` `InboxTransferAdjust(entry_id: int | None, line_id: int, candidate_entry_ids: list[int])`, `model_config = Strict`.
- `app/services/reconciliation_actions/transfer_support.py:34-41` `peer_leg(db, leg)`: the other top-level entry of the transfer group, `None` unless exactly one.
- `transfer_actions.py:153-170`: `adjust_transfer` refuses `not_standalone` when `peer_leg` is None and `kind_sign` unless (leg, peer) kinds are (transfer_out, transfer_in) or the reverse → a leg's direction is its `kind`.
- `transfer_actions.py:212-219` (`adjust_transfer`) and `:463-468` (`pair_transfer`): `peer_case_id = resolve_peer_case(...)` (`transfer_support.py:90-114`, `None` when the peer line has no open/proposed case), stored as `effect.inverse["peer_case_id"]` in both.
- `app/routers/reconciliation.py:31-35` `_action_out(row, …)`: copies every `ActionOut.model_fields` name from the row except `replayed, label, answer, line`. Used by `POST /cases/{id}/actions` (201, or 200 when replayed), `/dismiss`, `GET /reconciliation/actions`, `/actions/{id}/revert`, `/proposals/{id}/apply`.
- `app/schemas/reconciliation.py:29-48` `ActionOut` (`extra="forbid"`).
- Tests that pin the current shape: `tests/integration/test_inbox_api.py:708-713` (one leg) and `:733-740` (ambiguous, legs `[pair.in_leg, second_in]`); fixtures `pair` / `orphan_pair` in `tests/integration/test_reconciliation_actions_transfer.py:46-64, 394-398` (bank 「銀行」, card 「卡」, owner transfer bank → card 19500).

## Review Focus

1. A leg whose group has no single peer (`peer_leg` None — three-leg group or deleted peer): `counter_account_name` is `null`, the item still renders, nothing raises.
2. Leg order: `legs[i].entry_id == candidate_entry_ids[i]` always.
3. `peer_case_id` is `null` when the transfer action found no open peer case, and on every other action (`match`, `dismiss`, a revert row).
4. Replay (the same body POSTed again: engine `idempotency_key(case.id, case.version, action, params)`, `engine.py:232-237` → 200, `replayed: true`) returns the same `peer_case_id` as the first call.
5. `GET /reconciliation/actions` rows carry the field too (same builder) — value from the stored `inverse`, so a later revert of the action does not change it.

---

### Task 1: `adjust_transfer.legs` in the inbox

**Files:**
- Modify: `services/accounting-service/app/schemas/inbox.py` (new `InboxTransferLeg`, field on `InboxTransferAdjust`)
- Modify: `services/accounting-service/app/services/inbox_service.py:379-401`
- Test: `services/accounting-service/tests/integration/test_inbox_api.py`

**Interfaces:**
- Produces: `InboxTransferLeg(entry_id: int, account_name: str, counter_account_name: str | None, direction: Literal["out", "in"])`; `InboxTransferAdjust.legs: list[InboxTransferLeg]` (same order as `candidate_entry_ids`). `direction` = `"out"` for `transfer_out`, `"in"` for `transfer_in`. `account_name` = the leg's own account; `counter_account_name` = `peer_leg(db, leg)`'s account, or `None`. The SPA reads 來源 → 目的 as `account → counter` for `out`, `counter → account` for `in`.

- [ ] **Step 1: Update the two shape tests and add the null-peer test**

`test_transfer_delta_item_is_amount_differs_with_the_transfer_field_and_no_accept_button` expects
`"legs": [{"entry_id": pair.in_leg.id, "account_name": "卡", "counter_account_name": "銀行", "direction": "in"}]` inside `adjust_transfer`.
`test_ambiguous_item_offers_adjust_transfer_over_its_transfer_leg_candidates` expects `legs` for `[pair.in_leg, second_in]`, both `{"account_name": "卡", "counter_account_name": "銀行", "direction": "in"}`, in that order.
New `test_adjust_transfer_leg_without_a_single_peer_has_no_counter_account(pair, seed, db_session, client)`: ambiguous case over `[pair.in_leg, lone]` where `lone` is a `transfer_in` on the card with its own fresh `transfer_group_id` and no other leg; expects `lone`'s leg `counter_account_name is None`, `direction == "in"`, and `pair.in_leg`'s leg unchanged.

- [ ] **Step 2: Run them — expect FAIL (no `legs` key)**

Run: `… -m pytest tests/integration/test_inbox_api.py -k "transfer" -q`
Expected: the three tests fail on the missing `legs` key.

- [ ] **Step 3: Implement**

Schema as in Interfaces. In `_transfer_out`, after `legs` ids are known: load each leg (`db.get(LedgerEntry, id)`), `peer_leg(db, leg)`, names through one `names: dict[int, str]` cache shared with the pair part (move the cache above both branches). Import `peer_leg` from `reconciliation_actions.transfer_support` (inbox_service already imports `reconciliation_actions`; check no import cycle with `python -c "import app.services.inbox_service"`).

- [ ] **Step 4: Run — expect PASS**, then `tests/integration/test_inbox_api.py` whole file green.

- [ ] **Step 5: Commit** `feat(reconciliation): name each adjust_transfer leg's account and counter account in the inbox (#433)`

### Task 2: `peer_case_id` on `ActionOut`

**Files:**
- Modify: `services/accounting-service/app/schemas/reconciliation.py:29-48`
- Modify: `services/accounting-service/app/routers/reconciliation.py:31-35`
- Test: `services/accounting-service/tests/integration/test_reconciliation_actions_transfer.py` (API-level tests through `client`)

**Interfaces:**
- Produces: `ActionOut.peer_case_id: int | None = None` — the peer line's case this action resolved; set only for `action in ("adjust_transfer", "pair_transfer")` from `(row.inverse or {}).get("peer_case_id")`; `None` otherwise. `_action_out` excludes it from the row copy and computes it.

- [ ] **Step 1: Tests**

New, all through `POST /reconciliation/cases/{id}/actions` with `S = {"Authorization": "Bearer spa-token"}` (write scope, `test_reconciliation_api.py:27`):
- `test_adjust_transfer_response_names_the_peer_case_it_resolved(pair, client, db_session)`: bank line's case is open before; response 201, `peer_case_id ==` that case's id, and the case is `resolved` with `resolved_action_id == body["id"]`.
- `test_adjust_transfer_response_peer_case_is_null_without_an_open_peer_case`: same fixture after the bank statement's peer case is dismissed (or a pair with no bank statement line) → `peer_case_id is None`.
- `test_pair_transfer_response_names_the_peer_case_it_resolved(orphan_pair, client, db_session)`: `peer_case_id ==` the bank line's `needs_peer` case id.
- `test_replayed_transfer_action_returns_the_same_peer_case_id`: the same body POSTed again → 200, `replayed`, same `id` and `peer_case_id` (pattern: `test_reconciliation_api.py:158-166`).
- `test_other_actions_and_the_action_list_carry_peer_case_id`: a `dismiss` response has `peer_case_id is None`; `GET /reconciliation/actions?case_id=` returns the adjust row with the same id; after `POST /actions/{id}/revert` the original row still lists it and the revert row has `None`.

- [ ] **Step 2: Run — expect FAIL** (`KeyError`/missing key `peer_case_id`).

- [ ] **Step 3: Implement** as in Interfaces (`PEER_CASE_ACTIONS = ("adjust_transfer", "pair_transfer")` in the router).

- [ ] **Step 4: Run — expect PASS**, then `test_reconciliation_api.py`, `test_reconciliation_actions_transfer.py`, `test_inbox_api.py` green.

- [ ] **Step 5: Commit** `feat(reconciliation): action responses name the peer case a transfer action resolved (#433)`

### Task 3: Docs + full suite

**Files:**
- Modify: `docs/superpowers/specs/2026-10-25-reconciliation-r6c-transfers-design.md` (§7 field list + a dated decision line: 2026-11-25 #433 `legs`, `ActionOut.peer_case_id`)

- [ ] **Step 1:** Edit the doc lines that state the `transfer` shape (§7, decision list) to include `legs`; add `peer_case_id` to the response note.
- [ ] **Step 2:** Full accounting suite: `… -m pytest -q -p no:cacheprovider` → all pass (record count).
- [ ] **Step 3: Commit** `docs(reconciliation): transfer legs and peer_case_id (#433)`

After merge (separate PR): SPA follow-up — 照帳單改轉帳… rows 「來源 → 目的」, one-leg note names the counter account, toast adds 「另一個帳戶的那一題也處理了」 when `peer_case_id` is set; screenshots of all three (owner requirement).
