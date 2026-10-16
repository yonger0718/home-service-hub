# Statement Reconciliation R6b (statement as truth) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the policy fill the HomeHub ledger from statement lines (`backfill_entry`), anchor each bank account at its first live statement's opening balance (`anchor_opening`), replace a backfilled stand-in with the owner's own entry when it arrives (`replace_stand_in`), make the sweep release a match only on structural edits, retire a creation whose entry the owner deleted in the ledger, and prove each step with a read-only dry-run before enabling it.

**Architecture:** Three ordinary engine actions (registered, `KIND_ACTIONS` + `CONDITIONS`, `actors = {OWNER, POLICY}`) on top of the R1c/R1d action engine; the policy's candidate builders find them. Stand-ins are a population flag the service derives from applied `backfill_entry` actions and the matcher uses to let owner entries win. The sweep gains two behaviours (structural-only release; retirement of creations whose entry was deleted). `_desired_cases` derives two new case shapes (`shadowed_by_stand_in`, `orphaned_stand_in`) and the owner-deleted marker from the action table on every reconcile, so nothing new has to be carried in case context. No migration, no new column, no new case kind.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2, pytest (unit: pure matcher; integration: throw-away Postgres per session via `tests/conftest.py`), Decimal money.

**Spec:** `docs/superpowers/specs/2026-10-15-reconciliation-r6-design.md` §2 (R6b.0, R6b.1, R6b.2, R6b.5–R6b.8; R6b.3/R6b.4 keep only the `needs_peer` hint, R6b.9 is R6c input) and §3; parent `docs/superpowers/specs/2026-10-08-statement-reconciliation-design.md` §7 (cases, actions, policy, lock order).

## Global Constraints

- Branch `feat/reconciliation-r6b` in worktree `/home/opc/workspace/home-hub-r6b` (from `origin/main` c729d8f = R6a merged); run everything from `/home/opc/workspace/home-hub-r6b/services/accounting-service` with `.venv/bin/python -m pytest -q -p no:cacheprovider …` (venv and a mode-600 `.env` copy are in place).
- No migration, no new DB column, no new case kind, no new enum value: `reconciliation_action.action` and `statement_coverage.match_rule` are `String(32)`; `ACTION_STATUSES` stays `applied | reverted | failed`; settings are JSON keys. Audit event names are a closed set in `app/services/reconciliation_actions/audit.py` — new events must be added there.
- Every new action: `actors = frozenset({OWNER, POLICY})`, an entry in `KIND_ACTIONS` and `CONDITIONS`, `register(...)`; the engine's `allowed(case)`, idempotency, snapshots, proposals and `_check_creation_family` apply unchanged. Never nest `engine.apply_action`/`revert_action` inside an action.
- Lock order is the engine's: statements ascending → sweep → case → (precheck) → `check_before_locks` → account row → barrier → definitions → instances → groups → entries (NOWAIT). An action declares everything it touches in `lock_targets` (apply) / `revert_lock_targets` (revert); the engine never discovers targets for it.
- Stand-in = ledger entry with `source == "statement"` created by an **applied** `backfill_entry` action (`reconciliation_action.created_entry_ids`); `create_system_entry`, `create_entry`, `create_refund` and `balance_adjustment` rows are not stand-ins.
- Structural keys (sweep release): `STRUCTURAL_KEYS = frozenset({"amount", "flow", "currency", "original_amount", "original_currency", "entry_date", "posted_date", "account_id", "kind", "group_id", "parent_entry_id", "transfer_group_id", "refunds_entry_id", "settles_entry_id", "is_settlement"})` = `snapshot_entry` keys minus `id`, `name`, `merchant`.
- Settings defaults verbatim: `backfill_grace_days 14`, `backfill_accounts []`, `actions.backfill_entry {enabled False, max_amount "5000"}`, `actions.anchor_opening {enabled False, max_amount "200000"}`, `actions.replace_stand_in {enabled True, max_amount "0"}`; `POLICY_VERSION_PREFIX = "r6b"`.
- Backfill line-kind map: card `purchase` → `expense`; bank `withdrawal`/`purchase` → `expense`; bank `deposit` → `income`; never `unknown`, `transfer_in`, `transfer_out`, `payment`, `installment`, `balance_adjustment`, `refund`. Category `對帳待分類` per kind (`backfill_category_id`), `needs_review=True`, `source="statement"`.
- Owner-deleted marker is derived from the action table each reconcile (context key `owner_deleted: true`; hint `stand_in_deleted` only when the matcher gave the line no other hint) — never carried, never a column.
- No owner financial data in tests, fixtures, docs, commit messages or dry-run summaries. Dry-run outputs mode 600 under `~/reports/`.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Full suite before the final review: `.venv/bin/python -m pytest -q -p no:cacheprovider` → 0 failed (baseline at c729d8f: 2275 passed, 3 skipped).

## Review Focus

1. A fee/interest/reward line whose stand-in the owner deleted still carries `hint: bank_only` (the hint is single-valued): the `create_system_entry` policy builder must skip it via the `owner_deleted` key, while the owner may still run `create_system_entry` — test in Task 6.
2. A stand-in whose coverage the sweep released re-enters the population as an ordinary unclaimed entry: with no owner entry it must re-claim its line (self-heal, coverage `auto`), and the policy must not backfill the line a second time meanwhile (`_check_creation_family`) — test in Task 4.
3. `anchor_opening` flags every statement of the account `needs_recheck`, and `_statement_excluded` skips flagged statements: the policy must not act on them until they are reconciled, and the next reconcile must clear the flag and re-derive `balance_gap` — test in Task 7.
4. A stand-in the owner edited only cosmetically (category/name) must still be replaceable and must keep its coverage; an amount-edited stand-in must lose coverage (sweep) and must be refused by `replace_stand_in` — tests in Tasks 2 and 8.
5. Two owner entries that both exactly match one line held by a stand-in: no `shadowed_by_stand_in` hint (not unique) — test in Task 8.

---

### Task 1: Settings, backfill category, audit events

**Files:**
- Modify: `app/services/settings_service.py` (`POLICY_VERSION_PREFIX`, `POLICY_DEFAULTS`, `_check_policy`), `app/services/entry_write_service.py` (`backfill_category_id`), `app/services/reconciliation_actions/audit.py` (`EVENTS`)
- Test: `tests/integration/test_reconciliation_service.py` (settings), `tests/integration/test_reconciliation_actions_ledger.py` (category)

**Interfaces:**
- Produces: `settings_service.POLICY_DEFAULTS["backfill_grace_days"] == 14`, `["backfill_accounts"] == []`, action blocks `backfill_entry`, `anchor_opening`, `replace_stand_in`; `POLICY_VERSION_PREFIX == "r6b"`; `ews.backfill_category_id(db, kind: str) -> int` (`kind ∈ {"expense", "income"}`, category name `對帳待分類`, created on demand, one per kind); audit events `"stand_in_retired"`, `"anchor_applied"`, `"stand_in_replaced"` accepted by `audit.record`.

- [ ] **Step 1: Write the failing tests**

In `tests/integration/test_reconciliation_service.py`:

```python
def test_r6b_policy_defaults_and_validation(db_session):
    settings = settings_service.get_reconciliation_settings(db_session)
    policy = settings["policy"]
    assert policy["backfill_grace_days"] == 14 and policy["backfill_accounts"] == []
    assert policy["actions"]["backfill_entry"] == {"enabled": False, "max_amount": "5000"}
    assert policy["actions"]["anchor_opening"] == {"enabled": False, "max_amount": "200000"}
    assert policy["actions"]["replace_stand_in"] == {"enabled": True, "max_amount": "0"}
    assert settings["policy_version"].startswith("r6b-") or settings["policy_version"] == "r1c-1"
    out = settings_service.update_reconciliation_settings(
        db_session, {"dirty_enabled": False, "policy": {"backfill_grace_days": 7, "backfill_accounts": [3, 5]}})
    assert out["policy_version"] == f"r6b-{out['version']}" and out["policy"]["backfill_grace_days"] == 7
    for bad in ({"backfill_grace_days": 400}, {"backfill_grace_days": "7"}, {"backfill_accounts": ["3"]},
                {"backfill_accounts": 3}, {"actions": {"backfill_entry": {"grace": 1}}}):
        with pytest.raises(ValidationError):
            settings_service.update_reconciliation_settings(db_session, {"dirty_enabled": False, "policy": bad})
```

In `tests/integration/test_reconciliation_actions_ledger.py`:

```python
def test_backfill_category_is_created_once_per_kind(db_session):
    a = ews.backfill_category_id(db_session, "expense")
    b = ews.backfill_category_id(db_session, "expense")
    c = ews.backfill_category_id(db_session, "income")
    assert a == b and a != c
    rows = db_session.execute(select(Category).where(Category.name == "對帳待分類").order_by(Category.id)).scalars().all()
    assert [(r.kind, r.parent_id) for r in rows] == [("expense", None), ("income", None)]
    with pytest.raises(KeyError):
        ews.backfill_category_id(db_session, "fee")


def test_audit_accepts_the_r6b_events(db_session):
    from app.services.reconciliation_actions import audit
    assert {"stand_in_retired", "anchor_applied", "stand_in_replaced"} <= audit.EVENTS
```

(`Category` and `select` are already imported in that file; add `from app.services import entry_write_service as ews` if missing.)

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_reconciliation_service.py::test_r6b_policy_defaults_and_validation tests/integration/test_reconciliation_actions_ledger.py -k "backfill_category or r6b_events"`
Expected: FAIL (`KeyError: 'backfill_grace_days'`, `AttributeError: backfill_category_id`, missing events).

- [ ] **Step 3: Implement**

`settings_service.py`:

```python
POLICY_VERSION_PREFIX = "r6b"
POLICY_DEFAULTS = {
    "enabled": False, "accept_agent_proposals": False, "per_statement_count": 5, "per_statement_total": "5000",
    "monthly_total": "20000",
    "backfill_grace_days": 14,  # R6b.1: a line is backfilled only this many days after its statement's period end
    "backfill_accounts": [],  # R6b.1: account ids the policy may backfill; empty = every account
    "actions": {"create_system_entry": {"enabled": False, "max_amount": "2000"},
                "attach_fee": {"enabled": False, "max_amount": "500"},
                "match": {"enabled": False, "max_amount": "0"},
                "backfill_entry": {"enabled": False, "max_amount": "5000"},
                "anchor_opening": {"enabled": False, "max_amount": "200000"},
                "replace_stand_in": {"enabled": True, "max_amount": "0"}},
    "per_currency_enabled": ["TWD"],
}
```

In `_check_policy`, add before the `elif key == "actions":` branch:

```python
        elif key == "backfill_grace_days":
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 365:
                raise ValidationError("policy", f"{key!r} must be an integer in 0..365")
        elif key == "backfill_accounts":
            if not isinstance(value, list) or not all(isinstance(a, int) and not isinstance(a, bool) for a in value):
                raise ValidationError("policy", f"{key!r} must be a list of account ids")
```

(the `actions` branch already refuses unknown action names and unknown keys inside an action, so the three new blocks are validated by the existing loop; update the docstring's "only the three action names" to "only the action names in POLICY_DEFAULTS").

`entry_write_service.py` (next to `system_category_id`):

```python
BACKFILL_CATEGORY_NAME = "對帳待分類"
BACKFILL_KINDS = ("expense", "income")


def backfill_category_id(db: Session, kind: str) -> int:
    """R6b.1: the fixed top-level category a backfilled stand-in is booked under, one per kind, created on demand."""
    if kind not in BACKFILL_KINDS:
        raise KeyError(kind)
    category_id = db.scalar(select(Category.id).where(Category.kind == kind, Category.parent_id.is_(None),
                                                      Category.name == BACKFILL_CATEGORY_NAME))
    if category_id is None:
        category = Category(kind=kind, name=BACKFILL_CATEGORY_NAME)
        db.add(category)
        db.flush()
        category_id = category.id
    return category_id
```

`audit.py`: add `"stand_in_retired", "anchor_applied", "stand_in_replaced"` to `EVENTS`.

- [ ] **Step 4: Run the tests**

Run: the Step 2 command plus `tests/integration/test_settings_api.py tests/integration/test_statement_ingest_api.py -k "policy or settings"`.
Expected: PASS. If an existing test asserts `policy_version` starts with `r1c-`, update it to `r6b-` and say so in the commit body.

- [ ] **Step 5: Commit**

```bash
git add app/services/settings_service.py app/services/entry_write_service.py app/services/reconciliation_actions/audit.py tests/integration/test_reconciliation_service.py tests/integration/test_reconciliation_actions_ledger.py
git commit -m "feat(reconciliation): R6b settings keys, backfill category, audit events

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Structural-only sweep release

