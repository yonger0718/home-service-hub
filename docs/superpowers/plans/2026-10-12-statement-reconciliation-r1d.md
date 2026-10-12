# Statement reconciliation R1d — policy-readiness fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fifth stacked backend PR of 對帳: the seven "Deferred from R1c" items that must land before the policy is ever enabled — policy coverage survives a reconcile, `supersede_stale` locks cases before proposals, reverting `link_installment_plan` releases the plan's claims on every statement, `attach_fee` is charged with the fee recomputed under the lock, `policy_skipped` is audited once per case version, the engine refuses a policy apply without a budget, and only propose-only tokens count as agents.

**Architecture:** Seven surgical changes inside the R1c modules (`reconciliation_actions/{engine,ledger_actions,case_actions}.py`, `proposal_service.py`, `policy_service.py`), each with its own failing test first; one docs task at the end. No new module, no migration, no new setting, no env change; the feature flag and the policy stay off.

**Tech Stack:** as R1a–R1c (FastAPI, SQLAlchemy 2.0 `Column` models, Postgres 16, pydantic v2, pytest with `seed` / `db_session` / `run` / `pg_engine`, `tests.helpers.race`, `tests/integration/statement_fixtures.py`).

**Spec:** `docs/superpowers/specs/2026-10-08-statement-reconciliation-design.md` §17 "R1d design (2026-10-12, PR "R1d" — policy-readiness fixes)" items 1–7 (the binding text), read with §17 "R1c implementation notes" (rulings 1–36) and "Deferred from R1c".

## Global Constraints

- Lock order (§7.4, R1c engine): `import advisory key (shared) → policy_budget row → account_statement rows (FOR UPDATE, ascending id, each swept) → reconciliation_action row (revert) → reconciliation_case row → reconciliation_proposal row → [account row, writer barrier] → schedule definitions/instances, groups, entries NOWAIT`. A new lock must fit this order or be `NOWAIT`; nothing waits on a statement after the case lock.
- **Audit FK rule (AGENT-109 Must 1):** `audit.record(statement_id=S)` inserts a row whose FK takes `FOR KEY SHARE` on statement S, which conflicts with another transaction's `FOR UPDATE` on S. Any code path that holds a case (or proposal) row and then audits must already hold that statement's lock (statement → case → … → audit), or it can deadlock with the engine / `create_proposal`. No helper may take a case lock without its statement lock first.
- Services never commit; routers do. Documented exceptions stay: `policy_service.run_policy` commits per candidate; `_skip`/`_propose` commit their own audit.
- Every coverage write goes through `coverage_service.write_claim`; every release through `coverage_service.release_rows` / `release_line`; the transaction ends with `coverage_service.assert_conserved` where the engine already calls it.
- `MATCH_KINDS = ("auto", "manual", "agent")` is a Postgres enum: no new value (that would be a migration). The policy's claims use `agent`.
- No Alembic revision in this PR. If a task finds it needs a column, stop and rule (the plan is wrong).
- Money: `Decimal`; `policy_service._money(value)` = `|value|` normalised (`Decimal('15')`, never `15.0000`).
- Tests: synthetic data only (never owner data); integration tests use the `db_session` / `seed` / `run` / `pg_engine` fixtures; race tests use `tests.helpers.race(engine, first, second)` (asserts `second` blocks ≥ 0.5 s, then finishes after `first` commits) or an explicit thread with a bounded `join`. Run a task's tests with `cd services/accounting-service && .venv/bin/python -m pytest <path> -q`; the full suite (`.venv/bin/python -m pytest -q`, ~1882 tests, ~6 min) must pass before the PR.
- Commits end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`; the PR body ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- Error codes: a refusal that writes nothing is `CodedValidationError(field, code, detail)` (422) or `CodedConflictError(code, detail)` (409) from `app.services.errors`; `NotRevertable` (422 `not_revertable`) for a revert the action cannot do.

## Review Focus

1. A policy `create_system_entry` claim followed by an **owner reconcile** must keep the same `statement_coverage` rows (ids, `match_kind='agent'`) and the action must still revert (Task 2 test `test_policy_coverage_survives_a_reconcile_and_reverts`).
2. `GET /reconciliation/proposals` while another transaction holds the **statement** (not yet the case) must not wait and must not deadlock on the audit's FK (Task 6 tests `test_global_supersede_skips_a_locked_statement`, `test_scoped_supersede_waits_for_the_statement_lock`); the stale proposal it could not supersede must not read as `pending` (`test_listing_hides_a_stale_pending_proposal_it_could_not_supersede`).
3. Reverting a plan link whose claims live on **two statements** must release both and flag the second, and the second statement's next reconcile must open `installment_unmapped` again (Task 7 test `test_reverting_a_link_releases_the_plans_claims_on_other_statements`); while the second statement's reconcile holds an uncommitted claim the revert must be `action_busy`, while the revert is open that reconcile must be `reconcile_busy`, and an instance posted + claimed after the revert's discovery must make the revert `action_busy` with nothing mutated — no orphan claim in any of them (`test_reverting_a_link_is_busy_while_another_statement_reconciles`, `test_another_statements_reconcile_is_busy_during_a_link_revert`, `test_an_instance_posted_after_discovery_makes_the_revert_retry`); the real posting lock must conflict both ways (`test_a_posting_in_flight_makes_the_revert_busy`, `test_a_posting_waits_behind_an_open_link_revert`).
4. An `attach_fee` candidate whose entry was **edited after the reconcile** (stale `context.residual`) must be `candidate_changed`, charging nothing (Task 3 test `test_attach_fee_candidate_is_recomputed_under_the_lock`); two runs skipping the same candidate at once must audit it **once** (Task 4 test `test_concurrent_skips_audit_once`).
5. A proposal by the **owner's own token** (`spa`, legacy scope) must never be auto-applied as an agent proposal (Task 5 test `test_only_propose_only_tokens_are_agents`).

---

### Task 1: The engine requires a budget for the policy actor (spec item 6)

**Files:**
- Modify: `services/accounting-service/app/services/reconciliation_actions/engine.py:210-213` (`apply_action` actor checks)
- Modify: `services/accounting-service/tests/integration/test_reconciliation_actions_ledger.py:203,341` and `tests/integration/test_reconciliation_proposals.py:241,418` (existing policy-actor applies now pass a budget)
- Test: `services/accounting-service/tests/integration/test_reconciliation_actions_engine.py`

**Interfaces:**
- Consumes: `engine.apply_action(db, *, case_id, action, params, actor, label, proposal_id=None, run_id=None, request_id=None, budget: tuple[date, str, Decimal] | None = None, precheck=None) -> (ReconciliationAction, bool)`; `base.POLICY == "policy"`.
- Produces: `apply_action(actor="policy", budget=None)` raises `ValidationError("budget", "the policy must spend the policy budget")` (422) before any lock. Later tasks that apply as `policy` in tests pass `budget=(date(2026, 10, 1), "TWD", Decimal("<amount>"))`.

- [ ] **Step 1: Write the failing test**

In `tests/integration/test_reconciliation_actions_engine.py` (it already has `_apply`, `_refuse`, `_card`, `_submit`, `_cases`, `_line`, `DAY` — reuse them; check the import block and add `ValidationError` from `app.services.errors` if missing):

```python
def test_policy_actor_without_budget_is_refused(seed, db_session, run):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "年費", 1200, "fee")])
    (case,) = _cases(db_session, stmt.id, "open")

    exc = _refuse(db_session, lambda: _apply(db_session, case, "create_system_entry", {"kind": "fee"},
                                             actor="policy", label="policy"))

    assert isinstance(exc, ValidationError) and exc.field == "budget"
    db_session.expire_all()
    assert db_session.execute(select(ReconciliationAction)).scalars().all() == []
    assert db_session.get(ReconciliationCase, case.id).status == "open"
```

(`_refuse` in that file returns the raised exception after rolling the session back; if its signature differs, adapt the call, not the assertion.)

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/accounting-service && .venv/bin/python -m pytest tests/integration/test_reconciliation_actions_engine.py::test_policy_actor_without_budget_is_refused -q`
Expected: FAIL — the apply succeeds (an action row exists) because nothing refuses a budget-less policy apply.

- [ ] **Step 3: Refuse it in the engine**

In `engine.py` `apply_action`, right after the existing

```python
    if budget is not None and actor != POLICY:
        raise ValidationError("actor", "only the policy spends the policy budget")
```

add

```python
    if actor == POLICY and budget is None:
        raise ValidationError("budget", "the policy must spend the policy budget")  # R1d item 6
```

and extend the docstring's 422 list with `budget`.

- [ ] **Step 4: Give the existing policy-actor applies a budget**

