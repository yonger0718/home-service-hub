# Reconciliation — printed-whole guard: lines an unrelated entry explains exactly (design)

Rev 2, 2026-10-24 (rev 1 + builder check: two passes, stand-in definition, signed sums — §8). Amends the split-members design (`2026-10-24-reconciliation-split-members-design.md`, "the base
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

- **Holder** of a guard line: `Holder(entry_ids: frozenset[int], stand_in: bool, exact: bool)`, built from one of two
  sources:
  - **stable coverage** (read from the DB, `guard_holders`): the active `statement_coverage` rows (`status = 'active'`)
    of the guard lines that this reconcile run does **not** rewrite — on this statement the rows whose `match_kind` is
    in `OWNED_KINDS` (owner `manual`, policy `agent`; reconciliation_service.py `_manual_claims`), on the neighbouring
    statements every active row. This statement's own `auto` rows are the *previous* run's output: `_write_coverage`
    releases and rewrites them every run (reconciliation_service.py:427–433), so reading them would make the first
    run differ from the second and break idempotency (builder check Q1). Rows are read by `line_id` (guard lines span
    statements), not through `coverage_service.active_rows`.
  - **this run's own claims** (in memory, pass 1 → pass 2 below): the rows the matcher claimed on this statement's
    lines in pass 1 (`Row.id`, `Row.flow` of each claimed representation).
  Fields:
  - `entry_ids`: the rows' `entry_id`s, plus the snapshot `id` of a row whose `entry_id` was SET NULL (the entry was
    deleted) — the fallback `_manual_claims` uses (reconciliation_service.py:399–406);
  - `stand_in`: any holder entry is in `stand_ins.stand_in_entry_ids(db, accounts)` (created by an applied backfill
    action, the matcher's own `Entry.stand_in`, loaded at reconciliation_service.py:271 — an edited stand-in keeps its
    id, so it stays one) **or** has `source == STATEMENT_SOURCE` (`"statement"`, entry_write_service.py:245). The union
    is deliberately broad: `split_entry` members, `create_refund`, `attach_fee` children and the period-adjustment
    entries also carry `source = statement`; a line they hold stays a guard line (fail-closed), at the cost of a rare
    false whole. The loader exposes the predicate as a new `Entry.source: str | None` (set from `LedgerEntry.source`
    in `entry_view`), so both holder sources use one rule;
  - `exact`: every `entry_id` resolves to a live `ledger_entry` row (or, for own claims, to a population entry) **and**
    either Σ `flow` over the holder rows `_same` `line.flow` (**signed** — `entry_view` sets `flow = amount` and the
    matcher's `conserved` compares row flows to the line directly, so a purchase member plus a discount child on one
    line is exact), or every holder entry has `original_currency == line.foreign_currency` and
    |Σ `original_amount`| `_same` |`line.foreign_amount`| (`line.foreign_amount` not null; magnitude, as
    `_foreign_case` does). A row with an unresolvable entry makes `exact = False`.
  A line with no row from either source has no holder.

- **Two passes** (builder check Q1): `reconcile()` runs the matcher twice over the same lines and population.
  Pass 1: `holders = stable`. Pass 2: `holders = stable ∪ holders(pass-1 claims on this statement)`; its result is the
  run's result (coverage, cases, proposals). Both passes depend only on stable state, so a re-run with nothing
  changed reproduces the same result (module docstring rule), and a member that ties with a pass-1 claimant for the
  same line is `ambiguous` in pass 2 and stays so on every run — no flip-flop. A third-order chain (a member freed in
  pass 2 explaining a line that holds a third group) is **not** followed: that group stays whole, fail-closed, until
  the owner acts. Pass 1 is the only extra cost (the matcher is pure and fast; the 170k-statement fuzz ran it in
  minutes).

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
8. Two holder rows (a group reading of *another* split group claimed the line: one row per member) whose flows sum
   to the line → exact, unrelated → the line is skipped; the same two rows summing to something else → whole.
9. First run on a never-reconciled statement (no stored coverage at all): pass 1 claims the unrelated line for its
   standalone entry, pass 2 releases the group → the member is matched in the **first** run; a second run is
   identical (idempotency).
10. Previous run's `auto` row on this statement is the only holder → ignored in pass 1 (not stable); the group is
    whole in pass 1 and, if pass 1 claims the line again, apart in pass 2 — same result as shape 9.

## 4. Code