**Files:**
- Modify: `app/services/coverage_service.py` (`STRUCTURAL_KEYS`, `structurally_changed`, the sweep's entry-update branch)
- Test: `tests/integration/test_coverage_sweep.py` (create if absent; else append to `tests/integration/test_reconciliation_service.py`)

**Interfaces:**
- Produces: `coverage_service.STRUCTURAL_KEYS: frozenset[str]`; `coverage_service.structurally_changed(snapshot: dict, entry: LedgerEntry) -> list[str]` (the structural keys whose stored snapshot value differs from `snapshot_entry(entry)`; empty = cosmetic edit); the sweep releases on `update` only when the list is non-empty or the entry row is gone.

- [ ] **Step 1: Write the failing tests**

```python
from app.services import coverage_service
from app.services import reconciliation_service as rec
from tests.integration.statement_fixtures import _active, _card, _cases, _line, _submit, run  # noqa: F401


def _dirty(db):
    from tests.helpers import set_dirty
    set_dirty(db, True)
    db.commit()


def _claimed_line(seed, db, run, merchant="全聯", **entry_fields):
    card = _card(seed, db)
    entry = seed.entry(card, "-580", day=date(2026, 9, 3), merchant=merchant, **entry_fields)
    stmt, (line,), _ = _submit(db, run, card, [_line(1, date(2026, 9, 3), merchant, 580)])
    assert len(_active(db, stmt.id)) == 1
    return card, entry, stmt, line


def _sweep(db, stmt):
    db.expire_all()
    stmt = db.get(AccountStatement, stmt.id)
    with db.begin_nested():
        rec._lock_statement(db, stmt.id)
        coverage_service.sweep(db, stmt, run_id=None, barrier_wait=False)
    db.commit()
    return stmt


@pytest.mark.parametrize("field, value", [("category_id", None), ("name", "改名"), ("merchant", "改商家"),
                                          ("description", "備註"), ("needs_review", True)])
def test_cosmetic_edit_keeps_the_match(seed, db_session, run, field, value):
    _dirty(db_session)
    card, entry, stmt, line = _claimed_line(seed, db_session, run)
    if field == "category_id":
        value = seed.category("其他").id
    setattr(entry, field, value)
    db_session.commit()
    _sweep(db_session, stmt)
    assert len(_active(db_session, stmt.id)) == 1 and not [c for c in _cases(db_session, stmt.id, "open")]


@pytest.mark.parametrize("field, value", [("amount", "-581"), ("posted_date", date(2026, 9, 4)),
                                          ("entry_date", date(2026, 9, 4)), ("is_settlement", True)])
def test_structural_edit_releases_the_match(seed, db_session, run, field, value):
    _dirty(db_session)
    card, entry, stmt, line = _claimed_line(seed, db_session, run)
    setattr(entry, field, Decimal(value) if field == "amount" else value)
    db_session.commit()
    _sweep(db_session, stmt)
    assert _active(db_session, stmt.id) == []


def test_refund_link_change_releases(seed, db_session, run):
    _dirty(db_session)
    card, entry, stmt, line = _claimed_line(seed, db_session, run)
    other = seed.entry(card, "-100", day=date(2026, 9, 5))
    db_session.commit()
    entry.refunds_entry_id = other.id
    db_session.commit()
    _sweep(db_session, stmt)
    assert _active(db_session, stmt.id) == []


def test_pre_r6b_snapshot_shape_is_compared_the_same_way(seed, db_session, run):
    # existing coverage rows carry the same snapshot keys: a cosmetic edit keeps them, an amount edit releases them
    _dirty(db_session)
    card, entry, stmt, line = _claimed_line(seed, db_session, run)
    (row,) = _active(db_session, stmt.id)
    assert set(coverage_service.STRUCTURAL_KEYS) < set(row.snapshot)
    assert coverage_service.structurally_changed(row.snapshot, entry) == []
    entry.name = "x"
    assert coverage_service.structurally_changed(row.snapshot, entry) == []
    entry.amount = Decimal("-1")
    assert coverage_service.structurally_changed(row.snapshot, entry) == ["amount", "flow"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_coverage_sweep.py`
Expected: cosmetic cases FAIL (coverage released today), `structurally_changed` missing.

- [ ] **Step 3: Implement**

`coverage_service.py`, after `snapshot_entry`:

```python
STRUCTURAL_KEYS = frozenset(snapshot_entry.__code__.co_consts and {
    "amount", "flow", "currency", "original_amount", "original_currency", "entry_date", "posted_date",
    "account_id", "kind", "group_id", "parent_entry_id", "transfer_group_id", "refunds_entry_id",
    "settles_entry_id", "is_settlement"})  # R6b.0: every snapshot key except id, name, merchant


def structurally_changed(snapshot: dict, entry: LedgerEntry) -> list[str]:
    """R6b.0: the structural keys on which the entry now differs from the coverage snapshot (cosmetic edits —
    name, merchant, category, description, needs_review — never count). Compared as the snapshot stores them."""
    now = snapshot_entry(entry)
    return sorted(k for k in STRUCTURAL_KEYS if (snapshot or {}).get(k) != now.get(k))
```

(write `STRUCTURAL_KEYS` as the plain literal frozenset — the `__code__` trick above is NOT to be copied; it is here only to say "do not derive it from the function".)

In `sweep`, replace the entry branch:

```python
            if ev.kind == "entry":
                if ev.row_id in lines_by_entry:
                    if ev.op == "delete":
                        release(lines_by_entry[ev.row_id], ev, f"dirty:delete:{ev.id}")
                    elif ev.op == "update" and _structural_update(db, active, lines_by_entry[ev.row_id], ev.row_id):
                        release(lines_by_entry[ev.row_id], ev, f"dirty:update:{ev.id}")
                elif in_population(ev.new_account_id, ev.new_date) or in_population(ev.old_account_id, ev.old_date):
                    result.recheck = result.recheck or live
```

with the helper (module level):

```python
def _structural_update(db: Session, active: dict[int, list[StatementCoverage]], line_ids: set[int], entry_id: int) -> bool:
    """R6b.0: an update event releases only when a structural key differs from the stored snapshot of any active
    row holding the entry; a vanished row counts as changed."""
    entry = db.get(LedgerEntry, entry_id)
    if entry is None:
        return True
    for line_id in line_ids:
        for row in active.get(line_id, ()):
            if row.entry_id == entry_id or (row.snapshot or {}).get("id") == entry_id:
                if structurally_changed(row.snapshot, entry):
                    return True
    return False
```

Import `LedgerEntry` in `coverage_service` if not already imported.

- [ ] **Step 4: Run the file, then the sweep-related suites**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_coverage_sweep.py tests/integration/test_reconciliation_service.py tests/integration/test_reconciliation_actions_engine.py`
Expected: PASS. An existing test that edits a covered entry's `name`/`merchant` and expects a release now fails by design — change its edit to `amount` and record the change in the commit body.

- [ ] **Step 5: Commit**

```bash
git add app/services/coverage_service.py tests/integration/test_coverage_sweep.py
git commit -m "feat(reconciliation): R6b.0 structural-only sweep release

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Matcher — stand-in flag, owner-wins rule, `needs_peer` hint, `create_entry` condition

**Files:**
- Modify: `app/services/statements/matching.py` (`Entry.stand_in`, `_representations`, `match()` fallback), `app/services/reconciliation_actions/__init__.py` (`CONDITIONS[("line_unmatched", "create_entry")]`)
- Test: `tests/unit/test_statement_matching.py`, `tests/integration/test_reconciliation_actions_ledger.py`

**Interfaces:**
- Produces: `matching.Entry.stand_in: bool = False`; `PEER_LINE_KINDS = frozenset({"payment", "transfer_in", "transfer_out"})`; a bare fallback case for those kinds carries `{"hint": "needs_peer"}`; `_representations` drops stand-in representations when any non-stand-in representation exists; `reconciliation_actions.PEER_HINT = "needs_peer"` and `create_entry` is not allowed on a case with that hint. Task 4 sets `stand_in` from provenance; Task 5 relies on the `create_entry` condition shape for `backfill_entry`.

- [ ] **Step 1: Write the failing tests**

Unit (`tests/unit/test_statement_matching.py`; `E` gains `stand_in=kw.get("stand_in", False)` — add that argument to the helper):

```python
def test_owner_representation_drops_stand_in_representations():
    # R6b.0: a stand-in (backfilled entry) never competes with the owner's entry for the same line
    entries = [E(10, date(2026, 9, 3), "-580", merchant="", stand_in=True), E(11, date(2026, 9, 3), "-580", merchant="")]
    res = run([L(1, date(2026, 9, 3), "-580", merchant="")], entries)
    assert [c.representation.rows[0].entry_id for c in res.claims] == [11]
    assert 10 in res.unmatched_entry_ids


def test_stand_ins_compete_only_among_themselves():
    entries = [E(10, date(2026, 9, 3), "-580", merchant="", stand_in=True), E(11, date(2026, 9, 3), "-580", merchant="", stand_in=True)]
    res = run([L(1, date(2026, 9, 3), "-580", merchant="")], entries)
    assert res.claims == [] and res.cases[0].kind == "ambiguous"
    res = run([L(1, date(2026, 9, 3), "-580", merchant="")], entries[:1])
    assert [c.representation.rows[0].entry_id for c in res.claims] == [10]


def test_stand_in_drop_applies_to_group_and_child_representations_too():
    g = m.Group(id=5, kind="split", member_ids=(21, 22))
    entries = [E(10, date(2026, 9, 3), "-580", merchant="", stand_in=True),
               E(21, date(2026, 9, 3), "-300", group_id=5, merchant=""), E(22, date(2026, 9, 3), "-280", group_id=5, merchant="")]
    res = run([L(1, date(2026, 9, 3), "-580", merchant="")], entries, groups={5: g})
    assert res.claims and res.claims[0].representation.rule == "group"


@pytest.mark.parametrize("kind, stmt", [("payment", "card"), ("transfer_in", "bank"), ("transfer_out", "bank"), ("payment", "bank")])
def test_payment_and_transfer_lines_without_a_match_get_needs_peer(kind, stmt):
    res = run([L(1, date(2026, 9, 3), "-20000" if kind != "transfer_in" else "20000", kind=kind, merchant="")], [], kind=stmt)
    assert res.cases[0].kind == "line_unmatched" and res.cases[0].context == {"hint": "needs_peer"}


def test_needs_peer_is_not_set_when_a_near_or_drift_case_exists():
    ok = E(10, date(2026, 9, 3), "20200", kind="transfer_in", peer_is_card=False)
    res = run([L(1, date(2026, 9, 3), "20000", kind="payment", merchant="")], [ok])
    assert res.cases[0].kind == "amount_delta"
```

(`pytest` import at the top of the unit file if absent.)

Integration (`tests/integration/test_reconciliation_actions_ledger.py`):

```python
def test_create_entry_is_not_offered_on_a_needs_peer_case(seed, db_session, run):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "繳款", 20000, "payment")])
    (case,) = _cases(db_session, stmt.id, "open")
    assert case.context == {"hint": "needs_peer"}
    assert "create_entry" not in registry.allowed(case) and {"match", "dismiss"} <= set(registry.allowed(case))
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/unit/test_statement_matching.py -k "stand_in or needs_peer" tests/integration/test_reconciliation_actions_ledger.py -k needs_peer`
Expected: FAIL (`Entry` has no `stand_in`; context `{}`; `create_entry` offered).

- [ ] **Step 3: Implement**

`matching.py`:

```python
    claimable: bool = True
    stand_in: bool = False  # R6b.0: a backfilled entry (statement source, applied backfill_entry); owner rows win over it
```

At the end of `_representations` (replace the final `return`):

```python
    reps = [r for r in out if conserved(r.rows, line.flow)]
    by_id = {e.id: e for e in entries}
    if any(not _is_stand_in_rep(r, by_id) for r in reps):  # R6b.0: an owner representation drops every stand-in one
        reps = [r for r in reps if not _is_stand_in_rep(r, by_id)]
    return reps


def _is_stand_in_rep(rep: Representation, by_id: dict[int, Entry]) -> bool:
    """A representation made only of stand-in rows (a child row counts as its parent's)."""
    owners = {by_id[r.entry_id] for r in rep.rows if r.entry_id in by_id}
    children = {c.id for e in by_id.values() for c in e.children}
    parents = [e for e in by_id.values() if any(r.entry_id in {c.id for c in e.children} for r in rep.rows)]
    return bool(owners | set(parents)) and all(e.stand_in for e in owners | set(parents))
```

Add `PEER_LINE_KINDS = frozenset({"payment", "transfer_in", "transfer_out"})` next to `BANK_ONLY_KINDS`, and change the bare fallback in `match()`:

```python
        if line.line_kind in PEER_LINE_KINDS:  # R6b.3/R6b.4: transfers wait for R6c; the owner matches or dismisses
            result.cases.append(Case(line.id, None, "line_unmatched", (), {"hint": "needs_peer"}))
            continue
        result.cases.append(Case(line.id, None, "line_unmatched", (), {}))
```

`reconciliation_actions/__init__.py`:

```python
PEER_HINT = "needs_peer"


def _no_peer_hint(case: ReconciliationCase) -> bool:  # R6b: a transfer/payment line gets no single-leg entry
    return _context(case).get("hint") != PEER_HINT
```

and `("line_unmatched", "create_entry"): _no_peer_hint,` in `CONDITIONS`.

- [ ] **Step 4: Run the unit file and the actions-ledger file**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/unit/test_statement_matching.py tests/integration/test_reconciliation_actions_ledger.py tests/integration/test_reconciliation_service.py`
Expected: PASS (an existing test asserting `context == {}` on a payment line changes to `{"hint": "needs_peer"}` — note in the commit body).

- [ ] **Step 5: Commit**

```bash
git add app/services/statements/matching.py app/services/reconciliation_actions/__init__.py tests/unit/test_statement_matching.py tests/integration/test_reconciliation_actions_ledger.py
git commit -m "feat(reconciliation): R6b.0 stand-in representations lose to owner rows; needs_peer hint

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Service — stand-in provenance in the population, owner-deleted marker in `_desired_cases`

**Files:**
- Create: `app/services/stand_ins.py`
- Modify: `app/services/reconciliation_service.py` (`load_candidates`, `_desired_cases`)
- Test: `tests/integration/test_stand_ins.py`

**Interfaces:**
- Produces: `stand_ins.BACKFILL_ACTIONS = frozenset({"backfill_entry"})` (R6c extends); `stand_ins.stand_in_entry_ids(db, account_ids: list[int]) -> set[int]` (entries created by applied `BACKFILL_ACTIONS` on statements of those accounts, still existing); `stand_ins.creation_for_entry(db, entry_id) -> ReconciliationAction | None` (the applied creation whose `created_entry_ids` contains the id); `stand_ins.owner_deleted_events(db, statement) -> set[int]` (event ids whose latest creation action is `reverted` with `inverse.deleted_by_event` set and no later applied creation); `load_candidates` sets `Entry.stand_in`; `_desired_cases` adds `context["owner_deleted"] = True` on the line case of an owner-deleted event and `hint = "stand_in_deleted"` when the case has no hint. Tasks 5, 6, 8 consume these.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_stand_ins.py`:

```python
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import LedgerEntry, ReconciliationAction, ReconciliationCase
from app.services import reconciliation_service as rec
from app.services import stand_ins
from app.services.reconciliation_actions import engine
from tests.integration.statement_fixtures import _active, _card, _cases, _line, _submit, run  # noqa: F401

DAY = date(2026, 9, 3)


def _fake_backfill(db, stmt, line, entry):
    """A stand-in as Task 5 will create it: an applied backfill_entry action whose created_entry_ids names the entry."""
    (case,) = [c for c in _cases(db, stmt.id, "open") if c.line_id == line.id]
    row = ReconciliationAction(idempotency_key=f"t-{entry.id}", event_id=line.event_id, effect_slot="create", case_id=case.id,
                               action="backfill_entry", params={"kind": "expense"}, actor="policy", status="applied",
                               created_entry_ids=[entry.id], inverse={"created_entry_id": entry.id})
    db.add(row)
    db.commit()
    return row


def test_stand_in_ids_and_creation_lookup(seed, db_session, run):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    entry = seed.entry(card, "-580", day=DAY, source="statement")
    db_session.commit()
    row = _fake_backfill(db_session, stmt, line, entry)
    assert stand_ins.stand_in_entry_ids(db_session, [card.id]) == {entry.id}
    assert stand_ins.creation_for_entry(db_session, entry.id).id == row.id
    row.status = "reverted"
    db_session.commit()
    assert stand_ins.stand_in_entry_ids(db_session, [card.id]) == set()


def test_population_marks_stand_ins_and_owner_entry_wins_on_reconcile(seed, db_session, run):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    stand_in = seed.entry(card, "-580", day=DAY, source="statement", merchant="全聯")
    db_session.commit()
    _fake_backfill(db_session, stmt, line, stand_in)
    owner = seed.entry(card, "-580", day=DAY, merchant="全聯")
    db_session.commit()
    rec.reconcile(db_session, stmt.id)
    db_session.commit()
    (cov,) = _active(db_session, stmt.id)
    assert cov.entry_id == owner.id
    orphan = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "entry_unmatched" and c.entry_id == stand_in.id]
    assert orphan  # Task 8 adds the orphaned_stand_in hint; here only the unmatched stand-in is asserted


