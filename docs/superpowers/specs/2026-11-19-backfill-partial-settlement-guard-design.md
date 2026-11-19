# Policy backfill: hold a line that may be part of an owner settlement receipt (#389)

Status: short design (small change), approved by the owner 2026-11-19.

## Why

A friend repaid the owner in several bank transfers. The owner had recorded each repayment as **one** settlement receipt for the sum. The statement printed one deposit line per transfer.

- No single line equals the receipt, so the matcher leaves the receipt `entry_unmatched` and each line `line_unmatched` with no candidates.
- After the grace period the policy `backfill_entry` booked every such line as income (category 對帳待分類).
- Result on one statement (2026-11-18): 8 lines booked as income beside 3 receipts that already recorded them. Income was counted twice.

The policy already refuses to book a line an owner entry may record (`stand_ins.owner_may_record` → `owner_twins`). That guard knows two shapes:

- one line ↔ one owner entry, exact or near (`_conserves`, `_near`);
- one line ↔ several members of one split group (`_split_twins`).

It does not know **several lines ↔ one owner entry**.

## Goals

- The policy never books a bank line while an unclaimed owner settlement entry, larger than the line, could still absorb it.
- The case stays open, and the case detail names that entry as the counterpart (`owner_twins`).
- The hold ends by itself: once the entry is claimed, deleted or re-recorded per transfer, the next reconcile treats the remaining lines as before.
- Bookings that have their own evidence for the line (dividends, trade settlements, transfer pairs, owner rules) are not held.

## Non-goals / anti-goals

- No N-lines ↔ one-entry matching or coverage. A receipt for the sum still has to be re-recorded per transfer.
- No guard for non-settlement entries. Tried on history: "any larger unclaimed owner entry" would have held 38 of the 54 past policy backfills, most of them correct ones on card accounts.
- No new setting, no migration, no SPA change.
- **Known gap, follow-up:** the inbox list (`GET /reconciliation/inbox`) and the SPA card do not show `owner_twins`. A held line looks like any other unmatched deposit there, and tapping 補記 on it books the duplicate by hand. Only the case detail API names the receipt. The card needs a hint (API field plus SPA text) in a follow-up change.
- The owner's own backfill button must keep working on a held line.

## Approaches

1. **Coarse settlement guard** (picked). A line has a twin when an unclaimed owner settlement entry of an eligible class is larger than the line and inside the drift window. One predicate next to the existing ones.
2. **Exact subset sum.** Hold only when a subset of the statement's unmatched lines sums to an unclaimed owner entry. Rejected: it misses a repayment whose transfers fall in two statement periods, and a repayment that arrived only in part. Both happen, and a missed hold books income twice without notice.
3. **Match N lines to one entry.** Rejected for now: it changes coverage (one entry held by several lines), the matcher and the balance check. Far larger than the problem, and receipts are recorded per transfer from now on (assistant rule of 2026-11-19).

A false hold costs one inbox question. A missed hold costs a silent double booking. So the guard errs towards holding, like `_near` does.

## Design

- **New predicate** in `app/services/stand_ins.py`, `_partial_settlement_twins(statement, line, population, rules) -> list[int]`. It returns the ids of entries `e` of `population` (the same `owner_population` list `owner_twins` builds: unclaimed, top-level, not a stand-in) for which all of these hold:
  - `e.is_settlement` is true;
  - `matching._entry_class(entry_view(e))` is in `matching.eligible_kinds(statement.kind, line.line_kind)`;
  - `abs(e flow) > abs(line flow)`, strictly; an equal or near-equal amount is already an exact or near twin;
  - `drift_days(line, e) <= rules.drift_window_days` (15 by default).
- **Opt-in.** `owner_twins` and `owner_may_record` gain `settlement_parts: bool = False`. Only with `True` are these ids added. The shape is for a line that nothing else explains, so only the plain backfill and the read model ask for it:
  - the policy collect and its re-check under the lock (`policy_service._backfill_candidate`, `_unchanged` for `backfill_entry`);
  - a pending policy `backfill_entry` proposal (`_supersede_reason`), so a proposal made before the receipt existed is superseded;
  - the writer-barrier re-check of `BackfillEntryAction` (`_TwinGuarded.settlement_parts = True` on that class only). It binds the policy and the owner applying a policy-authored proposal. A plain owner action has no guard code and stays allowed;
  - the case read model (`statement_ingest_service.get_case` → `owner_twins`), so the receipt shows as the counterpart.
- **Unchanged, by the default `False`** (found by the whole-branch review: an unconditional shape reached all of them):
  - `book_dividend` and `book_settlement`: a portfolio fact proves the line;
  - `pair_transfer` (`transfer_twin_reason` rule (c)): a peer line proves it;
  - `create_system_entry`: a bank-only line kind, and no settlement class is eligible for it anyway;
  - owner rules (`owner_rule_service._precheck` → `refuse_owner_recorded`): a record rule is the owner's standing answer for the line.
- **Scope that follows from the kind gates** (`matching.BANK_GATES` / `CARD_GATES`) and `BACKFILL_KINDS`:
  - bank `deposit` ↔ `settlement_in`; bank `withdrawal` ↔ `settlement_out` (the owner repaying a debt in several transfers is the mirror case);
  - a bank `payment` line is eligible for `settlement_out` too, but is never backfilled, so only the read model shows it;
  - card statements have no settlement class in their gates, so nothing changes for cards.
- **The hold does not end on a dismiss.** Dismissing or deferring the receipt's own case leaves the entry unclaimed, so lines within the window stay held. Each costs one manual answer; none is booked twice. Accepted.

## Evidence (production, read only, counts)

- 54 policy `backfill_entry` actions exist in total (12 applied, 42 reverted).
- The rule with a 15-day window holds exactly the 8 duplicated lines of #389 and none of the other 46. A 5-day window gives the same result.
- One further settlement entry lies 13 days from a backfilled line, but it was created a week after that action ran, so the rule would not have fired then.

## Tests (tests/integration/test_reconciliation_policy.py, beside the split-twin tests)

- Two deposit lines whose sum is one unclaimed settlement receipt: neither is backfilled; both cases stay open.
- The same lines with the receipt removed: both are backfilled (the hold ends by itself).
- The receipt is smaller than the line, is not a settlement (plain income), or lies outside the drift window: the line is backfilled.
- The receipt is already claimed by another line: the line is backfilled.
- Mirror case: two withdrawal lines and one larger unclaimed `settlement_out` entry: not backfilled.
- The owner's own `backfill_entry` on a held line still applies.
- `GET /reconciliation/cases/{id}` lists the receipt id in `owner_twins`.
- The shape is opt-in: the plain `owner_twins` / `owner_may_record` do not see it.
- A receipt recorded after the collect refuses the backfill under the lock; a pending policy proposal is superseded, and the owner applying it meanwhile gets 409 `owner_may_record`.
- A policy dividend booking and an owner record rule beside a larger unclaimed receipt still book.

## Rollout

- API deploy, no migration. The worker and verify checkouts follow main as usual; `worker/verify.py` uses the matcher only, not this guard.
- After the deploy and one clean reconcile, put account 76 back into `policy.backfill_accounts` (settings change, on the owner's go).

## Left to the builder

- Whether the predicate reads the flow through `entry_view` or `Decimal(e.amount)`, as the neighbouring helpers do.
- The exact test fixtures.
