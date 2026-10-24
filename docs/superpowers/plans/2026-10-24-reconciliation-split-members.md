# Reconciliation split-group members printed apart — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Members of a MOZE `split` group that the statement does not print whole become candidates like standalone entries (matcher, `match`, `adjust_amount`, inbox search, policy validator), while every group the statement prints whole keeps today's behaviour.

**Architecture:** A per-statement, pure "printed whole" guard in `matching.py` computed over the *structural population* (every member of each split group loaded as a non-claimable `Entry`, with all its children) and *every* current line (covered ones included), enumerating exactly the readings the matcher itself would build and failing closed where it cannot; `match()` and the policy validator share it through `statement_context`. Members of apart groups are threaded into the existing standalone paths with `Row.group_id=None`. Actions relax two refusals for `split` members and lock the member's group explicitly. No schema change.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy, pytest (`services/accounting-service`, `.venv/bin/pytest`; integration tests need the worktree's `.env` — copy from the main checkout, mode 600, never commit).

**Spec:** `docs/superpowers/specs/2026-10-24-reconciliation-split-members-design.md` (rev 4.1; «AGENT-248 n» / «AGENT-249 n» mark reviewer folds). Plan rev 2 folds the AGENT-249 review (1 major, 2 moderate, 1 minor).

## Global Constraints

- Spec §3: a member is top-level, `group.kind == "split"`, instance rule of `_standalone` (installment instance excluded, recurring allowed).
- Spec §3: guard tolerance `max(rules.near_tolerance_abs, rules.near_tolerance_pct · |total|)`, same sign, within `rules.drift_window_days` of the nearest structural member; foreign total compared by magnitude with `_same`, within `rules.foreign_window_days`; the line's kind, gate and coverage are never consulted.
- Spec §3: readings = `_group_variants` member readings × children minus each combination of printed-alone children (cap `ASIDE_COMBINATIONS_MAX`); incomplete or unloadable → whole by fail-closed. Structural rows: `claimable=False, in_reverse=False`, never represented or reported.
- Spec §3 («AGENT-249 1»): two child views — `Entry.children` (claimable view, unchanged) and `Entry.all_children` (every child as stored, filled for every split-group member); guard functions read `e.all_children or e.children`; claim paths read `children` only.
- Spec §4.1: member rows are `Row(e.id, "principal", e.flow, group_id=None)`; representation kinds `member_alone` (rule as standalone) and `member_children` (rule `exact_children`).
- Spec §5.1/§5.2: `entry_in_group` / `not_standalone` stay for `installment` and `reward_claim` members; the member's group is always in the action's lock targets and `touched_group_ids`.
- Spec §5.3: inbox search is an owner override (may list members of whole groups); automatic candidates come from the matcher only.
- Spec §5.6: the validator refuses `not_single_admissible` when `incomplete_groups` is non-empty for the line.
- Commits: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` trailer; conventional subjects; no ledger amounts of the owner in tests or docs (fictional numbers only).
- Existing behaviour pinned by tests not named in this plan must stay green; a flipped existing test is renamed with the reason in its name (spec §7.1/3/14).

## Review Focus

1. A split whose sibling is on another account while the card prints the **sum** (expense + receivable): the member must not be claimed or listed (§7 test 1b, Task 2).
2. A fee member covered by an earlier owner match, with an unrelated same-amount line printed first: the group must stay whole and the purchase member must not move (§7 test 5b, Task 1/2).
3. A group above `ASIDE_COMBINATIONS_MAX` that is apart: reconcile must still emit `group_readings_incomplete` and the validator must refuse any proposal on that line (§7 tests 13/18c, Tasks 2/6).
4. Two member matches on one group then a revert of the first: refused `later_actions`; a group rename or delete releases the member's line (§7 test 15, Task 4).
5. Repeated reconcile of a cross-account fixture: idempotent, no superseded churn (§7 test 19, Task 3).

---

### Task 1: Structural printed-alone pieces, group readings and the printed-whole guard (pure functions)

**Files:**
- Modify: `services/accounting-service/app/services/statements/matching.py` (new functions after `incomplete_groups` :338; `match` signature only in Task 2)
- Test: `services/accounting-service/tests/unit/test_statement_matching.py`

**Interfaces:**
- Produces (pure; one new dataclass field `Entry.all_children: tuple[Child, ...] = ()` appended after `stand_in`; structural rows are ordinary `Entry` objects with `claimable=False, in_reverse=False`; the `E(...)` test helper gains `all_children=` and `S(...)` = `E(..., claimable=False, in_reverse=False)`; a helper `_kids(e) = e.all_children or e.children` is used by every guard function):
  - `def printed_alone_children(kind: str, lines_all: list[Line], entries: list[Entry], groups: dict[int, Group]) -> frozenset[int]` — child ids (from `_kids(member)`) of split-group members whose `kind` is in `eligible_kinds(kind, line.line_kind)` and whose flow `_same` a line of `lines_all` with `line_kind not in ("purchase", "payment")` (the child path of `_representations` :380–387).
  - `@dataclass(frozen=True) class Reading: flow: Decimal; foreign_amount: Decimal | None; foreign_currency: str | None; members: tuple[Entry, ...]`
  - `def group_readings(g: Group, by_id: dict[int, Entry], aside: frozenset[int], aside_children: frozenset[int]) -> tuple[list[Reading], bool]` — for each member reading of `_group_variants(g, by_id, set(), aside)`: the net reading, and for each combination (sizes 0..k, k ≤ `ASIDE_COMBINATIONS_MAX`) of that reading's children in `aside_children`, the reading plus all of `_kids(member)` minus that combination; `complete` = `_group_variants` complete and the removable children ≤ cap; a missing member (`by_id` lacks an id of `member_ids`) → `([], False)`. Foreign fields set only when every member of the reading has `original_amount` and one shared `original_currency`.
  - `def printed_whole(lines_all: list[Line], entries: list[Entry], groups: dict[int, Group], rules: Rules, aside: frozenset[int], aside_children: frozenset[int]) -> frozenset[int]` — split groups with a reading satisfying spec §3 (a)/(b), **or** whose `group_readings` is incomplete / empty (fail-closed).
  - `def apart_members(entries: list[Entry], groups: dict[int, Group], whole: frozenset[int]) -> frozenset[int]` — ids of **claimable** top-level entries whose `group_id` names a `split` group not in `whole`, `instance is None or instance.definition_kind != "installment"`.
  - `@dataclass(frozen=True) class Context: aside: frozenset[int]; aside_children: frozenset[int]; whole: frozenset[int]; apart: frozenset[int]; structural: tuple[Entry, ...]`
  - `def statement_context(kind: str, lines_all: list[Line], entries: list[Entry], groups: dict[int, Group], rules: Rules) -> Context` — `entries` unfiltered (claimable ∪ structural); `aside = printed_alone(kind, lines_all, entries, groups, rules)`; `structural = tuple(entries)`.

- [ ] **Step 1: Write failing tests for `printed_alone_children` and `group_readings`** (`d = date(2026, 9, 3)`; `S(...)` = `E(..., claimable=False, in_reverse=False)` helper for structural rows)

```python
def test_printed_alone_children_match_a_non_purchase_line_of_any_coverage():
    g = m.Group(5, "split", (21, 22))
    ents = [E(21, d, "-300", group_id=5, children=[m.Child(211, "discount", D("100"))],
              all_children=[m.Child(210, "discount", D("200")), m.Child(211, "discount", D("100"))]), E(22, d, "-280", group_id=5)]  # 210 covered: structural view only
    assert m.printed_alone_children("card", [L(1, d, "200", kind="refund")], ents, {5: g}) == {210}
    assert m.printed_alone_children("card", [L(1, d, "-200", kind="purchase")], ents, {5: g}) == frozenset()