Every test that calls `engine.apply_action` / `_apply` with `actor="policy"` and expects success now passes `budget=(date(2026, 10, 1), "TWD", Decimal("<the line's amount>"))`:
- `test_reconciliation_actions_ledger.py:203` (`create_system_entry`, `Decimal("1200")`) and `:341` in `test_attach_fee_policy_claims_as_auto` (`attach_fee` on the `fx_pair` fixture: line 1015 vs entry −1000 → fee `Decimal("15")`);
- `test_reconciliation_proposals.py:241` and `:418` (policy applying an agent `match`; amount = the line's flow, 580 in those fixtures).
`test_reconciliation_actions_engine.py:397` (policy `unmatch` refused as actor) stays as is: the actor check comes first. Grep `actor="policy"` and `actor=POLICY` under `tests/` to be sure none is missed. The budget reservation reads `monthly_total` from the policy defaults, which is enough for these amounts.

- [ ] **Step 5: Run the touched files and the new test**

Run: `.venv/bin/python -m pytest tests/integration/test_reconciliation_actions_engine.py tests/integration/test_reconciliation_actions_ledger.py tests/integration/test_reconciliation_proposals.py tests/integration/test_reconciliation_policy.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add services/accounting-service/app/services/reconciliation_actions/engine.py services/accounting-service/tests/integration/
git commit -m "fix(accounting): the engine refuses a policy apply without a budget (R1d 6)"
```

---

### Task 2: Policy coverage survives a reconcile (spec item 1)

**Files:**
- Modify: `services/accounting-service/app/services/reconciliation_actions/ledger_actions.py:48-51` (`_match_kind`)
- Modify: `services/accounting-service/app/services/reconciliation_service.py:317-327` (`OWNED_KINDS` comment, `_manual_claims` docstring)
- Modify: `services/accounting-service/tests/integration/test_reconciliation_policy.py:138` and `tests/integration/test_reconciliation_actions_ledger.py:211` (`"auto"` → `"agent"`)
- Test: `services/accounting-service/tests/integration/test_reconciliation_policy.py`, `tests/integration/test_reconciliation_actions_ledger.py`

**Interfaces:**
- Consumes: `ledger_actions._match_kind(ctx) -> str`; `reconciliation_service.OWNED_KINDS = ("manual", "agent")` and `_manual_claims` (lines the reconcile never rewrites); `rec.reconcile(db, statement_id)`; `engine.revert_action(db, *, action_id, actor, label)`; the Task 1 budget tuple.
- Produces: every claim written by a `policy` actor has `match_kind == "agent"`; a reconcile after a policy action leaves the action's coverage rows untouched; the revert succeeds.

- [ ] **Step 1: Write the failing tests**

In `tests/integration/test_reconciliation_policy.py` (helpers `_fee_statement`, `_set_policy`, `SYSTEM_ON`, `_actions`, `_active`, `_booked`, `_reload` exist; add `from app.services import reconciliation_service as rec` and `from app.services.reconciliation_actions import engine` if not imported):

```python
def test_policy_coverage_survives_a_reconcile_and_reverts(seed, db_session, run):
    s = _fee_statement(seed, db_session, run)
    _set_policy(db_session, SYSTEM_ON)
    policy_service.run_policy(db_session, s.stmt.id)
    (row,) = _actions(db_session)
    (claim,) = _active(db_session, s.stmt.id)
    assert claim.match_kind == "agent" and claim.id in row.after["coverage_ids"]

    rec.reconcile(db_session, s.stmt.id)  # the owner's reconcile must not rewrite the policy's claim
    db_session.commit()

    (same,) = _active(db_session, s.stmt.id)
    assert (same.id, same.match_kind, same.match_rule) == (claim.id, "agent", "create_system_entry")

    engine.revert_action(db_session, action_id=row.id, actor="owner", label="owner-test")
    db_session.commit()
    assert _booked(db_session) == [] and _active(db_session, s.stmt.id) == []
    assert _reload(db_session, ReconciliationAction, row.id).status == "reverted"
    assert _reload(db_session, ReconciliationCase, s.cases[0].id).status == "open"
```

In `tests/integration/test_reconciliation_actions_ledger.py`, next to the existing policy `attach_fee` test (line ~341; it builds an `amount_delta` case `s` with `s.case`, `s.entry`, `s.stmt` — reuse its fixture):

```python
def test_policy_attach_fee_survives_a_reconcile_and_reverts(db_session, fx_pair):
    s = fx_pair
    row = _apply(db_session, s.case, "attach_fee", {"entry_id": s.entry.id}, actor="policy", label="policy",
                 budget=(date(2026, 10, 1), "TWD", Decimal("15")))
    before = sorted(c.id for c in _active(db_session, s.stmt.id))
    assert {c.match_kind for c in _active(db_session, s.stmt.id)} == {"agent"}

    _reconcile(db_session, s.stmt.id)

    assert sorted(c.id for c in _active(db_session, s.stmt.id)) == before
    _revert(db_session, row.id)
    assert _active(db_session, s.stmt.id) == []
    assert _fresh(db_session, ReconciliationAction, row.id).status == "reverted"
```

(`_reconcile` is in `statement_fixtures.py`; import it. `fx_pair` is the existing fixture at line ~296 of that file.)

Also change the three existing assertions that a policy claim is `"auto"` to `"agent"`: `test_reconciliation_policy.py:138`, `test_reconciliation_actions_ledger.py:211`, and `test_attach_fee_policy_claims_as_auto` at `:339-342` (rename it `test_attach_fee_policy_claims_as_agent`). Rename `test_policy_on_books_a_bank_only_fee_as_auto_and_charges_the_budget` to `…_as_agent_…`.

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/integration/test_reconciliation_policy.py::test_policy_coverage_survives_a_reconcile_and_reverts tests/integration/test_reconciliation_actions_ledger.py::test_policy_attach_fee_survives_a_reconcile_and_reverts -q`
Expected: FAIL — `match_kind == "auto"`, and after the reconcile the claim ids differ / the revert raises `NotRevertable("coverage … was released")`.

- [ ] **Step 3: Make every policy claim `agent`**

`ledger_actions.py`:

```python
def _match_kind(ctx: Context) -> str:
    if ctx.actor == POLICY:
        # R1d item 1: never `auto` — a reconcile keeps only `manual`/`agent` rows (ruling 16), so an `auto` claim
        # written by the policy was rewritten by the next reconcile and its action could not be reverted
        return "agent"
    return "manual"  # an owner-applied proposal is the owner's decision
```

`reconciliation_service.py`: `OWNED_KINDS = ("manual", "agent")  # rulings 16, 31 + R1d item 1: owner, agent and policy coverage a reconcile never rewrites` and mention the policy in `_manual_claims`'s docstring.

- [ ] **Step 4: Run the two files**

Run: `.venv/bin/python -m pytest tests/integration/test_reconciliation_policy.py tests/integration/test_reconciliation_actions_ledger.py -q`
Expected: all PASS (including the two changed `"agent"` assertions).

- [ ] **Step 5: Commit**

```bash
git add services/accounting-service/app/services/reconciliation_actions/ledger_actions.py services/accounting-service/app/services/reconciliation_service.py services/accounting-service/tests/integration/
git commit -m "fix(accounting): policy claims are agent coverage and survive a reconcile (R1d 1)"
```

---

### Task 3: `attach_fee` candidate recomputed under the lock (spec item 4)

**Files:**
- Modify: `services/accounting-service/app/services/policy_service.py:106-116` (`_attach_fee_candidate`), `:199-202` (`_amount_now`)
- Test: `services/accounting-service/tests/integration/test_reconciliation_policy.py`

**Interfaces:**
- Consumes: `Candidate(case_id, kind, status, action, params, amount, entry_id=None, proposal_id=None)`; `current_line(db, case, statement) -> StatementLine | None`; `_money`; `Context.line`, `Context.case`; `apply_candidate(db, statement, candidate, *, month, run_id=None)`; `_only_candidate(session, statement_id, case_id=None)` test helper.
- Produces: `policy_service._fee_now(db, case, line) -> Decimal | None` = `_money(entry.amount − line.flow_amount)` from the rows as they are now; both the candidate's amount (collect time) and `_amount_now` (under the lock) use it.

- [ ] **Step 1: Write the failing test**

In `test_reconciliation_policy.py` add a helper next to `_fee_statement` (the same rows `test_a_failing_apply_is_skipped_and_the_loop_goes_on` builds, without the pre-existing fee child and without the 年費 line):

```python
def _fx_fee_statement(seed, db, run, *, existing_child=False):
    """A live September card statement whose 1015 AMAZON line meets a −1000 USD entry: one open amount_delta case
    with fee_matches (expected FX fee 1.5 % = 15). `existing_child=True` books the fee child beforehand (fee_exists)."""
    card = _card(seed, db, fx_fee_pct=Decimal("1.5"))
    entry = seed.entry(card, "-1000", day=date(2026, 9, 15), merchant="AMAZON", original_amount=Decimal("-30"),
                       original_currency="USD")
    if existing_child:
        seed.entry(card, "-5", kind="fee", day=date(2026, 9, 15), parent_entry_id=entry.id, name=ews.FX_FEE_CHILD_NAME)
    db.commit()
    stmt, lines, _ = _submit(db, run, card, [
        _line(1, date(2026, 9, 15), "AMAZON", 1015, foreign_amount=Decimal("-30"), foreign_currency="USD")])
    (case,) = [c for c in _cases(db, stmt.id, "open") if c.kind == "amount_delta"]
    assert case.context["fee_matches"] is True
    return SimpleNamespace(card=card, stmt=stmt, case=case, entry=entry, fee=Decimal("15"))
```

Then:

```python
def test_attach_fee_candidate_is_recomputed_under_the_lock(seed, db_session, run):
    s = _fx_fee_statement(seed, db_session, run)
    _set_policy(db_session, {"enabled": True, "actions": {"attach_fee": {"enabled": True}}})
    statement, candidate = _only_candidate(db_session, s.stmt.id, s.case.id)
    assert (candidate.action, candidate.amount) == ("attach_fee", s.fee)

    # the entry is edited after the reconcile (dirty tracking off: no sweep release, context.residual is stale)
    entry = db_session.get(LedgerEntry, s.entry.id)
    entry.amount = Decimal(entry.amount) - Decimal("1")
    db_session.commit()

    with pytest.raises(CodedConflictError) as info:
        policy_service.apply_candidate(db_session, statement, candidate, month=MONTH)
    db_session.rollback()
    assert info.value.code == "candidate_changed"
    assert _budget(db_session) is None or _budget(db_session).used_count == 0
    assert _actions(db_session) == []
```

(`CodedConflictError` from `app.services.errors`; `LedgerEntry` is imported already.)

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_reconciliation_policy.py::test_attach_fee_candidate_is_recomputed_under_the_lock -q`
Expected: FAIL — the apply goes through (or is refused by `attach_fee` itself with `fee_unexpected`, not `candidate_changed`) because `_amount_now` still reads the stale `context.residual`.

- [ ] **Step 3: Compute the fee from the rows**

In `policy_service.py` add (near `_money`):

```python
def _fee_now(db: Session, case: ReconciliationCase, line: StatementLine | None) -> Decimal | None:
    """R1d item 4: the fee `attach_fee` would book now — `entry.amount − line.flow_amount` from the rows as they are
    (the case's `context.residual` is the reconcile-time value and goes stale when the entry is edited before the
    next sweep). None without a line or an entry."""
    entry_id = (case.context or {}).get("entry_id")
    if line is None or not isinstance(entry_id, int) or isinstance(entry_id, bool):
        return None
    entry = db.get(LedgerEntry, entry_id)
    if entry is None:
        return None
    return _money(Decimal(entry.amount) - Decimal(line.flow_amount))
```

(`StatementLine`, `LedgerEntry` from `app.models` — add to the import if absent.) Then:

```python
def _attach_fee_candidate(db: Session, case: ReconciliationCase, statement: AccountStatement) -> Candidate | None:
    """An open `amount_delta` whose residual is the expected FX fee (`fee_matches`) → attach it to the entry."""
    context = case.context or {}
    entry_id = context.get("entry_id")
    if case.kind != "amount_delta" or context.get("fee_matches") is not True \
            or not isinstance(entry_id, int) or isinstance(entry_id, bool):
        return None
    amount = _fee_now(db, case, current_line(db, case, statement))
    if amount is None:
        return None
    return Candidate(case.id, case.kind, case.status, "attach_fee", {"entry_id": entry_id}, amount, entry_id=entry_id)
```

and

```python
def _amount_now(ctx: Context, candidate: Candidate) -> Decimal | None:
    if candidate.action == "attach_fee":
        return _fee_now(ctx.db, ctx.case, ctx.line)  # R1d item 4: recomputed under the lock, not context.residual
    return None if ctx.line is None else _money(ctx.line.flow_amount)
```

- [ ] **Step 4: Run the policy tests**

Run: `.venv/bin/python -m pytest tests/integration/test_reconciliation_policy.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/accounting-service/app/services/policy_service.py services/accounting-service/tests/integration/test_reconciliation_policy.py
git commit -m "fix(accounting): attach_fee policy candidate recomputed from the rows under the lock (R1d 4)"
```

---

### Task 4: `policy_skipped` audited once per case version (spec item 5)

**Files:**
- Modify: `services/accounting-service/app/services/policy_service.py:319-327` (`_skip`)
- Test: `services/accounting-service/tests/integration/test_reconciliation_policy.py`

**Interfaces:**
- Consumes: `_skip(db, statement_id, candidate, code, result, *, run_id, reason=None)` (runs after the candidate's rollback, in its own transaction, and commits); `rec._lock_statement(db, statement_id)`; `audit.record`; `ReconciliationAudit.detail` (JSONB); Task 3's `_fx_fee_statement(seed, db, run, *, existing_child=False)`; `tests.helpers.race`.
- Produces: a `policy_skipped` audit row carries `detail.case_version`; a second skip with the same `(case_id, case_version, code, reason)` is counted in `result.skipped` but not audited again; the read-version → check → insert sequence runs under the statement → case locks (the engine's order; the audit's `FOR KEY SHARE` on the statement is then already held).

- [ ] **Step 1: Write the failing tests**

With Task 3's `_fx_fee_statement(..., existing_child=True)` (the `attach_fee` candidate meets an existing FX fee child → `fee_exists`, rolled back and audited, deterministic on every run). Imports: `threading`, `queue`, `from app.services import reconciliation_service as rec`, `from app.services.reconciliation_actions import audit`.

```python
def test_a_deterministic_skip_is_audited_once_per_case_version(seed, db_session, run):
    s = _fx_fee_statement(seed, db_session, run, existing_child=True)
    _set_policy(db_session, {"enabled": True, "actions": {"attach_fee": {"enabled": True}}})

    first = policy_service.run_policy(db_session, s.stmt.id)
    second = policy_service.run_policy(db_session, s.stmt.id)

    assert first.skipped == second.skipped == {"fee_exists": 1}
    (skipped,) = _audit(db_session, "policy_skipped")
    assert (skipped.case_id, skipped.detail["code"], skipped.detail["case_version"]) == (
        s.case.id, "fee_exists", _reload(db_session, ReconciliationCase, s.case.id).version)


def test_concurrent_skips_audit_once(seed, db_session, run, pg_engine, monkeypatch):
    """Two real `_skip` calls for the same candidate: A takes statement → case, inserts and (deferred here) commits;
    B must acquire the statement only after A's commit, find the row and insert nothing. The hooks record the
    order in which the locks were ACQUIRED and the inserts happened."""
    s = _fx_fee_statement(seed, db_session, run, existing_child=True)
    _set_policy(db_session, {"enabled": True, "actions": {"attach_fee": {"enabled": True}}})
    statement, candidate = _only_candidate(db_session, s.stmt.id, s.case.id)
    db_session.commit()
    order: list[tuple[str, str]] = []
    real_lock, real_record = rec._lock_statement, audit.record

    def lock(db, statement_id):
        row = real_lock(db, statement_id)
        order.append(("locked", db.info["name"]))
        return row

    def record(db, **kw):
        order.append(("insert", db.info["name"]))
        return real_record(db, **kw)

    monkeypatch.setattr(rec, "_lock_statement", lock)
    monkeypatch.setattr(audit, "record", record)
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    a, b = factory(), factory()
    a.info["name"], b.info["name"] = "A", "B"
    real_commit = a.commit
    monkeypatch.setattr(a, "commit", lambda: None)  # A's _skip returns still holding its locks
    outcome: queue.Queue = queue.Queue()
    try:
        policy_service._skip(a, s.stmt.id, candidate, "fee_exists", PolicyResult(), run_id=None)
        thread = threading.Thread(target=lambda: outcome.put(
            policy_service._skip(b, s.stmt.id, candidate, "fee_exists", PolicyResult(), run_id=None)), daemon=True)
        thread.start()
        thread.join(0.5)
        assert thread.is_alive(), "B must wait for A's statement lock"
        order.append(("commit", "A"))
        real_commit()
    finally:
        a.close()
        thread.join(5)  # reclaimed after A is released; B rolls back / closes its own session below
        assert not thread.is_alive(), "B is still blocked after A committed"
        b.rollback()
        b.close()
    outcome.get_nowait()
    assert order == [("locked", "A"), ("insert", "A"), ("commit", "A"), ("locked", "B")]
    (skipped,) = _audit(db_session, "policy_skipped")
    assert skipped.detail["case_version"] == _reload(db_session, ReconciliationCase, s.case.id).version
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/integration/test_reconciliation_policy.py -k "audited_once or skips_audit_once" -q`
Expected: both FAIL — two `policy_skipped` rows (no `case_version` in the detail). In the race test the old `_skip` takes no statement lock, so B never waits: the assertion `thread.is_alive()` fails (B completes before A's commit with a second row — both audit inserts take compatible KEY SHARE locks).

- [ ] **Step 3: Serialise and dedupe the audit**

```python
def _skip(db: Session, statement_id: int, candidate: Candidate, code: str, result: PolicyResult, *,
          run_id: int | None, reason: str | None = None) -> None:
    result.skipped[code] = result.skipped.get(code, 0) + 1
    if code == ACTION_DISABLED:
        return
    # R1d item 5: a deterministic skip repeats on every run; audit it once per (case, version, code, reason).
    # Read-version → check → insert under the statement → case locks (the engine's order: the audit row's FK takes
    # FOR KEY SHARE on the statement, which must already be held). This runs after the candidate's rollback in
    # its own transaction and commits below.
    rec._lock_statement(db, statement_id)
    version = db.execute(select(ReconciliationCase.version).where(ReconciliationCase.id == candidate.case_id)
                         .with_for_update()).scalar_one_or_none()
    stored_version = ReconciliationAudit.detail["case_version"].astext
    already = db.execute(select(ReconciliationAudit.id).where(
        ReconciliationAudit.event == "policy_skipped", ReconciliationAudit.case_id == candidate.case_id,
        ReconciliationAudit.detail["code"].astext == code,
        stored_version.is_(None) if version is None else stored_version == str(version),
        func.coalesce(ReconciliationAudit.detail["reason"].astext, "") == (reason or "")).limit(1)).first()
    if already is None:
        audit.record(db, event="policy_skipped", actor=POLICY, statement_id=statement_id, case_id=candidate.case_id,
                     run_id=run_id, detail={"code": code, "action": candidate.action, "reason": reason,
                                            "proposal_id": candidate.proposal_id, "case_version": version})
    db.commit()
```

(`ReconciliationAudit` from `app.models`, `func` from `sqlalchemy`; `rec` is already imported in `policy_service`. `_lock_statement` raises `NotFoundError` for a vanished statement — `_run_candidate`'s callers never reach `_skip` for one, but wrap the lock in `try/except NotFoundError: return` after counting, to keep the loop going.)

- [ ] **Step 4: Run the policy tests**

Run: `.venv/bin/python -m pytest tests/integration/test_reconciliation_policy.py -q`
Expected: all PASS (any existing test asserting a `policy_skipped` detail dict must add `"case_version": <n>`; fix such assertions, not the code).

- [ ] **Step 5: Commit**

```bash
git add services/accounting-service/app/services/policy_service.py services/accounting-service/tests/integration/test_reconciliation_policy.py
git commit -m "fix(accounting): audit a policy skip once per case version, serialised (R1d 5)"
```

---

### Task 5: Only propose-only tokens are agents (spec item 7)

**Files:**
- Modify: `services/accounting-service/app/services/policy_service.py:119-137` (`_agent_candidates`)
- Test: `services/accounting-service/tests/integration/test_reconciliation_policy.py`

**Interfaces:**
- Consumes: `app.auth.scopes_configured() -> bool`, `app.auth.configured_scopes() -> dict[str, frozenset[str]]`, `VALID_SCOPES` (`read, propose, write, admin, enqueue, ingest, legacy`); the policy test env `SCOPES = "spa=legacy; worker=ingest; hermes=read,propose,enqueue"`; the `ambiguous` fixture (proposal by `"hermes"`).
- Produces: `policy_service._agent_authors(authors: Iterable[str]) -> set[str]` — the authors that count as agents: never `policy`; with scopes configured, labels holding `propose` and neither `write` nor `legacy`; with scopes unconfigured, every other author.

- [ ] **Step 1: Write the failing test**

Turn the `ambiguous` fixture body into a helper `_ambiguous_case(seed, db, run, *, author="hermes")` (same body, `author=author` in `create_proposal`) and keep the fixture as `return _ambiguous_case(seed, db_session, run)`. Then:

```python
@pytest.mark.parametrize("author", ["spa", "worker", "nobody"])
def test_only_propose_only_tokens_are_agents(seed, db_session, run, author):
    s = _ambiguous_case(seed, db_session, run, author=author)
    _set_policy(db_session, MATCH_ON)
    s.second.amount = Decimal("-581")
    db_session.commit()

    result = policy_service.run_policy(db_session, s.stmt.id)

    assert result.applied == [] and _actions(db_session) == []
    assert _reload(db_session, ReconciliationProposal, s.proposal.id).status == "pending"


def test_agent_authors_without_scopes_are_every_non_policy_author(monkeypatch):
    monkeypatch.delenv(auth.SCOPES_ENV, raising=False)
    assert policy_service._agent_authors(["hermes", "spa", "policy"]) == {"hermes", "spa"}
```

(`spa` holds `legacy`, `worker` holds `ingest` only, `nobody` has no scope entry: none is an agent. The existing `test_a_single_admissible_agent_match_is_applied_as_agent` keeps proving `hermes` is.)

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/integration/test_reconciliation_policy.py -k "agents" -q`
Expected: FAIL — the `spa`/`worker`/`nobody` proposals are applied; `_agent_authors` does not exist.

- [ ] **Step 3: Filter by scope**

In `policy_service.py` (add `from app import auth`):

```python
def _agent_authors(authors: Iterable[str]) -> set[str]:
    """R1d item 7: an agent is a propose-only token — configured with `propose` and neither `write` nor `legacy`
    (an owner token's proposal is the owner's own draft, never auto-applied). With scopes unconfigured
    (compatibility mode) every author but the policy counts, as before."""
    candidates = {author for author in authors if author != POLICY}
    if not auth.scopes_configured():
        return candidates
    scopes = auth.configured_scopes()
    return {author for author in candidates
            if "propose" in scopes.get(author, frozenset()) and not ({"write", "legacy"} & scopes.get(author, frozenset()))}
```

and in `_agent_candidates`, after the query: `agents = _agent_authors({proposal.author for proposal, _ in rows})`, then skip rows whose `proposal.author not in agents`. Update its docstring ("by an agent — a propose-only token, `_agent_authors`").

- [ ] **Step 4: Run the policy tests**

Run: `.venv/bin/python -m pytest tests/integration/test_reconciliation_policy.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/accounting-service/app/services/policy_service.py services/accounting-service/tests/integration/test_reconciliation_policy.py
git commit -m "fix(accounting): only propose-only tokens count as agents for the policy (R1d 7)"
```

---

### Task 6: `supersede_stale` locks statements, then cases, then proposals (spec item 2)

**Files:**
- Modify: `services/accounting-service/app/services/proposal_service.py:137-169` (`supersede_stale`, `list_proposals`)
- Test: `services/accounting-service/tests/integration/test_reconciliation_proposals.py`

**Interfaces:**
- Consumes: `supersede_stale(db, *, case_id=None) -> int`; `reopen_idle(db, case_ids)`; `rec._lock_statement`; callers: `routers/reconciliation.py` (global, before `list_proposals` / `apply_proposal`, after an apply refusal), `statement_ingest_service.get_case` (scoped), `confirm_service._supersede_stale_proposals` (scoped, under the statement locks), `policy_service._run_candidate` (scoped, after a rollback), `list_proposals` (global); the `unmatched` fixture and `_make(db, s)` (creates a pending proposal) in the proposals test; `tests.helpers.race`.
- Produces: the same signature and return; the global form never waits on a held statement or case (SKIP LOCKED on both) and leaves them to that transaction's own path; the scoped form waits for the statement, then the case (`FOR UPDATE`), the engine's order; `list_proposals` never returns a pending proposal whose `case_version` is behind its case.

- [ ] **Step 1: Write the failing tests**

In `test_reconciliation_proposals.py` (imports: `threading`, `queue`, `sessionmaker` from `sqlalchemy.orm`, `race` from `tests.helpers`, `update`, `select`, `AccountStatement`, `rec` = `reconciliation_service`; add a `_proposals(db, **where)` helper like the policy test's if the file has none):

```python
def _stale_pending(db, s):
    """A pending proposal on s.case made at the previous version (the case moved on)."""
    _make(db, s)
    db.execute(update(ReconciliationCase).where(ReconciliationCase.id == s.case.id)
               .values(version=ReconciliationCase.version + 1))
    db.commit()


def _hold_case(db, s):
    db.execute(select(ReconciliationCase).where(ReconciliationCase.id == s.case.id).with_for_update())


def _hold_statement(db, s):
    rec._lock_statement(db, s.stmt.id)


def _global_supersede_while(pg_engine, hold, s):
    """Run the global helper while `hold(session, s)` keeps a row locked; returns (its result, whether it waited)."""
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    holder, other = factory(), factory()
    out: queue.Queue = queue.Queue()
    try:
        hold(holder, s)
        thread = threading.Thread(target=lambda: out.put(proposal_service.supersede_stale(other)), daemon=True)
        thread.start()
        thread.join(2)
        waited = thread.is_alive()
        holder.commit()
        thread.join(5)
        assert not thread.is_alive()
        result = out.get_nowait()
        other.commit()
        return result, waited
    finally:
        holder.close()
        other.close()


@pytest.mark.parametrize("hold", [_hold_case, _hold_statement], ids=["case", "statement"])
def test_global_supersede_skips_a_locked_row(unmatched, db_session, pg_engine, hold):
    s = unmatched
    _stale_pending(db_session, s)
    result, waited = _global_supersede_while(pg_engine, hold, s)
    assert (result, waited) == (0, False), "the global helper must neither wait nor supersede a held row"
    assert proposal_service.supersede_stale(db_session) == 1  # released: the next call does it durably
    db_session.commit()
    assert _reload(db_session, ReconciliationCase, s.case.id).status == "open"


@pytest.mark.parametrize("hold", [_hold_case, _hold_statement], ids=["case", "statement"])
def test_scoped_supersede_waits_for_the_lock(unmatched, db_session, pg_engine, hold):
    s = unmatched
    _stale_pending(db_session, s)
    outcome = race(pg_engine, first=lambda db: hold(db, s),
                   second=lambda db: proposal_service.supersede_stale(db, case_id=s.case.id))
    assert outcome == "committed"
    (proposal,) = _proposals(db_session, case_id=s.case.id)
    assert proposal.status == "superseded"
    assert _reload(db_session, ReconciliationCase, s.case.id).status == "open"


def test_listing_hides_a_stale_pending_proposal_it_could_not_supersede(unmatched, db_session, pg_engine):
    s = unmatched
    _stale_pending(db_session, s)
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    holder = factory()
    try:
        _hold_case(holder, s)
        listed = proposal_service.list_proposals(db_session)  # global supersede skips the held case
        db_session.commit()
        assert [p.id for p in listed] == []
        assert _proposals(db_session, case_id=s.case.id)[0].status == "pending"  # still pending in the table
        holder.commit()
    finally:
        holder.close()
    listed = proposal_service.list_proposals(db_session)
    db_session.commit()
    assert [(p.id, p.status) for p in listed] == [(s.proposal.id if hasattr(s, "proposal") else listed[0].id, "superseded")]
```

(The statement-held variants are the AGENT-109 Must 1 sequence: another transaction holds the statement and has not yet taken the case; the old helper took the case, updated the proposal and then blocked on the audit row's `FOR KEY SHARE` of the statement.)

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/integration/test_reconciliation_proposals.py -k "supersede or hides_a_stale" -q`
Expected: the global `case` variant FAILS (returns 1 while the case is held); the global `statement` variant FAILS (the helper blocks on the audit FK → `waited` is True); both scoped variants FAIL at `race`'s "must wait" assertion; the listing test FAILS (the stale proposal is listed as `pending`).

- [ ] **Step 3: Lock statements, then cases, then proposals; filter the listing**

```python
def supersede_stale(db: Session, *, case_id: int | None = None) -> int:
    """Pending proposals whose case moved on (`case_version < case.version`) → superseded, audited, and a `proposed`
    case left without a live proposal reopens. `case_id` limits it to one case. Returns how many.
    R1d item 2: the statements of those cases are locked first (ascending), then the cases (ascending), then the
    proposals are updated — `FOR UPDATE SKIP LOCKED` on statements and cases in the global form (a held row is
    left to its holder's own path or the next call) and `FOR UPDATE` when scoped. The statement lock is required:
    the audit row's FK takes FOR KEY SHARE on the statement, which conflicts with a FOR UPDATE held elsewhere;
    taking it first makes this helper's order a prefix of the engine's and of `create_proposal`'s."""
    stale_case = (select(ReconciliationProposal.id)
                  .where(ReconciliationProposal.case_id == ReconciliationCase.id,
                         ReconciliationProposal.status == "pending",
                         ReconciliationProposal.case_version < ReconciliationCase.version).exists())
    scope = [ReconciliationCase.id == case_id] if case_id is not None else []
    skip = case_id is None
    statements = list(db.execute(
        select(AccountStatement.id)
        .where(AccountStatement.id.in_(select(ReconciliationCase.statement_id).where(stale_case, *scope)))
        .order_by(AccountStatement.id).with_for_update(skip_locked=skip)).scalars())
    if not statements:
        return 0
    cases = list(db.execute(
        select(ReconciliationCase.id)
        .where(stale_case, ReconciliationCase.statement_id.in_(statements), *scope)
        .order_by(ReconciliationCase.id).with_for_update(skip_locked=skip)).scalars())
    if not cases:
        return 0
    stale = (select(ReconciliationCase.id)
             .where(ReconciliationCase.id == ReconciliationProposal.case_id,
                    ReconciliationProposal.case_version < ReconciliationCase.version).exists())
    rows = db.execute(update(ReconciliationProposal)
                      .where(ReconciliationProposal.status == "pending", ReconciliationProposal.case_id.in_(cases), stale)
                      .values(status="superseded", decided_at=func.now())
                      .returning(ReconciliationProposal.id, ReconciliationProposal.case_id)
                      .execution_options(synchronize_session=False)).all()
    for proposal_id, proposal_case_id in sorted(rows):
        statement_id = db.execute(select(ReconciliationCase.statement_id)
                                  .where(ReconciliationCase.id == proposal_case_id)).scalar_one()
        audit.record(db, event="proposal_superseded", actor="system", statement_id=statement_id,
                     case_id=proposal_case_id, detail={"proposal_id": proposal_id, "by": "case_version"})
    reopen_idle(db, cases)
    db.expire_all()
    return len(rows)


def list_proposals(db: Session, *, status: str | None = None, case_id: int | None = None,
                   limit: int = 200) -> list[ReconciliationProposal]:
    """After the global supersede: a pending proposal whose case moved on but whose rows were held (SKIP LOCKED)
    is not listed — it is superseded durably by its holder's own path or the next call (ruling 21)."""
    supersede_stale(db)
    current = or_(ReconciliationProposal.status != "pending",
                  ReconciliationProposal.case_version == ReconciliationCase.version)
    q = (select(ReconciliationProposal)
         .join(ReconciliationCase, ReconciliationCase.id == ReconciliationProposal.case_id).where(current))
    if status is not None:
        q = q.where(ReconciliationProposal.status == status)
    if case_id is not None:
        q = q.where(ReconciliationProposal.case_id == case_id)
    return list(db.execute(q.order_by(ReconciliationProposal.id.desc()).limit(limit)
                           .execution_options(populate_existing=True)).scalars())
```

(`AccountStatement` from `app.models`, `or_` from `sqlalchemy` — add to the imports.) Note for `statement_ingest_service.get_case` (scoped, a GET): it now waits for the case's statement lock as `create_proposal` already does; acceptable, document in Task 8.

- [ ] **Step 4: Run the proposals, policy, confirm, ingest and API tests**

Run: `.venv/bin/python -m pytest tests/integration/test_reconciliation_proposals.py tests/integration/test_reconciliation_policy.py tests/integration/test_statement_confirm.py tests/integration/test_reconciliation_api.py tests/integration/test_statement_ingest_service.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/accounting-service/app/services/proposal_service.py services/accounting-service/tests/integration/test_reconciliation_proposals.py
git commit -m "fix(accounting): supersede_stale locks statements, cases, then proposals (R1d 2)"
```

---

### Task 7: Reverting a plan link releases its claims on other statements (spec item 3)

**Files:**
- Modify: `services/accounting-service/app/services/reconciliation_actions/base.py:30-42,61-69` (`Context.lock_set`; `LockSet.exclusive_definition_ids`)
- Modify: `services/accounting-service/app/services/reconciliation_actions/engine_locks.py:69-95` (`lock_rows`: exclusive definitions)
- Modify: `services/accounting-service/app/services/reconciliation_actions/engine.py` (`apply_action` and `revert_action` set `ctx.lock_set = targets` once the locked context exists)
- Modify: `services/accounting-service/app/services/reconciliation_actions/case_actions.py:94-188` (`LinkInstallmentPlanAction`)
- Test: `services/accounting-service/tests/integration/test_reconciliation_actions_case.py`

**Interfaces:**
- Consumes: `LockSet.statement_ids` (extra statements the engine locks ascending with the case's, each swept; the engine's `_recount(db, statements)` after `invert` recounts every locked statement); `lockset_for(db, *, entry_ids, group_ids, statement_ids)`; `snapshots.ids_of(action.after)`; `locks.lock_rows` (definitions `lock_definition(db, id, share=True, nowait=True)`, instances, groups, entries `FOR UPDATE NOWAIT` → 409 `action_busy`); `schedule_locks.lock_definition(db, definition_id, *, share=False, nowait=False)` (FOR UPDATE unless `share`); `schedule_posting.lock_definition_for_post` (FOR SHARE for an ordinary posting, waits); `coverage_service.release_rows(db, ids, *, reason)`; `AccountStatement.needs_recheck`; `engine_locks.ACTION_BUSY`; `rec.reconcile` (`_lock_candidates` locks candidate entries `NOWAIT` before the map is read → 409 `reconcile_busy`); the plan-map claim rule `"installment"` (`matching.py` ~263); `confirm_service.confirm_statement(db, *, account_id, statement_id, force, note, label, request_id)` (the checks JSON is the `confirm_refused` error's `detail`); `tests.conftest.Seed`; the `unmapped` fixture pattern, `_submit(..., period=OCT)`, `_reconcile`, `_apply`, `_revert`, `_refuse`, `_active`, `_cases`, `_fresh`, `PLAN_KEY`.
- Produces: `Context.lock_set: LockSet | None` (what the engine locked for this attempt); `LockSet.exclusive_definition_ids: tuple[int, ...]` (definitions locked FOR UPDATE NOWAIT instead of FOR SHARE, in the same ordered step); `LinkInstallmentPlanAction._plan_entries(db, definition_id) -> set[int]`, `_other_claims(db, definition_id, statement_id) -> list[StatementCoverage]`, `_check_lock_set(ctx, definition_id) -> list[StatementCoverage]` (the pre-mutation re-derivation: 409 `action_busy` when the posted-entry set or the other-statement set grew); a revert releases those rows (reason `plan_unmapped`) and flags their statements.

- [ ] **Step 1: Write the failing tests**

Bounded helpers first (every race below runs the call under test in a worker thread that owns its session; the holder is released in `finally` and only then is the worker joined with a timeout and its result or exception collected — a call that degenerates into a wait fails the test explicitly instead of hanging it or racing the session close):

```python
class _Run:
    """Run fn() in a daemon thread and keep the handle: `waiting(grace)` = still running after `grace` seconds,
    `result(timeout)` = the return value or the exception, failing explicitly when the thread is still alive after
    `timeout` (a NOWAIT path that waited). fn owns its own session and must roll it back / close it itself."""

    def __init__(self, fn):
        self.out: queue.Queue = queue.Queue()
        self.thread = threading.Thread(target=self._go, args=(fn,), daemon=True)
        self.thread.start()

    def _go(self, fn):
        try:
            self.out.put(fn())
        except Exception as exc:  # noqa: BLE001  (asserted by the caller)
            self.out.put(exc)

    def waiting(self, grace=0.5) -> bool:
        self.thread.join(grace)
        return self.thread.is_alive()

    def result(self, timeout=5.0):
        self.thread.join(timeout)
        assert not self.thread.is_alive(), f"still running after {timeout} s: the call waited instead of refusing"
        return self.out.get_nowait()


def _own_session(pg_engine, fn, *, commit=False):
    """A worker body that opens, uses and closes its own session (rolled back unless `commit`)."""
    def body():
        session = sessionmaker(bind=pg_engine, autoflush=False)()
        try:
            value = fn(session)
            if commit:
                session.commit()
            return value
        finally:
            session.rollback()
            session.close()
    return body


def _busy(outcome, code):
    assert isinstance(outcome, CodedConflictError) and outcome.code == code, outcome


@pytest.fixture
def unmapped_twice(seed, db_session, run):
    """The same unconfirmed plan printed on September (seq 2) and October (seq 3): two installment_unmapped cases."""
    card = _card(seed, db_session)
    definition = seed.definition([seed.line("expense", card, "1000")], kind="installment", name="手機", times=12)
    sep_entry = seed.entry(card, "-1000", day=date(2026, 9, 5), merchant="手機")
    oct_entry = seed.entry(card, "-1000", day=date(2026, 10, 5), merchant="手機")
    seed.instance(definition, 2, date(2026, 9, 5), status="posted", entries=[sep_entry])
    seed.instance(definition, 3, date(2026, 10, 5), status="posted", entries=[oct_entry])
    db_session.commit()
    sep, (sep_line,), _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 6), "手機", 1000, "installment",
                                                              installment_seq=2, installment_total=12)])
    oct, (oct_line,), _ = _submit(db_session, run, card, [_line(1, date(2026, 10, 6), "手機", 1000, "installment",
                                                              installment_seq=3, installment_total=12)], period=OCT)
    (sep_case,) = [c for c in _cases(db_session, sep.id, "open") if c.kind == "line_unmatched"]
    (oct_case,) = [c for c in _cases(db_session, oct.id, "open") if c.kind == "line_unmatched"]
    assert sep_case.context["hint"] == oct_case.context["hint"] == "installment_unmapped"
    return SimpleNamespace(card=card, definition=definition, sep=sep, oct=oct, sep_case=sep_case, oct_case=oct_case,
                           sep_entry=sep_entry, oct_entry=oct_entry)


