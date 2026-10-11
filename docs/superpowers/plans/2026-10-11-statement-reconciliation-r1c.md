# Statement reconciliation R1c — cases, actions, proposals, policy, confirm — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fourth stacked backend PR of 對帳: the owner (and, later, policy and the agent) can act on reconciliation cases — match/unmatch, create or adjust ledger rows, attach FX fees, defer, dismiss, split, book an as-of balance adjustment, link an installment plan, revert — through an idempotent, snapshot-checked, audited action engine; agent proposals with supersession; the policy engine shipped off with caps and a monthly budget; statement confirm; manual statements; and the four matching-rule fixes the 2026-10-10 verify run proved necessary (bank `payment`/`purchase` gates, recurring schedule instances, loan installment groups, sibling-account candidates for the 綜合月結單 case).

**Architecture:** A new package `app/services/reconciliation_actions/` holds one module per action family behind a common `Action` protocol (params model, allowed case kinds, effect slot, `prepare` outside locks, `apply` inside the §7.4 lock order, `invert`). `engine.py` owns the single transaction: idempotency replay, import key, optional budget reservation, statement lock(s), the pre-apply sweep, case-version check, D32 row locks, `before` snapshots, the action row (its partial unique index is the duplicate-effect guard), the `app.reconciliation_action_id` GUC, coverage + conservation, `after`/`inverse`, audit, case resolution and recounts. `proposal_service.py`, `policy_service.py` (second transaction after matching) and `confirm_service.py` sit on top; routes live in `app/routers/reconciliation.py` (new router, same feature/scope mechanics as `statements.py`). The pure matcher gains two gates, a `definition_kind` on instances and an `installment_group` representation; `reconciliation_service` adds sibling-account candidates to cases. No Alembic revision: every table and column exists since R1a; new settings are JSON keys.

**Tech Stack:** as R1a/R1b (FastAPI, SQLAlchemy 2.0 `Column` models, Postgres 16, pydantic v2 strict models, pytest with `client`/`db_session`/`seed`, `tests/helpers.race`).

