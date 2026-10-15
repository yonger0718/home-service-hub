# Statement Reconciliation R6a (matcher rules) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the matcher's exact-amount window actually work, gate receivable/payable entries, relax the near rule on the same day, add a 4–15-day date-drift proposal, tag bank-only lines by line kind, widen the candidate population, and ship a read-only dry-run that proves the case delta on the live database before merge.

**Architecture:** All rule changes live in the pure matcher `app/services/statements/matching.py` (no DB) and are exercised by unit tests with the existing `L`/`E`/`run` helpers. The orchestrator `app/services/reconciliation_service.py` only changes how it loads the population window and how it coerces rules from settings; `app/services/settings_service.py` bumps the rules version and validates one new key. A standalone script pair under `scripts/` dumps desired cases from a baseline checkout and from the working tree and diffs them.

**Tech Stack:** Python 3.13, FastAPI service, SQLAlchemy 2, pytest (unit: no DB; integration: throw-away Postgres database per session via `tests/conftest.py`), Decimal arithmetic.

**Spec:** `docs/superpowers/specs/2026-10-15-reconciliation-r6-design.md` §1 (R6a.1–R6a.8) and the population-window paragraph of §2 R6b.0. Parent: `docs/superpowers/specs/2026-10-08-statement-reconciliation-design.md` §6–§7.

## Global Constraints

- Branch `feat/reconciliation-r6` in worktree `/home/opc/workspace/home-hub-r6`; run everything from `/home/opc/workspace/home-hub-r6/services/accounting-service` with `.venv` there (create with `uv venv .venv && uv pip install --python .venv/bin/python -e . -c ../../requirements-worker-constraints-aarch64-py313.txt` if absent — check with `ls .venv/bin/pytest`); the repo-root `.env` copy is present (mode 600) and the tests read it.
- No migration, no new case kind, no new DB column (spec §1 intro, §2 intro).
- Matcher stays pure: `matching.py` imports no DB, no I/O.
- Rule defaults verbatim from the spec: `accept 0.80`, `margin 0.05`, `ambiguous_floor 0.50`, `exact_window_days 3`, `near_tolerance_pct 0.05`, `near_tolerance_abs 10`, `near_window_days 5`, `foreign_window_days 5`, `drift_window_days 15`, `candidate_window_days 10`; `rules_version` becomes `r6a-1` and later `r6a-<version>`.
- Score for exact-amount representations: `0.80 + 0.10·(1 − dist/exact_window_days) + 0.10·text` inside the window, `0` outside; `exact_window_days == 0` → date term `0.10` at `dist == 0`.
- Population window: `population_window_days = max(candidate_window_days, drift_window_days, near_window_days, foreign_window_days)`; `candidate_window_days` keeps its meaning for installments.
- No owner financial data in tests, fixtures, docs, commit messages or dry-run summaries (synthetic data only; the dry-run detail file is mode 600 under `~/reports/`).
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Full suite before the final review: `.venv/bin/python -m pytest -q -p no:cacheprovider` (expect the R5 count 924–926 plus the new tests, 0 failed).

## Review Focus

1. A stored rules override that sets `candidate_window_days` below the drift window (e.g. 5): the population must still load `period ± 15`, else drift cases silently vanish — test in Task 7.
2. `exact_window_days = 0` with a line whose `txn_date` equals the entry's `entry_date` while `posted_date` differs by 2 days: `_date_distance` takes the minimum, so the representation exists at distance 0 and scores `0.90 + text` — test in Task 1.
3. A split-group representation and a standalone representation for the same line on the same day with equal text: both score equally under the new formula and must stay `ambiguous` (the margin test, not first-match) — test in Task 2.
4. Near rule on tiny amounts: `max(abs 10, 5 %)` means a 50 vs 58 pair (16 %) is a near candidate on the same day; the case must never claim and must carry `fee_matches: False` — test in Task 4.
5. Drift candidates must exclude entries already claimed by an earlier line of the same run and entries that are not standalone (group members, children, installment instances); a claimed entry must not appear in `candidates` — test in Task 5.

---

### Task 1: Score rescale, margin default, zero window

**Files:**
- Modify: `app/services/statements/matching.py:89-102` (`Rules.margin` default), `:210-216` (`_score`)
- Test: `tests/unit/test_statement_matching.py`

**Interfaces:**
- Consumes: `_score(line, entries, window) -> (Decimal, tuple[str, ...])`, `_text(line, entries)`, `_date_distance(line, entry)`.
- Produces: same signature, new formula; `Rules.margin == Decimal("0.05")`. Later tasks rely on "any conserved exact representation inside the window scores ≥ 0.80".

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_statement_matching.py`:

```python
def test_exact_amount_three_days_apart_claims_without_merchant_text():
    # R6a.1: the configured 3-day window must work; under the old formula distance 3 scored 0.675 < accept
    res = run([L(1, date(2026, 9, 3), "-580", merchant="")], [E(10, date(2026, 9, 6), "-580", merchant="")])
    assert [c.representation.rows[0].entry_id for c in res.claims] == [10]
    assert res.cases == []


def test_exact_amount_two_days_apart_claims_without_merchant_text():
    res = run([L(1, date(2026, 9, 3), "-580", merchant="")], [E(10, date(2026, 9, 5), "-580", merchant="")])
    assert [c.representation.rows[0].entry_id for c in res.claims] == [10]


def test_exact_amount_four_days_apart_is_not_a_representation():
    res = run([L(1, date(2026, 9, 3), "-580", merchant="")], [E(10, date(2026, 9, 7), "-580", merchant="")])
    assert res.claims == [] and res.cases[0].line_id == 1 and res.cases[0].kind != "ambiguous"


def test_score_orders_by_date_then_text_on_the_new_scale():
    score0, _ = m._score(L(1, date(2026, 9, 3), "-580", merchant=""), (E(10, date(2026, 9, 3), "-580", merchant=""),), 3)
    score3, _ = m._score(L(1, date(2026, 9, 3), "-580", merchant=""), (E(10, date(2026, 9, 6), "-580", merchant=""),), 3)
    score_text, _ = m._score(L(1, date(2026, 9, 3), "-580", merchant="全聯 大安"), (E(10, date(2026, 9, 3), "-580", merchant="全聯 大安"),), 3)
    assert (score0, score3, score_text) == (D("0.9000"), D("0.8000"), D("1.0000"))


def test_closer_candidate_wins_when_two_days_apart_with_equal_text():
    entries = [E(10, date(2026, 9, 5), "-580", merchant=""), E(11, date(2026, 9, 3), "-580", merchant="")]
    res = run([L(1, date(2026, 9, 3), "-580", merchant="")], entries)
    assert [c.representation.rows[0].entry_id for c in res.claims] == [11]


def test_candidates_one_day_apart_with_equal_text_stay_ambiguous():
    entries = [E(10, date(2026, 9, 4), "-580", merchant=""), E(11, date(2026, 9, 3), "-580", merchant="")]
    res = run([L(1, date(2026, 9, 3), "-580", merchant="")], entries)
    assert res.claims == [] and res.cases[0].kind == "ambiguous" and {c["entry_id"] for c in res.cases[0].candidates} == {10, 11}


def test_text_can_reverse_the_date_order():
    # distance 0 / text 0 → 0.90; distance 2 / text 1 → 0.9333: inside the 0.05 margin → ambiguous, not the closer one
    entries = [E(10, date(2026, 9, 3), "-580", merchant="家樂福"), E(11, date(2026, 9, 5), "-580", merchant="全聯 大安")]
    res = run([L(1, date(2026, 9, 3), "-580", merchant="全聯 大安")], entries)
    assert res.claims == [] and res.cases[0].kind == "ambiguous"


def test_exact_tie_stays_ambiguous():
    entries = [E(10, date(2026, 9, 3), "-580", merchant=""), E(11, date(2026, 9, 3), "-580", merchant="")]
    res = run([L(1, date(2026, 9, 3), "-580", merchant="")], entries)
    assert res.claims == [] and res.cases[0].kind == "ambiguous"


def test_zero_window_scores_same_day_and_nothing_else():
    rules = dataclasses.replace(R, exact_window_days=0)
    same, _ = m._score(L(1, date(2026, 9, 3), "-580", merchant=""), (E(10, date(2026, 9, 3), "-580", merchant=""),), 0)
    next_day, reasons = m._score(L(1, date(2026, 9, 3), "-580", merchant=""), (E(10, date(2026, 9, 4), "-580", merchant=""),), 0)
    assert same == D("0.9000") and next_day == D("0") and reasons == ("date_out_of_window",)
    res = m.match("card", [L(1, date(2026, 9, 3), "-580", merchant="")], [E(10, date(2026, 9, 3), "-580", merchant="")], {}, {}, lambda e: None, rules, "TWD")
    assert [c.representation.rows[0].entry_id for c in res.claims] == [10]


def test_zero_window_uses_txn_date_distance_too():
    # Review Focus 2: txn_date == entry_date counts as distance 0 even when posted dates differ
    rules = dataclasses.replace(R, exact_window_days=0)
    line = L(1, date(2026, 9, 5), "-580", merchant="", txn_date=date(2026, 9, 3))
    res = m.match("card", [line], [E(10, date(2026, 9, 3), "-580", merchant="")], {}, {}, lambda e: None, rules, "TWD")
    assert [c.representation.rows[0].entry_id for c in res.claims] == [10]


def test_margin_default_is_five_hundredths():
    assert m.Rules(period_end=date(2026, 9, 30)).margin == D("0.05")


