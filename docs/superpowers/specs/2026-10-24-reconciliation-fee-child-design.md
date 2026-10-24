# Reconciliation — a fee child claims the statement's fee line (issue #143)

Rev 5, 2026-10-24 (rev 4 + Multica AGENT-271 re-check FAIL folded, §10). Branch `feat/reconciliation-fee-child` from main `fbe7425` (PR #141 merged). Amends the matcher
(`app/services/statements/matching.py`) and the candidate loader (`app/services/reconciliation_service.py
load_candidates`). No schema change. Builds on the split-members spec (`2026-10-24-reconciliation-split-members-design.md`)
and the guard-holders spec (`2026-10-24-reconciliation-guard-holders-design.md`); neither rules on the shapes below.

## 1. Intent and why

The owner records a card purchase that carried a bank fee as **one parent entry plus one `kind=fee` child** (MOZE's FX
fee under the purchase; `attach_fee` books the same shape). The bank prints **two lines**: the purchase and a separate
`fee` line. Since R6 the matcher can pair the child with the fee line on its own (the *child path* of
`_representations`, matching.py:617–625: same amount, kind in the fee gate, the parent's date within
`exact_window_days`) — 125 active `auto` coverage rows on production are exactly that. Three gaps remain, found on
2026-10-24 when `create_system_entry` was switched on (settings v16) and 504 / 554 / 555 re-reconciled with no effect:

1. **The parent is owner-covered → the child vanishes.** `owned_coverage` puts a manually matched parent into
   `exclude`; `load_candidates` drops it from `parents` and loads children only for surviving parents
   (reconciliation_service.py:256–270). The fee child is not in the population at all; the fee line ends as
   `line_unmatched {"hint": "bank_only"}`. The same happens when the parent is claimed on *another* statement (a
   fee printed one statement later). Production: 555/5784, 555/5786, 504/4244, 504/4245.
2. **Identical fee children tie.** Two fee lines of the same amount on one statement and two fee children of that
   amount → each line sees two equal child representations → `ambiguous` (R6a.2), nothing claimed. Production:
   504/4242 + 4243 (owner dismissed the two ambiguous cases on 2026-10-17; the children 57288/57306 sit in
   `entry_unmatched`), and 555 once gap 1 is closed (5784/5786 vs 57706/57708, same day, same amount).
3. **The child's fee differs from the bank's.** The owner typed one fee, the bank charged another (554/4164 vs
   58223). The child path needs `_same`; the near/drift fallbacks iterate top-level entries only, so the line falls to
   `bank_only` with no pointer to the child the owner should correct.

In every gap the policy's `create_system_entry` then refuses — `stand_ins.owner_children` makes the unclaimed child an
owner twin (`owner_may_record`, the 2026-10-17 fix), which is right: booking a system fee beside the owner's child
would duplicate it. And the owner cannot match the child by hand: the `match` action refuses a child (`child_entry`),
`adjust_amount` / `attach_fee` refuse `not_standalone`. The line is stuck. This spec unsticks the matcher side.

## 2. Goals, non-goals, anti-goals

Goals.

1. A fee child whose **standalone** parent (`group_id` None) is covered — by the owner, an agent, the policy, or an
   `auto` claim on another statement — stays a claimable child, so the existing child path claims the fee line
   exactly as it does for an auto-claimed parent of this statement.
2. Identical fee children are paired with identical fee lines deterministically when, and only when, the pairing
   cannot go wrong: the children and lines form a closed, **complete** cohort (every child admissible for every
   line) of equal size; otherwise the tie stays `ambiguous`.
3. A `bank_only` fee line that has a *near* child names it (`near_children` in the case context), so the owner sees
   which fee to correct; no new action, no policy change.
4. Reconcile and the policy validator keep seeing the same population and the same claims (shared loader).
5. Every run is idempotent and converges: re-running a statement after the change keeps the claims it made.

Non-goals (follow-ups if the owner wants them).

- Owner `match` / agent proposals naming a child (`child_entry`, `_is_proposed` has no `child` kind) — unchanged.
- Children of **split-group members** that are owner-covered: today a structural row (`claimable=False`) read by the
  printed-whole guard only; its children still do not claim lines. Keeping it fail-closed; see §8.
- Child-level `amount_delta`, `adjust_amount` on a child, `Child` dates: unchanged. A near child is *reported*
  (Goal 3), not acted on; the owner's remedy is to correct the child's amount in the ledger and re-reconcile.
- Policy: `create_system_entry`, the twin guard, `attach_fee`: unchanged.

Anti-goals (would make this a failure even if the tests pass).

- A covered parent being claimed again (its principal row in any new representation) — a `duplicate_claim`
  conflict or a double count.
- A child claimed on a line whose kind gate does not admit `fee` (the child path's gate check stays).
- An identical-children tie resolved when a perfect pairing does **not** exist (one line, two children — 504/4245 —
  must stay `ambiguous`, because the second child is probably a duplicate the owner needs to see).
- A near child *claimed* (only exact amounts claim).
- Any change to which reading a purchase line claims, to the printed-whole guard, or to holders.

## 3. Definitions

- **Standalone parent**: `Entry` with `group_id is None`, `parent_entry_id is None`, not an installment instance
  (`_standalone`: `instance is None or instance.definition_kind != "installment"` — a recurring instance is still
  standalone). Installment membership comes from the schedule tables (`_instances`), **not** from `group_id`, so any
  predicate on "standalone" must consult them. Its `children` are the claimable child view (`_child_view`), as today.
- **Covered parent**: a standalone parent whose id is in `claimed` as the loader computes it today
  (`claimed_entry_ids(exclude_statement_id=this)` ∪ `exclude`), i.e. active coverage on another statement, or owner /
  agent / policy coverage on this one. (This statement's own `auto` rows never count — `_write_coverage` rewrites
  them each run.)
- **Carrier**: the `Entry` row the loader now builds for a covered parent that has at least one unclaimed child in the
  participating accounts and the statement currency, inside the loader's population window (the `lo`/`hi` bounds the
  `parents` query uses: `coalesce(posted_date, entry_date)` within `period ± window_days`, where `window_days =
  matching.population_window_days(rules)` = 15 today).
  `Entry.carrier = True`, `claimable = True`, `in_reverse = False`, `children` = those unclaimed children,
  `all_children` = (), `stand_in` as for any parent, `instance = None`. A carrier is never a split member
  (`group_id` None by construction) and never `hint_only`.
- **Principal paths**: every representation or case that puts the parent's own id in a `Row` or names it as an entry:
  `standalone`, `principal_children`, the installment paths, `_claimed_by_earlier_line`, `_foreign_case`,
  `_near_case`, `_drift_case`, and the reverse side. The **child path** is matching.py:617–625 (rep kind `child`,
  row role `principal`, rule `exact`).
- **Identical children** for a fee line: the child representations of that line whose children share one `kind`,
  one `flow` **and one parent `account_id`** (participating accounts can hold several accounts; a fee is
  interchangeable only inside one) and whose scores tie within the margin (the condition under which R6a.2 would
  report `ambiguous`).
- **Admissible** (child *c* of parent *e* for line *L*): the child path's own predicate — *c* unclaimed, `c.kind` in
  *L*'s gate, `_same(c.flow, L.flow)`, and `_date_distance(L, e) ≤ exact_window_days` (so the child path yields a rep
  with a positive score).
