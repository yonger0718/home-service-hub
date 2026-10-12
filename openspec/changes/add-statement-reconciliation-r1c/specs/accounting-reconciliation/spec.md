## ADDED Requirements

### Requirement: Action engine

Every owner, policy and proposal-applied action SHALL run through `engine.apply_action(case_id, action, params, actor, ...)` in ONE transaction that the engine never commits (the router commits; `policy_service.run_policy` commits per candidate and only after the matching transaction committed). `idempotency_key` SHALL be `sha256("{case_id}|{case_version}|{action}|{canonical params JSON}")` (validated params, sorted keys, no spaces, Decimals as strings, defaults included); a key is never reused (`uq_reconciliation_action_idempotency` is unconditional). A stored `applied` row under the key SHALL be replayed (HTTP 200, `replayed: true`, no new effect, audit `action_replayed`; a first application answers 201), a `reverted` row SHALL be 409 `action_reverted`, any other status 409 `action_failed`; the lookup runs before `prepare` and again under the statement lock, so a concurrent identical request replays instead of failing `case_closed`. Steps and lock order: the shared import key (409 `import_running` while an import runs) → the policy budget row (policy only) → the case's statement and any extra statement (`move_account` target) `FOR UPDATE` in ascending id order, each swept → the case `FOR UPDATE` → the proposal row (proposal apply only) → the case checks (409 `case_moved` when the version differs from the one the key was built from, 409 `case_closed` unless `open`/`proposed`, 422 `action` "not allowed for this case" unless the registry offers the action, 422 `historical_statement` on a historical statement for anything but `match`/`unmatch`) → the policy `precheck` and budget charge → the action's `check_before_locks` → [account row, writer barrier: `balance_adjustment_asof` only] → schedule definitions (`FOR SHARE`) and instances, groups ascending, then entries with their children and transfer legs in one ordered select, all `NOWAIT` (a held row is 409 `action_busy`, nothing written) → `before` snapshots → `validate_before` → the creation-family check → the action row. An unknown action or invalid params SHALL be 422 before any lock; an actor the action does not serve SHALL be 422 `actor`.

`prepare` SHALL capture, without locks, an EXPECTED snapshot of every row the action will touch (entries, their child rows, groups and member rows, incoming dependency ids). After the locks are held the engine SHALL re-read the same set as `before`; any difference SHALL be 409 `snapshot_conflict` (naming up to five paths) with nothing written. The action row SHALL be inserted first (status `applied`, flushed): the partial unique index `ux_reconciliation_action_effect(event_id, action, effect_slot) WHERE status='applied'` then refuses a duplicate effect with 422 `duplicate_effect` before any ledger write, and the row id exists for the GUC. Effect slots SHALL be: `match`, `unmatch`, `dismiss`, `defer_next_period` → `v<case version>` (a reopened case at a new version may be acted on again); `create_entry`, `create_refund`, `create_system_entry` → `create`; `attach_fee` → `fee`; `adjust_amount` → `amount`; `move_account` → `move`; `split_entry` → `split`; `balance_adjustment_asof` → `balance`; `link_installment_plan` → `plan`; `revert` → `r<reverted action id>`. Under the statement lock a creation-family action SHALL also be refused with 422 `duplicate_effect` when an applied `create` slot exists on the same `event_id`, whichever creation action wrote it. No path writes a `failed` row; a policy candidate that fails is rolled back and audited `policy_skipped {code}`.

Every ledger write of an action SHALL run after `SELECT set_config('app.reconciliation_action_id', '<action id>', true)` so the dirty triggers tag the events and the sweep skips them. Every coverage write SHALL go through `coverage_service.write_claim`, and the transaction SHALL end with `coverage_service.assert_conserved` for every locked statement. After `apply` the engine SHALL record `before`, `after` (a snapshot set plus `coverage_ids`), `inverse`, `created_entry_ids`, `deleted_entry_ids`, `touched_entry_ids` (the coverage rows' entries included, also for coverage-only actions) and `touched_group_ids`, resolve the case (`resolved` or `dismissed` with `resolved_by`, `resolved_action_id`, `resolved_at`, or leave it open with merged `context` when the effect does not resolve it), supersede the case's other pending proposals (audit `proposal_superseded`), write audit `action_applied` (action, actor, params, slot, ids) and run the counts-only recount of every locked statement. Any failure SHALL roll the attempt savepoint back with every write and row lock.

`revert` (`POST /reconciliation/actions/{id}/revert`) SHALL take the same locks for the action's recorded rows and SHALL be refused with 422 `not_revertable` for a `revert`, an action that is not `applied`, an action with no inverse (`resolve_conflict`), a later applied non-revert action touching the same entries or groups, released coverage, or the action's own refusal; 409 `snapshot_conflict` when the rows differ from the action's `after`; 422 `historical_statement` for anything but `match`/`unmatch` on a historical statement. It SHALL invert the effect, release the action's coverage (reason `revert`), end with `assert_conserved`, mark the original `reverted` (`reverted_by_action_id`), insert a `revert` row (key `sha256("revert|<id>")`; a second revert is `not_revertable`), reopen the case at `version + 1` with `context.reverted_action_id`, and write audit `action_reverted`.

#### Scenario: Match claims, resolves, recounts and audits
- **GIVEN** an open `line_unmatched` case with one admissible entry
- **WHEN** the owner applies `match` with that entry
- **THEN** HTTP 201, the line SHALL have active `manual` coverage, the case SHALL be `resolved` with `resolved_by='owner'`, the counts SHALL be recounted and audit `action_applied` SHALL exist

#### Scenario: Replay returns the stored row
- **GIVEN** an applied action
- **WHEN** the identical request is sent again
- **THEN** HTTP 200 with `replayed: true`, the same action id, no new effect and an `action_replayed` audit row
- **AND** after a revert the case is at a new version, so the same request computes a new key and applies again

#### Scenario: A second action of one slot on one case version is a duplicate effect
- **GIVEN** an applied `match` on an event and a case that is open again WITHOUT a version bump
- **WHEN** a `match` with different params (a new idempotency key) is applied
- **THEN** 422 `duplicate_effect` and nothing SHALL be written; a resolved case answers 409 `case_closed`

#### Scenario: A reopened case can be matched again
- **GIVEN** a `match` that was reverted (case at version + 1)
- **WHEN** `match` is applied again
- **THEN** the slot `v<version + 1>` SHALL be free and the line SHALL be claimed again

#### Scenario: Concurrent identical requests produce one effect
- **WHEN** two identical `match` requests race
- **THEN** exactly one coverage claim SHALL exist and the loser SHALL replay (200) or be refused without an effect

#### Scenario: Snapshot conflict writes nothing
- **GIVEN** an entry edited, deleted or reposted between `prepare` and the locks
- **WHEN** the action takes its locks
- **THEN** 409 `snapshot_conflict` and no action row, coverage or audit row SHALL be written

#### Scenario: A held row makes the action yield
- **GIVEN** another transaction holds a target entry, group, schedule definition or schedule instance row
- **WHEN** the action takes its locks
- **THEN** 409 `action_busy` and nothing is written

#### Scenario: Case moved between prepare and the lock
- **GIVEN** the case version bumped after the key was computed
- **WHEN** the action reaches the case lock
- **THEN** 409 `case_moved`

#### Scenario: One creation per statement event
- **GIVEN** an applied `create_entry` on an event
- **WHEN** `create_system_entry` or `create_refund` is applied on the same event
- **THEN** 422 `duplicate_effect`, also when two creations race

#### Scenario: Historical statements allow only coverage actions
- **GIVEN** a historical statement
- **WHEN** `match` or `unmatch` (or their revert) is applied it SHALL succeed, and any other action SHALL be 422 `historical_statement`

#### Scenario: Audit rows carry the request id
- **WHEN** an action is applied with an `X-Request-Id` header
- **THEN** its `action_applied` audit row SHALL carry that id

#### Scenario: Revert releases and reopens
- **GIVEN** an applied `match`
- **WHEN** it is reverted
- **THEN** the coverage SHALL be released, the original `reverted`, the case `open` at `version + 1`, and a second revert SHALL be 422 `not_revertable`

#### Scenario: Revert refuses after a later action touched the rows
- **GIVEN** a later applied action touching the same entries
- **WHEN** the earlier action is reverted
- **THEN** 422 `not_revertable` ("later action <id>")

### Requirement: Owner actions

The registry (`reconciliation_actions.allowed(case)`) SHALL offer actions only while the case is `open` or `proposed`, by kind: `line_unmatched`: `match`, `create_entry`, `create_system_entry` (only for a bank-only hint or a `fee`/`interest`/`reward` line), `create_refund` (only for a `refund` line), `link_installment_plan` (only with hint `installment_unmapped`), `split_entry` (only when the case names an entry in `candidates` or `context.entry_id`), `dismiss`; `ambiguous`: `match`, `dismiss`; `amount_delta`: `match`, `adjust_amount`, `attach_fee` (only with `context.fee_matches` true), `split_entry`, `dismiss`; `entry_unmatched`: `defer_next_period`, `move_account`, `dismiss`; `balance_gap`: `balance_adjustment_asof`, `dismiss`; `duplicate_claim`: `unmatch`, `dismiss`; `recheck`: `match`, `unmatch`, `dismiss`; `parse_review`: `dismiss`; `statement_conflict`: `resolve_conflict` only. Params are strict (unknown keys refused, 422 on `params`). A 422 body's message starts with the stable code, optionally `: <detail>`; a 409 detail is `<code>` or `<code>: <detail>`. Money SHALL be `Decimal`, 4 dp, with ledger sign conventions. The actions served for the policy actor are only `match`, `create_system_entry` and `attach_fee`.

**match** `{line_id, entry_id | group_id (exactly one), with_children=false, allow_sibling=false}` (kinds `line_unmatched`, `ambiguous`, `amount_delta`, `recheck`; `with_children` only with `entry_id`). The line SHALL be a line of the case's current revision (the case's line or another line of its event, else 422 `line_not_in_case`) and unclaimed (422 `line_claimed`). Each row SHALL be on a participating account (or, with `allow_sibling`, a configured sibling account; 422 `wrong_account`), in the statement's currency (`wrong_currency`), and unclaimed (`entry_claimed`); an entry SHALL be missing (`not_found`), a child (`child_entry`) or grouped (`entry_in_group`: match the group), a group empty (`empty_group`); rows plus the line SHALL be conserved (`not_conserved`). Coverage `match_kind` is `manual` for the owner, `agent` when policy applies an agent proposal; `match_rule='owner'`. Inverse: release the line.