def test_group_readings_enumerate_members_and_children_like_the_matcher():
    g = m.Group(5, "split", (21, 22, 23))
    ents = [E(21, d, "-300", group_id=5, children=[m.Child(210, "discount", D("200")), m.Child(211, "discount", D("100"))]),
            E(22, d, "-280", group_id=5), S(23, d, "-10", kind="fee", group_id=5)]
    readings, complete = m.group_readings(g, {e.id: e for e in ents}, aside=frozenset({23}), aside_children=frozenset({210}))
    assert complete and {r.flow for r in readings} == {D("-590"), D("-290"), D("-490"), D("-580"), D("-280"), D("-480")}

def test_group_readings_missing_member_is_incomplete():
    g = m.Group(5, "split", (21, 22))
    assert m.group_readings(g, {21: E(21, d, "-300", group_id=5)}, frozenset(), frozenset()) == ([], False)

def test_group_readings_foreign_total_needs_one_currency_on_every_member():
    g = m.Group(5, "split", (21, 22))
    jp = dict(original_currency="JPY")
    ents = [E(21, d, "-3000", group_id=5, original_amount="-15000", **jp), S(22, d, "-100", group_id=5, original_amount="-500", **jp)]
    r, _ = m.group_readings(g, {e.id: e for e in ents}, frozenset({22}), frozenset())
    assert {(x.flow, x.foreign_amount) for x in r} == {(D("-3100"), D("-15500")), (D("-3000"), D("-15000"))}
    ents[1] = S(22, d, "-100", group_id=5)
    assert all(x.foreign_amount is None for x in m.group_readings(g, {e.id: e for e in ents}, frozenset(), frozenset())[0])