- **Cohort** of a fee-gate line *L* at the moment the loop decides it: the connected component containing *L* in the
  bipartite graph whose right nodes are the unclaimed children with *L*'s identical `(kind, flow, account_id)`,
  whose left nodes — the **slots** — are this statement's unclaimed lines with `line_kind == L.line_kind` and
  `flow` `_same` as *L*'s **whose current representations are non-empty and all `child` reps of right-node children**
  (a line that also reads a standalone, a group or a child of another ident is neither a slot nor a connector: it will
  take, or weigh, something outside the cohort — AGENT-271 F1), and whose edges are the admissible pairs. The cohort is **complete** when every left
  node is admissible for every right node, **balanced** when the two sides have the same size, and **undecided**
  when no left node has already been left as a case — the loop decides lines in id order, so: every left node has
  `id ≥ L.id` (AGENT-270 finding 1). A decided line in the component means the component was unpairable when the
  loop reached it, and it will not be revisited; it must neither balance a later line's cohort (the reviewer's
  example) nor be dropped so that the rest looks balanced (three lines, two children: lines 2 and 3 must not pair
  after line 1 went `ambiguous`).
- **Near child**: a child in the line's gate that no claim of this run names (not in `claimed` when the line is
  decided), same sign as the line, `|line.flow − c.flow| ≤ max(near_tolerance_abs, near_tolerance_pct · |line.flow|)`
  (the `_near_case` tolerance, taken of the line; a residual of 0 qualifies), with the parent's `_date_distance` to
  the line ≤ `near_window_days` (5). No merchant overlap required (fee lines rarely print one). So an exact child the
  child path could not claim because its parent is 4–5 days away is listed with residual `"0"`; an exact child the
  child path claimed is not listed.

## 4. Matcher (`matching.py`)

