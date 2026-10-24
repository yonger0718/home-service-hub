# Fee child claims the fee line (#143) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `kind=fee` child whose standalone parent is already covered (owner / agent / policy / another statement) can
still claim the statement's fee line; identical fee children pair with identical fee lines only inside a complete,
balanced cohort; a `bank_only` fee line names its near children.

**Architecture:** Three matcher changes in `app/services/statements/matching.py` (carrier rows skipped by every
principal path, a cohort-based `exact_pair` rule at the R6a.2 ambiguity point, a `near_children` annotation before
`_bank_only`) and one loader/lock change in `app/services/reconciliation_service.py` (`load_candidates` builds carrier
rows for covered standalone parents with unclaimed children; `reconcile()` locks their children, never the carrier).
The policy validator shares the loader and `match`, so parity needs tests only.

**Tech Stack:** Python 3.13, FastAPI service `services/accounting-service`, SQLAlchemy, pytest (unit + Postgres
integration). Plan rev 5, 2026-10-24 (AGENT-270 + AGENT-271 folded; Task 2b = cohort slots, untied gate, forced take, lazy slots — re-review 2 folded).

**Spec:** `docs/superpowers/specs/2026-10-24-reconciliation-fee-child-design.md` rev 7 (on this branch). Context specs:
`2026-10-24-reconciliation-split-members-design.md`, `2026-10-24-reconciliation-guard-holders-design.md`.

## Global Constraints

- Worktree `/home/opc/workspace/home-hub-feechild`, branch `feat/reconciliation-fee-child`; never touch `main`.
- Test command (from `services/accounting-service`): `PYTHONPATH=../shared-python-lib .venv/bin/python -m pytest <paths> -q -p no:cacheprovider` — never `-I`. Integration tests need the Postgres container; run only the files you touch per task (the full integration suite takes ~12 min; run it once in the final task).
- No schema change, no migration, no new dependency, no settings change.
- Spec invariants (anti-goals §2): a carrier id never appears in a claim row; a near child is never claimed; the pairing rule fires only for a complete **and** balanced cohort; no change to purchase-line readings, the printed-whole guard, holders, the policy or the twin guard.
- Existing `bank_only` contexts stay byte-identical when there is no near child (`near_children` key present only when non-empty).
- Names fixed by this plan: `Entry.carrier`, `principals`, `_exact_pair`, `_cohort`, `_one_ident`, `_tied`, `"exact_pair"` (match rule), `_near_children`, `NEAR_CHILDREN_MAX = 5`, `_carriers`, `_survivors`.
- A pair of two tied fee lines reads `exact_pair` (first line) then `exact` (second line: single rep, ordinary accept branch). `exact_pair` appears once per tie.
- Commits end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Never print `.env` values; tests use seeded fixtures only.

## Review Focus

1. A covered parent whose only child is in another currency or another account: no carrier (the child query filters both) — Task 4 test 16.
2. A carrier's child claimed by this statement's own `auto` row on the previous run: reloaded (own rows are not "claimed") and kept by `_claim_unchanged` — Task 4 test 11 (second reconcile identical).
3. A stand-in carrier (parent booked by `attach_fee`/backfill): its child rep is a stand-in rep and loses to an owner rep — Task 1 test 10.
4. The validator and reconcile disagreeing on a fee line once carriers exist (an agent `match` proposal for a standalone twin of the child) — Task 5 test 17.
5. Two identical children of one parent on one line (duplicate child): must stay `ambiguous`, never silently take the lower id — Task 2 test 5.

---

### Task 1: Carrier rows in the matcher

**Files:**
- Modify: `services/accounting-service/app/services/statements/matching.py` (`Entry` ~:65–94; `_representations` ~:582–625; `match()` ~:865–917)
- Test: `services/accounting-service/tests/unit/test_statement_matching.py`

**Interfaces:**
- Produces: `matching.Entry.carrier: bool = False` (dataclass field, after `source`). In `match()`: `principals = [e for e in entries if not e.carrier]` passed to `_installment_case` (as `principals + hint_only`), `_foreign_case`, `_near_case`, `_drift_case`; `_representations` and `_claimed_by_earlier_line` keep receiving `entries`. In `_representations`, the principal loop (`for e in entries:` before `principal = (Row(e.id, "principal", e.flow),)`) starts with `if e.carrier: continue`; the child path loop is untouched (visits carriers).

- [ ] **Step 1: Write the failing tests** (unit helpers `L`, `E`, `run`, `m.Child`; a carrier is `E(..., carrier=True, in_reverse=False, children=(...))` — add `carrier=kw.get("carrier", False)` to `E`):