```

- [ ] **Step 2: Run, expect failure** — `.venv/bin/pytest tests/unit/test_statement_matching.py -k "printed_alone_children or group_readings" -q`.
- [ ] **Step 3: Implement** `Reading`, `printed_alone_children`, `group_readings` per Interfaces (reuse `_group_variants`, `itertools.combinations`, dedupe readings by `(flow, foreign_amount, member ids)`).
- [ ] **Step 4: Run Step 1 tests, expect PASS.**

- [ ] **Step 5: Write failing tests for `printed_whole` / `apart_members` / `statement_context`**

```python
def test_printed_whole_by_exact_near_drift_and_sign():
    g = m.Group(5, "split", (21, 22)); ents = [E(21, d, "-300", group_id=5), E(22, d, "-280", group_id=5)]
    pw = lambda lines: m.printed_whole(lines, ents, {5: g}, R, frozenset(), frozenset())
    assert pw([L(1, d, "-580")]) == {5} and pw([L(1, d, "-575")]) == {5} and pw([L(1, d + timedelta(days=12), "-580")]) == {5}
    assert pw([L(1, d + timedelta(days=16), "-580")]) == frozenset() and pw([L(1, d, "-300")]) == frozenset()
    assert pw([L(1, d, "580", kind="refund")]) == frozenset()

def test_printed_whole_ignores_gate_and_uses_structural_rows():
    g = m.Group(5, "split", (21, 22)); ents = [E(21, d, "20000", kind="income", group_id=5), S(22, d, "10000", kind="income", group_id=5)]
    assert m.printed_whole([L(1, d, "30000", kind="deposit")], ents, {5: g}, R, frozenset(), frozenset()) == {5}

def test_printed_whole_fails_closed_without_the_other_member():
    g = m.Group(5, "split", (21, 22))
    assert m.printed_whole([L(1, d, "-300")], [E(21, d, "-300", group_id=5)], {5: g}, R, frozenset(), frozenset()) == {5}

def test_printed_whole_by_reduced_and_children_readings_through_covered_lines():
    g = m.Group(5, "split", (21, 22, 23))
    ents = [E(21, d, "-300", group_id=5, children=[m.Child(210, "discount", D("200")), m.Child(211, "discount", D("100"))]),
            E(22, d, "-280", group_id=5), S(23, d, "-100", kind="fee", group_id=5)]
    lines_all = [L(1, d, "-300"), L(2, d, "-380"), L(3, d, "-100", kind="fee"), L(4, d, "200", kind="refund")]
    ctx = m.statement_context("card", lines_all, ents, {5: g}, R)
    assert ctx.aside == {23} and ctx.aside_children == {210} and ctx.whole == {5} and ctx.apart == frozenset()   # -680 -100 fee +100 child = -380

def test_printed_whole_by_foreign_total():
    g = m.Group(5, "split", (21, 22)); jp = dict(original_currency="JPY")
    ents = [E(21, d, "-3000", group_id=5, original_amount="-15000", **jp), E(22, d, "-100", group_id=5, original_amount="-500", **jp)]
    line = L(1, d, "-3200", foreign_amount=D("15500"), foreign_currency="JPY")
    assert m.printed_whole([line], ents, {5: g}, R, frozenset(), frozenset()) == {5}

def test_apart_members_are_claimable_split_members_of_apart_groups_only():
    g5 = m.Group(5, "split", (21, 22)); g6 = m.Group(6, "split", (31, 32)); g7 = m.Group(7, "installment", (41,))
    ents = [E(21, d, "-300", group_id=5), E(22, d, "-280", group_id=5), E(31, d, "-50", group_id=6), S(32, d, "-60", group_id=6), E(41, d, "-70", group_id=7)]
    ctx = m.statement_context("card", [L(1, d, "-580")], ents, {5: g5, 6: g6, 7: g7}, R)
    assert ctx.whole == {5} and ctx.apart == {31}
