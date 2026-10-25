# Reconciliation R6c (transfers) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pair a card-payment / transfer statement line with the owner's transfer leg on the same account even when MOZE recorded a slightly different amount (`transfer_delta`), let the owner rewrite that whole transfer to the statement figures and close both statements' cases in one action (`adjust_transfer`), and book a transfer from two complementary lines that nothing represents (`pair_transfer`, policy-eligible under a cap) — without a suspense account and without owner-wins for paired transfers (owner rulings 2026-10-25).

**Architecture:** One new matcher builder `_transfer_case` in `matching.py` slotted between `_foreign_case` and `_near_case`; two ordinary engine actions in a new module `reconciliation_actions/transfer_actions.py` sharing peer discovery, the both-statement lifecycle (peer statement locked through `LockSet.statement_ids`, peer case resolved by the same action row, explicit claims on both statements, `_reconcile_keeping_case`), and an `invert` that releases coverage and reopens both cases itself; `stand_ins` gains the pair-occupancy guard and the four action-name constants learn `pair_transfer`; the policy gets `_pair_transfer_candidate`; `proposal_service._validate` learns `peer_line_id`; the inbox item gains a `transfer` field the SPA ignores.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2, pytest (unit: pure matcher; integration: throw-away Postgres per session via `tests/conftest.py`), Decimal money.

**Spec:** `docs/superpowers/specs/2026-10-25-reconciliation-r6c-transfers-design.md` rev 5 (Multica AGENT-290 PASS with minors folded); parents `2026-10-15-reconciliation-r6-design.md` (decisions 7/8, R6b.0 stand-ins, R6b.9 inputs), `2026-10-08-statement-reconciliation-design.md` §7 (engine, lock order), `2026-10-21-reconciliation-inbox-design.md` §4. Facts sheet used while planning: file:line citations below were read at `5468b75`.

## Global Constraints

