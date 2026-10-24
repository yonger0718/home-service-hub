# Reconciliation — printed-whole guard: lines an unrelated entry explains exactly (design)

Rev 1, 2026-10-24. Amends the split-members design (`2026-10-24-reconciliation-split-members-design.md`, "the base
spec") §3 "Printed whole" and §5.6. GitHub issue #136. Everything the base spec says still holds unless this document
says otherwise.

## 1. Intent and why

PR #130 (main `89d4440`) decides per statement whether a `split` group is *printed whole* (its members stay together)
or *printed apart* (its claimable members stand alone). The guard scans the **guard lines** — this statement's current
lines including covered ones, plus the participating accounts' neighbouring statements' lines — and takes the group
whole when any line prints a reading within the near tolerance (base spec §3 (a)) or by exact foreign amount ((b)).
The line's coverage is deliberately never consulted: a sum line the owner or the policy booked to a stand-in must
still mark the group whole, so `replace_stand_in` remains the remedy (base spec §1 third shape).

Production audit after the deploy (`~/reports/home-hub-pr130-api-20261024/whole-guard-diagnosis.txt`,
`issue136-simulation.txt`, owner-only): of the five whole groups among the open member cases, three are right — the
hit line is held by a `source = statement` stand-in (groups 4302, 4343; 4309's sum line sits on the neighbouring
statement 127 under stand-in 28776). Two are wrong: group 4316 (JPY) is held whole by two **unrelated purchases**
2.3 % and 4.7 % off its total, each already covered by its own MOZE entry whose TWD **and** JPY amounts equal the line;
group 4300 (TWD) by a line 0.9 % off, covered by an auto match to an unrelated entry. Neither group has any line
equal to its sum (TWD or foreign, ±15 days, neighbours included), and each member has a free, same-day, exact line
of its own — currently open `line_unmatched` cases 847, 1381 and 849. A simulation of the rule below over every live
statement flips 10 of 231 whole decisions, all of the same shape (one member with a free same-day exact card line,
the other member on an account without statements), and no stand-in-held group.

A line that another entry explains **exactly** is that entry's line. It cannot also be this group's sum print, so it
must not keep the group whole. A line a stand-in holds is the group's sum print booked blind, and must.

## 2. Goals, non-goals, anti-goals

Goals:
1. A guard line held only by entries outside the group, none of them a stand-in, whose held amount equals the line
   exactly, does not print any reading of that group.
2. Every other guard line keeps today's behaviour: uncovered, held by a stand-in, held by a member or a member's
   child, held inexactly (an owner `amount_differs` match), or whose holder cannot be resolved — fail-closed, whole.
3. `reconcile()` and the policy validator decide identically (base spec §5.6 parity).

Non-goals:
- Changing the tolerance (5 %) or the window; changing (b).
- Comparing foreign-denominated lines in the foreign currency *only* (dropping (a) when both sides carry the same
  currency). Sound, releases 4316 alone, not 4300; the holder rule covers both, so it stays a possible follow-up.
- Any change to `printed_alone`, `printed_alone_children`, `group_readings`, the claim side, actions, inbox or SPA.
- A stored decision; a migration.

Anti-goals: a group with a stand-in-held sum line going apart (the three audited groups must stay whole); the claim
side or `aside_all` seeing a different line set than today; a holder of this group's own pieces counting as
"unrelated".

## 3. Definitions (additions to base spec §3)

- **Holder** of a guard line: the active `statement_coverage` rows of that line (`status = 'active'`, any
  `match_kind`, any statement — guard lines span the neighbours, so rows are read by `line_id`, not through
  `coverage_service.active_rows`). `Holder(entry_ids: frozenset[int], stand_in: bool, exact: bool)`:
  - `entry_ids`: the rows' `entry_id`s, plus the snapshot `id` of a row whose `entry_id` was SET NULL (the entry was
    deleted) — the same fallback `_manual_claims` uses (reconciliation_service.py:399–406);
  - `stand_in`: any holder entry has `source == STATEMENT_SOURCE` (`"statement"`, entry_write_service.py:245; stand-ins
    and policy system entries are booked with it);
  - `exact`: every `entry_id` resolves to a live `ledger_entry` row **and** either Σ|`amount`| over the holder entries
    `_same` |`line.flow`|, or every holder entry has `original_currency == line.foreign_currency` and
    Σ|`original_amount`| `_same` |`line.foreign_amount`| (`line.foreign_amount` not null). A row with an unresolvable
    entry makes `exact = False`. Magnitudes, not signs: a holder's `amount` sign convention is the ledger's, the line's
    flow is the statement's.
  A line with no active row has no holder.

- **Unrelated exact holder** (per group `g`): the line's holder exists, `exact` is true, `stand_in` is false, and
  `entry_ids` is disjoint from `g.member_ids ∪ {children of every member}` — children taken from the structural rows'
  `all_children` (falling back to `children`, as the guard does), so a line a member's fee child holds is never
  "unrelated".