```

- [ ] **Step 6: Run, expect failure.** — [ ] **Step 7: Implement `printed_whole`, `apart_members`, `Context`, `statement_context`.** — [ ] **Step 8: Run the whole unit file, expect PASS.**
- [ ] **Step 9: Commit** — `feat(matching): structural printed-whole guard for split groups`.

---

### Task 2: Apart members as standalone-like candidates in `match()`

**Files:**
- Modify: `services/accounting-service/app/services/statements/matching.py` (`_representations` :348–424, `_foreign_case` :495–536, `_near_case` :538, `_drift_case` :565, `_claimed_by_earlier_line` :599, `match` :623–670)
- Test: `services/accounting-service/tests/unit/test_statement_matching.py`

**Interfaces:**
- Consumes: Task 1 (`statement_context`, `Context`; structural rows = `S(...)` helper).
- Produces: `match(..., account_currency: str, lines_all: list[Line] | None = None) -> MatchResult`; `_representations(kind, line, entries, groups, plan_map, claimed, rules, aside=frozenset(), apart=frozenset())`; `_foreign_case(..., aside=frozenset(), apart=frozenset())`; `_near_case(kind, line, entries, claimed, rules, apart=frozenset())`; `_drift_case(kind, line, entries, claimed, rules, apart=frozenset())`; `_claimed_by_earlier_line(..., aside, apart)`; the loop's `truncated = incomplete_groups(kind, line, ctx.structural, groups, set(), ctx.aside)` (structural, `claimed=∅`). Representation kinds `member_alone` / `member_children`. The `run(...)` test helper gains `lines_all=None`.

- [ ] **Step 1: Write the failing tests** (spec §7.1–3, 4–14; fictional amounts; `d = date(2026, 9, 3)`)

```python
def test_cross_account_member_claims_its_own_line():
    g = m.Group(5, "split", (21, 22))
    res = run([L(1, d, "-300")], [E(21, d, "-300", group_id=5), S(22, d, "-280", group_id=5, account_id=2)], groups={5: g})  # 22: another account, structural
    rep = res.claims[0].representation
    assert (rep.kind, rep.rule, [(r.entry_id, r.group_id) for r in rep.rows]) == ("member_alone", "exact", [(21, None)])
    assert res.cases == [] and res.unmatched_entry_ids == []

def test_cross_account_expense_plus_receivable_whose_sum_the_card_prints_stays_whole():
    g = m.Group(5, "split", (21, 22))
    res = run([L(1, d, "-300")], [E(21, d, "-580", group_id=5), S(22, d, "280", kind="receivable", group_id=5, account_id=2)], groups={5: g})
    assert res.claims == [] and res.cases[0].kind == "line_unmatched" and res.unmatched_entry_ids == [21]

def test_partial_group_without_its_other_member_fails_closed_and_is_never_admissible():
    # renamed from test_partial_group_is_never_admissible: no structural row for 22 → whole by fail-closed
    ...same body as the existing test...

def test_same_card_split_printed_apart_claims_each_member():
    g = m.Group(5, "split", (21, 22))
    ents = [E(21, d, "-300", group_id=5), E(22, d, "-280", group_id=5)]
    res = run([L(1, d, "-300"), L(2, d, "-280")], ents, groups={5: g})
    assert sorted(c.representation.rows[0].entry_id for c in res.claims) == [21, 22] and all(c.representation.kind == "member_alone" for c in res.claims)
    res2 = run([L(1, d, "-300"), L(2, d, "-280"), L(3, d, "-580")], ents, groups={5: g})
    assert [c.representation.rule for c in res2.claims] == ["group"] and [c.kind for c in res2.cases] == ["line_unmatched", "line_unmatched"]

def test_a_split_member_never_stands_alone_when_the_group_is_printed_whole():
    # rewritten from test_a_split_member_never_stands_alone_for_a_purchase_line: _fee_group is -300/-280/-10 → a -590 line
    g, members = _fee_group()  # give the group structural members via G(...) if _fee_group lacks them
    res = run([L(1, d, "-300"), L(2, d, "-590")], members, groups={6: g})
    assert [c.representation.rule for c in res.claims] == ["group"] and res.cases[0].kind == "line_unmatched"

def test_whole_by_covered_sum_line_in_lines_all():
    g = m.Group(5, "split", (21, 22))
    ents = [E(21, d, "-300", group_id=5), E(22, d, "-280", group_id=5)]
    res = run([L(1, d, "-300")], ents, groups={5: g}, lines_all=[L(1, d, "-300"), L(2, d, "-580")])
    assert res.claims == [] and res.cases[0].kind == "line_unmatched"

def test_whole_by_covered_fee_member_keeps_the_purchase_member_home():
    g = m.Group(5, "split", (21, 22, 23))
    ents = [E(21, d, "-300", group_id=5), E(22, d, "-280", group_id=5), S(23, d, "-100", kind="fee", group_id=5)]   # fee 23 owner-covered: structural
    res = run([L(1, d, "-300"), L(2, d, "-580")], ents, groups={5: g},
              lines_all=[L(1, d, "-300"), L(2, d, "-580"), L(3, d, "-100", kind="fee")])
    assert res.claims == [] and [c.kind for c in res.cases] == ["line_unmatched", "line_unmatched"]
    assert run([L(1, d, "-300"), L(2, d, "-580")], ents, groups={5: g}, lines_all=[...same...]).claims == []  # idempotent