- Branch `feat/reconciliation-r6c` in worktree `/home/opc/workspace/home-hub-r6c` (from `origin/main` 56e79cf); run everything from `/home/opc/workspace/home-hub-r6c/services/accounting-service` with `PYTHONPATH=../shared-python-lib ../../../home-hub/services/accounting-service/.venv/bin/python -m pytest -q -p no:cacheprovider …` (the main checkout's venv; a mode-600 `.env` copy is already in the worktree root). Paths below are relative to `services/accounting-service/` unless they start with `docs/`.
- No migration, no new column, no new case kind, no new enum value: `reconciliation_action.action` and `statement_coverage.match_rule` are `String(32)`; `effect_slot` `String(16)`; `ReconciliationCase.explanation` is an enum (`deferred_next_period | accepted_exception`) — a peer case resolved by a transfer action gets `explanation=None` and `note=f"{action} from case {case_id}"` (spec §5.1 step 5 errata, Task 7). Settings are JSON keys.
- Every new action: registered via `register(...)` from a module imported in `reconciliation_actions/__init__.py:145`, an entry in `KIND_ACTIONS` and `CONDITIONS`; the engine's `allowed(case)`, idempotency, snapshots, `_check_creation_family` and proposals apply unchanged. Never nest `engine.apply_action`/`revert_action` inside an action. `reconciliation_actions` modules never import `proposal_service` or `policy_service` (both import the engine).
- Lock order is the engine's (`engine_locks.py`): statements ascending (+ sweeps; only the lowest id waits at the barrier) → case → `check_before_locks` → case statement's account row → barrier → definitions → instances → groups → entries with every top-level leg of their transfer (NOWAIT, 409 `action_busy`). The peer account row is never locked.
- Transfer actions never call `transfer_service.update_transfer` (it copies one date to both legs, deletes children and forces `source="manual"`); `adjust_transfer` writes leg columns directly; `pair_transfer` uses `transfer_service.create_transfer(db, payload, source="statement", remember=False)` then fixes the in leg's dates and both `needs_review`.
- Stand-in constants that learn `pair_transfer`: `stand_ins.BACKFILL_ACTIONS` (:20), `stand_ins.OWNER_TWIN_GUARDED` (:22), `policy_service.OWNER_DELETED_GUARDED` (:285), `coverage_service.RETIRABLE_ACTIONS` (:544); `coverage_service.TARGET_ACTIONS` (:628) learns `adjust_transfer`. `stand_ins.stand_in_entry_ids` filters by the entry's own `account_id`.
- Refusal codes (spec §5.2/§5.3): `peer_ambiguous`, `peer_leg_claimed`, `peer_not_live`, `peer_statement_busy`, `cross_currency`, `fx_pair_required`, `kind_sign`, `peer_is_card`, `not_standalone`, `has_settlements`, `entry_out_of_scope`, `peer_out_of_scope`, `candidate_changed` (409), `owner_may_record` (409), `owner_deleted` (422, field `action`), `duplicate_effect` (422, engine + peer-event repeat), `entry_claimed`, `line_claimed`, `not_revertable`.
- Rule defaults verbatim: `transfer_tolerance_pct = Decimal("0.10")`, `transfer_pair_window_days = 7`; policy block `actions.pair_transfer = {"enabled": False, "max_amount": "50000"}`; `POLICY_VERSION_PREFIX = "r6c"`. `adjust_transfer` has no policy block and `actors = frozenset({OWNER})`.
- Transfer-twin guard (spec §5.3 rev 6, owner ruling B): guard code as `_TwinGuarded._guard_code` (`ledger_actions.py:147–166`) — `candidate_changed` for POLICY, `owner_may_record` for the owner applying a policy-authored proposal, **no check** for a plain owner action or a non-policy proposal; occupancy (`owner_deleted`) and family (`duplicate_effect`) checks run for every actor. The guard fires when inside `transfer_pair_window_days` of either line (a) a transfer joins the two account families with at least one unclaimed leg, any amount; (b) an unclaimed top-level entry on either family has the line's flow sign and `|amount|` within `max(near_tolerance_abs, transfer_tolerance_pct·|line.flow|)`; (c) `stand_ins.owner_may_record` for either line.
- No owner financial data in tests, fixtures, docs, commit messages or dry-run summaries (fictional amounts only). Dry-run outputs mode 600 under `~/reports/`.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Full suite before the final review: `… -m pytest -q -p no:cacheprovider` → 0 failed (baseline at 56e79cf: backend 2578 passed per the PR E record; confirm the number in Task 7 before comparing).

## Review Focus

1. A card `payment` line whose only in-tolerance leg belongs to a card-to-card transfer must not get a `transfer_delta` case (`_peer_ok`), and `adjust_transfer` must refuse `peer_is_card` even when the case was hand-made — tests in Tasks 2 and 3.
2. The peer statement is locked with a lower id than the case statement (the engine sorts ids): the action's `validate_before` must re-derive the peer line under the locks and refuse `candidate_changed` when another session claimed it meanwhile — test in Task 3.
3. A `pair_transfer` revert when the owner has meanwhile categorised one leg (description/category only): structural fields unchanged → revert proceeds; an amount edit → `not_revertable` — test in Task 4.
4. The daily pass order (sweep → commit → policy per statement): after the owner deletes a paired transfer, the peer statement's policy run must be refused by the occupancy guard while the originating action is still `applied` — test in Task 5.
5. A `transfer_delta` case whose leg is already covered on the peer statement (the two banks print different figures): `adjust_transfer` refuses `peer_leg_claimed` and never releases the peer's coverage — test in Task 3.

---

### Task 1: Rules, policy settings and stand-in constants

**Files:**
- Modify: `app/services/statements/matching.py:124–138` (`Rules`), `app/services/settings_service.py` (`RULE_UNIT_INTERVAL` :217, `_check_rules` :221–250, `POLICY_VERSION_PREFIX` :116, `POLICY_DEFAULTS` :117–129), `app/services/stand_ins.py:20–37` (`BACKFILL_ACTIONS`, `OWNER_TWIN_GUARDED`, `stand_in_entry_ids`), `app/services/policy_service.py:285` (`OWNER_DELETED_GUARDED`), `app/services/coverage_service.py:544,628` (`RETIRABLE_ACTIONS`, `TARGET_ACTIONS`)
- Test: `tests/integration/test_reconciliation_service.py`, `tests/integration/test_reconciliation_actions_ledger.py`

**Interfaces:**
- Produces: `matching.Rules.transfer_tolerance_pct: Decimal = Decimal("0.10")`, `matching.Rules.transfer_pair_window_days: int = 7` (appended after `bank_only_patterns`); `settings_service.RULE_DEFAULTS` derives both; `_check_rules` refuses `transfer_pair_window_days` outside 0..60; `POLICY_DEFAULTS["actions"]["pair_transfer"] == {"enabled": False, "max_amount": "50000"}`; `POLICY_VERSION_PREFIX == "r6c"`; `stand_ins.BACKFILL_ACTIONS == frozenset({"backfill_entry", "pair_transfer"})`; `stand_ins.OWNER_TWIN_GUARDED` and `policy_service.OWNER_DELETED_GUARDED` contain `"pair_transfer"`; `coverage_service.RETIRABLE_ACTIONS` contains `"pair_transfer"`, `TARGET_ACTIONS` contains `"adjust_transfer"`; `stand_in_entry_ids(db, account_ids)` returns only entries whose own `account_id` is in `account_ids`.

- [ ] **Step 1: Write the failing tests**

In `tests/integration/test_reconciliation_service.py`:

```python
def test_r6c_rules_and_policy_defaults(db_session):
    settings = settings_service.read_reconciliation_settings(db_session)
    assert settings["rules"]["transfer_tolerance_pct"] == "0.10" and settings["rules"]["transfer_pair_window_days"] == 7
    assert settings["policy"]["actions"]["pair_transfer"] == {"enabled": False, "max_amount": "50000"}
    assert "adjust_transfer" not in settings["policy"]["actions"]
    rules = rec.rules_for(settings_service.reconciliation_rules(db_session), date(2026, 9, 30))
    assert rules.transfer_tolerance_pct == Decimal("0.10") and rules.transfer_pair_window_days == 7
    out = settings_service.update_reconciliation_settings(db_session, {"dirty_enabled": settings["dirty_enabled"], "rules": {"transfer_pair_window_days": 60}, "policy": {"actions": {"pair_transfer": {"enabled": True, "max_amount": "1000"}}}})
    assert out["policy_version"].startswith("r6c-") and out["rules"]["transfer_pair_window_days"] == 60
    with pytest.raises(ValidationError):
        settings_service.update_reconciliation_settings(db_session, {"dirty_enabled": False, "rules": {"transfer_pair_window_days": 61}})
    with pytest.raises(ValidationError):
        settings_service.update_reconciliation_settings(db_session, {"dirty_enabled": False, "rules": {"transfer_tolerance_pct": "1.5"}})
    with pytest.raises(ValidationError):
        settings_service.update_reconciliation_settings(db_session, {"dirty_enabled": False, "policy": {"actions": {"adjust_transfer": {"enabled": True}}}})

def test_r6c_action_name_constants():
    assert {"backfill_entry", "pair_transfer"} == stand_ins.BACKFILL_ACTIONS
    assert "pair_transfer" in stand_ins.OWNER_TWIN_GUARDED and "pair_transfer" in policy_service.OWNER_DELETED_GUARDED
    assert "pair_transfer" in coverage_service.RETIRABLE_ACTIONS and "adjust_transfer" in coverage_service.TARGET_ACTIONS
```

In `tests/integration/test_reconciliation_actions_ledger.py` (uses the existing `_booked`/backfill helpers; a stand-in on account A, queried for account B):

```python
def test_stand_in_entry_ids_filters_by_the_entry_account(dirty_on, seed, run, db_session):
    card = _card(seed, db_session); other = _card(seed, db_session, name="卡乙")
    stmt, lines, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "超商", "-300")])
    case = next(c for c in _cases(db_session, stmt.id, "open") if c.kind == "line_unmatched")
    row = _apply(db_session, case, "backfill_entry", {"kind": "expense"})
    created = row.created_entry_ids[0]
    assert created in stand_ins.stand_in_entry_ids(db_session, [card.id])
    assert created not in stand_ins.stand_in_entry_ids(db_session, [other.id])
    db_session.execute(update(LedgerEntry).where(LedgerEntry.id == created).values(account_id=other.id)); db_session.commit()
    assert created not in stand_ins.stand_in_entry_ids(db_session, [card.id])   # the entry's own account decides
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `… -m pytest tests/integration/test_reconciliation_service.py -k r6c tests/integration/test_reconciliation_actions_ledger.py -k stand_in_entry_ids -q -p no:cacheprovider`
Expected: FAIL (`KeyError: 'transfer_tolerance_pct'`, `AssertionError` on the constants).

- [ ] **Step 3: Implement**

`matching.Rules`: append `transfer_tolerance_pct: Decimal = Decimal("0.10")` and `transfer_pair_window_days: int = 7` (after `bank_only_patterns`; `population_window_days` unchanged — the pair window never widens the population). `settings_service`: `RULE_UNIT_INTERVAL |= {"transfer_tolerance_pct"}`; in `_check_rules` the int branch keeps `0 <= value <= 10000` and adds `if key == "transfer_pair_window_days": ok = ok and value <= 60`; `POLICY_VERSION_PREFIX = "r6c"`; `POLICY_DEFAULTS["actions"]["pair_transfer"] = {"enabled": False, "max_amount": "50000"}` (`_check_policy` then accepts it, `settings_service.py:295–296`). Constants per Interfaces. `stand_in_entry_ids`: add `LedgerEntry.account_id.in_(account_ids)` to the existing query's entry filter (the join through the case statement's account stays).

- [ ] **Step 4: Run the tests and the settings/stand-in suites**

Run: `… -m pytest tests/integration/test_reconciliation_service.py tests/integration/test_reconciliation_actions_ledger.py tests/integration/test_reconciliation_policy.py -q -p no:cacheprovider`
Expected: PASS (existing `policy_version` assertions that pin `r6b-` must be updated to `r6c-` in the same commit — grep `r6b-` in `tests/`).

- [ ] **Step 5: Commit**

```bash
git add app/services/statements/matching.py app/services/settings_service.py app/services/stand_ins.py app/services/policy_service.py app/services/coverage_service.py tests/
git commit -m "feat(reconciliation): R6c rules and policy keys, transfer actions in the stand-in constants"
```

---

### Task 2: Matcher — `_transfer_case` and the `transfer_delta` hint

**Files:**
- Modify: `app/services/statements/matching.py` (new `_transfer_case` beside `_near_case` :883; call chain :1062–1064; `TRANSFER_HINT` constant beside `PEER_LINE_KINDS` :936)
- Test: `tests/unit/test_statement_matching.py`

**Interfaces:**
- Consumes: Task 1 `Rules.transfer_tolerance_pct`.
- Produces: `TRANSFER_HINT = "transfer_delta"`; `def _transfer_case(kind: str, line: Line, entries: list[Entry], claimed: set[int], rules: Rules, apart: frozenset[int] = frozenset()) -> Case | None`; `match()` chain becomes `_foreign_case(...) or _transfer_case(statement_kind, line, principals, claimed, rules, apart) or _near_case(...) or _drift_case(...)`. Single candidate → `Case(line.id, e.id, "amount_delta", ({"entry_id": e.id, "reason": "transfer", "residual": str(delta), "date_distance": dist},), {"hint": TRANSFER_HINT, "entry_id": e.id, "residual": str(delta), "date_distance": dist, "fee_matches": False})`; several → `Case(line.id, None, "ambiguous", <same dicts>, {"hint": TRANSFER_HINT})`; `residual = (line.flow − e.flow).quantize(Q)`.

- [ ] **Step 1: Write the failing tests** (`d = date(2026, 9, 3)`; `E(..., kind="transfer_in", transfer_group_id="g1", peer_is_card=False)` is a bank-peer leg; card statement unless `kind="bank"`)

```python
def test_transfer_line_with_a_same_account_leg_inside_tolerance_is_a_transfer_delta_case():
    res = run([L(1, d, "20000", kind="payment", merchant="信用卡繳款")],
              [E(10, d + timedelta(days=2), "19500", kind="transfer_in", name="轉帳", transfer_group_id="g1", peer_is_card=False)])
    c = res.cases[0]
    assert (c.kind, c.entry_id, c.context["hint"], c.context["residual"], c.context["date_distance"]) == ("amount_delta", 10, "transfer_delta", "500.0000", 2)
    assert c.candidates == ({"entry_id": 10, "reason": "transfer", "residual": "500.0000", "date_distance": 2},)
    assert res.explained.get(10) != "deferred_next_period" and 10 in res.unmatched_entry_ids   # designated, still unmatched («BC 2»)

def test_exact_amount_leg_five_days_away_is_transfer_delta_not_date_drift():
    res = run([L(1, d, "20000", kind="payment")], [E(10, d + timedelta(days=5), "20000", kind="transfer_in", transfer_group_id="g1", peer_is_card=False)])
    assert res.cases[0].context["hint"] == "transfer_delta" and res.cases[0].context["residual"] == "0.0000"

def test_leg_outside_the_transfer_tolerance_falls_back_to_needs_peer():
    res = run([L(1, d, "20000", kind="payment")], [E(10, d + timedelta(days=1), "17000", kind="transfer_in", transfer_group_id="g1", peer_is_card=False)])
    assert res.cases[0].kind == "line_unmatched" and res.cases[0].context == {"hint": "needs_peer"}

def test_two_legs_inside_tolerance_are_ambiguous_with_the_transfer_hint():
    legs = [E(10, d, "19800", kind="transfer_in", transfer_group_id="g1", peer_is_card=False), E(11, d + timedelta(days=1), "20300", kind="transfer_in", transfer_group_id="g2", peer_is_card=False)]
    c = run([L(1, d, "20000", kind="payment")], legs).cases[0]
    assert c.kind == "ambiguous" and c.context == {"hint": "transfer_delta"} and [x["entry_id"] for x in c.candidates] == [10, 11]

def test_two_lines_may_designate_one_leg_without_reservation():
    leg = [E(10, d, "20000", kind="transfer_in", transfer_group_id="g1", peer_is_card=False)]
    res = run([L(1, d, "19900", kind="payment"), L(2, d + timedelta(days=1), "20100", kind="payment")], leg)
    assert [c.entry_id for c in res.cases] == [10, 10]

def test_settlement_and_non_transfer_entries_are_never_transfer_candidates():
    bank = run([L(1, d, "-20000", kind="payment")], [E(10, d, "-19900", kind="expense"), E(11, d, "-19900", kind="receivable", is_settlement=True)], kind="bank")
    assert bank.cases[0].kind == "amount_delta" and bank.cases[0].context.get("hint") is None   # same-day near case as today, not transfer_delta

def test_card_payment_line_ignores_a_leg_whose_peer_is_a_card():
    res = run([L(1, d, "20000", kind="payment")], [E(10, d + timedelta(days=1), "19900", kind="transfer_in", transfer_group_id="g1", peer_is_card=True)])
    assert res.cases[0].context == {"hint": "needs_peer"}

def test_purchase_lines_never_reach_the_transfer_builder():
    res = run([L(1, d, "-300")], [E(10, d + timedelta(days=2), "-290", kind="transfer_out", transfer_group_id="g1", peer_is_card=False)])
    assert res.cases[0].kind == "line_unmatched"

def test_exact_leg_within_the_exact_window_still_claims_without_a_case():
    res = run([L(1, d, "20000", kind="payment")], [E(10, d + timedelta(days=1), "20000", kind="transfer_in", transfer_group_id="g1", peer_is_card=False)])
    assert res.claims[0].representation.rule == "payment" and res.cases == []

def test_transfer_tolerance_rule_key_is_honoured():
    rules = m.Rules(period_end=date(2026, 9, 30), transfer_tolerance_pct=Decimal("0.01"))
    res = m.match("card", [L(1, d, "20000", kind="payment")], [E(10, d + timedelta(days=2), "19500", kind="transfer_in", transfer_group_id="g1", peer_is_card=False)], {}, {}, lambda e: None, rules, "TWD")
    assert res.cases[0].context == {"hint": "needs_peer"}
```

Also re-read `test_needs_peer_is_not_set_when_a_near_or_drift_case_exists` (:930) and `test_near_applies_to_card_payment_lines_through_peer_ok` (:834): their entries carry no `transfer_group_id`, so they still go through `_near_case`; keep them green unchanged.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `… -m pytest tests/unit/test_statement_matching.py -k "transfer_delta or transfer_builder or transfer_tolerance or designate_one_leg or peer_is_a_card or exact_leg_within" -q -p no:cacheprovider`
Expected: FAIL (the first case is `needs_peer` today).

- [ ] **Step 3: Implement `_transfer_case`**

Candidates: `e.id not in claimed`, `(_standalone(e) or e.id in apart)`, `_entry_class(e) in eligible_kinds(kind, line.line_kind)`, `_peer_ok(kind, line, e)`, `e.transfer_group_id is not None`, same sign (`(e.flow > 0) == (line.flow > 0)`), `abs(line.flow − e.flow) ≤ max(rules.near_tolerance_abs, (rules.transfer_tolerance_pct * abs(line.flow)).quantize(Q))`, `_date_distance(line, e) <= rules.drift_window_days`. Return None unless `line.line_kind in PEER_LINE_KINDS`. Sort `(abs(delta), dist, e.id)`. Build the cases per Interfaces. Insert the call in the chain at :1062–1064.

- [ ] **Step 4: Run the whole unit file**

Run: `… -m pytest tests/unit/test_statement_matching.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/statements/matching.py tests/unit/test_statement_matching.py
git commit -m "feat(reconciliation): transfer_delta — a transfer line pairs with a same-account leg within tolerance"
```

---

### Task 3: `transfer_actions.py` — shared helpers and `adjust_transfer`

**Files:**
- Create: `app/services/reconciliation_actions/transfer_actions.py`
- Modify: `app/services/reconciliation_actions/__init__.py` (`KIND_ACTIONS` :24–35, `CONDITIONS` :109–121, import list :145), `app/services/reconciliation_actions/base.py:30–45` (`Context.action_id`), `app/services/reconciliation_actions/engine.py:277–284` (set `ctx.action_id = row.id` after the insert, before `apply`), `app/services/stand_ins.py` (new `pair_transfer_block`)
- Test: `tests/integration/test_reconciliation_actions_transfer.py` (new)

**Interfaces:**
- Consumes: Task 2 `TRANSFER_HINT`; `_refuse`, `_open_line`, `case_line`, `_gate`, `_match_kind`, `_release`, `_DECIMALS` from `ledger_actions`; `_reconcile_keeping_case`, `_reopen` from `structural_actions`; `coverage_service.write_claim/release_rows/participating_accounts/claimed_entry_ids/active_rows`; `ews.assert_entry_editable/has_settlements_or_refunds/_plain`; `schedule_rows`, `lockset_for` from `base`; `audit.record`.
- Produces (module `transfer_actions`):
  - `ADJUST, PAIR = "adjust_transfer", "pair_transfer"`; `PEER_LINE_KINDS_FOR_ADJUST = frozenset({"payment", "transfer_in", "transfer_out", "withdrawal", "deposit"})`.
  - `def peer_leg(db: Session, leg: LedgerEntry) -> LedgerEntry | None` — the other top-level entry of `leg.transfer_group_id`.
  - `def peer_statements(db: Session, account_id: int, currency: str, days: Iterable[date]) -> list[AccountStatement]` — live statements of `participating_accounts(account_id)` in `currency` whose period contains any of `days`, ascending id.
  - `def complementary_lines(db: Session, statements: list[AccountStatement], line: StatementLine, *, kinds: frozenset[str], window_days: int) -> list[tuple[AccountStatement, StatementLine]]` — current-revision lines of those statements, `line_kind in kinds`, `flow_amount == -line.flow_amount`, `|posted_date − line.posted_date| ≤ window_days`, no active coverage; ordered by (day distance, line id).
  - `def peer_statement_refusal(statement: AccountStatement, db: Session) -> str | None` — `"peer_not_live"` when `mode != "live"`, `"peer_statement_busy"` when `conflict_open` or `needs_recheck` or an open `parse_review`/`statement_conflict` case exists (local copy of `policy_service.statement_excluded`'s predicate: no import).
  - `def resolve_peer_case(db: Session, ctx: Context, peer_statement: AccountStatement, peer_line: StatementLine) -> int | None` — the open/proposed case of `peer_line.event_id` on the peer statement → `status="resolved"`, `explanation=None`, `note=f"{ctx_action} from case {ctx.case.id}"`, `resolved_by=ctx.actor`, `resolved_action_id=ctx.action_id`, `resolved_at=func.now()`; its pending proposals → `superseded`, `decided_at=func.now()`, `audit.record(db, event="proposal_superseded", actor=ctx.label, statement_id=peer_statement.id, case_id=case.id, action_id=ctx.action_id, run_id=ctx.run_id, request_id=ctx.request_id, detail={"proposal_id": p.id, "by": "peer_resolved"})`; returns the case id.
  - `def reopen_peer_case(db: Session, action: ReconciliationAction) -> None` — `_reopen` the stored `inverse["peer_case_id"]` row when it is still `resolved` with `resolved_action_id == action.id`.
  - `def claim_on(db, ctx, statement, line, entry, rule) -> list[int]` — `coverage_service.write_claim(db, statement, line, [matching.Row(entry.id, "principal", Decimal(entry.amount))], entries_by_id={entry.id: entry}, match_kind=_match_kind(ctx), match_rule=rule, run_id=ctx.run_id)` ids.
  - `class AdjustTransferParams(ActionParams): entry_id: Int32; line_id: Int32`.
  - `class AdjustTransferAction(ActionBase)`: `name = ADJUST`, `case_kinds = frozenset({"amount_delta", "ambiguous"})`, `actors = frozenset({OWNER})`, `slot = AMOUNT` (import from ledger_actions); `lock_targets` → `lockset_for(db, entry_ids=(leg, peer leg), statement_ids=<peer statement ids from discovery>)`; `prepare` → base `Prepared` plus `data={"peer_leg_id", "peer_statement_ids", "peer_line_id"}`; `validate_before` → re-derive and compare (409 `candidate_changed`), then `_plan`; `apply`; `invert`; `revert_lock_targets` (adds stored `statement_ids`). `inverse = {"restore": {str(leg_id): {amount, entry_date, posted_date, description}}, "statement_ids": [...], "peer_case_id", "peer_event_id", "peer_line_id"}` (dates ISO strings, amounts str).
- `base.Context.action_id: int | None = None`; engine sets it right after `_insert` (engine.py:277–281) and before `impl.apply`.
- `stand_ins.pair_transfer_block(db: Session, event_ids: Iterable[int]) -> bool` — True when any `ReconciliationAction` with `action == "pair_transfer"` and (`event_id in event_ids` or `inverse->>'peer_event_id'` in event_ids) is `applied`, or is `reverted` with `inverse ? 'deleted_by_event'` or `inverse ? 'deleted_before_watermark'` and no later applied creation (`effect_slot == CREATION_SLOT`, `status == "applied"`) exists for that event. (Used by Task 4; defined here so the module compiles with its import.)
- Registry: `KIND_ACTIONS["amount_delta"] = ("match", "adjust_amount", "adjust_transfer", "attach_fee", "split_entry", "dismiss")`, `KIND_ACTIONS["ambiguous"] = ("match", "adjust_amount", "adjust_transfer", "dismiss")`; `CONDITIONS[("amount_delta", "adjust_transfer")] = _designates_transfer_leg` (the entry `context.entry_id`/`case.entry_id` has `transfer_group_id`), `CONDITIONS[("ambiguous", "adjust_transfer")] = _lists_transfer_leg` (any candidate `entry_id` is a leg).

- [ ] **Step 1: Write the fixture and the failing tests**

`tests/integration/test_reconciliation_actions_transfer.py` header: import `_apply, _revert, _refuse, _code, _fresh` from `test_reconciliation_actions_ledger`, `_card, _submit, _line, _cases, _active, _transfer, run` from `statement_fixtures`, `_set_policy, _sweep` from `test_reconciliation_policy`; local `def _bank(seed, db, name="銀行", **extra)` = `seed.account(name, statement_live_from=date(2026, 9, 1), **extra)` + commit. Fixture:

```python
@pytest.fixture
def pair(dirty_on, seed, run, db_session):
    """Bank (lower id) and card, one live September statement each; the owner's transfer 19500 on 9/4 vs lines 20000 on 9/5 (bank payment −20000, card payment +20000)."""
    bank = _bank(seed, db_session); card = _card(seed, db_session, name="卡")
    out_leg, in_leg = _transfer(seed, bank, card, "19500", date(2026, 9, 4)); db_session.commit()
    bstmt, blines, _ = _submit(db_session, run, bank, [_line(1, date(2026, 9, 5), "信用卡繳款", "-20000", kind="payment")], kind="bank")
    cstmt, clines, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 5), "繳款", "20000", kind="payment")])
    return SimpleNamespace(bank=bank, card=card, out_leg=out_leg, in_leg=in_leg, bstmt=bstmt, cstmt=cstmt, bline=blines[1], cline=clines[1])