@pytest.fixture
def unmapped_then_pending(seed, db_session, run):
    """September posted (seq 2); October's instance (seq 3) still PENDING — no October entry exists yet."""
    card = _card(seed, db_session)
    definition = seed.definition([seed.line("expense", card, "1000")], kind="installment", name="手機", times=12)
    sep_entry = seed.entry(card, "-1000", day=date(2026, 9, 5), merchant="手機")
    seed.instance(definition, 2, date(2026, 9, 5), status="posted", entries=[sep_entry])
    oct_instance = seed.instance(definition, 3, date(2026, 10, 5), status="pending")
    db_session.commit()
    sep, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 6), "手機", 1000, "installment",
                                                    installment_seq=2, installment_total=12)])
    oct, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 10, 6), "手機", 1000, "installment",
                                                    installment_seq=3, installment_total=12)], period=OCT)
    (sep_case,) = [c for c in _cases(db_session, sep.id, "open") if c.kind == "line_unmatched"]
    return SimpleNamespace(card=card, definition=definition, sep=sep, oct=oct, sep_case=sep_case,
                           sep_entry=sep_entry, oct_instance=oct_instance)


def _link(db, s):
    return _apply(db, s.sep_case, "link_installment_plan", {"definition_id": s.definition.id})


def _post_october(pg_engine, s):
    """Another transaction posts October's pending instance (a new posted entry appears) and commits."""
    session = sessionmaker(bind=pg_engine, autoflush=False)()
    try:
        entry = Seed(session).entry(session.get(Account, s.card.id), "-1000", day=date(2026, 10, 5), merchant="手機")
        instance = session.get(ScheduleInstance, s.oct_instance.id)
        instance.status, instance.posted_entry_ids = "posted", [entry.id]
        session.commit()
        return entry.id
    finally:
        session.close()


