# Reconciliation — printed-whole guard: lines an unrelated entry explains exactly (design)

Rev 3, 2026-10-24 (rev 2 + Multica AGENT-252 FAIL folded: row metadata for children, merge invariant, validator parity wording — §8). Amends the split-members design (`2026-10-24-reconciliation-split-members-design.md`, "the base
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
    lines in pass 1 (`Row.entry_id`, `Row.flow` of each claimed representation — parent **and child** rows, matching.py
    :550/:568/:576).
  **Row metadata** («AGENT-252 1»): both sources resolve every holder row id — a top-level entry or a child — to the
  same three facts, `source`, `original_amount`, `original_currency`. The DB source reads them from `ledger_entry`
  (children are ledger rows). The in-memory source reads them from the population: `Entry.source` (new) and
  `Child.source`, `Child.original_amount`, `Child.original_currency` (new; the loader fills them where it builds
  `matching.Child`, reconciliation_service.py:245 and :308), looked up in `by_row = {e.id: e} ∪ {c.id: c for c in
  e.children ∪ e.all_children}` over the unfiltered population. A row id neither source can resolve makes the holder
  **not exact** (fail-closed). One predicate serves both: `stand_in_row(row_id, source, stand_in_ids) = row_id ∈
  stand_in_ids or source == "statement"`. A fee child `attach_fee` booked (`source = statement`) therefore makes its
  parent's claim a stand-in holder — the line stays a guard line (fail-closed; reviewer's −90 + −10 example).
  Fields:
  - `entry_ids`: the rows' `entry_id`s, plus the snapshot `id` of a row whose `entry_id` was SET NULL (the entry was
    deleted) — the fallback `_manual_claims` uses (reconciliation_service.py:399–406);
  - `stand_in`: `stand_in_row` is true for any holder row — in `stand_ins.stand_in_entry_ids(db, accounts)` (created by
    an applied backfill action, the matcher's own `Entry.stand_in`, loaded at reconciliation_service.py:271 — an edited
    stand-in keeps its id, so it stays one) **or** `source == STATEMENT_SOURCE` (`"statement"`, entry_write_service.py:245). The union
    is deliberately broad: `split_entry` members, `create_refund`, `attach_fee` children and the period-adjustment
    entries also carry `source = statement`; a line they hold stays a guard line (fail-closed), at the cost of a rare
    false whole;
  - `exact`: every row id resolves (row metadata above) **and**
    either Σ `flow` over the holder rows `_same` `line.flow` (**signed** — `entry_view` sets `flow = amount` and the
    matcher's `conserved` compares row flows to the line directly, so a purchase member plus a discount child on one
    line is exact), or every holder entry has `original_currency == line.foreign_currency` and
    |Σ `original_amount`| `_same` |`line.foreign_amount`| (`line.foreign_amount` not null; magnitude, as
    `_foreign_case` does); for own claims the foreign sum is over the resolved rows' `original_amount`. A row with an
    unresolvable id makes `exact = False`.
  A line with no row from either source has no holder. **The two sources never share a line** («AGENT-252 2»): this
  statement's stable rows sit on owner/agent-covered lines, which are removed from the matcher's claimable `lines`
  (reconciliation_service.py ~:779–781), and neighbouring lines are never claimable here; `merge_holders` asserts
  this invariant and, should a line nevertheless carry both, marks it `exact = False` (fail-closed) — it has no
  flows to re-check.

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
11. Pass-1 claim of a parent plus its `source = statement` fee child (`attach_fee`) on the near line → `stand_in`
    → the line stays a guard line → whole (built through `match` → `claim_holders`, not a hand-built `Holder`).
12. Pass-1 claim of a plain parent plus its plain discount child (`principal_children`, signed sum = the line) →
    exact, unrelated → apart.
13. Third-order chain: group A's member, released in pass 2, exactly claims a line that holds group B whole → B
    stays whole in this run's result **and** in the validator (both stop after pass 2; neither reads stored
    pass-2 rows).
14. A pass-1 claimant and a member released in pass 2 tie on one line → `ambiguous` in pass 2, the line released;
    a second run and the validator reproduce the same decision (pass 1 is recomputed, never read back).

## 4. Code