def _delta(db, stmt):  # the transfer_delta case of a statement
    return next(c for c in _cases(db, stmt.id, "open") if c.kind == "amount_delta" and (c.context or {}).get("hint") == "transfer_delta")
```

Tests:

```python
def test_fixture_opens_transfer_delta_on_both_statements_and_entry_unmatched_on_both_legs(pair, db_session):
    assert _delta(db_session, pair.bstmt).entry_id == pair.out_leg.id and _delta(db_session, pair.cstmt).entry_id == pair.in_leg.id
    assert {c.entry_id for c in _cases(db_session, pair.bstmt.id, "open") if c.kind == "entry_unmatched"} == {pair.out_leg.id}
    assert reconciliation_actions.allowed(_delta(db_session, pair.cstmt)) == ["match", "adjust_transfer", "dismiss"]   # adjust_amount hidden: protected leg

def test_adjust_transfer_rewrites_both_legs_claims_both_lines_and_closes_four_cases(pair, db_session):
    case = _delta(db_session, pair.cstmt)
    row = _apply(db_session, case, "adjust_transfer", {"entry_id": pair.in_leg.id, "line_id": pair.cline.id})
    out_leg, in_leg = _fresh(db_session, LedgerEntry, pair.out_leg.id), _fresh(db_session, LedgerEntry, pair.in_leg.id)
    assert (out_leg.amount, in_leg.amount) == (Decimal("-20000"), Decimal("20000"))
    assert out_leg.posted_date == in_leg.posted_date == date(2026, 9, 5) and out_leg.source == in_leg.source == "moze_backup" or out_leg.source == "manual"   # source untouched (seed default)
    assert "對帳調整" in out_leg.description and "對帳調整" in in_leg.description
    assert {r.line_id for r in _active(db_session, pair.cstmt.id)} == {pair.cline.id} and {r.line_id for r in _active(db_session, pair.bstmt.id)} == {pair.bline.id}
    assert all(r.match_rule == "adjust_transfer" and r.match_kind == "manual" for r in _active(db_session, pair.cstmt.id) + _active(db_session, pair.bstmt.id))
    peer = _fresh(db_session, ReconciliationCase, row.inverse["peer_case_id"])
    assert (peer.status, peer.resolved_action_id, peer.note) == ("resolved", row.id, f"adjust_transfer from case {case.id}") and peer.explanation is None
    assert _cases(db_session, pair.bstmt.id, "open") == [] and _cases(db_session, pair.cstmt.id, "open") == []
    assert sorted(row.inverse) == ["peer_case_id", "peer_event_id", "peer_line_id", "restore", "statement_ids"] and row.inverse["statement_ids"] == [pair.bstmt.id]

