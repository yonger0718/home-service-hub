# Reconciliation: split-group members printed apart — design

Date: 2026-10-24. Status: rev 1, for Multica spec review. Parent specs: `2026-10-08-statement-reconciliation-design.md` (§6 representations), `2026-10-15-reconciliation-r6-design.md` (R6a matcher rules, `printed_alone` 2026-10-19), `2026-10-21-reconciliation-inbox-design.md` (buttons, `match_candidate`).

## 1. Intent and why

The matcher reads a MOZE `split` group as **one purchase the owner split into categories, printed by the bank as one line**: a line is represented by the whole group (the net reading) or, since 2026-10-19, by the group minus the fee/reward members a fee line prints alone. A split member never stands alone for a purchase, withdrawal or deposit line (`_standalone`, `_lone_member`, R6a test `test_a_split_member_never_stands_alone_for_a_purchase_line`), and the `match` action refuses a member (`entry_in_group`).

The production ledger uses splits differently. Triage of the open inbox on 2026-10-24 (`~/reports/home-hub-triage-20261023/triage.md` §Session 3, read-only): 23 of the 62 open `entry_unmatched` cases are split members (13 groups; 華航卡 12, 匯豐旅人 5, HSBC Live+ 4, 永豐卡 1, 星展 1). Two shapes, neither of which the matcher can ever pair:

- **Cross-account splits** (6 groups): one member on the card, the other on 錢包 / Line Bank / another card — a purchase paid from two sources. The card's population holds one member, so the group is *partial* and inadmissible; the member is not standalone; nothing represents the card's line. The sum never appears on any statement.
- **Same-card splits the bank prints apart** (8 groups, 2–3 members each): the bank prints one line per member (e.g. group 4316: one member equals a JPY line exactly, the other has JPY lines of its own); no line equals the TWD or JPY sum. Only the whole-group reading is tried, so every member stays `entry_unmatched` and every line `line_unmatched`.

Whole-ledger scale: 337 split groups / 769 members since 2024-07, so this recurs every statement. The owner dissolved five groups by hand on 2026-10-23 to get their members matched; that loses the grouping and does not scale.

**Owner decision 2026-10-24:** make members participate individually; keep the group. Spec → plan → Multica.

## 2. Goals, non-goals, anti-goals

Goals:
1. A top-level member of a `split` group that the statement does not print whole is a candidate **like a standalone entry**: exact representation (with its own children), foreign, near and drift cases, ambiguity, `claimed_by_earlier_line`.
2. The owner can `match` and `adjust_amount` such a member from the inbox, and the inbox's candidate search lists it.
3. A group the statement prints whole keeps today's behaviour exactly (net reading, printed-alone fee members, foreign group sum, `group_readings_incomplete`).

Non-goals: new UI; changing group semantics in the ledger (no auto-dissolve, no regrouping); `reward_claim` and `installment` groups (unchanged: never members alone; `installment_group` path unchanged); a "sum of several lines = one member" reading; R6c transfers.

Anti-goals (a failure even if the code works): a member stealing a same-amount line from the group's own line (a purchase split into categories must still match its one line as a group); a member of a group printed whole appearing as an inbox candidate for some unrelated line; a group's `group_readings_incomplete` block being bypassed by a member reading; coverage rows that make the sweep or a revert believe a whole group is claimed when one member is.

## 3. Definitions

- **Member**: a top-level (`parent_entry_id is None`) entry with `group_id` of a group of kind `split`, with no schedule instance (`instance is None`; the same instance condition `_standalone` applies). Children of a member reach a line through the member's own `member_children` variant or the child path, as for a standalone parent.
- **Printed whole** (per statement, computed once before the line loop like `printed_alone`): a split group for which at least one line of the statement admits a group reading — a `group` / `group_children` representation with `_score > 0` (conserved, inside `exact_window_days`), **or** a `_foreign_groups` hit (members all in the line's foreign currency, originals summing to the line's foreign amount, inside `foreign_window_days`). Computed with `claimed = ∅` and the statement's `aside` set, over the admissible readings of `_group_variants` (a partial group has none, so it is never printed whole).
- **Printed apart**: a split group in the population that is not printed whole. Its members are **apart members**.

The decision is per statement and per reconcile run; it is not stored. A group printed whole on statement A and apart on statement B (a member moved) is decided independently on each.