def test_two_installment_representations_with_a_text_gap_now_resolve():
    # the shared margin runs before _installment_case: two confirmed-plan reps whose text differs by >= 0.05 claim
    # the better one (under margin 0.15 they stayed ambiguous because text <= 0.10)
    entries = [E(40, date(2026, 9, 5), "-1000", merchant="APPLE STORE", instance=m.InstanceRef(definition_id=7, seq=2, times=12)),
               E(41, date(2026, 9, 5), "-1000", merchant="家樂福", instance=m.InstanceRef(definition_id=7, seq=2, times=12))]
    res = run([L(1, date(2026, 9, 5), "-1000", kind="installment", merchant="APPLE STORE", seq=2, total=12)], entries,
              plan_map={"APPLE STORE|12|1000.0000": 7})
    assert [c.representation.rows[0].entry_id for c in res.claims] == [40]


def test_claimed_by_earlier_line_hint_under_the_new_scale():
    # lines run in id order. Line 1 is 3 days from the entry (old score 0.675: no claim, one-candidate ambiguous),
    # line 2 is on the entry's day. Under R6a line 1 claims (0.80) and line 2 — which used to hold the exact claim —
    # sees exactly one shadow representation whose row is claimed → the hint names it (parent §6.3, newly reachable)
    lines = [L(1, date(2026, 9, 6), "-580", merchant=""), L(2, date(2026, 9, 3), "-580", merchant="")]
    res = run(lines, [E(10, date(2026, 9, 3), "-580", merchant="")])
    assert [c.line_id for c in res.claims] == [1]
    assert res.cases[0].line_id == 2 and res.cases[0].context == {"hint": "claimed_by_earlier_line", "entry_id": 10}
```

Also update `tests/integration/test_reconciliation_service.py:305` (`test_rules_for_coerces_settings_types`), which asserts the dataclass default `margin == Decimal("0.15")`:

```python
    assert rules.period_end == date(2026, 9, 30) and rules.margin == Decimal("0.05")
```

Then change the existing `test_same_day_same_amount_different_merchants_is_ambiguous` (the spec's R6a.1 ruling: a same-day text difference of 0.10 now resolves) to:

```python
def test_same_day_same_amount_merchant_text_resolves_on_the_new_scale():
    # R6a.1: text weighs 0.10, the margin is 0.05: on equal dates the better merchant match wins (global competition)
    res = run([L(1, date(2026, 9, 3), "-580", merchant="全聯 大安")], [E(10, date(2026, 9, 3), "-580", merchant="全聯 大安"), E(11, date(2026, 9, 3), "-580", merchant="家樂福")])
    assert [c.representation.rows[0].entry_id for c in res.claims] == [10]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_statement_matching.py tests/integration/test_reconciliation_service.py::test_rules_for_coerces_settings_types -q -p no:cacheprovider -k "three_days or two_days or four_days or new_scale or closer_candidate or one_day_apart or reverse_the_date or exact_tie or zero_window or margin_default or merchant_text_resolves or installment_representations_with_a_text_gap or earlier_line_hint_under or coerces_settings_types"`
Expected: RED for `three_days_apart`, `two_days_apart`, `new_scale`, `margin_default`, `merchant_text_resolves`, `installment_representations_with_a_text_gap`, `earlier_line_hint_under` and `coerces_settings_types` (`0.15 != 0.05`). The others (`four_days`, `closer_candidate`, `one_day_apart`, `reverse_the_date`, `exact_tie`, both `zero_window`) are already green on the old formula and stay as regression guards for the new one — they pin behaviour the rescale must not change (AGENT-130 Should).

- [ ] **Step 3: Implement the new score and margin default**

In `app/services/statements/matching.py`, change the `Rules` default:

```python
    margin: Decimal = Decimal("0.05")
```

and replace `_score`:

```python
def _score(line: Line, entries: tuple[Entry, ...], window: int) -> tuple[Decimal, tuple[str, ...]]:
    """R6a.1: any conserved exact-amount representation inside the window scores >= accept (0.80); the date term
    (0.10, linear to the window edge) and the merchant-text term (0.10) only order candidates. A zero window keeps
    same-day candidates only, with the full date term."""
    dist = min(_date_distance(line, e) for e in entries)
    if dist > window:
        return ZERO, ("date_out_of_window",)
    date_term = Decimal("0.10") if window == 0 else (Decimal("0.10") * (Decimal(1) - Decimal(dist) / Decimal(window))).quantize(Q)
    text = _text(line, entries)
    return (Decimal("0.80") + date_term + text).quantize(Q), (f"date_distance={dist}", f"text={text}")
```

Update the module docstring's "Known limit" paragraph by appending one sentence: `Since R6a the exact window is real: distance alone never drops an in-window representation below accept.`

- [ ] **Step 4: Run the unit file to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_statement_matching.py -q -p no:cacheprovider`
Expected: PASS. If `test_merchant_signal_breaks_the_tie` or group tests fail, read the assertion: a claim that moved because of the new scale is a real behaviour change and must be reconciled with the spec (R6a.1), not patched by loosening the test.

- [ ] **Step 5: Commit**

```bash
git add app/services/statements/matching.py tests/unit/test_statement_matching.py tests/integration/test_reconciliation_service.py
git commit -m "feat(reconciliation): R6a.1 exact-amount score rescale, margin 0.05, zero window

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Ambiguity needs two candidates

**Files:**
- Modify: `app/services/statements/matching.py:423-441` (`match()` classification)
- Test: `tests/unit/test_statement_matching.py`

**Interfaces:**
- Consumes: `match(statement_kind, lines, entries, groups, plan_map, fee_expected, rules, account_currency) -> MatchResult`.
- Produces: `ambiguous` only when `len(reps) >= 2`.

- [ ] **Step 1: Write the failing tests**

```python
def test_single_representation_below_accept_is_never_ambiguous():
    # R6a.2 contract: a lone candidate never stops a line as ambiguous; with accept raised to 0.95 a distance-0,
    # text-0 representation (0.90) is below accept and must fall through to the fallback chain
    rules = dataclasses.replace(R, accept=D("0.95"))
    res = m.match("card", [L(1, date(2026, 9, 3), "-580", merchant="")], [E(10, date(2026, 9, 3), "-580", merchant="")], {}, {}, lambda e: None, rules, "TWD")
    assert res.claims == [] and res.cases[0].kind != "ambiguous"


def test_group_and_standalone_same_day_equal_text_stay_ambiguous():
    # Review Focus 3: two representation shapes with equal scores compete globally; neither wins by position
    g = m.Group(id=5, kind="split", member_ids=(21, 22))
    entries = [E(10, date(2026, 9, 3), "-580", merchant=""), E(21, date(2026, 9, 3), "-300", group_id=5, merchant=""),
               E(22, date(2026, 9, 3), "-280", group_id=5, merchant="")]
    res = run([L(1, date(2026, 9, 3), "-580", merchant="")], entries, groups={5: g})
    assert res.claims == [] and res.cases[0].kind == "ambiguous"
    assert {(c["entry_id"], c["group_id"]) for c in res.cases[0].candidates} == {(10, None), (None, 5)}
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_statement_matching.py -q -p no:cacheprovider -k "never_ambiguous or group_and_standalone"`
Expected: `never_ambiguous` FAILS (`ambiguous` produced for one rep); `group_and_standalone` may already pass — keep it either way.

- [ ] **Step 3: Implement**

In `match()`, change the ambiguity line:

```python
        if len(reps) >= 2 and reps[0].score >= rules.ambiguous_floor:  # R6a.2: ambiguity needs two candidates
            result.cases.append(_ambiguous(line, reps))
            continue
```

- [ ] **Step 4: Run the unit file**

Run: `.venv/bin/python -m pytest tests/unit/test_statement_matching.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/statements/matching.py tests/unit/test_statement_matching.py
git commit -m "feat(reconciliation): R6a.2 ambiguity requires two representations

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Gates — receivable and payable

**Files:**
- Modify: `app/services/statements/matching.py:145-165` (`CARD_GATES`, `BANK_GATES`)
- Test: `tests/unit/test_statement_matching.py`

**Interfaces:**
- Consumes: `eligible_kinds(statement_kind, line_kind) -> frozenset[str]`, `_entry_class(entry)` (settlements map to `settlement_in`/`settlement_out`).
- Produces: `CARD_GATES["purchase"] == {"expense", "receivable"}`, `CARD_GATES["installment"] == {"expense", "receivable"}`, `BANK_GATES["withdrawal"] ⊇ {"receivable"}`, `BANK_GATES["deposit"] ⊇ {"payable"}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_card_purchase_matches_a_receivable_i_paid_for_someone():
    # R6a.3: a non-settlement receivable (amount < 0) conserves a purchase flow
    res = run([L(1, date(2026, 9, 3), "-1200")], [E(10, date(2026, 9, 3), "-1200", kind="receivable")])
    assert [c.representation.rows[0].entry_id for c in res.claims] == [10]


def test_card_purchase_never_matches_a_receivable_settlement():
    # a collection (amount > 0, is_settlement) is class settlement_in: not in the purchase gate, and the sign differs anyway
    res = run([L(1, date(2026, 9, 3), "-1200")], [E(10, date(2026, 9, 3), "1200", kind="receivable", is_settlement=True)])
    assert res.claims == [] and res.cases[0].kind == "line_unmatched"


def test_bank_withdrawal_matches_a_receivable_and_deposit_matches_a_payable():
    entries = [E(10, date(2026, 9, 3), "-1200", kind="receivable"), E(11, date(2026, 9, 4), "5000", kind="payable")]
    lines = [L(1, date(2026, 9, 3), "-1200", kind="withdrawal"), L(2, date(2026, 9, 4), "5000", kind="deposit")]
    res = run(lines, entries, kind="bank")
    assert sorted((c.line_id, c.representation.rows[0].entry_id) for c in res.claims) == [(1, 10), (2, 11)]


def test_gates_list_receivable_and_payable():
    assert m.eligible_kinds("card", "purchase") == frozenset({"expense", "receivable"})
    assert m.eligible_kinds("card", "installment") == frozenset({"expense", "receivable"})
    assert "receivable" in m.eligible_kinds("bank", "withdrawal") and "payable" in m.eligible_kinds("bank", "deposit")
    assert "payable" not in m.eligible_kinds("bank", "withdrawal") and "receivable" not in m.eligible_kinds("bank", "deposit")
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_statement_matching.py -q -p no:cacheprovider -k "receivable or payable"`
Expected: FAIL (no claims; gate sets differ).