def test_adjust_transfer_revert_restores_both_legs_and_reopens_both_lines(pair, db_session):
    case = _delta(db_session, pair.cstmt)
    row = _apply(db_session, case, "adjust_transfer", {"entry_id": pair.in_leg.id, "line_id": pair.cline.id})
    _revert(db_session, row.id)
    assert _fresh(db_session, LedgerEntry, pair.out_leg.id).amount == Decimal("-19500") and _fresh(db_session, LedgerEntry, pair.out_leg.id).posted_date == date(2026, 9, 4)
    assert _active(db_session, pair.cstmt.id) == [] and _active(db_session, pair.bstmt.id) == []
    assert _fresh(db_session, ReconciliationCase, case.id).status == "open" and _delta(db_session, pair.bstmt) is not None   # peer line has an open case again

def test_adjust_transfer_supersedes_a_pending_peer_proposal(pair, db_session):
    peer = _delta(db_session, pair.bstmt)
    prop = proposal_service.create_proposal(db_session, case_id=peer.id, payload=ProposalIn(action="match", params={"line_id": pair.bline.id, "entry_id": pair.out_leg.id}), author="hermes"); db_session.commit()
    _apply(db_session, _delta(db_session, pair.cstmt), "adjust_transfer", {"entry_id": pair.in_leg.id, "line_id": pair.cline.id})
    assert _fresh(db_session, ReconciliationProposal, prop.id).status == "superseded"
    assert any(a.detail == {"proposal_id": prop.id, "by": "peer_resolved"} for a in _audit(db_session, "proposal_superseded"))