def test_whole_by_children_reading_with_a_covered_child_both_line_orders():
    g = m.Group(5, "split", (21, 22))
    kids = [m.Child(210, "discount", D("100")), m.Child(211, "discount", D("200"))]
    ents = [E(21, d, "-300", group_id=5, children=kids[:1], all_children=kids), E(22, d, "-280", group_id=5)]   # 211 owner-covered: structural view only («AGENT-249 2»)
    covered = L(9, d, "200", kind="refund")   # the +200 child's own line: in lines_all only
    for first, second in ((1, 2), (2, 1)):     # match() sorts by line id: swap the ids to test both orders
        lines = [L(first, d, "-300"), L(second, d, "-480")]
        res = run(lines, ents, groups={5: g}, lines_all=lines + [covered])
        claim = res.claims[0].representation
        assert claim.kind == "group_children" and {r.entry_id for r in claim.rows} == {21, 22, 210} and res.cases[0].line_id == first

def test_income_split_on_a_bank_statement_is_whole_only_with_its_deposit_line():
    g = m.Group(5, "split", (21, 22))
    ents = [E(21, d, "20000", kind="income", group_id=5), E(22, d, "10000", kind="income", group_id=5)]
    whole = run([L(1, d, "30000", kind="deposit"), L(2, d, "20000", kind="deposit")], ents, groups={5: g}, kind="bank")
    assert all(c.representation.kind != "member_alone" for c in whole.claims)
    apart = run([L(2, d, "20000", kind="deposit")], ents, groups={5: g}, kind="bank")
    assert apart.claims[0].representation.rows[0].entry_id == 21

def test_apart_member_foreign_near_and_drift_cases():
    g = m.Group(5, "split", (21, 22))
    ents = [E(21, d, "-3000", group_id=5, original_amount="-15000", original_currency="JPY"),
            E(22, d, "-100", group_id=5, original_amount="-500", original_currency="JPY")]
    res = run([L(1, d, "-3010", foreign_amount=D("15000"), foreign_currency="JPY")], ents, groups={5: g})
    assert res.cases[0].kind == "amount_delta" and res.cases[0].candidates[0] == {"entry_id": 21, "reason": "foreign"}
    assert res.explained.get(21) != "deferred_next_period"
    g2 = m.Group(6, "split", (31, 32)); ents2 = [E(31, d, "-300", group_id=6), E(32, d, "-280", group_id=6)]
    near = run([L(1, d, "-305")], ents2, groups={6: g2}); assert near.cases[0].kind == "amount_delta" and near.cases[0].entry_id == 31
    drift = run([L(1, d + timedelta(days=6), "-300")], ents2, groups={6: g2}); assert drift.cases[0].context.get("hint") == "date_drift"

def test_apart_member_with_discount_child_conserves_as_member_children():
    g = m.Group(5, "split", (21, 22))
    ents = [E(21, d, "-300", group_id=5, children=[m.Child(210, "discount", D("20"))]), E(22, d, "-250", group_id=5)]   # distinct amounts: no ambiguity («AGENT-249 2»)
    res = run([L(1, d, "-280"), L(2, d, "-250")], ents, groups={5: g})
    kinds = sorted(c.representation.kind for c in res.claims); assert kinds == ["member_alone", "member_children"]

def test_apart_member_and_standalone_twin_are_ambiguous():
    g = m.Group(5, "split", (21, 22))
    ents = [E(21, d, "-300", group_id=5), E(22, d, "-280", group_id=5), E(30, d, "-300")]
    res = run([L(1, d, "-300", merchant="")], ents, groups={5: g})
    assert res.cases[0].kind == "ambiguous" and {(c["entry_id"], c["group_id"]) for c in res.cases[0].candidates} == {(21, None), (30, None)}

def test_group_readings_incomplete_is_structural_and_survives_covered_fee_members():
    g = m.Group(5, "split", tuple([21] + [30 + i for i in range(1, 10)]))
    fees = [E(30 + i, d, f"-{i}", kind="fee", group_id=5) for i in range(1, 10)]
    lines = [L(1, d, "-100")] + [L(10 + i, d, f"-{i}", kind="fee") for i in range(1, 10)]
    res = run(lines, [E(21, d, "-100", group_id=5)] + fees, groups={5: g})
    blocked = [c for c in res.cases if c.line_id == 1]
    assert blocked and blocked[0].context.get("hint") == "group_readings_incomplete" and all(c.line_id != 1 for c in res.claims)
    structural = [dataclasses.replace(f, claimable=False, in_reverse=False) for f in fees]   # fee lines covered by an earlier run
    res2 = run([L(1, d, "-100")], [E(21, d, "-100", group_id=5)] + structural, groups={5: g}, lines_all=lines)
    assert res2.claims == [] and res2.cases[0].context.get("hint") == "group_readings_incomplete"
