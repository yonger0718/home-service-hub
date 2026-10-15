# Statement reconciliation R6 — matching rules and statement-as-truth (design)

Date: 2026-10-15. Status: rev 9 — R6a and R6b core PASSED spec review at rev 8 (AGENT-129, 06:31 UTC, with two Shoulds folded here: wording sync, sweep-retirement serialization); rev 6 split transfers out into R6c (owner decision 8); R6b keeps the single-account actions. Folds the AGENT-129 reviews of rev 2/3/4/5; for Multica re-review (AGENT-128). Three stacks: R6a (matcher), R6b (backfill, anchor, replacement), R6c (transfers; separate spec round).
Parent spec: `2026-10-08-statement-reconciliation-design.md` (§6 matching engine, §7 cases/actions/policy, §17 R1c/R1d/R5 notes). This document amends §6 and §7; where the two disagree, this one wins for R6 and later.

## 0. Why

After R5 (OCR) every live statement parses. The open-case population on 2026-10-15 (354 cases over 58 live statements, 20 accounts) was classified by distance to the nearest ledger entry (report `~/reports/home-hub-cases-20261015T033113Z/`, aggregates only). The population is dominated by rule gaps, not by owner mistakes:

| class | cases | cause found in code |
|---|---|---|
| same amount, 1–3 days apart | 26 | `exact_window_days` is already 3, but the score formula (`0.60 + 0.30·(1−dist/4) + 0.10·text`, accept 0.80) cannot reach 0.80 at distance 3 and needs merchant-text overlap at distance 2. The window is effectively ±1 day. A single candidate at distance 2–3 scores ≥ 0.50 and becomes an `ambiguous` case with one candidate. |
| same day, same amount, not matched | 19 | `receivable` / `payable` entries are absent from every gate (`CARD_GATES`, `BANK_GATES`); card purchase accepts only `expense`. |
| same day, amount within 5 % | 29 | `_near_case` requires merchant-token overlap ≥ half (MOZE memo ≠ statement merchant text), tolerance is 3 %, and it never claims. |
| same amount, 4–15 days apart | 11 | no rule between the exact window (3) and nothing. |
| bank-side automatic lines with no ledger entry | 63 | `bank_only` hint is merchant-pattern based (6 Chinese patterns); only 14 of 63 matched a pattern, so the policy's `create_system_entry` saw 14. |
| `balance_gap`, every bank month | 27 | ledger balance = MOZE opening balance + entries; never aligned with the statements; 5 of 10 bank accounts have no ledger entries at all. |

Owner decisions taken in the brainstorm (2026-10-15):