def test_adjust_transfer_without_a_peer_line_rewrites_amounts_and_keeps_the_peer_dates(dirty_on, seed, run, db_session):
    bank = _bank(seed, db_session); card = _card(seed, db_session, name="卡")   # bank not live: no peer statement
    bank.statement_live_from = None; db_session.commit()
    out_leg, in_leg = _transfer(seed, bank, card, "19500", date(2026, 9, 4)); db_session.commit()
    cstmt, clines, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 5), "繳款", "20000", kind="payment")])
    row = _apply(db_session, _delta(db_session, cstmt), "adjust_transfer", {"entry_id": in_leg.id, "line_id": clines[1].id})
    assert _fresh(db_session, LedgerEntry, out_leg.id).amount == Decimal("-20000") and _fresh(db_session, LedgerEntry, out_leg.id).posted_date == date(2026, 9, 4)
    assert row.inverse["peer_line_id"] is None and row.inverse["statement_ids"] == [] and len(_active(db_session, cstmt.id)) == 1

def test_adjust_transfer_finds_a_bank_withdrawal_peer_line(dirty_on, seed, run, db_session):
    # bank prints the card payment as a withdrawal (not a payment line)
    ... same shape as `pair` with the bank line `_line(1, date(2026, 9, 5), "提款", "-20000", kind="withdrawal")`; after the apply both lines are claimed and the bank withdrawal's line case is resolved by the action.

@pytest.mark.parametrize("variant,code", [("peer_claimed", "peer_leg_claimed"), ("two_peer_lines", "peer_ambiguous"), ("child", "not_standalone"), ("cross_currency", "cross_currency"), ("card_peer", "peer_is_card"), ("wrong_sign", "kind_sign")])
def test_adjust_transfer_refusals(variant, code, pair, seed, db_session):
    ... per variant: claim the bank line onto the out leg by `match` first (peer_claimed); add a second complementary bank line in the same statement revision (two_peer_lines — resubmit with two lines); `seed.entry(pair.card, "-5", kind="fee", parent_entry_id=pair.in_leg.id)` (child); change the card's currency to JPY and the legs' currency (cross_currency); make the bank account `is_credit=True` (card_peer, refused for the card payment line); hand-make an `amount_delta` case naming the out leg from the card line (wrong_sign).
    exc = _refuse(db_session, lambda: _apply(db_session, _delta(db_session, pair.cstmt), "adjust_transfer", {"entry_id": pair.in_leg.id, "line_id": pair.cline.id}))
    assert _code(exc) == code

def test_adjust_transfer_candidate_changed_when_the_peer_line_is_claimed_between_discovery_and_the_locks(pair, db_session, monkeypatch):
    impl = reconciliation_actions.ACTIONS["adjust_transfer"]; original = impl.lock_targets
    def lock_then_claim(ctx, params):
        targets = original(ctx, params)
        coverage_service.write_claim(ctx.db, pair.bstmt, pair.bline, [matching.Row(pair.out_leg.id, "principal", Decimal("-20000"))], entries_by_id={pair.out_leg.id: ctx.db.get(LedgerEntry, pair.out_leg.id)}, match_kind="manual", match_rule="match", run_id=None)  # amount mismatch is irrelevant to the lock test: use a hand claim helper if write_claim refuses conservation
        return targets
    monkeypatch.setattr(impl, "lock_targets", lock_then_claim)
    exc = _refuse(db_session, lambda: _apply(db_session, _delta(db_session, pair.cstmt), "adjust_transfer", {"entry_id": pair.in_leg.id, "line_id": pair.cline.id}))
    assert _code(exc) == "candidate_changed"

def test_second_line_on_the_same_leg_is_refused_entry_claimed(pair, seed, run, db_session):
    ... resubmit the card statement with a second payment line 20100 on 9/6 → two transfer_delta cases naming in_leg; apply the first; the second → `entry_claimed`.

def test_adjust_amount_still_refuses_a_leg_and_the_inbox_hides_accept(pair, db_session):
    exc = _refuse(db_session, lambda: _apply(db_session, _delta(db_session, pair.cstmt), "adjust_amount", {"entry_id": pair.in_leg.id, "line_id": pair.cline.id}))
    assert _code(exc) == "protected"

def test_allowed_lists_adjust_transfer_on_near_and_ambiguous_leg_cases_only(pair, db_session):
    near = _delta_case(db_session, pair.cstmt, pair.cline, pair.in_leg, fee_matches=False)   # hand case without the hint
    assert "adjust_transfer" in reconciliation_actions.allowed(near)
    amb = _ambiguous_case(db_session, pair.cstmt, pair.cline, [pair.in_leg, seed.entry(pair.card, "20050", kind="income", day=date(2026, 9, 5))])
    assert "adjust_transfer" in reconciliation_actions.allowed(amb)
    plain = _delta_case(db_session, pair.cstmt, pair.cline, seed.entry(pair.card, "20050", kind="income", day=date(2026, 9, 5)), fee_matches=False)
    assert "adjust_transfer" not in reconciliation_actions.allowed(plain)