def test_stand_in_alone_self_heals_after_release(seed, db_session, run):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    stand_in = seed.entry(card, "-580", day=DAY, source="statement", merchant="全聯")
    db_session.commit()
    _fake_backfill(db_session, stmt, line, stand_in)
    rec.reconcile(db_session, stmt.id)
    db_session.commit()
    (cov,) = _active(db_session, stmt.id)
    assert cov.entry_id == stand_in.id and cov.match_kind == "auto"


def test_owner_deleted_events_are_derived_from_the_action_table(seed, db_session, run):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    entry = seed.entry(card, "-580", day=DAY, source="statement")
    db_session.commit()
    row = _fake_backfill(db_session, stmt, line, entry)
    assert stand_ins.owner_deleted_events(db_session, stmt) == set()
    row.status, row.inverse = "reverted", {**row.inverse, "deleted_by_event": 1}
    db_session.commit()
    assert stand_ins.owner_deleted_events(db_session, stmt) == {line.event_id}
    rec.reconcile(db_session, stmt.id)
    db_session.commit()
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.line_id == line.id]
    assert case.context.get("owner_deleted") is True and case.context.get("hint") == "stand_in_deleted"
    rec.reconcile(db_session, stmt.id)  # re-derived, not carried
    db_session.commit()
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.line_id == line.id]
    assert case.context.get("owner_deleted") is True
    later = ReconciliationAction(idempotency_key="t-later", event_id=line.event_id, effect_slot="create", case_id=case.id,
                                 action="create_entry", params={}, actor="owner", status="applied", created_entry_ids=[])
    db_session.add(later)
    db_session.commit()
    assert stand_ins.owner_deleted_events(db_session, stmt) == set()


def test_owner_deleted_keeps_an_existing_hint(seed, db_session, run):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "年費", 1200, "fee")])
    (case,) = _cases(db_session, stmt.id, "open")
    assert case.context["hint"] == "bank_only"
    row = ReconciliationAction(idempotency_key="t-fee", event_id=line.event_id, effect_slot="create", case_id=case.id,
                               action="create_system_entry", params={"kind": "fee"}, actor="policy", status="reverted",
                               created_entry_ids=[], inverse={"created_entry_id": 0, "deleted_by_event": 1})
    db_session.add(row)
    db_session.commit()
    rec.reconcile(db_session, stmt.id)
    db_session.commit()
    (case,) = _cases(db_session, stmt.id, "open")
    assert case.context["hint"] == "bank_only" and case.context["owner_deleted"] is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_stand_ins.py`
Expected: FAIL (`ModuleNotFoundError: app.services.stand_ins`).

- [ ] **Step 3: Implement**

`app/services/stand_ins.py`:

```python
"""R6b.0 stand-ins: provenance of backfilled entries and the owner-deleted marker, derived from the action table.
Read only; the engine and the sweep write the action rows."""
from __future__ import annotations

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.models import AccountStatement, LedgerEntry, ReconciliationAction, ReconciliationCase

BACKFILL_ACTIONS = frozenset({"backfill_entry"})  # R6c adds its transfer actions
CREATION_SLOT = "create"


def stand_in_entry_ids(db: Session, account_ids: list[int]) -> set[int]:
    """Entries created by an applied backfill action on a statement of these accounts that still exist."""
    rows = db.execute(
        select(ReconciliationAction.created_entry_ids)
        .join(ReconciliationCase, ReconciliationCase.id == ReconciliationAction.case_id)
        .join(AccountStatement, AccountStatement.id == ReconciliationCase.statement_id)
        .where(AccountStatement.account_id.in_(account_ids), ReconciliationAction.status == "applied",
               ReconciliationAction.action.in_(BACKFILL_ACTIONS))).all()
    ids = {i for (created,) in rows for i in (created or ())}
    if not ids:
        return set()
    return set(db.execute(select(LedgerEntry.id).where(LedgerEntry.id.in_(ids))).scalars())


def creation_for_entry(db: Session, entry_id: int) -> ReconciliationAction | None:
    """The applied creation action (any creation family) whose created_entry_ids names the entry."""
    return db.execute(
        select(ReconciliationAction)
        .where(ReconciliationAction.status == "applied", ReconciliationAction.effect_slot == CREATION_SLOT,
               ReconciliationAction.created_entry_ids.contains([entry_id]))
        .order_by(ReconciliationAction.id.desc())).scalars().first()


def owner_deleted_events(db: Session, statement: AccountStatement) -> set[int]:
    """Events of this statement whose LATEST creation action is reverted with inverse.deleted_by_event set and no
    later applied creation exists: the owner removed the created entry in the ledger on purpose (R6b.0)."""
    rows = db.execute(
        select(ReconciliationAction.event_id, ReconciliationAction.status, ReconciliationAction.inverse)
        .join(ReconciliationCase, ReconciliationCase.id == ReconciliationAction.case_id)
        .where(ReconciliationCase.statement_id == statement.id, ReconciliationAction.effect_slot == CREATION_SLOT,
               ReconciliationAction.event_id.is_not(None))
        .order_by(ReconciliationAction.event_id, ReconciliationAction.id)).all()
    latest: dict[int, tuple[str, dict]] = {}
    for event_id, status, inverse in rows:
        if status in ("applied", "reverted"):
            latest[event_id] = (status, inverse or {})
    return {e for e, (status, inverse) in latest.items() if status == "reverted" and inverse.get("deleted_by_event")}
```

`reconciliation_service.py`, in `load_candidates` before the `entries = []` loop: `stand_in_ids = stand_ins.stand_in_entry_ids(db, accounts)` and in the `matching.Entry(...)` construction add `stand_in=e.id in stand_in_ids,`. (Import `from app.services import stand_ins` at the top; `stand_ins` imports no service module, so no cycle.)

In `_desired_cases`, before the `for c in result.cases:` loop: `deleted = stand_ins.owner_deleted_events(db, statement)`, and inside the loop after the sibling block:

```python
        event_id = lines[c.line_id].event_id
        if c.kind == "line_unmatched" and event_id in deleted:  # R6b.0: derived every reconcile, never carried
            context = {**(context or {}), "owner_deleted": True}
            context.setdefault("hint", "stand_in_deleted")
```

(use `event_id` in the `_case(...)` call too).

- [ ] **Step 4: Run the file and the service suite**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_stand_ins.py tests/integration/test_reconciliation_service.py tests/unit/test_statement_matching.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/stand_ins.py app/services/reconciliation_service.py tests/integration/test_stand_ins.py
git commit -m "feat(reconciliation): R6b.0 stand-in provenance in the population, owner-deleted marker derived per reconcile

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: `backfill_entry` action and policy builder

**Files:**
- Modify: `app/services/reconciliation_actions/ledger_actions.py` (`BackfillEntryParams`, `BackfillEntryAction`, `backfill_kind`), `app/services/reconciliation_actions/__init__.py` (`KIND_ACTIONS`, `CONDITIONS`, `backfill_eligibility`), `app/services/policy_service.py` (`_backfill_candidate`, `POLICY_ACTIONS`, `_unchanged`)
- Test: `tests/integration/test_reconciliation_actions_ledger.py`, `tests/integration/test_reconciliation_policy.py`

**Interfaces:**
- Consumes: `ews.backfill_category_id` (Task 1), `PEER_HINT` (Task 3), `context.owner_deleted` (Task 4), settings keys (Task 1).
- Produces: `ledger_actions.backfill_kind(statement_kind: str, line_kind: str) -> str | None` (`"expense"`, `"income"` or None per the Global Constraints map); `BackfillEntryParams(kind: Literal["expense", "income"])`; action `backfill_entry` (case kind `line_unmatched`, slot CREATE, actors OWNER+POLICY); `reconciliation_actions.backfill_eligibility(case, line) -> str | None` (the entry kind when the owner may run it: hint none or `stand_in_deleted`, no candidates, kind map) used by `CONDITIONS`; `policy_service._backfill_candidate` (adds: statement live, grace, account list, not owner-deleted); candidate params `{"kind": ...}`, amount `|flow|`.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_reconciliation_actions_ledger.py`:

```python
@pytest.mark.parametrize("stmt_kind, line_kind, printed, expected", [
    ("card", "purchase", 580, "expense"), ("bank", "withdrawal", 580, "expense"), ("bank", "purchase", 580, "expense"),
    ("bank", "deposit", 3000, "income"), ("card", "fee", 50, None), ("bank", "unknown", 10, None),
    ("bank", "transfer_out", 500, None), ("card", "payment", 20000, None), ("card", "refund", 100, None)])
def test_backfill_kind_map(stmt_kind, line_kind, printed, expected):
    from app.services.reconciliation_actions.ledger_actions import backfill_kind
    assert backfill_kind(stmt_kind, line_kind) == expected


def test_owner_backfill_books_a_stand_in_and_reverts(seed, db_session, run):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    (case,) = _cases(db_session, stmt.id, "open")
    assert "backfill_entry" in registry.allowed(case)
    row = _apply(db_session, case, "backfill_entry", {"kind": "expense"})
    (entry,) = _booked(db_session)
    assert entry.kind == "expense" and entry.amount == Decimal("-580") and entry.needs_review is True
    assert entry.category_id == ews.backfill_category_id(db_session, "expense") and entry.source == "statement"
    (cov,) = _active(db_session, stmt.id)
    assert cov.entry_id == entry.id and cov.match_rule == "backfill_entry" and cov.match_kind == "manual"
    assert _reload(db_session, ReconciliationCase, case.id).status == "resolved"
    _revert(db_session, row.id)
    assert _booked(db_session) == [] and _active(db_session, stmt.id) == []


def test_backfill_refuses_a_kind_the_line_cannot_take(seed, db_session, run):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    (case,) = _cases(db_session, stmt.id, "open")
    assert _code(_refuse(db_session, lambda: _apply(db_session, case, "backfill_entry", {"kind": "income"}))) == "kind_sign"


def test_backfill_not_offered_on_needs_peer_fee_or_candidate_cases(seed, db_session, run):
    card = _card(seed, db_session)
    entry = seed.entry(card, "-580", day=date(2026, 9, 10), merchant="全聯")
    stmt, lines, _ = _submit(db_session, run, card, [_line(1, DAY, "繳款", 20000, "payment"), _line(2, DAY, "年費", 1200, "fee"),
                                                     _line(3, DAY, "全聯", 580)])
    by_line = {c.line_id: c for c in _cases(db_session, stmt.id, "open")}
    assert "backfill_entry" not in registry.allowed(by_line[lines[0].id])  # needs_peer
    assert "backfill_entry" not in registry.allowed(by_line[lines[1].id])  # bank_only
    assert by_line[lines[2].id].kind == "amount_delta"  # date drift proposal: not a backfill case


def test_second_backfill_of_the_same_event_is_refused_while_the_first_is_applied(seed, db_session, run):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    (case,) = _cases(db_session, stmt.id, "open")
    row = _apply(db_session, case, "backfill_entry", {"kind": "expense"})
    reopened = _hand_case(db_session, stmt, line, "line_unmatched")  # a sweep-reopened case for the same event
    assert _code(_refuse(db_session, lambda: _apply(db_session, reopened, "backfill_entry", {"kind": "expense"}))) == "duplicate_effect"
    _revert(db_session, row.id)
    _apply(db_session, reopened, "backfill_entry", {"kind": "expense"})
    assert len(_booked(db_session)) == 1
```