1. **Tiered risk (B).** Exact amount within 3 days and receivable↔purchase auto-claim. Residual ≤ 5 % and 4–15-day drift become proposals on a case; the owner confirms.
2. **`deposit` / `unknown` lines (A).** Not system entries. `unknown` is never `bank_only` and (confirmed 2026-10-15 after review) never backfilled; `deposit` lines are backfilled as `income` under decision 4.
3. **Statement as truth for bank accounts (owner's reframing, option A).** Instead of aligning MOZE opening balances by hand, the ledger is backfilled from statement lines and anchored at the first live statement's opening balance; from there every month's gap is a real missing entry. Scope: all bank accounts, all live months.
4. **Cards too, with a grace period (C).** Card purchase lines still unmatched 14 days after the statement's period end are backfilled as uncategorised expenses. The grace keeps late MOZE entries from colliding with backfilled ones; when they do collide, the owner's entry wins.
5. **Two stacks (A).** R6a (matcher rules, no ledger writes) ships and runs first; R6b (backfill, anchor, owner-wins) follows. Backfilling before the scoring fix would book lines that should have matched.
6. **HomeHub becomes the primary ledger (A, 2026-10-15).** Backfilled entries live only in HomeHub (nothing is written back to MOZE). From the cutover (2026-10-31 at the latest) the owner categorises `對帳待分類` entries and records new transactions in HomeHub; MOZE is input only until its final import and is not kept in step afterwards. Owner's wording: new data from October is appended in HomeHub once the move is complete. The owner-wins rule (R6b.5, `replace_stand_in`) therefore matters mostly for the transition window and uses the drift window (15 days), not the exact window.
7. **Transfers are booked too (both, 2026-10-15).** Card payments and own-account transfers are the largest flow on the bank accounts with no ledger entries, so leaving `transfer_in/out` and `payment` lines open keeps a `balance_gap` case on most bank months. Pairing a line with its counterpart on another live statement, and booking the rest against a suspense account `對帳待配對` or as spend, is wanted.
8. **Split transfers into R6c (A, 2026-10-15, after the rev 3–5 reviews).** The transfer actions carry most of the remaining review risk (two-statement locking and case lifecycle, per-leg dates, reciprocal uniqueness, whole-transfer replacement, suspense naming). R6b ships the single-account actions first; R6c gets its own spec round using the AGENT-129 findings listed in R6b.9 as inputs. Until R6c, transfer/payment lines stay open cases with the `needs_peer` hint and the bank `balance_gap` cases on accounts with unbooked transfers stay open with a known residual.

Balance-gap evidence (`~/reports/home-hub-balance-gaps-20261015T050149Z/`): only one of ten accounts has a constant gap; the rest drift month to month, so an opening-balance edit alone would not close them. This is why 3 replaced the "align opening balance in MOZE" option.

## 1. R6a — matcher rules (`app/services/statements/matching.py`, settings)

All of R6a is pure matching logic plus rule defaults. No ledger writes, no migration, no new case kind.

### R6a.1 Score of exact-amount representations

Today `_score` returns `0.60 + 0.30·max(0, 1 − dist/4) + 0.10·text`. New:

```
score = 0.80 + 0.10 · (1 − dist / exact_window_days) + 0.10 · text      (dist ≤ exact_window_days)
score = 0                                                                 (dist > exact_window_days)
```

`text` is the existing Jaccard term in [0, 1]. Any conserved exact-amount representation inside the window scores ≥ `accept` (0.80). When `exact_window_days == 0` (a valid setting) the date term is `0.10` for `dist == 0` and the representation does not exist otherwise — no division by the window (ruling on AGENT-129 Must 5; unit test for window 0). The quantum `Q` and `_installment_score` (0.90 + text inside `candidate_window_days`) are unchanged.

`margin` default changes 0.15 → 0.05 on the new scale. With equal text, two candidates whose date distances differ by ≥ 2 days (window 3: 0.033 per day) resolve to the closer one, and two on the same day or one day apart stay `ambiguous`. Text still competes: a farther candidate with much better merchant overlap can win or force ambiguity (distance 0 / text 0 → 0.90 against distance 2 / text 1 → 0.933 stays ambiguous), which is the intended global competition of parent §6, not a date-only rule. `accept` stays 0.80, `ambiguous_floor` 0.50. Tests cover text-driven reversal, exact ties, a split-group representation competing with a standalone one, and `claimed_by_earlier_line` under the new scale (AGENT-129 Should 1).

Settings compatibility: `rules_version` becomes `r6a-1`. A stored override of `accept`, `margin` or `ambiguous_floor` written under an `r1b-*` version is ignored by `rules_for()` with one warning log line naming the key (the old scale is meaningless); the owner re-sets it if wanted. Other stored overrides keep applying.

### R6a.2 Ambiguity needs two candidates

`match()` emits `ambiguous` only when `len(reps) ≥ 2` and the best is ≥ `ambiguous_floor` and the margin test fails. With R6a.1 a single in-window exact representation always claims, so the single-candidate `ambiguous` disappears by construction; the explicit `≥ 2` guard makes that a contract and a test, not a side effect. The installment path keeps its own rule (`_installment_case`).

### R6a.3 Gates: receivable and payable

```
CARD_GATES["purchase"]     = {expense, receivable}
CARD_GATES["installment"]  = {expense, receivable}
BANK_GATES["withdrawal"]  += {receivable}
BANK_GATES["deposit"]     += {payable}
```

Only non-settlement rows reach these gates: `_entry_class` already maps `is_settlement` rows to `settlement_in`/`settlement_out`, and the ledger constraint forces a receivable with amount > 0 and a payable with amount < 0 to be settlements. So a receivable in the purchase gate is "I paid for someone" (amount < 0, conserved against a purchase flow) and a payable in the deposit gate is "someone paid me / lent me" (amount > 0). `conserved` keeps sign and amount checks. Near, drift and foreign paths use `eligible_kinds`, so they inherit the change.

### R6a.4 Near rule (`_near_case`)

- `near_tolerance_pct` default 0.03 → 0.05 (`near_tolerance_abs` stays 10, `near_window_days` stays 5).
- Merchant-token rule applies only when `dist ≥ 1`. At `dist == 0` (same day) a candidate inside the tolerance needs no text overlap.
- `payment` lines are not special-cased; they pass through the gate and `_peer_ok` exactly as today (card payment ↔ `transfer_in` whose peer is known not to be a card).
- The case stays `amount_delta` with `fee_matches: False`, `residual`, ordered candidates; it never claims. Resolution: owner `match` / `adjust_amount` / `attach_fee` / `split_entry` as today. The policy's `attach_fee` (fee_matches only) is unchanged.

### R6a.5 Date drift (`_drift_case`, new)

New rule key `drift_window_days: int = 15` (settings-checked like the other int keys, 0..10000). The service's candidate population widens accordingly (`population_window_days`, R6b.0 — shipped with R6a so that drift candidates 11–15 days outside the period are loaded; AGENT-129 Must 4). For a line that reached the fallback chain (no claim, no ambiguity, no `claimed_by_earlier_line`, no foreign, no near): candidates are standalone, gated, `_peer_ok` entries with `_same(|flow|)` and conserved sign, not claimed, with `exact_window_days < dist ≤ drift_window_days`. If any: case kind `amount_delta`, `entry_id` = closest (ties: lower entry id), `context = {"hint": "date_drift", "date_distance": dist, "entry_id": id, "residual": "0", "fee_matches": False}`, candidates `[{entry_id, reason: "date_drift", date_distance}]` ordered by distance then id. Reusing `amount_delta` avoids a new enum value and migration; `KIND_ACTIONS["amount_delta"]` already allows `match` and `dismiss`. The policy never acts on it (`_attach_fee_candidate` requires `fee_matches is True`; nothing else consumes `amount_delta`).

Order in `match()` after R6a: claim → installment → ambiguous (≥ 2) → claimed_by_earlier_line → foreign → near → drift → bank_only → `line_unmatched {}`.

### R6a.6 `bank_only` by line kind

`_bank_only(line, rules)` returns a hint whenever `line.line_kind ∈ {fee, interest, reward}`; the context becomes `{"hint": "bank_only", "pattern": <first matching pattern or null>}`. `bank_only_patterns` stays in `Rules` (stored settings may carry it) and only annotates. `unknown`, `deposit`, `withdrawal` lines are never `bank_only` (decision 2). `policy_service._system_entry_candidate` and `_unchanged` already key on the hint plus `line_kind ∈ SYSTEM_LINE_KINDS`, so the policy picks up all 63 bank-side automatic lines with no code change on its side; `reconciliation_actions._system_line` is already kind-based.

### R6a.7 Evidence harness (`services/accounting-service/scripts/r6_dryrun.py`)

Read-only. Opens a DB session, and for every live statement computes the desired case set twice — once with the **baseline implementation** (the matcher and service modules imported from a pinned baseline checkout, by default the deployed worker checkout at the merge-base commit, passed as `--baseline-checkout <path>`; stored settings over that checkout's defaults) and once with the R6a implementation from the working tree — through each tree's `_desired_cases`, then rolls back. Swapping rule values alone cannot reproduce the old score, gates and bank-only behaviour once those functions change (AGENT-129 Should 4), so the baseline is code, not numbers. Output (file, mode 600, under `~/reports/home-hub-r6a-dryrun-<ts>/`): counts by (case kind, hint, matcher rule) before/after, and the per-line delta as `(statement_id, line_id, before, after, rule that changed it)`. No amounts, no merchants in the summary; the detail file is for the owner. Merge gate for R6a: every delta is attributed to one of R6a.1–R6a.6; no `ambiguous` case with one candidate remains; no exact-amount claim present before is lost after. A `--policy` mode is added by R6b (R6b.7).

### R6a.8 Tests

`tests/unit/test_statement_matching.py` (helpers `L`, `E`, `run`): distance 3 same amount claims; distance 2 with no text claims; two same-day exact candidates stay ambiguous; candidates at distance 0 and 2 claim the closer; single candidate never ambiguous; receivable (non-settlement) matches a purchase, receivable settlement does not; payable matches a deposit; near at distance 0 without token overlap, at distance 1 still requires overlap; near at 4.9 % passes, 5.1 % fails; payment line near via `_peer_ok`; drift at 4 and 15 days emits `amount_delta` with `date_drift`, at 16 does not; drift ordering; `bank_only` for an interest line without any pattern, never for `unknown`. `tests/integration/test_reconciliation_service.py`: a drift case round-trips through `_desired_cases` and resolves with `match`; a bank-only-by-kind case is a `create_system_entry` candidate in `test_reconciliation_policy.py`. `settings_service` tests: `drift_window_days` validated; stale `r1b-*` override of `margin` ignored with a log record.

## 2. R6b — statement as truth (actions, policy, settings)

R6b adds three actions (`backfill_entry`, `anchor_opening`, `replace_stand_in`) and one matcher notion (the *stand-in*); two more (`pair_transfer`, `backfill_transfer`) are deferred to R6c. All actions are ordinary engine actions — registered, listed in `KIND_ACTIONS` with a `CONDITIONS` entry, `actors = {OWNER, POLICY}` — so the engine's `allowed(case)` check, the proposal path (`proposal_service._validate`) and the owner's confirmation of an over-cap proposal work unchanged (AGENT-129 Must 1). "Policy-driven" means the policy's candidate builders find and apply them; the owner may also run each one directly from a case, without the grace. No migration: `reconciliation_action.action` is `String(32)`, no new case kind, new settings are JSON keys.

### R6b.0 Stand-ins

A **stand-in** is a ledger entry with `source == "statement"` created by an applied `backfill_entry` action (provenance: `reconciliation_action.created_entry_ids`, action status `applied`; R6c will add its transfer actions to this set). System entries from `create_system_entry` and `balance_adjustment` rows are not stand-ins. The service marks stand-ins on the matcher population (`Entry.stand_in: bool`, default False). Rule in `_representations`: when a line has at least one non-stand-in representation, its stand-in representations are dropped before scoring; a stand-in competes only against other stand-ins. So an owner entry always beats its stand-in at claim time, and a stand-in whose coverage the sweep released re-claims its line on the next reconcile when nothing else is there (self-healing). An owner entry for the *same* line can therefore arrive in two shapes: with the stand-in still holding the line (R6b.5, `shadowed_by_stand_in`) or with the line released and the owner entry already claimed by the matcher, leaving the stand-in orphaned (R6b.5, `orphaned_stand_in`).

**One stand-in per line event.** The engine's creation-family check (`_check_creation_family`, `engine.py:296–304`) already refuses a second creation for an event while an applied creation exists; `backfill_entry` relies on it; its eligibility is the table in R6b.1 (the owner column and the policy column differ only in the grace, the account list and the owner-deleted exclusion). Lifecycle: a stand-in removed by `replace_stand_in` (R6b.5) leaves its creating action `reverted`, so the event may be backfilled again later if the owner's entry disappears. A stand-in the owner deletes through the ledger is handled by the sweep: on a `delete` event the sweep looks up applied creation actions by `created_entry_ids` containing the deleted id (not only through active coverage, so a creation whose coverage an earlier structural edit released is found too), scoped to the sweeping statement's events; each such action (`backfill_entry`, and likewise the existing `create_entry`, `create_system_entry`, `create_refund`) is retired: `status = "reverted"`, `reverted_by_action_id` left as it is (NULL for a never-reverted action; an existing attribution is never overwritten), `inverse.deleted_by_event = <event id>` (the durable audit of the dirty delete); retirement is idempotent. The line's coverage is released as today and the line case reopens. Serialization: the sweep already runs under its statement's lock (`coverage_service.py:315–317`) and an engine revert or replacement takes the same statement lock before touching the action row (`engine.py:420–424`), so retirement scoped to the sweeping statement's events shares one serialization point with them; the sweep re-reads the action under that lock and retires it only while it is still `applied`, never overwriting an attribution written by a revert or a replacement. No cross-statement locking is needed for this single-statement lifecycle. Without this the line could never receive an entry again: the family check would refuse every creation and the action's own revert fails once its entry is gone (`_Creation.invert`: "entry … is gone") — a latent trap that already exists for `create_system_entry` rows deleted in the ledger.

The **deletion marker is derived, not carried**: an event is *owner-deleted* when its latest creation action is `reverted` with `inverse.deleted_by_event` set and no later applied creation exists for the event. `_desired_cases` recomputes this from the action table on every reconcile and puts `context.hint = "stand_in_deleted"` on the line's `line_unmatched` case, so the hint survives case replacement (`_write_cases` carries only the sweep note) and identical reparses (the event id is stable). Every automatic creation builder in `policy_service` — `backfill_entry` and the existing `create_system_entry` — excludes owner-deleted events (the owner deleted the entry on purpose; recreating it would loop). Owner availability is separate from policy suppression: the `CONDITIONS` for `backfill_entry` accept a case with no hint *or* with `stand_in_deleted` (line-kind and sign restrictions of R6b.1 still apply), and `create_system_entry`/`create_refund` keep their own conditions, so the owner resolves the reopened case with `create_entry`, an owner-run `backfill_entry`, `match` or `dismiss`; the R3 UI shows the hint. An explicit engine revert of a backfill (entry intact) is different: the action is `reverted` without `deleted_by_event`, the line is *not* owner-deleted, and the policy may backfill it again on a later run — the owner who wants silence dismisses the case. The dry-run lists owner-deleted lines. Without this guard a line whose coverage the sweep released could be backfilled a second time before the next reconcile re-claims it; the new stand-in would orphan the owner-categorised one and `replace_stand_in` would delete it.

**Structural-only sweep release.** Today the sweep releases a line's coverage on *any* update of a covered entry (`coverage_service.py:371–376`, the dirty trigger fires on every real column change). R6b narrows it: on an `update` event the sweep recomputes `coverage_service.snapshot_entry` for the entry and compares it with the coverage row's stored `snapshot` (`StatementCoverage.snapshot`, written by `write_claim` since R1) **over the structural key set only**: `STRUCTURAL_KEYS = {amount, flow, currency, original_amount, original_currency, entry_date, posted_date, account_id, kind, group_id, parent_entry_id, transfer_group_id, refunds_entry_id, settles_entry_id, is_settlement}` — every key of `snapshot_entry` except `id`, `name` and `merchant` (`coverage_service.py:44–53`). A difference in any structural key releases; `name`, `merchant` and the fields outside the snapshot (category, description, project, `needs_review`) never release a match. No new column, no version marker: existing rows carry the same shape, so the projection applies to them too. `delete` events release as before; relationship changes (transfer membership, refund or settlement links, parent, group) still release because they are structural keys. This changes sweep behaviour for every covered entry, so the plan carries a regression gate: the R6b dry-run reports, over the pending dirty events on the live statements, how many coverage rows the old rule would release and the new one would keep, broken down by which snapshot keys differ (cosmetic `name`/`merchant` vs each structural key), together with the exact baseline and candidate checkout SHAs and the `STRUCTURAL_KEYS` set used; the release record keeps that table. Tests: category-only, name-only and merchant-only edits keep coverage, on a pre-R6b snapshot fixture and on a new row alike; amount edit, one-day date edit, transfer-peer change, refund link change each release.

Candidate population (AGENT-129 Must 4): the service loads entries for `period ± population_window_days` where `population_window_days = max(candidate_window_days, drift_window_days, near_window_days, foreign_window_days)` (15 with the defaults); `candidate_window_days` keeps its own meaning for installment plans. `in_reverse` (unmatched reporting) stays period-only; the stand-in discovery of R6b.5 runs its own query and does not depend on `unmatched_entry_ids`.

### R6b.1 `backfill_entry`

Eligibility (one helper `backfill_eligibility(case, line) -> str | None` used by both `CONDITIONS[("line_unmatched", "backfill_entry")]` and the policy builder; the policy adds its own rows):

| check | owner (`allowed(case)`) | policy builder |
|---|---|---|
| case kind `line_unmatched`, status open | yes | yes |
| `context.hint` | none **or** `stand_in_deleted` | none only (owner-deleted events are skipped) |
| candidates | empty | empty |
| line kind → entry kind (table below) | yes | yes |
| creation-family check (`_check_creation_family`) | engine, at apply | engine, at apply |
| statement `live`, `period_end + backfill_grace_days ≤ today` | no | yes |
| `backfill_accounts` scoping | no | yes |

A case reopened by an ordinary engine revert (`reverted_action_id`, entry intact) is eligible for both columns: the explicit ruling in R6b.0 (policy may re-backfill after an engine revert) is authoritative. The line kind map:

| statement | line kind | entry kind |
|---|---|---|
| card | `purchase` | `expense` |
| bank | `withdrawal`, `purchase` | `expense` |
| bank | `deposit` | `income` |

Not booked by `backfill_entry`: `unknown` (owner ruling 2026-10-15 on AGENT-129 Must 6: an `unknown` line has an empty gate, so a stand-in for it could never be replaced by an owner entry; the case stays open for the owner's `create_entry`), `transfer_in`, `transfer_out`, `payment` (R6c; the matcher's fallback tags such a case `context.hint = "needs_peer"`, R6b.3/R6b.4), `installment` (plan mapping), `balance_adjustment`, `refund` (`create_refund` stays an owner action).

Booking: `_Creation._book` with `kind`, `category_id = backfill_category_id(db, kind)` (a fixed category `對帳待分類` per kind, created on demand like `system_category_id`), `needs_review=True`, `source="statement"`, merchant/description/dates from the line. The line is claimed (rule `backfill_entry`), the case resolves, `inverse = {created_entry_id}`; the ordinary revert path applies (and, as for every creation, refuses once the entry was edited — the owner then deletes the entry through the ledger instead).

Policy: `POLICY_ACTIONS += {"backfill_entry"}`; `_backfill_candidate` requires statement mode `live`, `period_end + backfill_grace_days ≤ today` (default 14), account in `backfill_accounts` when that list is non-empty, amount `|flow|`, params `{kind}`; caps as the other actions, over cap → proposal; `_unchanged` compares status, kind, absence of hint and candidates, line kind and amount. Owner runs skip the grace and the account list.

### R6b.2 `anchor_opening`

Action on a `balance_gap` case of a bank statement. Condition: the account has at least one live statement with `opening_balance` not null and no applied `anchor_opening` action on any of its statements (provenance = `reconciliation_action` rows joined through `case → statement → account`, AGENT-129 Should 3; no account column is added).

Apply (locks: `LockSet(account_lock=True, barrier=True, statement_ids=<all live bank statements of the account>)`, ascending ids through the engine's existing ordering; refused while `dirty_enabled` is off, as `balance_adjustment_asof`):
1. Target = the account's earliest live statement with `opening_balance` (recomputed under the locks). `as_of = target.period_start − 1 day`; `delta = target.opening_balance − account_balance(db, account_id, as_of)` computed under the barrier.
2. Refuse `zero_delta` when `delta == 0`; refuse `closing_adjustments_present` when the account has any `balance_adjustment` entry dated `≥ as_of` (a closing adjustment booked earlier would overcorrect once the opening is anchored; the owner reverts or deletes those first — none exist on 2026-10-15, the dry-run checks) (AGENT-129 Must 3).
3. Book the `balance_adjustment` entry (`system_category_id(db, "balance_adjustment")`, `entry_date = posted_date = as_of`, description `期初對齊 <as_of>`, `source="statement"`, `needs_review=True`).
4. Set `needs_recheck = True` on every locked statement of the account (the dirty event of an entry dated outside every period marks no statement by itself, AGENT-129 Must 3); the daily run and the sweep's flagged set re-derive their cases, including the target's own residual closing gap. No in-transaction reconcile of other statements.

Effect: `created_entry_ids=[entry]`, `inverse={created_entry_id, statement_ids}`; `revert_lock_targets` re-acquires the account lock, the barrier and every live bank statement of the account (the recorded set plus any added since), and the revert deletes the entry while unchanged and flags those statements again. On apply **and on revert**, the statement set is discovered before locking and re-read under the locks; a difference refuses `targets_changed` (the policy retries next run; the owner retries the revert) rather than flagging an unlocked statement (AGENT-129 rev-4/rev-6 Should). Policy: the candidate is any open `balance_gap` case of the account (not only the target's: the earliest statement may balance at its close while its opening is off); the policy applies it once per account per run, amount `|delta|`, cap `actions.anchor_opening.max_amount` (default "200000"), over cap → proposal. `balance_adjustment_asof` stays the owner's tool for later months.

### R6b.3 / R6b.4 — transfers: deferred to R6c (decision 8)

`pair_transfer` (counterpart line on another statement → one two-legged transfer) and `backfill_transfer` (suspense account `對帳待配對`, or spend mode per account) move to the R6c spec. R6b keeps only the matcher hint: a `line_unmatched` case for a `payment`, `transfer_out` or `transfer_in` line gets `context.hint = "needs_peer"` so that `backfill_entry` and the owner's `create_entry` are not offered for it (the owner still has `match` and `dismiss`). R6b.9 lists the R6c inputs.

### R6b.5 Stand-in replacement — the owner's entry wins

Two hints on `entry_unmatched` cases, produced by a non-locking discovery step in `_desired_cases` that runs after the matcher and does not use `unmatched_entry_ids` (AGENT-129 Must 4, Should 2):

- `shadowed_by_stand_in`: an owner entry (non-stand-in, claimable, unclaimed, same account set, gate-eligible for the line's kind, `_same(|amount|)`, conserved sign, posted within `drift_window_days` of the line — inside or outside the period) while the line's active coverage is held by a stand-in. Case: `entry_id` = the owner entry, `event_id` = the line's event, context `{"hint": "shadowed_by_stand_in", "stand_in_entry_id", "backfill_action_id"}`. Exactly one such line per owner entry, else no hint.
- `orphaned_stand_in`: a stand-in that is unmatched after the matcher ran (its line is now claimed by a non-stand-in, or released and claimed again by an owner entry). Case: `entry_id` = the stand-in, `event_id` = the line's event, context `{"hint": "orphaned_stand_in", "backfill_action_id", "claimant_entry_id"}`.

Canonical evidence (AGENT-129 rev-6 Should): the line is always `case.event_id` (stable across reparse; the current line is `current_line()`), the stand-in is `case.entry_id` (orphan) or `context.stand_in_entry_id` (shadow), the creating action is the applied creation whose `created_entry_ids` contains the stand-in, and the claimant is the owner entry `case.entry_id` (shadow) or the non-stand-in entry holding the event's active coverage (orphan). The context ids are informational; the action re-derives everything from `case.event_id` and `case.entry_id` under the locks.

One action `replace_stand_in` serves both (single native action, no nested `revert_action`/`match`, AGENT-129 Must 2 and Must 7). It takes **no params** (`Params` = empty `ActionParams`): the stand-in, its creating action and the claimant are derived from `case.event_id` and `case.entry_id` as above, so a proposal for it carries only the action name and passes `proposal_service._validate` without new entries in `ID_KEYS` (AGENT-129 rev-6 Must 3); stale evidence is caught by the under-lock revalidation, not by params.

- Condition: `entry_unmatched` with one of the two hints.
- Locks: `lock_targets` returns the case statement, the stand-in entry id and the **claimant** entry id — the owner entry named by the shadow case (`case.entry_id`), or, for the orphan shape, the non-stand-in entry whose active coverage holds the event's current line (discovered before locking from `case.event_id`); the engine sweeps before taking entry locks as today. If no such claimant exists at discovery time the action refuses `no_claimant` (the orphan case is stale). Current-line resolution: `_desired_cases` sets `line_id` on these cases to the event's current line, and the action resolves the line by `case.event_id` against the statement's current revision regardless (`base.current_line` returns None when `line_id` is absent, `base.py:194–205`, so the action does not rely on it).
- Validate under the locks (replacement-only allowlist): the stand-in still exists, `source == "statement"`, its creating action is `applied`, and its `account_id`, `currency`, `kind`, `amount`, `entry_date`, `posted_date` equal the action's `after` snapshot; it has no children, refunds, settlements, rewards or transfer group. Category, name, description, project and `needs_review` may differ (the owner categorised it in HomeHub). Shadow shape: the event's current line has active coverage held by the stand-in, and the owner entry is still unclaimed, non-stand-in and conserves the line within the drift window. Orphan shape: the event's current line has active coverage held by the locked claimant, the claimant is a non-stand-in entry that conserves the line, and the stand-in holds no active coverage. Any failure refuses (`stand_in_changed`, `stand_in_coverage_gone`, `owner_entry_taken`, `claimant_changed`) and the case stays open; the stand-in is never deleted unless a conserved non-stand-in claim on the event is proven under the locks (AGENT-129 rev-4 Must 2).
- Apply, one transaction: (shadow shape) `coverage_service.release_rows` on the stand-in's coverage with reason `replaced`, then `write_claim` of the owner entry on the line (rule `replace_stand_in`, `match_kind` by actor); (both shapes) `ews.delete_entries_cascade([stand_in])`; the creating action gets `status = "reverted"` and `reverted_by_action_id = <this action>` — the existing lifecycle (`ACTION_STATUSES = applied | reverted | failed`, no enum change, AGENT-129 rev-4 Must 1), so cap counting (`_applied_totals`, applied rows only) and the creation-family check no longer see it. The replacement action's own row records the derived `stand_in_entry_id`, `backfill_action_id` and `claimant_entry_id` in its `before` snapshot for the audit (params stay empty).
- Effect: `deleted_entry_ids = [stand_in]`, `touched_entry_ids = [claimant]`, `coverage_ids`; `inverse = {"stand_in": <column snapshot>, "backfill_action_id"}`. **Not revertable** (`NotRevertable("stand-in deleted")`): the owner re-creates through `create_entry` if ever needed. One action row per replacement.
- Policy: `POLICY_ACTIONS += {"replace_stand_in"}`, `actions.replace_stand_in = {"enabled": True, "max_amount": "0"}` and candidate amount **0**; the per-statement count cap still applies, so a replacement beyond the count becomes an ordinary proposal the owner confirms (tested). Ruling (AGENT-129 rev-4 Should): this is a deliberate budget exemption, not a claim that the ledger is unchanged — the deletion does change the balance by the stand-in's amount, but the owner's own entry of equal amount already changed it the other way when it was imported; the two cancel, and the action is the owner's intent by construction. The cap and the monthly budget are therefore satisfied without a special case (a missing `max_amount` means zero, and the policy must spend a budget). `_unchanged` compares the stand-in id, the backfill action id, the owner entry's amount and date. Policy candidate collection extends to `entry_unmatched` (these hints) and `balance_gap` (R6b.2).

MOZE backup imports do not disturb this: `delete_moze_entries` removes only entries with a MOZE source or `moze_id`, and the import's balance arithmetic (`_moze_part`) sums MOZE-source entries only, so statement-source entries survive imports; the next sweep marks the statement (`needs_recheck`, the owner's new entry is an uncovered insert inside the period) and the next reconcile derives the shadow case. The same holds for the anchor of R6b.2: every import resets `account.opening_balance` from the backup's `originalAmount`, which is why the anchor is an entry and not an opening-balance edit.

### R6b.6 Settings (`settings_service`)

`POLICY_DEFAULTS` gains:

```
"backfill_grace_days": 14,
"backfill_accounts": [],
"actions": {
  ...,
  "backfill_entry":    {"enabled": False, "max_amount": "5000"},
  "anchor_opening":    {"enabled": False, "max_amount": "200000"},
  "replace_stand_in":  {"enabled": True,  "max_amount": "0"}
}
```

`_check_policy` validates the new keys (`backfill_grace_days` int 0..365, `backfill_accounts` list of int, action blocks with `enabled` bool and `max_amount` Decimal string). R6c adds `pair_window_days`, `transfer_lines_as_spend_accounts` and its two action blocks. `policy_version` prefix becomes `r6b` (`r6b-<version>`). `RULE_DEFAULTS` gains `drift_window_days` (R6a) and the derived `population_window_days` is not a setting. `SYSTEM_LINE_KINDS` and `create_system_entry` are unchanged.

### R6b.7 Evidence

`scripts/r6_dryrun.py --policy`: lists, per account, what `backfill_entry`, `anchor_opening` and `replace_stand_in` would do under the current settings (counts in the summary; amounts only in the detail file), flags any account with closing adjustments that would block the anchor, counts `needs_peer` lines per account (the R6c residual) and lines whose stand-in was deleted outside a revert. Run before each enable step; the release record keeps the summary.

### R6b.8 Tests

`tests/integration/test_reconciliation_policy.py`: backfill eligible after grace, refused before grace, owner run without grace, `unknown` never backfilled, transfer/payment line gets `needs_peer`, over-cap → proposal and owner confirmation applies it uncapped, account scoping, category created once, `_unchanged` refuses a changed amount; anchor books at `period_start − 1` with the opening-balance delta, flags every statement of the account, idempotent by provenance on rerun and after an earlier statement is added, refused with dirty triggers off, refused with a closing adjustment present, over cap → proposal, later `balance_adjustment_asof` books only the residual; replacement: shadow shape (release, claim, delete, creating action `reverted`, one row), orphan shape after a sweep released the line, categorised stand-in still replaced, amount-edited stand-in refused, owner entry 11–15 days outside the period found, stand-in never shadows a `create_system_entry` row, not revertable, concurrent delete of the stand-in refused, orphan case whose claimant was unmatched or deleted meanwhile → refused with no deletion, failure after delete rolls the whole action back; anchor apply with a statement added between discovery and lock → `targets_changed`, anchor revert re-locks all statements. `tests/integration`: second backfill of the same event refused while the first creation is applied, allowed after `replace_stand_in` reverted it and after an explicit engine revert; a ledger-side delete of a stand-in (and of a `create_system_entry` row) retires the creation via the sweep, reopens the case with `stand_in_deleted`, the policy (both builders) skips it across two further reconciles and an identical reparse, and an owner-run `backfill_entry`/`create_entry` then succeeds; edit → release → delete → retire → owner recovery; retirement idempotent and attribution preserved; both interleavings of sweep retirement versus an owner revert/replacement; repeated sweeps; deletion regressions for all four creation types (`backfill_entry`, `create_entry`, `create_system_entry`, `create_refund`); the dry-run reports retirement counts by action type beside suppression counts; owner eligibility exercised through the shared `allowed(case)` path, not only the candidate builder; a case reopened by an ordinary engine revert is backfilled again by the policy. `tests/unit/test_statement_matching.py`: stand-in representations dropped when an owner representation exists; stand-ins compete among themselves; `needs_peer` hint per excluded line kind. Service tests for shadow discovery with two candidate lines (no hint when not unique) and for context stability across an identical reparse (event ids). Sweep regression: the structural-only release against a fixture of pre-R6b coverage snapshots.

### R6b.9 R6c inputs (transfers) — from the AGENT-129 rev 3–5 reviews

Carried into the R6c spec round, not resolved here: per-leg posting dates for a paired transfer (`transfer_service` copies the outgoing dates to both legs; a month-boundary pair must keep each line's own date); reciprocal uniqueness of pairs with full predicate revalidation under both statement locks and the policy's conflict/parse-review/recheck exclusions on the peer statement; explicit both-case lifecycle (resolution, proposal invalidation, `revert_lock_targets` override, both-side reopening — `_reconcile_keeping_case` and `move_account` do not supply it); whole-transfer replacement (both legs validated, every affected statement discovered and locked ascending before entries, owner peer-account admissibility); suspense account naming under the globally unique `Account.name` (currency-qualified names, durable provenance, race-safe creation); late/ambiguous peers converging into suspense (report unresolved count and gross amounts, not net balance); re-pointing a suspense leg through `PUT /transfers/{group}` (entry edits refuse account changes; the transfer edit rewrites both legs and sets `source=manual`); cap and rollout-scope attribution on both statements; `remember=False` and both `needs_review` flags set before snapshots; spend-mode entries needing a provenance-bound representation to re-claim a payment/transfer line.

## 3. Evidence, rollout, process

### 3.1 Rollout

1. R6a: merge → deploy worker + API (detached checkouts, constraints file, `gate.sh`) → one manual run → open-case delta against the 354 baseline → owner ok. Release record `~/reports/home-hub-r6a-release-<ts>/` with a pytest-free evidence step (the deploy venv has no pytest; the README gets that step).
2. R6b: merge → deploy; all new actions off except `replace_stand_in`. Backup (`docker exec … pg_dump`, as on 2026-10-15). Enable `backfill_entry` with `backfill_accounts` = bank accounts → run → per-account `balance_gap` check against the `needs_peer` residual → enable `anchor_opening` → run → cards (`backfill_accounts` emptied). Each step is one wholesale `PUT /settings/reconciliation` (ADMIN token) and a dry-run before it.
3. R6c (transfers): own spec round, then the same pattern.
4. The sweep does not revisit untouched statements; after each enable the operator reconciles the affected statements explicitly (`POST /accounts/{acct}/statements/{id}/reconcile`) or waits for the daily run, which re-derives cases for statements the dirty triggers marked.

### 3.2 Process

Spec review by Multica first (lead-claude, non-Claude reviewer at the exact SHA), then a plan per stack, then PRs `feat/reconciliation-r6a`, `feat/reconciliation-r6b` and later `feat/reconciliation-r6c` (each branches from main after the previous merges). Worktrees carry a copied `.env` (mode 600). R5 worktrees are removed before R6a starts.

### 3.3 Ledger ownership after the cutover

Decision 6 fixes the direction of truth: statements → HomeHub ledger, owner edits in HomeHub. Consequences R6 relies on (the stand-in rules of R6b.0 are the code behind the second point):

- A MOZE backup import after the cutover is allowed but not expected; `delete_moze_entries` touches only MOZE-source rows, so HomeHub-created entries (backfill, system, manual, anchor) survive any import.
- Backfilled entries are findable for categorisation: category `對帳待分類` per kind and `needs_review=True`; the owner edits them through the existing ledger entry API/UI. Such an edit keeps the match (structural-only sweep release, R6b.0); replacement by a later owner entry still works because `replace_stand_in` compares an allowlist of structural fields, not the full snapshot (R6b.5, AGENT-129 Must 2).
- Reports and budgets that read the HomeHub ledger see backfilled entries immediately; MOZE's own balance is no longer a reference after the final import.

### 3.4 Non-goals

Case UI (R3a, PR #69); MOZE write-back; historical statements (`period_end ≤ 2026-06-30`); owner-vs-owner MOZE duplicates (43 suspected, list file for the owner); auto-booking `unknown` lines; installments (plan mapping stays an owner action); transfers and the suspense account (R6c); OCR changes; AGENT-126 (401 → `auth`) stays a separate maintenance PR.

### 3.5 Success criteria

- After R6a: open cases ≤ 290 (from 354): the 1–3-day, receivable/payable and bank-only-by-kind classes close or become policy-eligible; near/drift classes move to `amount_delta` with candidates; zero single-candidate `ambiguous`; no previously claimed line lost (dry-run gate).
- After R6b on bank accounts: bank `line_unmatched` reduced to `needs_peer` lines (R6c), `unknown` lines the owner classifies and refunds without a parent; on every anchored account the `balance_gap` equals an explained residual — the sum of its `needs_peer` lines plus any cap-refused, near/drift or open owner cases — checked per account in the release record (AGENT-129: anchoring alone cannot make every closing gap zero).
- After R6b on cards: a MOZE entry recorded after a backfill replaces its stand-in within one run, whether or not the stand-in was categorised meanwhile (integration tests plus one real case in the release record).
- After R6c: the `needs_peer` residual goes to zero; the suspense account's unresolved count and gross amounts are listed in every release record and trend to zero.

## 4. Risks and rulings

- **Backfill books something the owner would have matched.** Mitigated by order (R6a first), grace (14 d), owner-wins (R6b.5 `replace_stand_in`, 15-day window) and caps. Remaining: an owner entry dated more than `drift_window_days` from the line, or with a different amount, is not a shadow; it shows as `entry_unmatched` next to a backfilled line — the owner dismisses or reverts by hand. After the cutover (decision 6) the owner records in HomeHub and the collision cannot arise.
- **Score rescale changes historical behaviour.** Historical statements are informational; the dry-run reports live statements only, and the service never rewrites coverage of claims already written.
- **Transfers wait for R6c.** Until then the bank accounts without ledger entries keep a `balance_gap` case whose residual equals their `needs_peer` lines; the release record shows the residual per account so the gap is explained, not hidden.
- **Edits of a stand-in.** A category/name edit keeps the match (structural-only sweep release, R6b.0). A structural edit (amount, date, account) releases the line and the stand-in then competes like any entry at the next reconcile: a one-day date change with the same amount re-claims (still a valid representation inside the window); an amount change does not, and the stand-in stays `entry_unmatched` for the owner. The line is not backfilled again while the creation is applied; after a ledger-side delete the creation is retired by the sweep and the reopened case (`stand_in_deleted`, re-derived every reconcile) waits for the owner (R6b.0). The dry-run and the release record count orphaned stand-ins.
- **Anchor with pre-existing closing adjustments.** Refused until they are removed (R6b.2); the dry-run lists affected accounts before the enable step.

- **Stored rule overrides on the old scale.** Ignored with a warning, not migrated (ruling: the owner has no overrides today; settings v4 only changed policy keys).