```

- [ ] **Step 2: Run the new file to verify it fails**

Run: `… -m pytest tests/integration/test_reconciliation_actions_transfer.py -q -p no:cacheprovider -x`
Expected: FAIL at the first test (`allowed` lacks `adjust_transfer`).

- [ ] **Step 3: Implement the module, the registry entries, `Context.action_id`, `pair_transfer_block`**

`AdjustTransferAction._discover(db, case, params) -> dict`: leg = `db.get(LedgerEntry, params.entry_id)`; peer = `peer_leg`; statements = `peer_statements(db, peer.account_id, peer.currency, [peer.posted_date, line.posted_date])`; peer line = `complementary_lines(db, statements, line, kinds=PEER_LINE_KINDS_FOR_ADJUST, window_days=rules.drift_window_days)` → 0 or 1 (≥2 → `peer_ambiguous` raised in `_plan`, discovery returns the list); statement ids = those containing the peer leg's date ∪ the peer line's statement. `_plan` order: `_scoped` (`entry_out_of_scope`) → `_open_line(ctx, case_line(ctx, params.line_id))` → `_gate(ctx, leg, "entry_id", id)` (`entry_claimed` etc.) → peer leg exists (`not_standalone` when `transfer_group_id` None or no peer) → children/schedule rows (`not_standalone`) → `assert_entry_editable` both → `has_settlements` both → `cross_currency` → `fx_pair_required` → `kind_sign` → `peer_is_card` (card payment line and `Account.is_credit` of the peer leg's account) → peer statements: `peer_statement_refusal` each → `peer_leg_claimed` (peer leg in `claimed_entry_ids(db)`) → peer line uncovered. Apply per spec §5.2: amounts, dates (this leg from `line`, peer leg from the peer line when found), description note `f"對帳調整 原金額 {ews._plain(old)}"` appended; flush; `claim_on` this line and the peer line; `resolve_peer_case`; `_reconcile_keeping_case(db, ctx.case, [ctx.statement.id, *statement_ids])`; effect fields. `invert`: legs present and current values equal `after` snapshot (else `NotRevertable`); restore; `_release`; `reopen_peer_case`; `_reopen(ctx.case)`; `_reconcile_keeping_case`; return `Effect(touched_entry_ids=[both])`. `revert_lock_targets` as `MoveAccountAction` (:171–175). Engine: `ctx.action_id = row.id` after the insert (engine.py:281).

- [ ] **Step 4: Run the new file and the neighbours**

Run: `… -m pytest tests/integration/test_reconciliation_actions_transfer.py tests/integration/test_reconciliation_actions_ledger.py tests/integration/test_reconciliation_actions_engine.py tests/integration/test_reconciliation_actions_structural.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/reconciliation_actions/ app/services/stand_ins.py tests/integration/test_reconciliation_actions_transfer.py
git commit -m "feat(reconciliation): adjust_transfer — rewrite a transfer to the statement figures across both statements"
```

---

### Task 4: `pair_transfer` action, proposal validation, owner-deleted stamp

**Files:**
- Modify: `app/services/reconciliation_actions/transfer_actions.py`, `app/services/reconciliation_actions/__init__.py` (`KIND_ACTIONS["line_unmatched"]`, `CONDITIONS`), `app/services/proposal_service.py:69–82` (`_validate`), `app/services/reconciliation_service.py:634–648` (`_desired_cases` stamp), `app/services/reconciliation_actions/ledger_actions.py` (nothing; `replace_stand_in` already refuses legs — test only)
- Test: `tests/integration/test_reconciliation_actions_transfer.py`, `tests/integration/test_reconciliation_proposals.py`, `tests/integration/test_reconciliation_service.py`

**Interfaces:**
- Consumes: Task 3 helpers, `stand_ins.pair_transfer_block`, `stand_ins.owner_may_record`, `transfer_service.create_transfer`, `TransferIn`.
- Produces: `class PairTransferParams(ActionParams): peer_line_id: Int32`; `class PairTransferAction(ActionBase)`: `name = PAIR`, `case_kinds = frozenset({"line_unmatched"})`, `actors = frozenset({OWNER, POLICY})`, `slot = CREATE`; `@staticmethod def peer_candidates(db: Session, case: ReconciliationCase) -> list[tuple[AccountStatement, StatementLine]]` (live statements of accounts **outside** the case statement's participating family, same currency, `complementary_lines(..., kinds=matching.PEER_LINE_KINDS, window_days=rules.transfer_pair_window_days)`, whose own case is open/proposed with hint `needs_peer`); `lock_targets` → `lockset_for(db, statement_ids=(peer statement,), account_lock=True, barrier=True)`; `check_before_locks` → `dirty_disabled` when `read_reconciliation_settings(db)["dirty_enabled"]` is false (every actor); `validate_before` → re-derive (`candidate_changed`), `peer_statement_refusal`, `_open_line` both, peer event family check (`duplicate_effect`), occupancy guard (`owner_deleted`) on both events, then — only when `_guard_code(ctx)` is not None (copy of `_TwinGuarded._guard_code`, `ledger_actions.py:147–153`: POLICY → `candidate_changed`, policy-authored proposal → `owner_may_record`, else None) — the transfer-twin guard refusing with that code (409); `def transfer_twin_reason(db, rules, a: tuple[AccountStatement, StatementLine], b: tuple[AccountStatement, StatementLine]) -> str | None` (rules (a)/(b)/(c)); `apply`; `invert`; `revert_lock_targets`.
- `KIND_ACTIONS["line_unmatched"] = ("match", "create_entry", "create_system_entry", "backfill_entry", "pair_transfer", "create_refund", "link_installment_plan", "split_entry", "dismiss")`; `CONDITIONS[("line_unmatched", "pair_transfer")] = lambda case: _context(case).get("hint") == PEER_HINT and not case.candidates`.
- `proposal_service._validate`: in the `*_id` loop, `if name == "peer_line_id": allowed = {line.id for _, line in ACTIONS["pair_transfer"].peer_candidates(db, case)}` (no `ID_KEYS` change).
- `_desired_cases`: for a desired `line_unmatched` case with `context.hint == "needs_peer"`, `context["owner_deleted"] = True` when `stand_ins.pair_transfer_block(db, [event_id])` is true (the `stand_in_deleted` default hint is **not** set for these — the hint stays `needs_peer`).

- [ ] **Step 1: Write the failing tests**

Fixture `orphan_pair`: like `pair` but **no** owner transfer → both lines `needs_peer`. Tests in `test_reconciliation_actions_transfer.py`:

```python
def test_pair_transfer_books_one_transfer_claims_both_lines_and_closes_both_cases(orphan_pair, db_session):
    p = orphan_pair; case = _needs_peer(db_session, p.cstmt)
    assert reconciliation_actions.allowed(case) == ["match", "pair_transfer", "dismiss"]
    cands = reconciliation_actions.ACTIONS["pair_transfer"].peer_candidates(db_session, case)
    assert [(s.id, l.id) for s, l in cands] == [(p.bstmt.id, p.bline.id)]
    row = _apply(db_session, case, "pair_transfer", {"peer_line_id": p.bline.id})
    out_leg, in_leg = (db_session.get(LedgerEntry, i) for i in sorted(row.created_entry_ids))
    assert (out_leg.account_id, out_leg.kind, out_leg.amount, out_leg.posted_date) == (p.bank.id, "transfer_out", Decimal("-20000"), date(2026, 9, 5))
    assert (in_leg.account_id, in_leg.kind, in_leg.amount, in_leg.posted_date) == (p.card.id, "transfer_in", Decimal("20000"), date(2026, 9, 5))
    assert out_leg.transfer_group_id == in_leg.transfer_group_id and out_leg.source == in_leg.source == "statement" and out_leg.needs_review and in_leg.needs_review and out_leg.name == "對帳配對轉帳"
    assert row.effect_slot == "create" and sorted(row.inverse) == ["created_entry_ids", "peer_case_id", "peer_event_id", "peer_line_id", "statement_ids"]
    assert _cases(db_session, p.bstmt.id, "open") == [] and _cases(db_session, p.cstmt.id, "open") == []
    assert in_leg.id in stand_ins.stand_in_entry_ids(db_session, [p.card.id]) and out_leg.id not in stand_ins.stand_in_entry_ids(db_session, [p.bank.id])   # option A: stand-in on the case statement only

def test_pair_transfer_with_different_line_dates_keeps_each_legs_own_date(…):  # bank line 9/5, card line 9/7 → out 9/5, in 9/7

def test_pair_transfer_revert_deletes_both_legs_and_reopens_both_cases(orphan_pair, db_session):
    row = _apply(...); _revert(db_session, row.id)
    assert all(db_session.get(LedgerEntry, i) is None for i in row.created_entry_ids)
    assert _needs_peer(db_session, orphan_pair.bstmt) is not None and _needs_peer(db_session, orphan_pair.cstmt) is not None

def test_pair_transfer_revert_survives_a_cosmetic_edit_and_refuses_an_amount_edit(orphan_pair, db_session):
    ... edit `description` on one leg → revert OK; second scenario: edit `amount` on one leg (direct SQL) → `_revert` raises `not_revertable`.

def test_pair_transfer_refuses_a_peer_outside_the_candidates_and_a_busy_or_historical_peer(orphan_pair, ...):
    ... a line of a third account → `peer_out_of_scope`; set `bstmt.needs_recheck = True` → `peer_statement_busy`; set `bstmt.mode = "historical"` → `peer_not_live`.

def test_pair_transfer_repeats_the_creation_family_check_for_the_peer_event(orphan_pair, db_session):
    ... apply `create_entry` on the bank line's case first (so the bank event has an applied creation) → `pair_transfer` from the card → `duplicate_effect`.

@pytest.mark.parametrize("twin", ["unclaimed_transfer_any_amount", "near_amount_entry_any_kind", "r6b_twin"])
def test_pair_transfer_transfer_twin_guard_binds_the_policy_and_policy_proposals_not_the_owner(twin, orphan_pair, seed, db_session):
    ... twins (added where marked, after the proposal exists and before each refusal): (a) `_transfer(seed, bank, card, "17000", date(2026, 9, 1))` — 15 % off, unclaimed; (b) `seed.entry(bank, "-19000", kind="expense", day=date(2026, 9, 6))` (5 % inside the 10 % tolerance); (c) `seed.entry(card, "20000", kind="transfer_in", day=date(2026,9,5))` standalone exact. Order: proposal first, then twin, then the three applies below.
    assert transfer_actions.transfer_twin_reason(db_session, rules, (p.cstmt, p.cline), (p.bstmt, p.bline)) is not None
    # policy actor → candidate_changed (409); owner applying a policy-authored proposal → owner_may_record (409)
    exc = _refuse(db_session, lambda: _apply(db_session, case, "pair_transfer", {"peer_line_id": p.bline.id}, actor="policy")); assert _code(exc) == "candidate_changed"
    # policy-authored proposal: over-cap policy run BEFORE the twin exists (pattern of test_reconciliation_policy.py:2138 `_over_cap_proposal` + `_twin`)
    _set_policy(db_session, {"enabled": True, "actions": {"pair_transfer": {"enabled": True, "max_amount": "1000"}}}); policy_service.run_policy(db_session, p.cstmt.id)
    (prop,) = _proposals(db_session, author="policy", action="pair_transfer"); <add the twin here>
    exc = _refuse(db_session, lambda: proposal_service.apply_proposal(db_session, proposal_id=prop.id, label="owner-test", request_id=None)); assert _code(exc) == "owner_may_record"
    # plain owner action from the case: pairs (R6b owner override)
    row = _apply(db_session, _needs_peer(db_session, p.cstmt), "pair_transfer", {"peer_line_id": p.bline.id}); assert row.status == "applied"

def test_pair_transfer_owner_applying_an_agent_proposal_skips_the_twin_guard(orphan_pair, seed, db_session):
    ... twin (c) present; `proposal_service.create_proposal(..., author="hermes")` → `apply_proposal` by the owner succeeds.

def test_pair_transfer_occupancy_guard_still_binds_the_owner(orphan_pair, db_session):
    ... apply pair, `ews.delete_entry` one leg, reconcile → owner `_apply` on the reopened case → `owner_deleted` (the override covers the twin guard only).

def test_pair_transfer_twin_rule_a_ignores_a_transfer_whose_legs_are_both_covered(…):  # a second, already-claimed transfer between the two families the same week does not block