def test_reverting_a_link_releases_the_plans_claims_on_other_statements(db_session, unmapped_twice):
    s = unmapped_twice
    row = _link(db_session, s)
    _reconcile(db_session, s.oct.id)  # October now claims its line through the confirmed map
    (oct_claim,) = _active(db_session, s.oct.id)
    assert oct_claim.entry_id == s.oct_entry.id

    _revert(db_session, row.id)

    assert _active(db_session, s.sep.id) == [] and _active(db_session, s.oct.id) == []
    released = _fresh(db_session, StatementCoverage, oct_claim.id)
    assert (released.status, released.stale_reason) == ("stale", "plan_unmapped")
    oct = _fresh(db_session, AccountStatement, s.oct.id)
    assert (oct.needs_recheck, oct.matched_count, oct.open_case_count) == (True, 0, 0)
    assert _fresh(db_session, ReconciliationAction, row.id).status == "reverted"

    _reconcile(db_session, s.oct.id)  # the October case is raised again
    reopened = [c for c in _cases(db_session, s.oct.id, "open") if c.kind == "line_unmatched"]
    assert [c.context["hint"] for c in reopened] == ["installment_unmapped"]
    assert _fresh(db_session, AccountStatement, s.oct.id).needs_recheck is False


def test_reverting_a_link_flags_a_confirmed_other_statement(db_session, unmapped_twice):
    """AGENT-109 S3: an already reconciled October keeps its status, loses the claim, refuses confirm until its
    next reconcile reopens the case."""
    s = unmapped_twice
    row = _link(db_session, s)
    _reconcile(db_session, s.oct.id)
    confirm_service.confirm_statement(db_session, account_id=s.card.id, statement_id=s.oct.id, force=False, note=None,
                                      label="owner-test", request_id=None)
    db_session.commit()
    assert _fresh(db_session, AccountStatement, s.oct.id).status == "reconciled"

    _revert(db_session, row.id)

    oct = _fresh(db_session, AccountStatement, s.oct.id)
    assert (oct.status, oct.needs_recheck, oct.matched_count, oct.open_case_count) == ("reconciled", True, 0, 0)
    exc = _refuse(db_session, lambda: confirm_service.confirm_statement(
        db_session, account_id=s.card.id, statement_id=s.oct.id, force=False, note=None, label="owner-test",
        request_id=None))
    assert exc.code == "confirm_refused" and json.loads(exc.detail)["needs_recheck"] is False  # the check failed

    _reconcile(db_session, s.oct.id)
    oct = _fresh(db_session, AccountStatement, s.oct.id)
    assert (oct.status, oct.needs_recheck, oct.open_case_count) == ("reconciled", False, 1)