## 4. Matcher (`app/services/statements/matching.py`)

4.1 `printed_whole(kind, lines, entries, groups, plan_map, rules, aside) -> frozenset[int]` (group ids), and `apart_members(entries, groups, whole) -> frozenset[int]` (entry ids). `match()` computes both after `aside`, before the loop, and threads `apart` to every rule that today asks `_standalone(e)`:

- `_representations`: an apart member is admitted where a standalone entry is, with variants `member_alone` (rule `exact`, or the line kind's rule as for standalone: `payment`/`refund`/`transfer`) and `member_children` (rule `exact_children`) — rows `Row(e.id, "principal", e.flow, group_id=None)` plus child rows as for `principal_children`. **`Row.group_id` stays `None`**: a coverage row with `group_id` means "claimed as part of a group reading" (sweep `lines_by_group`, `_ambiguous` candidate shape, revert closure) and a member alone is not that. The entry's own `group_id` is still in the coverage snapshot (`STRUCTURAL_KEYS`), so a later regroup/dissolve releases the claim as today.
- `_foreign_case` (single-entry branch), `_near_case`, `_drift_case`: `_standalone(e) or e.id in apart`.
- `_claimed_by_earlier_line`: same `apart` set (shadow representations).
- `_lone_member` / `printed_alone` / `_group_variants` / `incomplete_groups` / `_foreign_groups`: unchanged. A member printed alone by a fee line (today's rule) is unaffected by `apart` (it is admitted either way).

4.2 Competition. Apart members compete with standalone entries under the existing score/margin/ambiguity rules; a member and a standalone entry of the same amount and day are `ambiguous` as two standalones are. A claimed member drops every group reading containing it (existing rule in `_group_variants`) — harmless, since an apart group has no admissible reading anyway. The `truncated` (`group_readings_incomplete`) block applies to groups printed whole only; an apart group never has printed-alone combinations that matter, but the rule is left as is.

4.3 Reverse side. Unchanged: an unclaimed member is reported `entry_unmatched` (with `deferred_next_period` by posted date) as today; `_designated_by_cases` unchanged.

4.4 Ambiguous candidates. `_ambiguous` keeps `{"entry_id": ..., "group_id": None}` for `member_alone`/`member_children` rows (follows from `Row.group_id=None`); a candidate from a group reading keeps `{"entry_id": None, "group_id": g}`.

## 5. Actions and inbox

5.1 `match` (`coverage_actions.py`): with `entry_id` naming a top-level member of a **`split`** group, the action proceeds like a standalone entry (role `principal`, `with_children` allowed, same `gate`, conservation required). `entry_in_group` stays for members of `installment` and `reward_claim` groups and for any member of a group that is not `split`. The member's group is added to the action's lock targets and `touched_group_ids` (`lock_targets(..., group_ids=(entry.group_id,))`, the same NOWAIT group lock every group write takes; the snapshot closure then covers every member, so a concurrent regroup is a `snapshot_conflict`). Coverage rows: `group_id=None` (§4.1). Proposals: a `match` proposal naming a member is scoped as any entry candidate.

The action does **not** check "printed apart": the owner's explicit `match` of a member is their decision (the matcher's listed candidates are already apart members; the inbox search may show a member of a group printed whole, and conservation still has to hold).

5.2 `adjust_amount` (`ledger_actions.py`): the standalone gate (`not_standalone` when `entry.group_id is not None` and no `group_id` param) is relaxed to **split** members: the member's amount is rewritten exactly as a standalone's (`_adjust` through `entry_write_service`, which already handles a member edit: group lock, every member locked, STRUCTURAL change → sweep). Members of other group kinds stay `not_standalone`. The existing foreign-group branch (`params.group_id`, 2026-10-19) is unchanged.

5.3 Inbox (`inbox_service.py`): `adjustable()` returns True for a split member on the same conditions as a standalone (so `accept_bank_amount` is offered on a member's `amount_delta`); `search_candidates` includes top-level members of `split` groups (join `EntryGroup.kind == "split"`; still excludes other group kinds, children, claimed and listed entries). `_listed_candidates` is unchanged (candidates with `entry_id` already resolve). `translate`/buttons unchanged: `match_candidate` → `match` with `entry_id`.

5.4 `attach_fee`, `split_entry`, `convert_to_split`, `move_account`, `replace_stand_in`: unchanged (members stay refused where they are refused today).

5.5 Policy / twin guard: unchanged. `stand_ins.owner_twins` already counts members, so the policy never books a stand-in beside an apart member; `create_system_entry` is off in production anyway (settings v15).

## 6. Data migration / rollout

No schema change, no migration. Behaviour change applies at the next `reconcile()` of a statement (manual, worker poll, or dirty sweep). Rollout: deploy the API, then re-reconcile the statements holding the 13 open groups (ids in `group-cases.tsv`: 121, 127, 128, 155, 156, 220, 224, 229, 504, 505) through `POST /accounts/{a}/statements/{s}/reconcile` and record the before/after open-case counts in the release record. Existing active coverage is never released by this change (the sweep's structural keys are untouched; `_reconcile` keeps owner/agent coverage and auto coverage whose representation still holds — a group claim stays a group claim).

Expected production effect (from the 2026-10-24 triage, not a promise): 5 members gain an exact candidate; the 華航卡 JPY members gain foreign (`amount_delta`) cases the owner resolves with 接受銀行金額; cross-account members pair with their own card line where one exists; the rest stay open with a visible reason.

## 7. Tests (unit, `tests/unit/test_statement_matching.py`, helpers `L`/`E`/`run` as today; integration where named)

Matcher:
1. Cross-account member: group (21 on account 1, 22 on account 2), population holds 21 only → line −300 claims 21 as `member_alone`, rule `exact`, `Row.group_id is None`; `unmatched_entry_ids == []`.
2. Same-card printed apart: group (21 −300, 22 −280), lines −300 and −280 → two `member_alone` claims; no group claim; a third line −580 stays `line_unmatched`.
3. Printed whole keeps the net reading: group (21 −300, 22 −280), lines −580 and −300 → the −580 line claims the group (existing `test_group_claims_every_member_and_blocks_a_second_line` stays green) and the −300 line is `line_unmatched` with `claimed_by_earlier_line` — members of a group printed whole never stand alone (replaces `test_a_split_member_never_stands_alone_for_a_purchase_line`, which becomes "…when the group is printed whole").
4. Foreign-whole keeps members together: JPY originals sum to the line's foreign amount, TWD does not conserve → `amount_delta` with `group_id` (existing foreign-group test), and a TWD line equal to one member's flow does **not** claim the member.
5. Apart member foreign case: a member whose `original_amount` equals the line's foreign amount, group not printed whole → `amount_delta` `{"entry_id": member, "reason": "foreign"}`; `designated`, so never `deferred_next_period`.
6. Apart member near case and drift case (one test each, mirroring the standalone tests).
7. Apart member with an uncovered discount child: `member_children` variant conserves; the child is a `child` row.
8. Ambiguity: an apart member and a standalone entry, same flow, same day, equal text → `ambiguous` with both `entry_id`s and `group_id None`.
9. Printed-whole decision ignores claims: group printed whole by a line claimed earlier by a different reading (fee-first ordering as in `test_a_fee_member_printed_as_its_own_line…`) still counts as whole.
10. `reward_claim` and `installment` members: never apart (existing tests at 239/443/582–627 stay green).
11. `printed_whole` with a partial group (member outside the population) → not whole; with a group whose only reading is `group_children` → whole.

Actions / inbox (integration, `tests/integration/test_reconciliation_actions_*.py`, `test_inbox_*.py`):
12. `match` with a split member's `entry_id`: applied, coverage row `group_id NULL`, `touched_group_ids == [group]`, the sibling's case untouched; the same on an `installment` member → 422 `entry_in_group`.
13. `adjust_amount` on a split member → applied (amount rewritten, group intact); on a `reward_claim` member → 422 `not_standalone`.
14. `adjustable()` True for a split member, False for a child; `search_candidates` lists a split member and not an installment member.
15. Reconcile integration: a statement with a cross-account split (fixture) → the card member is claimed; a second reconcile is idempotent (same claim kept, no `superseded` churn).

## 8. Open points for the review

- Whether `printed_whole` should also require the group reading to score ≥ `accept` (today: `> 0`, i.e. inside the exact window). Spec choice: `> 0`, so a group with a conserving line two days off is still "whole" and its members do not wander.
- Whether `match` should refuse a member of a group printed whole (would need the matcher's decision inside the action). Spec choice: no (§5.1), conservation and the owner's intent are the guard.