```
Re-state per spec §7.9/10/12/14: `test_drift_candidates_…_skip_claimed_and_grouped` (:820 — a grouped candidate of an apart group is listed, of a whole group is not), `:230`, `:380` gross branch (stays green: −90 within 10 TWD of −100), `:390` far branch, `:443` deposit branch (give the group a whole-marking line), `:469 res2`. Each re-stated test's name says why.

- [ ] **Step 2: Run the unit file, expect the new tests to fail and the flipped ones to fail.**

- [ ] **Step 3: Implement** per the Interfaces block: `match()` builds `ctx = statement_context(statement_kind, lines_all or lines, entries, groups, rules)` **before** filtering `claimable`, uses `ctx.aside` where `aside` is used today, evaluates `truncated` over `ctx.structural` with `claimed=set()` (still only when `reps` is non-empty — ruling on AGENT-249 Q3, spec §4.1: an incomplete group is whole by fail-closed so no member reading exists), passes `ctx.apart` to `_representations`, `_foreign_case`, `_near_case`, `_drift_case`, `_claimed_by_earlier_line`. In `_representations` the standalone loop condition becomes `_standalone(e) or _lone_member(...) or e.id in apart`; variants for an apart member are `("member_alone", rule, principal)` and `("member_children", "exact_children", principal + child_rows)`. In the three case functions: `(_standalone(e) or e.id in apart)`. `incomplete_groups` and `_group_variants` unchanged.

- [ ] **Step 4: Run the whole unit file, expect PASS.**

- [ ] **Step 5: Commit** — `feat(matching): members of a split group printed apart stand alone`.

---

### Task 3: Loader adds structural rows; reconcile passes every current line

**Files:**
- Modify: `services/accounting-service/app/services/reconciliation_service.py` (`load_candidates` :191–254 — after the groups block :234–244; `reconcile` :797–799)
- Test: `services/accounting-service/tests/integration/test_reconciliation_service.py`

**Interfaces:**
- Consumes: `match(..., lines_all=)` (Task 2); `entry_view` :175.
- Produces: `load_candidates` (a) queries **every** top-level member of every `split` group in `groups` (`LedgerEntry.group_id IN group_ids AND parent_entry_id IS NULL`) and **every** child of those members in one unfiltered query (no account/currency/claimed filter); (b) sets `all_children` on every loaded split member (claimable ones included — their `children` stays the filtered view, «AGENT-249 1»); (c) appends each member not already in `entries` as `entry_view(e, children=<all its children>, all_children=<same>, instance=None, in_reverse=False, claimable=False, stand_in=False, transfer_peer_is_card=None)`. `reconcile()`: `wanted` = claimable entries' ids + **claimable entries'** `children` ids only (structural rows and `all_children` are never locked or in `wanted`, «AGENT-249 4»); `_with_children` trims `children` only and leaves `all_children` untouched; the post-lock filter keeps structural rows unchanged (`not e.claimable` branch); `matching.match(..., lines_all=[_as_line(row) for row in all_lines])`. `_lock_candidates` unchanged (the group lock covers membership).

- [ ] **Step 1: Write the failing integration tests** (file's seeding helpers; fictional amounts)

```python
def test_load_candidates_adds_structural_rows_and_two_child_views(...):
    # split: card member (claimable) with children +100 (unclaimed) and +200 (covered elsewhere), 錢包 member (other account), fee member owner-matched on this statement
    entries, groups = rec.load_candidates(db, statement, [card.id], window_days=15, include_reward=False, exclude={fee.id})
    by_id = {e.id: e for e in entries}
    assert by_id[card_member.id].claimable and not by_id[wallet_member.id].claimable and not by_id[fee.id].claimable
    assert by_id[fee.id].in_reverse is False
    assert [c.id for c in by_id[card_member.id].children] == [child_100.id] and sorted(c.id for c in by_id[card_member.id].all_children) == sorted([child_100.id, child_200.id])

def test_reconcile_keeps_a_group_whole_through_a_covered_child_on_a_claimable_parent(...):   # «AGENT-249 1»
    # members -300/-280, child +200 covered elsewhere, lines -300 and -380 → the -380 structural reading marks the group whole: no member claim on -300; -380 gets the group_children claim with no child row for +200 (no duplicate coverage); second reconcile identical

def test_reconcile_claims_a_cross_account_member_and_is_idempotent(...):
    # card line -300; split: -300 on the card, -280 on 錢包 (already matched on the 錢包 statement)
    reconcile → coverage for the line == [(card_member.id, None, "auto")]; reconcile again → same row id, no superseded case of this statement

def test_reconcile_keeps_a_group_whole_when_its_sum_line_or_fee_member_is_owner_covered(...):
    # (a) -580 sum line owner-covered, -300 line open → no member claim; (b) fee member owner-matched to its fee line, lines -300 and -580 → group whole, **no claim at all** (the owner-covered member is absent from the claimable rows, so `_group_variants` has no reading — today's behaviour; «AGENT-249 2»), the -580 and -300 lines stay open, no member claim on -300