def test_reverting_a_link_is_busy_while_another_statement_reconciles(db_session, unmapped_twice, pg_engine):
    """AGENT-109 Must 3: October's reconcile holds its candidate entry and an UNCOMMITTED claim through the map;
    the revert must be action_busy (map intact); after that commit the retry releases the claim."""
    s = unmapped_twice
    row = _link(db_session, s)
    holder = sessionmaker(bind=pg_engine, autoflush=False)()
    run = None
    try:
        rec.reconcile(holder, s.oct.id)  # holds October's statement + entry, claim written, not committed
        run = _Run(_own_session(pg_engine, lambda db: engine.revert_action(
            db, action_id=row.id, actor="owner", label="owner-test")))
        _busy(run.result(), "action_busy")
        assert holder.execute(select(InstallmentPlanMap)).scalars().one() is not None
        holder.commit()
    finally:
        holder.close()
        if run is not None:
            run.result()  # the worker thread is reclaimed (bounded) after the holder is released
    _revert(db_session, row.id)  # the retry sees the committed claim, locks October and releases it
    assert db_session.execute(select(InstallmentPlanMap)).scalars().all() == []
    assert _active(db_session, s.oct.id) == [] and _fresh(db_session, AccountStatement, s.oct.id).needs_recheck is True


def test_another_statements_reconcile_is_busy_during_a_link_revert(db_session, unmapped_twice, pg_engine):
    """The mirror: the revert holds the plan's entries; October's reconcile is reconcile_busy; after the revert
    commits it finds no map and writes no claim."""
    s = unmapped_twice
    row = _link(db_session, s)
    reverter = sessionmaker(bind=pg_engine, autoflush=False)()
    run = None
    try:
        engine.revert_action(reverter, action_id=row.id, actor="owner", label="owner-test")  # not committed yet
        run = _Run(_own_session(pg_engine, lambda db: rec.reconcile(db, s.oct.id)))
        _busy(run.result(), "reconcile_busy")
        reverter.commit()
    finally:
        reverter.close()
        if run is not None:
            run.result()
    _reconcile(db_session, s.oct.id)
    assert _active(db_session, s.oct.id) == []
    assert [c.context["hint"] for c in _cases(db_session, s.oct.id, "open") if c.kind == "line_unmatched"] == [
        "installment_unmapped"]


