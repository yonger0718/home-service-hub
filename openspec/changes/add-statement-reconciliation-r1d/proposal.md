## Why

R1c shipped the action engine and the policy engine, off. Its final review and the Task 10 review left seven items that must land before `settings.reconciliation.policy.enabled` is ever turned on: a policy action cannot be reverted after the next reconcile, `supersede_stale` locks in the reverse of the engine's order, reverting a plan link leaves its claims on other statements, and four policy follow-ups. This change is the fifth stacked PR (R1d) of the 對帳 design `docs/superpowers/specs/2026-10-08-statement-reconciliation-design.md` (section 17 "R1d design" and "R1d implementation notes", plan `docs/superpowers/plans/2026-10-12-statement-reconciliation-r1d.md`).

## What Changes

- Policy claims are `agent` coverage (`ledger_actions._match_kind`; `OWNED_KINDS` keeps `manual` and `agent`), so a reconcile no longer rewrites them and the action stays revertable.
- `proposal_service.supersede_stale` locks statements, then cases, then proposals (`SKIP LOCKED` global, `FOR UPDATE` scoped); `list_proposals` hides a stale pending proposal it could not supersede; `GET /reconciliation/cases/{id}` waits for the case's statement lock.
- Reverting `link_installment_plan` freezes the plan (`LockSet.exclusive_definition_ids`, `Context.lock_set`, `_check_lock_set`), re-derives the posted entries and claiming statements under the locks (409 `action_busy` when either grew), releases the claims on other statements (`plan_unmapped`) and flags them `needs_recheck`.
- `attach_fee` policy candidates are recomputed under the lock (`policy_service._fee_now`); a changed amount is `candidate_changed`.
- `policy_skipped` is audited once per `(case, case_version, code, reason)`, serialised under the statement then case locks; `_run_candidate` commits the scoped supersede before the skip.
- The engine refuses `actor='policy'` without a budget (422 `budget`).
- `accept_agent_proposals` considers only propose-only tokens (`policy_service._agent_authors`).
- Docs: service README policy section, spec section 17 R1d implementation notes.
- Known limitation, documented: two `installment_plan_map` rows for one definition under different `plan_key`s on one account are not refused; reverting one releases the other's claims too (self-healing on the next reconcile).

## Capabilities

This delta's requirements are MODIFIED relative to the R1c change `add-statement-reconciliation-r1c`, which must be archived first (no `openspec/specs/accounting-reconciliation/` exists yet).

### New Capabilities

None.

### Modified Capabilities

- `accounting-reconciliation`: action engine (policy budget), policy engine (coverage kind, fee amount, skip audit, agent definition), proposals (supersede order, listing, case detail), `link_installment_plan` revert.

`accounting-ledger` is NOT affected. No delta is written.

## Impact

- **Code**: `services/accounting-service/app/services/reconciliation_actions/{base.py,engine.py,engine_locks.py,ledger_actions.py,case_actions.py}`, `reconciliation_service.py`, `proposal_service.py`, `policy_service.py`; tests under `services/accounting-service/tests/`.
- **Config**: none. **Data**: none, no Alembic revision.
- **Deploy**: nothing to install. The feature flag and the policy stay off; deploy R1d before enabling the policy (README step 0).
