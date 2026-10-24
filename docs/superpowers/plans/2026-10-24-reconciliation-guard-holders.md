# Reconciliation guard holders (issue #136) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A guard line that an unrelated, non-stand-in entry explains exactly no longer keeps a split group "printed whole"; decided from stable coverage plus this run's own pass-1 claims, identically in `reconcile()` and the policy validator.

**Architecture:** `matching.py` gains a `Holder` value, an optional `holders` map threaded `match → statement_context → printed_whole`, and two pure builders (`claim_holders`, `merge_holders`). `reconciliation_service.py` reads stable holders from the DB (`guard_holders`) and runs the matcher twice (pass 1 stable, pass 2 stable ∪ own claims). `policy_service._single_admissible` repeats pass 1 to build the same context. No schema change, no migration.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2, pytest (unit + Postgres integration).

**Spec:** `docs/superpowers/specs/2026-10-24-reconciliation-guard-holders-design.md` (rev 2), amending `docs/superpowers/specs/2026-10-24-reconciliation-split-members-design.md` §3 / §5.6.

## Global Constraints

- Repo root for commands: `services/accounting-service`; tests run as `PYTHONPATH=../shared-python-lib .venv/bin/python -m pytest <path> -q -p no:cacheprovider` (the venv's `pytest` shebang is stale). Never `-I` with app imports.
- Commit trailer: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Fail-closed everywhere the spec says so: no holder, inexact holder, unresolvable entry, stand-in holder, holder related to the group → the line stays a guard line (today's behaviour). `holders=None` or `{}` must reproduce PR #130 exactly.
- `printed_alone`, `printed_alone_children`, `group_readings`, `guard_lines`, `load_candidates` (beyond `Entry.source`), actions, inbox, schemas, SPA: unchanged.
- Stable coverage = this statement's active rows with `match_kind in OWNED_KINDS` (`("manual", "agent")`, reconciliation_service.py:396) + every active row of a neighbouring guard line. This statement's own `auto` rows are never read as holders.
- Exactness: signed Σ `flow` `_same` `line.flow`; or every holder entry `original_currency == line.foreign_currency` and `_same(|Σ original_amount|, |line.foreign_amount|)` with `line.foreign_amount` not null.
- Stand-in: entry id ∈ `stand_ins.stand_in_entry_ids(db, accounts)` **or** `source == "statement"` (`entry_write_service.STATEMENT_SOURCE`).
- Fictional amounts in tests (the −300/−280 family); no production ids in test names.

## Review Focus

1. A line with an owner row **and** a pass-1 claim (merge): exact only when the union of rows sums to the line — Task 1 `test_merge_holders_recheck_exact_over_the_union`.
2. Holder row whose `entry_id` was SET NULL and whose snapshot id no longer resolves → not exact, line kept — Task 2 `test_guard_holders_set_null_row_is_never_exact`.
3. Holder is a fee **child** of a member of this group (printed-alone child line) → related, line kept — Task 1 `test_unrelated_exact_treats_a_members_child_as_related`.
4. First run on a fresh statement must already release the group (two passes), and a second run must write identical coverage and cases — Task 2 `test_two_passes_release_a_group_on_the_first_run_and_are_idempotent`.
5. Validator parity when the member's own line is claimed in pass 2 only — Task 3 `test_policy_admits_the_member_reconcile_released_through_pass_one`.

---

### Task 1: `Holder`, the guard filter and the pure builders (`matching.py`)

**Files:**
- Modify: `services/accounting-service/app/services/statements/matching.py` (`Entry` ~:62, `printed_whole` :479, `statement_context` :511, `match` :808)
- Test: `services/accounting-service/tests/unit/test_statement_matching.py` (helpers `L`, `E`, `S`, `R`, `D`, `d` at the top; existing guard tests ~:960)

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Holder: entry_ids: frozenset[int]; stand_in: bool; exact: bool`
  - `Entry.source: str | None = None` (new trailing field)
  - `_unrelated_exact(holder: Holder, g: Group, by_id: dict[int, Entry]) -> bool` — `holder.exact and not holder.stand_in and not (holder.entry_ids & related_ids(g, by_id))` where related = `g.member_ids ∪ {c.id for m in g.member_ids if m in by_id for c in _kids(by_id[m])}`.
  - `printed_whole(lines_all, entries, groups, rules, aside, aside_children, account_currency=None, holders: Mapping[int, Holder] | None = None) -> frozenset[int]` — per group, scan only the lines whose holder (if any) is not `_unrelated_exact` for it.
  - `statement_context(kind, lines, lines_all, entries, groups, rules, account_currency, holders=None) -> Context`
  - `match(..., lines_all=None, holders=None) -> MatchResult`
  - `claim_holders(result: MatchResult, lines_all: list[Line], entries: list[Entry], stand_in: Callable[[Entry], bool]) -> dict[int, Holder]` — one `Holder` per claimed line of `result.claims`: `entry_ids` = the representation's `Row.entry_id`s (child rows included); `exact` = signed Σ `Row.flow` `_same` `line.flow`, or the foreign rule over the top-level entries among the rows (`by_id`); `stand_in` = any `stand_in(by_id[id])` for the row ids that are top-level entries (child ids are never stand-ins). A claimed line absent from `lines_all` is skipped.
  - `merge_holders(stable: Mapping[int, Holder], own: Mapping[int, Holder]) -> dict[int, Holder]` — a line present on one side only keeps that `Holder`; a line present on **both** sides gets `entry_ids` united, `stand_in` or-ed and `exact = False` (fail-closed: two row sets cannot both sum to the line, and the merge has no flows to re-check; spec §4.1 is corrected to this rule in Task 4).
- Consumes: `_same`, `_kids`, `_date_distance`, `_prints_reading`, `group_readings` (unchanged).

- [ ] **Step 1: Failing unit tests (append after `test_printed_whole_fails_closed_without_the_other_member`)**

```python
H = m.Holder
def _pw(lines, ents, holders): return m.printed_whole(lines, ents, {5: m.Group(5, "split", (21, 22))}, R, frozenset(), frozenset(), holders=holders)
SPLIT = lambda: [E(21, d, "-300", group_id=5), E(22, d, "-280", group_id=5)]

def test_printed_whole_skips_a_line_an_unrelated_entry_explains_exactly():        # shapes 1/2
    near = [L(1, d, "-575")]
    assert _pw(near, SPLIT(), None) == {5}
    assert _pw(near, SPLIT(), {1: H(frozenset({99}), False, True)}) == frozenset()

def test_printed_whole_keeps_a_stand_in_held_line():                              # shape 3
    assert _pw([L(1, d, "-580")], SPLIT(), {1: H(frozenset({99}), True, True)}) == {5}

def test_printed_whole_keeps_an_inexact_holder_and_a_missing_holder():            # shapes 5/7
    assert _pw([L(1, d, "-575")], SPLIT(), {1: H(frozenset({99}), False, False)}) == {5}
    assert _pw([L(1, d, "-575")], SPLIT(), {2: H(frozenset({99}), False, True)}) == {5}

def test_unrelated_exact_treats_a_member_and_a_members_child_as_related():        # shape 6
    ents = SPLIT(); ents[0] = E(21, d, "-300", group_id=5, children=(m.Child(210, "fee", D("-10")),))
    g = m.Group(5, "split", (21, 22)); by_id = {e.id: e for e in ents}
    assert not m._unrelated_exact(H(frozenset({21}), False, True), g, by_id)
    assert not m._unrelated_exact(H(frozenset({210}), False, True), g, by_id)
    assert m._unrelated_exact(H(frozenset({99}), False, True), g, by_id)

def test_claim_holders_marks_conserving_claims_exact_and_members_related():
    ents = [E(99, d, "-575"), *SPLIT()]
    lines = [L(1, d, "-575"), L(2, d, "-300")]
    res = m.match("card", lines, ents, {5: m.Group(5, "split", (21, 22))}, {}, lambda e: None, R, "TWD")
    own = m.claim_holders(res, lines, ents, stand_in=lambda e: e.id == 42)
    assert own == {1: H(frozenset({99}), False, True)}            # pass 1: group whole, 99 claims line 1, line 2 open
    res2 = m.match("card", lines, ents, {5: m.Group(5, "split", (21, 22))}, {}, lambda e: None, R, "TWD", holders=own)
    assert {c.line_id: c.representation.rows[0].entry_id for c in res2.claims} == {1: 99, 2: 21}
    assert m.claim_holders(res2, lines, ents, stand_in=lambda e: False)[2] == H(frozenset({21}), False, True)

def test_claim_holders_foreign_exactness_uses_the_entries_original_amount():
    ents = [E(99, d, "-3010", original_amount="-15000", original_currency="JPY")]
    lines = [L(1, d, "-3010", foreign_amount="-15000", foreign_currency="JPY")]
    res = m.match("card", lines, ents, {}, {}, lambda e: None, R, "TWD")
    assert m.claim_holders(res, lines, ents, stand_in=lambda e: False) == {1: H(frozenset({99}), False, True)}

def test_merge_holders_recheck_exact_over_the_union():
    a = {1: H(frozenset({7}), False, True), 2: H(frozenset({8}), True, True)}
    b = {1: H(frozenset({9}), False, True), 3: H(frozenset({10}), False, True)}
    assert m.merge_holders(a, b) == {1: H(frozenset({7, 9}), False, False), 2: a[2], 3: b[3]}
```
(`L(... foreign_amount=, foreign_currency=)` and `E(... children=)` — check the helper signatures at the file top and add the kwargs if a helper lacks them.)

- [ ] **Step 2: Run, expect failures** — `pytest tests/unit/test_statement_matching.py -q -k "holder or unrelated_exact or claim_holders or merge_holders"` → `AttributeError: Holder` / `TypeError: holders`.

- [ ] **Step 3: Implement** `Holder`, `Entry.source`, `_unrelated_exact`, the `holders` parameter on `printed_whole` / `statement_context` / `match`, `claim_holders`, `merge_holders` per Interfaces. `printed_whole`'s docstring gains one sentence naming the spec section; the holder filter is applied inside its scan only.

- [ ] **Step 4: Run the whole unit file** — `pytest tests/unit/test_statement_matching.py -q` → all pass (the 154 existing + new).

- [ ] **Step 5: Commit** — `feat(matching): printed-whole guard skips lines an unrelated entry explains exactly (#136)`.

---

### Task 2: Stable holders from the DB and the two-pass reconcile (`reconciliation_service.py`)

**Files:**
- Modify: `services/accounting-service/app/services/reconciliation_service.py` (`entry_view` :196, `guard_lines` :157, `reconcile` :840–870)
- Test: `services/accounting-service/tests/integration/test_reconciliation_service.py` (helpers `_card`, `_submit`, `_line`, `_owner_cover` :1518, `_claims_of` :1538, `_active`, `_cases`, `_reconcile`, `_cover_elsewhere` :1529; existing guard tests :1641–1693)

**Interfaces:**
- Consumes: Task 1 (`matching.Holder`, `claim_holders`, `merge_holders`, `match(..., holders=)`); `stand_ins.stand_in_entry_ids` (already loaded at :271 inside `load_candidates` — expose it: `load_candidates` returns are unchanged; add a module-level helper `_stand_in_predicate(db, accounts) -> Callable[[matching.Entry], bool]` = `lambda e: e.stand_in or e.id in ids or e.source == STATEMENT_SOURCE`, where `ids = stand_ins.stand_in_entry_ids(db, accounts)`).
- Produces:
  - `entry_view` sets `source=e.source`.
  - `guard_holders(db, statement, accounts, lines_all: list[matching.Line]) -> dict[int, matching.Holder]` — active `StatementCoverage` rows for `{l.id for l in lines_all}` in chunks of 200; rows with `statement_id == statement.id` kept only when `match_kind in OWNED_KINDS`; `entry_ids` per the SET-NULL fallback; one `LedgerEntry` query for the ids (`id, amount, original_amount, original_currency, source`); `stand_in` via the predicate above on ids (`stand_in_entry_ids ∪ source == "statement"`); `exact` per Global Constraints, `False` when any id is unresolved. Read only.
  - `reconcile()` two passes (replace the single `matching.match(...)` call at :862–864):
    ```python
    lines_all = guard_lines(db, statement, accounts, rules, all_lines)
    stable = guard_holders(db, statement, accounts, lines_all)
    args = (statement.kind, [_as_line(row) for row in line_rows], entries, groups, plan_map(db, statement.account_id),
            lambda e: proposed_fx_fee(accounts_by_id[e.account_id], e.flow), rules, statement.currency)
    first = matching.match(*args, lines_all=lines_all, holders=stable)
    own = matching.claim_holders(first, lines_all, entries, _stand_in_predicate(db, accounts))
    result = matching.match(*args, lines_all=lines_all, holders=matching.merge_holders(stable, own))
    ```
    `plan_map` / `proposed_fx_fee` are called once; everything after uses `result`.

- [ ] **Step 1: Flip the two fixtures that rely on a plain owner entry acting as a stand-in** — in `test_reconcile_keeps_a_group_whole_when_its_sum_line_is_owner_covered` and the `stand_in` branch of `test_reconcile_keeps_a_group_whole_when_its_sum_line_is_on_the_previous_statement`, seed the 代墊 entry with `source="statement"` (spec §3 stand-in; the docstrings say "stand-in shape"). Add a sibling test:

```python
def test_reconcile_releases_a_group_whose_near_line_the_owner_matched_to_an_unrelated_entry(seed, db_session, run):
    """Spec #136 shape 5 vs owner-trusted: the −575 line is owner-matched to a plain −575 entry (exact) → unrelated →
    the group is apart and the −300 line claims the member. The same owner match on a −570 entry (inexact,
    amount_differs) keeps the group whole."""
```
with two `_submit` scenarios (or `parametrize("other", ["-575", "-570"])`): assert `_claims_of` contains `(line.id, m1.id, None, "auto")` for −575 and no member claim for −570.

- [ ] **Step 2: Failing integration tests (append after :1693)**

```python
def test_two_passes_release_a_group_on_the_first_run_and_are_idempotent(seed, db_session, run):
    """Shapes 2/9/10: a −575 standalone entry and the −300/−280 split; lines −575 and −300. First run: pass 1 claims
    −575 for the standalone (group whole under stable holders = none), pass 2 sees that claim → apart → −300 claims
    the member. Second run: identical claims and cases (its own auto rows are never read as holders)."""
    ... assert sorted(_claims_of(db_session, stmt.id)) == sorted([(l575.id, other.id, None, "auto"), (l300.id, m1.id, None, "auto")])
    ... before = snapshot of claims + cases; _reconcile(...); assert unchanged

def test_guard_holders_counts_a_neighbouring_auto_row_and_ignores_this_statements_own(seed, db_session, run):
    """Shape 4/10 at the function level: build August (−575 auto-claimed by a standalone) and September (−300 member
    line); `guard_holders(db, september, accounts, guard_lines(...))` has the August line with exact=True and no entry
    for September's own auto rows after a reconcile."""

def test_guard_holders_set_null_row_is_never_exact(seed, db_session, run):
    """Shape 7: owner-cover a line, delete the entry (SET NULL, snapshot keeps the id) → Holder(exact=False) → the
    group stays whole on reconcile."""

def test_guard_holders_sums_the_rows_of_a_group_reading(seed, db_session, run):
    """Shape 8: another split group (−400/−175) claimed the −575 line as a group reading (two coverage rows); their
    flows sum to the line → Holder(exact=True, stand_in=False) with both ids; the −300/−280 group is released."""

def test_reconcile_keeps_a_group_whole_when_a_real_stand_in_holds_the_sum_line_on_the_neighbour(seed, db_session, run):
    """Shape 4: August's −580 sum line is held by a `source="statement"` entry (what the policy books) → whole in
    September; the −300 line stays line_unmatched with no member candidate."""
```
Use the existing helpers; the SET-NULL deletion goes through `db_session.delete(entry)` + commit (FK `ondelete="SET NULL"` on `statement_coverage.entry_id`, models/statements.py:270).

- [ ] **Step 3: Run, expect failures** — `pytest tests/integration/test_reconciliation_service.py -q -k "two_passes or guard_holders or real_stand_in or unrelated_entry"`.

- [ ] **Step 4: Implement** `entry_view` source, `_stand_in_predicate`, `guard_holders`, the two-pass `reconcile()`. Update the module docstring's idempotency paragraph (:13–20) with one sentence on the two passes.

- [ ] **Step 5: Run the file and the policy/actions suites that exercise reconcile** — `pytest tests/integration/test_reconciliation_service.py tests/integration/test_reconciliation_actions_engine.py -q` → pass.

- [ ] **Step 6: Commit** — `feat(reconciliation): stable guard holders and a two-pass match (#136)`.

---

### Task 3: Validator parity (`policy_service._single_admissible`)

**Files:**
- Modify: `services/accounting-service/app/services/policy_service.py` (:500–530)
- Test: `services/accounting-service/tests/integration/test_reconciliation_policy.py` (helpers `_validator_refusal` :702, `_owner_cover` :756; shape at :765)

**Interfaces:**
- Consumes: Task 1 + Task 2 (`rec.guard_holders`, `rec._stand_in_predicate`, `matching.match`, `claim_holders`, `merge_holders`). The validator needs `plan_map` and the fee lambda for pass 1: reuse `rec.plan_map(db, statement.account_id)` and `lambda e: rec.proposed_fx_fee(accounts_by_id[e.account_id], e.flow)` with `accounts_by_id` built as reconcile does (:840).
- Produces: `match_ctx = matching.statement_context(..., holders=merged)` where `merged = merge_holders(stable, claim_holders(pass1, lines_all, entries, pred))` and `pass1 = matching.match(statement.kind, claimable_lines, entries, groups, plan_map, fee, rules, currency, lines_all=lines_all, holders=stable)`. Docstring: one sentence on parity through pass 1.

- [ ] **Step 1: Failing tests** (mirror `_apart_member_case` :560 and the apply test :590)

```python
def _held_near_line_case(seed, db, run, holder_source="manual"):
    """Split −300 (card) / −280 (錢包, other account); a standalone −575 the same day and a −575 line it claims exactly
    (within 5 % of the −580 total: the only thing that could keep the group whole); a second standalone −300 so the
    −300 line is ambiguous between it and the member (an open case to hang a proposal on). `holder_source="statement"`
    seeds the −575 entry as a stand-in instead."""
    ... return SimpleNamespace(stmt, line300, case, member, twin, line575, holder)

def test_policy_admits_the_member_reconcile_released_through_pass_one(seed, db_session, run):
    s = _held_near_line_case(seed, db_session, run)
    s.twin.amount = Decimal("-301"); db_session.commit()          # only the member conserves the −300 line now
    proposal = _agent_proposal(db_session, s.case, s.line300, s.member.id)
    _set_policy(db_session, MATCH_ON)
    result = policy_service.run_policy(db_session, s.stmt.id)
    (row,) = _actions(db_session)
    assert result.applied == [row.id] and (row.action, row.proposal_id) == ("match", proposal.id)

def test_policy_refuses_the_member_when_a_real_stand_in_holds_the_near_line(seed, db_session, run):
    s = _held_near_line_case(seed, db_session, run, holder_source="statement")
    _owner_cover(db_session, s.stmt, s.line575, [s.holder])
    rec.reconcile(db_session, s.stmt.id); db_session.commit()
    (case,) = [c for c in _cases(db_session, s.stmt.id, "open") if c.line_id == s.line300.id]
    assert case.kind == "line_unmatched" or {c["entry_id"] for c in case.candidates} == {s.twin.id}   # whole: member never listed
    assert _validator_refusal(db_session, s.stmt.id, case, s.member.id).startswith("not_single_admissible")
```
Under PR #130 the first test refuses (the −575 line keeps the group whole, "0 admissible representation(s)"); under #136 it applies.

- [ ] **Step 2: Run, expect failures.** — `pytest tests/integration/test_reconciliation_policy.py -q -k "pass_one or real_stand_in"`.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** `pytest tests/integration/test_reconciliation_policy.py tests/integration/test_reconciliation_service.py -q` → pass.
- [ ] **Step 5: Commit** — `feat(policy): validator builds the guard holders through pass one (#136)`.

---

### Task 4: Docs — errata, base-spec pointer, full suites

**Files:**
- Modify: `docs/superpowers/specs/2026-10-24-reconciliation-split-members-design.md` (§3 "Printed whole" paragraph — one pointer line to the #136 spec; §9 one erratum line)
- Modify: `docs/superpowers/specs/2026-10-24-reconciliation-guard-holders-design.md` (§6: the two flipped fixtures and the merge rule "both sides present → not exact"; §9 Errata header for execution findings)

- [ ] **Step 1: Edit the two spec files as above.**
- [ ] **Step 2: Full backend suites** — `pytest tests/unit -q` and `pytest tests/integration -q` → green; paste counts into the commit body.
- [ ] **Step 3: Commit** — `docs(reconciliation): #136 errata and base-spec pointer`.

---

## Rollout (controller, after the final review; not a subagent task)

1. Bring `~/reports/home-hub-pr130-api-20261024/sim136.py` to the spec arithmetic (signed Σ over all active rows of a line; this statement's own `auto` rows stand in for pass-1 claims; disjointness against members ∪ children; unresolved entry → not exact; stand-in = `stand_in_entry_ids ∪ source = statement`) and run it read-only against production. Pass: groups 4316 and 4300 flip; no group whose hit line has a stand-in holder flips; the flip list (by group) goes into the PR body.
2. PR → Multica review (dev-astra) → owner merge → deploy API (`deploy-130.sh <full sha>` pattern) → reconcile statements 128 and 229 → compare open cases (155 before) and the member outcomes against spec §5.