- [ ] **Step 3: Implement**

```python
CARD_GATES = {
    "purchase": frozenset({"expense", "receivable"}),      # R6a.3: a receivable I paid for someone looks like a purchase
    "installment": frozenset({"expense", "receivable"}),
    "refund": frozenset({"refund"}),
    "payment": frozenset({"transfer_in"}),
    "fee": frozenset({"fee"}),
    "interest": frozenset({"interest"}),
    "reward": frozenset({"reward"}),
}
BANK_GATES = {
    "deposit": frozenset({"income", "transfer_in", "interest", "settlement_in", "payable"}),      # R6a.3: a payable I received
    "withdrawal": frozenset({"expense", "transfer_out", "fee", "settlement_out", "receivable"}),  # R6a.3: a receivable I paid
    "transfer_in": frozenset({"transfer_in"}),
    "transfer_out": frozenset({"transfer_out"}),
    "fee": frozenset({"fee"}),
    "interest": frozenset({"interest"}),
    "refund": frozenset({"refund"}),
    "reward": frozenset({"reward"}),
    "payment": frozenset({"settlement_out", "transfer_out"}),
    "purchase": frozenset({"expense"}),
}
```

(Settlements never reach these gates: `_entry_class` maps `is_settlement` rows to `settlement_in`/`settlement_out`, and the ledger constraint makes a receivable with amount > 0 and a payable with amount < 0 settlements.)

- [ ] **Step 4: Run the unit file**

Run: `.venv/bin/python -m pytest tests/unit/test_statement_matching.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/statements/matching.py tests/unit/test_statement_matching.py
git commit -m "feat(reconciliation): R6a.3 gate receivable and payable entries

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Near rule — 5 %, no token rule on the same day

**Files:**
- Modify: `app/services/statements/matching.py:95-96` (`near_tolerance_pct` default), `:382-403` (`_near_case`)
- Test: `tests/unit/test_statement_matching.py`

**Interfaces:**
- Consumes: `_near_case(kind, line, entries, claimed, rules) -> Case | None`.
- Produces: same; `Rules.near_tolerance_pct == Decimal("0.05")`; token overlap required only when `dist >= 1`.

- [ ] **Step 1: Write the failing tests**

```python
def test_near_same_day_needs_no_merchant_overlap():
    # R6a.4: a MOZE memo rarely repeats the statement merchant text; on the same day amount proximity is enough
    res = run([L(1, date(2026, 9, 3), "-1030", merchant="AMAZON JP")], [E(10, date(2026, 9, 3), "-1000", merchant="日亞")])
    case = res.cases[0]
    assert case.kind == "amount_delta" and case.entry_id == 10 and case.context["fee_matches"] is False
    assert case.context["residual"] == "-30.0000" and res.claims == []


def test_near_one_day_apart_still_needs_merchant_overlap():
    res = run([L(1, date(2026, 9, 4), "-1030", merchant="AMAZON JP")], [E(10, date(2026, 9, 3), "-1000", merchant="日亞")])
    assert res.cases[0].kind == "line_unmatched"
    res = run([L(1, date(2026, 9, 4), "-1030", merchant="AMAZON JP")], [E(10, date(2026, 9, 3), "-1000", merchant="AMAZON JP")])
    assert res.cases[0].kind == "amount_delta"


def test_near_tolerance_is_five_percent():
    assert m.Rules(period_end=date(2026, 9, 30)).near_tolerance_pct == D("0.05")
    res = run([L(1, date(2026, 9, 3), "-1049", merchant="")], [E(10, date(2026, 9, 3), "-1000", merchant="")])
    assert res.cases[0].kind == "amount_delta"
    # the tolerance base is the LINE amount (spec R6a.4 keeps `max(abs, pct·|line.flow|)`): 5 % of 1053 = 52.65 < 53
    res = run([L(1, date(2026, 9, 3), "-1053", merchant="")], [E(10, date(2026, 9, 3), "-1000", merchant="")])
    assert res.cases[0].kind == "line_unmatched"


def test_near_small_amounts_use_the_absolute_tolerance_and_never_claim():
    # Review Focus 4: 50 vs 58 is 16 % but within abs 10 — a proposal, never a claim
    res = run([L(1, date(2026, 9, 3), "-58", merchant="")], [E(10, date(2026, 9, 3), "-50", merchant="")])
    assert res.claims == [] and res.cases[0].kind == "amount_delta" and res.cases[0].context["fee_matches"] is False


def test_near_applies_to_card_payment_lines_through_peer_ok():
    # a payment line meets a transfer_in leg whose peer is known not to be a card; same day, 1 % off
    ok = E(10, date(2026, 9, 3), "20200", kind="transfer_in", peer_is_card=False)
    card_peer = E(11, date(2026, 9, 3), "20200", kind="transfer_in", peer_is_card=True)
    res = run([L(1, date(2026, 9, 3), "20000", kind="payment", merchant="")], [ok, card_peer])
    assert res.cases[0].kind == "amount_delta" and [c["entry_id"] for c in res.cases[0].candidates] == [10]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_statement_matching.py -q -p no:cacheprovider -k "near_"`
Expected: `needs_no_merchant_overlap`, `five_percent`, `small_amounts` and `card_payment` FAIL; `one_day_apart` passes already (kept as a regression guard).

- [ ] **Step 3: Implement**

Change the default and the loop:

```python
    near_tolerance_pct: Decimal = Decimal("0.05")
```

```python
def _near_case(kind: str, line: Line, entries: list[Entry], claimed: set[int], rules: Rules) -> Case | None:
    """R6a.4: same amount within max(abs, pct) and the near window. Merchant-token overlap (half of the union) is
    required only for candidates on another day; on the same day amount proximity is enough. Never claims."""
    gate = eligible_kinds(kind, line.line_kind)
    mine = tokens(line.merchant_norm)
    tol = max(rules.near_tolerance_abs, (rules.near_tolerance_pct * abs(line.flow)).quantize(Q))
    found: list[tuple[Decimal, int, int, Decimal]] = []
    for e in entries:
        if e.id in claimed or _entry_class(e) not in gate or not _standalone(e) or not _peer_ok(kind, line, e):
            continue
        delta = (line.flow - e.flow).quantize(Q)
        dist = _date_distance(line, e)
        if abs(delta) > tol or dist > rules.near_window_days:
            continue
        if dist >= 1:
            theirs = tokens(normalise(e.merchant or "")) | tokens(normalise(e.name or ""))
            if not mine or not theirs or 2 * len(mine & theirs) < len(mine | theirs):
                continue
        found.append((abs(delta), dist, e.id, delta))
    if not found:
        return None
    found.sort()
    _, _, best_id, best_delta = found[0]
    cands = tuple({"entry_id": eid, "reason": "near", "residual": str(delta)} for _, _, eid, delta in found)
    return Case(line.id, best_id, "amount_delta", cands, {"residual": str(best_delta), "fee_matches": False, "entry_id": best_id})
```

- [ ] **Step 4: Run the unit file**

Run: `.venv/bin/python -m pytest tests/unit/test_statement_matching.py -q -p no:cacheprovider`
Expected: PASS (`test_near_prefers_smaller_delta_then_date_and_lists_every_candidate` still passes: its candidates share merchant text).

- [ ] **Step 5: Commit**

```bash
git add app/services/statements/matching.py tests/unit/test_statement_matching.py
git commit -m "feat(reconciliation): R6a.4 near rule 5 %, same-day candidates need no merchant overlap

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Date drift — `_drift_case` and `drift_window_days`

**Files:**
- Modify: `app/services/statements/matching.py:89-102` (`Rules.drift_window_days`), new `_drift_case` after `_near_case`, `match()` fallback chain
- Test: `tests/unit/test_statement_matching.py`, `tests/integration/test_reconciliation_service.py`

**Interfaces:**
- Consumes: `eligible_kinds`, `_standalone`, `_peer_ok`, `_same`, `_date_distance`, `Case`.
- Produces: `Rules.drift_window_days: int = 15`; `_drift_case(kind, line, entries, claimed, rules) -> Case | None` emitting `Case(line.id, best_id, "amount_delta", candidates, {"hint": "date_drift", "date_distance": int, "entry_id": best_id, "residual": "0", "fee_matches": False})`; candidates `({"entry_id", "reason": "date_drift", "date_distance"}, ...)` ordered by distance then id. Task 7 reads `drift_window_days` for the population window; Task 8's dump keys on `context.hint`.

- [ ] **Step 1: Write the failing unit tests**