def test_pair_transfer_dirty_disabled_refusal_for_the_owner(orphan_pair, db_session):
    set_dirty(db_session, False); exc = _refuse(...); assert _code(exc) == "dirty_disabled"

def test_replace_stand_in_refuses_a_transfer_stand_in(orphan_pair, seed, db_session):
    ... apply pair_transfer; owner records an exact transfer later (`_transfer(seed, bank, card, "20000", date(2026,9,5))`); `_reconcile` the card statement → the card line is claimed by the owner's in leg (stand-in drop) and an `orphaned_stand_in` case names the stand-in in leg; `replace_stand_in` on it → `stand_in_changed`.

def test_owner_deleted_pair_blocks_a_second_pairing_on_both_statements(orphan_pair, db_session):
    row = _apply(...); ews.delete_entry(db_session, row.created_entry_ids[0]); db_session.commit()
    # (1) before the originating sweep: the row is still applied → both cases refuse owner_deleted
    for stmt in (orphan_pair.bstmt, orphan_pair.cstmt):
        _reconcile(db_session, stmt.id)   # reopens needs_peer with owner_deleted
        case = _needs_peer(db_session, stmt); assert case.context.get("owner_deleted") is True and case.context["hint"] == "needs_peer"
        exc = _refuse(db_session, lambda: _apply(db_session, case, "pair_transfer", {"peer_line_id": ...})); assert _code(exc) == "owner_deleted"
    # (2) after the sweep retires the row: still blocked
    _sweep(db_session, orphan_pair.cstmt); assert _fresh(db_session, ReconciliationAction, row.id).status == "reverted" and "deleted_by_event" in _fresh(db_session, ReconciliationAction, row.id).inverse
    ... same assertions

def test_engine_revert_of_a_pair_does_not_block_a_later_pairing(orphan_pair, db_session):
    row = _apply(...); _revert(db_session, row.id); _apply(db_session, _needs_peer(db_session, orphan_pair.cstmt), "pair_transfer", {...})   # succeeds
```

In `tests/integration/test_reconciliation_proposals.py`:

```python
def test_pair_transfer_proposal_validates_peer_line_id_against_the_live_candidates(orphan_pair, db_session):
    case = _needs_peer(db_session, orphan_pair.cstmt)
    ok = proposal_service.create_proposal(db_session, case_id=case.id, payload=ProposalIn(action="pair_transfer", params={"peer_line_id": orphan_pair.bline.id}), author="hermes")
    assert ok.status == "pending"
    with pytest.raises(CodedValidationError) as exc:
        proposal_service.create_proposal(db_session, case_id=case.id, payload=ProposalIn(action="pair_transfer", params={"peer_line_id": 999999}), author="hermes")
    assert exc.value.code == "id_out_of_scope"
```

- [ ] **Step 2: Run to verify failure**

Run: `… -m pytest tests/integration/test_reconciliation_actions_transfer.py -k pair_transfer tests/integration/test_reconciliation_proposals.py -k pair_transfer -q -p no:cacheprovider -x`
Expected: FAIL (`allowed` lacks `pair_transfer`).

- [ ] **Step 3: Implement**

`apply`: out/in sides by sign of `line.flow_amount` (negative = out; the case line may be either side); `group = transfer_service.create_transfer(db, TransferIn(from_account_id=out_stmt.account_id, to_account_id=in_stmt.account_id, out_amount=abs(flow), entry_date=out_line.txn_date or out_line.posted_date, posted_date=out_line.posted_date, name="對帳配對轉帳", description=f"對帳配對 {out_stmt.period_end.isoformat()} / {in_stmt.period_end.isoformat()}"), source=STATEMENT_SOURCE, remember=False)`; `out_leg, in_leg = transfer_service.transfer_legs(db, group)`; set `in_leg.entry_date/posted_date` from the in line; `needs_review=True` both; flush; `claim_on` both lines (rule `pair_transfer`); `resolve_peer_case`; `_reconcile_keeping_case`; `effect.created_entry_ids=[out, in]`, `effect.inverse={...}`. `invert`: both legs present, `snapshot_entry` structural keys equal `after` (else `NotRevertable`), `_release`, `ews.delete_entries_cascade(db, both)`, `reopen_peer_case`, `_reopen(ctx.case)`, `_reconcile_keeping_case`; `Effect(deleted_entry_ids=[...])`. Twin guard per Global Constraints, evaluated in `validate_before` after the locks (barrier held). `_desired_cases` stamp and `_validate` special case per Interfaces.

- [ ] **Step 4: Run the files**

Run: `… -m pytest tests/integration/test_reconciliation_actions_transfer.py tests/integration/test_reconciliation_proposals.py tests/integration/test_reconciliation_service.py tests/integration/test_reconciliation_actions_ledger.py -q -p no:cacheprovider`
Expected: PASS (the existing `test_create_entry_is_not_offered_on_a_needs_peer_case` :904 now sees `pair_transfer` in `allowed` — update its expected list).

- [ ] **Step 5: Commit**

```bash
git add app/services/reconciliation_actions/ app/services/proposal_service.py app/services/reconciliation_service.py tests/
git commit -m "feat(reconciliation): pair_transfer — book a transfer from two complementary statement lines"
```

---

### Task 5: Policy — `_pair_transfer_candidate`, caps, owner-deleted and twin suppression

**Files:**
- Modify: `app/services/policy_service.py` (`POLICY_ACTIONS` :287–289, new `_pair_transfer_candidate` beside `_backfill_candidate` :178, `_unchanged` :435–460)
- Test: `tests/integration/test_reconciliation_policy.py`

**Interfaces:**
- Consumes: Task 4 `PairTransferAction.peer_candidates`, `transfer_twin_reason`, `stand_ins.pair_transfer_block`; `settings_service.reconciliation_policy`; `ledger_service._today`.
- Produces: `def _pair_transfer_candidate(db: Session, case: ReconciliationCase, statement: AccountStatement) -> Candidate | None` — `case.kind == "line_unmatched"`, hint `needs_peer`, no `owner_deleted`, `statement.mode == "live"`, `period_end + backfill_grace_days ≤ today`, `backfill_accounts` scoping, exactly one peer candidate, `statement_excluded(db, peer_statement) is None`, `transfer_twin_reason(...) is None`, `pair_transfer_block(db, [case.event_id, peer_line.event_id])` false → `Candidate(case.id, case.kind, case.status, "pair_transfer", {"peer_line_id": peer_line.id}, _money(line.flow_amount), evidence={"peer_statement_id": ..., "peer_line_id": ...})`; `POLICY_ACTIONS["pair_transfer"] = _pair_transfer_candidate`; `_unchanged` branch for `pair_transfer`: the peer line id still the single candidate and uncovered, else False.

- [ ] **Step 1: Write the failing tests** (`TODAY` = 2026-10-12 from the autouse fixture; September period end + 14 days grace ≤ today holds)

```python
def test_pair_transfer_policy_pairs_once_and_the_peer_statement_run_collects_nothing(orphan_pair, db_session):
    _set_policy(db_session, {"enabled": True, "actions": {"pair_transfer": {"enabled": True, "max_amount": "50000"}}})
    first = policy_service.run_policy(db_session, orphan_pair.cstmt.id)
    assert first.applied == 1 and _audit(db_session, "policy_applied")
    second = policy_service.run_policy(db_session, orphan_pair.bstmt.id)
    assert second.applied == 0 and len(_actions_named(db_session, "pair_transfer")) == 1

def test_pair_transfer_over_cap_becomes_a_policy_proposal_with_a_valid_peer_line_id(orphan_pair, db_session):
    _set_policy(db_session, {"enabled": True, "actions": {"pair_transfer": {"enabled": True, "max_amount": "1000"}}})
    policy_service.run_policy(db_session, orphan_pair.cstmt.id)
    (prop,) = _proposals(db_session, action="pair_transfer")
    assert prop.params == {"peer_line_id": orphan_pair.bline.id} and prop.rationale == "policy: max_amount"
    proposal_service.apply_proposal(db_session, proposal_id=prop.id, label="owner-test", request_id=None)   # owner applies; policy-authored → twin guard code owner_may_record would apply if a twin appeared
    assert _cases(db_session, orphan_pair.cstmt.id, "open") == []

def test_pair_transfer_candidate_waits_for_the_grace_and_skips_a_busy_peer(orphan_pair, db_session, today):
    _set_policy(db_session, {"enabled": True, "actions": {"pair_transfer": {"enabled": True, "max_amount": "50000"}}})
    today(date(2026, 10, 5))   # period_end 9/30 + 14 > today
    assert policy_service.collect_candidates(db_session, orphan_pair.cstmt, settings_service.reconciliation_policy(db_session)) == []
    today(date(2026, 10, 20)); orphan_pair.bstmt.needs_recheck = True; db_session.commit()
    assert policy_service.collect_candidates(db_session, orphan_pair.cstmt, settings_service.reconciliation_policy(db_session)) == []