4.1 `matching.py`:
- `@dataclass(frozen=True) class Holder: entry_ids: frozenset[int]; stand_in: bool; exact: bool`.
- `Entry.source: str | None = None` (loader-set; `None` in old tests behaves as not statement-sourced).
- `printed_whole(lines_all, entries, groups, rules, aside, aside_children, account_currency=None, holders=None)` —
  `holders: Mapping[int, Holder] | None` (line id → holder; `None`/`{}` = today's behaviour). Per group, the scan
  skips a line whose holder is an unrelated exact holder for that group (`_unrelated_exact(holder, g, by_id)`).
- `statement_context(..., holders=None)` and `match(..., lines_all=None, holders=None)` thread it.
- `Child.source: str | None = None`, `Child.original_amount: Decimal | None = None`,
  `Child.original_currency: str | None = None` (loader-set).
- `claim_holders(result, lines_all, entries, stand_in_ids: frozenset[int]) -> dict[int, Holder]`: the pass-1 claims
  of a `MatchResult` as holders — rows resolved through `by_row` (§3 row metadata), `stand_in` via `stand_in_row`,
  `exact` by the §3 arithmetic over `Row.flow` and the resolved rows' foreign fields (true by construction for
  conserving rules, still computed; an unresolved row id → `False`). Pure.
- `merge_holders(stable, own) -> dict[int, Holder]`: disjoint union; a line present on both sides gets `entry_ids`
  united, `stand_in` or-ed and `exact = False` (§3 invariant, fail-closed). Pure.

4.2 `reconciliation_service.py`:
- `entry_view` sets `Entry.source`; the two `matching.Child(...)` constructions (:245, :308) set the child's `source`,
  `original_amount`, `original_currency`.
- `guard_holders(db, statement, lines_all) -> dict[int, matching.Holder]`: stable rows for the guard lines' ids
  (chunks of 200, `in_`; this statement's rows filtered to `OWNED_KINDS`), their `LedgerEntry` rows (`amount`,
  `original_amount`, `original_currency`, `source`) in one query, `stand_in_entry_ids` already loaded for the run, the
  §3 arithmetic (children resolve like parents — they are `ledger_entry` rows). Read only.
- `reconcile()` (~:864): `lines_all = guard_lines(...)`, `stable = guard_holders(...)`, pass 1 `match(..., holders=stable)`,
  `own = claim_holders(pass1, lines_all, entries, stand_in_ids)`, pass 2 `match(..., holders=merge_holders(stable, own))`; everything after `match()`
  consumes pass 2 only.

4.3 `policy_service.py` `_single_admissible` (~:516): the same `guard_lines`, `guard_holders`, a pass-1 `match()` with
the stable holders over the same population and lines reconcile used, `claim_holders` + `merge_holders`, then
`statement_context(..., holders=…)` — exact parity with reconcile's pass-2 context on unchanged state (base spec §5.6).
The validator never reads this statement's stored `auto` rows as holders: they are pass-2 output, and a member claim
among them would skip a guard line reconcile's pass 2 still honoured (shape 13), admitting a third group reconcile
kept whole («AGENT-252 3»). The validator's existing caveat stands: state the owner changed between the run and the
validation changes both.

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
hand-built `holders` map; shapes 11, 12 and a member-child relatedness case built **through `match` → `claim_holders`**
(no hand-built `Holder`); `merge_holders` collision → `exact = False`; `holders=None` leaves every existing guard test
green.
Integration (`tests/integration/test_reconciliation_service.py`): shape 1 end to end — a statement whose near line
is covered (`auto`, unrelated entry, exact), the member matched `member_alone` to its own line; shape 3 end to end —
a stand-in (`source = statement`) covering the sum line keeps the group whole and the member `entry_unmatched`;
`guard_holders` with a SET-NULL row (shape 7), a neighbouring-statement `auto` row (counted, shape 4) and this
statement's own `auto` row (ignored, shape 10); shape 9 — first run on a fresh statement matches the member, a second
run writes the same coverage and cases (idempotency); shapes 13 (third-order chain: B whole after the run) and 14
(tie → `ambiguous`, second run identical). The two PR #130 fixtures whose 代墊 entry is a plain `manual` entry
(`test_reconcile_keeps_a_group_whole_when_its_sum_line_is_owner_covered`, the `stand_in` branch of
`…_on_the_previous_statement`) are re-seeded with `source="statement"` (they describe a stand-in) and a sibling test
pins the plain-entry case as apart (owner-trusted exact holder).
Policy (`tests/integration/test_reconciliation_policy.py`): the validator admits the member's proposal in shape 1,
refuses it in shape 3, and agrees with reconcile on shapes 13 and 14 (parity).