def test_an_instance_posted_after_discovery_makes_the_revert_retry(db_session, unmapped_then_pending, pg_engine,
                                                                     monkeypatch):
    """AGENT-110 Must 1: between the revert's unlocked discovery and its locks, October's instance is posted and
    October's reconcile claims the new entry (uncommitted). The revert must notice the grown posted set under the
    exclusive definition lock and refuse with nothing mutated; the retry after the commit releases the claim."""
    s = unmapped_then_pending
    row = _link(db_session, s)
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    holder = factory()
    real = LinkInstallmentPlanAction.revert_lock_targets
    fired = []

    def discovery_then_posting(self, ctx, action):
        targets = real(self, ctx, action)
        if not fired:
            fired.append(_post_october(pg_engine, s))  # P: October posted + committed after the discovery
            rec.reconcile(holder, s.oct.id)  # B: claims the new entry through the still-valid map, uncommitted
        return targets

    monkeypatch.setattr(LinkInstallmentPlanAction, "revert_lock_targets", discovery_then_posting)
    run = None
    try:
        run = _Run(_own_session(pg_engine, lambda db: engine.revert_action(
            db, action_id=row.id, actor="owner", label="owner-test")))
        _busy(run.result(), "action_busy")
        # nothing mutated: map, action and case as before, no revert row
        assert holder.execute(select(InstallmentPlanMap)).scalars().one() is not None
        assert _fresh(db_session, ReconciliationAction, row.id).status == "applied"
        assert _fresh(db_session, ReconciliationCase, s.sep_case.id).status == "resolved"
        assert db_session.execute(select(ReconciliationAction).where(ReconciliationAction.action == "revert")
                                  ).scalars().all() == []
        holder.commit()  # B's claim lands
    finally:
        holder.close()
        if run is not None:
            run.result()
    (claim,) = _active(db_session, s.oct.id)
    assert claim.entry_id == fired[0]
    _revert(db_session, row.id)  # the retry discovers the posted entry and October, locks and releases
    assert _active(db_session, s.oct.id) == [] and _fresh(db_session, AccountStatement, s.oct.id).needs_recheck is True
    assert db_session.execute(select(InstallmentPlanMap)).scalars().all() == []