(`_hand_case(db, stmt, line, kind, **fields)` exists in this file at line ~75.)

`tests/integration/test_reconciliation_policy.py` (helpers `_set_policy`, `_booked`, `_actions`, `_proposals`, `TODAY = 2026-10-12`, `DAY = 2026-09-03`; a September statement's `period_end` is 2026-09-30, so TODAY is 12 days after it):

```python
BACKFILL_ON = {"enabled": True, "actions": {"backfill_entry": {"enabled": True, "max_amount": "5000"}}}


def _purchase_statement(seed, db, run, name="卡", **card):
    account = _card(seed, db, name, **card)
    stmt, (line,), _ = _submit(db, run, account, [_line(1, DAY, "全聯", 580)])
    (case,) = _cases(db, stmt.id, "open")
    return SimpleNamespace(card=account, stmt=stmt, line=line, case=case)


def test_policy_backfills_after_the_grace_and_not_before(seed, db_session, run, today):
    s = _purchase_statement(seed, db_session, run)
    _set_policy(db_session, {**BACKFILL_ON, "backfill_grace_days": 14})
    assert policy_service.run_policy(db_session, s.stmt.id).applied == [] and _booked(db_session) == []
    assert policy_service.run_policy(db_session, s.stmt.id).skipped.get("not_eligible", 0) >= 0
    _set_policy(db_session, {**BACKFILL_ON, "backfill_grace_days": 10})
    result = policy_service.run_policy(db_session, s.stmt.id)
    (row,) = _actions(db_session)
    assert result.applied == [row.id] and row.action == "backfill_entry" and row.params == {"kind": "expense"}
    (entry,) = _booked(db_session)
    assert entry.kind == "expense" and entry.needs_review is True


def test_policy_backfill_respects_the_account_list_and_caps(seed, db_session, run):
    s = _purchase_statement(seed, db_session, run)
    _set_policy(db_session, {**BACKFILL_ON, "backfill_grace_days": 0, "backfill_accounts": [s.card.id + 1000]})
    assert policy_service.run_policy(db_session, s.stmt.id).applied == []
    _set_policy(db_session, {**BACKFILL_ON, "backfill_grace_days": 0, "backfill_accounts": [s.card.id],
                             "actions": {"backfill_entry": {"enabled": True, "max_amount": "100"}}})
    result = policy_service.run_policy(db_session, s.stmt.id)
    (p,) = _proposals(db_session, status="pending")
    assert result.proposed == [p.id] and p.action == "backfill_entry" and p.params == {"kind": "expense"}
    row = proposal_service.apply_proposal(db_session, proposal_id=p.id, actor="owner", label="owner-test")
    db_session.commit()
    assert row.status == "applied" and len(_booked(db_session)) == 1


def test_policy_never_backfills_unknown_needs_peer_or_owner_deleted_lines(seed, db_session, run):
    account = _card(seed, db_session)
    stmt, lines, _ = _submit(db_session, run, account, [_line(1, DAY, "??", 10, "unknown"), _line(2, DAY, "繳款", 20000, "payment"),
                                                        _line(3, DAY, "全聯", 580)])
    _set_policy(db_session, {**BACKFILL_ON, "backfill_grace_days": 0})
    result = policy_service.run_policy(db_session, stmt.id)
    (row,) = _actions(db_session)
    assert result.applied == [row.id] and row.event_id == lines[2].event_id
    (case,) = [c for c in _cases(db_session, stmt.id, "resolved")]
    sweep_delete_marks(db_session, row)  # helper defined in Task 6; until then simulate: row.status, row.inverse = "reverted", {**row.inverse, "deleted_by_event": 1}
    rec.reconcile(db_session, stmt.id)
    db_session.commit()
    assert policy_service.run_policy(db_session, stmt.id).applied == []
```

(For Task 5, replace the `sweep_delete_marks(...)` line with the simulated two assignments and a `db_session.commit()`; Task 6 swaps in the real sweep.)

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_reconciliation_actions_ledger.py -k backfill tests/integration/test_reconciliation_policy.py -k backfill`
Expected: FAIL (`backfill_kind` missing; `backfill_entry` not allowed; policy applies nothing).

- [ ] **Step 3: Implement**

`ledger_actions.py`:

```python
BACKFILL_KINDS = {("card", "purchase"): "expense", ("bank", "withdrawal"): "expense", ("bank", "purchase"): "expense",
                  ("bank", "deposit"): "income"}


def backfill_kind(statement_kind: str, line_kind: str) -> str | None:
    """R6b.1: the entry kind a statement line is backfilled as, or None when the line is never backfilled."""
    return BACKFILL_KINDS.get((statement_kind, line_kind))


class BackfillEntryParams(ActionParams):
    kind: Literal["expense", "income"]


class BackfillEntryAction(_Creation):
    """R6b.1: books an unmatched purchase/withdrawal/deposit line as a stand-in (category 對帳待分類, needs_review)."""
    name = "backfill_entry"
    Params = BackfillEntryParams
    case_kinds = frozenset({"line_unmatched"})
    actors = frozenset({OWNER, POLICY})

    def _check(self, ctx: Context, params: BackfillEntryParams) -> StatementLine:
        line = _open_line(ctx)
        if backfill_kind(ctx.statement.kind, line.line_kind) != params.kind:
            raise _refuse("kind", "kind_sign", f"{line.line_kind} on a {ctx.statement.kind} statement is not backfilled as {params.kind}")
        return line

    def validate_before(self, ctx: Context, params: BackfillEntryParams, prepared: Prepared, before: dict) -> None:
        self._check(ctx, params)

    def apply(self, ctx: Context, params: BackfillEntryParams, prepared: Prepared, effect: Effect) -> None:
        line = self._check(ctx, params)
        self._book(ctx, line, effect, kind=params.kind, category_id=ews.backfill_category_id(ctx.db, params.kind),
                   needs_review=True)
```

and `register(BackfillEntryAction())` beside the other registrations.

`reconciliation_actions/__init__.py`:

```python
STAND_IN_DELETED_HINT = "stand_in_deleted"


def backfill_eligibility(case: ReconciliationCase) -> str | None:
    """R6b.1 owner column: no hint (or the owner-deleted hint), no candidates, a line kind the map books."""
    from app.services.reconciliation_actions.ledger_actions import backfill_kind  # local: ledger_actions imports this module
    ctx = _context(case)
    if ctx.get("hint") not in (None, STAND_IN_DELETED_HINT) or case.candidates:
        return None
    db = object_session(case)
    statement = None if db is None else db.get(AccountStatement, case.statement_id)
    kind = _line_kind(case)
    return None if statement is None or kind is None else backfill_kind(statement.kind, kind)
```

`KIND_ACTIONS["line_unmatched"]` gains `"backfill_entry"` after `"create_system_entry"`; `CONDITIONS[("line_unmatched", "backfill_entry")] = lambda case: backfill_eligibility(case) is not None`.

`policy_service.py`:

```python
def _backfill_candidate(db: Session, case: ReconciliationCase, statement: AccountStatement) -> Candidate | None:
    """R6b.1 policy column: the owner eligibility plus live mode, the grace, the account list and no owner delete."""
    if case.kind != "line_unmatched" or (case.context or {}).get("owner_deleted"):
        return None
    kind = registry.backfill_eligibility(case)
    if kind is None or statement.mode != "live":
        return None
    policy = settings_service.reconciliation_policy(db)
    if statement.period_end + timedelta(days=int(policy["backfill_grace_days"])) > ledger_service._today():
        return None
    accounts = policy.get("backfill_accounts") or []
    if accounts and statement.account_id not in accounts:
        return None
    line = current_line(db, case, statement)
    if line is None:
        return None
    return Candidate(case.id, case.kind, case.status, "backfill_entry", {"kind": kind}, _money(line.flow_amount))
```

`POLICY_ACTIONS["backfill_entry"] = _backfill_candidate`; in `_unchanged` add:

```python
    if candidate.action == "backfill_entry":
        return not context.get("owner_deleted") and not case.candidates \
            and registry.backfill_eligibility(case) == candidate.params.get("kind")
```

(`registry` = `from app.services import reconciliation_actions as registry`, already how the policy module refers to it; `timedelta`, `ledger_service` imports as needed. `ledger_service._today()` is what `run_policy` uses, monkeypatched by the `today` fixture.)

- [ ] **Step 4: Run both files and the engine/actions suites**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_reconciliation_actions_ledger.py tests/integration/test_reconciliation_policy.py tests/integration/test_reconciliation_actions_engine.py tests/integration/test_stand_ins.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/reconciliation_actions/ledger_actions.py app/services/reconciliation_actions/__init__.py app/services/policy_service.py tests/integration/test_reconciliation_actions_ledger.py tests/integration/test_reconciliation_policy.py
git commit -m "feat(reconciliation): R6b.1 backfill_entry action and policy builder

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Sweep retirement of a creation whose entry the owner deleted; policy suppression

**Files:**
- Modify: `app/services/coverage_service.py` (sweep `delete` branch → `_retire_creations`), `app/services/policy_service.py` (`_system_entry_candidate` skips `owner_deleted`)
- Test: `tests/integration/test_stand_ins.py`, `tests/integration/test_reconciliation_policy.py`

**Interfaces:**
- Consumes: `stand_ins.creation_for_entry` (Task 4), audit event `stand_in_retired` (Task 1).
- Produces: on a `delete` dirty event for an entry named in an applied creation action (`backfill_entry`, `create_entry`, `create_system_entry`, `create_refund`) of the sweeping statement, the sweep sets `status="reverted"`, keeps `reverted_by_action_id`, sets `inverse["deleted_by_event"] = ev.id`, writes audit `stand_in_retired` `{action_id, entry_id, event_id}`; idempotent; the line's coverage is released as today; `_system_entry_candidate` returns None when `context.owner_deleted`.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_stand_ins.py`:

```python
from app.services.reconciliation_actions import audit  # noqa: E402


def _sweep(db, stmt):
    db.expire_all()
    stmt = db.get(AccountStatement, stmt.id)
    with db.begin_nested():
        rec._lock_statement(db, stmt.id)
        coverage_service.sweep(db, stmt, run_id=None, barrier_wait=False)
    db.commit()


@pytest.mark.parametrize("action, line_kind, printed, params", [
    ("backfill_entry", "purchase", 580, {"kind": "expense"}), ("create_entry", "purchase", 580, None),
    ("create_system_entry", "fee", 50, {"kind": "fee"}), ("create_refund", "refund", 100, {})])
def test_ledger_delete_retires_the_creation_and_marks_the_event(seed, db_session, run, dirty_on, action, line_kind, printed, params):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "x", printed, line_kind)])
    (case,) = _cases(db_session, stmt.id, "open")
    if params is None:
        params = {"kind": "expense", "category_id": seed.category("食").id}
    row, _ = engine.apply_action(db_session, case_id=case.id, action=action, params=params, actor="owner", label="t")
    db_session.commit()
    (entry_id,) = row.created_entry_ids
    db_session.delete(db_session.get(LedgerEntry, entry_id))
    db_session.commit()
    _sweep(db_session, stmt)
    row = db_session.get(ReconciliationAction, row.id)
    assert row.status == "reverted" and row.reverted_by_action_id is None and row.inverse["deleted_by_event"]
    assert _active(db_session, stmt.id) == []
    _sweep(db_session, stmt)  # idempotent
    assert db_session.get(ReconciliationAction, row.id).inverse["deleted_by_event"] == row.inverse["deleted_by_event"]
    rec.reconcile(db_session, stmt.id)
    db_session.commit()
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.line_id == line.id]
    assert case.context.get("owner_deleted") is True
    assert "create_entry" in registry.allowed(case) or action == "create_refund"


def test_retirement_never_overwrites_an_engine_revert_attribution(seed, db_session, run, dirty_on):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    (case,) = _cases(db_session, stmt.id, "open")
    row, _ = engine.apply_action(db_session, case_id=case.id, action="backfill_entry", params={"kind": "expense"}, actor="owner", label="t")
    db_session.commit()
    rev = engine.revert_action(db_session, action_id=row.id, actor="owner", label="t")
    db_session.commit()
    _sweep(db_session, stmt)
    row = db_session.get(ReconciliationAction, row.id)
    assert row.status == "reverted" and row.reverted_by_action_id == rev.id and "deleted_by_event" not in (row.inverse or {})
    rec.reconcile(db_session, stmt.id)
    db_session.commit()
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.line_id == line.id]
    assert "owner_deleted" not in (case.context or {})  # an engine revert is not an owner delete: the policy may backfill again


def test_edit_release_then_delete_still_retires(seed, db_session, run, dirty_on):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    (case,) = _cases(db_session, stmt.id, "open")
    row, _ = engine.apply_action(db_session, case_id=case.id, action="backfill_entry", params={"kind": "expense"}, actor="owner", label="t")
    db_session.commit()
    entry = db_session.get(LedgerEntry, row.created_entry_ids[0])
    entry.amount = Decimal("-1")
    db_session.commit()
    _sweep(db_session, stmt)
    assert _active(db_session, stmt.id) == []
    db_session.delete(db_session.get(LedgerEntry, entry.id))
    db_session.commit()
    _sweep(db_session, stmt)
    assert db_session.get(ReconciliationAction, row.id).status == "reverted"
```

`tests/integration/test_reconciliation_policy.py` — replace the Task 5 simulation in `test_policy_never_backfills_unknown_needs_peer_or_owner_deleted_lines` with a real ledger delete + sweep (use the `_sweep` helper pattern above), and add:

```python
def test_policy_skips_an_owner_deleted_fee_line_but_the_owner_may_rebook_it(seed, db_session, run, dirty_on):
    s = _fee_statement(seed, db_session, run)
    _set_policy(db_session, SYSTEM_ON)
    (row_id,) = policy_service.run_policy(db_session, s.stmt.id).applied
    row = db_session.get(ReconciliationAction, row_id)
    db_session.delete(db_session.get(LedgerEntry, row.created_entry_ids[0]))
    db_session.commit()
    _sweep(db_session, s.stmt)
    rec.reconcile(db_session, s.stmt.id)
    db_session.commit()
    (case,) = _cases(db_session, s.stmt.id, "open")
    assert case.context["hint"] == "bank_only" and case.context["owner_deleted"] is True
    for _ in range(2):
        assert policy_service.run_policy(db_session, s.stmt.id).applied == []
        rec.reconcile(db_session, s.stmt.id)
        db_session.commit()
    (case,) = _cases(db_session, s.stmt.id, "open")
    assert "create_system_entry" in registry.allowed(case)
    engine.apply_action(db_session, case_id=case.id, action="create_system_entry", params={"kind": "fee"}, actor="owner", label="t")
    db_session.commit()
    assert len(_booked(db_session)) == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_stand_ins.py -k "retire or attribution or edit_release" tests/integration/test_reconciliation_policy.py -k owner_deleted`
Expected: FAIL (action stays `applied`; policy re-books).

- [ ] **Step 3: Implement**

`coverage_service.py` — in the entry `delete` branch, call retirement whether or not the entry is covered:

```python
            if ev.kind == "entry":
                if ev.op == "delete":
                    _retire_creations(db, statement, ev, run_id)
                if ev.row_id in lines_by_entry:
                    ...  # Task 2's branches unchanged
```

```python
RETIRABLE_ACTIONS = ("backfill_entry", "create_entry", "create_system_entry", "create_refund")


def _retire_creations(db: Session, statement: AccountStatement, ev, run_id) -> None:
    """R6b.0: the owner deleted a created entry in the ledger → its applied creation is retired (status reverted,
    inverse.deleted_by_event) so the family check and the policy see the event as owner-deleted. Scoped to this
    statement's cases (the statement lock serialises it with reverts and replacements); idempotent; never
    overwrites an existing reverted_by_action_id."""
    rows = db.execute(
        select(ReconciliationAction)
        .join(ReconciliationCase, ReconciliationCase.id == ReconciliationAction.case_id)
        .where(ReconciliationCase.statement_id == statement.id, ReconciliationAction.status == "applied",
               ReconciliationAction.action.in_(RETIRABLE_ACTIONS),
               ReconciliationAction.created_entry_ids.contains([ev.row_id]))
        .with_for_update()).scalars().all()
    for row in rows:
        if db.get(LedgerEntry, ev.row_id) is not None:  # re-created id reuse is impossible; a live row means a stale event
            continue
        row.status = "reverted"
        row.inverse = {**(row.inverse or {}), "deleted_by_event": ev.id}
        audit.record(db, event="stand_in_retired", actor="sweep", statement_id=statement.id, case_id=row.case_id,
                     action_id=row.id, run_id=run_id, request_id=None,
                     detail={"entry_id": ev.row_id, "dirty_event_id": ev.id, "action": row.action})
```

(`audit` = `from app.services.reconciliation_actions import audit`; check `audit.record`'s exact keyword names in `audit.py` and match them.)

`policy_service._system_entry_candidate`: first line becomes `if case.kind != "line_unmatched" or context.get("hint") != "bank_only" or context.get("owner_deleted"): return None`.

- [ ] **Step 4: Run the files and the sweep/engine suites**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_stand_ins.py tests/integration/test_reconciliation_policy.py tests/integration/test_coverage_sweep.py tests/integration/test_reconciliation_actions_engine.py tests/integration/test_reconciliation_actions_ledger.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/coverage_service.py app/services/policy_service.py tests/integration/test_stand_ins.py tests/integration/test_reconciliation_policy.py
git commit -m "feat(reconciliation): R6b.0 sweep retires creations whose entry the owner deleted; policy skips owner-deleted events

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: `anchor_opening` action and policy builder

**Files:**
- Modify: `app/services/reconciliation_actions/structural_actions.py` (`AnchorOpeningAction`), `app/services/reconciliation_actions/__init__.py` (`KIND_ACTIONS["balance_gap"]`, `CONDITIONS`), `app/services/policy_service.py` (`_anchor_candidate`, `collect_candidates` kinds, `_amount_now`, `_unchanged`)
- Test: `tests/integration/test_reconciliation_actions_structural.py`, `tests/integration/test_reconciliation_policy.py`

**Interfaces:**
- Produces: `structural_actions.anchor_target(db, account_id) -> AccountStatement | None` (earliest live statement of the account with `opening_balance` not null); `structural_actions.anchor_applied(db, account_id) -> bool` (an applied `anchor_opening` action exists on any statement of the account); `structural_actions.live_bank_statement_ids(db, account_id) -> tuple[int, ...]`; action `anchor_opening` (case kind `balance_gap`, slot `"anchor"`, actors OWNER+POLICY, `lock_targets = LockSet(account_lock=True, barrier=True, statement_ids=live ids)`, `revert_lock_targets` the same from `inverse["statement_ids"]` ∪ current live ids); refusals `dirty_disabled`, `zero_delta`, `closing_adjustments_present`, `already_anchored`, `targets_changed`; audit `anchor_applied`; policy candidate amount `|delta|`.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_reconciliation_actions_structural.py` (fixture `gap` builds a live bank statement; `_adjustments`, `_income`, `_apply`, `_revert`, `_refuse`, `_code` exist; `_submit(db, run, bank, lines, kind="bank", period=..., opening=...)`):

```python
@pytest.fixture
def anchored_bank(seed, db_session, run, dirty_on):
    bank = seed.account("銀行", opening="1000", statement_live_from=date(2026, 8, 1))
    db_session.commit()
    aug, _, _ = _submit(db_session, run, bank, [_line(1, date(2026, 8, 5), "存入", 100, "deposit")], kind="bank",
                        period=(date(2026, 8, 1), date(2026, 8, 31)), opening=Decimal("1500"))
    sep, _, _ = _submit(db_session, run, bank, [_line(1, DAY, "存入", 100, "deposit")], kind="bank", opening=Decimal("1600"))
    cases = {c.statement_id: c for c in db_session.execute(select(ReconciliationCase).where(
        ReconciliationCase.kind == "balance_gap", ReconciliationCase.status == "open")).scalars()}
    return SimpleNamespace(bank=bank, aug=aug, sep=sep, cases=cases)


def test_anchor_books_the_opening_delta_before_the_first_live_period_and_flags_every_statement(anchored_bank, db_session):
    a = anchored_bank
    row = _apply(db_session, a.cases[a.sep.id], "anchor_opening", {})  # any balance_gap case of the account
    (adj,) = _adjustments(db_session)
    assert adj.posted_date == date(2026, 7, 31) and adj.entry_date == date(2026, 7, 31)
    assert adj.amount == Decimal("500") and adj.description == "期初對齊 2026-07-31" and adj.needs_review is True
    assert ledger_service.account_balance(db_session, a.bank.id, as_of=date(2026, 7, 31)) == Decimal("1500")
    db_session.expire_all()
    assert all(db_session.get(AccountStatement, sid).needs_recheck for sid in (a.aug.id, a.sep.id))
    assert sorted(row.inverse["statement_ids"]) == sorted([a.aug.id, a.sep.id])
    assert _code(_refuse(db_session, lambda: _apply(db_session, a.cases[a.aug.id], "anchor_opening", {}))) == "already_anchored"


def test_anchor_refuses_zero_delta_dirty_off_and_closing_adjustments(seed, db_session, run, dirty_on, anchored_bank):
    a = anchored_bank
    db_session.add(_income(a.bank, "500", day=date(2026, 7, 1)))
    db_session.commit()
    assert _code(_refuse(db_session, lambda: _apply(db_session, a.cases[a.sep.id], "anchor_opening", {}))) == "zero_delta"
    db_session.add(LedgerEntry(account_id=a.bank.id, kind="balance_adjustment", amount=Decimal("1"), currency="TWD",
                               entry_date=date(2026, 8, 31), posted_date=date(2026, 8, 31), source="statement",
                               category_id=ews.system_category_id(db_session, "balance_adjustment")))
    db_session.commit()
    assert _code(_refuse(db_session, lambda: _apply(db_session, a.cases[a.sep.id], "anchor_opening", {}))) == "closing_adjustments_present"
    set_dirty(db_session, False)
    db_session.commit()
    assert _code(_refuse(db_session, lambda: _apply(db_session, a.cases[a.sep.id], "anchor_opening", {}))) == "dirty_disabled"


def test_anchor_revert_relocks_every_statement_and_flags_them_again(anchored_bank, db_session, run):
    a = anchored_bank
    row = _apply(db_session, a.cases[a.sep.id], "anchor_opening", {})
    for sid in (a.aug.id, a.sep.id):
        rec.reconcile(db_session, sid)
    db_session.commit()
    _revert(db_session, row.id)
    assert _adjustments(db_session) == []
    db_session.expire_all()
    assert all(db_session.get(AccountStatement, sid).needs_recheck for sid in (a.aug.id, a.sep.id))
    _apply(db_session, [c for c in _cases(db_session, a.sep.id, "open") if c.kind == "balance_gap"][0], "anchor_opening", {})


def test_anchor_refuses_when_the_statement_set_changed_between_discovery_and_lock(anchored_bank, db_session, run, monkeypatch):
    a = anchored_bank
    from app.services.reconciliation_actions import structural_actions as sa
    real = sa.live_bank_statement_ids
    calls = []

    def grown(db, account_id):
        calls.append(1)
        ids = real(db, account_id)
        return ids if len(calls) > 1 else ids[:-1]  # discovery saw one statement fewer than the locked read
    monkeypatch.setattr(sa, "live_bank_statement_ids", grown)
    assert _code(_refuse(db_session, lambda: _apply(db_session, a.cases[a.sep.id], "anchor_opening", {}))) == "targets_changed"


def test_later_closing_adjustment_books_only_the_residual_after_the_anchor(anchored_bank, db_session, run):
    a = anchored_bank
    _apply(db_session, a.cases[a.sep.id], "anchor_opening", {})
    rec.reconcile(db_session, a.sep.id)
    db_session.commit()
    gaps = [c for c in _cases(db_session, a.sep.id, "open") if c.kind == "balance_gap"]
    if gaps:
        _apply(db_session, gaps[0], "balance_adjustment_asof", {})
        assert sum(x.amount for x in _adjustments(db_session)) == Decimal("500") + Decimal(gaps[0].context["gap"])
```

(`set_dirty` from `tests.helpers`; `ews`, `ledger_service`, `rec`, `LedgerEntry`, `AccountStatement` imports as the file already has or add them. If `_submit` cannot take `period=` for an August bank statement, use the `period` kwarg shape from `statement_fixtures._submit` (`period=(start, end)`).)

`tests/integration/test_reconciliation_policy.py`:

```python
ANCHOR_ON = {"enabled": True, "actions": {"anchor_opening": {"enabled": True, "max_amount": "200000"}}}


def test_policy_anchors_once_per_account_and_proposes_over_the_cap(seed, db_session, run, dirty_on):
    bank = seed.account("銀行", opening="1000", statement_live_from=date(2026, 9, 1))
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, bank, [_line(1, DAY, "存入", 100, "deposit")], kind="bank", opening=Decimal("1600"))
    _set_policy(db_session, ANCHOR_ON)
    result = policy_service.run_policy(db_session, stmt.id)
    (row,) = _actions(db_session)
    assert result.applied == [row.id] and row.action == "anchor_opening" and row.after["policy_budget"]["amount"] == "600.0000"
    rec.reconcile(db_session, stmt.id)
    db_session.commit()
    assert policy_service.run_policy(db_session, stmt.id).applied == []  # already anchored → not a candidate
    bank2 = seed.account("銀行2", opening="0", statement_live_from=date(2026, 9, 1))
    db_session.commit()
    stmt2, _, _ = _submit(db_session, run, bank2, [_line(1, DAY, "存入", 100, "deposit")], kind="bank", opening=Decimal("300000"))
    result = policy_service.run_policy(db_session, stmt2.id)
    (p,) = _proposals(db_session, status="pending")
    assert result.proposed == [p.id] and p.action == "anchor_opening"


def test_policy_skips_statements_flagged_by_the_anchor_until_reconciled(seed, db_session, run, dirty_on):
    bank = seed.account("銀行", opening="1000", statement_live_from=date(2026, 9, 1))
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, bank, [_line(1, DAY, "年費", 50, "fee")], kind="bank", opening=Decimal("1600"))
    _set_policy(db_session, {**ANCHOR_ON, "actions": {**ANCHOR_ON["actions"], **SYSTEM_ON["actions"]}})
    first = policy_service.run_policy(db_session, stmt.id)
    assert len(first.applied) == 1  # the anchor; the fee waits: the statement is flagged needs_recheck now
    assert policy_service.run_policy(db_session, stmt.id).skipped.get("statement_excluded", 0) >= 1
    rec.reconcile(db_session, stmt.id)
    db_session.commit()
    assert len(policy_service.run_policy(db_session, stmt.id).applied) == 1  # the fee
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_reconciliation_actions_structural.py -k anchor tests/integration/test_reconciliation_policy.py -k anchor`
Expected: FAIL (`anchor_opening` unknown).

- [ ] **Step 3: Implement**

`structural_actions.py`:

```python
ANCHOR = "anchor"