**Spec:** `docs/superpowers/specs/2026-10-08-statement-reconciliation-design.md` v4 — §4.7–4.11, §6 (installment change), §7.1–7.4, §8, §8.1, §12, §16 (C4, C6, C8 acceptance items), §17 R1a/R1b notes; v3 text (commit `6aabdc9`) for "as v3" sections (§4.9 action table, §6.2 gates, §7.2 action table, §7.3 policy, §7.4 lock order, §8 routes); v2 text (commit `eef3b31`) for §4.8 proposals, §7.1 lifecycle, §7.3 exclusions, §7.4 agent proposals. Base: `main` 41d29ad (R2 + #66 merged). Code map used while planning: `.superpowers/plans/facts-reconcile-r1.md` and the R1b code surface (summarised in the Interfaces blocks below — every name there exists at 41d29ad unless a task creates it).

## Global Constraints

- Lock order (§7.4, D32, R1b errata): `import advisory key (shared) → policy_budget row(s) (FOR UPDATE, by month/currency; policy actor only) → account_statement row(s) (FOR UPDATE, ascending id; both statements for move_account) → schedule definition/instances (as D32) → entry_group rows (ascending) → entries with legs (one ordered FOR UPDATE)`. The `account` row lock stays its own class: only `balance_adjustment_asof` takes it, and that action locks no entry or group. Ledger writers never lock statements. Actions take entry/group locks blocking (not NOWAIT) — they run from a request, not the batch.
- Monetary conservation (§4.6): every coverage write goes through `coverage_service.write_claim` and the transaction ends with `coverage_service.assert_conserved(db, statement)`.
- `idempotency_key = sha256("{case_id}|{case_version}|{action}|{canonical params JSON}")` (sorted keys, no spaces, Decimals as strings); a replay returns the stored action row with HTTP 200 and no new effect. Duplicate effects are refused by the partial unique index `ux_reconciliation_action_effect(event_id, action, effect_slot) WHERE status='applied'` → HTTP 422 `duplicate_effect`.
- Snapshot conflict (§4.9): `before` holds every persisted field of each touched entry and group, their complete child/member id sets and incoming dependencies; the compare-and-write happens after the locks are held; any difference → 409 `snapshot_conflict`, no partial effect.
- Every ledger write by an action runs after `SELECT set_config('app.reconciliation_action_id', '<action id>', true)` in the same transaction so the dirty triggers tag the events and the sweep skips them (`coverage_service._applied_action_ids`).
- Services never commit; routers do. Routers call `policy_service.run_policy` only after the matching transaction committed (second transaction, §7.3).
- Policy ships **off**: `settings.reconciliation.policy.enabled=false` and every per-action flag false by default; caps and budget are enforced whenever it is on; `accept_agent_proposals=false` and even when true only `match` on `ambiguous` with one admissible representation confirmed by the validator.
- Money: `Decimal`, 4 dp (`_round_amount`), ledger sign conventions (`entry_write_service.signed`), `display_quantum(currency)` (1 for TWD/JPY, else 0.01). Fee bound (C8): `0 < fee ≤ 0.05 × |principal|` and exact conservation.
- Cases only on `live` statements (unchanged); actions are refused on `historical` statements except `match`/`unmatch` (coverage is kept for historical statements) — ruling below.
- Scopes: `write` for owner actions, dismiss, proposal apply/reject, confirm, manual statements, revert; `propose` for proposal creation; `read` for every GET; `admin` for settings. Hermes (`read,propose`) must get 403 on every other mutation route (the scope matrix test picks new routes up automatically).
- Commits end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`; the PR body ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. No owner financial data in tests or fixtures; synthetic only. No `.env` reads in tests beyond the existing conftest.
- No Alembic revision in this PR. If a task finds it needs a column, stop and rule (the plan is wrong).

## Rulings (plan-level, to be recorded in spec §17 "R1c notes" by Task 12)

1. **Bank gates (verify finding 2).** `BANK_GATES["payment"] = {settlement_out, transfer_out, expense}` and `BANK_GATES["purchase"] = {expense, settlement_out}`. A bank statement prints card auto-pay and loan repayments as `payment` and debit-card spend as `purchase`; the ledger side is a `payable` settlement (`is_settlement` → `settlement_out`), a `transfer_out` or an `expense`. Representation rule stays `payment` for `payment` lines, `exact` for `purchase`.
2. **Recurring instances are standalone (finding 3).** `matching.InstanceRef` gains `definition_kind: str` (`recurring`|`installment`); `_standalone` rejects only installment-definition instances. The spec's "not listed in any `posted_entry_ids`" (§6.2 purchase row) is narrowed to installment instances: a recurring transfer or expense posted by the scheduler is an ordinary entry for matching.
3. **Loan installment groups (finding 4).** A bank `payment` or `withdrawal` line may claim a complete `installment` group (every top-level member unclaimed, on a participating account, statement currency) when `Σ member.flow == line.flow`: representation kind and rule `installment_group`, scored with `_score(line, members, exact_window_days)`. Member kinds are not gate-checked (`payable` + `interest`). Card statements never use it.
4. **Sibling accounts (綜合月結單, owner choice C → R1c).** New admin setting `account_siblings: {"<account_id>": [account_id, …]}` (same currency, not archived, not the account itself). `reconciliation_service` adds `context.sibling_candidates` (≤ 5 `{entry_id, account_id, flow, posted_date}`: unclaimed, |flow| equal, ±`exact_window_days`, standalone) to `line_unmatched` cases of statements whose account has siblings. The `match` action accepts such an entry only with `allow_sibling=true`; coverage snapshots keep the entry's own `account_id`. Siblings are never in the automatic population.
5. **`balance_generation` stays derived** (R1a): `max(coverage_dirty.id)` over rows whose `old_account_id` or `new_account_id` is the account. With `dirty_enabled=false` it never moves, so the action is only meaningful once the feature is live; the engine still runs the compare-and-write.
6. **Action rows are inserted first** (status `applied`) inside the apply transaction so that the partial unique index refuses a duplicate effect before any ledger write and the row id exists for the GUC; a failure rolls the row back with everything else. `balance_adjustment_asof` exhaustion returns 409 with no row at all (§7.2 C4). The `failed` status is written only by the policy engine (Task 10): when an apply raises after the budget reservation, the candidate's transaction is rolled back (reservation included) and a `failed` row is inserted in its own transaction with `before={}` and `after={"error": code}` so the audit shows the attempt; owner actions never leave a `failed` row.
7. **Effect slots** (`effect_slot String(16)`): `match`→`match`, `unmatch`→`unmatch`, `create_entry`/`create_refund`/`create_system_entry`→`create`, `attach_fee`→`fee`, `adjust_amount`→`amount`, `move_account`→`move`, `split_entry`→`split`, `defer_next_period`→`defer`, `balance_adjustment_asof`→`balance`, `link_installment_plan`→`plan`, `dismiss`→`dismiss`, `revert`→`r<reverted action id>`. Entry-keyed cases (no `event_id`) rely on the idempotency key only (NULLs are distinct in the index).
8. **Historical statements:** `match`, `unmatch` and `revert` of those two are allowed (they are coverage only); every other action is refused with 422 `historical_statement`. Cases do not exist there, so the owner targets the line through `POST /reconciliation/statements/{sid}/lines/{line_id}/actions` (same engine, synthetic case-less context) — **not in R1c**; R1c exposes actions through cases only and historical statements stay read-only. Recorded so R3 can ask.
9. **`create_refund` source:** `settlement_service.refund` gains `source: str = "manual"` and the action passes `"statement"`; everything else about refunds stays the settlement service's rule set.
10. **Policy month** = the calendar month of the application day (`date.today()` under the `today` fixture), not the statement period; one `policy_budget` row per `(month, currency)`, inserted `ON CONFLICT DO NOTHING` then locked `FOR UPDATE` before the statement lock.
11. **Confirm and deferrals:** confirm first resolves this statement's incoming deferral links (earlier statements' `deferred_next_period` cases pointing here): a deferred entry claimed here → the earlier case stays resolved and gets `context.resolved_in_statement_id`; still unclaimed → the earlier case reopens (`open`, `version+1`, `explanation=NULL`, `context.reopened_by_confirm_of=<sid>`), the earlier statement's counts are recomputed, and this statement's confirm proceeds (the reopen never invalidates this confirmation).
12. **`move_account` target statement:** the target account's statement whose `[period_start, period_end]` contains the entry's `posted_date` (same currency), if any; both statements are locked ascending by id and both are re-reconciled with `locked=True` inside the apply transaction. Entries with `group_id`, `parent_entry_id`, `transfer_group_id` or an installment instance are refused (422 `not_movable`).
13. **Case DTO** grows to the full row (`statement_id, revision_id, resolved_by, resolved_action_id, resolved_at, deferred_to_period_end, deferred_to_statement_id, note, pending_proposals`) and `actions_allowed` is computed by the registry from the case kind, status and context; R1b's `CaseOut` fields keep their names.
14. **Reverting a `match`/`unmatch`** needs the rows unchanged versus `after` and the entry still unclaimed/claimed as expected; reverting a creation deletes through `delete_entries_cascade` only when the created rows equal `after`; reverting `split_entry` restores the anchor exactly and deletes the other members only when every member/child equals `after` (spec); reverting `link_installment_plan` deletes the map row and re-reconciles; `revert` of a `revert` is refused (422 `not_revertable`); `balance_adjustment_asof` revert deletes the adjustment when unchanged.

## Review Focus

1. Two concurrent `apply` calls for the same case and params must produce one effect: the second either replays (same idempotency key) or fails with `duplicate_effect`/`case_moved`, never a second ledger row — Task 4 (race test with `tests/helpers.race`).
2. A ledger edit committed between `prepare` and the apply locks (amount changed on the entry an `adjust_amount` targets) must yield 409 `snapshot_conflict` with no row changed — Task 6.
3. `attach_fee` with a residual that passes the quantum tolerance but breaks exact conservation (e.g. residual 1.5 TWD on a TWD card) must fail with no child written — Task 6 (C8 fixtures: zero, negative, > 5 %, quantum-violating).
4. Policy must never exceed a cap under concurrency: two statements applying `create_system_entry` at the monthly cap in parallel → exactly one applies, the other becomes a proposal — Task 10 (race on the budget row).
5. Confirm on a statement with a pending agent proposal, an open case, `needs_recheck`, `conflict_open` or a failed guardrail must refuse with the specific code; `force` without a note is 422; a forced confirm is audited with the note — Task 11.

---

### Task 1: Branch, baseline, facts file

**Files:**
- Create: `.superpowers/plans/facts-reconcile-r1c.md` (short: branch, base, test counts, the code-map pointers used by later tasks)

- [ ] `git -C /home/opc/workspace/home-hub-reconcile rev-parse --abbrev-ref HEAD` → `feat/reconciliation-r1c` at `41d29ad` (created from `origin/main`, never from a local `main`).
- [ ] `cd services/accounting-service && .venv/bin/pytest -q -p no:cacheprovider` → expected `1637 passed` (worker suite included). Record the count and runtime in the facts file.
- [ ] Commit: `chore(reconciliation): R1c facts file and baseline`.

---

### Task 2: Matching-rule fixes (pure)

**Files:**
- Modify: `app/services/statements/matching.py` (`InstanceRef`, `BANK_GATES`, `_standalone`, `_representations`), `app/services/reconciliation_service.py::_instances` (load `ScheduleDefinition.kind`)
- Test: `tests/unit/test_statement_matching.py`, `tests/integration/test_reconciliation_service.py`

**Interfaces:**
- Consumes: `matching.Entry/Group/Line/Rules/Row/Representation`, `_score`, `conserved`, `eligible_kinds`, `_entry_class`; `ScheduleDefinition.kind ∈ ("recurring","installment")`.
- Produces: `InstanceRef(definition_id: int, seq: int, times: int | None, definition_kind: str = "installment")` (default keeps every existing caller and test valid); `BANK_GATES` with `payment` and `purchase`; representation kind/rule `"installment_group"`; `_standalone(entry)` as ruling 2.

- [ ] **Step 1: Failing unit tests** (`tests/unit/test_statement_matching.py`), using the file's helpers `L(i, posted, flow, kind, merchant, **kw)`, `E(i, posted, flow, kind, merchant, **kw)` (kwargs `group_id`, `is_settlement`, `instance`, `parent_entry_id`, …) and `run(lines, entries, groups, plan_map, fee, kind)`; `E` needs one new kwarg `claimable=kw.get("claimable", True)` passed through to `m.Entry`:

```python
SEP3 = date(2026, 9, 3)
INST = lambda kind, did=8: m.InstanceRef(definition_id=did, seq=3, times=12 if kind == "installment" else None, definition_kind=kind)

def test_bank_payment_line_claims_settlement_out():
    res = run([L(1, SEP3, "-5000", kind="payment")], [E(10, SEP3, "-5000", kind="payable", is_settlement=True)], kind="bank")
    assert [c.representation.rule for c in res.claims] == ["payment"]

def test_bank_purchase_line_claims_expense():
    res = run([L(1, SEP3, "-120", kind="purchase")], [E(10, SEP3, "-120", kind="expense")], kind="bank")
    assert [r.entry_id for r in res.claims[0].representation.rows] == [10]

def test_recurring_instance_is_standalone_but_installment_instance_is_not():
    line = L(1, SEP3, "3000", kind="transfer_in")
    assert run([line], [E(10, SEP3, "3000", kind="transfer_in", instance=INST("recurring"))], kind="bank").claims
    assert not run([line], [E(11, SEP3, "3000", kind="transfer_in", instance=INST("installment"))], kind="bank").claims

def _loan(principal_flow="-10000", interest_flow="-500", **kw):
    g = m.Group(id=99, kind="installment", member_ids=(10, 11))
    entries = [E(10, SEP3, principal_flow, kind="payable", group_id=99, is_settlement=True, instance=INST("installment")),
               E(11, SEP3, interest_flow, kind="interest", group_id=99, instance=INST("installment"), **kw)]
    return entries, {99: g}

def test_bank_payment_line_claims_a_complete_installment_group():
    entries, groups = _loan()
    res = run([L(1, SEP3, "-10500", kind="payment")], entries, groups=groups, kind="bank")
    assert res.claims and res.claims[0].representation.rule == "installment_group"
    assert sorted(r.entry_id for r in res.claims[0].representation.rows) == [10, 11]

def test_partial_installment_group_is_not_admissible():
    entries, groups = _loan(claimable=False)   # the interest member is claimed elsewhere
    res = run([L(1, SEP3, "-10500", kind="payment")], entries, groups=groups, kind="bank")
    assert not res.claims and res.cases[0].kind == "line_unmatched"

def test_card_purchase_never_uses_installment_group():
    entries, groups = _loan()
    assert not run([L(1, SEP3, "-10500", kind="purchase")], entries, groups=groups, kind="card").claims

def test_installment_group_sum_mismatch_is_not_admissible():
    entries, groups = _loan()
    res = run([L(1, SEP3, "-10000", kind="payment")], entries, groups=groups, kind="bank")
    assert not res.claims   # the group sums to -10500; a lone member is never admissible either
```

- [ ] **Step 2:** run → FAIL (`definition_kind` unexpected kwarg; no claims).
- [ ] **Step 3: Implement.**
  - `InstanceRef`: add `definition_kind: str = "installment"`.
  - `BANK_GATES`: add `"payment": frozenset({"settlement_out", "transfer_out", "expense"})`, `"purchase": frozenset({"expense", "settlement_out"})`.
  - `_standalone`: `return entry.group_id is None and entry.parent_entry_id is None and (entry.instance is None or entry.instance.definition_kind != "installment")`.
  - `_representations`: after the split-group block add, guarded by `kind == "bank" and line.line_kind in ("payment", "withdrawal")`: for each `g` with `g.kind == "installment"`, members = `[by_id.get(i) for i in g.member_ids]`; skip if any member is `None`, claimed, or `not member.claimable`; rows = `tuple(Row(m.id, "member", m.flow, g.id) for m in members)`; if `conserved(rows, line.flow)`: `score, reasons = _score(line, tuple(members), rules.exact_window_days)`; if `score > ZERO`: `out.append(Representation("installment_group", rows, score, "installment_group", reasons))`.
  - `reconciliation_service._instances`: select `ScheduleDefinition.kind` too and pass `definition_kind=row.kind`.
- [ ] **Step 4:** unit + `tests/integration/test_reconciliation_service.py` green; add one integration test: a bank statement with a `payment` line equal to a scheduled loan group (seed `definition(kind="installment")` + `instance` posting `payable` + `interest` members in an `installment` group) → one claim with rule `installment_group`; and a recurring `transfer_in` instance claimed by a `transfer_in` line.
- [ ] **Step 5:** Commit `fix(accounting): bank payment/purchase gates, recurring instances standalone, loan installment groups`.

---

### Task 3: Settings — policy, caps, siblings

**Files:**
- Modify: `app/services/settings_service.py`, `app/schemas/statements.py` (`ReconciliationSettingsIn/Out`), `app/services/reconciliation_service.py` (sibling candidates in `_desired_cases`)
- Test: `tests/integration/test_statement_ingest_api.py` (settings round trip), `tests/integration/test_reconciliation_service.py` (sibling candidates)

**Interfaces:**
- Produces in `settings_service`: `POLICY_DEFAULTS = {"enabled": False, "accept_agent_proposals": False, "per_statement_count": 5, "per_statement_total": "5000", "monthly_total": "20000", "actions": {"create_system_entry": {"enabled": False, "max_amount": "2000"}, "attach_fee": {"enabled": False, "max_amount": "500"}}, "per_currency_enabled": ["TWD"]}` (`per_currency_enabled`: list of ISO codes policy may touch; statements in other currencies are skipped with audit `policy_skipped currency`); `_check_policy(policy: dict) -> dict` (unknown keys refused; booleans; non-negative Decimal strings; ints 0..1000; only the two action names); `_check_siblings(db, siblings: dict) -> dict[str, list[int]]` (keys int-strings of existing accounts; values distinct existing, non-archived accounts of the same currency, not the key); `reconciliation_policy(db) -> dict` (defaults ⊕ stored, SELECT only); `account_siblings(db, account_id) -> list[int]`; `policy_version` = `f"r1c-{version}"` bumped when the effective policy changes (same pattern as `rules_version`).
- `ReconciliationSettingsIn` gains `policy: dict = {}` and `account_siblings: dict[str, list[Int32]] = {}`; `ReconciliationSettingsOut` gains `policy`, `policy_version`, `account_siblings`.
- `reconciliation_service._desired_cases`: for `line_unmatched` cases when `account_siblings(db, statement.account_id)` is non-empty, add `context["sibling_candidates"]` = up to 5 dicts `{entry_id, account_id, flow: str, posted_date: iso}` from `ledger_entry` rows on sibling accounts, `currency == statement.currency`, `parent_entry_id IS NULL`, `group_id IS NULL`, `abs(amount) == abs(line.flow)`, `|posted_date − line.posted_date| ≤ rules.exact_window_days`, not in `claimed_entry_ids`. Ordered by date distance then id. (`_key` already hashes context, so a changed candidate list refreshes the case.)

- [ ] **Step 1: Tests:** PUT with `policy.enabled=true` and an unknown action name → 422; `max_amount="-1"` → 422; siblings with an archived account / other currency / self → 422; GET returns defaults when absent; `policy_version` bumps only when policy changed; a `line_unmatched` case on an account with a sibling holding an equal-amount entry two days away lists it in `context.sibling_candidates`, an entry on a non-sibling account is not listed, a claimed entry is not listed.
- [ ] **Step 2–4:** RED → implement → GREEN.
- [ ] **Step 5:** Commit `feat(accounting): policy and sibling-account settings; sibling candidates on unmatched lines`.

---

### Task 4: Action engine — framework, snapshots, audit, `match`/`unmatch`

**Files:**
- Create: `app/services/reconciliation_actions/__init__.py` (registry), `base.py`, `snapshots.py`, `audit.py`, `engine.py`, `coverage_actions.py` (`match`, `unmatch`)
- Create: `app/schemas/reconciliation.py` (`ActionIn`, `ActionOut`, `AuditOut`), `tests/integration/statement_fixtures.py` (shared `_submit/_card/_line/_transfer` helpers moved out of `test_reconciliation_service.py` and `test_coverage_sweep.py`, which then import them)
- Test: `tests/integration/test_reconciliation_actions_engine.py`

**Interfaces (binding for Tasks 5–11):**

```python
# base.py
class ActionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

@dataclass
class Context:
    db: Session
    case: ReconciliationCase            # locked statement, re-read after locks
    statement: AccountStatement
    account: Account
    line: StatementLine | None          # case.line_id's row (current revision) or None
    actor: str                          # "owner" | "policy"
    label: str                          # token label or "policy"
    run_id: int | None
    request_id: str | None
    rules: matching.Rules

@dataclass
class Effect:
    created_entry_ids: list[int] = field(default_factory=list)
    deleted_entry_ids: list[int] = field(default_factory=list)
    touched_entry_ids: list[int] = field(default_factory=list)
    touched_group_ids: list[int] = field(default_factory=list)
    before: dict = field(default_factory=dict)   # {"entries": {id: snap}, "groups": {id: snap}, "case": {...}}
    after: dict = field(default_factory=dict)
    inverse: dict = field(default_factory=dict)  # operation-specific, see each action
    coverage_ids: list[int] = field(default_factory=list)
    resolves_case: bool = True
    case_status: str = "resolved"       # or "dismissed"
    explanation: str | None = None

class Action(Protocol):
    name: str
    Params: type[ActionParams]
    case_kinds: frozenset[str]                     # kinds the action is allowed on
    actors: frozenset[str] = frozenset({"owner"})  # policy-eligible actions add "policy"
    def effect_slot(self, ctx: Context, params) -> str: ...
    def validate_before(self, ctx: Context, params, prepared, before: dict) -> None: ...  # id scoping, protected rules; default no-op in ActionBase
    def lock_targets(self, ctx: Context, params) -> LockSet: ...   # entry ids, group ids, extra statement ids, account lock?
    def prepare(self, ctx: Context, params) -> Any: ...            # no locks held; FX fetch, reference checks
    def apply(self, ctx: Context, params, prepared, effect: Effect) -> None: ...  # locks held; GUC set
    def invert(self, ctx: Context, action: ReconciliationAction) -> Effect: ...   # locks held; raises NotRevertable

@dataclass(frozen=True)
class LockSet:
    entry_ids: tuple[int, ...] = ()
    group_ids: tuple[int, ...] = ()
    statement_ids: tuple[int, ...] = ()     # extra statements (move_account target)
    account_lock: bool = False              # balance_adjustment_asof only
    budget: tuple[date, str, Decimal] | None = None   # policy only: (month, currency, amount)
```

```python
# snapshots.py
def entry_snapshot(db, entry: LedgerEntry) -> dict
    # every column of LedgerEntry (Decimal → str, dates → iso), plus
    # "child_ids": sorted ids with parent_entry_id == entry.id,
    # "refund_ids": entries with refunds_entry_id == entry.id, "settlement_ids": settles_entry_id == entry.id,
    # "reward_ids": reward_source_entry_id == entry.id, "instance_id": posted schedule instance id or None,
    # "coverage_ids": active coverage row ids referencing the entry
def group_snapshot(db, group: EntryGroup) -> dict   # columns + "member_ids" sorted
def diff(before: dict, current: dict) -> list[str]  # dotted paths that differ, [] when equal
def snapshot_set(db, entry_ids, group_ids) -> dict  # {"entries": {...}, "groups": {...}}
```

```python
# audit.py
def record(db, *, event: str, actor: str, statement_id=None, case_id=None, action_id=None, run_id=None,
           request_id=None, detail: dict | None = None) -> ReconciliationAudit
# events used in R1c: action_applied, action_replayed, action_reverted, case_dismissed, proposal_created,
# proposal_applied, proposal_rejected, proposal_superseded, policy_applied, policy_proposed, policy_skipped,
# statement_confirmed, statement_confirmed_forced, deferral_reopened, manual_statement_created
```

```python
# engine.py
class NotRevertable(CodedConflictError)  # code "not_revertable" → 409? No: 422 via ValidationError("action_id", code)
def apply_action(db, *, case_id: int, action: str, params: dict, actor: str, label: str,
                 proposal_id: int | None = None, run_id: int | None = None, request_id: str | None = None,
                 budget: tuple[date, str, Decimal] | None = None) -> tuple[ReconciliationAction, bool]
    # returns (row, replayed). Steps:
    # 0 registry lookup → ValidationError("action", "unknown action") ; params = Action.Params(**params) (422 on error)
    # 1 case = SELECT … FOR SHARE? No: load without lock; statement_id from it; compute idempotency key from
    #   case.version; existing = SELECT action WHERE idempotency_key → return (existing, True) + audit action_replayed
    # 2 prepared = action.prepare(ctx0, params)  (ctx0 with an unlocked case; may call get_rate → commits)
    # 3 attempt loop (max 3 only when the action declares retry_on_generation; else once):
    #   take_import_key_shared(db)
    #   if budget: _reserve_budget(db, *budget)  (INSERT … ON CONFLICT DO NOTHING; SELECT … FOR UPDATE; cap check by caller)
    #   lock statements ascending (case.statement_id ∪ lock_targets.statement_ids) via reconciliation_service._lock_statement
    #   coverage_service.sweep(db, statement, run_id=run_id, barrier_wait=True) for each locked statement
    #   re-read case FOR UPDATE; if case.version != key_version → CodedConflictError("case_moved")
    #   if case.status not in ("open","proposed") → CodedConflictError("case_closed")
    #   if action.name not in registry.allowed(case) → ValidationError("action", "not allowed for this case")
    #   if statement.mode == "historical" and action.name not in ("match","unmatch") → ValidationError("action","historical_statement")
    #   lock groups ascending (lock_group) then entries (locked_with_legs over entry_ids, one ordered SELECT); account lock if asked
    #   before = snapshots.snapshot_set(...); action.validate_before(ctx, params, prepared, before) (ids scoping, protected rules)
    #   row = ReconciliationAction(idempotency_key, event_id=case.event_id, effect_slot, case_id, proposal_id, action, params(json), actor, run_id, status="applied", before=before)
    #   db.add(row); flush inside a savepoint → IntegrityError on ux_reconciliation_action_effect → ValidationError("action","duplicate_effect")
    #   db.execute(text("SELECT set_config('app.reconciliation_action_id', :id, true)"), {"id": str(row.id)})
    #   effect = Effect(before=before); action.apply(ctx, params, prepared, effect)
    #   coverage_service.assert_conserved(db, statement) (and for the extra statement)
    #   effect.after = snapshots.snapshot_set(db, touched ∪ created, groups); row.after/inverse/ids ← effect
    #   case: status=effect.case_status, explanation, resolved_by=actor, resolved_action_id=row.id, resolved_at=now, note (dismiss)
    #   proposals: other pending proposals on the case → superseded (audit proposal_superseded)
    #   audit action_applied {action, params, effect ids}
    #   reconciliation_service._recount(db, statement) for each locked statement
    #   return (row, False)
    # GenerationMoved (from balance_adjustment_asof) → db.rollback() to the attempt's savepoint … simpler: the route
    # catches GenerationMoved, rolls back the session and calls apply_action again (attempt+1 ≤ 3), then 409 "balance_generation_moved".
def revert_action(db, *, action_id: int, actor: str, label: str, request_id=None) -> ReconciliationAction
    # loads the applied, non-reverted action (else 404/422 not_revertable); case & statement; same lock steps;
    # refuses when a later applied action's touched/created ids intersect this one's (422 not_revertable: "later action N");
    # current = snapshot_set(after ids); diff(after, current) must be [] (409 snapshot_conflict);
    # new row action="revert", effect_slot=f"r{action_id}", event_id=original.event_id, case_id=original.case_id;
    # set GUC; effect = action.invert(ctx, original); release the coverage rows in original.after["coverage_ids"] (status stale, reason "revert")
    # original.status="reverted", original.reverted_by_action_id=new.id; case → open, version+1, resolved_* cleared,
    # context["reverted_action_id"]=action_id; recount; audit action_reverted.
```

- Registry (`__init__.py`): `ACTIONS: dict[str, Action]` filled by Tasks 4–8; `allowed(case: ReconciliationCase) -> list[str]`: by kind — `line_unmatched`: match, create_entry, create_system_entry (only with `context.hint == "bank_only"`), create_refund (card refund lines), link_installment_plan (hint `installment_unmapped`), dismiss; `ambiguous`: match, dismiss; `amount_delta`: match (owner may pick a candidate), adjust_amount, attach_fee (when `context.fee_matches`), dismiss; `entry_unmatched`: defer_next_period, move_account (hint `move_account` or any), unmatch? no, dismiss; `balance_gap`: balance_adjustment_asof, dismiss; `duplicate_claim`: unmatch, dismiss; `recheck`: match, unmatch, dismiss; `parse_review`/`statement_conflict`: dismiss only (they resolve by revision). Status must be `open`/`proposed`.
- `coverage_actions.MatchAction`: `Params(line_id: int, entry_id: int | None = None, group_id: int | None = None, with_children: bool = False, allow_sibling: bool = False)` (exactly one of entry/group); `effect_slot="match"`; validation: `line_id == case.line_id` (or, for `recheck`/`ambiguous`, a line of the current revision whose event is the case's), line unclaimed; entry: on `participating_accounts ∪ (account_siblings if allow_sibling)`, statement currency, `parent_entry_id IS NULL`, unclaimed (`claimed_entry_ids`), not a member unless `group_id` given; group: complete, every member unclaimed; rows = principal (+ children when `with_children`) or members; `conserved(rows, line.flow_amount)` else 422 `not_conserved`; apply: `coverage_service.write_claim(db, statement, line, rows, entries_by_id, match_kind="manual", match_rule="owner", run_id)`; `effect.inverse = {"release_line": line_id}`; `invert`: `release_line(reason="revert")`.
- `UnmatchAction`: `Params(line_id: int)`; rows = `active_rows(db, statement_id)[line_id]` must exist (422 `not_claimed`); apply: `release_line(db, statement, line_id, reason="owner")`; `inverse = {"rows": [row dicts], "match_rule": …}`; `invert`: re-claim via `write_claim` if the entries' snapshots equal `after` and they are unclaimed, else `NotRevertable`. The case for an unmatch is the `recheck`/`duplicate_claim` case; after an unmatch the engine also opens a `line_unmatched` case for the line? No — the next reconcile does; the engine sets `statement.needs_recheck=True` so the batch re-runs it.
- Schemas: `ActionIn(action: str, params: dict = {})`; `ActionOut(id, case_id, proposal_id, action, params, actor, status, effect_slot, created_entry_ids, deleted_entry_ids, touched_entry_ids, touched_group_ids, reverted_by_action_id, created_at, replayed: bool)`.

- [ ] **Step 1: Tests** (integration, with the R1b `_submit`/`_card` helpers copied into a shared `tests/integration/statement_fixtures.py` so Tasks 5–11 reuse them; move, do not duplicate — update the two R1b files to import from it):
  1. `match` on a `line_unmatched` case claims the entry (coverage row `match_kind='manual'`, `match_rule='owner'`), resolves the case (`resolved_by='owner'`, `resolved_action_id`), recounts `matched_count`, writes an `action_applied` audit row, and the dirty event of nothing (no ledger write) — assert no `coverage_dirty` row.
  2. Replay with identical params → same action id, `replayed=True`, one coverage row, one audit `action_replayed`.
  3. Same effect via a new case version (case superseded/reopened, then `match` again with the same `line`) → 422 `duplicate_effect` because the first action is still applied on that event/slot.
  4. `match` on an entry of another account (not sibling) → 422; with `allow_sibling=true` and the account configured as sibling → claim, snapshot keeps the sibling's `account_id`.
  5. `match` whose rows do not conserve → 422 `not_conserved`, no coverage.
  6. Concurrency (Review Focus 1): `race(engine, first=apply match, second=apply match same params)` → one coverage row; the loser returns the same action (replay) or `case_moved`.
  7. Case version moved between prepare and lock (simulate by bumping `case.version` in a second session before the first takes the statement lock — use `wait_until_blocked`) → 409 `case_moved`.
  8. `unmatch` on a `recheck` case releases the row, `needs_recheck=True`; `revert` of that unmatch re-claims when unchanged; after an edit of the entry amount `revert` → 409 `snapshot_conflict`.
  9. `revert` of a `match` releases the coverage, reopens the case (`open`, version+1), marks the original `reverted`; a second `revert` → 422 `not_revertable`.
  10. Historical statement: `match` allowed through a synthetic case? Cases do not exist on historical statements → skip (ruling 8) — assert that `apply_action` on a case whose statement is historical refuses every action but match/unmatch (construct the case row directly).
  11. Audit rows carry `request_id` when given.
- [ ] **Step 2–4:** RED → implement → GREEN.
- [ ] **Step 5:** Commit `feat(accounting): reconciliation action engine — idempotency, effect slots, snapshots, audit, match/unmatch, revert`.

---

### Task 5: Case actions without ledger writes — `dismiss`, `defer_next_period`, `link_installment_plan`

**Files:**
- Create: `app/services/reconciliation_actions/case_actions.py`
- Modify: registry, `app/services/reconciliation_service.py::_write_deferrals` (owner-resolved deferrals survive)
- Test: `tests/integration/test_reconciliation_actions_case.py`

**Interfaces:**
- `DismissAction`: `Params(note: str = Field(min_length=1, max_length=1000))`; allowed on every kind in `open|proposed`; slot `dismiss`; apply: nothing ledger-side; `effect.case_status="dismissed"`, `effect.explanation="accepted_exception"`, `case.note=note`; `invert`: reopen (`open`, explanation NULL, note kept in `context.dismiss_note`).
- `DeferNextPeriodAction`: `Params(entry_id: int)` must equal `case.entry_id` on an `entry_unmatched` case; apply: `case.explanation="deferred_next_period"`, `deferred_to_period_end = reconciliation_service.next_period_end(statement.period_end, account.closing_day if account.is_credit else None)`, `deferred_to_statement_id` = existing statement with that `(account_id, currency, period_end)` or NULL; `effect.case_status="resolved"`; `invert`: clear the three fields, reopen. The reverse population of the next reconcile keeps the explanation (R1b `_write_deferrals` re-derives from the matcher; a manual deferral must survive: `_write_deferrals` must treat an existing resolved case with `resolved_by='owner'` as authoritative — modify `_write_deferrals` to skip entries whose case is owner-resolved).
- `LinkInstallmentPlanAction`: `Params(definition_id: int)`; allowed on `line_unmatched` with `context.hint == "installment_unmapped"`; `definition_id` must be in `case.candidates[*].definition_id` (id scoping, 422 otherwise) and be an active `installment` definition; apply: insert `InstallmentPlanMap(account_id=statement.account_id, plan_key=case.context["plan_key"], definition_id, confirmed_by="owner")` (unique → 409 `plan_already_mapped`), then `reconciliation_service.reconcile(db, statement.id, locked=True)` so the line is claimed in the same transaction; `effect.inverse={"plan_map_id": id}`; `invert`: delete the map row (only when no applied action other than this one references the plan — check no later action touched the instance entries), then re-reconcile.
- Registry `allowed` updated per Task 4's table.

- [ ] **Step 1: Tests:** dismiss resolves with note and counts in `explained_count`; a dismissed case is not recreated by the next reconcile (`_dismissed`); revert of a dismiss reopens; defer on a non-`entry_unmatched` case → 422; defer sets `deferred_to_period_end` from `closing_day` (card) and month end (bank); the next statement's reconcile fills `deferred_to_statement_id` (R1b `_link_earlier_deferrals`) and the manual deferral survives a re-reconcile of the earlier statement; link plan with a definition outside the candidates → 422; with a candidate → map row + claim with rule `installment` in the same call; revert removes the map and the claim.
- [ ] **Step 2–4:** RED → implement → GREEN.
- [ ] **Step 5:** Commit `feat(accounting): dismiss, defer_next_period and link_installment_plan actions`.

---

### Task 6: Ledger-writing actions — `create_entry`, `create_system_entry`, `create_refund`, `attach_fee`, `adjust_amount`

**Files:**
- Create: `app/services/reconciliation_actions/ledger_actions.py`
- Modify: `app/services/settlement_service.py::refund` (add `source: str = "manual"`), registry
- Test: `tests/integration/test_reconciliation_actions_ledger.py`

**Interfaces:**
- Common: every created entry has `source="statement"`, `account_id=statement.account_id`, `currency=statement.currency`, `entry_date = line.txn_date or line.posted_date`, `posted_date = line.posted_date`, `needs_review = True` unless the owner supplied the category (`create_entry` with `category_id`), `description` prefixed `對帳建立 <statement period_end>`; after the write the action claims the line on the new row (`write_claim`, `match_kind="manual"`, `match_rule=<action name>`; policy uses `match_kind="auto"`). Amount = `line.flow_amount` (already the ledger sign); for `create_entry` the kind must agree with the sign (`expense` ← negative, `income` ← positive, else 422 `kind_sign`).
- `CreateEntryAction`: `Params(kind: Literal["expense","income"], category_id: int, name: str | None = None, project_id: int | None = None)`; prepare: category exists and matches kind (`Category.kind`), project exists; apply: `LedgerEntry(...)`, flush, claim; `inverse={"created_entry_id": id}`; `invert`: `delete_entries_cascade(db, [id])` when the snapshot equals `after` (no children added, no refunds/settlements referencing it).
- `CreateSystemEntryAction`: `Params(kind: Literal["fee","interest","reward"])`; allowed on `line_unmatched` with `context.hint == "bank_only"` (or any `line_unmatched` whose `line_kind ∈ {fee, interest, reward}`); category `system_category_id(db, kind)`; `actors = {"owner","policy"}`; otherwise as `create_entry`.
- `CreateRefundAction`: `Params(original_entry_id: int)`; allowed on `line_unmatched` of a card `refund` line (and bank `refund`); original must be on a participating account, kind `expense`, no parent, `refunded_amount(original) + |flow| ≤ |original.amount|` (422 `refund_exceeds`), same currency; apply: `settlement_service.refund(db, original.id, RefundIn(account_id=statement.account_id, amount=abs(flow), entry_date=line.posted_date), source="statement")` → claim; invert: delete when unchanged.
- `AttachFeeAction` (C8): `Params(entry_id: int, fee_line_id: int | None = None)`; `actors={"owner","policy"}`; allowed on `amount_delta` with `context.fee_matches is True` (bundled) and on `line_unmatched` fee lines (separate, `fee_line_id` = that line, the purchase line is `context.entry_id`'s claimed line); bundled: purchase line = the case's line, `residual = entry.flow − line.flow` (both negative for a card purchase; the child amount is `-|residual|`); separate: `fee = fee_line.flow_amount`; checks: `0 < |fee| ≤ Decimal("0.05") * abs(entry.amount)` (422 `fee_out_of_bounds`), `|fee|` quantised to `display_quantum(currency)` equals itself (422 `fee_quantum`), `fee == proposed_fx_fee(account, entry.amount)` within one quantum (422 `fee_unexpected`), no existing child named `FX_FEE_CHILD_NAME` (409 `fee_exists`), `entry.original_currency` set; apply: `write_children(db, entry, fee=ChildIn(amount=abs(fee), name=FX_FEE_CHILD_NAME), discount=None, source="statement")`; child id = the new row with `parent_entry_id == entry.id`; coverage: bundled → `write_claim(rows=[Row(entry.id,"principal",entry.flow), Row(child.id,"child",child.flow)])` on the purchase line (conservation exact or the engine's `assert_conserved` fails → the C8 "quantum-tolerant but not exact" case); separate → `Row(child.id,"principal",child.flow)` on the fee line; `inverse={"child_id": id}`; invert: delete the child when unchanged.
- `AdjustAmountAction`: `Params(entry_id: int, line_id: int)`; allowed on `amount_delta` (entry from `candidates`); entry editable (`assert_entry_editable`, `protected_reason` None else 409 `protected`), standalone (no group/parent/instance), no settlements/refunds (`has_settlements_or_refunds` → 409); apply: `new_amount = signed(entry.kind, abs(line.flow_amount))` (for kinds outside `SIGN_BY_KIND` use the sign of `line.flow_amount`); if `line.foreign_amount`: `original_amount = sign(new_amount) * abs(line.foreign_amount)`, `original_currency = line.foreign_currency`, `fx_rate = (abs(new_amount) / abs(original_amount)).quantize(RATE_QUANTUM)` (422 when original is zero), `fx_source="statement"`; `description = f"{old description}\n對帳調整 原金額 {old amount}"`; date unchanged; claim the line on the entry; `inverse={"restore": {amount, original_amount, original_currency, fx_rate, fx_source, description}}`; invert: restore when the entry equals `after`.

- [ ] **Step 1: Tests:** create_entry with a category of the wrong kind → 422; success creates the row with `source='statement'`, `needs_review=False` (owner category), claims the line, `coverage_dirty` carries `action_id` = the action id (dirty_on fixture) and the next sweep skips it (no `recheck` case); create_system_entry on a non-bank-only case → 422; refund over the remaining → 422; attach_fee: zero, negative, 5.5 %, quantum-violating (TWD residual 1.5) → 422 and no child; a fee of exactly `proposed_fx_fee` → child `國外交易手續費`, coverage principal+child, case resolved; a second attach on the same entry → 409 `fee_exists`; adjust_amount on a protected entry → 409; success rewrites amount and `fx_rate` from the pair and appends the description note; Review Focus 2: amount edited by a second session between prepare and lock → 409 `snapshot_conflict`, amount unchanged; each action's revert restores/deletes when unchanged and refuses after a later edit.
- [ ] **Step 2–4:** RED → implement → GREEN.
- [ ] **Step 5:** Commit `feat(accounting): create/refund/system-entry, attach_fee and adjust_amount actions`.

---

### Task 7: `move_account`, `split_entry`, `balance_adjustment_asof`

**Files:**
- Create: `app/services/reconciliation_actions/structural_actions.py`
- Modify: registry; `engine.py` (`GenerationMoved` retry loop, account lock)
- Test: `tests/integration/test_reconciliation_actions_structural.py`

**Interfaces:**
- `MoveAccountAction`: `Params(entry_id: int, account_id: int)`; allowed on `entry_unmatched`; target account exists, not archived, same currency (422 `currency`), `!= entry.account_id`; entry standalone (ruling 12 → 422 `not_movable`), editable, not protected; `lock_targets.statement_ids` = the target statement per ruling 12; apply: `entry.account_id = account_id` (the dirty trigger records old/new account), then `reconciliation_service.reconcile(db, sid, locked=True)` for the case statement and the target statement (if any); `inverse={"account_id": old}`; invert: move back when unchanged + both reconciles.
- `SplitEntryAction`: `Params(entry_id: int, parts: list[SplitPart])` with `SplitPart(amount: Money, category_id: int, name: str | None = None)`; `len(parts) ≥ 2`, `Σ parts == |entry.amount|`; allowed on `amount_delta`/`line_unmatched` whose candidates/context name the entry; prepare: build `SplitIn(entry_date=entry.entry_date, posted_date=entry.posted_date, project_id=entry.project_id, tags=entry.tags, members=[SplitMemberIn(id=entry.id, kind=entry.kind, amount=parts[0].amount, category_id=…, account_id=entry.account_id, name=…), *[SplitMemberIn(kind=entry.kind, amount=p.amount, category_id=p.category_id, account_id=entry.account_id, name=p.name) for p in parts[1:]]])` (exact `EntryIn` required fields copied from the entry: `currency`, `entry_date`, `account_id`, `category_id`, `kind`, `fx` fields when foreign); apply: `split_service.convert_to_split(db, entry.id, split_in)` → group id, member ids; then claim: the line is matched to the member whose amount equals the line (`write_claim` principal on that member) when one does, else no claim; `before` stores the anchor and all members/children; `inverse={"group_id": gid, "anchor_id": entry.id, "member_ids": [...]}`; invert (spec): refuse unless every member/child equals `after`; then `delete_entries_cascade(db, member_ids - {anchor})`, `dissolve_group(db, group_id, anchor, name=…, merchant=…, description=…)`, restore the anchor's columns from `before`.
- `BalanceAdjustmentAsOfAction` (C4): `Params()` empty; allowed on `balance_gap`; `lock_targets.account_lock=True`; `retry_on_generation=True`; prepare: nothing; apply (engine already holds import key + statement lock; the account lock is taken inside `apply` after the generation read):
  1. `gen0 = _generation(db, account_id)` = `SELECT coalesce(max(id),0) FROM coverage_dirty WHERE old_account_id=:a OR new_account_id=:a`;
  2. `delta = statement.statement_total − ledger_service.account_balance(db, account_id, as_of=statement.period_end)`; `delta == 0` → 422 `zero_delta`;
  3. `SELECT … FROM account WHERE id=:a FOR UPDATE`; `gen1 = _generation(db, account_id)`; `gen1 != gen0` → raise `GenerationMoved`;
  4. insert `LedgerEntry(kind="balance_adjustment", amount=delta, entry_date=period_end, posted_date=period_end, category_id=system_category_id(db,"balance_adjustment"), description=f"對帳調整 {period_end}", source="statement", needs_review=True)`; `inverse={"created_entry_id": id}`; the `balance_gap` case resolves;
  5. engine: on `GenerationMoved` the route rolls back and re-calls `apply_action` (fresh prepare and locks) at most 3 times, then 409 `balance_generation_moved` with no action/audit row.
  invert: delete the adjustment when unchanged.

- [ ] **Step 1: Tests:** move_account to another currency → 422; to a grouped entry → 422 `not_movable`; success re-reconciles both statements (target claim appears), dirty row has the action id; split with parts not summing → 422; success converts, claims the matching member; revert after editing a member → 409, otherwise restores the anchor exactly (`before == snapshot after revert`) and deletes the members; balance_adjustment_asof: zero gap → 422; success inserts the adjustment dated `period_end` and the gap case resolves; C4 race: a second session inserts an entry on the account (with `dirty_on`) between the balance read and the account lock (`wait_until_blocked` + a monkeypatched `_generation` hook) → `GenerationMoved` → retry succeeds with the new delta; three forced moves → 409 `balance_generation_moved` and no action row; double submit → the second is a replay.
- [ ] **Step 2–4:** RED → implement → GREEN.
- [ ] **Step 5:** Commit `feat(accounting): move_account, split_entry and balance_adjustment_asof actions`.

---

### Task 8: Routes for cases, actions, revert; case read model

**Files:**
- Create: `app/routers/reconciliation.py` (router `APIRouter(tags=["Reconciliation"], dependencies=[Depends(require_feature())])`), mounted in `app/main.py` next to the statements router
- Modify: `app/schemas/statements.py` (`CaseOut` additions, `CaseDetailOut`), `app/services/statement_ingest_service.py` (`CASE_FIELDS`, `get_case(db, case_id)`, `list_actions(db, *, statement_id=None, case_id=None, limit=200)`), `app/schemas/reconciliation.py`
- Test: `tests/integration/test_reconciliation_api.py`, `tests/integration/test_scope_enforcement.py` (inventory grows automatically; add the new `{case_id}`/`{action_id}` fills if missing)

**Routes:**
| method | path | scope | body → response |
|---|---|---|---|
| GET | `/reconciliation/cases/{case_id}` | read | → `CaseDetailOut` (CaseOut + `statement_id, revision_id, resolved_by, resolved_action_id, resolved_at, deferred_to_period_end, deferred_to_statement_id, note, actions_allowed: list[str], pending_proposals: list[ProposalOut], line: LineOut | None`) |
| POST | `/reconciliation/cases/{case_id}/actions` | write | `ActionIn` → `ActionOut` (200 on replay, 201 on a new effect) |
| POST | `/reconciliation/cases/{case_id}/dismiss` | write | `DismissIn(note)` → `ActionOut` (sugar for action `dismiss`) |
| GET | `/reconciliation/actions?statement_id=&case_id=&limit=` | read | → `list[ActionOut]` |
| POST | `/reconciliation/actions/{action_id}/revert` | write | → `ActionOut` (the revert row) |
| GET | `/reconciliation/audit?statement_id=&limit=` | read | → `list[AuditOut]` |

- Route handler pattern: `with service_errors(): row, replayed = engine.apply_action(db, case_id=…, action=body.action, params=body.params, actor="owner", label=_label(request), request_id=request.headers.get("x-request-id"))`; `db.commit()`; for `GenerationMoved` the handler retries up to 3 times with `db.rollback()` between attempts, then raises `CodedConflictError("balance_generation_moved")`. After a successful apply the handler calls `policy_service.run_policy(db, statement_id)`? No — policy runs after matching only (reconcile/revision/sweep), not after owner actions.
- `CaseOut` keeps R1b's shape; `list_cases` adds `statement_id` (cheap, already selected). `actions_allowed` from `reconciliation_actions.allowed(case)`.
- Error mapping stays `service_errors()`: `ValidationError` 422 with `message` = the code, `CodedConflictError` 409 with `message` = `"<code>[: detail]"`, `NotFoundError` 404.

- [ ] **Step 1: Tests:** every route's scope (spa 200/201, hermes 403 on mutations, worker 403); action on an unknown case 404; unknown action 422; detail lists `actions_allowed` per kind; replay returns 200 with `replayed=true`; revert route; audit listing ordered by id; `x-request-id` lands in the audit row.
- [ ] **Step 2–4:** RED → implement → GREEN.
- [ ] **Step 5:** Commit `feat(accounting): reconciliation router — case detail, actions, revert, audit`.

---

### Task 9: Agent proposals

**Files:**
- Create: `app/services/proposal_service.py`
- Modify: `app/routers/reconciliation.py`, `app/schemas/reconciliation.py` (`ProposalIn`, `ProposalOut`, `ProposalRejectIn`), `app/services/statement_revision_service.py::_supersede_pending_proposals` (also audit)
- Test: `tests/integration/test_reconciliation_proposals.py`

**Interfaces:**
- `ProposalIn(action: str, params: dict = {}, rationale: str = Field(min_length=1, max_length=1000), confidence: Decimal = Field(ge=0, le=1))`.
- `create_proposal(db, *, case_id, payload, author: str) -> ReconciliationProposal`: case `open|proposed` (409 `case_closed`); action in `allowed(case)` (422); `Action.Params(**params)` validates; id scoping: every `entry_id/group_id/line_id/definition_id` in params must appear in `case.candidates`/`case.context` or equal `case.line_id/entry_id` (422 `id_out_of_scope`) — proposals never reach `allow_sibling` or `move_account` targets outside the context; the author's previous pending proposal on the case → `superseded` (audit); insert with `case_version=case.version`, `event_id=case.event_id`, `status="pending"`; `case.status="proposed"`; audit `proposal_created`.
- `list_proposals(db, *, status=None, case_id=None, limit=200)`: before returning, `UPDATE … SET status='superseded', decided_at=now()` for pending rows whose `case_version < case.version` (audit each) — "superseded on read".
- `apply_proposal(db, *, proposal_id, label, request_id) -> (ReconciliationAction, replayed)`: pending (409 `proposal_not_pending`), `case_version == case.version` (409 `proposal_superseded`, row updated), then `engine.apply_action(..., actor="owner", proposal_id=proposal.id, params=proposal.params)`; proposal `applied`, `applied_action_id`, `decided_at`; audit `proposal_applied`.
- `reject_proposal(db, *, proposal_id, label, note: str | None)`: `rejected`, `decided_at`; if no other pending proposal on the case and the case is `proposed` → `open`; audit.
- Routes: `POST /reconciliation/cases/{case_id}/proposals` (**propose**, 201), `GET /reconciliation/proposals?status=&case_id=&limit=` (read), `POST /reconciliation/proposals/{id}/apply` (write), `POST /reconciliation/proposals/{id}/reject` (write, `ProposalRejectIn(note: str | None = None)`).
- `_supersede_pending_proposals` (revision service) additionally writes an audit row per superseded proposal.

- [ ] **Step 1: Tests:** hermes (`read,propose`) creates a proposal → 201, case `proposed`; hermes cannot apply (403); spa applies → action row with `proposal_id`, proposal `applied`; a proposal with `entry_id` outside candidates → 422 `id_out_of_scope`; a second proposal by the same author supersedes the first; a case version bump (reconcile reopened it) → the pending proposal reads back `superseded` and apply → 409; reject reopens the case to `open` when it was the only pending; a proposal for `match` with `allow_sibling=true` → 422 (never allowed from a proposal); `create_proposal` on `dismissed` case → 409.
- [ ] **Step 2–4:** RED → implement → GREEN.
- [ ] **Step 5:** Commit `feat(accounting): agent proposals — create, list with supersession, apply, reject`.

---

### Task 10: Policy engine and budget (shipped off)

**Files:**
- Create: `app/services/policy_service.py`
- Modify: `app/routers/statements.py` (reconcile route, revisions route, sweep route call `run_policy` after commit), `app/services/reconciliation_actions/engine.py` (`budget` reservation step)
- Test: `tests/integration/test_reconciliation_policy.py`

**Interfaces:**
- `POLICY_ACTIONS = {"create_system_entry": _system_entry_candidate, "attach_fee": _attach_fee_candidate}`; `@dataclass PolicyResult(applied: list[int], proposed: list[int], skipped: dict[str, int])`.
- `run_policy(db, statement_id: int, *, run_id: int | None = None, today: date | None = None) -> PolicyResult`: returns an empty result at once when `policy.enabled` is false, the statement is not `live`, or has `conflict_open`/`needs_recheck`/an open `parse_review`/`statement_conflict`. Otherwise for each open case of kind `line_unmatched` (hint `bank_only`) → candidate `("create_system_entry", {"kind": <line kind>}, amount=|flow|)`; `amount_delta` with `context.fee_matches` → `("attach_fee", {"entry_id": context.entry_id}, amount=|residual|)`; never `ambiguous`, near `amount_delta` (`fee_matches` false), `balance_gap`, `statement_conflict`, `parse_review`, `recheck`, historical, `balance_adjustment` lines. Per candidate: action enabled; `amount ≤ actions[name].max_amount`; per-statement caps counted over this statement's applied policy actions (`actor='policy'`, status `applied`): count < `per_statement_count` and total + amount ≤ `per_statement_total`; then `engine.apply_action(db, case_id, action, params, actor="policy", label="policy", run_id=run_id, budget=(month, currency, amount))` where the engine's `_reserve_budget` does `INSERT INTO policy_budget(month, currency) VALUES … ON CONFLICT DO NOTHING; SELECT … FOR UPDATE; if used_amount + amount > monthly_total → raise BudgetExceeded; UPDATE used_amount += amount, used_count += 1` **before** the statement lock; the whole candidate commits per case (`db.commit()` inside `run_policy` after each apply so a later failure never loses an earlier reservation+effect pair). Over any cap or `BudgetExceeded` → `proposal_service.create_proposal(db, case_id=…, payload=ProposalIn(action, params, rationale=f"policy: {reason}", confidence=Decimal("1")), author="policy")` (`proposed` list; audit `policy_proposed`). An apply that raises `ValidationError`/`ConflictError` → rollback of that candidate, audit `policy_skipped {code}`, continue. Non-TWD statements are skipped unless `policy.budgets` lists their currency? Simplification (ruling): budgets are per currency automatically (one row per currency); the exclusion of foreign-currency accounts in §7.3 is satisfied by `per_currency_enabled: ["TWD"]` default in `POLICY_DEFAULTS` (Task 3 adds the key; other currencies are skipped with `policy_skipped currency`).
- `accept_agent_proposals`: when true, pending proposals authored by a `propose` label on `ambiguous` cases with action `match` are applied by policy **only if** the validator finds exactly one admissible representation for the line among the case's candidates (re-run `matching._representations` on the current population; count == 1 and it is the proposed one) — otherwise skipped with audit; caps apply (amount = |line.flow|).
- Routers: `reconcile` route → after `db.commit()`: `result = policy_service.run_policy(db, statement_id)`; `ReconcileOut` gains `policy: {"applied": n, "proposed": n}`. `POST /statements/revisions` → after commit, when `result.statement.mode == "live"`: `run_policy(db, statement_id, run_id=run.id)`. `POST /reconciliation/sweep` → per item after its commit. Each of these ignores policy errors except by audit (`policy_skipped`), never failing the caller.

- [ ] **Step 1: Tests:** policy off → no effect, no audit; on with `create_system_entry` enabled: a bank-only fee line → entry created `source='statement'`, `actor='policy'`, coverage `match_kind='auto'`, budget row `used_count=1`; `max_amount` exceeded → proposal authored `policy`; per-statement count cap → proposal; monthly cap race (Review Focus 4): two statements, each with a 15,000 TWD system entry at a 20,000 TWD monthly cap, applied via `race` → one `applied`, one `proposed`, `used_amount == 15000`; a candidate whose apply raises (`fee_exists`) → `policy_skipped`, the loop continues; `accept_agent_proposals` off → an `ambiguous` proposal stays pending; on, single admissible rep → applied with `proposal_id`; two admissible → skipped; JPY statement skipped with `policy_skipped currency`; reconcile route returns `policy` counts; revision route runs policy only for live statements.
- [ ] **Step 2–4:** RED → implement → GREEN.
- [ ] **Step 5:** Commit `feat(accounting): policy engine with caps and monthly budget (shipped off)`.

---

### Task 11: Confirm, manual statements, deferral resolution

**Files:**
- Create: `app/services/confirm_service.py`
- Modify: `app/routers/statements.py` (`POST /accounts/{account_id}/statements` and `/confirm`), `app/schemas/statements.py` (`ManualStatementIn`, `ConfirmIn`, `ConfirmOut`), `app/services/statement_revision_service.py` (`submit_revision(..., origin: str | None = None)`), `app/services/statement_ingest_service.py` (`create_worker_run` accepts `trigger="owner_cli"` for manual statements — already does)
- Test: `tests/integration/test_statement_confirm.py`, `tests/integration/test_statement_manual.py`

**Interfaces:**
- `confirm_statement(db, *, account_id, statement_id, force: bool, note: str | None, label: str, request_id) -> ConfirmResult(status, checks: dict[str, bool], reopened_cases: list[int], forced: bool)`: `force and not note` → 422 `note_required`; `take_import_key_shared`; lock the statement; `coverage_service.sweep(db, statement, barrier_wait=True)`; ruling 11 deferral resolution (for each `reconciliation_case` with `deferred_to_statement_id == statement_id` and `explanation='deferred_next_period'`: claimed here? → `context.resolved_in_statement_id`; else reopen on the earlier statement — lock that statement first? It is an earlier id: lock ascending means locking it before ours; so collect the earlier statement ids first, lock all ascending, then proceed); checks: `current_revision` (and `guardrail_ok`), `not conflict_open`, `not needs_recheck`, `no open or proposed cases`, `no pending proposals` (after superseding stale ones), `conserved` (`assert_conserved`); any false and not `force` → 409 `confirm_refused` with `checks` in the detail JSON (`CodedConflictError("confirm_refused", json.dumps(checks))`); force → proceed; `statement.status = "reconciled"`; `_recount`; audit `statement_confirmed` / `statement_confirmed_forced {note, checks}`.
- `POST /accounts/{account_id}/statements/{sid}/confirm` (write) `ConfirmIn(force: bool = False, note: str | None = Field(None, max_length=1000))` → `ConfirmOut(status, checks, reopened_cases, forced)`.
- Manual statement: `ManualStatementIn(kind, currency, period_start, period_end, closing_date?, due_date?, opening_balance?, statement_total, minimum_payment?, lines: list[LineIn] = [])` (reuse `RevisionIn`'s validators by composition: build a `RevisionIn` internally with `file_id=None`, `parser="manual"`, `parser_version="1"`, `raw={}`, `account_id=account_id`, `lease=…`). Service `create_manual_statement(db, *, account_id, payload, label) -> SubmitResult`: `run = create_worker_run(db, trigger="owner_cli", initiator_hint=label, mode="live")`, `claim_run`, `submit_revision(db, run, revision_in, account_map=get_reconciliation_settings(db)["account_map"], origin="manual")`, `finish_run(status="done", summary={"manual": True})`; route `POST /accounts/{account_id}/statements` (write, 201 → `RevisionOut`); after commit `run_policy` if live.
- `submit_revision(..., origin=None)`: `origin or ("import" if payload.file_id is not None else "manual")` (keeps R1a behaviour).

- [ ] **Step 1: Tests (Review Focus 5):** each refusing condition yields 409 with `checks[<name>] is False`; `force` without note 422; forced confirm sets `reconciled` and audits the note; a confirmed statement receiving a new revision sets `conflict_open` (existing R1a path) and a later confirm clears it only when the new revision is current and clean; deferral: statement A defers entry E to B's period; confirming B with E unmatched reopens A's case (`open`, version+1) and B still confirms; with E claimed in B the A case stays resolved and gets `resolved_in_statement_id`; manual statement route creates a live statement with `origin='manual'`, runs the hook (claims) and policy; hermes 403 on both routes; lines capped at 2000 (schema).
- [ ] **Step 2–4:** RED → implement → GREEN.
- [ ] **Step 5:** Commit `feat(accounting): statement confirm with deferral resolution; manual statements`.

---

### Task 12: openspec delta, README, spec §17 R1c notes

- [ ] `openspec/changes/add-statement-reconciliation-r1c/` (`proposal.md`, `tasks.md`, `.openspec.yaml`, `specs/accounting-reconciliation/spec.md` with ADDED: Action engine (idempotency, effect slots, snapshots, audit); Owner actions (per action: params, refusals, inverse); Agent proposals; Policy engine and budget; Statement confirm; Manual statements; Sibling-account candidates; MODIFIED: Eligibility gates (bank payment/purchase, recurring instances, installment groups), Case read model) — scenarios copied from the tests' names.
- [ ] `openspec validate add-statement-reconciliation-r1c` → valid.
- [ ] `services/accounting-service/README.md`: the reconciliation routes table (all R1c routes with scopes), the `policy` and `account_siblings` settings with defaults, the "policy ships off; enable per action" procedure, the confirm semantics.
- [ ] Spec §17: append "R1c implementation notes (2026-10-11, PR 'R1c')" = rulings 1–14 above in final wording, plus the verify-run findings that motivated 1–4 (counts only), and the deferred list (historical-statement actions through a line route; ambiguity hints by card identity; OCR for Type3-font statements; `move_account` typed exceptions beyond ruling 12).
- [ ] Commit `docs(reconciliation): R1c openspec delta, README routes and settings, spec §17 R1c notes`.

---

### Task 13: Whole-branch verification and PR

- [ ] `cd services/accounting-service && .venv/bin/pytest -q -p no:cacheprovider` → green; record the count (expect ≈ 1637 + ~140).
- [ ] `alembic heads` still `f3b1d2c4a9e7` (no migration added); `git diff --stat origin/main` confined to `services/accounting-service/`, `openspec/`, `docs/`, `.superpowers/`.
- [ ] CodeRabbit CLI against `main` (`cr review --agent --base main` from the worktree); fix Critical/Warning.
- [ ] Final whole-branch review (most capable model) per subagent-driven-development; one fix dispatch; scoped re-review.
- [ ] Push; PR "feat(accounting): statement reconciliation R1c — actions, proposals, policy (off), confirm" with: summary per task, the rule-fix rulings, the deploy note "no migration, no env change; feature flag stays off; policy settings default off; after merge `pip install` is not needed (no new dependencies)", test counts; body ends with the Claude Code footer.
- [ ] Multica non-author exact-head review via lead-claude (non-Claude reviewer); answer findings; owner merges.

---

## Self-review (done while writing)

- **Spec coverage:** §4.7 case fields exposed (Task 8); §4.8 proposals incl. supersession on version move and lineage (Task 9 + existing `_supersede_pending_proposals`); §4.9 idempotency, effect slot unique index, snapshots, audit (Task 4); §4.10 budget (Task 10); §4.11 `link_installment_plan` (Task 5); §6 installment via confirmed map unchanged, gates/instances/groups amended by rulings 1–3 (Task 2); §7.1 lifecycle transitions (Tasks 4, 5, 9, 11); §7.2 all thirteen actions + revert (Tasks 4–7), C4 generation compare-and-write (Task 7), C6 snapshot scope (Task 4), C8 fee bounds (Task 6); §7.3 policy second transaction, caps, budget, exclusions, agent-proposal acceptance (Task 10); §7.4 lock order incl. budget before statement (Task 4/10); §8 routes: cases/actions/proposals/revert/confirm/manual statement (Tasks 8, 9, 11), scopes `propose`/`write` (Tasks 8–11); §12 items: C4 races, C6 concurrent edit during apply, C8 fixtures, A6/A7 concurrency (Tasks 4, 6, 7, 10). Not in R1c (recorded): UI (R3), Hermes DTO masking (R4), §3 user split, historical-statement actions (ruling 8), card-identity ambiguity hints, OCR.
- **Placeholders:** none; the only `...` is the `Protocol` method stub in Task 4, which is Python, not a gap.
- **Type consistency:** `apply_action` returns `(ReconciliationAction, bool)` everywhere (Tasks 4, 8, 9, 10); `Effect`/`Context`/`LockSet` names match across Tasks 4–7; `allowed(case)` is the single source for `actions_allowed` (Task 8) and proposal validation (Task 9); `run_policy(db, statement_id, *, run_id=None, today=None)` in Tasks 10 and 11; `create_proposal(db, *, case_id, payload, author)` in Tasks 9 and 10.
- **Review Focus:** each of the five lines names its owning task and the test that pins it (Task 4 #6, Task 6 snapshot race, Task 6 C8 fixtures, Task 10 budget race, Task 11 refusal matrix).