```python
def test_carrier_child_claims_the_fee_line_and_the_carrier_never_appears():
    carrier = E(20, date(2026, 9, 16), "-1000", carrier=True, in_reverse=False, children=(m.Child(21, "fee", D("-15")),))
    res = run([L(1, date(2026, 9, 16), "-15", kind="fee", merchant="")], [carrier])
    assert [(c.line_id, c.representation.kind, c.representation.rule, [(r.entry_id, r.role) for r in c.representation.rows]) for c in res.claims] == [(1, "child", "exact", [(21, "principal")])]
    assert res.cases == [] and res.unmatched_entry_ids == []

def test_carrier_is_never_a_principal():
    carrier = E(20, date(2026, 9, 16), "-1000", carrier=True, in_reverse=False, children=(m.Child(21, "fee", D("-15")),))
    res = run([L(1, date(2026, 9, 16), "-1000", merchant=""), L(2, date(2026, 9, 16), "-1015", merchant="")], [carrier])
    assert res.claims == [] and [(c.kind, c.context) for c in res.cases] == [("line_unmatched", {}), ("line_unmatched", {})]

def test_carrier_is_invisible_to_near_and_drift_fallbacks():
    carrier = E(20, date(2026, 9, 16), "-1000", carrier=True, in_reverse=False, children=(m.Child(21, "fee", D("-15")),))
    res = run([L(1, date(2026, 9, 16), "-1004", merchant="")], [carrier])          # within max(10, 5 %) → would be near
    assert res.claims == [] and res.cases[0].kind == "line_unmatched" and res.cases[0].context == {}
    res = run([L(1, date(2026, 9, 26), "-1000", merchant="")], [carrier])          # 10 days → would be drift
    assert res.cases[0].kind == "line_unmatched" and res.cases[0].context == {}

@pytest.mark.parametrize("carrier", [True, False])
def test_second_identical_fee_line_gets_the_same_claimed_by_earlier_line_hint(carrier):
    parent = E(20, date(2026, 9, 16), "-1000", carrier=carrier, in_reverse=not carrier, children=(m.Child(21, "fee", D("-15")),))
    lines = [L(1, date(2026, 9, 16), "-15", kind="fee", merchant=""), L(2, date(2026, 9, 16), "-15", kind="fee", merchant="")]
    res = run(lines + ([] if carrier else [L(3, date(2026, 9, 16), "-1000", merchant="")]), [parent])
    assert [c.line_id for c in res.claims if c.representation.kind == "child"] == [1]
    second = next(c for c in res.cases if c.line_id == 2)
    assert second.kind == "line_unmatched" and second.context == {"hint": "claimed_by_earlier_line", "entry_id": 21}

def test_carrier_and_its_unclaimed_child_are_not_in_the_reverse_side():
    carrier = E(20, date(2026, 9, 16), "-1000", carrier=True, in_reverse=False, children=(m.Child(21, "fee", D("-15")),))
    res = run([L(1, date(2026, 9, 16), "-580", merchant="")], [carrier])
    assert res.unmatched_entry_ids == [] and res.explained == {}

def test_stand_in_carrier_child_rep_loses_to_an_owner_rep():
    carrier = E(20, date(2026, 9, 16), "-1000", carrier=True, in_reverse=False, stand_in=True, children=(m.Child(21, "fee", D("-15")),))
    owner = E(30, date(2026, 9, 16), "-15", kind="fee", merchant="")
    res = run([L(1, date(2026, 9, 16), "-15", kind="fee", merchant="")], [carrier, owner])
    assert [[r.entry_id for r in c.representation.rows] for c in res.claims] == [[30]]
```

- [ ] **Step 2: Run them, expect failures** — `TypeError: ... unexpected keyword argument 'carrier'`.
- [ ] **Step 3: Add `carrier: bool = False` to `Entry`; add the `if e.carrier: continue` guard at the top of the principal loop in `_representations`; in `match()` compute `principals` once after the `hint_only` filter and pass it to the four fallbacks** (the `_installment_case` call becomes `principals + hint_only`). Nothing else changes.
- [ ] **Step 4: Run `tests/unit/test_statement_matching.py`** — all pass (existing tests untouched).
- [ ] **Step 5: Commit** — `feat(matching): carrier rows — covered parents whose children stay claimable`.

### Task 2: Identical-children pairing (`exact_pair`)

**Files:**
- Modify: `services/accounting-service/app/services/statements/matching.py` (new helpers next to `_ambiguous` ~:675; hook in `match()` at the R6a.2 branch ~:896–898)
- Test: `services/accounting-service/tests/unit/test_statement_matching.py`

**Interfaces:**
- Consumes: Task 1 (`Entry.carrier`, carriers visible to the child path).
- Produces:
  - *(rev 1–2 text, superseded by Task 2b below — kept for the review log: `_identical_children` with the tie
    condition, a list-returning `_cohort` over all unclaimed same-flow lines, and the hook inside the R6a.2 branch.)*
  - `_exact_pair(line: Line, cohort: list[tuple[Entry, Child]], rules: Rules) -> Representation` — pick key
    `(parent.stand_in, _date_distance(line, parent), child.id)`, rule `"exact_pair"`, rep kind `child`, row role `principal`.

- [ ] **Step 1: Write the failing tests**:

```python
def _fee_parent(i, day, child_id, flow="-15", **kw):
    return E(i, day, "-1000", merchant="", children=(m.Child(child_id, "fee", D(flow)),), **kw)

def test_complete_balanced_cohort_pairs_by_nearest_parent_date_then_id():
    lines = [L(1, date(2026, 7, 20), "-15", kind="fee", merchant=""), L(2, date(2026, 7, 21), "-15", kind="fee", merchant="")]
    res = run(lines, [_fee_parent(10, date(2026, 7, 20), 11), _fee_parent(20, date(2026, 7, 21), 21)])
    assert sorted((c.line_id, c.representation.rows[0].entry_id, c.representation.rule) for c in res.claims) == [(1, 11, "exact_pair"), (2, 21, "exact")]
    same_day = run([L(1, date(2026, 9, 16), "-15", kind="fee", merchant=""), L(2, date(2026, 9, 16), "-15", kind="fee", merchant="")],
                   [_fee_parent(10, date(2026, 9, 16), 11, carrier=True, in_reverse=False), _fee_parent(20, date(2026, 9, 16), 21, carrier=True, in_reverse=False)])
    assert sorted((c.line_id, c.representation.rows[0].entry_id, c.representation.rule) for c in same_day.claims) == [(1, 11, "exact_pair"), (2, 21, "exact")]
    assert run(lines, [_fee_parent(10, date(2026, 7, 20), 11), _fee_parent(20, date(2026, 7, 21), 21)]).claims == res.claims  # deterministic

def test_a_line_already_left_as_a_case_never_balances_a_later_cohort_agent_270():
    lines = [L(1, date(2026, 9, 2), "-15", kind="fee", merchant=""), L(2, date(2026, 9, 5), "-15", kind="fee", merchant="")]
    entries = [_fee_parent(10, date(2026, 9, 3), 11), _fee_parent(20, date(2026, 9, 3), 21), E(30, date(2026, 8, 31), "-15", kind="fee", merchant="")]
    res = run(lines, entries)
    assert res.claims == [] and [c.kind for c in res.cases if c.line_id in (1, 2)] == ["ambiguous", "ambiguous"]   # B's 2×2 is complete+balanced but A is decided
    assert {c["entry_id"] for c in next(c for c in res.cases if c.line_id == 1).candidates} == {11, 21, 30}
    assert {c["entry_id"] for c in next(c for c in res.cases if c.line_id == 2).candidates} == {11, 21}

def test_unbalanced_cohort_stays_ambiguous():
    one_line = run([L(1, date(2026, 8, 2), "-15", kind="fee", merchant="")],
                   [E(10, date(2026, 8, 2), "-1000", merchant="", carrier=True, in_reverse=False, children=(m.Child(11, "fee", D("-15")), m.Child(12, "fee", D("-15"))))])
    assert one_line.claims == [] and one_line.cases[0].kind == "ambiguous" and {c["entry_id"] for c in one_line.cases[0].candidates} == {11, 12}
    three_lines = run([L(i, date(2026, 9, 16), "-15", kind="fee", merchant="") for i in (1, 2, 3)],
                      [_fee_parent(10, date(2026, 9, 16), 11), _fee_parent(20, date(2026, 9, 16), 21)])
    assert three_lines.claims == [] and [c.kind for c in three_lines.cases] == ["ambiguous"] * 3   # mirror of AGENT-270 f1: lines 2+3 never pair after line 1

def test_incomplete_cohort_stays_ambiguous_agent_267_counterexample():
    lines = [L(1, date(2026, 9, 4), "-5", kind="fee", merchant=""), L(2, date(2026, 9, 8), "-5", kind="fee", merchant="")]
    res = run(lines, [_fee_parent(100, date(2026, 9, 5), 101, "-5"), _fee_parent(200, date(2026, 9, 3), 102, "-5")])
    first = next(c for c in res.cases if c.line_id == 1)
    assert first.kind == "ambiguous" and not any(c.representation.rule == "exact_pair" for c in res.claims)
    assert [(c.line_id, c.representation.rows[0].entry_id, c.representation.rule) for c in res.claims] == [(2, 101, "exact")]

def test_child_reachable_only_from_a_later_line_unbalances_the_cohort():
    lines = [L(1, date(2026, 9, 10), "-5", kind="fee", merchant=""), L(2, date(2026, 9, 13), "-5", kind="fee", merchant="")]
    res = run(lines, [_fee_parent(10, date(2026, 9, 11), 11, "-5"), _fee_parent(20, date(2026, 9, 12), 21, "-5"), _fee_parent(30, date(2026, 9, 15), 31, "-5")])
    assert res.claims == [] and sorted(c.line_id for c in res.cases if c.kind == "ambiguous") == [1, 2]

def test_text_winner_claims_exact_and_the_rest_pairs():
    lines = [L(1, date(2026, 9, 16), "-15", kind="fee", merchant="AMAZON 手續費"), L(2, date(2026, 9, 16), "-15", kind="fee", merchant="")]
    res = run(lines, [E(10, date(2026, 9, 16), "-1000", merchant="AMAZON", children=(m.Child(11, "fee", D("-15")),)), _fee_parent(20, date(2026, 9, 16), 21)])
    assert sorted((c.line_id, c.representation.rows[0].entry_id, c.representation.rule) for c in res.claims) == [(1, 11, "exact"), (2, 21, "exact")]

def test_pairing_never_crosses_kind_flow_or_account():
    interest = run([L(1, date(2026, 9, 16), "-15", kind="interest", merchant="")], [_fee_parent(10, date(2026, 9, 16), 11), _fee_parent(20, date(2026, 9, 16), 21)])
    assert interest.claims == [] and interest.cases[0].kind == "line_unmatched"      # fee children are not in the interest gate
    flows = run([L(1, date(2026, 9, 16), "-15", kind="fee", merchant=""), L(2, date(2026, 9, 16), "-16", kind="fee", merchant="")],
                [_fee_parent(10, date(2026, 9, 16), 11), _fee_parent(20, date(2026, 9, 16), 21, "-16")])
    assert sorted((c.line_id, c.representation.rule) for c in flows.claims) == [(1, "exact"), (2, "exact")]  # single reps, no pairing needed
    accounts = run([L(1, date(2026, 9, 16), "-15", kind="fee", merchant=""), L(2, date(2026, 9, 16), "-15", kind="fee", merchant="")],
                   [_fee_parent(10, date(2026, 9, 16), 11, account_id=1), _fee_parent(20, date(2026, 9, 16), 21, account_id=2)])
    assert accounts.claims == [] and [c.kind for c in accounts.cases] == ["ambiguous", "ambiguous"]
```