def live_bank_statement_ids(db: Session, account_id: int) -> tuple[int, ...]:
    return tuple(db.execute(select(AccountStatement.id).where(
        AccountStatement.account_id == account_id, AccountStatement.kind == "bank", AccountStatement.mode == "live")
        .order_by(AccountStatement.id)).scalars())


def anchor_target(db: Session, account_id: int) -> AccountStatement | None:
    return db.execute(select(AccountStatement).where(
        AccountStatement.account_id == account_id, AccountStatement.kind == "bank", AccountStatement.mode == "live",
        AccountStatement.opening_balance.is_not(None)).order_by(AccountStatement.period_start, AccountStatement.id)).scalars().first()


def anchor_applied(db: Session, account_id: int) -> bool:
    return db.scalar(select(exists().where(
        ReconciliationAction.action == "anchor_opening", ReconciliationAction.status == "applied",
        ReconciliationAction.case_id == ReconciliationCase.id, ReconciliationCase.statement_id == AccountStatement.id,
        AccountStatement.account_id == account_id)))


class AnchorOpeningParams(ActionParams):
    pass


class AnchorOpeningAction(ActionBase):
    """R6b.2: books `target.opening_balance − balance(period_start − 1)` as a balance_adjustment dated the day before
    the account's earliest live statement, once per account, and flags every live bank statement of the account
    for recheck. Same barrier discipline as balance_adjustment_asof."""
    name = "anchor_opening"
    Params = AnchorOpeningParams
    case_kinds = frozenset({"balance_gap"})
    actors = frozenset({OWNER, POLICY})
    slot = ANCHOR

    def lock_targets(self, ctx: Context, params: AnchorOpeningParams) -> LockSet:
        return LockSet(account_lock=True, barrier=True, statement_ids=live_bank_statement_ids(ctx.db, ctx.statement.account_id))

    def check_before_locks(self, ctx: Context, params: AnchorOpeningParams) -> None:
        if not settings_service.read_reconciliation_settings(ctx.db)["dirty_enabled"]:
            raise _refuse("action", "dirty_disabled", "the writer barrier needs the dirty triggers")

    def _verify_targets(self, ctx: Context, locked: tuple[int, ...]) -> None:
        now = live_bank_statement_ids(ctx.db, ctx.statement.account_id)
        if set(now) != set(locked):
            raise _refuse("action", "targets_changed", "the account's live statements changed; retry")

    def apply(self, ctx: Context, params: AnchorOpeningParams, prepared: Prepared, effect: Effect) -> None:
        self.check_before_locks(ctx, params)
        db, account_id = ctx.db, ctx.statement.account_id
        locked = tuple(i for i in ctx.lock_set.statement_ids) if ctx.lock_set else ()
        self._verify_targets(ctx, locked)
        if anchor_applied(db, account_id):
            raise _refuse("action", "already_anchored", f"account {account_id} is anchored")
        target = anchor_target(db, account_id)
        if target is None:
            raise _refuse("action", "no_opening_balance", "no live statement carries an opening balance")
        as_of = target.period_start - timedelta(days=1)
        delta = (Decimal(target.opening_balance) - ledger_service.account_balance(db, account_id, as_of=as_of)).quantize(Decimal("0.0001"))
        if delta == 0:
            raise _refuse("action", "zero_delta", f"account {account_id} balances at {as_of.isoformat()}")
        if db.scalar(select(exists().where(LedgerEntry.account_id == account_id, LedgerEntry.kind == "balance_adjustment",
                                            LedgerEntry.posted_date >= as_of))):
            raise _refuse("action", "closing_adjustments_present", "revert or delete the account's closing adjustments first")
        entry = LedgerEntry(account_id=account_id, currency=ctx.statement.currency, kind="balance_adjustment", amount=delta,
                            entry_date=as_of, posted_date=as_of, category_id=ews.system_category_id(db, "balance_adjustment"),
                            description=f"期初對齊 {as_of.isoformat()}", source=STATEMENT_SOURCE, needs_review=True)
        db.add(entry)
        db.flush()
        for sid in locked:
            db.get(AccountStatement, sid).needs_recheck = True
        effect.created_entry_ids = [entry.id]
        effect.inverse = {"created_entry_id": entry.id, "statement_ids": list(locked), "as_of": as_of.isoformat()}
        audit.record(db, event="anchor_applied", actor=ctx.actor, statement_id=ctx.statement.id, case_id=ctx.case.id,
                     action_id=None, run_id=ctx.run_id, request_id=ctx.request_id,
                     detail={"account_id": account_id, "as_of": as_of.isoformat(), "statements": list(locked)})

    def revert_lock_targets(self, ctx: Context, action: ReconciliationAction) -> LockSet:
        recorded = tuple(action.inverse.get("statement_ids") or ())
        return LockSet(account_lock=True, barrier=True,
                       statement_ids=tuple(sorted(set(recorded) | set(live_bank_statement_ids(ctx.db, ctx.statement.account_id)))))

    def invert(self, ctx: Context, action: ReconciliationAction) -> Effect:
        self.check_before_locks(ctx, None)
        locked = tuple(ctx.lock_set.statement_ids) if ctx.lock_set else ()
        self._verify_targets(ctx, locked)
        entry_id = action.inverse["created_entry_id"]
        if ctx.db.get(LedgerEntry, entry_id) is None:
            raise NotRevertable(f"entry {entry_id} is gone")
        ews.delete_entries_cascade(ctx.db, [entry_id])
        for sid in locked:
            ctx.db.get(AccountStatement, sid).needs_recheck = True
        return Effect(deleted_entry_ids=[entry_id])