def test_pair_transfer_candidate_skips_an_owner_twin_and_an_owner_deleted_pair(orphan_pair, seed, db_session):
    ... (a) `_transfer(seed, bank, card, "17000", date(2026,9,1))` → no candidate; (b) apply pair, delete one leg, reconcile both, run policy on the bank statement BEFORE sweeping the card statement → `applied == 0` and a `policy_skipped`/no action (the still-applied arm); then `_sweep` the card statement → still no candidate.

def test_pair_transfer_policy_precheck_refuses_a_stale_candidate(orphan_pair, db_session):
    ... build the candidate, then claim the peer line by `match` → `_policy_precheck` → 409 `candidate_changed` (via `apply_candidate` returning None and audit `policy_skipped`, matching the existing stale-candidate test pattern :957 in the ledger tests).

def test_pending_pair_transfer_proposal_is_superseded_when_an_owner_twin_appears(orphan_pair, seed, db_session):
    ... over-cap proposal exists; `_transfer(seed, bank, card, "20000", date(2026,9,5))`; `run_policy` on the card statement → proposal `superseded` (`_supersede_owner_recorded` through OWNER_TWIN_GUARDED).
```

- [ ] **Step 2: Run to verify failure**

Run: `… -m pytest tests/integration/test_reconciliation_policy.py -k pair_transfer -q -p no:cacheprovider -x`
Expected: FAIL (no candidate built).

- [ ] **Step 3: Implement** per Interfaces; `_unchanged` compares `(status, kind, hint, line kind, amount)` and recomputes `peer_candidates` (exactly the stored `peer_line_id`, uncovered) and `pair_transfer_block`.

- [ ] **Step 4: Run the policy suite**

Run: `… -m pytest tests/integration/test_reconciliation_policy.py tests/integration/test_reconciliation_actions_transfer.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/policy_service.py tests/integration/test_reconciliation_policy.py
git commit -m "feat(reconciliation): policy pairs complementary transfer lines under a cap"
```

---

### Task 6: Inbox `transfer` field

**Files:**
- Modify: `app/schemas/inbox.py:67–82` (`InboxItem.transfer`), `app/services/inbox_service.py:316–335` (`_item`)
- Test: `tests/integration/test_inbox_api.py`

**Interfaces:**
- Produces: `class InboxTransferAdjust(BaseModel): entry_id: int; line_id: int`; `class InboxPeerCandidate(BaseModel): line_id: int; statement_id: int; account_name: str; posted_date: date; amount: Decimal`; `class InboxTransfer(BaseModel): adjust_transfer: InboxTransferAdjust | None = None; pair_transfer: list[InboxPeerCandidate] | None = None`; `InboxItem.transfer: InboxTransfer | None = None`. `_item` sets `transfer` when `allowed` contains `adjust_transfer` (entry id = `context.entry_id` or `case.entry_id`, line id = `case.line_id`) or `pair_transfer` (candidates from `PairTransferAction.peer_candidates`); `actions`/`buttons()`/`classify`/`adjustable` unchanged.

- [ ] **Step 1: Write the failing tests**

```python
def test_transfer_delta_item_is_amount_differs_with_the_transfer_field_and_no_accept_button(pair, client):
    item = next(i for i in _items(_inbox(client), "amount_differs") if i["case_id"] == _delta(db, pair.cstmt).id)
    assert item["actions"] == ["match_candidate", "dismiss"] and item["transfer"] == {"adjust_transfer": {"entry_id": pair.in_leg.id, "line_id": pair.cline.id}, "pair_transfer": None}

def test_needs_peer_item_lists_peer_candidates(orphan_pair, client):
    item = next(i for i in _items(_inbox(client), "transfer") if i["case_id"] == _needs_peer(db, orphan_pair.cstmt).id)
    assert item["actions"] == ["match_candidate", "dismiss"]
    assert item["transfer"]["pair_transfer"] == [{"line_id": orphan_pair.bline.id, "statement_id": orphan_pair.bstmt.id, "account_name": "銀行", "posted_date": "2026-09-05", "amount": "-20000.0000"}]

def test_items_without_transfer_actions_carry_a_null_transfer_field(client, ...):  # an ordinary bank_only item → item["transfer"] is None
```

(Existing `test_amount_differs_hides_accept_on_a_transfer_entry` :98 becomes a `transfer_delta` case; keep its assertion and add `item["transfer"]["adjust_transfer"] is not None`.)

- [ ] **Step 2: Run to verify failure** — `… -m pytest tests/integration/test_inbox_api.py -k transfer -q -p no:cacheprovider` → FAIL (`KeyError: 'transfer'`).
- [ ] **Step 3: Implement** per Interfaces (the SPA `InboxItem` TS interface needs no change: extra JSON keys are ignored).
- [ ] **Step 4: Run** `… -m pytest tests/integration/test_inbox_api.py -q -p no:cacheprovider` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(reconciliation): inbox items carry adjust_transfer params and pair_transfer peer candidates"`.

---

### Task 7: Dry-run `--transfers`, docs, spec errata, full suite

**Files:**
- Modify: `scripts/r6_dryrun.py` (`attribute` :33–46, new `transfers_summary(after) -> str`, flag `--transfers`), `scripts/r6_case_dump.py` (`_policy_section` adds `transfer_delta` counts per account via `_count_hint(db, statements, "transfer_delta")`), `scripts/README-r6-dryrun.md` ("Transfers mode (R6c)" section), `README.md` of the service (action table rows `adjust_transfer`, `pair_transfer`; matcher note `transfer_delta`), `docs/superpowers/specs/2026-10-25-reconciliation-r6c-transfers-design.md` (§12 Errata)
- Test: `tests/unit/test_r6_dryrun.py` (exists; add `attribute` + `transfers_summary` tests: `attribute({"kind": "amount_delta", "hint": "transfer_delta"}) == "R6c.1 transfer_delta"`)

- [ ] **Step 1: Write the failing test** for `attribute` and `transfers_summary` (counts-only markdown with per-account `transfer_delta` and `needs_peer` columns; no amounts).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**; README section: dump with `--policy` on the candidate checkout → `r6_dryrun.py --transfers` → `transfers-summary.md` (mode 600, exit 0).
- [ ] **Step 4: Spec §12 Errata** (execution facts the spec got wrong, each one line): `ReconciliationCase.explanation` is an enum — the peer case carries `note`; `Context.action_id` added so the action can stamp `resolved_action_id`; peer-line claims use `coverage_service.write_claim` directly (`_claim` binds `ctx.statement`); `coverage_service.TARGET_ACTIONS` gains `adjust_transfer` (sweep retirement of a stranded amount action); the peer-busy predicate is a local copy (no `policy_service` import from an action module); the `needs_peer` `owner_deleted` stamp keeps hint `needs_peer`.
- [ ] **Step 5: Full suite** — `… -m pytest -q -p no:cacheprovider` (≈14 min; run in the background to a scratchpad file) → 0 failed; record the count against the 56e79cf baseline (run the baseline once in the main checkout if the PR E number cannot be confirmed).
- [ ] **Step 6: Commit** — `git commit -m "docs(reconciliation): R6c dry-run transfers mode, README action table, spec errata"`.

---

## Self-review notes

- Spec coverage: §4 → Task 2; §5.1 → Tasks 3/4 (shared helpers, `Context.action_id`, revert order); §5.2 → Task 3; §5.3 → Task 4 (+ stand-in constants Task 1, twin guard, occupancy, `_validate`); §6 → Task 1; §7 → Task 6; §8 dry-run + README → Task 7; §9 tests distributed; option A provenance (`stand_in_entry_ids` filter) → Task 1; `replace_stand_in` refusal test → Task 4.
- Owner ruling B (spec rev 6) folded: twin guard binds POLICY and policy-authored proposals only; Task 4 `_guard_code` copy + three tests; Task 5's over-cap proposal test keeps the `owner_may_record` comment.
- Type consistency: `peer_candidates` returns `list[tuple[AccountStatement, StatementLine]]` everywhere (Tasks 4, 5, 6); inverse keys identical in Tasks 3/4; `TRANSFER_HINT` string `"transfer_delta"` in Tasks 2/3/6/7.
- Proportion: ~330 lines for a 140-line spec; code blocks are tests and signatures only.