(In the last test's second shape the line 2 child is `-16`: one rep each, claimed `exact` — pins that pairing is not invoked when there is no tie. For the `interest` shape, `CARD_GATES["interest"]` does not admit `fee`; if it does in this codebase, switch the line kind to one whose gate excludes `fee` and keep the assertion.)

- [ ] **Step 2: Run, expect failures** (`exact_pair` never produced; counterexample shapes already pass as `ambiguous` — keep them, they pin the rule's limits).
- [ ] **Step 3: Implement `_identical_children`, `_cohort`, `_exact_pair` and the `match()` hook** per the Interfaces block. The component: BFS from `line` alternating unclaimed lines and unclaimed identical children over the admissible predicate; complete = `all(admissible(l, e, c) for l in left for (e, c) in right)`; balanced = `len(left) == len(right)`; undecided = `min(l.id for l in left) == line.id`.
- [ ] **Step 4: Run `tests/unit/test_statement_matching.py`** — all pass.
- [ ] **Step 5: Commit** — `feat(matching): exact_pair — identical fee children pair inside a complete balanced cohort`.

### Task 2b: Cohort slots, the untied gate, the forced take (spec rev 5 §3/§4.2 + rev 6)

**Files:**
- Modify: `app/services/statements/matching.py` (`_one_ident`, `_tied`, `_cohort`, the gate in `match()`)
- Test: `tests/unit/test_statement_matching.py`

**Interfaces:**
- Consumes: Task 1 `Entry.carrier`, Task 2 `_exact_pair`, `BANK_ONLY_KINDS`, `_representations(kind, ln, entries, groups, plan_map, claimed, rules, aside, apart)`.
- Produces:
  - `_one_ident(reps: list[Representation], entries: list[Entry]) -> tuple[str, Decimal, int] | None` — `(kind, flow, account_id)` when every rep is a `child` rep and the children share one kind, one `Q`-quantized flow and one parent `account_id`; else `None`. No tie condition.
  - `_tied(reps: list[Representation], rules: Rules) -> bool` — `len(reps) >= 2 and reps[0].score - reps[-1].score < rules.margin`.
  - `_cohort(kind, line, lines, entries, groups, plan_map, claimed, claimed_line_ids, ident, rules, aside=frozenset(), apart=frozenset()) -> tuple[list[tuple[Entry, Child]] | None, dict[int, Line], dict[int, tuple[Entry, Child]]]` — right pool = unclaimed children of `ident` in the line's gate; a **slot** = an unclaimed (`id not in claimed_line_ids`) same-`line_kind`, `_same`-flow line whose current reps (`_representations` with the live `claimed`, `aside`, `apart`) are non-empty and all `child` reps of the right pool; the slot test runs **lazily** inside the BFS (only for a line admissible to a just-reached child) and is memoised per call. BFS from `line` over admissible pairs (`_same(c.flow, ln.flow) and _date_distance(ln, e) <= rules.exact_window_days`). Returns `(children if complete and balanced and undecided else None, left, right)` with `undecided = min(left) == line.id`.
  - Gate in `match()`, **before** the margin accept and after the truncated-group check: `ident = _one_ident(reps, entries) if line.line_kind in BANK_ONLY_KINDS else None`; if `ident`: `cohort, slots, right = _cohort(...)`; if `len(slots) >= 2 and len(right) >= 2`: `cohort is None` → `_ambiguous(line, reps)`, `continue`, **unless** `len(reps) == 1 and degree == 1 and min(slots) == line.id` where `degree` = the line's admissible edges into `right` (forced take, rev 6/7 — fall through to the accept); `_tied(reps, rules) and reps[0].score >= rules.ambiguous_floor` → `_exact_pair`, claim, `continue`; untied → fall through (`exact`). One slot or one child → fall through.

- [ ] **Step 1: Tests** (spec §7 5e, 5f, 5g, 5h; `_fee_parent(i, day, child_id, flow="-15", **kw)` helper exists)

```python
def test_a_sibling_line_with_a_rep_outside_the_cohort_is_not_a_slot_agent_271_f1(): ...            # 5e: claims [(2, 30, "exact")]; line 1 ambiguous {11, 21}
def test_an_untied_child_take_that_would_strand_a_sibling_line_is_ambiguous_agent_271_f2(): ...  # 5f: no claims; lines 1, 2 ambiguous; line 2 candidates {11}
def test_an_untied_take_inside_a_complete_cohort_still_claims_exact(): ...                        # 5f: [(1, 11, "exact"), (2, 21, "exact")]
def test_one_line_two_untied_children_keeps_nearest_wins(): ...                                   # 5f: [(1, 11, "exact")]
def test_a_single_rep_lowest_slot_takes_its_child_in_an_incomplete_cohort_forced_first():         # 5g
    lines = [L(1, date(2026, 9, 2), "-15", kind="fee", merchant=""), L(2, date(2026, 9, 4), "-15", kind="fee", merchant="")]
    res = run(lines, [_fee_parent(10, date(2026, 9, 1), 11), _fee_parent(20, date(2026, 9, 6), 21)])
    assert sorted((c.line_id, c.representation.rows[0].entry_id, c.representation.rule) for c in res.claims) == [(1, 11, "exact"), (2, 21, "exact")]
    assert [c for c in res.cases if c.line_id in (1, 2)] == []
def test_a_sibling_reading_another_accounts_child_is_not_a_slot():                                 # 5h
    # lines Sep 16, Sep 19; children 11, 21 under Sep 16 parents (account 1); child 31 under a Sep 19 parent of account 2
    # → line 2 reads 11, 21 and 31 (two idents) → not a slot → line 1's component has one slot → gate off → line 1 ambiguous by the plain tie {11, 21}; line 2: 31 nearest, untied → `exact` 31
def test_a_sibling_reading_a_split_group_is_not_a_slot():                                          # 5h, group variant: line 2 also reads a fee group → not a slot
```

- [ ] **Step 2: Run, expect failure** (5g: both lines `ambiguous` today; 5h: write against the current code and confirm they pin the one-slot path)
- [ ] **Step 3: Implement** per Interfaces (lazy memoised slot test; forced-take exemption).
- [ ] **Step 4: Run** `PYTHONPATH=../shared-python-lib .venv/bin/python -m pytest tests/unit/test_statement_matching.py -q -p no:cacheprovider` → green. Timing check: 60 same-flow fee lines + 60 children + 300 unrelated entries, one `match()` < 1 s.
- [ ] **Step 5: Commit** `fix(matching): forced single-rep take in a gated cohort; lazy slot test`.

### Task 3: `near_children` annotation

**Files:**
- Modify: `services/accounting-service/app/services/statements/matching.py` (`_bank_only` ~:833; emission in `match()` ~:910–913)
- Test: `services/accounting-service/tests/unit/test_statement_matching.py`

**Interfaces:**
- Consumes: Task 1 (carriers in `entries`).
- Produces: `NEAR_CHILDREN_MAX = 5`; `_near_children(line: Line, entries: list[Entry], claimed: set[int], rules: Rules) -> list[dict]` — children of every entry (carriers included) with `c.id not in claimed`, `c.kind in gate`, same sign, `abs(line.flow - c.flow) <= max(rules.near_tolerance_abs, rules.near_tolerance_pct * abs(line.flow))`, `_date_distance(line, e) <= rules.near_window_days`; dicts `{"entry_id": c.id, "parent_entry_id": e.id, "residual": str((line.flow - c.flow).quantize(Q))}` sorted by `(abs(residual), entry_id)`, truncated to `NEAR_CHILDREN_MAX`. In `match()`: `bank_only = _bank_only(line, rules)`; if truthy, `near = _near_children(...)`; `if near: bank_only["near_children"] = near`.

- [ ] **Step 1: Write the failing test**:

```python
def test_bank_only_line_names_its_near_children():
    carrier = E(20, date(2026, 6, 7), "-1000", carrier=True, in_reverse=False, children=(m.Child(21, "fee", D("-5")),))
    far = E(30, date(2026, 6, 14), "-1000", merchant="", children=(m.Child(31, "fee", D("-6")),))       # 6 days → not near
    exact_late = E(40, date(2026, 6, 12), "-1000", merchant="", children=(m.Child(41, "fee", D("-6")),)) # 4 days: outside exact, inside near
    res = run([L(1, date(2026, 6, 8), "-6", kind="fee", merchant="")], [carrier, far, exact_late])
    assert res.claims == []
    assert res.cases[0].context == {"hint": "bank_only", "pattern": None,
                                    "near_children": [{"entry_id": 41, "parent_entry_id": 40, "residual": "0.0000"},
                                                      {"entry_id": 21, "parent_entry_id": 20, "residual": "-1.0000"}]}
    plain = run([L(1, date(2026, 6, 8), "-6", kind="fee", merchant="")], [far])
    assert plain.cases[0].context == {"hint": "bank_only", "pattern": None}
    many = run([L(1, date(2026, 6, 8), "-6", kind="fee", merchant="")],
               [E(50 + i, date(2026, 6, 8), "-1000", merchant="", children=(m.Child(60 + i, "fee", D("-7")),)) for i in range(7)])
    assert [d["entry_id"] for d in many.cases[0].context["near_children"]] == [60, 61, 62, 63, 64]
    claimed = run([L(1, date(2026, 6, 8), "-5", kind="fee", merchant=""), L(2, date(2026, 6, 8), "-6", kind="fee", merchant="")], [carrier])
    assert [c.line_id for c in claimed.claims] == [1] and "near_children" not in next(c for c in claimed.cases if c.line_id == 2).context
```

(`residual` is quantized with the module constant `Q = Decimal("0.0001")` exactly as `_near_case` / `_foreign_case` do, hence `"0.0000"` / `"-1.0000"`.)

- [ ] **Step 2: Run, expect failure** (`KeyError`/assertion on `near_children`).
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run the unit file** — all pass; `grep -n '"hint": "bank_only"' tests/unit/test_statement_matching.py` shows the existing exact-context assertions still green.
- [ ] **Step 5: Commit** — `feat(matching): bank_only lines name their near fee children`.

### Task 4: Loader carriers and the lock set

**Files:**
- Modify: `services/accounting-service/app/services/reconciliation_service.py` (`load_candidates` ~:235–318; `reconcile()` ~:942–951)
- Test: `services/accounting-service/tests/integration/test_reconciliation_service.py` (helpers `_card`, `_line`, `_submit`, `_active`, `_cases`, `_cover_elsewhere`, `engine.apply_action` as at :479; `rec.load_candidates` as at :1545)

**Interfaces:**
- Consumes: Task 1 `Entry.carrier`.
- Produces: `_carriers(db: Session, statement: AccountStatement, accounts: list[int], claimed: set[int], lo: date, hi: date, kinds_out: list[str], stand_in_ids: set[int]) -> list[matching.Entry]` — the covered standalone parents (`id IN claimed` chunked by 200, `group_id IS NULL`, `parent_entry_id IS NULL`, accounts, `currency == statement.currency`, `day` in `[lo, hi]`, `kind NOT IN kinds_out`), minus those whose `_instances(db, ids)` entry has `definition_kind == "installment"`, each with its unclaimed children (today's child query) — only parents with ≥ 1 child; `entry_view(e, children=kids, all_children=(), instance=None, in_reverse=False, claimable=True, carrier=True, stand_in=e.id in stand_in_ids, transfer_peer_is_card=None)`. Appended in `load_candidates` after `_drifted_instances`, before the structural rows. In `reconcile()`: `wanted` and `transfers` exclude carriers (`e.claimable and not e.carrier`); the survivor comprehension (:951–952) moves into a pure `_survivors(entries: list[matching.Entry], locked_rows: Mapping[int, LedgerEntry], first_groups: dict[int, int | None]) -> list[matching.Entry]`: non-claimable rows pass unchanged; a carrier passes without its own id in `locked_rows`; claimable rows are trimmed by `_with_children`; then **a carrier left with no child is dropped** (`not (e.carrier and not e.children)`) — spec §5.2, AGENT-270 finding 3 (`_with_children` returns the trimmed row, it never drops).

- [ ] **Step 1: Write the failing tests**:

```python
def test_load_candidates_builds_a_carrier_for_a_covered_standalone_parent(seed, db_session, run):
    card = _card(seed, db_session); day = date(2026, 9, 16)
    parent = seed.entry(card, "-1000", day=day, merchant="AMAZON")
    fee = seed.entry(card, "-15", kind="fee", day=day, parent_entry_id=parent.id)
    other_cur = seed.entry(card, "-1", kind="fee", day=day, parent_entry_id=parent.id, currency="USD")
    other_acct = seed.entry(_card(seed, db_session, "別卡"), "-2", kind="fee", day=day, parent_entry_id=parent.id)   # non-participating account
    stmt, ls, _ = _submit(db_session, run, card, [_line(1, day, "AMAZON", 1000), _line(2, day, "手續費", 15, "fee")])
    entries, _ = rec.load_candidates(db_session, stmt, [card.id], window_days=15, include_reward=False, exclude={parent.id})
    carrier = next(e for e in entries if e.id == parent.id)
    assert carrier.carrier and carrier.claimable and not carrier.in_reverse and carrier.all_children == ()
    assert [c.id for c in carrier.children] == [fee.id]                      # USD child and other-account child are not in this population
    # all children claimed elsewhere → no carrier; installment parent → no carrier; outside the window → no carrier
    _cover_elsewhere(db_session, run, card, fee, date(2026, 10, 3))
    assert all(e.id != parent.id for e in rec.load_candidates(db_session, stmt, [card.id], window_days=15, include_reward=False, exclude={parent.id})[0])

def test_load_candidates_skips_installment_split_member_and_out_of_window_parents(seed, db_session, run):
    ...  # an ungrouped installment instance parent (seed.definition(..., kind="installment") → posted entry) with a fee child, covered: no carrier;
         # a recurring instance parent with a fee child, covered: carrier; a covered parent dated 20 days before period_start: no carrier;
         # a covered split-group member with a fee child: no carrier (structural row as today, `claimable=False`)

def test_survivors_drop_a_carrier_without_a_locked_child():
    from app.services.statements import matching as m
    carrier = m.Entry(..., id=20, carrier=True, claimable=True, in_reverse=False, children=(m.Child(21, "fee", ...), m.Child(22, "fee", ...)))  # build with the unit `E` shape or a local helper
    plain = m.Entry(..., id=30, claimable=True, children=())
    locked = {22: object(), 30: object()}                                    # 21 joined after the lock; the carrier's own id is not locked
    out = rec._survivors([carrier, plain], locked, {20: None, 30: None})
    assert [(e.id, [c.id for c in e.children]) for e in out] == [(20, [22]), (30, [])]
    assert rec._survivors([carrier], {}, {20: None}) == []                    # no locked child → dropped
    assert [e.id for e in rec._survivors([plain], {}, {30: None})] == []     # a claimable non-carrier still needs its own lock

def test_owner_matched_parent_fee_child_claims_the_fee_line_and_is_idempotent(seed, db_session, run):
    card = _card(seed, db_session); day = date(2026, 9, 16)
    # pre-state (AGENT-270 finding 4): submit FIRST so the revision hook finds no entries; then book the parent + child
    stmt, ls, _ = _submit(db_session, run, card, [_line(1, day, "AMAZON", 1000), _line(2, day, "國外交易手續費", 15, "fee")])
    parent = seed.entry(card, "-1000", day=day, merchant="AMAZON")
    fee = seed.entry(card, "-15", kind="fee", day=day, parent_entry_id=parent.id)
    db_session.commit()
    _owner_cover(db_session, stmt, ls[0], [parent])          # owner shape: parent manual on line 1
    open_fee = [c for c in _cases(db_session, stmt.id, "open") if c.line_id == ls[1].id]
    assert [(c.kind, c.context.get("hint")) for c in open_fee] == [("line_unmatched", "bank_only")]
    rec.reconcile(db_session, stmt.id); db_session.commit()
    rows = _active(db_session, stmt.id)
    assert sorted((r.line_id, r.entry_id, r.role, r.match_kind, r.match_rule) for r in rows) == [(ls[0].id, parent.id, "principal", "manual", "owner"), (ls[1].id, fee.id, "principal", "auto", "exact")]
    assert [c.kind for c in _cases(db_session, stmt.id, "open") if c.line_id == ls[1].id] == []
    before = sorted((r.id, r.line_id, r.entry_id) for r in rows)
    rec.reconcile(db_session, stmt.id); db_session.commit()
    assert sorted((r.id, r.line_id, r.entry_id) for r in _active(db_session, stmt.id)) == before   # kept, not rewritten

def test_parent_claimed_on_the_previous_statement_fee_line_on_the_next(seed, db_session, run):
    ...  # submit N+1 (Sep 1–30, fee line Sep 1) first; then book the parent dated Aug 30 + fee child; then submit N (Aug) whose hook claims the parent
         # auto; `rec.reconcile(N+1)` → the child claims the Sep 1 fee line (carrier from `claimed` on another statement)

def test_two_manual_parents_two_identical_children_two_identical_lines_pair(seed, db_session, run):
    ...  # shape 1: submit 4 lines (two purchases, two identical fee lines) first; book two parents + identical children; `_owner_cover` both purchase
         # lines; reconcile → fee line 1 claimed `exact_pair`, fee line 2 `exact` (single rep); coverage conserved; second reconcile identical row ids

def test_one_line_two_identical_children_stays_ambiguous(seed, db_session, run):
    ...  # shape 3: `ambiguous` case with two candidates, no coverage on the fee line

def test_near_child_is_named_and_a_corrected_child_is_claimed_next_run(seed, db_session, run):
    ...  # shape 5: child -5, bank fee line -6, parent auto-claimed → open line_unmatched {hint bank_only, pattern None, near_children [{child, parent, "-1"}]};
         # update the child's amount to -6 (entry_write_service or direct update + commit) → reconcile → claimed, case resolved
```

Write the elided bodies in the style of the first two; produce the owner coverage with the file's own `_owner_cover(db, statement, line, [parent])` (:1520, release + `write_claim` manual/owner). For 16b (carrier not locked) use the `pg_engine` fixture as `test_hook_leaves_the_pass_to_the_batch_while_an_import_runs` (:676) does: a second connection holds `SELECT … FOR UPDATE` on the **parent** row → `rec.reconcile` in the session still claims the child; the same hold on the **child** row → `reconcile` raises `CodedConflictError` with code `rec.RECONCILE_BUSY`.

- [ ] **Step 2: Run the new tests, expect failures** (no carrier in the population; `bank_only` stays).
- [ ] **Step 3: Implement `_carriers` and call it from `load_candidates`; adjust `reconcile()`'s `wanted`, `transfers` and the survivor filter** per Interfaces (`entry_view` passes `carrier=True` through `**extra` once Task 1's field exists).
- [ ] **Step 4: Run** `tests/integration/test_reconciliation_service.py` (whole file) and `tests/unit/test_statement_matching.py` — all pass.
- [ ] **Step 5: Commit** — `feat(reconcile): carrier rows for covered parents; lock their children only`.

### Task 5: Policy parity, docs, errata

**Files:**
- Test: `services/accounting-service/tests/integration/test_reconciliation_policy.py` (helpers `_set_policy` :60, `_fee_statement` :101, `_only_candidate` :126, `_agent_proposal` :551, `_validator_refusal` :702, `_owner_cover` :756)
- Modify: `docs/superpowers/specs/2026-10-24-reconciliation-split-members-design.md` (§3 "Member" line: add "a child of a **covered standalone** parent reaches a fee line through a carrier row — fee-child spec 2026-10-24"), `docs/superpowers/specs/2026-10-24-reconciliation-fee-child-design.md` (§10: execution errata if any name/behaviour changed), `docs/superpowers/specs/2026-10-08-statement-reconciliation-design.md` only if it lists match rules by name (add `exact_pair` beside `exact`).

**Interfaces:**
- Consumes: Tasks 1–4.

- [ ] **Step 1: Write the failing policy tests** (pre-state as in Task 4: `_fee_statement` submits the lines first — one 1000 AMAZON purchase + one 15 fee line, `DAY` — then book the entries, then `_owner_cover` the purchase line with the parent; helpers `_agent_proposal` :551, `_validator_refusal` :702, `policy_service.collect_candidates`):

```python
def test_validator_sees_the_carrier_child_when_it_ties_with_an_owner_twin(seed, db_session, run):
    # (a) spec §5.4 / test 17a: parent owner-covered, fee child −15 and a standalone owner fee −15 twin, both dated DAY, no merchant text
    ...  # rec.reconcile → the fee line is `ambiguous`, candidates == {child.id, twin.id}, no coverage on it
    ...  # _agent_proposal(db, case, fee_line, twin.id); msg = _validator_refusal(db, stmt.id, case, twin.id); assert "2 admissible" in msg
    ...  # negative control: the same shape without the child → reconcile claims the twin `exact` (today's behaviour, pins what changed)

def test_child_only_fee_line_has_no_case_and_no_policy_candidate(seed, db_session, run):
    # (b) test 17b: child −15 only → claimed `exact`; no open case on the fee line; collect_candidates(...) has no candidate whose case is on that line
    ...

def test_near_child_keeps_the_twin_guard_closed(seed, db_session, run):
    # (c) test 17c: child −14, fee line −15 → open line_unmatched {hint bank_only, near_children [{child, parent, "-1.0000"}]}
    ...  # found = policy_service.collect_candidates(db, stmt, settings_service.reconciliation_policy(db)) with create_system_entry enabled (_set_policy)
    ...  # assert [c for c in found if c.case_id == case.id] == []     — on the list, never `_only_candidate` (it unpacks exactly one)
```

- [ ] **Step 2: Run, expect failure** (today: (a) the validator sees one reading, the twin, and accepts; (b) the line is `bank_only`; (c) `near_children` absent).
- [ ] **Step 3: No production code expected.** If a test exposes a real reconcile/validator disagreement, do **not** patch the policy: stop, write the disagreement into the task report, and the controller decides (spec §2 anti-goals: no policy change).
- [ ] **Step 4: Run** `tests/integration/test_reconciliation_policy.py` and then the full suites once: `tests/unit` and `tests/integration` — both green; paste the pass counts into the task report (the branch base is green on both, so any failure is this branch's).
- [ ] **Step 5: Docs** — the split-members §3 pointer line; fee-child spec §10 execution errata; commit `docs: fee-child errata and cross-spec pointer`.

## Rollout (after merge; host, not part of the tasks)

Deploy the API; before/after active-coverage snapshot for statements 504, 554, 555; re-reconcile them; expect 5784,
5786, 4244, 4242, 4243 claimed by children, 4245 `ambiguous`, 4164 `bank_only` + `near_children` (case re-created
once); open cases 151 → ≤ 146. Host fuzz (`~/reports/…/fuzz_fee_child.py`): random carriers / children / fee lines,
properties (a) no carrier id in any row, (b) `exact_pair` only with a complete, balanced, undecided cohort whose owner children are all claimed by run end or still listed as `ambiguous` candidates, (c) two runs identical.