register(AnchorOpeningAction())
```

Check the engine: `ctx.lock_set` must be set on the revert context too (`engine.py` ~`:427` sets `ctx.lock_set = targets` — verify; if not, set it). `KIND_ACTIONS["balance_gap"] = ("balance_adjustment_asof", "anchor_opening", "dismiss")`; `CONDITIONS[("balance_gap", "anchor_opening")] = lambda case: _bank_statement(case) and not _anchored(case)` with two small helpers in `__init__.py` that look up the statement via `object_session(case)` and call `structural_actions.anchor_applied` lazily (local import to avoid the import cycle).

`policy_service.py`:

```python
def _anchor_candidate(db: Session, case: ReconciliationCase, statement: AccountStatement) -> Candidate | None:
    """R6b.2: any open balance_gap of an un-anchored bank account; amount = |opening delta| at the target."""
    from app.services.reconciliation_actions import structural_actions as sa
    if case.kind != "balance_gap" or statement.kind != "bank" or sa.anchor_applied(db, statement.account_id):
        return None
    target = sa.anchor_target(db, statement.account_id)
    if target is None:
        return None
    as_of = target.period_start - timedelta(days=1)
    delta = Decimal(target.opening_balance) - ledger_service.account_balance(db, statement.account_id, as_of=as_of)
    if delta == 0:
        return None
    return Candidate(case.id, case.kind, case.status, "anchor_opening", {}, _money(delta))
```

`POLICY_ACTIONS["anchor_opening"] = _anchor_candidate`; `collect_candidates` kinds become `("line_unmatched", "amount_delta", "balance_gap", "entry_unmatched")`; `_amount_now`: `if candidate.action == "anchor_opening": return candidate.amount` is NOT acceptable (must be recomputed under the lock) — recompute with the same arithmetic as `_anchor_candidate` (factor a helper `_anchor_delta(db, account_id) -> Decimal | None` used by both); `_unchanged` for `anchor_opening`: `not sa.anchor_applied(...)`. `run_policy` must apply at most one anchor per account per run: keep a `set()` of anchored account ids in the loop and skip further anchor candidates for the same account with reason `anchored_this_run`.

- [ ] **Step 4: Run both files, the engine suite and the policy suite**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_reconciliation_actions_structural.py tests/integration/test_reconciliation_policy.py tests/integration/test_reconciliation_actions_engine.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/reconciliation_actions/structural_actions.py app/services/reconciliation_actions/__init__.py app/services/policy_service.py tests/integration/test_reconciliation_actions_structural.py tests/integration/test_reconciliation_policy.py
git commit -m "feat(reconciliation): R6b.2 anchor_opening action and policy builder

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: `replace_stand_in` — discovery, action, policy builder

**Files:**
- Modify: `app/services/reconciliation_service.py` (`_desired_cases`: shadow/orphan discovery), `app/services/stand_ins.py` (`shadow_candidates`, `orphans`), `app/services/reconciliation_actions/ledger_actions.py` (`ReplaceStandInAction`), `app/services/reconciliation_actions/__init__.py` (`KIND_ACTIONS["entry_unmatched"]`, `CONDITIONS`), `app/services/policy_service.py` (`_replace_candidate`, `_unchanged`)
- Test: `tests/integration/test_stand_ins.py`, `tests/integration/test_reconciliation_policy.py`

**Interfaces:**
- Consumes: `stand_ins.stand_in_entry_ids`, `creation_for_entry` (Task 4); `matching.eligible_kinds`, `_entry_class`, `_same` (R6a); `coverage_service.active_rows`, `release_rows`, `write_claim`; `ews.delete_entries_cascade`; `Rules.drift_window_days`.
- Produces: `stand_ins.shadows(db, statement, accounts, unmatched_owner_entries: list[matching.Entry], rules) -> list[dict]` — for each unmatched non-stand-in entry, the unique line (event) on this statement whose active coverage is held by a stand-in and which the entry conserves within `drift_window_days`, as `{"entry_id", "event_id", "line_id", "stand_in_entry_id", "backfill_action_id"}`; `stand_ins.orphans(db, statement, unmatched_stand_in_entries) -> list[dict]` `{"entry_id", "event_id", "line_id", "backfill_action_id", "claimant_entry_id"}` where the stand-in's line event now has active non-stand-in coverage; `_desired_cases` emits `entry_unmatched` cases with those hints, `event_id` and `line_id` set; action `replace_stand_in` (no params, case kind `entry_unmatched`, slot `"replace"`, actors OWNER+POLICY, not revertable); refusals `no_claimant`, `stand_in_changed`, `stand_in_coverage_gone`, `owner_entry_taken`, `claimant_changed`; audit `stand_in_replaced`; policy candidate amount 0.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_stand_ins.py`:

```python
def _backfilled(seed, db, run, merchant="全聯", day=DAY, printed=580):
    card = _card(seed, db)
    stmt, (line,), _ = _submit(db, run, card, [_line(1, day, merchant, printed)])
    (case,) = _cases(db, stmt.id, "open")
    row, _ = engine.apply_action(db, case_id=case.id, action="backfill_entry", params={"kind": "expense"}, actor="owner", label="t")
    db.commit()
    return SimpleNamespace(card=card, stmt=stmt, line=line, action=row, stand_in=db.get(LedgerEntry, row.created_entry_ids[0]))


def test_shadow_case_when_the_owner_entry_arrives_while_the_stand_in_holds_the_line(seed, db_session, run, dirty_on):
    s = _backfilled(seed, db_session, run)
    owner = seed.entry(s.card, "-580", day=date(2026, 10, 12), merchant="全聯")  # 12 days outside the period, inside drift 15
    db_session.commit()
    rec.reconcile(db_session, s.stmt.id)
    db_session.commit()
    (case,) = [c for c in _cases(db_session, s.stmt.id, "open") if c.kind == "entry_unmatched"]
    assert case.entry_id == owner.id and case.event_id == s.line.event_id and case.line_id == s.line.id
    assert case.context == {"hint": "shadowed_by_stand_in", "stand_in_entry_id": s.stand_in.id, "backfill_action_id": s.action.id}
    assert "replace_stand_in" in registry.allowed(case)
    s.stand_in.category_id = seed.category("食").id  # cosmetic edit keeps the match and does not block replacement
    db_session.commit()
    row, _ = engine.apply_action(db_session, case_id=case.id, action="replace_stand_in", params={}, actor="owner", label="t")
    db_session.commit()
    assert db_session.get(LedgerEntry, s.stand_in.id) is None
    (cov,) = _active(db_session, s.stmt.id)
    assert cov.entry_id == owner.id and cov.match_rule == "replace_stand_in" and cov.match_kind == "manual"
    old = db_session.get(ReconciliationAction, s.action.id)
    assert old.status == "reverted" and old.reverted_by_action_id == row.id
    assert row.deleted_entry_ids == [s.stand_in.id] and row.before["stand_in_entry_id"] == s.stand_in.id
    with pytest.raises(NotRevertable):
        engine.revert_action(db_session, action_id=row.id, actor="owner", label="t")


def test_shadow_requires_a_unique_line_and_a_conserving_owner_entry(seed, db_session, run, dirty_on):
    s = _backfilled(seed, db_session, run)
    owner = seed.entry(s.card, "-580", day=DAY, merchant="全聯")
    other = seed.entry(s.card, "-580", day=DAY, merchant="全聯")  # Review Focus 5: two owner entries → no hint
    db_session.commit()
    rec.reconcile(db_session, s.stmt.id)
    db_session.commit()
    cases = [c for c in _cases(db_session, s.stmt.id, "open") if c.kind == "entry_unmatched"]
    assert {c.entry_id for c in cases} == {owner.id, other.id} and all("hint" not in (c.context or {}) for c in cases)


def test_orphan_case_after_the_sweep_released_the_stand_in(seed, db_session, run, dirty_on):
    s = _backfilled(seed, db_session, run)
    s.stand_in.posted_date = date(2026, 9, 4)  # structural edit → sweep releases the line
    db_session.commit()
    owner = seed.entry(s.card, "-580", day=DAY, merchant="全聯")
    db_session.commit()
    rec.reconcile(db_session, s.stmt.id)
    db_session.commit()
    (cov,) = _active(db_session, s.stmt.id)
    assert cov.entry_id == owner.id
    (case,) = [c for c in _cases(db_session, s.stmt.id, "open") if c.kind == "entry_unmatched"]
    assert case.entry_id == s.stand_in.id and case.context["hint"] == "orphaned_stand_in" and case.context["claimant_entry_id"] == owner.id
    assert _code(_refuse(db_session, lambda: _apply(db_session, case, "replace_stand_in", {}))) == "stand_in_changed"  # date edited


def test_replace_refuses_when_the_claimant_or_stand_in_moved(seed, db_session, run, dirty_on):
    s = _backfilled(seed, db_session, run)
    owner = seed.entry(s.card, "-580", day=DAY, merchant="全聯")
    db_session.commit()
    rec.reconcile(db_session, s.stmt.id)
    db_session.commit()
    (case,) = [c for c in _cases(db_session, s.stmt.id, "open") if c.kind == "entry_unmatched"]
    owner.amount = Decimal("-581")
    db_session.commit()
    assert _code(_refuse(db_session, lambda: _apply(db_session, case, "replace_stand_in", {}))) == "owner_entry_taken"
    owner.amount = Decimal("-580")
    s.stand_in.amount = Decimal("-1")
    db_session.commit()
    assert _code(_refuse(db_session, lambda: _apply(db_session, case, "replace_stand_in", {}))) == "stand_in_changed"
    assert db_session.get(LedgerEntry, s.stand_in.id) is not None


def test_system_entries_never_shadow(seed, db_session, run, dirty_on):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "年費", 1200, "fee")])
    (case,) = _cases(db_session, stmt.id, "open")
    engine.apply_action(db_session, case_id=case.id, action="create_system_entry", params={"kind": "fee"}, actor="owner", label="t")
    db_session.commit()
    seed.entry(card, "-1200", day=DAY, kind="fee", merchant="年費")
    db_session.commit()
    rec.reconcile(db_session, stmt.id)
    db_session.commit()
    cases = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "entry_unmatched"]
    assert cases and all("hint" not in (c.context or {}) for c in cases)
```