**unmatch** `{line_id}` (kinds `duplicate_claim`, `recheck`): 422 `line_not_in_case`, `not_claimed`. It releases the line's coverage (reason `owner`) and sets `needs_recheck`. Inverse: re-claim the same rows with the same kind, rule and roles (`not_revertable` if the line or entries are claimed again or gone).

**dismiss** `{note 1..1000}` (all kinds except `statement_conflict`): the case becomes `dismissed`, explanation `accepted_exception`, `note` stored. Inverse reopens and moves the note to `context.dismiss_note`. A dismissed case SHALL NOT be recreated by the next reconcile. `POST /reconciliation/cases/{id}/dismiss` is the same action.

**defer_next_period** `{entry_id}` (kind `entry_unmatched`; the id SHALL be the case's entry, else 422 `entry_id`): `deferred_to_period_end` = the account's next cycle end (card: the next closing date after `period_end`, bank: the last day of the next month), `deferred_to_statement_id` NULL (the target's reconcile or the batch links it), case `resolved` explanation `deferred_next_period`; the owner deferral stands in for the automatic deferral case and survives reconcile. Inverse clears the deferral and reopens.

**link_installment_plan** `{definition_id}` (kind `line_unmatched`, hint `installment_unmapped`): the id SHALL be in the case's `candidates` (422 `id_out_of_scope`) and an active `installment` definition (422 `definition_id`); a plan already mapped is 409 `plan_already_mapped`. It inserts the `installment_plan_map` row (`confirmed_by='owner'`) and reconciles the statement in the same transaction so the line is claimed. Inverse deletes the map row and re-reconciles; refused (`not_revertable`) after a later action touched the plan's entries.

**create_entry** `{kind: expense|income, category_id, name?, project_id?}` (kind `line_unmatched`): books the line as a new entry (`source='statement'`, the statement's account and currency, `entry_date` = the line's transaction date else posting date, `posted_date`, description `對帳建立 <period end> <merchant>`, `needs_review=false`) and claims it. 422 `kind_sign` when the kind contradicts the line's flow, `line_claimed`, `line_not_printed`, category/project errors on their fields. **create_system_entry** `{kind: fee|interest|reward}` books the line as that kind with the system category and `needs_review=true`. **create_refund** `{original_entry_id}` books the line (flow > 0, else 422 `refund_sign`) through `settlement_service.refund(..., source="statement")` against an owner-named expense on a participating account in the statement's currency (422 `not_found`, `wrong_account`, `wrong_currency`, `not_refundable` (not an expense, or a child), `refund_exceeds`). Inverse of the creation family: release the coverage and `delete_entries_cascade` the created row only while it equals `after`.

**attach_fee** `{entry_id}` (kind `amount_delta`, bundled fee only): the id SHALL equal `context.entry_id` (422 `entry_out_of_scope`); the entry standalone (`not_standalone`), foreign (`not_foreign`), without an existing fee child (409 `fee_exists`); the line a debit and `residual = line.flow − entry.flow` negative (422 `fee_sign` otherwise); `0 < fee ≤ 0.05 × |principal|` (`fee_out_of_bounds`), in display units (`fee_quantum`), within one display unit of the account's proposed FX fee (`fee_unexpected`). It writes the 國外交易手續費 child (stored negative) via `write_children` and claims principal + child. Inverse deletes the child. Credit-side residuals and separate fee lines are not supported.

**adjust_amount** `{entry_id, line_id}` (kind `amount_delta`): the entry SHALL be in the case's candidates (422 `entry_out_of_scope`), unclaimed on a participating account, standalone (`not_standalone`), unprotected (409 `protected`), without settlements or refunds (409 `has_settlements`), the line printing a foreign amount when the entry is foreign (422 `fx_pair_required`), the sign valid for the kind (`kind_sign`), a non-zero foreign amount (`foreign_zero`). It rewrites the amount to the printed flow (and the foreign pair with `fx_source="manual"`), appends `對帳調整 原金額 <old>` to the description and claims the entry. Inverse restores the recorded columns.

**move_account** `{entry_id, account_id}` (kind `entry_unmatched`): the entry SHALL be the case's (`entry_out_of_scope`) on a participating account (`wrong_account`); the target exists (`not_found`), is not archived (`archived`), has the entry's currency (`currency`), differs from the entry's account (`same_account`) and is not part of this statement's population (`not_movable`); the entry has no group, parent, transfer group, children or schedule instance (`not_movable`), is unclaimed (`entry_claimed`), editable and unprotected (409 `protected`). It changes `account_id`, then reconciles the case's statement and the target account's statement whose period contains the entry's posted date (same currency; both locked ascending; 409 `snapshot_conflict` if that set changed under the locks). The case resolves. Inverse moves the entry back and reconciles again.

**split_entry** `{entry_id, parts: 2..N × {amount, category_id, name?}}` (kinds `amount_delta`, `line_unmatched` naming the entry): the entry SHALL be in the case (422 `entry_out_of_scope`), unclaimed, and splittable (422 `not_splittable` with the reason: children, reward link, foreign amount, settlements or refunds, grouped, child, transfer, scheduled, non-editable kind, protected); the parts SHALL sum to |amount| (`parts_sum`) with valid categories (`parts.<i>.category_id`). The anchor keeps its id and takes the first part. When a member's amount equals the line the line is claimed on it and the case resolves; otherwise the case stays open with `context.split_group_id`. Inverse deletes the non-anchor members, dissolves the group and restores the anchor from `before`, only while the members equal `after`.

**balance_adjustment_asof** `{}` (kind `balance_gap`, bank statements): refused with 422 `dirty_disabled` while dirty tracking is off and 422 `zero_delta` when the statement balances. Under the account row `FOR UPDATE` and the exclusive transaction-scoped writer barrier (`coverage_service.take_barrier_xact`: 5 s `lock_timeout`, 409 `sweep_barrier_busy`; released at the router's commit or rollback) it books `statement_total − balance(period end)` as a `balance_adjustment` dated the period end (`needs_review=true`). Inverse deletes the entry.

**resolve_conflict** `{keep: current|new}` (kind `statement_conflict`): the case's revision SHALL still be the statement's newest non-rejected revision and not current (409 `conflict_stale`). `keep=new` promotes it (`promote_revision`: coverage transferred or quarantined by lineage, event pointers, header, current revision), sets `conflict_open=false`, `status='open'` and reconciles; a revision whose guardrails failed is refused (409 `conflict_guardrail_failed`). `keep=current` marks the revision `rejected`, `conflict_open=false`, the statement stays `reconciled`. On either outcome every other open `statement_conflict` case of an older revision is superseded (audit `conflict_superseded`) and, for `keep=current`, its revision is rejected too. Not revertable (the owner submits again).

#### Scenario: The allowed actions follow the kind table
- **GIVEN** one case of each kind
- **THEN** `actions_allowed` SHALL list exactly the registry's actions for its kind and context, and `[]` once the case is resolved

#### Scenario: Match refusals write nothing
- **WHEN** `match` names an unknown entry, a claimed entry, another currency, a foreign account, a child, a grouped entry, or rows that do not sum to the line
- **THEN** 422 with the code (`not_found`, `entry_claimed`, `wrong_currency`, `wrong_account`, `child_entry`, `entry_in_group`, `not_conserved`) and no row SHALL be written

#### Scenario: Match with children and a group
- **WHEN** `match` is applied with `with_children=true`, or with a complete group
- **THEN** principal and children (or every top-level member) SHALL be claimed on the line with roles `principal`/`child`/`member`

#### Scenario: Unmatch releases, flags the recheck and reverts
- **GIVEN** a `duplicate_claim` case with a claimed line
- **WHEN** `unmatch` is applied
- **THEN** the claim SHALL be released and `needs_recheck` set, and reverting SHALL re-claim the same rows with the same kind and rule
- **AND** `unmatch` of an unclaimed line SHALL be 422 `not_claimed`

#### Scenario: Dismiss and its revert
- **WHEN** a case is dismissed with a note (1 to 1000 characters)
- **THEN** it SHALL be `dismissed`/`accepted_exception`, counted as explained, not recreated by the next reconcile, and a revert SHALL reopen it keeping the note in `context.dismiss_note`

#### Scenario: Defer uses the account cycle
- **WHEN** `defer_next_period` is applied to a card entry and to a bank entry
- **THEN** `deferred_to_period_end` SHALL be the next closing date, respectively the last day of the next month; on another kind or a foreign entry it SHALL be refused
- **AND** the later statement's reconcile SHALL link it, and a revert SHALL clear it and reopen the case

#### Scenario: Link maps the plan and claims the line
- **WHEN** `link_installment_plan` names a candidate definition
- **THEN** the plan map and the claim SHALL exist in the same call, a definition outside the candidates SHALL be 422 `id_out_of_scope`, a non-installment or inactive one 422, and an already mapped plan 409 `plan_already_mapped`

#### Scenario: Create books, claims and tags its dirty events
- **WHEN** `create_entry` is applied
- **THEN** a `source='statement'` entry SHALL be created and claimed, its dirty events carry the action id so the sweep skips them, and a revert SHALL delete it only while it is unedited

#### Scenario: System entry only for bank-only or fee lines
- **WHEN** `create_system_entry` is requested on an ordinary line
- **THEN** it SHALL not be offered and SHALL be refused

#### Scenario: Refund over the remaining amount is refused
- **WHEN** `create_refund` would refund more than the original's remaining amount
- **THEN** 422 `refund_exceeds`, and a non-expense or non-participating original SHALL be `not_refundable`/`wrong_account`

#### Scenario: Attach fee claims principal and child
- **GIVEN** an `amount_delta` case with `fee_matches` and a foreign-currency entry
- **WHEN** `attach_fee` is applied
- **THEN** a negative fee child SHALL be written, both rows claimed, and a second fee on the entry SHALL be 409 `fee_exists`
- **AND** a fee of the wrong sign, size or quantum SHALL be refused before any write

#### Scenario: Adjust amount rewrites the pair
- **WHEN** `adjust_amount` is applied to a near entry
- **THEN** the amount (and the foreign pair with rate and `fx_source="manual"`) SHALL equal the printed values, the old amount SHALL be noted, and a revert SHALL restore the columns
- **AND** a protected entry SHALL be 409 `protected` and a foreign entry against a line without a pair 422 `fx_pair_required`

#### Scenario: Move account re-reconciles both statements
- **WHEN** `move_account` moves an unmatched entry to an account whose statement covers its date
- **THEN** both statements SHALL be reconciled, the target SHALL claim it, and a revert SHALL move it back
- **AND** another currency, a grouped entry, an entry outside the case or a participating target account SHALL be refused

#### Scenario: Split converts and claims the matching member
- **WHEN** `split_entry` parts sum to the entry and one equals the line
- **THEN** the line SHALL be claimed on that member and the case resolved; without a matching member the case SHALL stay open with `context.split_group_id`
- **AND** parts that do not sum SHALL be `parts_sum`, an anchor with children or a reward link `not_splittable`, and the revert SHALL restore the anchor exactly or refuse after an edit

#### Scenario: Balance adjustment books the gap
- **WHEN** `balance_adjustment_asof` is applied with dirty tracking on
- **THEN** an entry of the gap SHALL be dated the period end, a double submit SHALL replay, a zero gap SHALL be `zero_delta`, dirty tracking off `dirty_disabled`, and a barrier wait beyond the timeout 409 `sweep_barrier_busy` with no row

#### Scenario: Resolving a conflict
- **WHEN** `resolve_conflict` keeps `new` or `current`
- **THEN** the case SHALL resolve, `conflict_open` SHALL clear, older conflict cases SHALL be superseded, and an old revision SHALL be 409 `conflict_stale`
- **AND** `keep=new` on a guardrail-failed revision SHALL be 409 `conflict_guardrail_failed`

### Requirement: Agent proposals

A token with scope `propose` SHALL be able to attach a proposal to a case: `POST /reconciliation/cases/{id}/proposals {action, params, rationale 1..1000, confidence 0..1}` (201). Creation SHALL lock the statement then the case, refuse a case that is not `open`/`proposed` (409 `case_closed`), an action the registry does not offer (422), unknown params (422), `allow_sibling` anywhere in the params (422 `sibling_not_proposable`), and any id-typed param outside the case's own evidence (422 `id_out_of_scope`). Id scoping SHALL be by param type: `entry_id` and `original_entry_id` against entry ids in the case's `candidates`/`context` and the case's `entry_id`; `line_id` against line ids there (`line_id`, `old_line_id`, `new_line_id`) and the case's `line_id`; `group_id` against `group_id`/`split_group_id`; `definition_id` against `definition_id`; `account_id` against candidate accounts and the account of the statement a move hint names; `category_id` and `project_id` are free choices checked by the action; `context.sibling_candidates` never widens any scope. The proposal records the case version; a previous pending proposal by the same author is superseded; the case becomes `proposed`; audit `proposal_created`. A proposal SHALL be superseded (audit `proposal_superseded`, a `proposed` case with no live proposal reopens) when the case version moves on, when another action is applied to the case, and on read: `GET /reconciliation/proposals` and `GET /reconciliation/cases/{id}` supersede stale rows first and the routes commit that before answering. `POST /reconciliation/proposals/{id}/apply` (`write`) delegates to the engine as `actor='owner'` with the stored action and params: 409 `proposal_not_pending` (rejected, applied or superseded), 409 `proposal_superseded` (written at an older case version; the route persists the superseded mark), 409 `proposal_mismatch` (the stored action or params differ from the request being applied), 404 for an unknown proposal. `POST /reconciliation/proposals/{id}/reject` (`write`, optional `{note}`) marks it rejected and reopens the case when it was the only live proposal. A hermes token (`read,propose`) SHALL get 403 on apply and reject.

#### Scenario: Hermes proposes and the case becomes proposed
- **WHEN** a `propose` token posts a valid `match` proposal
- **THEN** 201, the case SHALL be `proposed`, `pending_proposals` SHALL list it and audit `proposal_created` SHALL exist

#### Scenario: Ids are scoped by type
- **WHEN** a proposal names an `entry_id` equal to a candidate's account id, the case's line id or a context statement id
- **THEN** 422 `id_out_of_scope`
- **AND** a `line_id` equal to an old or new line id of the case is accepted, and `sibling_candidates` never widen the scope

#### Scenario: allow_sibling is never proposable
- **WHEN** a proposal carries `allow_sibling`
- **THEN** 422 `sibling_not_proposable`

#### Scenario: Superseded by a newer proposal, a version bump, or another action
- **GIVEN** a pending proposal
- **WHEN** the same author proposes again, or the case version moves, or another action is applied
- **THEN** it SHALL read `superseded`, with audit, and applying it SHALL be 409 `proposal_superseded` / `proposal_not_pending`

#### Scenario: Owner applies, rejects, and the loser of a race is not pending
- **WHEN** the owner applies a proposal
- **THEN** the action SHALL run as `owner`, the proposal be `applied` with `applied_action_id`, and the case's other pending proposals be superseded
- **AND** when apply and reject race, the loser SHALL be 409 `proposal_not_pending`

#### Scenario: Mismatch guard
- **WHEN** the engine is asked to apply an action that is not the proposal's
- **THEN** 409 `proposal_mismatch`, and a rejected proposal SHALL be 409 even when the identical action already exists

#### Scenario: Reject reopens an idle case
- **WHEN** the only live proposal is rejected (stale pending rows ignored)
- **THEN** the case SHALL be `open` again

### Requirement: Policy engine and budget

The policy SHALL ship off and SHALL change nothing while `settings.reconciliation.policy.enabled` is false. Defaults (`POLICY_DEFAULTS`): `enabled=false`, `accept_agent_proposals=false`, `per_statement_count=5`, `per_statement_total="5000"`, `monthly_total="20000"`, `per_currency_enabled=["TWD"]`, `actions.create_system_entry={enabled:false, max_amount:"2000"}`, `actions.attach_fee={enabled:false, max_amount:"500"}`, `actions.match={enabled:false, max_amount:"0"}`; `PUT /settings/reconciliation` (`admin`) validates every key (unknown keys, non-boolean flags, counts outside 0..1000, non-amounts, non-ISO currency codes are 422) and replaces `policy` wholesale like `rules`; `policy_version` is `r1c-<settings version>` when the effective policy changes. `run_policy(statement)` SHALL run only after the matching transaction committed (reconcile route, revision submit for live statements, manual statement for live statements, each statement of the sweep; `run_policy_safe` turns any policy error into an audit `policy_skipped` with the exception class and never fails the caller). It SHALL do nothing for a statement that is not `live`, has `conflict_open` or `needs_recheck`, or an open/proposed `parse_review` or `statement_conflict`; a currency outside `per_currency_enabled` is audited `policy_skipped currency`. Candidates SHALL be only: `create_system_entry` for an open `line_unmatched` case with hint `bank_only` whose line kind is `fee`/`interest`/`reward`; `attach_fee` for an open `amount_delta` with `context.fee_matches` and `context.residual`; and, when `accept_agent_proposals` is true, a pending agent `match` proposal (author other than `policy`, at the case's current version) on an `ambiguous` case. Never near deltas, balance gaps, conflicts, parse reviews, rechecks or historical statements.

Per candidate, in its own transaction: an unlocked pre-filter (case unchanged, action enabled, caps) decides to try, to propose, or to skip; an action over a cap (`max_amount`, `per_statement_count`, `per_statement_total`) becomes a proposal authored `policy` (audit `policy_proposed`, rationale `policy: <reason>`) while an agent's own proposal over a cap stays pending. The engine then runs with a budget: the `policy_budget` row for `(calendar month of the application day, statement currency)` is inserted `ON CONFLICT DO NOTHING` and locked `FOR UPDATE` BEFORE the statement lock; `used_amount + amount > monthly_total` is 409 `budget_exceeded` (the candidate becomes a proposal). Under the statement and case locks the `precheck` SHALL re-validate: policy and action still enabled (409 `policy_disabled`, `action_disabled`), the statement still not excluded (409 `statement_excluded`), the case still the candidate (409 `candidate_changed`: status, kind, `context.entry_id`, amount, hint), for an agent match the single-admissible validator (409 `not_single_admissible`: the matcher's representations of the line over the current population must be exactly one, the proposed one, among the case's candidates), and the per-statement caps recounted from applied `actor='policy'` rows (409 `cap_exceeded` with reason). Only then SHALL the budget row be charged (`used_amount += amount`, `used_count += 1`) and the action applied; the charge is recorded in the action's `after.policy_budget` and is NOT refunded by a revert. Coverage kinds: policy creations `auto`, a policy-applied agent proposal `agent`; an agent-accepted `match` is capped by `actions.match.max_amount` (default `"0"`: never). A candidate that fails with a 422/409 is rolled back whole (reservation included) and audited `policy_skipped {code}` (a disabled action is counted but not audited); a successful one is audited `policy_applied` and committed. `run_policy` commits after each candidate (the one documented exception to "services never commit").

#### Scenario: Policy off does nothing
- **GIVEN** the default settings and a bank-only fee line
- **WHEN** the statement is reconciled
- **THEN** no action, proposal, budget row or audit row SHALL be written and the reconcile route SHALL report `policy: {applied: 0, proposed: 0}`

#### Scenario: Policy on books a bank-only fee as auto and charges the budget
- **GIVEN** the policy and `create_system_entry` enabled
- **WHEN** a bank-only fee line is reconciled
- **THEN** a system entry SHALL be created with `auto` coverage, `policy_budget.used_amount` SHALL grow by the amount and audit `policy_applied` SHALL exist

#### Scenario: Excluded statements and disabled actions are left alone
- **GIVEN** a statement with an open `parse_review`, or an action whose flag is false
- **THEN** nothing SHALL be tried

#### Scenario: Over a cap becomes a policy proposal
- **WHEN** the amount passes `max_amount`, or the second candidate passes `per_statement_count`
- **THEN** a pending proposal authored `policy` SHALL be created (audit `policy_proposed`) and nothing applied

#### Scenario: Monthly cap race applies one and proposes the other
- **WHEN** two candidates reserve the same month and currency and only one fits `monthly_total`
- **THEN** exactly one SHALL be applied (the budget row lock serialises them) and the other become a proposal

#### Scenario: The precheck recounts under the lock
- **WHEN** two runs of one statement race past the unlocked pre-filter
- **THEN** the per-statement caps SHALL still hold because the precheck counts applied policy rows under the statement lock

#### Scenario: A candidate resolved meanwhile is skipped
- **WHEN** the owner resolves the case during the lock wait
- **THEN** the candidate SHALL be skipped (`candidate_changed` / `case_closed`) and the budget not charged

#### Scenario: A failing apply is skipped and the loop continues
- **WHEN** one candidate's apply raises a 422 or 409
- **THEN** it SHALL be rolled back and audited `policy_skipped {code}`, the next candidate SHALL still run, and no `failed` action row SHALL exist

#### Scenario: Foreign currency is skipped
- **GIVEN** `per_currency_enabled=["TWD"]`
- **WHEN** a USD statement is run
- **THEN** it SHALL be skipped with audit `policy_skipped currency`

#### Scenario: Agent proposals stay pending unless accepted
- **GIVEN** `accept_agent_proposals=false`
- **THEN** an agent `match` proposal SHALL stay pending
- **AND** with it true, `actions.match` enabled with a `max_amount`, and exactly one admissible representation, the match SHALL be applied with `agent` coverage that reconcile keeps; with two admissible representations it SHALL be skipped `not_single_admissible`

#### Scenario: The routes run the policy after their commit and never fail on it
- **THEN** the reconcile, revision (live), manual-statement (live) and sweep routes SHALL run it, a historical statement SHALL skip it, and a policy error SHALL leave the route's own result intact

### Requirement: Statement confirm

`POST /accounts/{account_id}/statements/{statement_id}/confirm {force=false, note?}` (`write`) SHALL run one transaction: the shared import key → DISCOVER (unlocked) the earlier statements holding a pending incoming deferral linked to this statement → lock those and this statement `FOR UPDATE` in ascending id order, each swept → re-read the link set under the locks (a link that appeared on a statement not locked here is 409 `confirm_retry`) → resolve the incoming deferrals → counts-only recounts → supersede stale proposals → run the checks → set `status='reconciled'` → audit. A statement that is not the account's SHALL be 404. The checks, each true when it passes, SHALL be `current_revision`, `guardrail_ok` (of the current revision), `conflict_open` (no open conflict), `needs_recheck` (flag clear), `open_cases` (no open/proposed case), `pending_proposals` (none after the stale ones are superseded) and `conserved`. Any failing check without `force` SHALL be 409 `confirm_refused` whose detail is the checks as sorted JSON, the deferral processing rolled back with everything else; `force` requires a non-blank `note` (422 `note_required`) and confirms anyway, audited `statement_confirmed_forced` (else `statement_confirmed`) with the checks, reopened cases and note. Response `{status, checks, reopened_cases, forced}`. Confirming a reconciled statement again SHALL be allowed (it processes links that arrived since).

Incoming deferral resolution: a pending incoming deferral is an earlier statement's `resolved` `entry_unmatched` case with explanation `deferred_next_period`, `deferred_to_statement_id` = this statement and no `context.resolved_in_statement_id`; `StatementOut.incoming_deferrals_pending` counts them. When this statement claims the deferred entry the earlier case stays resolved and gets `context.resolved_in_statement_id`; when the entry is still unclaimed the earlier case reopens (`open`, `version + 1`, explanation cleared, `context.reopened_by_confirm_of`), the earlier statement's counts are recomputed and `deferral_reopened` is audited, while this confirmation proceeds. Incoming deferrals are never a refusal reason and processing them never sets `needs_recheck`. `needs_recheck` keeps only its matching meaning: it is set by the sweep's population changes and by `unmatch`, and cleared solely by a completed `reconcile` as its last step; `reconciliation_service._recount` is counts-only. Deferral linkers serialise with confirm through the target statement's row lock (`_link_earlier_deferrals` under the target's lock in reconcile; `fill_deferral_links` locks each target `FOR UPDATE`, one savepoint per target, and re-reads it before linking; an action never fills `deferred_to_statement_id`).

#### Scenario: Confirm reconciles a clean statement
- **WHEN** a statement with all checks true is confirmed
- **THEN** `status` SHALL be `reconciled`, `forced` false, and audit `statement_confirmed` exist

#### Scenario: Each failing check refuses and names itself
- **WHEN** a check fails and `force` is false
- **THEN** 409 `confirm_refused` with the failing check false in the JSON detail; a failed current revision SHALL fail `guardrail_ok`

#### Scenario: Stale proposals are superseded before the check
- **GIVEN** a pending proposal written at an older case version
- **THEN** it SHALL be superseded and `pending_proposals` pass

#### Scenario: Force needs a note
- **WHEN** `force` is true without a note
- **THEN** 422 `note_required`; with a note the statement SHALL be confirmed and `statement_confirmed_forced` audited with the note

#### Scenario: Another account's statement is not found
- **WHEN** the path account differs from the statement's
- **THEN** 404

#### Scenario: A deferral claimed here stays resolved
- **GIVEN** an incoming deferral whose entry this statement claims
- **WHEN** this statement is confirmed
- **THEN** the earlier case SHALL stay resolved with `context.resolved_in_statement_id`

#### Scenario: An unclaimed deferral reopens the earlier case
- **GIVEN** an incoming deferral whose entry this statement does not claim
- **WHEN** this statement is confirmed
- **THEN** the earlier case SHALL be `open` at `version + 1` with `context.reopened_by_confirm_of`, the earlier statement's counts recomputed, and this confirmation SHALL succeed

#### Scenario: A dirty flag still refuses
- **GIVEN** `needs_recheck` set
- **THEN** confirm SHALL be refused on `needs_recheck` although the incoming deferral was processed

#### Scenario: A link appearing between discovery and the locks is confirm_retry
- **THEN** 409 `confirm_retry`, and the next confirm processes the link; a linker waiting on the confirm links after it

#### Scenario: Deferral links to a reconciled target set no flag
- **WHEN** `fill_deferral_links` links to a reconciled target
- **THEN** `needs_recheck` SHALL NOT be set and `incoming_deferrals_pending` SHALL show the link

#### Scenario: A new revision of a confirmed statement opens a conflict
- **THEN** confirm SHALL be refused on `conflict_open` until `resolve_conflict` settles it

### Requirement: Manual statements

`POST /accounts/{account_id}/statements` (`write`) SHALL create an owner-entered statement with the header and lines of an import (`kind`, `currency`, `period_start`, `period_end`, optional `closing_date`, `due_date`, `opening_balance`, `minimum_payment`, required `statement_total`, up to 2000 lines with unique `seq`) without the worker's run, lease, file and parser fields. The service SHALL create and claim an `owner_cli` run of its own (finished `done` with `{"manual": true}`) and submit a revision with `file_id=NULL`, `parser="manual"`, `parser_version="1"`, `raw={}` and `origin='manual'`, so derivation, guardrails, lineage, cases and the reconcile hook are those of an import; the response is the revision result (201). It SHALL audit `manual_statement_created`, 404 an unknown account, and run the policy after the commit when the statement is live. A statement without lines SHALL be allowed. Hermes and worker tokens SHALL get 403.

#### Scenario: Manual statement is created, reconciled and policy-run
- **WHEN** the owner posts a manual statement for an account
- **THEN** a live statement with `origin='manual'` SHALL exist, be reconciled, and the policy SHALL run

#### Scenario: A historical manual statement skips the policy
- **THEN** no policy pass runs for it

#### Scenario: Scopes and validation
- **THEN** a token without `write` SHALL get 403, more than 2000 lines SHALL be 422, and `submit_revision` with an `origin` argument SHALL override the file rule

### Requirement: Sibling-account candidates

The admin setting `account_siblings` (`{"<account id>": [account ids]}`; every account exists and is not archived; siblings share the key account's currency, are distinct and not the key itself; keys normalised to integer strings; replaced wholesale by `PUT /settings/reconciliation`, default `{}`) SHALL let a statement whose account has siblings show, in `line_unmatched` cases only, `context.sibling_candidates`: at most five `{entry_id, account_id, flow, posted_date}` of unclaimed, top-level, ungrouped entries on the sibling accounts in the statement's currency whose absolute flow equals the line's and whose `posted_date` (else `entry_date`) lies within `exact_window_days` of the line's posting date, nearest date first then id. Sibling rows SHALL never be in the automatic population. The `match` action SHALL accept such an entry only with `allow_sibling=true` (else 422 `wrong_account`); coverage snapshots keep the entry's own `account_id`; the claim is `manual` coverage and survives reconcile.

#### Scenario: Unmatched line lists sibling candidates
- **GIVEN** siblings configured and an unclaimed sibling entry of the same amount inside the window
- **THEN** the case context SHALL list it with its account, flow and date

#### Scenario: No siblings leaves the context alone
- **THEN** no `sibling_candidates` key SHALL exist

#### Scenario: Claimed entries are not listed, the list is capped and ordered
- **THEN** a claimed sibling entry SHALL be absent and more than five candidates SHALL be cut to five, nearest date first

#### Scenario: Match needs allow_sibling
- **WHEN** `match` names a sibling entry without `allow_sibling`
- **THEN** 422 `wrong_account`; with `allow_sibling=true` it SHALL be claimed and survive a reconcile

## MODIFIED Requirements

### Requirement: Eligibility gates

A statement line SHALL be matched only against ledger rows whose class is admitted by the gate of its statement kind and line kind. Card gate: `purchase` and `installment` admit `expense`, `refund` admits `refund`, `payment` admits `transfer_in`, `fee` admits `fee`, `interest` admits `interest`, `reward` admits `reward`. Bank gate: `deposit` admits `income`, `transfer_in`, `interest` and settlement inflows; `withdrawal` admits `expense`, `transfer_out`, `fee` and settlement outflows; `payment` admits `settlement_out` and `transfer_out` (a card auto-pay or loan repayment is never a plain expense) and `purchase` admits `expense` (a debit-card spend); `transfer_in`, `transfer_out`, `fee`, `interest`, `refund` and `reward` admit their own kind. A settlement entry (`is_settlement`) takes the class `settlement_in` when its flow is positive and `settlement_out` otherwise. A card `payment` line SHALL only match a `transfer_in` whose peer leg is a non-card account. Every row a line can claim (a standalone entry, a child, a group member, an installment instance) SHALL be on one of the statement's participating accounts (its own and the accounts combined into it) AND in the statement's currency; a group with a member failing that gate is partial and never admissible, and a child failing it never completes its parent. A card `payment` line's near and foreign candidates SHALL pass the same peer check. An `installment` line SHALL be matched only through a confirmed plan map (`installment_plan_map`, key `<merchant_norm>|<installment_total>|<abs flow at 4 dp>`) to the schedule definition of an entry's posted instance with the same sequence and flow; without a mapping the line is never auto-claimed. An entry that is a posted instance of a RECURRING schedule definition is an ordinary standalone entry; only instances of `installment` definitions are not standalone (`InstanceRef.definition_kind`). A bank `payment` or `withdrawal` line SHALL additionally be able to claim a COMPLETE schedule `installment` group (representation kind and rule `installment_group`, scored like a group): every top-level member unclaimed, claimable (participating account, statement currency), carrying an instance of the SAME definition, with `Σ member.flow == line.flow`; member kinds are not gate-checked (principal `payable` plus `interest`); card statements never use it and a partial group is never admissible. A split group SHALL be admissible only when every top-level member is a candidate, none is claimed and none belongs to an installment instance: a partial group is never admissible. Children (`parent_entry_id` set) SHALL reach a line only through the child path (as part of their parent's representation, or alone as an uncovered fee/discount child claimed by a separately printed non-purchase, non-payment line), never as standalone rows. Candidates are the participating accounts' entries (the statement's account plus accounts combined into it) dated by `posted_date` (else `entry_date`) within `candidate_window_days` (10) of the period, excluding `balance_adjustment`, excluding `reward` unless the statement prints a reward line, and excluding rows actively claimed by another statement.

#### Scenario: Exact standalone expense is claimed
- **GIVEN** a card line `purchase` of `-580` on 2026-09-03 and one expense entry of `-580` on 2026-09-03
- **WHEN** the line is matched
- **THEN** the line SHALL be claimed by that entry with rule `exact` and no case SHALL be opened

#### Scenario: Another currency is never claimed
- **GIVEN** a TWD card statement whose combined child account holds USD, and a USD entry of `-580` on the line's date with the line's merchant
- **WHEN** the TWD line of `-580` is matched
- **THEN** it SHALL NOT be claimed, and neither SHALL a group with a USD member nor a parent completed only by a USD child

#### Scenario: Payment needs a non-card transfer peer
- **GIVEN** a card `payment` line of `5000` and a `transfer_in` entry of `5000` whose peer leg is a non-card account
- **WHEN** the line is matched
- **THEN** it SHALL be claimed with rule `payment`
- **AND** a `transfer_in` whose peer is a card or unknown SHALL NOT be eligible

#### Scenario: Installment is gated by the confirmed plan
- **GIVEN** an `installment` line for `APPLE`, 12 instalments, `-1000`, sequence 2, and an entry of `-1000` that is instance 2 of definition 7
- **WHEN** no plan is confirmed for `APPLE|12|1000.0000`
- **THEN** the line SHALL stay a `line_unmatched` case with hint `installment_unmapped` and candidates `[{"definition_id": 7}]`
- **AND** once `APPLE|12|1000.0000` maps to definition 7 the line SHALL be claimed with rule `installment`

#### Scenario: Plan mapped to another definition stays a case
- **GIVEN** the plan key `APPLE|12|1000.0000` maps to definition 7 and the only instance entry belongs to definition 8
- **WHEN** the line is matched
- **THEN** it SHALL NOT be claimed and SHALL be a `line_unmatched` case

#### Scenario: Partial group is never admissible
- **GIVEN** a split group with members `-300` and `-280` where only the `-300` member is inside the candidate window
- **WHEN** a `-300` line is matched
- **THEN** the line SHALL NOT be claimed and SHALL be a `line_unmatched` case

#### Scenario: Bank withdrawal respects groups and installment members
- **GIVEN** a bank statement and a split group {`-300`, `-280`}
- **WHEN** a `withdrawal` of `-300` is matched it SHALL NOT claim the lone member, and a `withdrawal` of `-580` SHALL claim both members with rule `group`
- **AND** a `withdrawal` of `-1000` SHALL NOT claim an entry that is an installment instance

#### Scenario: Orphan refund is eligible
- **GIVEN** a refund entry of `200` with no `refunds_entry_id`
- **WHEN** a card `refund` line of `200` on the same date is matched
- **THEN** it SHALL be claimed with rule `refund`

#### Scenario: Bank payment claims a settlement and never an unrelated expense
- **GIVEN** a bank `payment` line of `-3000` and a `settlement_out` (or `transfer_out`) entry of `-3000`
- **WHEN** the line is matched
- **THEN** it SHALL be claimed with rule `payment`
- **AND** an unrelated same-amount `expense` SHALL NOT be claimed and the line SHALL be a `line_unmatched` case

#### Scenario: Bank purchase claims an expense
- **GIVEN** a bank `purchase` line of `-250` and an `expense` of `-250`
- **THEN** the line SHALL be claimed with rule `exact`

#### Scenario: Recurring instances are standalone, installment instances are not
- **GIVEN** a bank `transfer_in` line and a `transfer_in` entry that is an instance of a recurring definition
- **THEN** the line SHALL be claimed by it
- **AND** an entry that is an instance of an installment definition SHALL NOT be a standalone candidate

#### Scenario: Bank payment claims a complete installment group
- **GIVEN** a loan `installment` group of `payable` `-8000` and `interest` `-500` entries, and a bank `payment` (or `withdrawal`) line of `-8500`
- **THEN** the line SHALL be claimed with rule `installment_group` on both members with role `member`

#### Scenario: Partial, mixed-definition or non-summing installment groups are never admissible
- **GIVEN** a group with a member outside the population, members of two definitions, or a total different from the line
- **THEN** the line SHALL NOT be claimed by it

#### Scenario: A card purchase never uses an installment group
- **THEN** a card `purchase` line equal to a group total SHALL NOT claim the group

### Requirement: Reconcile

`reconcile(statement)` SHALL run in one transaction and never commit. Lock order: the shared import key (`take_import_key_shared`, a non-waiting try-lock on every path: while an import holds the key it raises `ImportRunningError` at once; the revision hook (`locked=True`) runs reconcile inside a savepoint without ever waiting for the dirty barrier (its transaction already holds the coverage rows the lineage transfer moved) and on any conflict (`import_running`, `reconcile_busy`, `sweep_barrier_busy`, `duplicate_claim`, conservation) or a `DeadlockDetected` rolls back only that savepoint and sets `needs_recheck`, the revision still being stored and current (any other driver error fails the submission); the reconcile route answers HTTP 409 `import_running`, and the batch records `error: "ImportRunningError"` for that statement and goes on) → the statement row `FOR UPDATE` → flush → sweep (before any `entry_group` or `ledger_entry` lock, so the barrier cannot deadlock a writer) → `entry_group` rows ascending → the candidates, their children and their transfer legs in ONE ordered `SELECT … FOR UPDATE` (the group and entry locks NOWAIT inside a savepoint: a row held by another transaction raises a 409 conflict with code `reconcile_busy`, because a ledger delete locks the entry first and then the coverage/case rows the sweep may already have written) → re-read the population under the locks (rows that joined, or whose group changed, in between are left to the next sweep) → match → coverage → cases → counts. Reconcile SHALL write no ledger rows. It SHALL skip with `no_current_revision` when the statement has no current revision or the current one failed its guardrails, and with `parse_review` when a `parse_review` case is open/proposed (both after the sweep). Release and re-derive: a line whose active claim equals the new claim (same rows, roles, rule and snapshots) keeps its coverage rows; every other active row (including orphans whose entry was deleted) is released with reason `reconcile` and the new claim written, EXCEPT active `manual` or `agent` coverage (owner actions and policy-applied agent proposals): such a line counts as already claimed (excluded from the matcher input, its rows excluded from the candidate population, sibling-account rows included) and its coverage is released only by the sweep (a dirty edit on a covered row) or by lineage (`changed`/unpaired events). Matcher cases (`line_unmatched`, `ambiguous`, `amount_delta`, `entry_unmatched`, `duplicate_claim`, `balance_gap`, `recheck`) SHALL be written in live mode only: an open/proposed case equal to a newly derived one (kind, event, entry, candidates and context without the carried `sweep` note) keeps its id (its line pointer refreshed), every other open matcher case becomes `superseded` (version bumped), the rest are created, a superseded case's sweep event is carried to its successor as `context.sweep`, and a `dismissed` case of the same kind on the same event, entry or gap is never reopened. `parse_review` and `statement_conflict` cases are not touched. Reverse population (entries in the period not claimed): an entry posted later than `period_end − deferral_days` (2; with `period_end` 2026-09-30 only 2026-09-29 and 2026-09-30) SHALL be explained as `deferred_next_period` and opens no case; a claimed parent's unclaimed child SHALL open `entry_unmatched` with hint `uncovered_child`; any other becomes `entry_unmatched`, with hint `move_account` (statement id and line id) when an uncovered line of another live statement outside the participating accounts, same currency and flow, is printed within ±3 days of the entry. Deferral cases (live mode only, like all cases) are `resolved` `entry_unmatched` cases with explanation `deferred_next_period`, `resolved_by` NULL and `deferred_to_period_end` from the account's cycle (card: the next closing date after `period_end`, the day clamped to the month; bank: the last day of the next month). An earlier statement SHALL NOT lock or reference a later one: `deferred_to_statement_id` is filled only by the later statement's reconcile (a case-rows-only update matching account, currency and `deferred_to_period_end = period_end`) or by `fill_deferral_links` (batch), an existing link is kept and cleared only when `deferred_to_period_end` itself changes, the linking runs under the target statement's row lock (it serialises with confirm), and a deferral whose entry was evaluated and is no longer deferred is superseded. A bank statement SHALL open a `balance_gap` case with context `{"gap": "<statement_total − account balance as of period_end>"}` (4 decimals) when the gap is non-zero. After every run `matched_count` SHALL be the number of lines with active coverage, `explained_count` the number of cases `resolved` with an explanation plus `dismissed` cases, `open_case_count` the open/proposed cases, the recount SHALL be counts-only (`_recount` never touches `needs_recheck`; the action engine and confirm use the same counts-only recount), and a run that is not skipped SHALL clear `needs_recheck` as its LAST step, after the recount (a skipped run returns before it and leaves it set); `needs_recheck` therefore keeps one meaning: matching must run again (set by the sweep's population changes and by `unmatch`). An owner deferral (`defer_next_period`) stands in for the automatic deferral case and is kept. A reconcile run inside an action (`locked=True`) never waits at the writer barrier. Re-running with unchanged inputs SHALL be idempotent.

#### Scenario: Card statement end to end
- **GIVEN** a live card statement with matching, ambiguous and unmatched lines and entries
- **WHEN** it is reconciled
- **THEN** claims, cases and counts SHALL be written and `assert_conserved` SHALL pass

#### Scenario: Re-reconcile is idempotent
- **WHEN** the same statement is reconciled twice
- **THEN** coverage row ids and open case ids SHALL be unchanged

#### Scenario: Changed outcome supersedes the stale case and keeps dismissed ones
- **GIVEN** an open case that the new run no longer derives and a dismissed case of the same kind on the same event
- **WHEN** the statement is reconciled
- **THEN** the open case SHALL be `superseded` and the dismissed one SHALL stay dismissed with no replacement

#### Scenario: Historical statement gets coverage but no cases
- **GIVEN** a historical statement
- **WHEN** it is reconciled
- **THEN** coverage SHALL be written and no case SHALL exist

#### Scenario: Rows claimed by another statement are not stolen
- **GIVEN** an entry actively claimed by statement A
- **WHEN** statement B is reconciled
- **THEN** the entry SHALL NOT be a candidate of B and A's coverage SHALL stay active

#### Scenario: Combined child spend matches on the master
- **GIVEN** a child card combined into the master card
- **WHEN** the master's statement is reconciled
- **THEN** the child's spend SHALL be matchable on the master's statement

#### Scenario: Uncovered child is an entry case
- **GIVEN** a claimed group member with an unclaimed fee child of `-2`
- **WHEN** the statement is reconciled
- **THEN** an `entry_unmatched` case with hint `uncovered_child` SHALL open for the child

#### Scenario: Installment matches through the confirmed plan
- **GIVEN** a confirmed plan map for the line's key
- **WHEN** the statement is reconciled
- **THEN** the line SHALL be claimed with rule `installment`

#### Scenario: Changed re-parse waits for the quarantine
- **WHEN** a live statement receives a changed re-parse of a covered line
- **THEN** the revision hook SHALL leave the pass to the open `parse_review` and the next reconcile SHALL report `skipped = "parse_review"`

#### Scenario: Deferral is explained then linked by the later statement
- **GIVEN** a card with `closing_day` 25, an entry of `-90` on 2026-09-24 and a September statement (2026-08-26 to 2026-09-25) that does not print it
- **WHEN** the statement is reconciled
- **THEN** a `resolved` `entry_unmatched` case with explanation `deferred_next_period`, `deferred_to_period_end = 2026-10-25` and `deferred_to_statement_id` NULL SHALL exist and `explained_count = 1`
- **AND** when the next statement (2026-09-26 to 2026-10-25) prints the entry and is reconciled, the case SHALL link to it and survive a re-reconcile of the first statement

#### Scenario: Batch fills the link when the later statement already exists
- **GIVEN** a bank with October already imported and a September statement leaving a `-90` entry on 2026-09-30 deferred
- **WHEN** `fill_deferral_links` runs
- **THEN** it SHALL link exactly 1 case to the October statement and a second call SHALL link 0

#### Scenario: Next period end rules
- **THEN** a closing day of 30 after 2026-09-30 SHALL give 2026-10-30, 31 after 2026-01-31 SHALL give 2026-02-28, a bank after 2026-09-30 SHALL give 2026-10-31

#### Scenario: Cross-account hint
- **GIVEN** an entry of `-444` on 2026-09-08 on card A and an uncovered line of `-444` on 2026-09-10 on another live card's statement
- **WHEN** card A's statement is reconciled
- **THEN** the entry's `entry_unmatched` case SHALL carry context `{"hint": "move_account", "statement_id": <other>, "line_id": <line>}`

#### Scenario: Bank balance gap
- **GIVEN** a bank account opening at `1000`, a `500` income entry matched on 2026-09-05, a `-70` entry on 2026-10-02 and a statement printing `deposit` lines of `500` and `100`
- **WHEN** the statement is reconciled
- **THEN** a `balance_gap` case with context `{"gap": "100.0000"}` and no line or entry SHALL be open, kept by id on a re-run, and a balanced statement SHALL have none

#### Scenario: Skips
- **WHEN** the current revision failed its guardrails, or a `parse_review` is open
- **THEN** reconcile SHALL return `skipped` `no_current_revision` or `parse_review` after sweeping

#### Scenario: needs_recheck is cleared only by a run that is not skipped
- **GIVEN** `needs_recheck = true`
- **WHEN** the statement is reconciled
- **THEN** it SHALL be false
- **AND** when the run skips (`no_current_revision` or `parse_review`) it SHALL stay true

#### Scenario: Import in progress defers the hook
- **GIVEN** an import holds the import key exclusively
- **WHEN** a revision becomes current
- **THEN** the hook SHALL set `needs_recheck` and return no cases
- **AND** the reconcile route SHALL answer 409 `import_running` and the batch SHALL list `ImportRunningError` for that statement

#### Scenario: Edit waits for the reconcile
- **GIVEN** a reconcile holds the entry locks
- **WHEN** a writer edits a claimed entry
- **THEN** the edit SHALL wait for the reconcile to commit and the next sweep SHALL release the line

#### Scenario: Reconcile yields to a delete of a claimed entry
- **GIVEN** a sweep has released a claimed entry's line and a delete of that entry holds the entry lock while waiting on the coverage row
- **WHEN** the reconcile takes its entry locks
- **THEN** it SHALL raise `reconcile_busy` (route 409, batch error `reconcile_busy`) and the delete SHALL commit
- **AND** when the same race hits the revision hook, the ingest SHALL commit with `needs_recheck` set

#### Scenario: The revision hook never waits for the barrier
- **GIVEN** a re-parse has transferred a coverage row and a writer holding the shared dirty barrier waits on that row
- **WHEN** the hook sweeps
- **THEN** it SHALL try-lock the barrier, record `needs_recheck` and the ingest and the writer SHALL both commit

#### Scenario: Manual and agent coverage survive a reconcile
- **GIVEN** a line claimed by an owner `match` (`manual`) or a policy-applied agent proposal (`agent`), including a sibling-account entry
- **WHEN** the statement is reconciled again
- **THEN** that coverage SHALL be unchanged and its entry SHALL NOT be offered to other lines

#### Scenario: A dirty edit releases a manual claim through the sweep
- **GIVEN** a manually claimed entry edited afterwards
- **WHEN** the sweep runs
- **THEN** the claim SHALL be released and the line matched again

#### Scenario: needs_recheck is discharged only by a completed reconcile
- **GIVEN** `needs_recheck` set by `unmatch`
- **THEN** an action's recount SHALL leave it set, and a completed reconcile SHALL clear it

### Requirement: Statement read model

A statement detail (`GET /accounts/{id}/statements/{statement_id}`) SHALL return, besides the R1a fields, for each current line `matched` (true when it has active coverage) and `coverage[]` (each active row's `entry_id`, `group_id`, `role`, `match_rule`, `match_kind`, `status` and snapshot `flow`), the statement's `matched_count`, `explained_count` and `open_case_count`, `incoming_deferrals_pending` (the number of cases with `deferred_to_statement_id` = this statement, explanation `deferred_next_period`, status `resolved` and no `context.resolved_in_statement_id`, i.e. earlier deferrals the next confirm has not processed; also on the statement list) and `stale_events_pending`. A case (`GET /reconciliation/cases/{id}`, `CaseDetailOut`) SHALL return the full case row (`statement_id`, `revision_id`, `resolved_by`, `resolved_action_id`, `resolved_at`, `deferred_to_period_end`, `deferred_to_statement_id`, `note`, `version`, `candidates`, `context`), `actions_allowed` (computed by the action registry from the case kind, status and context; `[]` once the case is not open or proposed), `pending_proposals` (after stale ones were superseded and committed) and its `line` with `matched` and `coverage`; `GET /reconciliation/cases` keeps the R1b `CaseOut` fields; `POST /accounts/{id}/statements/{sid}/reconcile` additionally returns `policy: {applied, proposed}` counts. Unknown cases SHALL be 404. `stale_events_pending` SHALL be true exactly when `sweep_pending` would list the statement, i.e. a dirty event past its watermark that may touch it, not any ledger change on the account.

#### Scenario: Unrelated and related events
- **GIVEN** dirty tracking on and a freshly submitted statement
- **THEN** `stale_events_pending` SHALL be false
- **WHEN** an entry of `-5` is added on the account inside the period
- **THEN** it SHALL be true

#### Scenario: Matched line carries coverage
- **GIVEN** a line claimed by an entry
- **THEN** its `matched` SHALL be true and `coverage` SHALL list that entry with its role and rule

#### Scenario: Case detail carries the actions and pending proposals
- **GIVEN** an open `line_unmatched` case with a pending proposal
- **THEN** `actions_allowed` SHALL list the registry's actions, `pending_proposals` the proposal and `line.coverage` the line's claim; an `entry_unmatched` case has no line and offers `defer_next_period`, `move_account`, `dismiss`
- **AND** an unknown case SHALL be 404 and a stale pending proposal SHALL read `superseded`

#### Scenario: Incoming deferrals are a read-model signal
- **GIVEN** a resolved deferral case linked to a reconciled later statement and not yet processed by its confirm
- **THEN** both the statement list and the statement detail SHALL show `incoming_deferrals_pending: 1`