def test_a_posting_in_flight_makes_the_revert_busy(db_session, unmapped_then_pending, pg_engine):
    """AGENT-110 Should 1: the real posting lock (`lock_definition_for_post`, FOR SHARE) conflicts with the
    revert's FOR UPDATE NOWAIT on the definition: the revert is action_busy at once, nothing mutated."""
    s = unmapped_then_pending
    row = _link(db_session, s)
    poster = sessionmaker(bind=pg_engine, autoflush=False)()
    run = None
    try:
        schedule_posting.lock_definition_for_post(poster, s.definition.id)  # a posting holds the definition
        run = _Run(_own_session(pg_engine, lambda db: engine.revert_action(
            db, action_id=row.id, actor="owner", label="owner-test")))
        _busy(run.result(), "action_busy")
        assert poster.execute(select(InstallmentPlanMap)).scalars().one() is not None
        assert _fresh(db_session, ReconciliationAction, row.id).status == "applied"
        poster.commit()
    finally:
        poster.close()
        if run is not None:
            run.result()
    _revert(db_session, row.id)  # released: the retry succeeds
    assert db_session.execute(select(InstallmentPlanMap)).scalars().all() == []


def test_a_posting_waits_behind_an_open_link_revert(db_session, unmapped_then_pending, pg_engine):
    """The mirror: while the revert holds the definition FOR UPDATE, a posting's FOR SHARE cannot complete; it
    finishes only after the revert commits (bounded both ways)."""
    s = unmapped_then_pending
    row = _link(db_session, s)
    reverter = sessionmaker(bind=pg_engine, autoflush=False)()
    run = None
    try:
        engine.revert_action(reverter, action_id=row.id, actor="owner", label="owner-test")  # holds the definition
        run = _Run(_own_session(pg_engine, lambda db: schedule_posting.lock_definition_for_post(
            db, s.definition.id).id, commit=True))
        assert run.waiting(0.5), "the posting must wait behind the revert's exclusive definition lock"
        reverter.commit()
        assert run.result(5.0) == s.definition.id  # released: the posting's lock is granted and it completes
    finally:
        reverter.close()
        if run is not None:
            run.result()
    assert db_session.execute(select(InstallmentPlanMap)).scalars().all() == []
```

(Imports to add as needed: `json`, `queue`, `threading`, `OCT`, `SimpleNamespace`, `sessionmaker`, `Account`, `AccountStatement`, `LedgerEntry`, `InstallmentPlanMap`, `ScheduleInstance`, `StatementCoverage`, `CodedConflictError`, `engine`, `rec`, `confirm_service`, `schedule_posting` from `app.services`, `LinkInstallmentPlanAction` from `app.services.reconciliation_actions.case_actions`, `Seed` from `tests.conftest`; `_reconcile` is in `statement_fixtures.py`. If `_apply` in this file returns a tuple, unpack it in `_link`. `seed.instance(..., status="pending")` without `entries` must create an unposted instance — read `make_instance` in `tests/conftest.py` and pass `entries=[]` if required.)

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/integration/test_reconciliation_actions_case.py -k "unmapped_twice or other_statement or busy or posted_after or posting" -q`
Expected: all seven FAIL — October's claim stays active after the revert; the confirmed variant keeps `matched_count` 1; the busy variant's revert succeeds while the claim is uncommitted (no `action_busy`); the mirror's reconcile succeeds while the revert is open; the posted-after-discovery variant's revert succeeds and leaves October's claim active (an orphan); the posting-in-flight variant's revert succeeds (FOR SHARE and FOR SHARE are compatible) and the posting-waits variant's `waiting` assertion fails for the same reason.

- [ ] **Step 3: Carry the lock set on the context; exclusive definition locks**

`base.py`:
- `Context`: add `lock_set: "LockSet | None" = None  # what the engine locked for this attempt (set after the locks; R1d)`.
- `LockSet`: add `exclusive_definition_ids: tuple[int, ...] = ()  # definitions FOR UPDATE instead of FOR SHARE (R1d: a plan revert freezes the posted set)`.

`engine_locks.py` `lock_rows`: replace the definition loop with

```python
            exclusive = set(targets.exclusive_definition_ids)
            for definition_id in sorted({*targets.definition_ids, *exclusive}):
                lock_definition(db, definition_id, share=definition_id not in exclusive, nowait=True)
```

and extend the docstring: "a definition in `exclusive_definition_ids` is taken FOR UPDATE (an ordinary posting takes it FOR SHARE, so the posted set cannot change while it is held)".

`engine.py`: in `apply_action`, right after the locked `ctx = _context(...)` line, `ctx.lock_set = targets`; in `revert_action`, after its `ctx = _context(...)`, `ctx.lock_set = targets`.

- [ ] **Step 4: Extend the revert**

In `case_actions.py` (imports: `AccountStatement`, `StatementCoverage` from `app.models`; `coverage_service`; `snapshots`, `lockset_for` from `.base` / `.snapshots`; `ACTION_BUSY` from `.engine_locks` — or the literal `"action_busy"` if that import is circular):

```python
    PLAN_RULE = "installment"  # matching.py: Representation("installment", …, rule="installment") for a plan-map claim

    @staticmethod
    def _plan_entries(db: Session, definition_id: int) -> set[int]:
        posted = db.execute(select(ScheduleInstance.posted_entry_ids)
                            .where(ScheduleInstance.definition_id == definition_id)).scalars()
        return {i for ids in posted for i in ids or [] if isinstance(i, int)}

    @classmethod
    def _other_claims(cls, db: Session, definition_id: int, statement_id: int) -> list[StatementCoverage]:
        """R1d item 3: active plan-rule coverage of the plan's posted entries on OTHER statements of the account."""
        entries = sorted(cls._plan_entries(db, definition_id))
        if not entries:
            return []
        return list(db.execute(select(StatementCoverage).where(
            StatementCoverage.status == "active", StatementCoverage.match_rule == cls.PLAN_RULE,
            StatementCoverage.entry_id.in_(entries), StatementCoverage.statement_id != statement_id)
            .order_by(StatementCoverage.id)).scalars())

    def revert_lock_targets(self, ctx: Context, action: ReconciliationAction) -> LockSet:
        plan = ctx.db.get(InstallmentPlanMap, (action.inverse or {}).get("plan_map_id"))
        if plan is None:
            return super().revert_lock_targets(ctx, action)
        # R1d item 3: the definition FOR UPDATE (freezes the posted set: a posting takes it FOR SHARE), the plan's
        # posted entries (NOWAIT: a reconcile claiming one holds it first → action_busy) and the other statements
        # already claiming them (locked ascending with the case's, each swept)
        entries, groups = snapshots.ids_of(action.after)
        others = {c.statement_id for c in self._other_claims(ctx.db, plan.definition_id, ctx.statement.id)}
        targets = lockset_for(ctx.db,
                              entry_ids={*entries, *(action.touched_entry_ids or ()), *(action.created_entry_ids or ()),
                                         *self._plan_entries(ctx.db, plan.definition_id)},
                              group_ids={*groups, *(action.touched_group_ids or ())}, statement_ids=others)
        return replace(targets, definition_ids=tuple(i for i in targets.definition_ids if i != plan.definition_id),
                       exclusive_definition_ids=(plan.definition_id,))

    def _check_lock_set(self, ctx: Context, definition_id: int) -> list[StatementCoverage]:
        """Under the locks, before any mutation: the posted-entry set and the other-statement set re-derived; an
        entry or statement the engine did not lock (posted or claimed between the unlocked discovery and the
        locks) is 409 action_busy — the retry discovers and locks the full set. Returns the other claims."""
        held = ctx.lock_set or LockSet()
        new_entries = sorted(self._plan_entries(ctx.db, definition_id) - set(held.entry_ids))
        if new_entries:
            raise CodedConflictError(ACTION_BUSY, f"entry {new_entries[0]} was posted after discovery; retry")
        others = self._other_claims(ctx.db, definition_id, ctx.statement.id)
        late = sorted({c.statement_id for c in others} - set(held.statement_ids))
        if late:
            raise CodedConflictError(ACTION_BUSY, f"statement {late[0]} gained a plan claim after discovery; retry")
        return others
```