```

- [ ] **Step 2: Run, expect failure.** — [ ] **Step 3: Implement.** — [ ] **Step 4: Run the file and the unit file, expect PASS.**
- [ ] **Step 5: Commit** — `feat(reconciliation): structural split rows in the population; the guard sees every current line`.

---

### Task 4: `match` accepts split members and locks their group

**Files:**
- Modify: `services/accounting-service/app/services/reconciliation_actions/coverage_actions.py` (`lock_targets` :66–68, `apply` :78–80, `_claim` :113–121)
- Test: `services/accounting-service/tests/integration/test_reconciliation_actions_engine.py` (or the file holding the existing `entry_in_group` test)

**Interfaces:**
- Produces: `MatchAction.lock_targets` adds the entry's `group_id` to `group_ids` when the entry exists and `group_id is not None` (read with `db.get(LedgerEntry, params.entry_id)`; a missing entry keeps today's refusal path); `_claim` refuses `entry_in_group` only when the group's kind is not `split`; `effect.touched_group_ids` = `[params.group_id]` or `[entry.group_id]` when the matched entry is a member, else `[]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_match_accepts_a_split_member_and_refuses_an_installment_member(...):
    res = apply match with entry_id=split_member → 200; coverage row: entry_id == member, group_id is None, role "principal"
    action = latest ReconciliationAction; assert action.touched_group_ids == [split_group.id]
    sibling case still open with its version unchanged
    res2 = apply match with entry_id=installment_member → 422, body code "entry_in_group"

def test_revert_of_a_member_match_and_sibling_ordering(...):
    m1 = match member A; m2 = match member B (same group, another line)
    revert m1 → 409/422 "later_actions"; revert m2 → ok; revert m1 → ok; both lines released, both entries unclaimed

def test_group_update_and_delete_release_a_member_claim_through_the_sweep(...):
    match member; rename the group (dirty trigger) → sweep → coverage stale with the structural reason; repeat with delete
```

- [ ] **Step 2: Run, expect failure.**
- [ ] **Step 3: Implement** per Interfaces (the kind check loads `EntryGroup` once).
- [ ] **Step 4: Run the file, expect PASS.**
- [ ] **Step 5: Commit** — `feat(actions): match accepts a split member and locks its group`.

---

### Task 5: `adjust_amount` for split members; inbox `adjustable` and search

**Files:**
- Modify: `services/accounting-service/app/services/reconciliation_actions/ledger_actions.py` (`lock_targets` :386–390, `_plan` :433–435)
- Modify: `services/accounting-service/app/services/inbox_service.py` (`adjustable` :148–160, `search_candidates` :218–246, `_candidate` :201)
- Test: `services/accounting-service/tests/integration/test_reconciliation_actions_ledger.py`, `tests/integration/test_inbox_api.py`

**Interfaces:**
- Produces: `AdjustAmountAction.lock_targets` adds the entry's `group_id` when set (as Task 4); `_plan` refuses `not_standalone` for a grouped entry only when its group's kind is not `split`; `apply` (:483–488) sets `effect.touched_group_ids = [entry.group_id]` in the standalone branch when the entry is a member (coverage row keeps `group_id NULL`, «AGENT-249 3»); `inbox_service.adjustable` treats a `split` member like a standalone; `search_candidates` query: `or_(LedgerEntry.group_id.is_(None), EntryGroup.kind == "split")` via an outer join on `EntryGroup`; `_candidate` output gains `"group_id": entry.group_id`.

- [ ] **Step 1: Write the failing tests**

```python
def test_adjust_amount_rewrites_a_split_member_and_refuses_a_reward_claim_member(...):
    split member amount_delta case → adjust_amount → 200; entry amount == line amount; group still has both members; action.touched_group_ids == [group.id]
    reward_claim member → 422 "not_standalone"

def test_mixed_match_and_adjust_on_one_group_revert_in_reverse_order_only(...):   # «AGENT-249 3»
    match member A; adjust_amount member B (same group); revert the match → refused later_actions; revert the adjust → ok; revert the match → ok

def test_inbox_adjustable_true_for_split_member_false_for_child(...)

def test_inbox_search_lists_a_split_member_as_owner_override_and_never_an_installment_member(...):
    # replaces the B4 assertion at test_inbox_api.py:182
    ids = [c["entry_id"] for c in res.json()]; assert split_member.id in ids and installment_member.id not in ids
    assert next(c for c in res.json() if c["entry_id"] == split_member.id)["group_id"] == split_group.id