4.1 `matching.py`:
- `@dataclass(frozen=True) class Holder: entry_ids: frozenset[int]; stand_in: bool; exact: bool`.
- `Entry.source: str | None = None` (loader-set; `None` in old tests behaves as not statement-sourced).
- `printed_whole(lines_all, entries, groups, rules, aside, aside_children, account_currency=None, holders=None)` —
  `holders: Mapping[int, Holder] | None` (line id → holder; `None`/`{}` = today's behaviour). Per group, the scan
  skips a line whose holder is an unrelated exact holder for that group (`_unrelated_exact(holder, g, by_id)`).
- `statement_context(..., holders=None)` and `match(..., lines_all=None, holders=None)` thread it.
- `claim_holders(result, lines_all, entries, stand_in: Callable[[Entry], bool]) -> dict[int, Holder]`: the pass-1
  claims of a `MatchResult` as holders (§3 arithmetic over `Row.flow`; `exact` is true by construction for conserving
  rules but is still computed). Pure.
- `merge_holders(stable, own) -> dict[int, Holder]`: per line, `entry_ids` united, `stand_in` or-ed, `exact` = the
  union's rows re-checked (a stable owner row plus an own claim on one line: exact only if the union sums to the
  line). Pure.

4.2 `reconciliation_service.py`:
- `entry_view` sets `Entry.source`.
- `guard_holders(db, statement, lines_all) -> dict[int, matching.Holder]`: stable rows for the guard lines' ids
  (chunks of 200, `in_`; this statement's rows filtered to `OWNED_KINDS`), their `LedgerEntry` rows (`amount`,
  `original_amount`, `original_currency`, `source`) in one query, `stand_in_entry_ids` already loaded for the run, the
  §3 arithmetic. Read only.
- `reconcile()` (~:864): `lines_all = guard_lines(...)`, `stable = guard_holders(...)`, pass 1 `match(..., holders=stable)`,
  `own = claim_holders(pass1, ...)`, pass 2 `match(..., holders=merge_holders(stable, own))`; everything after `match()`
  consumes pass 2 only.

4.3 `policy_service.py` `_single_admissible` (~:516): the same `guard_lines`, `guard_holders`, a pass-1 `match()` with
the stable holders over the same population and lines reconcile used, `claim_holders` + `merge_holders`, then
`statement_context(..., holders=…)` — exact parity with reconcile's pass-2 context on unchanged state (base spec §5.6).
The validator's existing caveat stands: state the owner changed between the run and the validation changes both.

4.4 Nothing else: `guard_lines`, `load_candidates` (beyond `Entry.source`), actions, inbox, SPA, schemas unchanged.

## 5. Rollout

No migration. Deploy the accounting API; re-reconcile statements 128 and 229 (127 / 220 are neighbours and share the
decision). Expected: 57401 claims line 1466 (`exact`, auto); 57273 claims 2375 (auto); 57402 has three free
fx-exact lines within 2 days (1440, 1444, 1468) and is expected to surface as `ambiguous`, not auto-matched. Open
cases 155 → about 150. Groups 4302, 4343, 4309 unchanged (owner `replace_stand_in`). The simulation script is brought to the
§3 arithmetic (signed Σ over all holder rows, members' children in the disjointness test, unresolvable entries not
exact; stored `auto` rows stand in for pass-1 claims — equal to them when nothing changed since the last run) and
re-run on the branch before the PR. Pass condition: groups 4316 and 4300 flip; no group whose hit line has a stand-in
holder flips; the flip list (groups, not statement rows) is pasted into the PR body.

## 6. Tests

Unit (`tests/unit/test_statement_matching.py`): shapes 1–8 against `printed_whole` / `statement_context` with a
hand-built `holders` map; `claim_holders` (a conserving claim → exact; a member claim is related to its own group) and
`merge_holders` (owner row + own claim on one line); `holders=None` leaves every existing guard test green.
Integration (`tests/integration/test_reconciliation_service.py`): shape 1 end to end — a statement whose near line
is covered (`auto`, unrelated entry, exact), the member matched `member_alone` to its own line; shape 3 end to end —
a stand-in (`source = statement`) covering the sum line keeps the group whole and the member `entry_unmatched`;
`guard_holders` with a SET-NULL row (shape 7), a neighbouring-statement `auto` row (counted, shape 4) and this
statement's own `auto` row (ignored, shape 10); shape 9 — first run on a fresh statement matches the member, a second
run writes the same coverage and cases (idempotency).
Policy (`tests/integration/test_reconciliation_policy.py`): the validator admits the member's proposal in shape 1 and
refuses it in shape 3 (parity).

## 7. Open points for the review

- Whether an **owner** manual match to an unrelated exact entry should be trusted the same as an auto claim. Rev 2:
  yes — the owner said "this line is that entry".
- Whether the validator should run the pass-1 matcher (exact parity, rev 2) or read this statement's stored `auto`
  rows (cheaper; diverges only towards refusing more, since pass-2 rows can add member claims or drop a tied claim).

## 8. Builder check (rev 1 → rev 2, 2026-10-24)

- Q1 own `auto` coverage is rewritten by the run → stable rows only, plus a two-pass match; validator runs pass 1.
- Q2 `source = statement` is broader (split/refund/fee/adjustment entries) and misses nothing a backfill made →
  stand-in = `stand_in_entry_ids` ∪ `source = statement`; `Entry.source` added.
- Q3 ledger and statement flows share a sign convention → signed Σ for TWD; the baseline script is brought to the
  spec's arithmetic with an explicit pass condition (§5).
