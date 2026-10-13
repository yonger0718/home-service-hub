## MODIFIED Requirements

### Requirement: Action engine

`engine.apply_action` with `actor='policy'` and no `budget` SHALL be 422 `budget` ("the policy must spend the policy budget"), before any lock, mirroring the existing refusal of a non-policy actor that passes a budget. A `LockSet` MAY name `exclusive_definition_ids`; the engine SHALL lock those schedule definitions `FOR UPDATE` (the rest stay `FOR SHARE`) and SHALL hand the resolved `lock_set` to the action through `Context.lock_set`.

#### Scenario: A policy apply without a budget is 422
- **GIVEN** an open `line_unmatched` case
- **WHEN** `apply_action` is called with `actor='policy'` and `budget=None`
- **THEN** 422 `budget` and nothing SHALL be written

#### Scenario: An owner apply with a budget is still refused
- **GIVEN** an open case
- **WHEN** an `owner` apply passes a budget
- **THEN** 422 `budget` ("only the policy spends the policy budget")

### Requirement: Policy engine and budget

A claim written by any action applied with `actor='policy'` SHALL have `match_kind='agent'` (`ledger_actions._match_kind`); a proposal the owner applies SHALL stay `manual`; `auto` SHALL remain the matcher's. A reconcile SHALL keep `manual` and `agent` coverage (`OWNED_KINDS`), so a policy action SHALL stay revertable after the next reconcile of its statement.

The `attach_fee` candidate's amount SHALL be `policy_service._fee_now`, `line.flow_amount - entry.amount` read from the rows, at collect time and again under the statement lock; a different amount under the lock SHALL be `candidate_changed` (skipped, audited) and the budget SHALL be charged only the fee the action books.

`policy_skipped` SHALL carry `detail.case_version` and SHALL be audited once per `(case_id, case_version, code, reason)`; the check and insert SHALL run in the skip's own transaction under the statement lock, then the case lock, after `_run_candidate` committed any scoped supersede. The run result SHALL still count every skip.

`accept_agent_proposals` SHALL consider a pending `match` proposal only when its author label is configured with `propose` and without `write` or `legacy` (`policy_service._agent_authors`); when `ACCOUNTING_TOKEN_SCOPES` is unset every author but `policy` SHALL count.

#### Scenario: A policy claim survives the owner's reconcile
- **GIVEN** a `create_system_entry` applied by the policy on a bank-only line
- **WHEN** the owner reconciles the statement
- **THEN** the claim ids and `match_kind='agent'` SHALL be unchanged and `revert` of the action SHALL succeed and leave the line unclaimed

#### Scenario: A policy attach_fee survives the owner's reconcile
- **GIVEN** an `attach_fee` applied by the policy
- **WHEN** the owner reconciles and then reverts the action
- **THEN** the claim SHALL be unchanged by the reconcile and the revert SHALL succeed

#### Scenario: An owner apply is unchanged by a reconcile
- **GIVEN** an owner `match`
- **WHEN** the statement is reconciled
- **THEN** the coverage SHALL stay `manual`

#### Scenario: An attach_fee candidate whose entry changed is candidate_changed
- **GIVEN** a policy `attach_fee` candidate collected with residual R
- **WHEN** the entry's amount is edited before the statement lock is taken
- **THEN** the candidate SHALL be skipped as `candidate_changed`, audited, and no budget SHALL be charged

#### Scenario: A deterministic skip is audited once per case version
- **GIVEN** a candidate that fails the same check on every run
- **WHEN** the policy runs twice, or two runs race, on the same case version
- **THEN** exactly one `policy_skipped` row SHALL exist for that version, and a new case version SHALL be audited again

#### Scenario: An owner token's proposal is not an agent proposal
- **GIVEN** scopes configured, a pending `match` proposal authored by a token with `write`, and one by a `propose`-only token
- **WHEN** the policy runs with `accept_agent_proposals`
- **THEN** only the propose-only token's proposal SHALL be applied

### Requirement: Agent proposals

`proposal_service.supersede_stale` SHALL lock the statements of the stale cases, then the cases, then update their proposals, all ascending: `FOR UPDATE SKIP LOCKED` without `case_id`, `FOR UPDATE` with it. `list_proposals` SHALL NOT return a pending proposal whose `case_version` is behind its case, even when a locked row kept the helper from superseding it. `GET /reconciliation/cases/{id}` SHALL wait for the case's statement lock before superseding, as `create_proposal` does.

#### Scenario: The global supersede skips a locked case
- **GIVEN** a stale pending proposal whose statement and case another transaction holds
- **WHEN** the global supersede runs
- **THEN** it SHALL return without waiting and without deadlock, the proposal SHALL stay pending in storage, and the listing SHALL NOT show it

#### Scenario: The scoped supersede waits for the statement
- **GIVEN** another transaction holds the case's statement but not the case
- **WHEN** `GET /reconciliation/cases/{id}` runs
- **THEN** it SHALL wait, then supersede the stale proposal and answer without it

### Requirement: Owner actions

Reverting `link_installment_plan` SHALL lock the plan's schedule definition `FOR UPDATE`, its posted entries `NOWAIT` and the other statements of the account that hold an active installment claim on those entries (ascending, each swept). Before any mutation `LinkInstallmentPlanAction._check_lock_set` SHALL re-derive the posted entries (`_plan_entries`) and the claiming statements (`_other_claims`) under the locks and SHALL answer 409 `action_busy` when an entry "was posted after discovery" or a statement "gained a plan claim". It SHALL then delete the map, release the other statements' installment claims (`stale_reason='plan_unmapped'`) and set their `needs_recheck=True`; their status SHALL be unchanged, `confirm` SHALL be refused until their next reconcile, and the engine SHALL recount every locked statement.

Two `installment_plan_map` rows for one definition under different `plan_key`s on one account are NOT refused; reverting one SHALL release the other's claims too, and the next reconcile SHALL claim through the remaining map.

#### Scenario: Reverting a plan link releases claims on another statement
- **GIVEN** a plan linked on statement A whose entries are also claimed on statement B
- **WHEN** the link is reverted
- **THEN** both claims SHALL be released with reason `plan_unmapped`, B SHALL have `needs_recheck=True` and B's next reconcile SHALL open the `installment_unmapped` case again

#### Scenario: A confirmed statement keeps its status
- **GIVEN** B was confirmed before the revert
- **WHEN** the revert releases B's claims
- **THEN** B's status SHALL be unchanged, its `matched_count` SHALL drop and `confirm` SHALL be refused until the next reconcile

#### Scenario: A claim or posting that raced the revert is action_busy
- **GIVEN** an instance posted, or a claim committed, between the revert's discovery and its locks
- **WHEN** the revert runs
- **THEN** 409 `action_busy`, nothing SHALL be mutated and the retry SHALL release the new claim