## 7. Open points for the review

- Resolved by AGENT-252: an **owner** manual match to an unrelated exact entry is trusted (all rows resolved, no
  stand-in, disjoint from members and children, signed TWD or full same-currency foreign exactness — never on
  `manual` alone); the validator recomputes pass 1 (never reads stored `auto` rows).
- None open.

## 8. Review log

Builder check (rev 1 → rev 2, 2026-10-24):
- Q1 own `auto` coverage is rewritten by the run → stable rows only, plus a two-pass match; validator runs pass 1.
- Q2 `source = statement` is broader (split/refund/fee/adjustment entries) and misses nothing a backfill made →
  stand-in = `stand_in_entry_ids` ∪ `source = statement`; `Entry.source` added.
- Q3 ledger and statement flows share a sign convention → signed Σ for TWD; the baseline script is brought to the
  spec's arithmetic with an explicit pass condition (§5).

Multica AGENT-251/252, dev-astra, rev 2 @ `1833e09` — **FAIL** (one spec round, folded here, rev 3):
1. major — own-claim holders could not resolve child rows (`Child` had no source/foreign fields) → §3 "Row
   metadata": `Child.source/original_amount/original_currency`, `by_row` over entries ∪ children, one `stand_in_row`
   predicate for both sources, unresolved row → not exact; shapes 11/12 and a relatedness case tested through
   `match` → `claim_holders`.
2. minor — `merge_holders` cannot re-check exactness from summaries → the two sources never share a line
   (invariant, §3); a collision is `exact = False`; the overlap test becomes the collision test.
3. minor — "stored auto rows diverge only towards refusing more" was wrong (shape 13) → sentence removed; the
   validator recomputes pass 1; shapes 13/14 tested for reconcile and the validator.

## 9. Errata (execution 2026-10-24)

- `match` never claims through the foreign rule (it opens an `amount_delta` case), so `claim_holders`' foreign branch is reached only by hand-built claims; it is kept for the one-rule parity with the DB side.
- The exactness rule is one public helper `matching.holder_exact(flows, foreign, line)`, used by `claim_holders` and `reconciliation_service.guard_holders` (§4.1/§4.2 named the arithmetic twice).
- `_related_ids(g, by_id)` is computed once per group in `printed_whole`; `_unrelated_exact(holder, related)`.
- Coverage always balances when written, so an inexact DB holder exists only after an entry edit with dirty tracking off (tests build it that way).
- Both `matching.Child` constructions go through `reconciliation_service._child_view`.
- The policy validator runs one extra `match()` per validation (pass 1); its pre-existing lock-trim caveat is unchanged.
- §4.2/§4.3: the parity block (guard lines, stand-in ids, stable holders, pass 1, `claim_holders`, `merge_holders`) is one helper, `reconciliation_service.pass_two_holders(...) -> (lines_all, holders)`, called by `reconcile()` and by `_single_admissible` (Task 3 review ⚠️) — parity by construction. A validator test with a two-link chain (a stable owner holder releases A in pass 1, A's claim releases B in pass 2) pins the pass-1 link.
- Final review: an unrelated exact holder skips a line only for near prints; a line that prints a reading exactly (TWD `_same` or exact foreign) keeps the group whole — a plain entry equal to the group's total is a tie the owner resolves, not that entry's line. (`matching._exact_print`; the shape-1 JPY fixture moved its held line to −29100 JPY so it prints the group's −29000 JPY total only near.)
- §3 "The two sources never share a line": `merge_holders` fails closed on a collision (`exact = False`); it does not assert.
- Final review: `guard_holders` takes the guard's statement ids (`reconciliation_service.guard_statement_ids`, sharing `_guard_neighbours` with `guard_lines`) and filters `statement_coverage` by the indexed `statement_id` as well as by `line_id`.
