# Reconciliation: split-group members printed apart — design

Date: 2026-10-24. Status: rev 3 — folds the AGENT-248 dev-astra review of rev 2 (FAIL: three majors, two minors; all addressed below, each marked «AGENT-248 n»). Parent specs: `2026-10-08-statement-reconciliation-design.md` (§6 representations), `2026-10-15-reconciliation-r6-design.md` (R6a matcher rules, `printed_alone` 2026-10-19), `2026-10-21-reconciliation-inbox-design.md` (buttons, `match_candidate`).

## 1. Intent and why

The matcher reads a MOZE `split` group as **one purchase the owner split into parts, printed by the bank as one line**: a line is represented by the whole group (the net reading), by the group with its unclaimed children, or, since 2026-10-19, by the group minus the fee/reward members a fee line prints alone. A split member never stands alone for a purchase, withdrawal or deposit line (`_standalone`, `_lone_member`, test `test_a_split_member_never_stands_alone_for_a_purchase_line`), and the `match` action refuses a member (`entry_in_group`).

That reading is right for the dominant usage — 248 of the ledger's 337 split groups are **expense + receivable on one account** (a purchase for a friend: 跟朋友一起買, 待收) — and must stay. But the ledger also holds two shapes the matcher can never pair (triage 2026-10-24, `~/reports/home-hub-triage-20261023/triage.md` §Session 3; 23 of the 62 open `entry_unmatched` cases are members of 13 groups):

- **Cross-account splits** (38 groups ledger-wide, 6 open): one member on the card, the other on 錢包 / Line Bank / another card. The card's population holds one member → the group is *partial* → inadmissible; the member is not standalone; nothing represents the card's line. The sum never appears on any statement.
- **Same-card splits the bank prints apart** (open: 4316 and others): one line per member; no line equals the TWD or JPY sum.

A third shape explains several of the open expense+receivable groups and is **not** a matcher problem: the group's sum line exists but is held by a policy stand-in booked before the 2026-10-19 foreign-group fix (e.g. group 4302 ↔ line 1417, stand-in 58257; group 4343 ↔ line 3588, stand-in 28795). Those are resolved by `replace_stand_in`, and this design must not turn their members loose (§3).

**Owner decisions 2026-10-24:** make members participate individually; keep the group; a group whose sum the bank prints (expense + receivable) must keep matching as a group. Spec → plan → Multica.

## 2. Goals, non-goals, anti-goals

Goals:
1. A top-level member of a `split` group that the statement does not print whole is an **automatic** candidate like a standalone entry: exact representation (with its own children), foreign, near and drift cases, ambiguity, `claimed_by_earlier_line`.
2. The owner can `match` and `adjust_amount` such a member from the inbox, and the inbox's candidate search lists split members.
3. A group the statement prints whole — exactly, within the near tolerance, or by foreign sum; by any of its readings (net, with children, reduced by printed-alone members); **whether or not that line or any member is already covered** — keeps today's behaviour exactly (net reading, children, printed-alone fee members, foreign group sum, `group_readings_incomplete`).

Non-goals: new UI; changing group semantics in the ledger (no auto-dissolve, no regrouping); `reward_claim` and `installment` groups (unchanged: never members alone; `installment_group` path unchanged); a "several lines = one member" reading; R6c transfers; replacing the stand-ins of §1 (owner action, existing `replace_stand_in`).

