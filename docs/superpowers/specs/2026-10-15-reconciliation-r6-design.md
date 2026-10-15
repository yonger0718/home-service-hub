# Statement reconciliation R6 — matching rules and statement-as-truth (design)

Date: 2026-10-15. Status: rev 5 — rev 4 folded the AGENT-129 review of rev 2 (7 Must, 4 Should) and the owner's rulings; rev 5 adds the one-stand-in-per-event guard and the structural-only sweep release (R6b.0); for Multica re-review (AGENT-128).
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
7. **Transfers are booked too (both, 2026-10-15).** Card payments and own-account transfers are the largest flow on the bank accounts with no ledger entries, so leaving `transfer_in/out` and `payment` lines open would keep a `balance_gap` case on most bank months. R6b pairs a line with its counterpart line on another live statement when both exist (R6b.3) and otherwise books it against a HomeHub-native suspense account `對帳待配對` (R6b.4). The non-goal "cross-statement transfer pairing (R6c)" of rev 2 is withdrawn.

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

Read-only. Opens a DB session, and for every live statement computes the desired case set twice — once with the **baseline implementation** (the matcher and service modules imported from a pinned baseline checkout, by default the deployed worker checkout at the merge-base commit, passed as `--baseline-checkout <path>`; stored settings over that checkout's defaults) and once with the R6a implementation from the working tree — through each tree's `_desired_cases`, then rolls back. Swapping rule values alone cannot reproduce the old score, gates and bank-only behaviour once those functions change (AGENT-129 Should 4), so the baseline is code, not numbers. Output (file, mode 600, under `~/reports/home-hub-r6a-dryrun-<ts>/`): counts by (case kind, hint, matcher rule) before/after, and the per-line delta as `(statement_id, line_id, before, after, rule that changed it)`. No amounts, no merchants in the summary; the detail file is for the owner. Merge gate for R6a: every delta is attributed to one of R6a.1–R6a.6; no `ambiguous` case with one candidate remains; no exact-amount claim present before is lost after. A `--policy` mode is added by R6b (§2.5).

### R6a.8 Tests

`tests/unit/test_statement_matching.py` (helpers `L`, `E`, `run`): distance 3 same amount claims; distance 2 with no text claims; two same-day exact candidates stay ambiguous; candidates at distance 0 and 2 claim the closer; single candidate never ambiguous; receivable (non-settlement) matches a purchase, receivable settlement does not; payable matches a deposit; near at distance 0 without token overlap, at distance 1 still requires overlap; near at 4.9 % passes, 5.1 % fails; payment line near via `_peer_ok`; drift at 4 and 15 days emits `amount_delta` with `date_drift`, at 16 does not; drift ordering; `bank_only` for an interest line without any pattern, never for `unknown`. `tests/integration/test_reconciliation_service.py`: a drift case round-trips through `_desired_cases` and resolves with `match`; a bank-only-by-kind case is a `create_system_entry` candidate in `test_reconciliation_policy.py`. `settings_service` tests: `drift_window_days` validated; stale `r1b-*` override of `margin` ignored with a log record.

## 2. R6b — statement as truth (actions, policy, settings)

R6b adds five actions and one matcher notion (the *stand-in*). All actions are ordinary engine actions — registered, listed in `KIND_ACTIONS` with a `CONDITIONS` entry, `actors = {OWNER, POLICY}` — so the engine's `allowed(case)` check, the proposal path (`proposal_service._validate`) and the owner's confirmation of an over-cap proposal work unchanged (AGENT-129 Must 1). "Policy-driven" means the policy's candidate builders find and apply them; the owner may also run each one directly from a case, without the grace. No migration: `reconciliation_action.action` is `String(32)`, no new case kind, new settings are JSON keys.

### R6b.0 Stand-ins

A **stand-in** is a ledger entry with `source == "statement"` created by an applied `backfill_entry` or `backfill_transfer` action (provenance: `reconciliation_action.created_entry_ids`, action status `applied`). System entries from `create_system_entry` and `balance_adjustment` rows are not stand-ins. The service marks stand-ins on the matcher population (`Entry.stand_in: bool`, default False). Rule in `_representations`: when a line has at least one non-stand-in representation, its stand-in representations are dropped before scoring; a stand-in competes only against other stand-ins. So an owner entry always beats its stand-in at claim time, and a stand-in whose coverage the sweep released re-claims its line on the next reconcile when nothing else is there (self-healing). An owner entry for the *same* line can therefore arrive in two shapes: with the stand-in still holding the line (R6b.5, `shadowed_by_stand_in`) or with the line released and the owner entry already claimed by the matcher, leaving the stand-in orphaned (R6b.5, `orphaned_stand_in`).

**One stand-in per line event.** `backfill_entry` and `backfill_transfer` refuse (`stand_in_exists`) when an applied backfill action already created an entry for the case's `event_id` and that entry still exists, and their conditions exclude a case whose context carries `reopened_by_event` or `reverted_action_id`. Without this, a line whose coverage the sweep released (after an edit of its stand-in) could be backfilled a second time before the next reconcile re-claims it; the new stand-in would then orphan the owner-categorised one and `replace_stand_in` would delete it.

**Structural-only sweep release.** Today the sweep releases a line's coverage on *any* update of a covered entry (`coverage_service.py:371–376`, the dirty trigger fires on every real column change). R6b narrows it: on an `update` event the sweep re-reads the entry and releases only if `account_id`, `currency`, `amount`, `kind`, `entry_date`, `posted_date`, `is_settlement`, `parent_entry_id` or `group_id` changed against the coverage's recorded row (coverage rows already store the claimed amount; the other fields are compared against the entry's values at claim time, recorded in the coverage row's `context` by `write_claim` from R6b on; rows written before R6b lack them and keep the old release-on-any-update behaviour). Category, name, description, project, `needs_review` and merchant edits no longer touch a match. `delete` events release as before. This applies to every covered entry, not only stand-ins: a category edit never invalidates a match. Tests: category edit keeps coverage; amount edit releases; pre-R6b coverage row releases on any edit.

Candidate population (AGENT-129 Must 4): the service loads entries for `period ± population_window_days` where `population_window_days = max(candidate_window_days, drift_window_days, near_window_days, foreign_window_days)` (15 with the defaults); `candidate_window_days` keeps its own meaning for installment plans. `in_reverse` (unmatched reporting) stays period-only; the stand-in discovery of R6b.5 runs its own query and does not depend on `unmatched_entry_ids`.

### R6b.1 `backfill_entry`

Condition (`CONDITIONS[("line_unmatched", "backfill_entry")]`): the case has no `hint` and no candidates, and the line kind maps to an entry kind:

| statement | line kind | entry kind |
|---|---|---|
| card | `purchase` | `expense` |
| bank | `withdrawal`, `purchase` | `expense` |
| bank | `deposit` | `income` |

Not booked by `backfill_entry`: `unknown` (owner ruling 2026-10-15 on AGENT-129 Must 6: an `unknown` line has an empty gate, so a stand-in for it could never be replaced by an owner entry; the case stays open for the owner's `create_entry`), `transfer_in`, `transfer_out`, `payment` (R6b.3 and R6b.4; the matcher's fallback tags such a case `context.hint = "needs_peer"`), `installment` (plan mapping), `balance_adjustment`, `refund` (`create_refund` stays an owner action).

Booking: `_Creation._book` with `kind`, `category_id = backfill_category_id(db, kind)` (a fixed category `對帳待分類` per kind, created on demand like `system_category_id`), `needs_review=True`, `source="statement"`, merchant/description/dates from the line. The line is claimed (rule `backfill_entry`), the case resolves, `inverse = {created_entry_id}`; the ordinary revert path applies (and, as for every creation, refuses once the entry was edited — the owner then deletes the entry through the ledger instead).

Policy: `POLICY_ACTIONS += {"backfill_entry"}`; `_backfill_candidate` requires statement mode `live`, `period_end + backfill_grace_days ≤ today` (default 14), account in `backfill_accounts` when that list is non-empty, amount `|flow|`, params `{kind}`; caps as the other actions, over cap → proposal; `_unchanged` compares status, kind, absence of hint and candidates, line kind and amount. Owner runs skip the grace and the account list.

### R6b.2 `anchor_opening`

Action on a `balance_gap` case of a bank statement. Condition: the account has at least one live statement with `opening_balance` not null and no applied `anchor_opening` action on any of its statements (provenance = `reconciliation_action` rows joined through `case → statement → account`, AGENT-129 Should 3; no account column is added).

Apply (locks: `LockSet(account_lock=True, barrier=True, statement_ids=<all live bank statements of the account>)`, ascending ids through the engine's existing ordering; refused while `dirty_enabled` is off, as `balance_adjustment_asof`):
1. Target = the account's earliest live statement with `opening_balance` (recomputed under the locks). `as_of = target.period_start − 1 day`; `delta = target.opening_balance − account_balance(db, account_id, as_of)` computed under the barrier.
2. Refuse `zero_delta` when `delta == 0`; refuse `closing_adjustments_present` when the account has any `balance_adjustment` entry dated `≥ as_of` (a closing adjustment booked earlier would overcorrect once the opening is anchored; the owner reverts or deletes those first — none exist on 2026-10-15, the dry-run checks) (AGENT-129 Must 3).
3. Book the `balance_adjustment` entry (`system_category_id(db, "balance_adjustment")`, `entry_date = posted_date = as_of`, description `期初對齊 <as_of>`, `source="statement"`, `needs_review=True`).
4. Set `needs_recheck = True` on every locked statement of the account (the dirty event of an entry dated outside every period marks no statement by itself, AGENT-129 Must 3); the daily run and the sweep's flagged set re-derive their cases, including the target's own residual closing gap. No in-transaction reconcile of other statements.

Effect: `created_entry_ids=[entry]`, `inverse={created_entry_id}`; revert deletes the entry while unchanged and flags the same statements again. Policy: the candidate is any open `balance_gap` case of the account (not only the target's: the earliest statement may balance at its close while its opening is off); the policy applies it once per account per run, amount `|delta|`, cap `actions.anchor_opening.max_amount` (default "200000"), over cap → proposal. `balance_adjustment_asof` stays the owner's tool for later months.

### R6b.3 `pair_transfer` — both lines exist

Matcher/service: for a `line_unmatched` case whose line kind is `payment`, `transfer_out` or `transfer_in` the fallback sets `context.hint = "needs_peer"`. The service then looks (non-locking discovery, modelled on `_cross_account_line`) for an uncovered line of another live statement, any account outside the participating set, same currency, `flow_amount == −line.flow_amount`, posted within `pair_window_days` (default 3) of the line, whose kind is a counterpart (`payment` out ↔ card `payment` in; `transfer_out` ↔ `transfer_in`; bank `payment` ↔ bank `transfer_in`; never `withdrawal`/`deposit`). Exactly one → `context = {"hint": "needs_peer", "peer_statement_id", "peer_event_id"}` (event ids, not line ids: stable across identical reparses, AGENT-129 Should 2); several → the hint stays without a peer. The pair is derived from the statement with the lower id only, so one run never carries it twice; a peer whose own case names this statement is the same pair seen from the other side. Context change on peer discovery supersedes the case (new version) and invalidates pending proposals on it — documented behaviour, tested.

Condition: hint `needs_peer` with `peer_statement_id` present. Locks: `LockSet(statement_ids=(peer_statement_id,))` plus the case statement in the engine's order (precedent `move_account`); `validate_before` re-reads the peer line by event id under the locks and refuses `peer_taken` when it is covered. Apply: `transfer_service.create_transfer(TransferIn(from_account_id=<out side>, to_account_id=<in side>, out_amount=|flow|, entry_date=<out line txn or posted date>, posted_date=<out line posted date>, merchant/description from the out line), source="statement")`, both legs `needs_review=True`; `write_claim` for each line against its leg (rule `pair_transfer`); the peer statement's case for that line resolves through `_reconcile_keeping_case` on the peer statement (precedent `move_account`). Effect: `created_entry_ids` = both legs, `coverage_ids` both, `inverse = {transfer_group_id, peer_statement_id}`; revert deletes the group and releases both claims while both legs are unchanged. Caps: `actions.pair_transfer.max_amount` (default "300000"); over cap → proposal. Both legs are stand-ins (R6b.0).

### R6b.4 `backfill_transfer` — suspense account or spend

Condition: hint `needs_peer` without a peer. Policy applies it only after the grace (`period_end + backfill_grace_days ≤ today`) so a late peer statement can pair first; the owner may run it at once.

Default booking: a two-legged transfer between the line's account and the suspense account `對帳待配對` — a HomeHub-native account created on demand per currency (`include_in_total=False`, no `moze_id`; MOZE imports upsert MOZE accounts only, so it survives them, as accounts 145/146 do) — direction by the line's flow, `source="statement"`, both legs `needs_review=True`, line claimed against its leg (rule `backfill_transfer`). The suspense account's balance is the sum of transfers whose counterpart is unknown, in one place; the owner re-points the suspense leg to the right account through the ledger edit path (own transfers whose peer statement was never ingested).

Third-party payments (rent, utilities, people) have no "right account": the transfer must become an expense or income. Two paths: (a) the owner reverts the `backfill_transfer` action (existing revert endpoint; reopens the line case) and runs `create_entry` with a category — two existing actions, which the R3 UI can present as one "轉成支出/收入" step; (b) per account, policy setting `transfer_lines_as_spend_accounts: [account ids]` (no account column needed): for a listed account an unpaired transfer line is booked by `backfill_transfer` directly as a `對帳待分類` expense/income by sign instead of a suspense transfer (`params = {"mode": "spend"}`), for accounts whose transfer lines are mostly third-party payments. Pairing always runs first, so own transfers are not mis-booked as spend. Default is suspense (a transfer booked as spend inflates spending; a suspense leg is neutral until decided).

Caps: `actions.backfill_transfer.max_amount` (default "300000"). Effect/revert as R6b.3 (suspense mode) or as R6b.1 (spend mode). Legs and spend entries are stand-ins.

### R6b.5 Stand-in replacement — the owner's entry wins

Two hints on `entry_unmatched` cases, produced by a non-locking discovery step in `_desired_cases` that runs after the matcher and does not use `unmatched_entry_ids` (AGENT-129 Must 4, Should 2):

- `shadowed_by_stand_in`: an owner entry (non-stand-in, claimable, unclaimed, same account set, gate-eligible for the line's kind, `_same(|amount|)`, conserved sign, posted within `drift_window_days` of the line — inside or outside the period) while the line's active coverage is held by a stand-in. Context `{"hint": "shadowed_by_stand_in", "stand_in_entry_id", "backfill_action_id"}`, case `event_id` = the line's event. Exactly one such line per owner entry, else no hint.
- `orphaned_stand_in`: a stand-in that is unmatched after the matcher ran (its line is now claimed by a non-stand-in, or released and claimed again by an owner entry). Context `{"hint": "orphaned_stand_in", "backfill_action_id"}`; the case's `entry_id` is the stand-in.

One action `replace_stand_in` serves both (single native action, no nested `revert_action`/`match`, AGENT-129 Must 2 and Must 7):

- Condition: `entry_unmatched` with one of the two hints.
- Locks: `lock_targets` returns the stand-in entry id, the owner entry id (shadow shape) and the case statement; the engine sweeps before taking entry locks as today.
- Validate under the locks (replacement-only allowlist): the stand-in still exists, `source == "statement"`, its creating action is `applied`, and its `account_id`, `currency`, `kind`, `amount`, `entry_date`, `posted_date` equal the action's `after` snapshot; it has no children, refunds, settlements or rewards. Category, name, description, project and `needs_review` may differ (the owner categorised it in HomeHub). Shadow shape only: the line's active coverage is held by the stand-in and the owner entry is still unclaimed and still matches. Any failure refuses (`stand_in_changed`, `stand_in_coverage_gone`, `owner_entry_taken`) and the case stays open.
- Apply, one transaction: (shadow shape) `coverage_service.release_rows` on the stand-in's coverage with reason `replaced`, then `write_claim` of the owner entry on the line (rule `replace_stand_in`, `match_kind` by actor); (both shapes) `ews.delete_entries_cascade([stand_in])`; the creating action gets `status = "superseded"` and `reverted_by_action_id = <this action>` so cap counting (`_applied_totals`, applied rows only) no longer counts it. Transfer stand-ins (R6b.3/R6b.4): the whole transfer group is deleted and, in the pair case, the peer line's coverage released and its statement flagged `needs_recheck`.
- Effect: `deleted_entry_ids = [stand_in]` (or both legs), `touched_entry_ids = [owner]` (shadow), `coverage_ids`; `inverse = {"stand_in": <column snapshot>, "backfill_action_id"}`. **Not revertable** (`NotRevertable("stand-in deleted")`): the owner re-creates through `create_entry` if ever needed. One action row per replacement.
- Policy: `POLICY_ACTIONS += {"replace_stand_in"}`, `actions.replace_stand_in = {"enabled": True, "max_amount": "0"}` and candidate amount **0** (the net ledger change is the owner's own entry replacing a stand-in of equal amount), so the cap and the monthly budget are satisfied without a special case (AGENT-129 Must 1: a missing `max_amount` means zero, and the policy must spend a budget). `_unchanged` compares the stand-in id, the backfill action id, the owner entry's amount and date. Policy candidate collection extends to `entry_unmatched` (these hints) and `balance_gap` (R6b.2).

MOZE backup imports do not disturb this: `delete_moze_entries` removes only entries with a MOZE source or `moze_id`, and the import's balance arithmetic (`_moze_part`) sums MOZE-source entries only, so statement-source entries survive imports; the next sweep marks the statement (`needs_recheck`, the owner's new entry is an uncovered insert inside the period) and the next reconcile derives the shadow case. The same holds for the anchor of R6b.2: every import resets `account.opening_balance` from the backup's `originalAmount`, which is why the anchor is an entry and not an opening-balance edit.

### R6b.6 Settings (`settings_service`)

`POLICY_DEFAULTS` gains:

```
"backfill_grace_days": 14,
"pair_window_days": 3,
"backfill_accounts": [],
"transfer_lines_as_spend_accounts": [],
"actions": {
  ...,
  "backfill_entry":    {"enabled": False, "max_amount": "5000"},
  "pair_transfer":     {"enabled": False, "max_amount": "300000"},
  "backfill_transfer": {"enabled": False, "max_amount": "300000"},
  "anchor_opening":    {"enabled": False, "max_amount": "200000"},
  "replace_stand_in":  {"enabled": True,  "max_amount": "0"}
}
```

`_check_policy` validates the new keys (`backfill_grace_days` int 0..365, `pair_window_days` int 0..30, the two lists as lists of int, action blocks with `enabled` bool and `max_amount` Decimal string). `policy_version` prefix becomes `r6b` (`r6b-<version>`). `RULE_DEFAULTS` gains `drift_window_days` (R6a) and the derived `population_window_days` is not a setting. `SYSTEM_LINE_KINDS` and `create_system_entry` are unchanged.

### R6b.7 Evidence

`scripts/r6_dryrun.py --policy`: lists, per account, what `backfill_entry`, `pair_transfer`, `backfill_transfer`, `anchor_opening` and `replace_stand_in` would do under the current settings (counts in the summary; amounts only in the detail file), and flags any account with closing adjustments that would block the anchor. Run before each enable step; the release record keeps the summary and the suspense account balances.

### R6b.8 Tests

`tests/integration/test_reconciliation_policy.py`: backfill eligible after grace, refused before grace, owner run without grace, `unknown` never backfilled, transfer/payment line gets `needs_peer`, over-cap → proposal and owner confirmation applies it uncapped, account scoping, category created once, `_unchanged` refuses a changed amount; anchor books at `period_start − 1` with the opening-balance delta, flags every statement of the account, idempotent by provenance on rerun and after an earlier statement is added, refused with dirty triggers off, refused with a closing adjustment present, over cap → proposal, later `balance_adjustment_asof` books only the residual; pair: bank payment ↔ card payment within 3 days books one transfer and claims both lines, two candidate peers → no peer, peer covered between discovery and lock → `peer_taken`, lower-statement-id derivation, revert releases both; suspense: created once per currency, direction by flow, refused before grace for the policy, spend mode for a listed account, a later owner transfer replaces it; replacement: shadow shape (release, claim, delete, superseded action, one row), orphan shape after a sweep released the line, categorised stand-in still replaced, amount-edited stand-in refused, owner entry 11–15 days outside the period found, stand-in never shadows a `create_system_entry` row, not revertable, concurrent delete of the stand-in refused, failure after delete rolls the whole action back. `tests/integration`: second backfill of the same event refused while the first stand-in exists, allowed after it was deleted; reopened case not backfilled. `tests/unit/test_statement_matching.py`: stand-in representations dropped when an owner representation exists; stand-ins compete among themselves; `needs_peer` hint per excluded line kind. Service tests for discovery with two candidate lines (no hint when not unique) and for context stability across an identical reparse (event ids).

## 3. Evidence, rollout, process

### 3.1 Rollout

1. R6a: merge → deploy worker + API (detached checkouts, constraints file, `gate.sh`) → one manual run → open-case delta against the 354 baseline → owner ok. Release record `~/reports/home-hub-r6a-release-<ts>/` with a pytest-free evidence step (the deploy venv has no pytest; the README gets that step).
2. R6b: merge → deploy; all new actions off except `replace_stand_in`. Backup (`docker exec … pg_dump`, as on 2026-10-15). Enable `pair_transfer` → run; enable `backfill_entry` and `backfill_transfer` with `backfill_accounts` = bank accounts → run → per-account `balance_gap` check → enable `anchor_opening` → run → cards (`backfill_accounts` emptied). Each step is one wholesale `PUT /settings/reconciliation` (ADMIN token) and a dry-run before it.
3. The sweep does not revisit untouched statements; after each enable the operator reconciles the affected statements explicitly (`POST /accounts/{acct}/statements/{id}/reconcile`) or waits for the daily run, which re-derives cases for statements the dirty triggers marked.

### 3.2 Process

Spec review by Multica first (lead-claude, non-Claude reviewer at the exact SHA), then a plan per stack, then PRs `feat/reconciliation-r6a` and `feat/reconciliation-r6b` (R6b branches from main after R6a merges). Worktrees carry a copied `.env` (mode 600). R5 worktrees are removed before R6a starts.

### 3.3 Ledger ownership after the cutover

Decision 6 fixes the direction of truth: statements → HomeHub ledger, owner edits in HomeHub. Consequences R6 relies on, none of which needs new code:

- A MOZE backup import after the cutover is allowed but not expected; `delete_moze_entries` touches only MOZE-source rows, so HomeHub-created entries (backfill, system, manual, anchor) survive any import.
- Backfilled entries are findable for categorisation: category `對帳待分類` per kind and `needs_review=True`; the owner edits them through the existing ledger entry API/UI. Such an edit keeps the match (structural-only sweep release, R6b.0); replacement by a later owner entry still works because `replace_stand_in` compares an allowlist of structural fields, not the full snapshot (R6b.5, AGENT-129 Must 2).
- Reports and budgets that read the HomeHub ledger see backfilled entries immediately; MOZE's own balance is no longer a reference after the final import.

### 3.4 Non-goals

Case UI (R3a, PR #69); MOZE write-back; historical statements (`period_end ≤ 2026-06-30`); owner-vs-owner MOZE duplicates (43 suspected, list file for the owner); auto-booking `unknown` lines beyond expense/income by sign; installments (plan mapping stays an owner action); OCR changes; AGENT-126 (401 → `auth`) stays a separate maintenance PR.

### 3.5 Success criteria

- After R6a: open cases ≤ 290 (from 354): the 1–3-day, receivable/payable and bank-only-by-kind classes close or become policy-eligible; near/drift classes move to `amount_delta` with candidates; zero single-candidate `ambiguous`; no previously claimed line lost (dry-run gate).
- After R6b on bank accounts: bank `line_unmatched` ≈ 0 (remaining: `unknown` lines the owner classifies, refunds without a parent); `balance_gap` = 0 on every anchored account; the suspense account's balance equals the sum of transfers without a known counterpart (checked per account in the release record).
- After R6b on cards: a MOZE entry recorded after a backfill replaces its stand-in within one run, whether or not the stand-in was categorised meanwhile (integration tests plus one real case in the release record).
- The suspense account's balance per currency is listed in every release record and trends to zero as the owner pairs, re-points or converts its legs.

## 4. Risks and rulings

- **Backfill books something the owner would have matched.** Mitigated by order (R6a first), grace (14 d), owner-wins (R6b.5 `replace_stand_in`, 15-day window) and caps. Remaining: an owner entry dated more than `drift_window_days` from the line, or with a different amount, is not a shadow; it shows as `entry_unmatched` next to a backfilled line — the owner dismisses or reverts by hand. After the cutover (decision 6) the owner records in HomeHub and the collision cannot arise.
- **Score rescale changes historical behaviour.** Historical statements are informational; the dry-run reports live statements only, and the service never rewrites coverage of claims already written.
- **Suspense account hides misdirected transfers and understates spending.** A transfer to a third party and an own-account transfer whose peer statement never arrives look the same in the suspense account, and neither counts as spending. Mitigation: `needs_review` on every leg, one account per currency, its balance in every release record and a success criterion that it trends to zero; the revert + `create_entry` path and the per-account spend mode (R6b.4) turn third-party payments into expenses. Pairing before suspense (grace) keeps the population small.
- **Edits of a stand-in.** A category/name edit keeps the match (structural-only sweep release, R6b.0). A structural edit (amount, date, account) releases the line; the stand-in then no longer conserves the line and stays `entry_unmatched` for the owner, while the line is not backfilled again (one stand-in per event) — the owner fixes the entry or deletes it and lets the policy backfill afresh. The dry-run and the release record count orphaned stand-ins.
- **Anchor with pre-existing closing adjustments.** Refused until they are removed (R6b.2); the dry-run lists affected accounts before the enable step.
- **Pairing across two statements.** Two statements are locked in one action (precedent: `move_account`); the peer line is re-read under the locks. The matcher derives the pair from the lower statement id only, so one run never proposes it twice.
- **Stored rule overrides on the old scale.** Ignored with a warning, not migrated (ruling: the owner has no overrides today; settings v4 only changed policy keys).