4.1 `Entry.carrier: bool = False`. `match()` keeps carriers in `entries` (they are claimable). **One list, not two:**
`_representations` keeps receiving the whole `entries` list and skips carriers in each principal branch
(`standalone`, `principal_children`, member/group and installment branches: `if e.carrier: continue`); the child path
(:617–625) iterates every entry, carriers included. Consequences the one-list shape buys: `by_id` at :658 contains
the carriers, so `_is_stand_in_rep` still resolves a carrier's child row to its parent (a stand-in carrier's child rep
is a stand-in rep); and `_claimed_by_earlier_line` (:841–846), which re-runs `_representations` with `claimed = ∅`,
names a carrier's child taken by an earlier line exactly as it names an auto parent's child — the owner sees the same
`claimed_by_earlier_line {entry_id: <child>}` hint for the same duplicate-fee-line shape whoever covered the parent.
The fallbacks that build cases naming an entry — `_foreign_case`, `_near_case`, `_drift_case`, `_installment_case`
(and its `entries + hint_only` argument) — receive `principals = [e for e in entries if not e.carrier]`, computed
once in `match()`. The near-child annotation (§4.3) reads all `entries`. `statement_context`, `incomplete_groups`,
`printed_alone`, `group_readings`, `claim_holders`, `_related_ids`: unchanged (a carrier is not a split member, so no
guard path visits it; `claim_holders` resolves child rows through `by_row` over `entries ∪ children`, which now
contains the carriers' children — nothing to add).

4.2 **Identical-children pairing** (Goal 2). In the claim loop, **before** the margin accept, when every
representation of a fee-gate line (`line.line_kind ∈ BANK_ONLY_KINDS`) is a `child` rep of one ident (§3 "Identical
children" without the tie condition) build the line's **cohort** (§3). If the cohort holds ≥ 2 slots **and** ≥ 2
children and is **not** complete, balanced and undecided → `ambiguous` (candidates = the reps), even when the margin
would have accepted one child (AGENT-271 F2: an untied greedy take can strand a sibling line's only child); a cohort
with one slot or one child (nothing can be stranded — a leftover line is not a strand) keeps today's accept. If the cohort is complete, balanced and undecided and the reps are untied
(≥ margin) → the margin winner claims by today's rule (`exact`). If they are tied, claim this line with the child whose parent's `_date_distance` to the line is smallest,
ties by lowest child id (`rule = "exact_pair"`, rep kind `child`, row role `principal`); the child enters `claimed`.
Otherwise today's `ambiguous` case, unchanged (candidates list the children). Only a *tied* line gets `exact_pair`: once a
pair's first line has taken its child, the second line has a single rep (and a one-child cohort) and the ordinary
accept branch claims it with `rule = "exact"` — so a pair of two lines reads `exact_pair`, `exact` (AGENT-270
finding 2), and `exact_pair` appears exactly once per tie the rule breaks. A line whose single rep is a child of an
ident shared with a still-undecided sibling line is gated too: when that sibling is a decided `ambiguous` line the
undecided veto makes it `ambiguous` as well (F2 shape, test 5b/5e).

Why complete *and* balanced, not counts alone (AGENT-267 finding 1): with only equal counts a greedy choice can strand
a later line — lines dated Sep 4 and Sep 8, parents dated Sep 3 and Sep 5: line 1 ties, takes the lower id (parent
Sep 5), line 2's only admissible child was that one, the other parent is 5 days away — although the assignment
1→(Sep 3), 2→(Sep 5) existed. In a **complete** cohort every remaining child is admissible for every remaining line,
so whatever a line takes (by this rule, or by an untied score when merchant text favours one child) the remaining
lines can always be completed; **balanced** means no child or line is left over. Completeness also closes the
reviewer's second gap: a child admissible only for a *later* line is in the component (the graph is built over all
undecided same-flow lines), so it unbalances or breaks completeness and the cohort stays `ambiguous`. The cohort is
rebuilt per line from the current `claimed` set and the unclaimed lines, so claims made earlier in the loop
(a text-favoured child, a non-tied line) are already removed. Lines the loop already left as a case stay in the graph
but veto the pairing (**undecided**, §3): they will not be revisited, so they must not make a later line's cohort
balanced (AGENT-270 finding 1: fee lines Sep 2 and Sep 5, two identical children under Sep 3 parents and a standalone
fee entry Aug 31 — the Sep 2 line has three reps (not identical children) and stays `ambiguous`; counting it would
have let the Sep 5 line pair as a "complete 2 × 2" and strand a child; with the veto the Sep 5 line is `ambiguous`
too), and leaving them out would be the mirror error (three lines, two children: line 1 `ambiguous`, lines 2 and 3
would pair). Rule in one line: a child-only fee line in a cohort of ≥ 2 slots and ≥ 2 children claims iff its cohort is complete ∧ balanced ∧
`min(slot ids) == L.id` (tied → `exact_pair`, untied → `exact`); otherwise `ambiguous`. AGENT-271 F1: a sibling line
whose best rep is a standalone at distance 1 is not a slot — counting it let line 1 pair and line 2 take the
standalone, stranding a carrier child nobody reports (§4.4). AGENT-271 F2: lines Sep 5 and Sep 8, children under
parents Sep 5 and Sep 2 — the untied margin accept took the nearer child for line 1 and left line 2 without a child
although the swapped assignment was perfect; the gate now runs before the accept, so both lines are `ambiguous` (the
owner sees two lines, two children, no perfect greedy order). Rejected: computing a perfect matching (a bipartite
assignment on tiny graphs) — correct but a new rule; §8.5. Why pairing inside such a cohort is safe: the children
share account, kind and flow and every one of them could legitimately print as any of the lines; the bank's fee line
names no purchase, so the ledger's attribution (the child under its parent) is untouched and only the
statement-line ↔ child link is a canonical choice among equivalents. When the cohort is unbalanced there is a child
or a line too many, which the owner must see — e.g. the duplicate child 58248 under 57454 (504/4245).

Owner option (§8.2): the same mechanism with a stricter tie-break — pair only when the nearest parent date is
**unique** for the line, leaving same-day ties `ambiguous` — is a one-line variant; the default here pairs same-day
ties by id.

4.3 **Near-child annotation** (Goal 3): just before `_bank_only` (:833–838) is emitted for a line, collect the near
children (§3) over all `entries` (carriers included) in the line's gate; if any, add
`"near_children": [{"entry_id": c.id, "parent_entry_id": e.id, "residual": str(line.flow − c.flow)}, …]` to the
`bank_only` context, sorted by `|residual|` then `entry_id`, at most `NEAR_CHILDREN_MAX = 5`. The key is present only
when the list is non-empty, so every existing `bank_only` context is byte-identical. Hint stays `bank_only`:
`_system_entry_candidate` is unchanged and the twin guard already refuses these lines; the annotation is for the
owner (API `context`, inbox) and for the follow-up that may act on it.

4.4 Reverse side: carriers are `in_reverse = False`, so neither they nor their unclaimed children are reported as
`entry_unmatched` / `uncovered_child` by this statement (the parent's own statement, or the owner, already accounts
for the parent; a stray child is visible through §4.3 when a fee line is near it). Today these children are reported
nowhere either, so no case disappears.

4.5 Stand-in drop (`_is_stand_in_rep`), scoring (`_score`, parent's dates and text), gates, tolerances, windows:
unchanged. A carrier that is a stand-in (`stand_in = True`) makes its child's rep a stand-in rep as today.

## 5. Loader and service (`reconciliation_service.py`)

5.1 `load_candidates`: after `parents` (unchanged), select the **covered standalone parents** — same `where` as
`parents` (accounts, currency, `parent_entry_id IS NULL`, window, `kind.not_in(kinds_out)`) restricted to
`LedgerEntry.id.in_(claimed)` and `group_id IS NULL`; then drop every one that carries an **installment** instance
(`_instances(db, ids)` as the loader already does for `parents` and for the structural rows — a recurring instance
stays; AGENT-267 finding 2: `group_id IS NULL` does not imply `_standalone`) — and load their children with today's
child query (participating accounts, statement currency, `c.id not in claimed`). For each surviving parent with ≥ 1
unclaimed child, append
`entry_view(e, children=kids, all_children=(), instance=None, in_reverse=False, claimable=True, carrier=True,
stand_in=e.id in stand_in_ids, transfer_peer_is_card=None)`. The `claimed` set can be large; query it as
`id IN (...)` chunked by 200 like the other id lists, or as a join on `statement_coverage` active rows — builder's
choice, one extra query per load. Order carriers after the drifted instances and before the structural rows so the
`entries` list order (and so claim order within equal scores) is stable: parents, drifted instances, carriers,
structural rows.

5.2 `reconcile()` lock set (:942–951): a carrier's **children are locked, the carrier itself is not** — the parent
row belongs to another statement's active claim or to the owner's coverage, and taking `FOR UPDATE NOWAIT` on it would
add a new `reconcile_busy` path between statements (shape 6: N+1 locking N's claimed purchase) for a row this run
never writes. So `wanted = {e.id for e in entries if e.claimable and not e.carrier} | {c.id for e in entries if
e.claimable for c in e.children}`; `transfers` likewise skip carriers. The survivor filter keeps a carrier without
requiring its own id in `locked_rows` (`… if not e.claimable or e.carrier or (e.id in locked_rows and …)`), and
`_with_children` trims its children to the locked ones as for any claimable row; a carrier left with no locked child
is dropped from the population (nothing to claim) — `_with_children` itself returns the trimmed `Entry`, so the drop
is an explicit second filter after the trim (`not (e.carrier and not e.children)`; AGENT-270 finding 3), and the
survivor logic moves into a pure helper `_survivors(entries, locked_rows, first_groups)` so it can be unit-tested
with a carrier whose only child joined after the lock. `first_groups` is moot for carriers (`group_id` None).
`_write_coverage` / `write_claim` unchanged: a child-alone claim writes `entry_id = child`, `role = "principal"`,
`match_rule = "exact"` or `"exact_pair"`, as today's child path does; `entries_by_id = locked` holds the child.

5.3 `entry_view` already passes `**extra` through, so `carrier=True` needs only the dataclass field. `_desired_cases`
unchanged: a case's identity `_key` (reconciliation_service.py:554–558) includes the context (minus the sweep note),
so the `near_children` annotation supersedes and re-creates an existing `bank_only` case **once**, on the first run
after deploy; one live line today (554/4164). Subsequent runs are stable (the annotation is deterministic: sorted,
capped).

5.4 Policy validator (`_single_admissible`): same loader, same `match`, so the validator sees the carrier's child
exactly as reconcile does. The observable parity (AGENT-270 finding 5): when a carrier child and a standalone owner
twin tie on a fee line (same date, same flow, no text), reconcile leaves an `ambiguous` case naming both, and an
agent `match` proposal for the twin is refused `not_single_admissible` with **two** admissible readings — before this
change the validator (like reconcile) saw only the twin. When the child outranks the twin (nearer parent, margin
met) or is alone, reconcile claims it and there is no case for an agent to act on; a proposal naming an entry that
is not in the case's candidates is refused by `_in_candidates` regardless. `_system_entry_candidate`: unchanged.

5.5 `stand_ins.owner_children`: unchanged. Once a child is claimed it **enters** `claimed_entry_ids` and so leaves
the twin population (stand_ins.py:142–156); while a near child exists the twin guard still refuses
`create_system_entry` — the intended fail-closed.

## 6. Worked shapes (production, 2026-10-24; ids and dates only)

| # | statement / line | child (parent, parent coverage) | today | after |
|---|---|---|---|---|
| 1 | 555 / 5784, 5786 (09-16) | 57706 (57705, manual 555/5785), 57708 (57707, manual 555/5787), same day, same flow | bank_only ×2 | carriers → two identical children, two identical lines → §4.2 pairs 5784←57706 (`exact_pair`), then 5786←57708 is the single rep (`exact`) |
| 2 | 504 / 4244 (07-22) | 57326 (57325, manual 504/4260), 07-23, exact | bank_only | carrier → child path claims (`exact`) |
| 3 | 504 / 4245 (08-02) | 57455 and 58248 (both under 57454, manual 504/4267), identical | bank_only | carrier → two children, one line → `ambiguous` with both candidates (owner removes the duplicate) |
| 4 | 504 / 4242 (07-20), 4243 (07-21) | 57288 (57287 auto 504/4258, 07-20), 57306 (57305 auto 504/4259, 07-21), identical flows | ambiguous ×2 (dismissed 10-17) | §4.2: two lines, two children → 4242←57288 (`exact_pair`, nearest parent date), then 4243←57306 single rep (`exact`) |
| 5 | 554 / 4164 (06-08) | 58223 (56977 auto 554/4156), same day, flow differs | bank_only | bank_only + `near_children: [{58223, 56977, residual}]`; owner corrects the child's amount, next run claims |
| 6 | parent on statement N, fee line on N+1 (none live today; historical shapes exist) | child unclaimed, parent claimed on N | bank_only on N+1 | carrier on N+1 (parent inside N+1's 15-day population window) → child path claims when the parent's date is within 3 days of the fee line |

Shape 2 data check (2026-10-24 SQL over active coverage and current lines): 4244's exact child is 57326 (parent
57325, owner-matched to 504/4260); the auto parents' children 57288/57306 are near 4244 but exact for 4242/4243
(shape 4). After the change 4244 has one `child` rep (57326) and no tie — shape 2, not a `near_children` line.

Not covered: a fee printed more than `exact_window_days` after the purchase (child path window is the parent's
date) — stays `bank_only`, possibly with a `near_children` pointer when within 5 days. See §8.

## 7. Tests

Unit (`tests/unit/test_statement_matching.py`, helpers `L/E/S/run`; add a `C(...)`/`carrier=` convenience as needed):

1. Carrier child claims the fee line: `E(1, …, carrier=True, children=(Child(11, "fee", -5),))`, line −5 `fee` →
   one claim, rows `[(11, "principal")]`, rule `exact`; the carrier's id appears in no row; `unmatched_entry_ids`
   empty (not in reverse).
2. Carrier never a principal: a purchase line equal to the carrier's flow → no claim, plain `line_unmatched`
   (not `claimed_by_earlier_line`, not `near`); a line equal to carrier + child → no `principal_children`.
   2b. Consistent hint: two identical fee lines, one carrier child → the first line claims it, the second gets
   `line_unmatched {"hint": "claimed_by_earlier_line", "entry_id": <child>}` — the same as with an auto parent
   (assert both variants in one test).
3. Carrier invisible to fallbacks: a purchase line within the near tolerance of the carrier's flow → no
   `amount_delta` naming the carrier; drift likewise.
4. Identical pairing, complete and balanced: two −5 fee lines (days 1, 2), two carriers (or one auto parent with
   two fee children, or two auto parents) with −5 fee children dated days 1 and 2 → 4242/4243 shape: each line
   claims the child whose parent is nearest; same-day tie → lowest child id; the first line's rule is `exact_pair`,
   the second line's `exact` (single rep left); second `match()` identical.
5. Unbalanced: one −5 fee line, two −5 children → `ambiguous`, candidates both, no claim (504/4245). Three lines,
   two children → all three `ambiguous`; pin it.
   5b. Incomplete (AGENT-267 counterexample): lines dated Sep 4 and Sep 8, children whose parents are dated Sep 3
   and Sep 5 → the Sep 8 line is admissible for only one child → cohort incomplete → the Sep 4 line is `ambiguous`
   (not claimed), nothing is `exact_pair`; the Sep 8 line's single rep is a child of the same ident in a cohort whose
   other slot (Sep 4) is already decided → `ambiguous` too (rev 5: the undecided veto applies to the single-rep line;
   before rev 5 it claimed `exact` and the other child was stranded).
   5c. Reachable only from a later line: lines A, B (same flow), children x, y admissible for both, child z
   admissible only for B → component {A, B} × {x, y, z} unbalanced → A and B `ambiguous`.
   5e. A sibling line with a rep outside the cohort is not a slot (AGENT-271 F1): lines Sep 16 and Sep 19, carrier
   children under Sep 16 parents, standalone fee Sep 20 → line 1 `ambiguous` {11, 21}, line 2 claims the standalone
   `exact`, no `exact_pair`.
   5f. The gate guards an untied take (AGENT-271 F2): lines Sep 5 and Sep 8, children under parents Sep 5 and Sep 2 →
   both lines `ambiguous`, no claim; the complete variant (parents Sep 5 and Sep 7, both lines admissible to both) →
   line 1 `exact` (margin winner), line 2 `exact`; one line, two untied children → nearest wins `exact` (single slot); two lines, one child → first line `exact`, second `claimed_by_earlier_line` (single child).
   5d. Decided lines never balance a cohort (AGENT-270 finding 1): fee lines A (Sep 2) and B (Sep 5), identical
   children x, y under parents dated Sep 3, a standalone fee entry s dated Aug 31 → A has three reps (x, y, s) →
   `ambiguous`; B's cohort is {A, B} × {x, y}, complete and balanced but A is decided → `ambiguous`; no claim, no
   `exact_pair`. (And test 5's three-lines shape pins the mirror: lines 2 and 3 never pair after line 1.)
6. Pairing is scoped to the tie: when one child's parent merchant text matches the line (score differs beyond the
   margin), the text winner claims by today's rule (`exact`); the other line's cohort is then one line, one child →
   `exact_pair` (or `exact` if it is the only rep).
7. Pairing never crosses kinds, flows or accounts: −5 fee children vs a −5 `interest` line; −5 and −6 children →
   not identical; two −5 fee children whose parents sit on two different participating accounts → not identical →
   `ambiguous`.
8. Near child annotation: fee line −6, carrier child −5 (within `max(10, 5 %)`), parent 1 day away → `bank_only`
   with `near_children: [{entry_id, parent_entry_id, residual "-1"}]`; an exact child whose parent is 4 days away
   (outside the 3-day exact window, inside the 5-day near window) appears with residual `"0"` and the line is
   **not** claimed; a child 6 days away does not appear; an exact child the child path claimed is not listed; more
   than 5 → first 5 by `|residual|` then id. A fee line with no near child → context exactly
   `{"hint": "bank_only", "pattern": None}` (existing tests stay).
9. Reverse side: carrier with an unclaimed child and no fee line → `unmatched_entry_ids` empty, no
   `uncovered_child`.
10. Stand-in drop: a stand-in carrier's child rep loses to an owner rep as today.

Integration (`tests/integration/test_reconciliation_service.py`, fixture `card_month`, `seed.entry(…,
parent_entry_id=)`, `_owner_cover`-style manual coverage via the `match` action or `write_claim`):

11. Owner matches the parent (`match`, `with_children=False`) to the purchase line, fee line open as `bank_only`;
    `reconcile` → the child claims the fee line (`auto`, role `principal`, rule `exact`), the manual row kept, the
    `bank_only` case resolved; second `reconcile` idempotent (`_claim_unchanged` keeps the child row).
12. Parent auto-claimed on statement N (period 1), fee line only on statement N+1 whose period starts within 3
    days of the parent → N+1's reconcile claims the child (carrier from `claimed` on another statement).
13. Shape 1 end-to-end: two manual parents, two identical fee children, two identical fee lines → both claimed
    (`exact_pair`, then `exact`); coverage conserved; idempotent. Pre-state for 11–13: the statement is submitted
    **before** the parents and children exist (the revision hook then finds nothing), the owner covers the purchase
    lines by hand, and the fee lines are open `bank_only` when the reconcile under test runs (AGENT-270 finding 4).
14. Shape 3: one line, two identical children → `ambiguous` with two candidates; no coverage.
15. Shape 5: near child → open `line_unmatched` with `hint bank_only` and `near_children` naming the child; owner
    edits the child's amount to the bank's → next `reconcile` claims it and the case resolves.
16. `load_candidates` unit-ish: a covered standalone parent with one unclaimed child yields a carrier row
    (`carrier=True`, `claimable=True`, `in_reverse=False`, children = that child); a covered parent whose children
    are all claimed yields none; a covered **split member** yields no carrier (structural row as today); a covered
    parent outside the 15-day window yields none; a covered ungrouped parent that is an **installment instance**
    yields none (AGENT-267 finding 2), while a covered **recurring** instance with an unclaimed fee child does; a
    child in another currency or on a non-participating account is not a carrier child (child query filters).
16c. `_survivors`: a carrier whose only child is not in `locked_rows` is dropped; a carrier with one locked child of
    two keeps that one; non-carrier rows behave as today (AGENT-270 finding 3).
16b. Carrier not locked (if the harness allows a second session; `tests/conftest.py` `SessionLocal`): hold
    `SELECT … FOR UPDATE` on the covered parent in another session; `reconcile` still claims the child (the parent is
    not in the lock set) — and, as the negative, holding the lock on the **child** makes `reconcile` raise the
    existing `reconcile_busy` path.

Policy (`tests/integration/test_reconciliation_policy.py`):

17. Validator parity (§5.4): (a) owner-covered parent with a fee child and a standalone owner twin, both the same
    day as the fee line, no text → reconcile leaves `ambiguous` naming both; the agent's `match` proposal for the
    twin is refused `not_single_admissible` with two readings. (b) Child only (no twin) → reconcile claims the child
    `exact`, no open case on the line, `collect_candidates` yields nothing for it. (c) Near child only (child −14,
    line −15) → `bank_only` + `near_children`, and `collect_candidates` yields no `create_system_entry` for the case
    (twin guard: the child is an owner twin) — asserted on the list, not via `_only_candidate` (which unpacks one).

Property check (host, not in the suite): the existing `fuzz_holders.py` style — random carriers/children/fee lines;
(a) a carrier id never appears in a claim row; (b) `exact_pair` claims only inside a complete, balanced, undecided cohort, and every child of that cohort is claimed by the end of the run; (c) two
runs give identical claims.

## 8. Open points for the review

1. Split members' children when the member is owner-covered (non-goal here; fail-closed). Worth a follow-up?
2. The pairing rule is deliberately strict (complete and balanced cohort only). Rejected: greedy pairing whenever
   children ≥ lines (hides a duplicate child, 504/4245); counts without completeness (AGENT-267 finding 1). Owner's
   open choice: keep same-day ties paired by id (default), or require a unique nearest date (then 555's same-day pair
   stays `ambiguous`; 4242/4243 still pair). The ledger attribution is unaffected either way (§4.2).
3. Child dates: the child path and the near-child annotation use the parent's date. Fees the bank posts days after
   the purchase stay open. Giving `Child` its own dates is a separate change (stand-ins already date children by
   their own rows).
4. Whether a child-level resolution (owner `match` admitting a child; `adjust_amount` on a child) should follow —
   today the owner's remedy for shape 5 is to edit the child's amount, which the annotation now points at.
5. Perfect-matching assignment for incomplete cohorts (AGENT-271 F2 shape has a unique perfect assignment) — left
   `ambiguous` here; a follow-up once child-level resolution exists.

## 9. Rollout

No migration. Deploy the accounting API; re-reconcile 504, 554, 555 and compare active coverage before/after (expect
5784 (`exact_pair`), 5786 (`exact`), 4244 (`exact`), 4242 (`exact_pair`), 4243 (`exact`) claimed by children; 4245 `ambiguous`; 4164 `bank_only` + `near_children`, re-created
once per §5.3); open cases expected 151 → ≤ 146 (−3 `line_unmatched` 5784/5786/4244, −2 `entry_unmatched`
57288/57306; 4245 stays open as `ambiguous`, 4164 stays open). Record in `~/reports/`. Owner to-dos surfaced: remove
the duplicate child 58248; correct the fee child 58223.

## 10. Review log

Rev 1 → rev 2 (builder check, 2026-10-24): near-child predicate made consistent (residual 0 allowed, "not claimed"
instead of "not `_same`"); one-list carrier shape fixed instead of builder's choice (`_claimed_by_earlier_line` and
`_is_stand_in_rep` consistency); carriers lock children only (§5.2); population window is `population_window_days`
(15), not `candidate_window_days`; 504/4244 confirmed as shape 2 by data; `_key` includes the context (§5.3);
`_bank_only` line reference corrected; tests 2b, 16 (window), 16b added.

Rev 2 → rev 3 (Multica AGENT-267, dev-astra FAIL, 2026-10-24): **finding 1 (Major)** — pairing now requires a
*complete and balanced cohort* (§3 "Admissible", "Cohort"; §4.2 rewritten with the reviewer's Sep 4/8 counterexample
and the later-line child), account equality made an explicit predicate of "identical"; tests 5b, 5c, 7 (accounts)
added. **Finding 2 (Medium)** — carriers exclude installment instances via `_instances`, recurring kept (§3, §5.1;
test 16). **(d)** §5.4 reworded: after the claim exists the validator sees the child as claimed, refusal holds either
way. **(e)** §5.5: a claimed child *enters* `claimed_entry_ids`.

Rev 3 → rev 4 (Multica AGENT-270 plan review, dev-astra FAIL, 2026-10-24): **finding 1 (Major)** — cohort left nodes
stay the unclaimed same-kind lines, and a cohort pairs only when it is also *undecided* — no left node already left
as a case (§3, §4.2 example and the three-lines mirror; test 5d). **Finding 2** — a pair reads `exact_pair`, `exact` (§4.2, §6 rows 1/4, §7 tests 4/13,
§9). **Finding 3** — explicit drop of a carrier with no locked child via `_survivors` (§5.2; test 16c). **Finding 4** —
pre-state for tests 11–13 (statement submitted before the entries exist). **Finding 5** — §5.4 restated as the
observable parity (ambiguous child-vs-twin → validator sees two readings); test 17 split into (a)/(b)/(c), zero
candidates asserted on `collect_candidates`.

Rev 4 → execution errata (2026-10-24): `_near_children(statement_kind, line, entries, claimed, rules)` takes the
statement kind first (`eligible_kinds` needs it); the `_exact_pair` pick key is `(parent.stand_in, _date_distance,
child id)` — owner children before stand-ins (R6b.0 consistency; the §4.2 tie-break is amended accordingly); carriers
are also excluded from `by_id`/`evaluated` in `reconcile()`, so a parent's `deferred_next_period` case is never
superseded by its carrier (§5.2 addendum); `_carriers` selects from the `parents` rows already read, so the extra
queries per load are the child query, then the instance lookup on the parents that have children (§5.1);
`_survivors(entries, locked_rows, first_groups)` is the pure survivor helper (§5.2); residuals in `near_children` are
`Q`-quantized strings (`"0.0000"`, `"-1.0000"`) like `_near_case`, where §3/§7 write `"0"`/`"-1"` for brevity.

Rev 4 → rev 5 (Multica AGENT-271 re-check, dev-grok FAIL, 2026-10-24): **F1 (Major)** — cohort left nodes are *slots*:
lines whose current reps are all child reps of the cohort's ident; a line with any other rep is neither slot nor
connector (§3, §4.2; test 5e). **F2 (Major)** — the cohort gate runs before the margin accept for child-only fee
lines with ≥ 2 slots; untied inside a passing cohort → `exact`, failing cohort → `ambiguous`; single-slot cohorts keep
today's accept (§4.2; tests 5f, 5b updated). **F3 (Minor)** — plan rollout property (b) wording. Q2 confirmed (three
identical lines, two children → all `ambiguous`). §8.5 added.