Anti-goals (a failure even if the code works), scoped to **automatic** candidates — matcher claims, matcher cases, policy-applied proposals («AGENT-248 5»; the owner's explicit `match` and the inbox search are owner overrides, §5.1/§5.3): a member stealing or being listed against the group's own sum line or a line near it (the expense of an expense+receivable purchase must not become an `amount_delta` against the purchase line, nor claim a same-amount line elsewhere while its sum line sits covered by a stand-in or while a sibling is covered by an owner match); a member of a group printed whole appearing as a matcher candidate for an unrelated line; the `group_readings_incomplete` block bypassed by a member reading, in reconcile or in the policy validator; the policy auto-applying a proposal because the validator did not see a member competing; coverage rows that make a revert believe a whole group is claimed when one member is.

## 3. Definitions

- **Member**: a top-level (`parent_entry_id is None`) entry whose `group_id` names a group of kind `split`, under the instance rule of `_standalone` (no `installment` instance; a recurring instance is allowed — a split with a recurring member is inadmissible as a group today, so nothing is lost). Children of a member reach a line through the member's `member_children` variant or the child path, as for a standalone parent.

- **Structural members** («AGENT-248 2»). `matching.Group` gains `members: tuple[GroupMember, ...]` — **every** top-level member of the group as stored, regardless of account, currency, window, claim state or exclusion: `GroupMember(id, flow, original_amount, original_currency, posted_date, entry_date, children_flow)` where `children_flow` is Σ of the member's children (all of them, claimed or not). `load_candidates` fills it for every group in the population with one query over `LedgerEntry.group_id IN group_ids` (plus the children of those ids); `member_ids` stays as today (the claim side keeps its partial-group rule unchanged). The guard below reads `Group.members` only — it never depends on which entries are loaded or claimable. For a `Group` built without `members` (unit tests, default `()`), the guard falls back to the loaded entries' flows; `load_candidates` always fills it.

- **Group totals** («AGENT-248 1»): for a group, the set of amounts a reading of it could conserve:
  (i) Σ `member.flow` over all structural members (the net reading);
  (ii) (i) + Σ `children_flow` (the `group_children` reading);
  (iii) for each non-empty combination of the group's printed-alone members (`aside`, `printed_alone`; combinations enumerated up to `ASIDE_COMBINATIONS_MAX` exactly as `_group_variants` does; above it only (i)/(ii)), the totals (i) and (ii) without those members and their children.
  Foreign total: Σ `original_amount` over structural members that share one `original_currency` (all of them must, as `_foreign_groups` requires); for the reduced readings of (iii) likewise without the removed members («AGENT-248 1», reduced foreign readings preserved).

- **Printed whole** (per statement, computed once before the line loop, **over every current line of the statement including lines held by owner/agent/policy coverage**): a split group for which some line satisfies either
  (a) *amount*: same sign and `|line.flow − total| ≤ max(near_tolerance_abs, near_tolerance_pct · |total|)` for one of the group totals, with the line within `drift_window_days` (15) of the nearest structural member (by `_date_distance` on the member's dates); the line's kind, gate and coverage are **not** consulted — this is a guard, not a claim, so a deposit, refund or fee line near an income split's total marks it whole too (today no group reading exists for a non-expense gate; without this an income split on a bank statement would always be apart); or
  (b) *foreign*: `|foreign total| == |line.foreign_amount|` (`_same`, magnitude compare as `_foreign_case`) for the full or a reduced reading, same currency, within `foreign_window_days`.
  Any reading the exact rules would claim today is whole under (a) (exact ⊂ near tolerance, exact window ⊂ drift window; children and reduced readings are in the totals).

- **Printed apart**: a split group in the population that is not printed whole. Its members are **apart members**. There is no separate partial case any more: a cross-account group's total is known from its structural members, so a card line equal to expense + receivable (receivable on another account) marks the group whole and nothing is stolen; a card line equal to the card's member alone leaves the group apart and that member is claimed.

The decision is per statement and per reconcile run; it is not stored. A group printed whole on statement A and apart on statement B is decided independently on each.

Why "whole" is generous: the tolerance and window are the near and drift rules' own, so a group whose sum the bank prints a few TWD off (FX) or days later stays together and, as today, surfaces as the group's foreign `amount_delta` or stays open for the owner — never as a wandering member. Note the tolerance percentage is taken of the group total, while `_near_case` takes it of the line amount (reviewer note; equivalent at the tolerance edge within rounding, documented here). Covered lines and covered members are included so a sum line held by a stand-in or a fee member covered by an owner match keeps the group whole (§1 third shape, «AGENT-248 2»): the owner's remedy there is `replace_stand_in` / `unmatch`, after which a reconcile pairs the group.

## 4. Matcher (`app/services/statements/matching.py`)

4.1 New pure functions, exported for the policy validator (§5.6):
- `group_totals(g: Group, by_id, aside) -> list[Total]` per §3 (`Total(flow, foreign_amount|None, foreign_currency|None, dates)`);
- `printed_whole(lines_all: list[Line], entries, groups, rules, aside) -> frozenset[int]` (group ids);
- `apart_members(entries, groups, whole) -> frozenset[int]` (entry ids of members of apart groups, instance rule of §3);
- `statement_context(kind, lines_all, entries, groups, rules) -> Context(aside, whole, apart)` wrapping `printed_alone` + the two above, so `match()` and `policy_service._single_admissible` compute the same sets from the same inputs.

`match(statement_kind, lines, entries, groups, plan_map, fee_expected, rules, account_currency, lines_all=None)`: `lines_all` defaults to `lines`; `reconcile()` passes every current line (`all_lines`, before the owner-covered lines are removed from `line_rows`, reconciliation_service.py ~779–781). `match()` computes the context after `aside`, before the loop (over `entries` before the `claimable` filter — the guard reads `Group.members` anyway), and threads it to every rule that today asks `_standalone(e)`:

- `_representations`: an apart member is admitted where a standalone entry is, with variants `member_alone` (rule as for standalone: `exact`, or the line kind's `payment`/`refund`/`transfer`) and `member_children` (rule `exact_children`) — rows `Row(e.id, "principal", e.flow, group_id=None)` plus child rows as for `principal_children`.
- `_foreign_case` single-entry branch, `_near_case`, `_drift_case`: `_standalone(e) or e.id in apart`.
- `_claimed_by_earlier_line`: same context (shadow representations).
- `_lone_member`, `printed_alone`, `_group_variants`, `incomplete_groups`, `_foreign_groups`: unchanged. `incomplete_groups` keeps blocking expense-gated lines for any group above `ASIDE_COMBINATIONS_MAX`; such a group's full and children totals are still tested by (a), so it is whole whenever the statement prints it. If it is apart (nothing near any total) its members may stand alone; the block then still applies to the line in the loop (`truncated` is computed when `reps` is non-empty), so a member reading never bypasses it («AGENT-248 3», reconcile side).

`Row.group_id` stays `None` for member rows: a coverage row's `group_id` means "claimed by a group reading" (`_ambiguous` candidate shape, `match` params, revert closure of `touched_group_ids`). The sweep still finds the member's line through the snapshot's `group_id` (`lines_by_group`, coverage_service.py:369 and :501–503), so an update or delete of the group row releases the member's claim like any structural change — intended.

4.2 Competition. Apart members compete with standalone entries under the existing score/margin/ambiguity rules; a member and a standalone entry of the same amount and day are `ambiguous` as two standalones are. A claimed member drops every group reading containing it (existing rule) — moot for an apart group.

4.3 Reverse side unchanged: an unclaimed member is reported `entry_unmatched` (with `deferred_next_period`) as today; `_designated_by_cases` unchanged.

4.4 `_ambiguous` keeps `{"entry_id": e, "group_id": None}` for member rows; group-reading candidates keep `{"entry_id": None, "group_id": g}`.

## 5. Actions, inbox, policy

5.1 `match` (`coverage_actions.py:113–121`): with `entry_id` naming a top-level member of a **`split`** group, the action proceeds like a standalone entry (role `principal`, `with_children` allowed, same `gate`, conservation required). `entry_in_group` stays for members of `installment` and `reward_claim` groups. The member's group is added explicitly to the lock targets and `touched_group_ids` (`lock_targets(..., group_ids=(entry.group_id,))`; today the group is locked only through `params.group_id`), so prepare's snapshot closure covers every member and a concurrent regroup is a `snapshot_conflict`. Coverage rows: `group_id=None` (§4.1). Proposal id scoping: a member's `entry_id` is an entry candidate like any other. Owner override: the action does **not** check "printed apart" — conservation and the owner's intent are the guard.

Revert semantics (reviewer note, folded into the plan's tests): the `after` snapshot records the touched entry and the touched group (`engine._after`, `snapshots.snapshot_set`) — the group row and its `member_ids`, not every sibling entry — so a later regroup is detected while a sibling's own later match is an ordinary later action touching the same group (`engine.py:433` dependency): reverting the first member match after a second one on the same group is refused `later_actions` as for any shared target. Plan tests: revert of a member match (restored to unclaimed, sibling untouched), two member matches on one group reverted in reverse order only, group update/delete sweeping the member's line.

5.2 `adjust_amount` (`ledger_actions.py:433–435`): the `not_standalone` refusal for `entry.group_id is not None` without `params.group_id` is relaxed to **split** members (other group kinds stay refused). The rewrite itself is unchanged (`apply` sets the fields and flushes; the dirty trigger and sweep handle the structural change). The member's group is added to the lock targets as in §5.1 (today `lock_targets` takes the group only with `params.group_id`, :386–390). The foreign-group branch (`params.group_id`, 2026-10-19) is unchanged.

5.3 Inbox (`inbox_service.py`): `adjustable()` (:153) treats a split member as standalone (so `accept_bank_amount` is offered on a member's `amount_delta`); `search_candidates` (:234) includes top-level members of `split` groups (join `EntryGroup.kind == "split"`; still excludes other group kinds, children, claimed and listed entries). **Search is an owner override** («AGENT-248 5»): it may list a member of a group printed whole — the owner is looking for an entry by hand and the `match` that follows requires conservation; the automatic candidates (case `candidates`, buttons offered without a search) are the matcher's and never list such a member. The search payload marks members with `group_id` (already in `_candidate`'s source entry; add the field) so the sheet can show 「拆帳成員」. `_listed_candidates` unchanged. Buttons unchanged: `match_candidate` → `match` with `entry_id`.

5.4 `attach_fee`, `split_entry`, `convert_to_split`, `move_account`, `replace_stand_in`: unchanged.

5.5 Twin guard unchanged (`stand_ins.owner_twins` already counts members); `create_system_entry` is off in production (settings v15).

5.6 Policy validator (`policy_service._single_admissible`, :498–513, and `_is_proposed`, :484–490) («AGENT-248 2, 3»):
- builds the same `statement_context` from the same loader output (`rec.load_candidates` with the structural members filled, `rec._current_lines` as `lines_all`) and passes it to `_representations`, so a line that reconcile saw as ambiguous between an apart member and a standalone entry has two admissible readings here too → `not_single_admissible`;
- additionally refuses `not_single_admissible` when `matching.incomplete_groups(kind, line, entries, groups, claimed=∅, aside)` is non-empty for the line — the same block `match()` applies before any claim — so no proposal (member or standalone) applies to a line reconcile would leave `group_readings_incomplete`;
- `_is_proposed` accepts `member_alone` for `entry_id` without `with_children` and `member_children` with it, beside `standalone`/`principal_children`.
No other policy change; the policy proposes no new action kinds.

## 6. Data migration / rollout

No schema change, no migration. Behaviour applies at the next `reconcile()` of a statement (manual, worker poll, dirty sweep). Rollout: deploy the API, then re-reconcile the statements holding the 13 open groups (121, 127, 128, 155, 156, 220, 224, 229, 504, 505) through `POST /accounts/{a}/statements/{s}/reconcile`; record before/after open-case counts in the release record. Existing active coverage is never released by this change (owner/agent coverage is kept; an auto group claim whose representation still holds is kept, reconciliation_service.py:331).

Expected production effect (triage 2026-10-24, not a promise): 4302 and 4343 stay whole (sum line held by a stand-in → owner `replace_stand_in`); 4316's members gain foreign cases; cross-account members (4260, 4280, 4282, 4300, 4319) pair with their own card line where one equals them; the rest stay open with a visible reason.

## 7. Tests

Unit (`tests/unit/test_statement_matching.py`, helpers `L`/`E`/`run`; `run` gains `lines_all=`; a `G(...)` helper builds a `Group` with structural members from the entries given plus extra off-population members):
1. Cross-account member: group (21 on account 1, 22 on account 2), population holds 21 only, structural members both → line −300 claims 21 as `member_alone`, rule `exact`, `Row.group_id is None`; `unmatched_entry_ids == []`. (Replaces `test_partial_group_is_never_admissible` :75, whose premise flips; the old assertion is kept under a renamed accepted-risk name for a group **without** structural members — the fallback path.)
1b. Cross-account expense + receivable: card holds the −300 expense, the +… receivable sits on another account, the card line equals the sum → whole; the member is **not** claimed and the line stays `line_unmatched` (today's behaviour; §3 "no partial case").
2. Same-card printed apart: group (21 −300, 22 −280), lines −300 and −280 → two `member_alone` claims; no group claim; adding a −580 line makes the group whole and both members return to the net reading (lines −300/−280 then plain `line_unmatched`).
3. Printed whole keeps the net reading: `test_group_claims_every_member_and_blocks_a_second_line` stays green as written (the −300 line is plain `line_unmatched`, no hint). `test_a_split_member_never_stands_alone_for_a_purchase_line` (:455; `_fee_group()` = −300/−280/−10) flips to a claim without a sum line; rewritten with a **−590** line present («AGENT-248 4»), name "…when the group is printed whole".
4. Whole by near tolerance: total −580, only line −575 → whole, members never alone. Whole by drift window: total −580, line −580 twelve days later → whole; no member claims a same-day −300 line.
5. Whole by a covered line: `lines_all` holds the −580 line, `lines` does not → whole; the −300 line stays `line_unmatched`.
5b. Whole by a covered member («AGENT-248 2»): split −300/−280/−100 fee, the −100 fee member claimed (absent from `entries`, present in `Group.members`), lines −300 (first) and −580 → the −580 reduced reading marks the group whole; the −300 line does **not** claim the member; repeated run identical.
5c. Whole by the children reading («AGENT-248 1»): members −300/−280, a +100 discount child, lines −300 (first) and −480 → whole by total (ii); −480 claims `group_children`, −300 is `line_unmatched`; also with the −480 line only in `lines_all`.
6. Whole regardless of gate: bank statement, income split (+30000 = +20000 + +10000), deposit line +30000 → whole; a +20000 deposit does not claim the member. Without the +30000 line → apart, +20000 claims the member.
7. Foreign-whole: JPY originals sum to the line's foreign amount, TWD off → `amount_delta` with `group_id` (existing foreign-group tests stay green); a TWD line equal to one member does **not** claim it. Reduced foreign reading (fee member printed alone) still marks whole.
8. Apart member foreign case (member's `original_amount` equals a line's foreign amount, no sum line) → `amount_delta` `{"entry_id": member, "reason": "foreign"}`, designated, never `deferred_next_period`.
9. Apart member near case and drift case (mirroring the standalone tests; `test_drift_candidates_…_skip_claimed_and_grouped` :820 changes: a grouped candidate of an apart group is listed, of a whole group is not).
10. Apart member with an uncovered discount child: `member_children` conserves; `test_a_net_group_reading_is_kept_for_members_without_a_line_of_their_own` :380 gross branch stays green (the −90 total is within 10 TWD of the −100 line → whole).
11. Ambiguity: apart member and standalone, same flow/day/text → `ambiguous`, both `entry_id`s, `group_id None`.
12. `reward_claim` / `installment` members never apart (tests :239, :443, :582–627 stay green; :443's deposit branch is re-stated with a whole group).
13. `group_readings_incomplete` with an apart group («AGENT-248 3»): expense −100 plus nine printed-alone fee members, purchase line −100, nothing near any total → reconcile still emits `ambiguous` with `hint: group_readings_incomplete` and writes no claim.
14. The remaining existing tests the builder check named (:230, :390 far branch, :469 `res2`) are each re-read in the plan and either kept green by a whole-marking line or re-stated under the new rule, with the reason in the test name.

Integration (`tests/integration/test_reconciliation_actions_*.py`, `test_inbox_api.py`, `test_policy_*.py`, `test_reconciliation_service*.py`):
15. `match` with a split member's `entry_id`: applied, coverage row `group_id NULL`, `touched_group_ids == [group]`, sibling's case untouched; `installment` member → 422 `entry_in_group`. Revert restores the member unclaimed and leaves the sibling alone; with a second member match on the same group, reverting the first is refused `later_actions`; a group update (name) and a group delete each release the member's line through the sweep.
16. `adjust_amount` on a split member → applied (group intact, group locked); `reward_claim` member → 422 `not_standalone`.
17. `adjustable()` True for a split member, False for a child; `search_candidates` lists a split member (with `group_id`) and not an installment member (`test_inbox_api.py:182` "a group member is never offered" becomes "an installment member is never offered; a split member is, as an owner override").
18. Policy validator: (a) a line ambiguous between an apart member and a standalone → an agent `match` proposal for the standalone is refused `not_single_admissible`, no coverage write; (b) a `match` proposal naming the apart member when it is the single reading → applied; (c) the §7.13 shape → any proposal on that line refused `not_single_admissible`, no coverage write; (d) parity: validator and reconcile agree on whole/apart for a statement with a covered fee member.
19. Reconcile integration: a statement with a cross-account split (fixture) → the card member claimed; a second reconcile is idempotent (same claim kept, no superseded churn); the same fixture with the sum line present → no member claim, line open.

## 8. Open points for the review

- The whole guard's tolerance reuses `near_tolerance_*` and `drift_window_days`. Alternative: its own rule keys. Spec choice: reuse (one owner-visible notion of "close enough"). Accepted by AGENT-248 as within the stated risk.
- `match` does not refuse a member of a group printed whole (owner override). Accepted by AGENT-248.