(`_apply`, `_refuse`, `_code`, `SimpleNamespace`, `NotRevertable` imports as in the ledger-actions tests.)

`tests/integration/test_reconciliation_policy.py`:

```python
def test_policy_replaces_a_stand_in_with_zero_budget_and_counts_toward_the_statement_cap(seed, db_session, run, dirty_on):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    (case,) = _cases(db_session, stmt.id, "open")
    engine.apply_action(db_session, case_id=case.id, action="backfill_entry", params={"kind": "expense"}, actor="owner", label="t")
    db_session.commit()
    owner = seed.entry(card, "-580", day=DAY, merchant="全聯")
    db_session.commit()
    rec.reconcile(db_session, stmt.id)
    db_session.commit()
    _set_policy(db_session, {"enabled": True})  # replace_stand_in is enabled by default
    result = policy_service.run_policy(db_session, stmt.id)
    (row,) = [a for a in _actions(db_session) if a.action == "replace_stand_in"]
    assert result.applied == [row.id] and row.after["policy_budget"]["amount"] == "0.0000"
    assert _budget(db_session).used_amount == Decimal("0") and _budget(db_session).used_count == 1
    _set_policy(db_session, {"enabled": True, "per_statement_count": 1})
    # a second replacement on the same statement would exceed the count cap → proposal (set up a second stand-in first)
```

(finish the cap half of that test by booking a second line/stand-in/owner pair on the same statement and asserting `result.proposed` has one `replace_stand_in` proposal.)

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_stand_ins.py -k "shadow or orphan or replace or never_shadow" tests/integration/test_reconciliation_policy.py -k replace`
Expected: FAIL (no hints; `replace_stand_in` unknown).

- [ ] **Step 3: Implement**

`stand_ins.py` additions:

```python
def _stand_in_coverage(db: Session, statement: AccountStatement) -> dict[int, tuple[int, int, int]]:
    """event_id → (line_id, stand_in_entry_id, backfill_action_id) for active coverage held by a stand-in."""
    ids = stand_in_entry_ids(db, [statement.account_id])
    out = {}
    for line_id, rows in coverage_service.active_rows(db, statement.id).items():
        for r in rows:
            if r.entry_id in ids:
                action = creation_for_entry(db, r.entry_id)
                if action is not None:
                    out[r.event_id] = (line_id, r.entry_id, action.id)
    return out


def shadows(db: Session, statement: AccountStatement, lines: dict[int, StatementLine], owners: list, rules) -> list[dict]:
    """R6b.5 shadowed_by_stand_in: owner entry ↔ the unique stand-in-held line it conserves within drift_window_days."""
    held = _stand_in_coverage(db, statement)
    if not held:
        return []
    by_event = {lines[lid].event_id: lines[lid] for lid in lines if lines[lid].event_id in held}
    out = []
    for e in owners:
        hits = []
        for event_id, (line_id, stand_in_id, action_id) in held.items():
            line = by_event.get(event_id)
            if line is None:
                continue
            ml = reconciliation_service._as_line(line)
            if matching._entry_class(e) not in matching.eligible_kinds(statement.kind, line.line_kind):
                continue
            if not matching._same(e.flow, ml.flow) or matching._date_distance(ml, e) > rules.drift_window_days:
                continue
            hits.append({"entry_id": e.id, "event_id": event_id, "line_id": line_id, "stand_in_entry_id": stand_in_id,
                         "backfill_action_id": action_id})
        if len(hits) == 1:
            out.append(hits[0])
    return out


def orphans(db: Session, statement: AccountStatement, stand_ins_unmatched: list) -> list[dict]:
    """R6b.5 orphaned_stand_in: an unmatched stand-in whose creating action's event is now held by a non-stand-in."""
    ids = stand_in_entry_ids(db, [statement.account_id])
    active = coverage_service.active_rows(db, statement.id)
    by_event = {r.event_id: (line_id, r.entry_id) for line_id, rows in active.items() for r in rows}
    out = []
    for e in stand_ins_unmatched:
        action = creation_for_entry(db, e.id)
        if action is None or action.event_id not in by_event:
            continue
        line_id, claimant = by_event[action.event_id]
        if claimant is None or claimant in ids:
            continue
        out.append({"entry_id": e.id, "event_id": action.event_id, "line_id": line_id, "backfill_action_id": action.id,
                    "claimant_entry_id": claimant})
    return out
```

(To avoid a circular import, `stand_ins` imports `coverage_service`, `matching` and uses `reconciliation_service._as_line` through a local import inside `shadows`; or move `_as_line` into `matching` as `line_from_row` — the implementer picks one and keeps both modules importable.)

`_desired_cases`: after the `for entry_id in result.unmatched_entry_ids:` loop, the owner-entry loop must not emit a plain `entry_unmatched` for an entry that gets a shadow hint, and the stand-in loop must emit orphans:

```python
    owners = [entries[i] for i in result.unmatched_entry_ids if i in entries and not entries[i].stand_in]
    shadow_by_entry = {s["entry_id"]: s for s in stand_ins.shadows(db, statement, lines, owners, rules)}
    orphan_by_entry = {o["entry_id"]: o for o in stand_ins.orphans(db, statement, [entries[i] for i in result.unmatched_entry_ids if i in entries and entries[i].stand_in])}
```

and in the loop, before the `move_account` lookup:

```python
        if entry_id in shadow_by_entry:
            s = shadow_by_entry[entry_id]
            out.append(_case("entry_unmatched", event_id=s["event_id"], line_id=s["line_id"], entry_id=entry_id,
                             context={"hint": "shadowed_by_stand_in", "stand_in_entry_id": s["stand_in_entry_id"],
                                      "backfill_action_id": s["backfill_action_id"]}))
            continue
        if entry_id in orphan_by_entry:
            o = orphan_by_entry[entry_id]
            out.append(_case("entry_unmatched", event_id=o["event_id"], line_id=o["line_id"], entry_id=entry_id,
                             context={"hint": "orphaned_stand_in", "backfill_action_id": o["backfill_action_id"],
                                      "claimant_entry_id": o["claimant_entry_id"]}))
            continue
```

Note (F14 of the survey): a stand-in's own coverage is `manual`/`agent`, so `_manual_claims` keeps its line out of matching and `exclude=owned` keeps the stand-in out of the population — the owner entry is therefore unmatched and reaches `shadows()`; nothing else changes.

`ledger_actions.py`:

```python
REPLACE = "replace"
STAND_IN_FIELDS = ("account_id", "currency", "kind", "amount", "entry_date", "posted_date")


class ReplaceStandInParams(ActionParams):
    pass


class ReplaceStandInAction(ActionBase):
    """R6b.5: the owner's entry takes the line its stand-in held (shadow) or has already taken it (orphan); the
    stand-in is deleted and its backfill action reverted. Everything is derived from case.event_id/case.entry_id
    and re-proven under the locks. Not revertable."""
    name = "replace_stand_in"
    Params = ReplaceStandInParams
    case_kinds = frozenset({"entry_unmatched"})
    actors = frozenset({OWNER, POLICY})
    slot = REPLACE

    def _shape(self, ctx: Context) -> tuple[str, int, int]:
        """('shadow', stand_in_id, owner_id) or ('orphan', stand_in_id, claimant_id), from the case alone."""
        hint = (ctx.case.context or {}).get("hint")
        if hint == "shadowed_by_stand_in":
            stand_in = (ctx.case.context or {}).get("stand_in_entry_id")
            return "shadow", stand_in, ctx.case.entry_id
        if hint == "orphaned_stand_in":
            claimant = self._claimant(ctx)
            if claimant is None:
                raise _refuse("action", "no_claimant", "the orphan's line has no owner claim any more")
            return "orphan", ctx.case.entry_id, claimant
        raise _refuse("action", "not_a_stand_in_case")

    def _claimant(self, ctx: Context) -> int | None:
        for rows in coverage_service.active_rows(ctx.db, ctx.statement.id).values():
            for r in rows:
                if r.event_id == ctx.case.event_id and r.entry_id is not None:
                    return r.entry_id
        return None

    def lock_targets(self, ctx: Context, params: ReplaceStandInParams) -> LockSet:
        _, stand_in, other = self._shape(ctx)
        return LockSet(entry_ids=tuple(sorted({stand_in, other})))

    def validate_before(self, ctx: Context, params: ReplaceStandInParams, prepared: Prepared, before: dict) -> None:
        self._prove(ctx)

    def _prove(self, ctx: Context):
        db = ctx.db
        shape, stand_in_id, other_id = self._shape(ctx)
        stand_in = db.get(LedgerEntry, stand_in_id)
        action = None if stand_in is None else stand_ins.creation_for_entry(db, stand_in_id)
        if stand_in is None or stand_in.source != STATEMENT_SOURCE or action is None or action.action not in stand_ins.BACKFILL_ACTIONS:
            raise _refuse("action", "stand_in_changed", "the stand-in is gone or is not a backfilled entry")
        after = ((action.after or {}).get("entries") or {}).get(str(stand_in_id)) or {}
        now = snapshots.snapshot_set(db, [stand_in_id], [])["entries"][str(stand_in_id)]
        if any(after.get(k) != now.get(k) for k in STAND_IN_FIELDS) or now.get("child_ids") or now.get("refund_ids") \
                or now.get("settlement_ids") or now.get("reward_ids") or stand_in.transfer_group_id is not None:
            raise _refuse("action", "stand_in_changed", "the stand-in was edited structurally")
        line = _line_for_event(ctx)  # the event's current line on the statement's current revision
        held = [r for r in coverage_service.active_rows(db, ctx.statement.id).get(line.id, ()) ]
        other = db.get(LedgerEntry, other_id)
        if other is None or other.source == STATEMENT_SOURCE and other.id in stand_ins.stand_in_entry_ids(db, [ctx.statement.account_id]):
            raise _refuse("action", "claimant_changed" if shape == "orphan" else "owner_entry_taken")
        ml = rec._as_line(line)
        me = rec.entry_view(db, other)  # a matching.Entry for one row (helper added in reconciliation_service, same construction as load_candidates)
        if matching._entry_class(me) not in matching.eligible_kinds(ctx.statement.kind, line.line_kind) or not matching._same(me.flow, ml.flow) \
                or matching._date_distance(ml, me) > ctx.rules.drift_window_days:
            raise _refuse("action", "claimant_changed" if shape == "orphan" else "owner_entry_taken")
        if shape == "shadow":
            if not held or any(r.entry_id != stand_in_id for r in held) or other_id in coverage_service.claimed_entry_ids(db):
                raise _refuse("action", "stand_in_coverage_gone" if not held else "owner_entry_taken")
        else:
            if not held or any(r.entry_id != other_id for r in held) or any(r.entry_id == stand_in_id for r in held):
                raise _refuse("action", "claimant_changed")
        return shape, stand_in, other, line, held, action

    def apply(self, ctx: Context, params: ReplaceStandInParams, prepared: Prepared, effect: Effect) -> None:
        shape, stand_in, other, line, held, action = self._prove(ctx)
        db = ctx.db
        effect.before = {"stand_in_entry_id": stand_in.id, "backfill_action_id": action.id, "claimant_entry_id": other.id, "shape": shape}
        if shape == "shadow":
            coverage_service.release_rows(db, [r.id for r in held], reason="replaced")
            effect.coverage_ids = _claim(ctx, line, [(other, "principal")], self.name)
        effect.touched_entry_ids = [other.id]
        ews.delete_entries_cascade(db, [stand_in.id])
        action.status = "reverted"
        action.reverted_by_action_id = None  # set by the engine after the row exists: see _record_effect hook below
        effect.deleted_entry_ids = [stand_in.id]
        effect.inverse = {"stand_in": prepared.expected["entries"].get(str(stand_in.id)), "backfill_action_id": action.id}
        effect.case_context = {"replaced_stand_in": stand_in.id}
        audit.record(db, event="stand_in_replaced", actor=ctx.actor, statement_id=ctx.statement.id, case_id=ctx.case.id,
                     action_id=None, run_id=ctx.run_id, request_id=ctx.request_id,
                     detail={"stand_in_entry_id": stand_in.id, "backfill_action_id": action.id, "claimant_entry_id": other.id, "shape": shape})

    def invert(self, ctx: Context, action: ReconciliationAction) -> Effect:
        raise NotRevertable("stand-in deleted")