```python
def test_drift_window_default_and_settings_shape():
    assert m.Rules(period_end=date(2026, 9, 30)).drift_window_days == 15


def test_exact_amount_four_to_fifteen_days_apart_is_a_date_drift_proposal():
    for days, expect in ((4, "amount_delta"), (15, "amount_delta"), (16, "line_unmatched")):
        res = run([L(1, date(2026, 9, 3), "-580", merchant="")], [E(10, date(2026, 9, 3 + days), "-580", merchant="")])
        case = res.cases[0]
        assert case.kind == expect, days
        if expect == "amount_delta":
            assert res.claims == [] and case.entry_id == 10
            assert case.context == {"hint": "date_drift", "date_distance": days, "entry_id": 10, "residual": "0", "fee_matches": False}
            assert case.candidates == ({"entry_id": 10, "reason": "date_drift", "date_distance": days},)


def test_drift_candidates_are_ordered_by_distance_then_id_and_skip_claimed_and_grouped():
    # Review Focus 5: an entry claimed by an earlier line and a group member never appear
    g = m.Group(id=5, kind="split", member_ids=(30, 31))
    entries = [E(10, date(2026, 9, 3), "-580", merchant=""), E(12, date(2026, 9, 10), "-580", merchant=""),
               E(11, date(2026, 9, 10), "-580", merchant=""), E(30, date(2026, 9, 9), "-580", group_id=5, merchant=""),
               E(31, date(2026, 9, 9), "-1", group_id=5, merchant="")]
    lines = [L(1, date(2026, 9, 3), "-580", merchant=""), L(2, date(2026, 9, 3), "-580", merchant="")]
    res = run(lines, entries, groups={5: g})
    assert [c.representation.rows[0].entry_id for c in res.claims] == [10]
    drift = next(c for c in res.cases if c.line_id == 2)
    assert drift.kind == "amount_delta" and drift.context["hint"] == "date_drift"
    assert [c["entry_id"] for c in drift.candidates] == [11, 12]


def test_drift_runs_after_near_and_before_bank_only():
    # a near candidate on the same day wins over a drift candidate ten days away
    entries = [E(10, date(2026, 9, 3), "-1000", merchant=""), E(11, date(2026, 9, 13), "-1030", merchant="")]
    res = run([L(1, date(2026, 9, 3), "-1030", merchant="")], entries)
    assert res.cases[0].context["entry_id"] == 10 and "hint" not in res.cases[0].context
    # a fee line with a drift candidate is drift, not bank_only
    res = run([L(1, date(2026, 9, 3), "-5", kind="fee", merchant="跨行手續費")], [E(10, date(2026, 9, 10), "-5", kind="fee", merchant="")])
    assert res.cases[0].context["hint"] == "date_drift"


def test_exact_amount_four_days_apart_with_merchant_overlap_is_near_not_drift():
    # near runs first and accepts delta 0 inside its 5-day window when the merchant text overlaps; drift takes the
    # same pair only without text overlap. Both outcomes are proposals, never claims.
    res = run([L(1, date(2026, 9, 3), "-580", merchant="全聯 大安")], [E(10, date(2026, 9, 7), "-580", merchant="全聯 大安")])
    assert res.claims == [] and res.cases[0].kind == "amount_delta" and "hint" not in res.cases[0].context
    res = run([L(1, date(2026, 9, 3), "-580", merchant="全聯 大安")], [E(10, date(2026, 9, 7), "-580", merchant="家樂福")])
    assert res.cases[0].context["hint"] == "date_drift"


def test_drift_respects_the_gate_and_peer_rule():
    res = run([L(1, date(2026, 9, 3), "-580", merchant="")], [E(10, date(2026, 9, 10), "-580", kind="refund", merchant="")])
    assert res.cases[0].kind == "line_unmatched"
    card_peer = E(11, date(2026, 9, 10), "20000", kind="transfer_in", peer_is_card=True)
    res = run([L(1, date(2026, 9, 3), "20000", kind="payment", merchant="")], [card_peer])
    assert res.cases[0].kind == "line_unmatched"
```

- [ ] **Step 2: Write the failing integration test (drift case round-trips and resolves with `match`)**

In `tests/integration/test_reconciliation_service.py`, next to the other `run`/`seed` tests (helpers `_submit`, `_cases`, `_active`, `_line` come from `tests/integration/statement_fixtures.py`; `seed.account(name, currency="TWD", opening="0", **extra)` and `seed.entry(account, amount, *, kind="expense", day=..., source="manual", **extra)` are `tests/conftest.py:408,431`):

```python
def test_date_drift_case_is_written_and_resolves_with_match(seed, db_session, run):
    from app.services.reconciliation_actions import engine
    card = seed.account("drift 卡", is_credit=True)
    entry = seed.entry(card, "-580", day=date(2026, 9, 15), merchant="全聯")
    stmt, lines, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 5), "全聯", 580, "purchase")])
    case = next(c for c in _cases(db_session, stmt.id, "open") if c.kind == "amount_delta")
    assert case.context["hint"] == "date_drift" and case.context["date_distance"] == 10 and case.entry_id == entry.id
    assert case.candidates == [{"entry_id": entry.id, "reason": "date_drift", "date_distance": 10}]
    engine.apply_action(db_session, case_id=case.id, action="match", params={"line_id": lines[0].id, "entry_id": entry.id},
                        actor="owner", label="owner")
    db_session.commit()
    assert db_session.get(ReconciliationCase, case.id).status == "resolved" and len(_active(db_session, stmt.id)) == 1
```

(Line 5 Sep and entry 15 Sep: distance 10, inside the period, outside the near window; merchant text overlaps, so near would take it only inside 5 days.)

- [ ] **Step 3: Run both to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_statement_matching.py tests/integration/test_reconciliation_service.py -q -p no:cacheprovider -k "drift or near_not_drift"`
Expected: FAIL (`Rules` has no `drift_window_days`; cases are `line_unmatched`; the integration test finds no `amount_delta` case).

- [ ] **Step 4: Implement**

Add to `Rules` (after `near_tolerance_pct`):

```python
    drift_window_days: int = 15  # R6a.5: exact amount, farther than the exact window → a date_drift proposal
```

Add after `_near_case`:

```python
def _drift_case(kind: str, line: Line, entries: list[Entry], claimed: set[int], rules: Rules) -> Case | None:
    """R6a.5: a standalone, gated, unclaimed entry with the same amount and sign, farther than the exact window but
    within the drift window → an amount_delta proposal (residual 0) the owner confirms with `match`. Never claims."""
    gate = eligible_kinds(kind, line.line_kind)
    found: list[tuple[int, int]] = []
    for e in entries:
        if e.id in claimed or _entry_class(e) not in gate or not _standalone(e) or not _peer_ok(kind, line, e):
            continue
        if not _same(e.flow, line.flow):
            continue
        dist = _date_distance(line, e)
        if rules.exact_window_days < dist <= rules.drift_window_days:
            found.append((dist, e.id))
    if not found:
        return None
    found.sort()
    best_dist, best_id = found[0]
    cands = tuple({"entry_id": eid, "reason": "date_drift", "date_distance": dist} for dist, eid in found)
    return Case(line.id, best_id, "amount_delta", cands,
                {"hint": "date_drift", "date_distance": best_dist, "entry_id": best_id, "residual": "0", "fee_matches": False})
```

In `match()`, extend the fallback chain:

```python
        case = _foreign_case(statement_kind, line, entries, claimed, fee_expected, rules, account_currency) \
            or _near_case(statement_kind, line, entries, claimed, rules) \
            or _drift_case(statement_kind, line, entries, claimed, rules)
```

- [ ] **Step 5: Run both files to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_statement_matching.py tests/integration/test_reconciliation_service.py -q -p no:cacheprovider`
Expected: PASS. If `engine.apply_action` refuses with `not allowed for this case`, the case kind is wrong — `KIND_ACTIONS["amount_delta"]` includes `match` on main.

- [ ] **Step 6: Commit**

```bash
git add app/services/statements/matching.py tests/unit/test_statement_matching.py tests/integration/test_reconciliation_service.py
git commit -m "feat(reconciliation): R6a.5 date-drift proposals (amount_delta, residual 0) within 15 days

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: `bank_only` by line kind

**Files:**
- Modify: `app/services/statements/matching.py:406-412` (`_bank_only`), `match()` call site (`:452-455`)
- Test: `tests/unit/test_statement_matching.py`, `tests/integration/test_reconciliation_policy.py`

**Interfaces:**
- Consumes: `Rules.bank_only_patterns`.
- Produces: `_bank_only(line, rules) -> dict | None` returning `{"hint": "bank_only", "pattern": <str | None>}` for `line_kind ∈ {fee, interest, reward}`; `policy_service._system_entry_candidate` and `reconciliation_actions._system_line` consume the hint unchanged.

- [ ] **Step 1: Write the failing tests**

Unit:

```python
def test_bank_only_by_line_kind_without_any_pattern():
    res = run([L(1, date(2026, 9, 30), "12", kind="interest", merchant="INTEREST CREDIT")], [], kind="bank")
    assert res.cases[0].kind == "line_unmatched" and res.cases[0].context == {"hint": "bank_only", "pattern": None}


def test_bank_only_keeps_the_matched_pattern_as_annotation():
    res = run([L(1, date(2026, 9, 30), "-5", kind="fee", merchant="跨行手續費")], [])
    assert res.cases[0].context == {"hint": "bank_only", "pattern": "跨行手續費"}


def test_unknown_deposit_withdrawal_lines_are_never_bank_only():
    for kind in ("unknown", "deposit", "withdrawal"):
        res = run([L(1, date(2026, 9, 30), "12", kind=kind, merchant="利息")], [], kind="bank")
        assert "hint" not in res.cases[0].context, kind