- **Printed whole** (replaces the first sentence of base spec §3 "Printed whole"): a split group with a reading that
  some guard line **without an unrelated exact holder for this group** prints under (a) or (b), or whole by fail-closed.
  The holder test is applied in `printed_whole` only — `printed_alone`, `printed_alone_children` and `group_readings`
  still see every guard line, so `aside_all`, `aside_children` and the readings are unchanged (anti-goal 2).

Worked shapes (the §7 tests):
1. 4316 shape — JPY group, no sum line; two lines within 5 % held exactly (TWD and JPY) by unrelated entries → apart;
   member's own exact free line claimed (`member_alone`).
2. 4300 shape — TWD group, a line within 5 % held exactly by an auto match to an unrelated entry → apart.
3. 4302 / 4343 shape — sum line held by a `source = statement` stand-in (exact, as a stand-in always is) → whole.
4. 4309 shape — sum line on the neighbouring statement, held there by a stand-in → whole.
5. Inexact unrelated holder (owner `amount_differs` match: held amount ≠ line) → whole.
6. Holder is a member of this group, or a member's child (printed-alone fee child) → the line is not unrelated → the
   guard behaves exactly as today for it (whole when it prints a reading).
7. Holder row with `entry_id` NULL and a snapshot id that resolves to no entry → not exact → whole.
8. Two holder rows (a group reading of *another* split group claimed the line: one row per member) whose amounts sum
   to the line → exact, unrelated → the line is skipped; the same two rows summing to something else → whole.

## 4. Code

4.1 `matching.py`:
- `@dataclass(frozen=True) class Holder: entry_ids: frozenset[int]; stand_in: bool; exact: bool`.
- `printed_whole(lines_all, entries, groups, rules, aside, aside_children, account_currency=None, holders=None)` —
  `holders: Mapping[int, Holder] | None` (line id → holder; `None`/`{}` = today's behaviour). Per group, the scan
  skips a line whose holder is an unrelated exact holder for that group (`_unrelated_exact(holder, g, by_id)`).
- `statement_context(kind, lines, lines_all, entries, groups, rules, account_currency, holders=None)` threads it.
- `match(..., lines_all=None, holders=None)` threads it to `statement_context`.

4.2 `reconciliation_service.py`:
- `guard_holders(db, lines_all: list[matching.Line]) -> dict[int, matching.Holder]`: active `StatementCoverage` rows
  for the guard lines' ids (chunks of 200, `in_`), their `LedgerEntry` rows (`amount`, `original_amount`,
  `original_currency`, `source`) in one query, the §3 arithmetic. Read only.
- `reconcile()` (~:864) computes `lines_all = guard_lines(...)` once, then `holders = guard_holders(db, lines_all)`,
  and passes both to `match()`.

4.3 `policy_service.py` `_single_admissible` (~:516): builds `holders` the same way from the same `guard_lines` list
and passes it to `statement_context` — parity (base spec §5.6).

4.4 Nothing else: `guard_lines`, `load_candidates`, actions, inbox, SPA, schemas unchanged.

## 5. Rollout

No migration. Deploy the accounting API; re-reconcile statements 128 and 229 (127 / 220 are neighbours and share the
decision). Expected: 57401 claims line 1466 (`exact`, auto); 57273 claims 2375 (auto); 57402 has three free
fx-exact lines within 2 days (1440, 1444, 1468) and is expected to surface as `ambiguous`, not auto-matched. Open
cases 155 → about 150. Groups 4302, 4343, 4309 unchanged (owner `replace_stand_in`). The simulation scripts stay in
the release record and are re-run on the branch before the PR as the regression baseline (same 10 flips, no stand-in
group).

## 6. Tests

Unit (`tests/unit/test_statement_matching.py`): shapes 1–8 against `printed_whole` / `statement_context` with a
hand-built `holders` map; `holders=None` leaves every existing guard test green.
Integration (`tests/integration/test_reconciliation_service.py`): shape 1 end to end — a statement whose near line
is covered (`auto`, unrelated entry, exact), the member matched `member_alone` to its own line; shape 3 end to end —
a stand-in (`source = statement`) covering the sum line keeps the group whole and the member `entry_unmatched`;
`guard_holders` with a SET-NULL row (shape 7) and a neighbouring-statement row (shape 4).
Policy (`tests/integration/test_reconciliation_policy.py`): the validator admits the member's proposal in shape 1 and
refuses it in shape 3 (parity).

## 7. Open points for the review

- Whether `exact` should also accept Σ signed flows (a refund child and a purchase member on one line). Rev 1 says
  magnitudes only; a mismatch is fail-closed, so the cost of being wrong is a group staying whole, as today.
- Whether an **owner** manual match to an unrelated exact entry should be trusted the same as an auto match. Rev 1:
  yes — the owner said "this line is that entry".