```

Engine hook: after `_record_effect` in `apply_action`, if `effect.inverse.get("backfill_action_id")` is set and the action name is `replace_stand_in`, set that action row's `reverted_by_action_id = row.id` (two lines in `engine.py`, guarded by the action name). `_line_for_event(ctx)` = `StatementLine` where `revision_id == statement.current_revision_id and event_id == case.event_id` (add to `base.py` next to `current_line`). `rec.entry_view(db, entry) -> matching.Entry` = the single-row version of the construction in `load_candidates` (factor the field mapping into a helper used by both). `KIND_ACTIONS["entry_unmatched"]` gains `"replace_stand_in"`; `CONDITIONS[("entry_unmatched", "replace_stand_in")] = lambda case: _context(case).get("hint") in ("shadowed_by_stand_in", "orphaned_stand_in")`.

`policy_service.py`:

```python
def _replace_candidate(db: Session, case: ReconciliationCase, statement: AccountStatement) -> Candidate | None:
    context = case.context or {}
    if case.kind != "entry_unmatched" or context.get("hint") not in ("shadowed_by_stand_in", "orphaned_stand_in"):
        return None
    return Candidate(case.id, case.kind, case.status, "replace_stand_in", {}, Decimal("0"), entry_id=None)
```

`POLICY_ACTIONS["replace_stand_in"] = _replace_candidate`; `_amount_now`: `if candidate.action == "replace_stand_in": return Decimal("0")`; `_unchanged`: for `replace_stand_in` compare `context.get("backfill_action_id")` and `context.get("hint")` with the case as collected (store both in `Candidate.params`? no — params stay empty; keep them in a new optional `Candidate.evidence: dict` field defaulting to `{}` and compare that). Note `entry_id=None` in the candidate while `case.entry_id` is set: `_unchanged` compares `context.get("entry_id")` (None for these cases) — consistent.

- [ ] **Step 4: Run the files and the full reconciliation suites**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_stand_ins.py tests/integration/test_reconciliation_policy.py tests/integration/test_reconciliation_actions_ledger.py tests/integration/test_reconciliation_actions_engine.py tests/integration/test_reconciliation_service.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/stand_ins.py app/services/reconciliation_service.py app/services/reconciliation_actions/ledger_actions.py app/services/reconciliation_actions/base.py app/services/reconciliation_actions/engine.py app/services/reconciliation_actions/__init__.py app/services/policy_service.py tests/integration/test_stand_ins.py tests/integration/test_reconciliation_policy.py
git commit -m "feat(reconciliation): R6b.5 replace_stand_in — shadow/orphan discovery, native replacement action, policy builder

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Dry-run `--policy` mode, sweep regression table, retirement counts

**Files:**
- Modify: `scripts/r6_case_dump.py` (`--policy` flag → `policy` section), `scripts/r6_dryrun.py` (`--policy` summary), `scripts/README-r6-dryrun.md`
- Test: `tests/unit/test_r6_dryrun.py`

**Interfaces:**
- Produces: `r6_case_dump.py --policy` adds to the dump `{"policy": {"<statement_id>": [{"action", "kind", "amount_bucket"}], "anchor_blockers": [account_ids with closing adjustments], "needs_peer": {"<account_id>": n}, "owner_deleted": {"<statement_id>": n}, "sweep": {"would_release_old": n, "would_release_new": n, "kept_by_key": {"name": n, "merchant": n, ...}}}}` computed read-only (`collect_candidates` + `_prefilter` under the current settings, in the READ ONLY transaction; `amount_bucket` = one of `"<1000"`, `"<5000"`, `">=5000"` — never the amount); `r6_dryrun.py --policy` prints a per-account table of counts and the sweep table and exits 0 (no gate; evidence only).

- [ ] **Step 1: Write the failing unit test**

```python
def test_policy_summary_counts_by_account_and_action():
    after = _dump({"1": {"claims": [], "cases": []}})
    after["policy"] = {"1": [{"action": "backfill_entry", "kind": "expense", "amount_bucket": "<1000"},
                             {"action": "backfill_entry", "kind": "income", "amount_bucket": "<5000"},
                             {"action": "anchor_opening", "kind": None, "amount_bucket": ">=5000"}],
                       "anchor_blockers": [7], "needs_peer": {"3": 2}, "owner_deleted": {"1": 1},
                       "sweep": {"would_release_old": 5, "would_release_new": 2, "kept_by_key": {"name": 2, "merchant": 1}}}
    text = dr.policy_summary(after, accounts={"1": 3})
    assert "| 3 | backfill_entry | 2 |" in text and "| 3 | anchor_opening | 1 |" in text
    assert "anchor blocked by closing adjustments: 1 account" in text and "needs_peer lines: 2" in text
    assert "sweep: old rule would release 5, new rule 2 (kept: name 2, merchant 1)" in text
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/unit/test_r6_dryrun.py -k policy_summary`
Expected: FAIL (`policy_summary` missing).

- [ ] **Step 3: Implement**

`r6_case_dump.py`: add `--policy`; when set, after the statements loop:

```python
def _policy_section(db, statements) -> dict:
    from app.services import policy_service, settings_service, stand_ins
    from app.services.reconciliation_actions import structural_actions as sa
    policy = settings_service.reconciliation_policy(db)
    out = {"anchor_blockers": [], "needs_peer": {}, "owner_deleted": {}, "sweep": _sweep_table(db, statements)}
    seen_accounts = set()
    for stmt in statements:
        rows = []
        for cand in policy_service.collect_candidates(db, stmt, policy):
            decision = policy_service._prefilter(db, stmt, cand, policy)
            rows.append({"action": cand.action, "kind": cand.params.get("kind"), "amount_bucket": _bucket(cand.amount),
                         "decision": decision[0] if isinstance(decision, tuple) else str(decision)})
        out[str(stmt.id)] = rows
        out["owner_deleted"][str(stmt.id)] = len(stand_ins.owner_deleted_events(db, stmt))
        if stmt.kind == "bank" and stmt.account_id not in seen_accounts:
            seen_accounts.add(stmt.account_id)
            target = sa.anchor_target(db, stmt.account_id)
            if target is not None and db.scalar(select(func.count(LedgerEntry.id)).where(
                    LedgerEntry.account_id == stmt.account_id, LedgerEntry.kind == "balance_adjustment",
                    LedgerEntry.posted_date >= target.period_start)):
                out["anchor_blockers"].append(stmt.account_id)
    out["needs_peer"] = _count_hint(db, statements, "needs_peer")
    return out
```

with `_bucket(amount)` (`"<1000" | "<5000" | ">=5000"`), `_count_hint` (open `line_unmatched` cases with that hint per account), and `_sweep_table(db, statements)`: for every pending `coverage_dirty` update event (`id > swept_through_event_id` per statement, `kind == "entry"`, `op == "update"`) whose row is held by active coverage of that statement, compare `new_row` (the trigger's JSON of the row) against the coverage snapshot with `coverage_service.STRUCTURAL_KEYS` — old rule counts every such event as a release, new rule counts only structural differences; `kept_by_key` counts the differing non-structural keys (`name`, `merchant`, `category_id`, `description`, …) over kept events. Read-only; compare JSON values as strings on both sides (money to 4 dp via `coverage_service._m`).

`r6_dryrun.py`: `policy_summary(after: dict, accounts: dict[str, int]) -> str` (statement id → account id map passed from the dump: add `"accounts": {"<statement_id>": account_id}` to the dump) producing the markdown table `| account | action | lines |`, the anchor-blocker line, the needs_peer total, the owner-deleted total, the sweep line; `main()` with `--policy` prints it and writes `policy-summary.md` (mode 600), exit 0.

README: a "Policy mode" section with the two commands and what each line means; the sweep table is the R6b.0 regression gate evidence (no automatic failure; the release record keeps the table).

- [ ] **Step 4: Run the unit file and a candidate-tree smoke against the test database**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/unit/test_r6_dryrun.py`; then, from this worktree only, `PYTHONPATH=$PWD .venv/bin/python scripts/r6_case_dump.py --policy --out /tmp/claude-1000/-home-opc/e725ec3e-bc74-45aa-a474-cf3d3532997f/scratchpad/r6b-dump.json` once (READ ONLY transaction; report only the "dumped N …" line).
Expected: PASS; the smoke prints the line and `git status` shows no tracked change.

- [ ] **Step 5: Commit**

```bash
git add scripts/r6_case_dump.py scripts/r6_dryrun.py scripts/README-r6-dryrun.md tests/unit/test_r6_dryrun.py
git commit -m "feat(reconciliation): R6b dry-run policy mode, sweep regression table, retirement counts

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Docs and the full-suite gate

**Files:**
- Modify: `docs/superpowers/specs/2026-10-15-reconciliation-r6-design.md` (append `## 6. R6b implementation notes`), `services/accounting-service/README.md` (policy keys and actions), `services/accounting-service/deploy/statements/README.md` (R6b rollout: enable order, dry-run policy mode, caps)

- [ ] **Step 1: Append the implementation notes**

```markdown
## 6. R6b implementation notes (2026-10-16, PR "R6b")

- Stand-in = applied `backfill_entry` creation (`stand_ins.BACKFILL_ACTIONS`); `load_candidates` sets `Entry.stand_in`; `_representations` drops stand-in representations when an owner one exists (`_is_stand_in_rep`).
- Sweep: `delete` events retire creations of the four creation actions (`_retire_creations`, audit `stand_in_retired`, `inverse.deleted_by_event`); `update` events release only on `STRUCTURAL_KEYS` differences (`structurally_changed`).
- Owner-deleted marker: `stand_ins.owner_deleted_events` re-derived in `_desired_cases` → `context.owner_deleted = True` (+ `hint = stand_in_deleted` when the line has no other hint); both automatic builders skip it; owner `backfill_entry`/`create_system_entry` stay available.
- Actions: `backfill_entry` (kind map `BACKFILL_KINDS`, category `對帳待分類`), `anchor_opening` (slot `anchor`, account+barrier+all live bank statements locked, `targets_changed` on apply and revert, `needs_recheck` on each), `replace_stand_in` (slot `replace`, no params, evidence re-proven under the locks, not revertable; the engine sets the retired action's `reverted_by_action_id`).
- Policy: `collect_candidates` kinds `line_unmatched, amount_delta, balance_gap, entry_unmatched`; one anchor per account per run; `replace_stand_in` amount 0 (deliberate budget exemption, counts toward `per_statement_count`); `policy_version` `r6b-<n>`.
- Known: `needs_peer` lines (payment/transfer) stay open until R6c; anchored accounts keep a `balance_gap` equal to their `needs_peer` sum.
```

- [ ] **Step 2: README updates**

Service README: the three policy actions, `backfill_grace_days`, `backfill_accounts`, the `對帳待分類` category, the owner-deleted rule ("delete a backfilled entry in the ledger and the policy will not recreate it; dismiss or re-run the action from the case"). Deploy README: rollout order (deploy with all three actions as defaulted → `--policy` dry-run → enable `backfill_entry` for bank accounts → run → `balance_gap` residual vs `needs_peer` → enable `anchor_opening` → run → cards), and the sweep regression table from the dry-run in the release record.

- [ ] **Step 3: Full suite**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: 0 failed (R6a baseline 2275 + the new tests). Paste the summary line into the commit body.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-10-15-reconciliation-r6-design.md services/accounting-service/README.md services/accounting-service/deploy/statements/README.md
git commit -m "docs: R6b implementation notes, policy keys, rollout order

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review

- **Spec coverage:** R6b.0 stand-in notion → Tasks 3–4; one-stand-in-per-event → Task 5 (family check test) + Task 6 (lifecycle); structural-only sweep → Task 2; derived marker → Tasks 4, 6; R6b.1 → Task 5; R6b.2 → Task 7; R6b.3/4 hint only → Task 3; R6b.5 → Task 8; R6b.6 → Task 1; R6b.7 → Task 9; R6b.8 tests spread across Tasks 2–9 (listed per task); §3 rollout → Task 10.
- **Survey flags folded:** F1/F2 (create_entry condition, needs_peer) → Task 3; F3 (single-valued hint) → `owner_deleted` key + hint only when free, Tasks 4/6; F4 (`current_line` needs `line_id`) → shadow/orphan cases carry `line_id` and the action resolves by event (`_line_for_event`), Task 8; F7 (revert path) → anchor overrides `revert_lock_targets`, re-checks dirty and targets in `invert`, Task 7; F8/F9 (`_unchanged`, `collect_candidates`) → Tasks 5/7/8; F10 (needs_recheck exclusion) → Task 7 test; F11/F12 → Tasks 6/2; F14 (stand-in coverage is agent/manual) → noted in Task 8; F15/F16 → Tasks 1/5/7/8; F19 → Task 1; F23 → Task 7.
- **Placeholders:** none; the `STRUCTURAL_KEYS` literal is spelled out in Global Constraints (the odd expression in Task 2 is explicitly marked "do not copy"); Task 8's `rec.entry_view` and `_line_for_event` are defined in the same task.
- **Type consistency:** `stand_ins.stand_in_entry_ids(db, account_ids)`, `creation_for_entry(db, entry_id)`, `owner_deleted_events(db, statement)`, `shadows(db, statement, lines, owners, rules)`, `orphans(db, statement, entries)` used identically in Tasks 4, 6, 8, 9; `backfill_kind(statement_kind, line_kind)` in Tasks 5 and 9; `live_bank_statement_ids`, `anchor_target`, `anchor_applied` in Tasks 7 and 9; audit event names in Tasks 1, 6, 7, 8.
- **Review Focus:** 1 → Task 6 (`test_policy_skips_an_owner_deleted_fee_line_but_the_owner_may_rebook_it`), 2 → Task 4 (`test_stand_in_alone_self_heals_after_release`) + Task 5 (family-check test), 3 → Task 7 (`test_policy_skips_statements_flagged_by_the_anchor_until_reconciled`), 4 → Task 2 (cosmetic vs structural) + Task 8 (categorised replaced; amount-edited refused), 5 → Task 8 (`test_shadow_requires_a_unique_line_and_a_conserving_owner_entry`).