```

Integration, in `tests/integration/test_reconciliation_policy.py` (helpers `_fee_statement`, `_set_policy`, `_booked`, `_line`, `DAY` exist there; the policy apply entry point is the one the file's existing `create_system_entry` tests call — copy its call shape):

```python
def test_policy_books_an_interest_line_without_a_pattern(seed, db_session, run):
    env = _fee_statement(seed, db_session, run, lines=[_line(1, DAY, "INTEREST CREDIT", 12, "interest")])
    assert env.cases[0].context == {"hint": "bank_only", "pattern": None}
    _set_policy(db_session, SYSTEM_ON)  # the file's constant enabling create_system_entry (see its first policy test, ~line 165)
    result = policy_service.run_policy(db_session, env.stmt.id)
    booked = _booked(db_session, env.card.id)
    (row,) = _actions(db_session)
    assert len(booked) == 1 and booked[0].kind == "interest" and result.applied == [row.id]
```

(`policy_service.run_policy(db, statement_id)` is the entry point the file's existing tests call at `test_reconciliation_policy.py:167`; `PolicyResult.applied` is a `list[int]` of action ids, `policy_service.py:82-83`, asserted the same way at `:170`.)

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_statement_matching.py -q -p no:cacheprovider -k bank_only` and `.venv/bin/python -m pytest tests/integration/test_reconciliation_policy.py -q -p no:cacheprovider -k without_a_pattern`
Expected: FAIL (`context == {}` for the interest line without a pattern; `_fee_statement` assertion fails).

- [ ] **Step 3: Implement**

```python
BANK_ONLY_KINDS = frozenset({"fee", "interest", "reward"})


def _bank_only(line: Line, rules: Rules) -> dict | None:
    """R6a.6: every fee/interest/reward line is bank-side automatic; the matched pattern (if any) only annotates."""
    if line.line_kind not in BANK_ONLY_KINDS:
        return None
    pattern = next((p for p in rules.bank_only_patterns if p in line.merchant_norm), None)
    return {"hint": "bank_only", "pattern": pattern}
```

and in `match()`:

```python
        bank_only = _bank_only(line, rules)
        if bank_only:
            result.cases.append(Case(line.id, None, "line_unmatched", (), bank_only))
            continue
```

- [ ] **Step 4: Run both files**

Run: `.venv/bin/python -m pytest tests/unit/test_statement_matching.py tests/integration/test_reconciliation_policy.py -q -p no:cacheprovider`
Expected: PASS. `policy_service._unchanged` compares `hint` and `line_kind`, not `pattern`, so `None` patterns are fine; if a policy test asserts a non-null `pattern` for a line that matched none, the test was asserting the old gap — update it to `None` and note it in the commit body.

- [ ] **Step 5: Commit**

```bash
git add app/services/statements/matching.py tests/unit/test_statement_matching.py tests/integration/test_reconciliation_policy.py
git commit -m "feat(reconciliation): R6a.6 bank_only hint by line kind, pattern as annotation

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Population window, rules version `r6a`, stale-override guard

**Files:**
- Modify: `app/services/reconciliation_service.py:81-97` (`rules_for`), `:671` and `:677` (`reconcile`, both `load_candidates` calls), `app/services/settings_service.py:66` (`RULES_VERSION`), `:253-258` (`reconciliation_rules`), `:296-299` (`update_reconciliation_settings` version string)
- Test: `tests/integration/test_reconciliation_service.py`, `tests/integration/test_reconciliation_policy.py` (none), `tests/unit/test_statement_matching.py` (none)

**Interfaces:**
- Consumes: `matching.Rules` (Task 5's `drift_window_days`), `settings_service.reconciliation_rules(db) -> dict`.
- Produces: `matching.population_window_days(rules) -> int`; `settings_service.RULES_VERSION == "r6a-1"`; `reconciliation_rules(db)` **and** `read_reconciliation_rules(db)` return the rules dict **plus** `"rules_version"`; a stored row that carries `rules` overrides but no `rules_version` reads as `r1b-1` (it was written before R6a); `rules_for(raw, period_end)` ignores `accept`/`margin`/`ambiguous_floor` overrides when `raw.get("rules_version", "").startswith("r1b")` and logs one warning per ignored key; `reconcile`, `worker/verify.py` and `policy_service` load candidates with `population_window_days(rules)`. Task 8 calls `population_window_days` through `load_candidates`.

- [ ] **Step 1: Write the failing tests**

In `tests/integration/test_reconciliation_service.py`:

```python
def test_population_window_is_the_max_of_the_rule_windows():
    from app.services.statements import matching as m
    rules = m.Rules(period_end=date(2026, 9, 30))
    assert m.population_window_days(rules) == 15
    small = dc_replace(rules, candidate_window_days=5, drift_window_days=15)   # Review Focus 1 (dc_replace is imported there)
    assert m.population_window_days(small) == 15
    wide = dc_replace(rules, candidate_window_days=20)
    assert m.population_window_days(wide) == 20


def test_stored_overrides_without_a_version_read_as_r1b(db_session):
    # a row PUT before R6a may carry rules overrides and no rules_version key: its scale overrides are stale too
    row = settings_service._reconciliation_row(db_session)
    row.data = {**(row.data or {}), "rules": {"margin": "0.15", "exact_window_days": 4}}
    row.data.pop("rules_version", None)
    db_session.flush()
    raw = settings_service.reconciliation_rules(db_session)
    assert raw["rules_version"] == "r1b-1" and settings_service.read_reconciliation_rules(db_session)["rules_version"] == "r1b-1"
    rules = rec.rules_for(raw, date(2026, 9, 30))
    assert rules.margin == Decimal("0.05") and rules.exact_window_days == 4
    assert settings_service.get_reconciliation_settings(db_session)["rules_version"] == "r1b-1"


def test_drift_window_is_validated_like_the_other_int_rules(db_session):
    with pytest.raises(ValidationError):
        settings_service.update_reconciliation_settings(db_session, {"dirty_enabled": False, "rules": {"drift_window_days": 10001}})
    with pytest.raises(ValidationError):
        settings_service.update_reconciliation_settings(db_session, {"dirty_enabled": False, "rules": {"drift_window_days": "15"}})


def test_rules_for_ignores_old_scale_overrides_with_a_warning(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="app.services.reconciliation_service"):
        rules = rec.rules_for({"accept": "0.95", "margin": "0.15", "exact_window_days": "4", "rules_version": "r1b-7"}, date(2026, 9, 30))
    assert rules.accept == Decimal("0.80") and rules.margin == Decimal("0.05") and rules.exact_window_days == 4
    assert sum("ignored stale rule override" in r.message for r in caplog.records) == 2
    fresh = rec.rules_for({"accept": "0.95", "rules_version": "r6a-9"}, date(2026, 9, 30))
    assert fresh.accept == Decimal("0.95")


def test_rules_defaults_in_settings(db_session):
    settings = settings_service.get_reconciliation_settings(db_session)
    assert settings["rules_version"] == "r6a-1"
    assert settings["rules"]["accept"] == "0.80" and settings["rules"]["margin"] == "0.05"
    assert settings["rules"]["candidate_window_days"] == 10 and settings["rules"]["drift_window_days"] == 15
    assert "period_end" not in settings["rules"] and "年費" in settings["rules"]["bank_only_patterns"]


def test_rule_change_bumps_rules_version_with_the_r6a_prefix(db_session):
    current = settings_service.get_reconciliation_settings(db_session)
    out = settings_service.update_reconciliation_settings(db_session, {"dirty_enabled": current["dirty_enabled"], "rules": {"drift_window_days": 20}})
    assert out["rules_version"] == f"r6a-{out['version']}" and out["rules"]["drift_window_days"] == 20
    assert settings_service.reconciliation_rules(db_session)["rules_version"] == out["rules_version"]


def test_drift_candidate_eleven_days_outside_the_period_is_loaded(seed, db_session, run):
    # an entry 11 days after period end is outside the old ±10 window but inside the drift window
    card = seed.account("window 卡", is_credit=True)
    entry = seed.entry(card, "-580", day=date(2026, 10, 11), merchant="全聯")
    stmt, lines, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 28), "全聯", 580, "purchase")])
    case = next(c for c in _cases(db_session, stmt.id, "open") if c.kind == "amount_delta")
    assert case.context["hint"] == "date_drift" and case.entry_id == entry.id and case.context["date_distance"] == 13
```

Replace the existing `test_rules_defaults_in_settings` with the version above (same name). `ValidationError` is `app.services.settings_service.ValidationError` (import it as the file's other settings tests do). Also change `tests/integration/test_statement_ingest_api.py:330` from `f"r1b-{current['version'] + 1}"` to `f"r6a-{current['version'] + 1}"`.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/integration/test_reconciliation_service.py -q -p no:cacheprovider -k "population_window or stale_rule or rules_defaults or r6a_prefix or eleven_days or without_a_version or drift_window_is_validated"`
Expected: FAIL for `population_window`, `stale_rule`, `rules_defaults`, `r6a_prefix`, `eleven_days`, `without_a_version` (`population_window_days` missing; version `r1b-1` vs `r6a-1`; no warning; no drift case; no `rules_version` key in the read dicts). `drift_window_is_validated` is already green once Task 5 added the int field (`_check_rules` validates every int rule) — it locks the behaviour.

- [ ] **Step 3: Implement**

`matching.py` (after `Rules`):

```python
def population_window_days(rules: Rules) -> int:
    """R6b.0 (shipped with R6a): the orchestrator loads entries this many days around the period so that every
    rule's candidates are present; `candidate_window_days` keeps its installment meaning."""
    return max(rules.candidate_window_days, rules.drift_window_days, rules.near_window_days, rules.foreign_window_days)
```

`settings_service.py`:

```python
RULES_VERSION = "r6a-1"
RULES_VERSION_PREFIX = "r6a"
STALE_RULE_PREFIX = "r1b"  # overrides of the score-scale keys written on the old scale are ignored (R6a.1)
```

Add one helper and use it in the three readers:

```python
def _stored_rules_version(data: dict) -> str:
    """A row that carries rule overrides but no version was written before R6a: its scale overrides are stale."""
    return data.get("rules_version") or (f"{STALE_RULE_PREFIX}-1" if data.get("rules") else RULES_VERSION)
```

`reconciliation_rules`:

```python
    row = db.get(ReconciliationSettings, 1)
    data = (row.data or {}) if row is not None else {}
    return {**RULE_DEFAULTS, **(data.get("rules") or {}), "rules_version": _stored_rules_version(data)}
```

`read_reconciliation_rules` (`settings_service.py:271-272`, used by `worker/verify.py:200`):

```python
def read_reconciliation_rules(db: Session) -> dict:
    settings = read_reconciliation_settings(db)
    return {**settings["rules"], "rules_version": settings["rules_version"]}
```

`_reconciliation_dict`: replace `data.get("rules_version") or RULES_VERSION` with `_stored_rules_version(data)` (the API then shows `r1b-1` for such a row, matching what `rules_for` applies).

In `update_reconciliation_settings`, replace `f"r1b-{row.version}"` with `f"{RULES_VERSION_PREFIX}-{row.version}"`.

`reconciliation_service.py` — add `import logging` and `log = logging.getLogger(__name__)` at module top if absent, then:

```python
SCALE_KEYS = ("accept", "margin", "ambiguous_floor")  # meaningless across the R6a rescale (spec R6a.1)


def rules_for(raw: dict, period_end: date) -> matching.Rules:
    """matching.Rules from the settings' JSON rules (unknown keys ignored, types from the dataclass defaults).
    Score-scale overrides stored under an r1b-* rules_version are ignored with a warning (R6a.1)."""
    stale = str(raw.get("rules_version") or "").startswith(settings_service.STALE_RULE_PREFIX)
    kwargs = {}
    for f in fields(matching.Rules):
        if f.name == "period_end" or f.name not in raw:
            continue
        if stale and f.name in SCALE_KEYS:
            log.warning("ignored stale rule override %s=%r (rules_version %s predates the R6a score scale)",
                        f.name, raw[f.name], raw.get("rules_version"))
            continue
        value, default = raw[f.name], f.default
        if isinstance(default, bool):  # before int: bool is an int subclass
            value = value is True or str(value).lower() == "true"
        elif isinstance(default, Decimal):
            value = Decimal(str(value))
        elif isinstance(default, tuple):
            value = tuple(value)
        elif isinstance(default, int):
            value = int(value)
        kwargs[f.name] = value
    return matching.Rules(period_end=period_end, **kwargs)
```

Population loaders — switch every `window_days=...candidate_window_days` to `window_days=matching.population_window_days(rules)`:
- `app/services/reconciliation_service.py` `reconcile`, both `load_candidates` calls (`:671`, `:677`; search `window_days=rules.candidate_window_days`);
- `worker/verify.py:205` (the verify preview must load the same population as reconcile, else it omits drift/near candidates 11–15 days out; `rules` there is already a `matching.Rules`);
- `app/services/policy_service.py:278` (`ctx.rules.candidate_window_days` → `matching.population_window_days(ctx.rules)`; safe at the defaults, and keeps the agent-match representation rebuild on the same population).
Leave the installment uses alone: `_installment_score` (`matching.py:222`) and `_drifted_instances`. Confirm with `grep -rn "candidate_window_days" app/ worker/ scripts/` that only those remain.

- [ ] **Step 4: Run the integration file and the unit file**

Run: `.venv/bin/python -m pytest tests/integration/test_reconciliation_service.py tests/integration/test_statement_ingest_api.py tests/unit/test_statement_matching.py tests/worker/test_verify.py -q -p no:cacheprovider`
Expected: PASS. `test_rules_for_coerces_settings_types` passes (a raw dict with no `rules_version` key is not stale; only stored rows are classified by `_stored_rules_version`).

- [ ] **Step 5: Commit**

```bash
git add app/services/statements/matching.py app/services/reconciliation_service.py app/services/settings_service.py app/services/policy_service.py worker/verify.py tests/integration/test_reconciliation_service.py tests/integration/test_statement_ingest_api.py
git commit -m "feat(reconciliation): population window = max rule window, rules_version r6a, stale-scale overrides ignored

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Dry-run harness (`scripts/r6_case_dump.py`, `scripts/r6_dryrun.py`)

**Files:**
- Create: `scripts/r6_case_dump.py`, `scripts/r6_dryrun.py`, `scripts/README-r6-dryrun.md`
- Test: `tests/unit/test_r6_dryrun.py`

**Interfaces:**
- Consumes (from whichever tree `PYTHONPATH` points at, both trees export these names): `app.database.SessionLocal` (or `get_engine`), `reconciliation_service.{participating_accounts, rules_for, _current_lines, _manual_claims, load_candidates, _as_line, plan_map, _desired_cases}`, `settings_service.reconciliation_rules`, `entry_write_service.proposed_fx_fee`, `statements.matching.match`, `coverage_service.claimed_entry_ids`.
- Produces: `r6_case_dump.py --out PATH` writes JSON `{"sha": <git HEAD of the tree that supplied the imported app package>, "watermark": [max reconciliation_case.id, max statement_coverage.id, max ledger_entry.id], "statements": {<id>: {"claims": [[event_id, rule, [entry_ids]]...], "cases": [[event_id|null, kind, hint|null, [candidate keys], entry_id|null]...]}}}` where a candidate key is the entry id or `"g<group_id>"` for a group representation; `r6_dryrun.py --baseline PATH --candidate PATH --out-dir DIR` writes `summary.md` (counts by kind×hint before/after, by attributed rule, both SHAs) and `delta.jsonl` (one line per changed statement line), both mode 600; exit 1 when the merge gate fails: any `ambiguous` with fewer than two candidate keys after, any exact claim lost, any change attributed `unattributed`, or the two dumps' watermarks differ (the database changed between the runs). The pure diff/attribution lives in importable functions `diff_dumps(before: dict, after: dict) -> list[dict]`, `attribute(change: dict) -> str` and `gate_failures(changes, after, before=None) -> list[str]` so they are unit-testable.

- [ ] **Step 1: Write the failing unit test for the diff and attribution**

`tests/unit/test_r6_dryrun.py`:

```python
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("r6_dryrun", Path(__file__).parents[2] / "scripts" / "r6_dryrun.py")
dr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dr)


def _dump(statements, watermark=(1, 1, 1), sha="x"):
    return {"sha": sha, "watermark": list(watermark), "statements": statements}


def test_diff_attributes_each_change_to_a_rule():  # uses the single _dump helper above
    before = _dump({"1": {"claims": [], "cases": [[11, "ambiguous", None, [5], None], [12, "line_unmatched", None, [], None],
                                               [13, "line_unmatched", None, [], None], [14, "line_unmatched", None, [], None],
                                               [15, "amount_delta", None, [8], 8], [16, "ambiguous", None, [5], None]]}},
                   sha="base")
    after = _dump({"1": {"claims": [[11, "exact", [5]], [12, "exact", [6]]],
                         "cases": [[13, "amount_delta", "date_drift", [7], 7], [14, "line_unmatched", "bank_only", [], None],
                                   [15, "amount_delta", None, [8, 9], 8], [16, "line_unmatched", "claimed_by_earlier_line", [], None]]}},
                  sha="cand")
    changes = dr.diff_dumps(before, after)
    by_event = {c["event_id"]: dr.attribute(c) for c in changes}
    assert by_event == {11: "R6a.2 single-candidate ambiguous now claims", 12: "R6a.1/R6a.3 new exact claim",
                        13: "R6a.5 date_drift proposal", 14: "R6a.6 bank_only by kind", 15: "R6a.4 near candidates changed",
                        16: "R6a.1 claimed by an earlier line"}
    assert dr.gate_failures(changes, after, before) == []