```

- [ ] **Step 2: Run, expect failure.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run both files, expect PASS.**
- [ ] **Step 5: Commit** — `feat(inbox): adjust_amount and search admit split members`.

---

### Task 6: Policy validator shares the context and the incomplete block

**Files:**
- Modify: `services/accounting-service/app/services/policy_service.py` (`_is_proposed` :484–490, `_single_admissible` :498–513)
- Test: `services/accounting-service/tests/integration/test_reconciliation_policy.py`

**Interfaces:**
- Consumes: `matching.statement_context`, `matching.incomplete_groups`, `rec._current_lines`, `rec._as_line`, `rec.load_candidates` (Task 3 adds structural rows).
- Produces: `_single_admissible` computes `match_ctx = matching.statement_context(statement.kind, [rec._as_line(r) for r in rec._current_lines(db, statement)], entries, groups, ctx.rules)` (the policy `ctx` keeps its name: `ctx.rules`/`ctx.case` are still used, «AGENT-249 4»), calls `_representations(..., aside=match_ctx.aside, apart=match_ctx.apart)` over `[e for e in entries if e.claimable]`, and raises `not_single_admissible` when `matching.incomplete_groups(statement.kind, rec._as_line(line), list(match_ctx.structural), groups, set(), match_ctx.aside)` is non-empty (structural population, `claimed=∅`; the matcher `Line`, not the ORM row); `_is_proposed` accepts `member_alone` (no `with_children`) and `member_children` (`with_children`).

- [ ] **Step 1: Write the failing tests** (spec §7.18 a–d)

```python
def test_policy_refuses_a_standalone_proposal_when_an_apart_member_competes(...)      # not_single_admissible, no coverage
def test_policy_applies_a_match_proposal_naming_the_single_apart_member(...)           # applied, coverage group_id None
def test_policy_refuses_the_purchase_member_after_reconcile_claimed_the_fee_lines(...)  # run reconcile first (fee claims persist), then propose: not_single_admissible, no purchase coverage
def test_policy_and_reconcile_agree_on_whole_with_a_covered_fee_member(...)            # the member proposal is refused; reconcile wrote no member claim
```

- [ ] **Step 2: Run, expect failure.** — [ ] **Step 3: Implement.** — [ ] **Step 4: Run the file, expect PASS.**
- [ ] **Step 5: Commit** — `fix(policy): validator shares the matcher's statement context and incomplete-group block`.

---

### Task 7: Spec errata, full suite, rollout notes

**Files:**
- Modify: `docs/superpowers/specs/2026-10-24-reconciliation-split-members-design.md` (append "§9 Errata" only if implementation deviated; otherwise add "Status: implemented at <sha>")
- Modify: `docs/superpowers/specs/2026-10-15-reconciliation-r6-design.md` — one line under the 2026-10-19 `printed_alone` note pointing to this spec.

- [ ] **Step 1: Run the whole accounting suite** — `cd services/accounting-service && .venv/bin/pytest -q -p no:cacheprovider` (absolute paths if backgrounded). Expected: all pass except the known-flaky OCR test noted in the ledger if it appears; any other failure is a task defect, fix in that task's files.
- [ ] **Step 1b: Errata and copy fixes found during execution** — spec §5.1: the revert refusal code is `not_revertable` (detail "later action N"), not `later_actions`; spec §5.3: the search payload's `group_id` is also present on listed candidates (`InboxCandidate` schema gained the optional field). SPA copy: `frontend/src/app/components/accounting/inbox/inbox-view.ts` maps `entry_in_group` to 「這筆是拆帳成員，不能直接對」 — that code now fires only for installment / reward_claim members; change the copy to 「這筆是分期或回饋群組的成員，不能直接對」 (one line, plus its spec test if one asserts the string: `grep -rn "不能直接對" frontend/src`). Run the touched SPA spec file only (`cd frontend && npx ng test --include='<spec path>'`) if a spec asserts it; otherwise no SPA test run.
- [ ] **Step 2: Write the rollout note** into the plan's tail (release record steps): deploy API from the release checkout (homehub-api-ctl recipe), then `POST /accounts/{a}/statements/{s}/reconcile` for statements 121, 127, 128, 155, 156, 220, 224, 229, 504, 505; record open-case counts before/after; owner follow-up: `replace_stand_in` for groups 4302 (line 1417) and 4343 (line 3588).
- [ ] **Step 3: Commit** — `docs(reconciliation): split-members spec status + r6 pointer`.

## Rollout (release record steps)

1. Deploy the accounting API from the release checkout (`homehub-api-ctl` recipe). Alembic has no new migration.
2. Record open-case counts before (per statement and total), then `POST /accounts/{a}/statements/{s}/reconcile` for statements 121, 127, 128, 155, 156, 220, 224, 229, 504, 505; record open-case counts after.
3. Owner follow-up: `replace_stand_in` for groups 4302 (line 1417) and 4343 (line 3588).
4. Follow-up GitHub issues to file after merge:
   - (i) SPA 「拆帳成員」 label for `group_id` candidates (`inbox.model.ts` lacks the field).
   - (ii) The policy twin guard is population-bound: a line near a cross-account group total can still be backfilled.
   - (iii) A pending policy creation proposal on a superseded case is never marked superseded.
   - (iv) Validator/reconcile parity gap when a group's only loaded members are this statement's auto-claimed fee members.