and rewrite `invert` so that every check precedes the first mutation:

```python
    def invert(self, ctx: Context, action: ReconciliationAction) -> Effect:
        db = ctx.db
        plan = db.get(InstallmentPlanMap, action.inverse["plan_map_id"])
        if plan is None:
            raise NotRevertable("the plan mapping is gone")
        definition_id = plan.definition_id
        posted = db.execute(select(ScheduleInstance.posted_entry_ids)
                            .where(ScheduleInstance.definition_id == definition_id)).scalars()
        entries = sorted({i for ids in posted for i in ids or [] if isinstance(i, int)}
                         | set(action.touched_entry_ids or ()))
        if entries:
            later = db.execute(select(ReconciliationAction.id).where(
                ReconciliationAction.id > action.id, ReconciliationAction.status == "applied",
                ReconciliationAction.action != "revert",
                ReconciliationAction.touched_entry_ids.overlap(entries)).order_by(ReconciliationAction.id).limit(1)
            ).scalar_one_or_none()
            if later is not None:
                raise NotRevertable(f"later action {later} touched the plan's entries")
        others = self._check_lock_set(ctx, definition_id)  # R1d item 3: the last check before any write
        # --- mutations ---
        db.delete(plan)
        db.flush()
        _reopen(ctx.case)  # so the reconcile keeps this case instead of opening a twin
        db.flush()
        # the installment claim is no longer derived: released (barrier_wait=False as in apply)
        rec.reconcile(db, ctx.statement.id, locked=True, barrier_wait=False)
        if others:  # R1d item 3: the plan's claims on other statements are no longer derived either
            coverage_service.release_rows(db, [c.id for c in others], reason="plan_unmapped")
            statement_ids = sorted({c.statement_id for c in others})
            for statement in db.execute(select(AccountStatement).where(AccountStatement.id.in_(statement_ids))
                                        .execution_options(populate_existing=True)).scalars():
                statement.needs_recheck = True  # ruling 29: a matching reason; its next reconcile raises the case
            db.flush()
        return Effect(touched_entry_ids=list(action.touched_entry_ids or ()))
```

(The engine's `_recount(db, statements)` after `invert` recounts every locked statement, the released ones included; the engine's attempt savepoint rolls back its own revert row on the 409, so a refused revert leaves no trace.)

- [ ] **Step 5: Run the case-action and engine tests**

Run: `.venv/bin/python -m pytest tests/integration/test_reconciliation_actions_case.py tests/integration/test_reconciliation_actions_engine.py tests/integration/test_reconciliation_actions_structural.py -q`
Expected: all PASS (the existing single-statement link/revert tests unchanged; the structural tests prove `lock_rows` still locks ordinary definitions FOR SHARE).

- [ ] **Step 6: Commit**

```bash
git add services/accounting-service/app/services/reconciliation_actions/ services/accounting-service/tests/integration/test_reconciliation_actions_case.py
git commit -m "fix(accounting): reverting a plan link freezes the plan and releases its claims everywhere (R1d 3)"
```

---
### Task 8: Docs — spec §17 R1d notes, README, openspec change, full suite

**Files:**
- Modify: `docs/superpowers/specs/2026-10-08-statement-reconciliation-design.md` (§17: add "R1d implementation notes (2026-10-12)" after the R1d design; strike the seven items from "Deferred from R1c" with a pointer)
- Modify: `services/accounting-service/README.md:167-235` (policy section)
- Create: `openspec/changes/add-statement-reconciliation-r1d/{proposal.md,tasks.md,specs/accounting-reconciliation/spec.md}` mirroring `openspec/changes/add-statement-reconciliation-r1c/` (read those three files first and keep their format: the spec delta lists MODIFIED requirements with `#### Scenario:` blocks)

**Interfaces:**
- Consumes: the seven behaviours as shipped by Tasks 1–7 (names: `_fee_now`, `_agent_authors`, `plan_unmapped`, `budget` 422, `case_version` detail, SKIP LOCKED global supersede, `agent` policy claims).
- Produces: documentation only.

- [ ] **Step 1: Spec §17 R1d implementation notes**

Append after the R1d design:

```markdown
### R1d implementation notes (2026-10-12, PR "R1d")

Shipped as designed (items 1–7 above). Facts a reader of the code needs:
- `match_kind='agent'` now means "automation's decision" (an agent proposal applied by the policy, or the policy's own booking; an agent proposal the owner applies stays `manual`); `auto` is the matcher's. `OWNED_KINDS` keeps both `manual` and `agent` across reconciles.
- `policy_service._fee_now` is the single source of an `attach_fee` candidate's amount (collect time and under the lock).
- `policy_skipped` audit rows carry `case_version`; the same `(case, version, code, reason)` is audited once, the check serialised under the statement → case locks.
- `policy_service._agent_authors`: propose-only labels (scopes configured) or every non-policy author (compatibility).
- `supersede_stale`: statements, then cases, locked ascending first (`SKIP LOCKED` global, `FOR UPDATE` scoped) because the audit row's FK takes `FOR KEY SHARE` on the statement; `list_proposals` hides a stale pending proposal it could not supersede; `get_case` (scoped) now waits for the statement like `create_proposal`.
- Reverting `link_installment_plan` locks the plan's definition FOR UPDATE (freezes the posted set: posting takes it FOR SHARE), its posted entries NOWAIT (the serialisation with other statements' reconciles) and the other claiming statements (ascending, swept); before any mutation it re-derives both sets under the locks and is 409 `action_busy` when either grew; then it releases their claims (`stale_reason='plan_unmapped'`) and flags them `needs_recheck` (status unchanged, confirm refused until the next reconcile reopens the case). `LockSet.exclusive_definition_ids` is the new engine knob.
- The engine refuses `actor='policy'` without a budget (422 `budget`).

Still deferred (unchanged): budget refund on revert, separate FX fee lines / credit residuals, `move_account` typed exceptions, the historical-statement line route, card-identity hints, OCR.
```

and in "Deferred from R1c" mark the seven resolved bullets with "→ R1d" (do not delete them: the review trail cites them).

- [ ] **Step 2: README**

In the R1c section: replace the sentence that says a policy action's claim is rewritten by the next reconcile ("edit or delete the booked entry instead") with "policy claims are `agent` coverage, kept across reconciles and revertable like the owner's (R1d)"; note that `GET /reconciliation/cases/{id}` and the proposal listing take the statement lock briefly (supersede order) and that the listing hides a stale pending proposal it could not supersede; add one line each for: `policy_skipped` once per case version, agents = propose-only tokens (`ACCOUNTING_TOKEN_SCOPES`: `propose` without `write`/`legacy`), `revert` of a plan link releasing other statements' claims (`plan_unmapped`, `needs_recheck`), and `422 budget` for a policy apply without a budget. Keep the "Enabling the policy" checklist and add step: "(0) deploy R1d first".

- [ ] **Step 3: openspec change**

`proposal.md`: why (the seven deferred items block enabling the policy) and what changes (one bullet per item). `tasks.md`: the eight tasks of this plan, checked. `specs/accounting-reconciliation/spec.md`: `## MODIFIED Requirements` with one requirement per item and scenarios copied from the tests' Given/When/Then (e.g. "Scenario: a policy claim survives the owner's reconcile", "Scenario: the global supersede skips a locked case", "Scenario: reverting a plan link releases claims on another statement", "Scenario: an attach_fee candidate whose entry changed is candidate_changed", "Scenario: a deterministic skip is audited once per case version", "Scenario: a policy apply without a budget is 422", "Scenario: an owner token's proposal is not an agent proposal").

- [ ] **Step 4: Full suite**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q > /tmp/r1d-suite.txt; echo exit=$?; tail -3 /tmp/r1d-suite.txt`
Expected: `exit=0`, all passed (1 skipped as before), no new warnings about the changed modules.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-10-08-statement-reconciliation-design.md services/accounting-service/README.md openspec/changes/add-statement-reconciliation-r1d
git commit -m "docs(accounting): R1d notes, README policy section, openspec change"
```