def test_gate_fails_on_lost_claim_single_candidate_ambiguous_unattributed_or_moved_watermark():
    before = _dump({"1": {"claims": [[11, "exact", [5]]], "cases": [[12, "line_unmatched", None, [], None]]}})
    after = _dump({"1": {"claims": [], "cases": [[11, "ambiguous", None, [5], None], [12, "recheck", None, [], None]]}})
    changes = dr.diff_dumps(before, after)
    assert dr.gate_failures(changes, after, before) == ["event 11: exact claim lost", "event 11: ambiguous with one candidate",
                                                         "event 12: unattributed change"]
    # a group representation counts as a candidate: standalone + split group stays a legal two-candidate ambiguous case
    legal = _dump({"1": {"claims": [], "cases": [[11, "ambiguous", None, [5, "g7"], None]]}})
    assert dr.gate_failures([], legal, legal) == []
    moved = _dump({"1": {"claims": [], "cases": []}}, watermark=(2, 1, 1))
    assert dr.gate_failures([], moved, legal) == ["database changed between the dumps (watermark 1,1,1 → 2,1,1)"]
    same_tree = _dump({"1": {"claims": [], "cases": []}}, sha="x")
    assert dr.gate_failures([], same_tree, _dump({"1": {"claims": [], "cases": []}}, sha="x")) == ["both dumps came from the same tree (sha x)"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_r6_dryrun.py -q -p no:cacheprovider`
Expected: FAIL (file not found).

- [ ] **Step 3: Write `scripts/r6_case_dump.py`**

```python
"""Dump the matcher's desired claims and cases for every live statement, read-only, from the code on PYTHONPATH.

Run once with PYTHONPATH pointing at the baseline checkout's services/accounting-service and once at the working
tree; r6_dryrun.py diffs the two files. Opens one transaction, SET TRANSACTION READ ONLY, never commits.
Usage: PYTHONPATH=<tree>/services/accounting-service python scripts/r6_case_dump.py --out /path/dump.json  (no -I/-E: they drop PYTHONPATH)
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import func, select, text

from app.database import SessionLocal  # the tree on PYTHONPATH
from app.models import Account, AccountStatement
from app.services import reconciliation_service as rec
from app.services import settings_service
from app.services.entry_write_service import proposed_fx_fee
from app.services.statements import matching


def _sha() -> str:
    """HEAD of the tree that actually supplied the imported `app` package (not whatever PYTHONPATH says)."""
    import app
    tree = str(Path(app.__file__).resolve().parents[1])
    return subprocess.run(["git", "-C", tree, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


def _watermark(db) -> list[int]:
    from app.models import LedgerEntry, ReconciliationCase, StatementCoverage
    return [db.scalar(select(func.coalesce(func.max(t.id), 0))) for t in (ReconciliationCase, StatementCoverage, LedgerEntry)]


def _statement(db, statement) -> dict:
    accounts = rec.participating_accounts(db, statement.account_id)
    accounts_by_id = {a.id: a for a in db.execute(select(Account).where(Account.id.in_(accounts))).scalars()}
    rules = rec.rules_for(settings_service.reconciliation_rules(db), statement.period_end)
    all_lines = rec._current_lines(db, statement)
    manual = rec._manual_claims(db, statement)
    line_rows = [line for line in all_lines if line.id not in manual]
    owned = set().union(*manual.values()) if manual else set()
    window = matching.population_window_days(rules) if hasattr(matching, "population_window_days") else rules.candidate_window_days
    entries, groups = rec.load_candidates(db, statement, accounts, window_days=window,
                                          include_reward=any(l.line_kind == "reward" for l in all_lines), exclude=owned)
    result = matching.match(statement.kind, [rec._as_line(r) for r in line_rows], entries, groups,
                            rec.plan_map(db, statement.account_id),
                            lambda e: proposed_fx_fee(accounts_by_id[e.account_id], e.flow), rules, statement.currency)
    lines = {r.id: r for r in line_rows}
    by_id = {e.id: e for e in entries if e.claimable}
    desired = rec._desired_cases(db, statement, accounts, result, lines, by_id, rules)
    claims = [[lines[c.line_id].event_id, c.representation.rule, sorted(r.entry_id for r in c.representation.rows)] for c in result.claims]
    def _keys(cands):  # a candidate is an entry id, or "g<id>" for a group representation (matching._ambiguous)
        out = []
        for x in cands or ():
            if not isinstance(x, dict):
                continue
            if x.get("entry_id") is not None:
                out.append(x["entry_id"])
            elif x.get("group_id") is not None:
                out.append(f"g{x['group_id']}")
        return sorted(out, key=str)
    cases = [[c.get("event_id"), c["kind"], (c.get("context") or {}).get("hint"), _keys(c.get("candidates")), c.get("entry_id")]
             for c in desired]
    return {"claims": sorted(claims), "cases": sorted(cases, key=lambda c: (c[0] or 0, c[1], c[2] or ""))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = {"sha": _sha(), "statements": {}}
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        out["watermark"] = _watermark(db)
        for stmt in db.execute(select(AccountStatement).where(AccountStatement.mode == "live").order_by(AccountStatement.id)).scalars():
            if stmt.current_revision_id is None:
                continue
            out["statements"][str(stmt.id)] = _statement(db, stmt)
        db.rollback()
    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(out, fh, default=str)
    print(f"dumped {len(out['statements'])} live statements at {out['sha']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Verified while planning: `_desired_cases` returns dicts from `_case(...)` with keys `kind`, `event_id`, `line_id`, `entry_id`, `candidates` (list of dicts), `context` (dict) (`reconciliation_service.py:366-368`); `app.database.SessionLocal` exists (`app/database.py:32`); `Account` comes from `app.models`.

- [ ] **Step 4: Write `scripts/r6_dryrun.py`**

```python
"""Diff two r6_case_dump.py files and write the R6a merge-gate evidence (summary.md + delta.jsonl, mode 600).
Usage: python scripts/r6_dryrun.py --baseline before.json --candidate after.json --out-dir ~/reports/home-hub-r6a-dryrun-<ts>
Exit 1 when the gate fails: an exact claim lost, an ambiguous case with fewer than two candidates after, an unattributed
change, or a database change between the two dumps (watermarks differ)."""
import argparse
import json
import os
import sys
from collections import Counter


def _index(dump: dict) -> dict:
    """event_id → ("claim", rule, entries) | ("case", kind, hint, candidates, entry_id) per statement."""
    out = {}
    for sid, st in dump["statements"].items():
        for event_id, rule, entries in st["claims"]:
            out[(sid, event_id)] = ("claim", rule, tuple(entries))
        for event_id, kind, hint, cands, entry_id in st["cases"]:
            if event_id is not None:
                out[(sid, event_id)] = ("case", kind, hint, tuple(cands), entry_id)
    return out


def diff_dumps(before: dict, after: dict) -> list[dict]:
    b, a = _index(before), _index(after)
    changes = []
    for key in sorted(set(b) | set(a), key=lambda k: (int(k[0]), k[1])):
        if b.get(key) != a.get(key):
            changes.append({"statement_id": int(key[0]), "event_id": key[1], "before": b.get(key), "after": a.get(key)})
    return changes


def attribute(change: dict) -> str:
    before, after = change["before"], change["after"]
    if after and after[0] == "claim":
        if before and before[0] == "case" and before[1] == "ambiguous" and len(before[3]) == 1:
            return "R6a.2 single-candidate ambiguous now claims"
        return "R6a.1/R6a.3 new exact claim"
    if after and after[0] == "case":
        kind, hint = after[1], after[2]
        if kind == "amount_delta" and hint == "date_drift":
            return "R6a.5 date_drift proposal"
        if kind == "amount_delta" and (before is None or before[1] != "amount_delta"):
            return "R6a.4 near proposal"
        if kind == "amount_delta" and before[1] == "amount_delta":
            return "R6a.4 near candidates changed"
        if kind == "line_unmatched" and hint == "bank_only" and (before is None or before[2] != "bank_only"):
            return "R6a.6 bank_only by kind"
        if kind == "line_unmatched" and hint == "claimed_by_earlier_line" and (before is None or before[2] != "claimed_by_earlier_line"):
            return "R6a.1 claimed by an earlier line"
        if kind == "ambiguous":
            return "R6a.1 ambiguity changed"
    return "unattributed"


def gate_failures(changes: list[dict], after: dict, before: dict | None = None) -> list[str]:
    failures = [f"event {c['event_id']}: exact claim lost" for c in changes
                if c["before"] and c["before"][0] == "claim" and c["before"][1] == "exact" and not (c["after"] and c["after"][0] == "claim")]
    for st in after["statements"].values():
        for event_id, kind, _hint, cands, _entry in st["cases"]:
            if kind == "ambiguous" and len(cands) < 2:  # group candidates count ("g<id>")
                failures.append(f"event {event_id}: ambiguous with one candidate")  # or none: both are gate failures
    failures += [f"event {c['event_id']}: unattributed change" for c in changes if attribute(c) == "unattributed"]
    if before is not None and before.get("watermark") != after.get("watermark"):
        a, b = ",".join(map(str, before.get("watermark", []))), ",".join(map(str, after.get("watermark", [])))
        failures.append(f"database changed between the dumps (watermark {a} → {b})")
    if before is not None and before.get("sha") == after.get("sha"):
        failures.append(f"both dumps came from the same tree (sha {after.get('sha')})")
    return failures


def _counts(dump: dict) -> Counter:
    c = Counter()
    for st in dump["statements"].values():
        c["claims"] += len(st["claims"])
        for _e, kind, hint, _c, _i in st["cases"]:
            c[f"{kind}/{hint or '-'}"] += 1
    return c


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--allow-moved-claims", action="store_true",
                    help="an exact claim lost by a line whose entry is now held by an earlier line is reported, not failed")
    args = ap.parse_args()
    with open(args.baseline) as fb, open(args.candidate) as fa:
        before, after = json.load(fb), json.load(fa)
    os.makedirs(args.out_dir, mode=0o700, exist_ok=True)
    changes = diff_dumps(before, after)
    rules = Counter(attribute(c) for c in changes)
    failures = gate_failures(changes, after, before)
    if args.allow_moved_claims:
        moved = {c["event_id"] for c in changes if c["before"] and c["before"][0] == "claim"
                 and c["after"] and c["after"][0] == "case" and c["after"][2] == "claimed_by_earlier_line"}
        failures = [f for f in failures if not any(f == f"event {e}: exact claim lost" for e in moved)]
    cb, ca = _counts(before), _counts(after)
    lines = [f"# R6a dry-run — baseline {before['sha']} → candidate {after['sha']}", "",
             f"live statements: {len(after['statements'])}; changed lines: {len(changes)}", "",
             "| kind/hint | before | after |", "|---|---|---|"]
    lines += [f"| {k} | {cb.get(k, 0)} | {ca.get(k, 0)} |" for k in sorted(set(cb) | set(ca))]
    lines += ["", "| attributed rule | lines |", "|---|---|"] + [f"| {k} | {v} |" for k, v in rules.most_common()]
    lines += ["", "gate: " + ("PASS" if not failures else "FAIL\n- " + "\n- ".join(failures))]
    for name, payload in (("summary.md", "\n".join(lines) + "\n"), ("delta.jsonl", "".join(json.dumps({**c, "rule": attribute(c)}) + "\n" for c in changes))):
        fd = os.open(os.path.join(args.out_dir, name), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(payload)
    print("\n".join(lines))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Write `scripts/README-r6-dryrun.md`**

```markdown
# R6a dry-run (merge gate)

Read-only. Both dumps run against the live database inside a READ ONLY transaction that is rolled back.
Run as the owner (or the verify user with the read-only role) from the candidate worktree:

    BASE=/home/opc/srv/homehub-worker/checkout/services/accounting-service   # deployed checkout = baseline (merge-base)
    CAND=$PWD                                                                 # services/accounting-service of the branch
    TS=$(date -u +%Y%m%dT%H%M%SZ); OUT=~/reports/home-hub-r6a-dryrun-$TS; mkdir -m 700 -p $OUT
    PYTHONPATH=$BASE .venv/bin/python scripts/r6_case_dump.py --out $OUT/before.json    # prints "... at <baseline sha>"
    PYTHONPATH=$CAND .venv/bin/python scripts/r6_case_dump.py --out $OUT/after.json     # prints "... at <candidate sha>"
    .venv/bin/python scripts/r6_dryrun.py --baseline $OUT/before.json --candidate $OUT/after.json --out-dir $OUT

Never pass `-I` or `-E` to the dump: both drop `PYTHONPATH`, and the dump would silently import the venv's editable
install (the candidate) twice. The dump prints the SHA of the tree that actually supplied `app`; the two printed SHAs
must differ and match `git rev-parse HEAD` of `$BASE` and `$CAND`. Run the two dumps back to back while the poll timer is
idle (`systemctl --user status homehub-statements-poll.timer` as the worker user, or just between two ticks); the dump
records a watermark (max case / coverage / entry ids) and the gate fails if it moved between the runs.

`summary.md` carries counts only; `delta.jsonl` carries statement/event/entry ids (no amounts, no merchants) and stays
mode 600 under ~/reports. Gate: exit 0 — every changed line attributed to an R6a rule, no exact claim lost, no
ambiguous case with fewer than two candidates, same watermark, two different SHAs. Record the two SHAs and the summary
in the release record.

Reading an "exact claim lost" failure: lines match in id order, so a line that newly claims under R6a (distance 2–3,
below accept before) can take an entry that a later line held before; the later line then shows
`claimed_by_earlier_line`. That pair is the rescale working, not a harness bug — note the pair in the release record
and re-run the gate with `--allow-moved-claims` only after the owner has read the pair list (the flag turns those
specific failures into an "exact claim moved" line in the summary; any other lost claim still fails).
```

(Replace the `BASE` path with the actual deployed worker checkout path from the R5 release record `~/reports/home-hub-r5-release-20261015T030215Z/RELEASE.md` when running; the plan does not hard-code it.)

- [ ] **Step 6: Run the unit test, then a smoke run against the test database**

Run: `.venv/bin/python -m pytest tests/unit/test_r6_dryrun.py -q -p no:cacheprovider`
Expected: PASS.

Smoke: `PYTHONPATH=$PWD .venv/bin/python scripts/r6_case_dump.py --out /tmp/claude-1000/-home-opc/e725ec3e-bc74-45aa-a474-cf3d3532997f/scratchpad/dump.json` against the database from `.env` — expect `dumped N live statements at <sha>` where `<sha>` equals `git rev-parse HEAD` of this worktree, and no write (the script contains no `commit`; the READ ONLY transaction is the guard if `.env` points at the production database — run it once and keep the file in the scratchpad, mode 600).

- [ ] **Step 7: Commit**

```bash
git add scripts/r6_case_dump.py scripts/r6_dryrun.py scripts/README-r6-dryrun.md tests/unit/test_r6_dryrun.py
git commit -m "feat(reconciliation): R6a dry-run harness (baseline vs candidate case dump, merge gate)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Spec implementation notes and deploy README evidence step

**Files:**
- Modify: `docs/superpowers/specs/2026-10-15-reconciliation-r6-design.md` (append `## 5. R6a implementation notes`), `deploy/statements/README.md` (operator evidence section), `services/accounting-service/README.md:151-157` (rule defaults and version string)

- [ ] **Step 1: Append the implementation notes to the spec**

```markdown
## 5. R6a implementation notes (<date>, PR "R6a")

- Score: `_score` = `0.80 + 0.10·(1 − dist/window) + 0.10·text`; window 0 → date term 0.10 at dist 0. `margin` 0.05. The old unit test "same day, same amount, different merchants is ambiguous" flipped: a 0.10 text difference now resolves, which is the intended global competition.
- `match()` emits `ambiguous` only with ≥ 2 representations; `_drift_case` sits after `_near_case` and before `_bank_only`.
- Gates: `receivable` in card purchase/installment and bank withdrawal; `payable` in bank deposit.
- Near: pct 0.05; token overlap only for `dist ≥ 1`.
- `_bank_only` returns `{"hint": "bank_only", "pattern": <matched or null>}` for fee/interest/reward lines.
- Population: `matching.population_window_days(rules)` (max of the four windows) replaces `candidate_window_days` as the load window; installments keep `candidate_window_days`.
- Settings: `rules_version` `r6a-<version>`; `reconciliation_rules()` / `read_reconciliation_rules()` return `rules_version`; a stored row with overrides and no version reads as `r1b-1`; `rules_for()` ignores `accept`/`margin`/`ambiguous_floor` overrides stored under `r1b-*` with a warning. The settings API still *displays* such a stored override (e.g. `margin: "0.15"`) while the matcher ignores it — the displayed value is not the live scale until the owner re-saves rules.
- Evidence: `scripts/r6_case_dump.py` + `scripts/r6_dryrun.py` (README beside them); the release record keeps `summary.md` and both SHAs.
```

- [ ] **Step 2: Add the pytest-free evidence step to `deploy/statements/README.md`**

Under the operator evidence section (near line 221), add:

```markdown
Matcher evidence without pytest (the deploy venv has no test runner): run the R6a dry-run from the candidate
worktree as described in `services/accounting-service/scripts/README-r6-dryrun.md` with `BASE` = this checkout
before it is moved, and keep `summary.md` in the release record. `gate.sh` stays the parser/OCR evidence.
```

- [ ] **Step 2b: Update the service README rule defaults**

In `services/accounting-service/README.md:151-157` change `near_tolerance_pct` (`"0.03"`) → (`"0.05"`), `margin` (`"0.15"`) → (`"0.05"`), add `drift_window_days` (15) to the list, and `rules_version` becomes `r6a-<settings version>`.

- [ ] **Step 3: Full suite**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: all passed (R5 count + the new tests), 0 failed, 0 errors. Paste the final line into the commit body.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-10-15-reconciliation-r6-design.md deploy/statements/README.md services/accounting-service/README.md
git commit -m "docs: R6a implementation notes, rule defaults, pytest-free matcher evidence step

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review

- **Spec coverage:** R6a.1 → Task 1; R6a.2 → Task 2; R6a.3 → Task 3; R6a.4 → Task 4; R6a.5 → Task 5 (+ settings validation is automatic: `_check_rules` types from the dataclass, int 0..10000); R6a.6 → Task 6; R6a.7 → Task 8; R6a.8 tests → Tasks 1–7; R6b.0 population window → Task 7; rules version / stale overrides → Task 7; README evidence → Task 9.
- **Placeholders:** none (Task 6's policy invocation is `policy_service.run_policy`; Task 8's imports are verified).
- **Type consistency:** `population_window_days(rules) -> int` used in Tasks 7 and 8; `_drift_case` signature identical in Task 5 and the `match()` call; `_bank_only` returns a dict in Task 6 and `match()` passes it as context; `reconciliation_rules()` carries `rules_version` (Task 7) and `rules_for` reads it.
- **Review Focus:** 1 → Task 7 (`small` rules), 2 → Task 1 (`txn_date` zero window), 3 → Task 2 (group vs standalone), 4 → Task 4 (50 vs 58), 5 → Task 5 (claimed/grouped excluded).
- **Plan rev 3 (AGENT-130 re-review):** Task 6 asserts `result.applied == [row.id]`; `attribute()` has a `claimed_by_earlier_line` branch (R6a.1) with a unit row; the gate fails when both dumps carry the same SHA; `--allow-moved-claims` documents the line-order claim move as the rescale; Task 1's `claimed_by_earlier_line` test puts the farther line first so it is red on the old scale; Task 7's red run selects every new test and the int-validation test is labelled already-green; `git add` lists, line numbers, duplicate `_dump` and unclosed files fixed.
- **Plan rev 2 (AGENT-130, dev-grok):** Task 4 tolerance arithmetic fixed (1053 vs 1000); Task 8 drops `-I`, reports the SHA of the tree that supplied `app`, counts group candidates, fails on `unattributed` and on a moved watermark; Task 1 marks the already-green tests as regression guards and adds the installment text-gap, `claimed_by_earlier_line` and `margin` assertion updates; Task 5 writes the integration test before the code and adds the near-vs-drift overlap test; Task 7 treats a stored row without `rules_version` as `r1b-1`, carries the version through `read_reconciliation_rules` (verify), switches `worker/verify.py:205` and `policy_service.py:278` to the population window, fixes the ingest API test and adds the out-of-range `drift_window_days` test; Task 3 pastes the full `BANK_GATES`; Task 6 names `policy_service.run_policy`; Task 9 updates the service README defaults.
