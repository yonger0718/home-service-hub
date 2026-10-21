# Reconciliation Inbox (待處理) + Owner-Taught Rules — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A 待處理 tab in the SPA's 提醒中心 that turns open reconciliation cases into plain-language cards with ≤3 one-tap
answers, plus owner-taught rules (「以後都這樣」) the daily reconcile applies before the policy.

**Architecture:** Backend first: one composite read endpoint (`GET /reconciliation/inbox`) built by a new
`inbox_service` on top of the existing case/line/entry tables and the action registry; one batch write endpoint
(`POST /reconciliation/inbox/apply`) that calls `engine.apply_action` per case with an expected-version precheck; a new
table `reconciliation_owner_rule` managed by `owner_rule_service`, whose `run_rules_safe` is hooked at the four
`run_policy_safe` call sites. Then the SPA: an `InboxService`, pure view helpers, an `app-inbox-panel` with two sheets,
the tab/bell wiring, the 已處理 list and the settings block.

**Tech Stack:** Python 3.13 / FastAPI / SQLAlchemy / Alembic / pytest (Postgres); Angular 21 signals / Vitest.

**Spec:** `docs/superpowers/specs/2026-10-21-reconciliation-inbox-design.md` (rev 2 + the accept-rule amendment,
commit 66bb115). The plan argues from the spec; conflicts resolve against it.

## Global Constraints

- Worktree `/home/opc/workspace/home-hub-inbox`, branch `feat/reconciliation-inbox` (from main `9c634f6`). Never
  `git add -A` / `git add .` — `frontend/src/environments` and `.env` are local; add paths explicitly.
- Backend tests: run from `services/accounting-service` as
  `PYTHONPATH=../shared-python-lib .venv/bin/python -m pytest <file> -q` (the venv is a symlink to the main checkout's).
  Never run two accounting test files concurrently (shared dev Postgres). Synthetic fixtures only — no owner data.
- Frontend tests: run from `frontend` as `npx ng test --include='<spec path relative to frontend>'`; a "could not
  determine executable" failure is environmental — rerun once.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. One commit per task minimum.
- Copy (zh-TW) is fixed by the spec §7.2 and repeated verbatim in the tasks below; do not paraphrase.
- Scopes: inbox read = `read`; inbox apply, rule delete/patch = `write`; the SPA's `legacy` scope satisfies both.
  All new routes sit behind the feature flag like the rest of the reconciliation router.
- Never surface actions the spec §4.3 leaves out (`create_system_entry`, `backfill_entry`, `split_entry`,
  `move_account`, `create_refund`, `attach_fee`, `link_installment_plan`, `replace_stand_in`,
  `balance_adjustment_asof`, `unmatch`, `resolve_conflict`).
- The engine never commits; routes and the rule runner commit per case (spec §5, §6.3).
- Audit event names: `rule_applied`, `rule_skipped`, `inbox_answer` (added to `audit.EVENTS`; `record()` refuses
  unknown names).
- Actor for rules and inbox applies is `owner`; the audit/engine label is the token label for inbox applies and
  `rule:<id>` for rules.

## Review Focus

1. A line whose `merchant_norm` is only digits (pattern `""`): `group_key` must not merge every such line of an account,
   and `learn` must refuse an empty pattern (`rule_pattern_empty`). → Task 2 (`test_pattern_only_digits_is_empty`,
   `test_group_key_empty_pattern_never_merges`), Task 5 (`test_learn_refuses_empty_pattern`).
2. A merged card whose members span two statements: every member applies in its own transaction and the rule is
   created once → Task 4 (`test_apply_batch_two_statements`).
3. A rule whose `category_id` was deleted after learning: the rule's `create_entry` is refused by the action, audited
   `rule_skipped`, and the case stays open; the run continues to the next case → Task 6
   (`test_rule_with_missing_category_is_skipped_not_fatal`).
4. `counts` must be computed over the whole live set even when `limit` truncates `accounts[].items` → Task 3
   (`test_inbox_counts_ignore_limit`).
5. A proposed case whose proposal was superseded between render and tap: the SPA's "全部套用" loop reports the 409 and
   continues with the remaining proposals → Task 9 (`applies proposals one by one and keeps going after a 409`).

---

## File structure

Backend (`services/accounting-service/`):
- `alembic/versions/a7c3e1d9b2f4_owner_rules.py` — the `reconciliation_owner_rule` table (Task 1).
- `app/models/statements.py` — `ReconciliationOwnerRule` model; `app/models/__init__.py` export (Task 1).
- `app/services/reconciliation_actions/audit.py` — three new event names (Task 1).
- `app/services/inbox_service.py` — read model (pure classifiers + `build_inbox`) and `apply_batch` (Tasks 2–4).
- `app/services/owner_rule_service.py` — `learn`, rule CRUD, `match_rule`, `apply_rules`, `run_rules_safe` (Tasks 5–6).
- `app/schemas/inbox.py` — all inbox / rule schemas (Tasks 3–5).
- `app/routers/reconciliation.py` — inbox + rule routes, `ActionOut` label/answer (Tasks 3, 4, 5, 7).
- `app/routers/statements.py` — the four rule hooks (Task 6).
- `app/schemas/reconciliation.py` — `ActionOut.label`, `ActionOut.answer` (Task 7).
- Tests: `tests/unit/test_inbox_service.py`, `tests/unit/test_owner_rule_service.py`,
  `tests/integration/test_inbox_api.py`, `tests/integration/test_owner_rules.py`.

Frontend (`frontend/src/app/components/accounting/`):
- `inbox/inbox.model.ts`, `inbox/inbox.service.ts`, `inbox/inbox-view.ts` (+ `.spec.ts`) (Task 8).
- `inbox/inbox-panel/inbox-panel.{ts,html,scss,spec.ts}` (Task 9).
- `inbox/record-sheet/record-sheet.{ts,html,spec.ts}`, `inbox/match-sheet/match-sheet.{ts,html,spec.ts}` (Task 10).
- `inbox/handled-list/handled-list.{ts,html,spec.ts}` (Task 11).
- `reminders/reminders.{ts,html}` (Tasks 9, 11), `timeline/timeline.ts` (Task 11),
  `accounting-settings/accounting-settings.{ts,html}` (Task 12).

---

### Task 1: Owner-rule table, model and audit events

**Files:**
- Create: `services/accounting-service/alembic/versions/a7c3e1d9b2f4_owner_rules.py`
- Modify: `services/accounting-service/app/models/statements.py` (after `InstallmentPlanMap`, line ~409)
- Modify: `services/accounting-service/app/models/__init__.py` (export `ReconciliationOwnerRule`)
- Modify: `services/accounting-service/app/services/reconciliation_actions/audit.py:9-16` (`EVENTS`)
- Test: `services/accounting-service/tests/integration/test_owner_rules.py` (new)

**Interfaces:**
- Produces: model `ReconciliationOwnerRule` (`__tablename__ = "reconciliation_owner_rule"`) with columns
  `id`, `account_id` (FK `account.id`, RESTRICT, not null), `enabled` (Boolean, not null, server_default true),
  `line_kind` (String(32), not null), `merchant_pattern` (String(256), not null), `flow_sign` (String(1), not null),
  `amount` (MONEY, nullable), `action` (String(32), not null), `params` (JSONB, not null, server_default `'{}'`),
  `created_from_case_id` (FK `reconciliation_case.id`, SET NULL, nullable), `created_at` (tz, server_default NOW),
  `last_applied_at` (tz, nullable), `applied_count` (Integer, not null, server_default 0), `note` (Text, nullable);
  `UniqueConstraint("account_id", "line_kind", "merchant_pattern", "flow_sign", "amount",
  name="uq_reconciliation_owner_rule_key")`; `Index("ix_reconciliation_owner_rule_account", "account_id")`.
- Produces: `audit.EVENTS` ∪ {`"rule_applied"`, `"rule_skipped"`, `"inbox_answer"`}.

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_owner_rules.py
"""Owner-taught reconciliation rules (inbox spec §6). Synthetic data only."""
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import ReconciliationOwnerRule
from app.services.reconciliation_actions import audit


def _rule(seed, db, account, **fields) -> ReconciliationOwnerRule:
    defaults = dict(line_kind="purchase", merchant_pattern="放款還款", flow_sign="-", amount=Decimal("3865"),
                    action="record", params={"category_id": 1})
    row = ReconciliationOwnerRule(account_id=account.id, **{**defaults, **fields})
    db.add(row)
    db.commit()
    return row


def test_rule_row_defaults(seed, db_session):
    account = seed.account("樂天")
    row = _rule(seed, db_session, account)
    db_session.expire_all()
    row = db_session.get(ReconciliationOwnerRule, row.id)
    assert row.enabled is True and row.applied_count == 0 and row.last_applied_at is None
    assert row.amount == Decimal("3865.0000") and row.params == {"category_id": 1}


def test_rule_key_is_unique_per_account(seed, db_session):
    account = seed.account("樂天")
    _rule(seed, db_session, account)
    with pytest.raises(IntegrityError):
        _rule(seed, db_session, account)
    db_session.rollback()


def test_new_audit_events_are_known():
    assert {"rule_applied", "rule_skipped", "inbox_answer"} <= audit.EVENTS
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=../shared-python-lib .venv/bin/python -m pytest tests/integration/test_owner_rules.py -q`
Expected: FAIL — `ImportError: cannot import name 'ReconciliationOwnerRule'`.

- [ ] **Step 3: Add the migration**

`a7c3e1d9b2f4_owner_rules.py`: `revision = "a7c3e1d9b2f4"`, `down_revision = "f3b1d2c4a9e7"`; `upgrade()` creates the
table exactly as the Interfaces block lists it (MONEY = `sa.Numeric(20, 4)`; use `postgresql.JSONB` for `params`);
`downgrade()` drops it. Copy the docstring header style of `f3b1d2c4a9e7_statement_event_flag.py`.

- [ ] **Step 4: Add the model and export it; add the three audit events**

Model in `app/models/statements.py` right after `InstallmentPlanMap`, same column helpers (`MONEY`, `NOW`, `JSONB`);
add the name to `app/models/__init__.py` beside `InstallmentPlanMap`. Add the three names to `audit.EVENTS` with the
comment `# inbox (spec 2026-10-21 §5, §6.3)`.

- [ ] **Step 5: Run the tests**

Run: `PYTHONPATH=../shared-python-lib .venv/bin/python -m pytest tests/integration/test_owner_rules.py -q`
Expected: 3 passed. Then run `alembic` consistency: `PYTHONPATH=../shared-python-lib .venv/bin/python -m pytest tests/integration/test_reconciliation_api.py -q` → 12 passed (the test DB upgrades through the new revision).

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/a7c3e1d9b2f4_owner_rules.py app/models/statements.py app/models/__init__.py app/services/reconciliation_actions/audit.py tests/integration/test_owner_rules.py
git commit -m "feat(reconciliation): owner-rule table, model and inbox audit events"
```

---

### Task 2: Inbox classifiers (pure)

**Files:**
- Create: `services/accounting-service/app/services/inbox_service.py`
- Test: `services/accounting-service/tests/unit/test_inbox_service.py`

**Interfaces:**
- Produces (all pure, no DB):
  - `SETTLEMENT_LABELS: tuple[str, ...] = ("交割股款", "申購股款")`
  - `QUESTIONS = ("bank_only", "transfer", "ledger_only", "amount_differs", "date_differs", "ambiguous", "balance_gap", "settlement", "proposed", "other")`
  - `FOLDED = frozenset({"proposed", "balance_gap", "settlement", "other"})`
  - `pattern(merchant_norm: str) -> str` — digit runs removed (`re.sub(r"\d+", "", …)`), whitespace collapsed, stripped.
  - `classify(*, kind: str, status: str, statement_kind: str, hint: str | None, merchant_norm: str | None, allowed: list[str]) -> str`
    per spec §4.1, in this order: settlement (`statement_kind == "bank"` and `kind == "line_unmatched"` and any label
    in `merchant_norm`), proposed (`status == "proposed"`), transfer (`hint == "needs_peer"`), bank_only
    (`kind == "line_unmatched"` and `"create_entry" in allowed`), ledger_only, date_differs
    (`kind == "amount_delta"` and `hint == "date_drift"`), amount_differs, ambiguous, balance_gap, else other.
  - `group_key(question: str, account_id: int, line_kind: str, merchant_norm: str, flow_amount: Decimal) -> str | None`
    — `None` unless `question == "bank_only"` and `pattern(merchant_norm) != ""`; else
    `f"{account_id}|{line_kind}|{pattern}|{flow_amount:.4f}"`.
  - `flow_sign(flow_amount: Decimal) -> str` — `"-"` if negative else `"+"`.
  - `buttons(question: str, allowed: list[str], *, adjustable: bool) -> list[str]` — spec §4.3, in this order:
    bank_only → `record` (if `create_entry`), `match_candidate` (if `match`), `dismiss` (if `dismiss`);
    transfer / ambiguous / date_differs → `match_candidate` (if `match`), `dismiss`;
    ledger_only → `defer` (if `defer_next_period`), `dismiss`;
    amount_differs → `accept_bank_amount` (if `adjust_amount` and `adjustable`), `match_candidate` (if `match`), `dismiss`;
    balance_gap → `anchor` (if `anchor_opening`), `dismiss`; proposed → `["apply_proposal", "reject_proposal"]`;
    settlement → `[]`; other → `dismiss` (if `dismiss`).
  - `learnable(question: str, button: str) -> bool` — `(bank_only, record|dismiss)` or `(amount_differs, accept_bank_amount)`.
  - `BUTTONS_TO_ACTIONS = {"record": "create_entry", "match_candidate": "match", "accept_bank_amount": "adjust_amount", "defer": "defer_next_period", "dismiss": "dismiss", "anchor": "anchor_opening"}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_inbox_service.py
from decimal import Decimal

import pytest

from app.services import inbox_service as ib


def test_pattern_strips_digit_runs_and_collapses_space():
    assert ib.pattern("放款還款 20260925 001") == "放款還款"
    assert ib.pattern("ＡＰＰＬＥ 1234  5678 TW") == "ＡＰＰＬＥ TW"


def test_pattern_only_digits_is_empty():
    assert ib.pattern("20260925") == ""


@pytest.mark.parametrize("kind,status,skind,hint,merchant,allowed,expected", [
    ("line_unmatched", "open", "bank", None, "交割股款 1234", ["create_entry", "dismiss"], "settlement"),
    ("line_unmatched", "proposed", "bank", None, "交割股款", [], "settlement"),
    ("line_unmatched", "proposed", "card", None, "全聯", [], "proposed"),
    ("line_unmatched", "open", "bank", "needs_peer", "放款還款", ["match", "dismiss"], "transfer"),
    ("line_unmatched", "open", "card", None, "全聯", ["match", "create_entry", "dismiss"], "bank_only"),
    ("line_unmatched", "open", "card", "installment_unmapped", "分期", ["match", "link_installment_plan", "dismiss"], "other"),
    ("entry_unmatched", "open", "card", None, None, ["defer_next_period", "dismiss"], "ledger_only"),
    ("amount_delta", "open", "card", "date_drift", "自動扣繳", ["match", "dismiss"], "date_differs"),
    ("amount_delta", "open", "bank", None, "卡費", ["match", "adjust_amount", "dismiss"], "amount_differs"),
    ("ambiguous", "open", "card", None, "全聯", ["match", "dismiss"], "ambiguous"),
    ("balance_gap", "open", "bank", None, None, ["anchor_opening", "dismiss"], "balance_gap"),
    ("duplicate_claim", "open", "card", None, "全聯", ["unmatch", "dismiss"], "other"),
])
def test_classify(kind, status, skind, hint, merchant, allowed, expected):
    assert ib.classify(kind=kind, status=status, statement_kind=skind, hint=hint, merchant_norm=merchant,
                       allowed=allowed) == expected


def test_folded_set():
    assert ib.FOLDED == {"proposed", "balance_gap", "settlement", "other"}


def test_group_key_merges_same_pattern_and_amount_only():
    k = lambda m, a, acct=1, kind="purchase": ib.group_key("bank_only", acct, kind, m, Decimal(a))
    assert k("放款還款 0925", "-3865") == k("放款還款 1025", "-3865")
    assert k("放款還款", "-3865") != k("放款還款", "-3870")
    assert k("轉帳支取", "-20637") != k("轉帳支取", "-5000")
    assert k("放款還款", "-3865") != k("放款還款", "-3865", acct=2)
    assert k("放款還款", "-3865") != k("放款還款", "3865")
    assert ib.group_key("transfer", 1, "payment", "放款還款", Decimal("-3865")) is None
    assert ib.group_key("ledger_only", 1, "purchase", "x", Decimal("-1")) is None


def test_group_key_empty_pattern_never_merges():
    assert ib.group_key("bank_only", 1, "purchase", "20260925", Decimal("-100")) is None


@pytest.mark.parametrize("question,allowed,adjustable,expected", [
    ("bank_only", ["match", "create_entry", "create_system_entry", "backfill_entry", "dismiss"], False, ["record", "match_candidate", "dismiss"]),
    ("transfer", ["match", "dismiss"], False, ["match_candidate", "dismiss"]),
    ("ledger_only", ["defer_next_period", "move_account", "dismiss"], False, ["defer", "dismiss"]),
    ("amount_differs", ["match", "adjust_amount", "split_entry", "dismiss"], True, ["accept_bank_amount", "match_candidate", "dismiss"]),
    ("amount_differs", ["match", "adjust_amount", "dismiss"], False, ["match_candidate", "dismiss"]),
    ("date_differs", ["match", "dismiss"], False, ["match_candidate", "dismiss"]),
    ("balance_gap", ["balance_adjustment_asof", "anchor_opening", "dismiss"], False, ["anchor", "dismiss"]),
    ("balance_gap", ["balance_adjustment_asof", "dismiss"], False, ["dismiss"]),
    ("proposed", [], False, ["apply_proposal", "reject_proposal"]),
    ("settlement", ["create_entry", "dismiss"], False, []),
    ("other", ["unmatch", "dismiss"], False, ["dismiss"]),
])
def test_buttons(question, allowed, adjustable, expected):
    assert ib.buttons(question, allowed, adjustable=adjustable) == expected


def test_learnable_and_flow_sign():
    assert ib.learnable("bank_only", "record") and ib.learnable("bank_only", "dismiss")
    assert ib.learnable("amount_differs", "accept_bank_amount")
    assert not ib.learnable("bank_only", "match_candidate") and not ib.learnable("ledger_only", "dismiss")
    assert ib.flow_sign(Decimal("-1")) == "-" and ib.flow_sign(Decimal("0")) == "+"
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=../shared-python-lib .venv/bin/python -m pytest tests/unit/test_inbox_service.py -q`
Expected: FAIL — `ModuleNotFoundError: app.services.inbox_service`.

- [ ] **Step 3: Implement the pure functions in `app/services/inbox_service.py`**

Module docstring: one paragraph naming the spec. Nothing in this task touches the DB.

- [ ] **Step 4: Run to verify pass**

Run: the same command. Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/inbox_service.py tests/unit/test_inbox_service.py
git commit -m "feat(reconciliation): inbox question, group-key and button classifiers"
```

---

### Task 3: `GET /reconciliation/inbox`

**Files:**
- Modify: `services/accounting-service/app/services/inbox_service.py` (add `adjustable`, `build_inbox`)
- Create: `services/accounting-service/app/schemas/inbox.py`
- Modify: `services/accounting-service/app/routers/reconciliation.py` (route)
- Test: `services/accounting-service/tests/integration/test_inbox_api.py` (new)

**Interfaces:**
- Consumes: Task 2 functions; `reconciliation_actions.allowed(case)`; `split_protection.protected_reason(db, entry)`;
  `base.schedule_rows(db, [entry_id])`; `proposal_service.pending_for_case(db, case_id)`;
  `coverage_service.claimed_entry_ids(db)` is NOT needed here.
- Produces:
  - `adjustable(db, case: ReconciliationCase) -> bool` — the case's single candidate entry (`case.entry_id`, else the
    only `entry_id` in `case.candidates`) exists, has `parent_entry_id is None`, no schedule rows, and
    `protected_reason(db, entry) is None`; False when the candidate is a group or absent.
  - `build_inbox(db, *, statuses: tuple[str, ...] = ("open", "proposed"), account_id: int | None = None, limit: int = 500) -> dict`
    returning the spec §4 `InboxOut` shape. Only `AccountStatement.mode == "live"`. Items sorted by account name, then
    statement `period_end` desc, then a date key (line `posted_date`, else entry `posted_date`, else `date.max`), then case id.
    `counts` computed over the whole filtered set: `ask` = number of distinct cards among unfolded items (items with
    `group_key` None count one each; items sharing a key count once), `ask_items` = unfolded item count, the rest per
    question. `limit` truncates the flat item list before grouping into accounts.
    Per item: `line` = `{id, date: posted_date, merchant: merchant_norm, amount: flow_amount, line_kind}` or None;
    `entry` from `case.entry_id` = `{id, date: posted_date, title, amount, account_id}` where
    `title = name or merchant or (category.name if category_id else None) or kind`; `candidates` = up to 5 of
    `case.candidates` that carry an `entry_id` (group candidates skipped — ruling: `match` from the inbox takes an entry),
    resolved to `{entry_id, date, title, amount, reason, residual (str|None), date_distance (int|None)}` in the
    stored order; `proposal` = first `pending_for_case` row as `{id, action, params, author, rationale}` or None;
    `actions` = `buttons(question, allowed, adjustable=adjustable(db, case))`; `rule_hint` = `{merchant_pattern,
    line_kind, flow_sign, amount: str}` when the line exists and any `learnable(question, b)` for `b in actions`, else None;
    `statement` = `{id, period_end}`; `case_version` = `case.version`.
  - Schemas in `app/schemas/inbox.py` (all `model_config = Strict` from `schemas.statements`): `InboxLine`,
    `InboxEntry`, `InboxCandidate`, `InboxProposal`, `InboxRuleHint`, `InboxStatementRef`, `InboxItem`,
    `InboxAccount {id, name, currency, is_credit, items}`, `InboxCounts {ask, ask_items, proposed, balance_gap,
    settlement, other}`, `InboxOut {generated_at: datetime, counts, accounts}`. Amounts are `Decimal`.
  - Route: `GET /reconciliation/inbox` (READ), query `status: str = "open,proposed"` (comma list, each ∈
    {open, proposed}, else 422), `account_id: int | None`, `limit: int = Query(500, ge=1, le=1000)`;
    `response_model=InboxOut`; commits after (proposal supersede on read, like `get_case`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_inbox_api.py
"""Inbox read model and batch apply (spec 2026-10-21 §4–§5). Synthetic data only."""
from datetime import date
from decimal import Decimal

import pytest

from app import auth
from app.models import ReconciliationCase
from tests.integration.statement_fixtures import (  # noqa: F401
    _card, _cases, _line, _open_beside_gap, _submit, run,
)

TOKENS = "spa:spa-token,hermes:hermes-token"
SCOPES = "spa=legacy; hermes=read,propose,enqueue"
S = {"Authorization": "Bearer spa-token"}
H = {"Authorization": "Bearer hermes-token"}
DAY = date(2026, 9, 3)


@pytest.fixture(autouse=True)
def recon_env(monkeypatch):
    monkeypatch.setenv(auth.TOKENS_ENV, TOKENS)
    monkeypatch.setenv(auth.SCOPES_ENV, SCOPES)
    monkeypatch.setenv(auth.FEATURE_ENV, "true")


def _bank(seed, db, name="樂天", **extra):
    account = seed.account(name, statement_live_from=date(2026, 9, 1), **extra)
    db.commit()
    return account


def _inbox(client, **params):
    res = client.get("/reconciliation/inbox", headers=S, params=params)
    assert res.status_code == 200, res.text
    return res.json()


def _items(body, question=None):
    items = [item for account in body["accounts"] for item in account["items"]]
    return items if question is None else [i for i in items if i["question"] == question]


def _open_lines(db, statement_id):
    """The open line_unmatched / amount_delta cases of a statement (a bank statement may also open a balance_gap)."""
    return [c for c in _cases(db, statement_id, "open") if c.kind in ("line_unmatched", "amount_delta")]


def test_bank_only_card_with_buttons_and_rule_hint(client, seed, db_session, run):
    account = _bank(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, account, [_line(1, DAY, "放款還款 0903", 3865, "withdrawal")], kind="bank")
    body = _inbox(client)
    (item,) = _items(body, "bank_only")
    assert body["accounts"][0]["name"] == "樂天" and body["accounts"][0]["is_credit"] is False
    assert item["question"] == "bank_only" and item["folded"] is False
    assert item["actions"] == ["record", "match_candidate", "dismiss"]
    assert item["line"] == {"id": line.id, "date": "2026-09-03", "merchant": line.merchant_norm,
                            "amount": "-3865.0000", "line_kind": "withdrawal"}
    assert item["rule_hint"] == {"merchant_pattern": "放款還款", "line_kind": "withdrawal", "flow_sign": "-", "amount": "-3865.0000"}
    assert item["group_key"] == f"{account.id}|withdrawal|放款還款|-3865.0000"
    assert item["statement"] == {"id": stmt.id, "period_end": "2026-09-30"}
    assert body["counts"]["ask"] == 1 and body["counts"]["ask_items"] == 1


def test_two_same_lines_count_one_card(client, seed, db_session, run):
    account = _bank(seed, db_session)
    _submit(db_session, run, account, [_line(1, DAY, "放款還款 0903", 3865, "withdrawal"),
                                       _line(2, date(2026, 9, 25), "放款還款 0925", 3865, "withdrawal")], kind="bank")
    body = _inbox(client)
    assert (body["counts"]["ask"], body["counts"]["ask_items"]) == (1, 2)
    keys = {item["group_key"] for item in _items(body, "bank_only")}
    assert len(keys) == 1


def test_transfer_and_settlement_and_installment(client, seed, db_session, run):
    account = _bank(seed, db_session)
    _submit(db_session, run, account, [
        _line(1, DAY, "他行轉入", 500, "transfer_in"),
        _line(2, DAY, "交割股款 1234", 9000, "withdrawal"),
    ], kind="bank")
    card = _card(seed, db_session)
    _submit(db_session, run, card, [_line(1, DAY, "分期 1/3", 300, "installment", installment_seq=1, installment_total=3)])
    body = _inbox(client)
    by_q = {item["question"]: item for item in _items(body)}  # one item per question in this fixture
    assert by_q["transfer"]["actions"] == ["match_candidate", "dismiss"] and by_q["transfer"]["rule_hint"] is None
    assert by_q["settlement"]["actions"] == [] and by_q["settlement"]["folded"] is True
    assert by_q["other"]["folded"] is True and by_q["other"]["actions"] == ["dismiss"]
    assert body["counts"]["settlement"] == 1 and body["counts"]["other"] >= 1


def test_historical_statement_cases_are_absent(client, seed, db_session, run):
    card = _card(seed, db_session, live=False)
    _submit(db_session, run, card, [_line(1, DAY, "神秘", 77)])
    assert _items(_inbox(client)) == []


def test_amount_differs_hides_accept_on_a_transfer_entry(client, seed, db_session, run):
    from tests.integration.statement_fixtures import _transfer
    account = _bank(seed, db_session)
    other = seed.account("錢包")
    _transfer(seed, account, other, "879", DAY)  # the bank shows 888: a near delta on a protected transfer leg
    db_session.commit()
    _submit(db_session, run, account, [_line(1, DAY, "卡費", 888, "withdrawal")], kind="bank")
    items = [i for i in _items(_inbox(client)) if i["question"] == "amount_differs"]
    assert items and "accept_bank_amount" not in items[0]["actions"]


def test_inbox_counts_ignore_limit(client, seed, db_session, run):
    account = _bank(seed, db_session)
    _submit(db_session, run, account, [_line(n, DAY, f"店{n}", 10 * n, "withdrawal") for n in range(1, 5)], kind="bank")
    body = _inbox(client, limit=2)
    assert len(_items(body)) == 2 and body["counts"]["ask_items"] == 4


def test_status_filter_and_scope(client, seed, db_session, run):
    assert client.get("/reconciliation/inbox", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/reconciliation/inbox", headers=S, params={"status": "weird"}).status_code == 422
    assert client.get("/reconciliation/inbox", headers=H).status_code == 200
```

Fixture notes: `BANK_GATES["withdrawal"]` admits `transfer_out`, so the −879 transfer leg is a near candidate (delta 9
≤ `near_tolerance_abs` 10) and the case is `amount_delta` with a protected entry. If the installment line does not get
hint `installment_unmapped` from the matcher, set `case.context = {"hint": "installment_unmapped"}` on that case
before the GET (as `_hand_case` does in `test_reconciliation_actions_ledger.py`) and say so in the report. A bank
statement whose ledger is empty may also open a `balance_gap` case — tests filter by question / `_open_lines`.

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=../shared-python-lib .venv/bin/python -m pytest tests/integration/test_inbox_api.py -q`
Expected: FAIL — 404 on `/reconciliation/inbox`.

- [ ] **Step 3: Implement `adjustable`, `build_inbox`, the schemas and the route**

Query shape: one `select(ReconciliationCase, AccountStatement, Account)` joined and filtered, lines by
`StatementLine.id.in_`, entries by id (case entries + candidate entries) with categories in one `select(Category)`;
pending proposals via `proposal_service.pending_for_case` only for `status == "proposed"` cases. `allowed()` per case
(it reads the session for `_anchored`; acceptable at this size).

- [ ] **Step 4: Run to verify pass**

Expected: 7 passed. Also run `tests/unit/test_inbox_service.py` (unchanged, passes).

- [ ] **Step 5: Commit**

```bash
git add app/services/inbox_service.py app/schemas/inbox.py app/routers/reconciliation.py tests/integration/test_inbox_api.py
git commit -m "feat(reconciliation): GET /reconciliation/inbox read model"
```

---

### Task 4: `POST /reconciliation/inbox/apply`

**Files:**
- Modify: `services/accounting-service/app/services/inbox_service.py` (`translate`, `expect_version`, `apply_batch`)
- Modify: `services/accounting-service/app/schemas/inbox.py` (apply schemas)
- Modify: `services/accounting-service/app/routers/reconciliation.py` (route)
- Test: `services/accounting-service/tests/integration/test_inbox_api.py` (append)

**Interfaces:**
- Consumes: `engine.apply_action(db, case_id=, action=, params=, actor="owner", label=, request_id=, precheck=)`;
  `audit.record`; `CodedConflictError`, `CodedValidationError`, `ValidationError`, `ConflictError`, `NotFoundError`.
- Produces:
  - Schemas: `InboxCaseRef {case_id: Int32, case_version: Int32}`; `InboxLearnIn {amount_exact: bool = True}`;
    `InboxApplyIn {cases: list[InboxCaseRef] (min 1, max 50), action: Literal["record","match_candidate","accept_bank_amount","defer","dismiss","anchor"], params: dict = {}, answer: str (min 1, max 80), learn: InboxLearnIn | None = None}`;
    `InboxApplyResult {case_id: int, status: Literal["applied","replayed","refused"], action_id: int | None, code: str | None}`;
    `InboxApplyOut {results: list[InboxApplyResult], rule_id: int | None}`.
  - `translate(button: str, params: dict, case: ReconciliationCase, line: StatementLine | None, answer: str) -> tuple[str, dict]`:
    `record` → `("create_entry", {"kind": "expense" if line.flow_amount < 0 else "income", "category_id": params["category_id"], "name": params.get("name"), "project_id": params.get("project_id")})` (None values dropped);
    `match_candidate` → `("match", {"line_id": case.line_id, "entry_id": params["entry_ids"][str(case.id)] if "entry_ids" in params else params["entry_id"]})`;
    `accept_bank_amount` → `("adjust_amount", {"entry_id": case.entry_id or candidates[0]["entry_id"], "line_id": case.line_id})`;
    `defer` → `("defer_next_period", {"entry_id": case.entry_id})`; `dismiss` → `("dismiss", {"note": f"inbox: {answer}"})`;
    `anchor` → `("anchor_opening", {})`. Missing params → `ValidationError("params", "<name> required")`.
  - `expect_version(expected: int) -> Callable[[Context], None]` — raises `CodedConflictError("stale_case", f"case {ctx.case.id} is at version {ctx.case.version}")` when `ctx.case.version != expected`.
  - `apply_batch(db, *, body: InboxApplyIn, label: str, request_id: str | None) -> dict`:
    1. Load the cases (404 `NotFoundError` if any is missing); compute each case's `question` with `classify`; all
       must be equal, else `ValidationError("cases", "mixed questions")`; `body.action` must be in
       `buttons(question, allowed(case), adjustable=…)` for every case, else `ValidationError("action", "not offered")`.
    2. If `body.learn`: validate the rule key up front via `owner_rule_service.rule_key_for(lines, account_id)`
       (Task 5; raises `rule_pattern_mismatch` / `rule_pattern_empty` / `not_learnable`) — BEFORE any apply.
       (Until Task 5 lands, `learn` is accepted and `rule_id` is None; Task 5 wires it.)
    3. For each case in request order: `with db.begin_nested()`-free pattern — call `engine.apply_action(..., precheck=expect_version(ref.case_version))`
       inside `try`; on success `audit.record(db, event="inbox_answer", actor=label, statement_id=case.statement_id, case_id=case.id, action_id=row.id, request_id=request_id, detail={"answer": body.answer, "button": body.action})` then `db.commit()` → result `applied`/`replayed`; on `(ValidationError, ConflictError, NotFoundError)` → `db.rollback()`, result `refused` with `code = getattr(exc, "code", None) or (exc.message.split(":")[0] if isinstance(exc, ValidationError) else str(exc).split(":")[0])`.
    4. `rule_id` = Task 5's `learn(...)` id when `body.learn` and every result is `applied`/`replayed`, else None.
  - Route `POST /reconciliation/inbox/apply` (WRITE), `response_model=InboxApplyOut`, status 200, label `_label(request) or "owner"`.

- [ ] **Step 1: Write the failing tests (append to `test_inbox_api.py`)**

```python
def _apply(client, cases, action, params=None, answer="答", learn=None, headers=S):
    body = {"cases": [{"case_id": c.id, "case_version": c.version} for c in cases], "action": action,
            "params": params or {}, "answer": answer}
    if learn is not None:
        body["learn"] = learn
    return client.post("/reconciliation/inbox/apply", headers=headers, json=body)


def test_apply_record_books_and_audits_the_answer(client, seed, db_session, run):
    from app.models import LedgerEntry, ReconciliationAudit
    from sqlalchemy import select
    account = _bank(seed, db_session)
    food = seed.category("飲食")
    db_session.commit()
    stmt, (line,), _ = _submit(db_session, run, account, [_line(1, DAY, "放款還款", 3865, "withdrawal")], kind="bank")
    (case,) = _open_lines(db_session, stmt.id)
    res = _apply(client, [case], "record", {"category_id": food.id}, answer="記為支出 飲食")
    assert res.status_code == 200, res.text
    (result,) = res.json()["results"]
    assert result["status"] == "applied" and result["action_id"] and res.json()["rule_id"] is None
    booked = db_session.execute(select(LedgerEntry).where(LedgerEntry.account_id == account.id)).scalars().all()
    assert [(e.kind, e.amount, e.category_id) for e in booked] == [("expense", Decimal("-3865.0000"), food.id)]
    note = db_session.execute(select(ReconciliationAudit).where(ReconciliationAudit.event == "inbox_answer")).scalar_one()
    assert note.case_id == case.id and note.action_id == result["action_id"] and note.actor == "spa"
    assert note.detail == {"answer": "記為支出 飲食", "button": "record"}


def test_apply_refuses_a_stale_version_and_keeps_the_others(client, seed, db_session, run):
    account = _bank(seed, db_session)
    stmt, lines, _ = _submit(db_session, run, account, [_line(1, DAY, "甲", 10, "withdrawal"),
                                                        _line(2, DAY, "乙", 20, "withdrawal")], kind="bank")
    a, b = sorted(_open_lines(db_session, stmt.id), key=lambda c: c.line_id)
    stale = type("Ref", (), {"id": a.id, "version": a.version + 1})
    res = _apply(client, [stale, b], "dismiss", answer="忽略")
    assert res.status_code == 200
    results = {r["case_id"]: r for r in res.json()["results"]}
    assert results[a.id]["status"] == "refused" and results[a.id]["code"] == "stale_case"
    assert results[b.id]["status"] == "applied"
    db_session.expire_all()
    assert db_session.get(ReconciliationCase, a.id).status == "open"
    assert db_session.get(ReconciliationCase, b.id).status == "dismissed"


def test_apply_batch_two_statements(client, seed, db_session, run):
    from tests.integration.statement_fixtures import OCT
    account = _bank(seed, db_session)
    s1, _, _ = _submit(db_session, run, account, [_line(1, DAY, "放款還款", 3865, "withdrawal")], kind="bank")
    s2, _, _ = _submit(db_session, run, account, [_line(1, date(2026, 10, 3), "放款還款", 3865, "withdrawal")], kind="bank", period=OCT)
    cases = _open_lines(db_session, s1.id) + _open_lines(db_session, s2.id)
    res = _apply(client, cases, "dismiss", answer="忽略")
    assert [r["status"] for r in res.json()["results"]] == ["applied", "applied"]


def test_apply_rejects_mixed_questions_and_unoffered_buttons(client, seed, db_session, run):
    account = _bank(seed, db_session)
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "他行轉入", 500, "transfer_in"),
                                                    _line(2, DAY, "店", 20, "withdrawal")], kind="bank")
    cases = _open_lines(db_session, stmt.id)
    assert _apply(client, cases, "dismiss").status_code == 422
    transfer = next(c for c in cases if (c.context or {}).get("hint") == "needs_peer")
    assert _apply(client, [transfer], "record", {"category_id": 1}).status_code == 422


def test_apply_needs_write_scope(client, seed, db_session, run):
    account = _bank(seed, db_session)
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "店", 20, "withdrawal")], kind="bank")
    (case,) = _open_lines(db_session, stmt.id)
    assert _apply(client, [case], "dismiss", headers=H).status_code == 403
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=../shared-python-lib .venv/bin/python -m pytest tests/integration/test_inbox_api.py -q -k apply`
Expected: FAIL — 404.

- [ ] **Step 3: Implement `translate`, `expect_version`, `apply_batch`, the schemas and the route**

- [ ] **Step 4: Run the whole file**

Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/inbox_service.py app/schemas/inbox.py app/routers/reconciliation.py tests/integration/test_inbox_api.py
git commit -m "feat(reconciliation): POST /reconciliation/inbox/apply batch with stale-case precheck"
```

---

### Task 5: Learning rules and the rule routes

**Files:**
- Create: `services/accounting-service/app/services/owner_rule_service.py`
- Modify: `services/accounting-service/app/services/inbox_service.py` (`apply_batch` steps 2 and 4 call this service)
- Modify: `services/accounting-service/app/schemas/inbox.py` (`OwnerRuleOut`, `OwnerRulePatchIn`)
- Modify: `services/accounting-service/app/routers/reconciliation.py` (three rule routes)
- Test: `services/accounting-service/tests/integration/test_owner_rules.py` (append), `tests/integration/test_inbox_api.py` (append)

**Interfaces:**
- Consumes: Task 1 model; Task 2 `pattern`, `flow_sign`, `learnable`; Task 4 `apply_batch`.
- Produces:
  - `RuleKey = tuple[str, str, str]` (line_kind, merchant_pattern, flow_sign).
  - `rule_key_for(lines: list[StatementLine]) -> RuleKey` — every line must give the same key else
    `CodedValidationError("learn", "rule_pattern_mismatch")`; empty pattern → `CodedValidationError("learn", "rule_pattern_empty")`.
  - `learn(db, *, question: str, button: str, account_id: int, lines: list[StatementLine], params: dict, amount_exact: bool, created_from_case_id: int, label: str) -> ReconciliationOwnerRule`:
    `learnable(question, button)` else `CodedValidationError("learn", "not_learnable")`; `action = button`;
    `amount = lines[0].flow_amount if (amount_exact and button != "accept_bank_amount") else None`; stored `params`
    = `{category_id, name?, project_id?}` for `record`, `{}` otherwise; upsert on the unique key using
    `amount IS NOT DISTINCT FROM` (an existing row gets `params`, `enabled=True`, `created_from_case_id` updated);
    audit none (the rule row is its own record). Flushes, does not commit.
  - `list_rules(db) -> list[ReconciliationOwnerRule]` (by account_id, id); `delete_rule(db, rule_id)` (404);
    `set_enabled(db, rule_id, enabled: bool) -> ReconciliationOwnerRule` (404).
  - Schemas: `OwnerRuleOut {id, account_id, enabled, line_kind, merchant_pattern, flow_sign, amount: Decimal | None, action, params, created_from_case_id, created_at, last_applied_at, applied_count, note}` (`from_attributes=True`, extra forbid); `OwnerRulePatchIn {enabled: bool}` (Strict).
  - Routes: `GET /reconciliation/owner-rules` (READ) → `list[OwnerRuleOut]`; `PATCH /reconciliation/owner-rules/{rule_id}` (WRITE) body `OwnerRulePatchIn` → `OwnerRuleOut`; `DELETE /reconciliation/owner-rules/{rule_id}` (WRITE) → 204.
  - `apply_batch` change: step 2 calls `rule_key_for` + checks `learnable`; step 4 calls `learn` with
    `amount_exact=body.learn.amount_exact` and commits; the returned `rule_id` is the row id.

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_inbox_api.py`:

```python
def test_learn_creates_a_rule_with_the_exact_amount(client, seed, db_session, run):
    from app.models import ReconciliationOwnerRule
    from sqlalchemy import select
    account = _bank(seed, db_session)
    food = seed.category("飲食")
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "放款還款 0903", 3865, "withdrawal"),
                                                    _line(2, date(2026, 9, 25), "放款還款 0925", 3865, "withdrawal")], kind="bank")
    cases = _open_lines(db_session, stmt.id)
    res = _apply(client, cases, "record", {"category_id": food.id, "name": "信貸"}, learn={"amount_exact": True})
    assert res.status_code == 200, res.text
    rule = db_session.get(ReconciliationOwnerRule, res.json()["rule_id"])
    assert (rule.account_id, rule.line_kind, rule.merchant_pattern, rule.flow_sign) == (account.id, "withdrawal", "放款還款", "-")
    assert rule.amount == Decimal("-3865.0000") and rule.action == "record"
    assert rule.params == {"category_id": food.id, "name": "信貸"} and rule.enabled is True
    assert [r["status"] for r in res.json()["results"]] == ["applied", "applied"]
    assert client.get("/reconciliation/owner-rules", headers=S).json()[0]["id"] == rule.id


def test_learn_without_exact_amount_and_duplicate_updates(client, seed, db_session, run):
    from app.models import ReconciliationOwnerRule
    from sqlalchemy import func, select
    account = _bank(seed, db_session)
    a = seed.category("甲")
    b = seed.category("乙")
    db_session.commit()
    s1, _, _ = _submit(db_session, run, account, [_line(1, DAY, "店", 10, "withdrawal")], kind="bank")
    (c1,) = _open_lines(db_session, s1.id)
    r1 = _apply(client, [c1], "record", {"category_id": a.id}, learn={"amount_exact": False}).json()["rule_id"]
    from tests.integration.statement_fixtures import OCT
    s2, _, _ = _submit(db_session, run, account, [_line(1, date(2026, 10, 3), "店", 99, "withdrawal")], kind="bank", period=OCT)
    (c2,) = _open_lines(db_session, s2.id)
    r2 = _apply(client, [c2], "record", {"category_id": b.id}, learn={"amount_exact": False}).json()["rule_id"]
    assert r1 == r2
    assert db_session.execute(select(func.count()).select_from(ReconciliationOwnerRule)).scalar_one() == 1
    db_session.expire_all()
    assert db_session.get(ReconciliationOwnerRule, r1).params == {"category_id": b.id}
    assert db_session.get(ReconciliationOwnerRule, r1).amount is None


def test_learn_refuses_mismatched_members_before_applying(client, seed, db_session, run):
    account = _bank(seed, db_session)
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "甲", 10, "withdrawal"),
                                                    _line(2, DAY, "乙", 10, "withdrawal")], kind="bank")
    cases = _open_lines(db_session, stmt.id)
    res = _apply(client, cases, "dismiss", learn={"amount_exact": True})
    assert res.status_code == 422 and "rule_pattern_mismatch" in res.text
    db_session.expire_all()
    assert all(db_session.get(ReconciliationCase, c.id).status == "open" for c in cases)


def test_learn_refuses_empty_pattern(client, seed, db_session, run):
    account = _bank(seed, db_session)
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "20260903", 10, "withdrawal")], kind="bank")
    (case,) = _open_lines(db_session, stmt.id)
    res = _apply(client, [case], "dismiss", learn={"amount_exact": True})
    assert res.status_code == 422 and "rule_pattern_empty" in res.text


def test_learn_is_skipped_when_a_member_is_refused(client, seed, db_session, run):
    account = _bank(seed, db_session)
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "店", 10, "withdrawal"),
                                                    _line(2, DAY, "店", 10, "withdrawal")], kind="bank")
    a, b = _open_lines(db_session, stmt.id)
    stale = type("Ref", (), {"id": a.id, "version": a.version + 5})
    res = _apply(client, [stale, b], "dismiss", learn={"amount_exact": True})
    assert res.status_code == 200 and res.json()["rule_id"] is None


def test_accept_bank_amount_rule_has_no_amount(client, seed, db_session, run):
    from app.models import ReconciliationOwnerRule
    account = _bank(seed, db_session)
    seed.entry(account, "-879", day=DAY, merchant="卡費")
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "卡費", 888, "withdrawal")], kind="bank")
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "amount_delta"]
    res = _apply(client, [case], "accept_bank_amount", learn={"amount_exact": True})
    assert res.status_code == 200, res.text
    rule = db_session.get(ReconciliationOwnerRule, res.json()["rule_id"])
    assert rule.action == "accept_bank_amount" and rule.amount is None and rule.params == {}
```

Append to `tests/integration/test_owner_rules.py`:

```python
def test_rule_routes_list_patch_delete(client, seed, db_session, monkeypatch):
    from app import auth
    monkeypatch.setenv(auth.TOKENS_ENV, "spa:spa-token,hermes:hermes-token")
    monkeypatch.setenv(auth.SCOPES_ENV, "spa=legacy; hermes=read")
    monkeypatch.setenv(auth.FEATURE_ENV, "true")
    S, H = {"Authorization": "Bearer spa-token"}, {"Authorization": "Bearer hermes-token"}
    account = seed.account("樂天")
    row = _rule(seed, db_session, account)
    listed = client.get("/reconciliation/owner-rules", headers=H).json()
    assert [r["id"] for r in listed] == [row.id] and listed[0]["amount"] == "3865.0000"
    assert client.patch(f"/reconciliation/owner-rules/{row.id}", headers=H, json={"enabled": False}).status_code == 403
    assert client.patch(f"/reconciliation/owner-rules/{row.id}", headers=S, json={"enabled": False}).json()["enabled"] is False
    assert client.delete(f"/reconciliation/owner-rules/{row.id}", headers=S).status_code == 204
    assert client.delete(f"/reconciliation/owner-rules/{row.id}", headers=S).status_code == 404
    assert client.get("/reconciliation/owner-rules", headers=S).json() == []
```

- [ ] **Step 2: Run to verify failure**

Run both files. Expected: the new tests fail (no module / 404).

- [ ] **Step 3: Implement the service, schemas, routes and the `apply_batch` wiring**

- [ ] **Step 4: Run both files**

Expected: all passed (`test_inbox_api.py` 18, `test_owner_rules.py` 4).

- [ ] **Step 5: Commit**

```bash
git add app/services/owner_rule_service.py app/services/inbox_service.py app/schemas/inbox.py app/routers/reconciliation.py tests/integration/test_inbox_api.py tests/integration/test_owner_rules.py
git commit -m "feat(reconciliation): learn owner rules from inbox answers; rule routes"
```

---

### Task 6: Applying rules before the policy

**Files:**
- Modify: `services/accounting-service/app/services/owner_rule_service.py` (`match_rule`, `apply_rules`, `run_rules_safe`)
- Modify: `services/accounting-service/app/routers/statements.py:205,220,286,~305` (four hook sites)
- Test: `services/accounting-service/tests/unit/test_owner_rule_service.py` (new), `tests/integration/test_owner_rules.py` (append)

**Interfaces:**
- Consumes: `engine.apply_action(..., actor="owner", label=f"rule:{rule.id}", run_id=run_id, precheck=policy_service.refuse_owner_recorded)`;
  `reconciliation_actions.allowed`; `inbox_service.classify/adjustable/pattern/flow_sign/BUTTONS_TO_ACTIONS`;
  `proposal_service.pending_for_case`; `audit.record`.
- Produces:
  - `reverted_rule_id(db, case: ReconciliationCase) -> int | None`
  - `match_rule(rules: Iterable[ReconciliationOwnerRule], *, line_kind: str, merchant_norm: str, flow_amount: Decimal) -> ReconciliationOwnerRule | None`
    — enabled rules whose `line_kind` and `flow_sign` match, whose `merchant_pattern` is a substring of
    `pattern(merchant_norm)`, and whose `amount` is None or equals `flow_amount`; longest `merchant_pattern` wins,
    then lowest id.
  - `@dataclass RuleRunResult: applied: list[int] = [], skipped: list[tuple[int, str]] = []` (action ids; (case_id, code)).
  - `apply_rules(db, statement_id: int, *, run_id: int | None = None) -> RuleRunResult` — returns empty when the
    statement is not `live`. Loads the account's enabled rules; returns empty when none. For each `open` case of the
    statement with a line (`current_line`), in id order: skip when the line is a settlement line
    (`classify(...) == "settlement"`), when `pending_for_case` is non-empty; question via `classify`;
    eligible = (`bank_only` → rules with action `record`/`dismiss`) or (`amount_differs` with `adjustable` → rules
    with action `accept_bank_amount`); `match_rule` over the eligible rules minus `reverted_rule_id(db, case)` (the rule id parsed from the `rule:<id>` audit
    actor of the `action_applied` row whose `action_id == context.reverted_action_id`, else None — spec §6.3: the same
    rule never redoes what 撤銷 undid; any other rule may); translate with
    `inbox_service.translate(rule.action, rule.params, case, line, answer=f"rule:{rule.id}")` and apply; on success
    `rule.applied_count += 1`, `rule.last_applied_at = now`, audit `rule_applied {rule_id, case_id, action}` with
    `actor=f"rule:{rule.id}"`, `db.commit()`; on `(ValidationError, ConflictError, NotFoundError)`: `db.rollback()`,
    audit `rule_skipped {rule_id, case_id, code}` and `db.commit()`; continue.
  - `run_rules_safe(db, statement_id: int, *, run_id: int | None = None) -> RuleRunResult` — mirrors
    `policy_service.run_policy_safe`: any other exception → log, rollback, audit `rule_skipped {code: <class name>}`
    with `rule_id: None`, never raises.
  - Hooks: in `routers/statements.py`, immediately before each of the four `policy_service.run_policy_safe(...)`
    calls, `owner_rule_service.run_rules_safe(db, <same statement id>, run_id=<same run id>)`.

- [ ] **Step 1: Write the failing unit tests**

```python
# tests/unit/test_owner_rule_service.py
from decimal import Decimal
from types import SimpleNamespace as NS

from app.services import owner_rule_service as ors


def _r(i, pat, *, kind="withdrawal", sign="-", amount=None, enabled=True):
    return NS(id=i, merchant_pattern=pat, line_kind=kind, flow_sign=sign, amount=amount, enabled=enabled)


def test_match_rule_longest_pattern_then_lowest_id():
    rules = [_r(1, "還款"), _r(2, "放款還款"), _r(3, "放款還款")]
    assert ors.match_rule(rules, line_kind="withdrawal", merchant_norm="放款還款 0925", flow_amount=Decimal("-3865")).id == 2


def test_match_rule_respects_kind_sign_amount_enabled():
    m = lambda rules, **kw: ors.match_rule(rules, **{"line_kind": "withdrawal", "merchant_norm": "放款還款", "flow_amount": Decimal("-3865"), **kw})
    assert m([_r(1, "放款還款", kind="purchase")]) is None
    assert m([_r(1, "放款還款", sign="+")]) is None
    assert m([_r(1, "放款還款", amount=Decimal("-3870"))]) is None
    assert m([_r(1, "放款還款", amount=Decimal("-3865.0000"))]).id == 1
    assert m([_r(1, "放款還款", enabled=False)]) is None
    assert m([_r(1, "", )]) is None  # an empty pattern never matches
```

- [ ] **Step 2: Write the failing integration tests (append to `test_owner_rules.py`)**

```python
from datetime import date
from sqlalchemy import select
from app.models import AccountStatement, LedgerEntry, ReconciliationAction, ReconciliationAudit, ReconciliationCase
from app.services import owner_rule_service as ors
from app.services.reconciliation_actions import engine
from tests.integration.statement_fixtures import OCT, _cases, _line, _submit, run  # noqa: F401

DAY = date(2026, 9, 3)


def _bank(seed, db, name="樂天"):
    account = seed.account(name, statement_live_from=date(2026, 9, 1))
    db.commit()
    return account


def _audits(db, event):
    db.expire_all()
    return db.execute(select(ReconciliationAudit).where(ReconciliationAudit.event == event)).scalars().all()


def _open_lines(db, statement_id):
    return [c for c in _cases(db, statement_id, "open") if c.kind in ("line_unmatched", "amount_delta")]


def test_rule_books_a_matching_line_at_ingest_as_owner(seed, db_session, run):
    account = _bank(seed, db_session)
    food = seed.category("飲食")
    db_session.commit()
    _rule(seed, db_session, account, line_kind="withdrawal", merchant_pattern="放款還款", amount=Decimal("-3865"),
          params={"category_id": food.id})
    stmt, (line,), _ = _submit(db_session, run, account, [_line(1, DAY, "放款還款 0903", 3865, "withdrawal")], kind="bank")
    result = ors.run_rules_safe(db_session, stmt.id)
    assert len(result.applied) == 1 and result.skipped == []
    (case,) = _cases(db_session, stmt.id, "resolved")
    action = db_session.get(ReconciliationAction, result.applied[0])
    assert action.actor == "owner" and action.action == "create_entry" and case.resolved_by == "owner"
    (booked,) = db_session.execute(select(LedgerEntry).where(LedgerEntry.account_id == account.id)).scalars().all()
    assert booked.category_id == food.id and booked.amount == Decimal("-3865.0000")
    (ev,) = _audits(db_session, "rule_applied")
    assert ev.actor.startswith("rule:") and ev.detail["case_id"] == case.id and ev.detail["action"] == "create_entry"
    db_session.expire_all()
    rule = db_session.execute(select(ReconciliationOwnerRule)).scalar_one()
    assert rule.applied_count == 1 and rule.last_applied_at is not None


def test_rule_amount_mismatch_and_historical_and_reverted_are_skipped(seed, db_session, run):
    account = _bank(seed, db_session)
    food = seed.category("飲食")
    db_session.commit()
    rule = _rule(seed, db_session, account, line_kind="withdrawal", merchant_pattern="放款還款", amount=Decimal("-3865"),
                 params={"category_id": food.id})
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "放款還款", 3870, "withdrawal")], kind="bank")
    assert ors.run_rules_safe(db_session, stmt.id).applied == []  # amount differs → no match, no audit
    assert _audits(db_session, "rule_skipped") == []
    rule.amount = None
    db_session.commit()
    result = ors.run_rules_safe(db_session, stmt.id)
    assert len(result.applied) == 1
    engine.revert_action(db_session, action_id=result.applied[0], actor="owner", label="spa")
    db_session.commit()
    assert ors.run_rules_safe(db_session, stmt.id).applied == []  # the same rule never redoes what 撤銷 undid
    other = _rule(seed, db_session, account, line_kind="withdrawal", merchant_pattern="款還款", amount=None,
                  params={"category_id": food.id})  # a different rule (shorter pattern, higher id) still may
    assert len(ors.run_rules_safe(db_session, stmt.id).applied) == 1
    (ev,) = [e for e in _audits(db_session, "rule_applied") if e.detail["rule_id"] == other.id]
    hist = seed.account("舊戶")
    db_session.commit()
    _rule(seed, db_session, hist, line_kind="withdrawal", merchant_pattern="店", amount=None, params={"category_id": food.id})
    h, _, _ = _submit(db_session, run, hist, [_line(1, DAY, "店", 10, "withdrawal")], kind="bank")
    assert db_session.get(AccountStatement, h.id).mode == "historical"
    assert ors.run_rules_safe(db_session, h.id).applied == []


def test_rule_skips_owner_recorded_lines_and_audits(seed, db_session, run, monkeypatch):
    """The engine precheck is `policy_service.refuse_owner_recorded`; its own detection is tested in
    test_reconciliation_policy.py — here the wiring: a refusal is audited and the case stays open."""
    from app.services import policy_service
    from app.services.errors import CodedConflictError

    def refuse(ctx):
        raise CodedConflictError("owner_may_record", f"case {ctx.case.id}")

    monkeypatch.setattr(policy_service, "refuse_owner_recorded", refuse)
    account = _bank(seed, db_session)
    food = seed.category("飲食")
    db_session.commit()
    _rule(seed, db_session, account, line_kind="withdrawal", merchant_pattern="放款還款", amount=None, params={"category_id": food.id})
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "放款還款", 3865, "withdrawal")], kind="bank")
    result = ors.run_rules_safe(db_session, stmt.id)
    assert result.applied == [] and [code for _, code in result.skipped] == ["owner_may_record"]
    (ev,) = _audits(db_session, "rule_skipped")
    assert ev.detail["code"] == "owner_may_record" and len(_open_lines(db_session, stmt.id)) == 1


def test_rule_with_missing_category_is_skipped_not_fatal(seed, db_session, run):
    account = _bank(seed, db_session)
    _rule(seed, db_session, account, line_kind="withdrawal", merchant_pattern="甲", amount=None, params={"category_id": 999999})
    good = seed.category("飲食")
    db_session.commit()
    _rule(seed, db_session, account, line_kind="withdrawal", merchant_pattern="乙", amount=None, params={"category_id": good.id})
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "甲", 10, "withdrawal"), _line(2, DAY, "乙", 20, "withdrawal")], kind="bank")
    result = ors.run_rules_safe(db_session, stmt.id)
    assert len(result.applied) == 1 and len(result.skipped) == 1
    assert len(_open_lines(db_session, stmt.id)) == 1


def test_rule_accept_bank_amount_rewrites_within_near(seed, db_session, run):
    account = _bank(seed, db_session)
    entry = seed.entry(account, "-879", day=DAY, merchant="卡費")
    db_session.commit()
    _rule(seed, db_session, account, line_kind="withdrawal", merchant_pattern="卡費", amount=None, action="accept_bank_amount", params={})
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "卡費", 888, "withdrawal")], kind="bank")
    result = ors.run_rules_safe(db_session, stmt.id)
    assert len(result.applied) == 1
    db_session.expire_all()
    assert db_session.get(LedgerEntry, entry.id).amount == Decimal("-888.0000")


def test_rules_run_before_the_policy_at_the_reconcile_route(client, seed, db_session, run, monkeypatch):
    from app import auth
    monkeypatch.setenv(auth.TOKENS_ENV, "spa:spa-token")
    monkeypatch.setenv(auth.SCOPES_ENV, "spa=legacy")
    monkeypatch.setenv(auth.FEATURE_ENV, "true")
    account = _bank(seed, db_session)
    food = seed.category("飲食")
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "放款還款", 3865, "withdrawal")], kind="bank")
    _rule(seed, db_session, account, line_kind="withdrawal", merchant_pattern="放款還款", amount=None, params={"category_id": food.id})
    res = client.post(f"/accounts/{account.id}/statements/{stmt.id}/reconcile", headers={"Authorization": "Bearer spa-token"})
    assert res.status_code == 200, res.text
    assert _open_lines(db_session, stmt.id) == [] and len(_audits(db_session, "rule_applied")) == 1
```

The reconcile route is `POST /accounts/{account_id}/statements/{statement_id}/reconcile` (`routers/statements.py:275`).

- [ ] **Step 3: Run to verify failure**

Run both files. Expected: unit FAIL (no `match_rule`), integration FAIL (no `run_rules_safe`).

- [ ] **Step 4: Implement `match_rule`, `apply_rules`, `run_rules_safe` and the four hooks**

- [ ] **Step 5: Run**

Run `tests/unit/test_owner_rule_service.py`, `tests/integration/test_owner_rules.py`, then
`tests/integration/test_reconciliation_policy.py` (unchanged behaviour: all pass; the hook runs with zero rules).

- [ ] **Step 6: Commit**

```bash
git add app/services/owner_rule_service.py app/routers/statements.py tests/unit/test_owner_rule_service.py tests/integration/test_owner_rules.py
git commit -m "feat(reconciliation): apply owner rules before the policy at every reconcile site"
```

---

### Task 7: `ActionOut.label` and `ActionOut.answer`

**Files:**
- Modify: `services/accounting-service/app/schemas/reconciliation.py:22-38` (`ActionOut`)
- Modify: `services/accounting-service/app/services/inbox_service.py` (`action_labels`)
- Modify: `services/accounting-service/app/routers/reconciliation.py` (`_action_out`, `list_actions`)
- Test: `services/accounting-service/tests/integration/test_inbox_api.py` (append)

**Interfaces:**
- Produces: `ActionOut.label: str | None = None`, `ActionOut.answer: str | None = None`;
  `action_labels(db, action_ids: Iterable[int]) -> dict[int, tuple[str | None, str | None]]` — one query over
  `ReconciliationAudit` where `action_id IN ids` and `event IN ("action_applied", "action_replayed", "inbox_answer")`:
  label = the `actor` of the `action_applied` row (fallback `action_replayed`), answer = `detail["answer"]` of the
  `inbox_answer` row. `list_actions` route fills both; the single-action responses (`apply_action`, `revert`,
  `apply_proposal`) leave them None.

- [ ] **Step 1: Write the failing test (append to `test_inbox_api.py`)**

```python
def test_actions_list_carries_label_and_answer(client, seed, db_session, run):
    from sqlalchemy import select
    from app.models import ReconciliationOwnerRule
    from app.services import owner_rule_service as ors
    from tests.integration.statement_fixtures import OCT
    account = _bank(seed, db_session)
    food = seed.category("飲食")
    db_session.commit()
    s1, _, _ = _submit(db_session, run, account, [_line(1, DAY, "店", 10, "withdrawal")], kind="bank")
    (c1,) = _open_lines(db_session, s1.id)
    _apply(client, [c1], "record", {"category_id": food.id}, answer="記為支出 飲食", learn={"amount_exact": False})
    s2, _, _ = _submit(db_session, run, account, [_line(1, date(2026, 10, 3), "店", 10, "withdrawal")], kind="bank", period=OCT)
    ors.run_rules_safe(db_session, s2.id)
    rows = client.get("/reconciliation/actions", headers=S).json()
    by_stmt = {r["case_id"]: r for r in rows}
    mine = by_stmt[c1.id]
    assert mine["label"] == "spa" and mine["answer"] == "記為支出 飲食"
    (c2,) = _cases(db_session, s2.id, "resolved")
    rule_id = db_session.execute(select(ReconciliationOwnerRule.id)).scalar_one()
    assert by_stmt[c2.id]["label"] == f"rule:{rule_id}" and by_stmt[c2.id]["answer"] is None
```

- [ ] **Step 2: Run to verify failure** — `KeyError: 'label'`.

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run `test_inbox_api.py` and `test_reconciliation_api.py`** — all pass (existing `ActionOut` tests tolerate the two new optional fields; `extra="forbid"` is on input only).

- [ ] **Step 5: Commit**

```bash
git add app/schemas/reconciliation.py app/services/inbox_service.py app/routers/reconciliation.py tests/integration/test_inbox_api.py
git commit -m "feat(reconciliation): action list carries the actor label and the inbox answer"
```

- [ ] **Step 6: Backend gate** — run the whole accounting suite once:
`PYTHONPATH=../shared-python-lib .venv/bin/python -m pytest tests/unit tests/integration -q -p no:cacheprovider 2>&1 | tail -3`
Expected: 0 failed. Record the counts in the task report.

---

### Task 8: SPA inbox model, service and view helpers

**Files:**
- Create: `frontend/src/app/components/accounting/inbox/inbox.model.ts`
- Create: `frontend/src/app/components/accounting/inbox/inbox.service.ts`
- Create: `frontend/src/app/components/accounting/inbox/inbox-view.ts`
- Test: `frontend/src/app/components/accounting/inbox/inbox-view.spec.ts`, `inbox.service.spec.ts`

**Interfaces:**
- Produces (`inbox.model.ts`, mirroring the backend JSON, amounts as `string`):
  `InboxQuestion` union, `InboxButton = 'record' | 'match_candidate' | 'accept_bank_amount' | 'defer' | 'dismiss' | 'anchor' | 'apply_proposal' | 'reject_proposal'`,
  `InboxLine {id, date, merchant, amount, line_kind}`, `InboxEntry {id, date, title, amount, account_id}`,
  `InboxCandidate {entry_id, date, title, amount, reason, residual: string | null, date_distance: number | null}`,
  `InboxProposal {id, action, params, author, rationale}`, `InboxRuleHint {merchant_pattern, line_kind, flow_sign, amount}`,
  `InboxItem {case_id, case_version, kind, status, question, folded, group_key: string | null, statement: {id, period_end}, line, entry, candidates, proposal, actions: InboxButton[], rule_hint}`,
  `InboxAccount {id, name, currency, is_credit, items}`, `InboxCounts`, `InboxOut`,
  `InboxApplyBody {cases: {case_id, case_version}[], action, params, answer, learn?: {amount_exact: boolean}}`,
  `InboxApplyResult`, `InboxApplyOut`, `OwnerRule`, `ReconciliationAction {id, case_id, action, actor, status, label: string | null, answer: string | null, created_at, reverted_by_action_id: number | null}`.
- Produces (`inbox.service.ts`, `@Injectable({ providedIn: 'root' })`, base `/api/accounting`):
  `get(opts?: { accountId?: number; limit?: number }): Observable<InboxOut>`; `apply(body: InboxApplyBody): Observable<InboxApplyOut>`;
  `applyProposal(id: number)`, `rejectProposal(id: number)`; `actions(limit = 50): Observable<ReconciliationAction[]>`;
  `revert(actionId: number)`; `rules(): Observable<OwnerRule[]>`; `patchRule(id: number, enabled: boolean)`;
  `deleteRule(id: number)`; `readonly changed` signal bumped (`tap`) after every write, like `StatementsService.changed`.
- Produces (`inbox-view.ts`, pure):
  - `InboxCard { key: string; question; account: InboxAccount; items: InboxItem[]; actions: InboxButton[]; rule_hint; learnable: boolean }`
  - `buildCards(accounts: InboxAccount[]): { ask: InboxCard[]; proposed: InboxCard[]; balanceGap: InboxCard[]; settlement: InboxCard[]; other: InboxCard[] }`
    — unfolded items merged by `group_key` (null → one card each, key `case:<id>`), order preserved; folded items one card each.
  - `questionText(item: InboxItem, currency: string): string` — exactly:
    bank_only `銀行有、你沒記`; transfer `轉帳／繳費，你沒記 — 在時間軸記一筆轉帳，或對到已記的`; ledger_only `你記了，銀行沒有`;
    amount_differs `銀行 ${X}，你記 ${Y}（差 ${Z}）` with X = `formatMoney(line.amount)`, Y = `formatMoney(entry.amount)` (entry from `candidates[0]` when `entry` is null), Z = |X−Y| formatted;
    date_differs `你記 ${M/D}，銀行 ${M/D} 扣` (entry date, line date via `slashDate`); ambiguous `有 ${n} 筆可能是它`;
    other `需要在 API 處理（案件 #${case_id}）`; balance_gap `期初差額`; settlement `交割款，等證券連動`; proposed `系統有提議：${proposal.action}`.
  - `cardTitle(card: InboxCard, currency: string): string` — `${slashDate(line.date)} ${line.merchant} ${formatMoney(line.amount, currency, {sign: true})}` for line cards, `${slashDate(entry.date)} ${entry.title} ${formatMoney(entry.amount, …)}` for entry cards; merged cards append ` ×${n} 筆`.
  - `buttonLabel(button: InboxButton, item: InboxItem, currency: string): string` — `record` → `記為支出…`/`記為收入…` by line sign; `match_candidate` → `對到 ${M/D} 那行` when `question === 'date_differs'` else `已記在別處，對到…`; `accept_bank_amount` → `改成銀行的 ${formatMoney(line.amount, currency, {sign: true})}`; `defer` → `等下期`; `dismiss` → `不是同一筆` for amount_differs/date_differs/ambiguous else `忽略`; `anchor` → `對齊銀行期初`; `apply_proposal` → `套用`; `reject_proposal` → `退回`.
  - `learnLabel(hint: InboxRuleHint, button: InboxButton): string` — `以後「${merchant_pattern}」都這樣`.
  - `inboxErrorMessage(code: string | null): string` — `stale_case`/`candidate_changed` → `對象已變，重新整理`; `not_conserved` → `金額對不上，不能直接對`; `protected` → `這筆是轉帳／交割，不能改金額`; `owner_may_record` → `你已經記過了`; `historical_statement` → `舊對帳單不能改`; else `沒成功：${code ?? '未知錯誤'}`.
  - `errorCode(err: unknown): string | null` — the `<code>` prefix of an `HttpErrorResponse.error.detail` string (409) or `error.detail[0].msg` (422).

- [ ] **Step 1: Write the failing tests** (`inbox-view.spec.ts`: one `it` per helper with the exact strings above using a
`makeItem()` local factory; `inbox.service.spec.ts`: `HttpTestingController` asserting URLs
`/api/accounting/reconciliation/inbox?limit=1`, `POST …/inbox/apply`, `POST …/proposals/5/apply`, `GET …/actions?limit=50`,
`POST …/actions/7/revert`, `GET …/owner-rules`, `PATCH …/owner-rules/3`, `DELETE …/owner-rules/3`, and that `changed()` increments after `apply`.)

- [ ] **Step 2: Run** `npx ng test --include='src/app/components/accounting/inbox/**/*.spec.ts'` — FAIL (modules missing).

- [ ] **Step 3: Implement the three files**

- [ ] **Step 4: Run** — all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/inbox/inbox.model.ts frontend/src/app/components/accounting/inbox/inbox.service.ts frontend/src/app/components/accounting/inbox/inbox-view.ts frontend/src/app/components/accounting/inbox/inbox-view.spec.ts frontend/src/app/components/accounting/inbox/inbox.service.spec.ts
git commit -m "feat(spa): inbox model, service and view helpers"
```

---

### Task 9: Inbox panel and the 待處理 tab

**Files:**
- Create: `frontend/src/app/components/accounting/inbox/inbox-panel/inbox-panel.{ts,html,scss,spec.ts}`
- Modify: `frontend/src/app/components/accounting/reminders/reminders.ts:40-55` (`ReminderTab`, `REMINDER_TABS`), `:429-470` (`load()`), computed show flags
- Modify: `frontend/src/app/components/accounting/reminders/reminders.html` (tab section)
- Test: `inbox-panel.spec.ts`, `reminders.spec.ts` (append)

**Interfaces:**
- Consumes: Task 8.
- Produces: `AppInboxPanelComponent` (`selector: 'app-inbox-panel'`, standalone) with
  `data = input.required<InboxOut>()`, `mode = input<'full' | 'preview'>('full')`, `changed = output<void>()`,
  `openRecord = output<InboxCard>()`, `openMatch = output<InboxCard>()`.
  Rendering: header line `要你決定 ${counts.ask} · 其他 ${proposed+balance_gap+settlement+other}`; per account (name, `is_credit ? '💳' : '🏦'`),
  its ask cards (`.inbox-card`: `.title` bold = `cardTitle`, `.q` muted = `questionText`, `.btns` buttons with `buttonLabel`,
  merged cards list members in `.members` as `${slashDate(date)} ${formatMoney(amount)}`); preview mode shows only
  the first three ask cards and a link `還有 ${n} 張 →` to `/accounting/reminders?tab=todo`.
  Folded `<details>` sections under the cards: `系統有提議 ${n}（全部套用）` with a `全部套用` button and per-card 套用/退回;
  `期初差額 ${n}` with 對齊銀行期初/忽略; `國泰交割款 ${n} — 等證券連動` (no buttons); `其他 ${n}` with 忽略.
  One-tap buttons (`dismiss`, `accept_bank_amount`, `defer`, `anchor`) call `inbox.apply` with
  `cases = card.items.map(i => ({case_id, case_version}))`, `answer = buttonLabel(...)`, `learn` when the card's learn
  checkbox (`☐ 以後「…」都這樣`, rendered when `card.learnable`, pre-ticked and disabled when `items.length > 1`
  for record/dismiss; never shown with an amount option) is ticked: `{ amount_exact: true }` for record/dismiss and
  `{ amount_exact: false }` for accept_bank_amount. Dismiss on a merged card first toggles a `確定` state on the same button.
  `record` / `match_candidate` emit `openRecord` / `openMatch` (Task 10). `apply_proposal` loops `applyProposal` per
  item with a progress text `套用中 k/n`, continuing after an error and listing failures; `reject_proposal` likewise.
  After a successful apply: remove the card's applied items locally, keep refused ones with `.refused` text
  `沒成功：${inboxErrorMessage(code)}`, emit `changed`.
- Reminders: `ReminderTab` gains `'todo'`; `REMINDER_TABS` = 全部, **待處理** (`todo`), 信用卡帳單, 借還款追蹤, 待完成交易;
  `inbox = signal<InboxOut | null>(null)`; `load()` adds `inbox: this.inboxService.get().pipe(catchError(() => of(null)))`;
  `showInbox = computed(() => tab() === 'all' || tab() === 'todo')`; the panel is rendered with `mode` `preview` on 全部
  and `full` on 待處理; `(changed)` → `this.accounting.markEntriesChanged()` — add that public method to
  `AccountingService` if no public bump exists (it wraps `this.changeCount.update(c => c + 1)`); the existing
  `load()` effect then reloads. The `empty` computed (`reminders.ts:~344`) counts `inbox()?.counts.ask ?? 0` when the tab is `todo` or `all`.

- [ ] **Step 1: Write the failing tests**

`inbox-panel.spec.ts` (TestBed + `provideHttpClientTesting`): `renders one card per group_key with ×N and the question text`;
`one-tap dismiss posts the apply body with every member and the answer`; `learn checkbox adds learn with amount_exact true for record and false for accept`;
`a refused member stays with 沒成功 text and the others leave`; `preview mode shows three cards and the 還有 N 張 link`;
`applies proposals one by one and keeps going after a 409` (two proposals, the first request flushed with status 409, the second still sent, failure listed).
`reminders.spec.ts`: `the 待處理 tab is second and deep-links with ?tab=todo` and `load() requests the inbox and renders the panel on 全部`.

- [ ] **Step 2: Run** `npx ng test --include='src/app/components/accounting/inbox/**/*.spec.ts'` and `--include='src/app/components/accounting/reminders/reminders.spec.ts'` — FAIL.

- [ ] **Step 3: Implement the panel, the tab and the load wiring**

Styles: reuse the mockup's card look (border `var(--border)`, radius 10px, pill buttons, muted `.q`); phone width first.

- [ ] **Step 4: Run both** — pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/inbox/inbox-panel frontend/src/app/components/accounting/reminders/reminders.ts frontend/src/app/components/accounting/reminders/reminders.html frontend/src/app/components/accounting/reminders/reminders.spec.ts frontend/src/app/services/accounting.service.ts
git commit -m "feat(spa): 待處理 inbox panel and reminders tab"
```

---

### Task 10: Record sheet and match sheet

**Files:**
- Create: `frontend/src/app/components/accounting/inbox/record-sheet/record-sheet.{ts,html,spec.ts}`
- Create: `frontend/src/app/components/accounting/inbox/match-sheet/match-sheet.{ts,html,spec.ts}`
- Modify: `inbox-panel.{ts,html}` (open/close the sheets, forward the result)
- Test: the two spec files, `inbox-panel.spec.ts` (append)

**Interfaces:**
- Consumes: `CategoryPicker` (`app-category-picker`, `kind = input.required<string>()`, `selected = model<CategoryNode | null>`), `AccountingService.getCategories(kind)`, `AccountingService.getAllEntries(query)` with `AllEntriesQuery` (`models/accounting.model.ts:225-245`: `account_id` is `number[]`; use its date-range keys for the ±14-day window and `limit: 50`), `sheet.scss` + `data-overlay` + `focusSheetField` as `schedule-sheet.ts` does.
- Produces:
  - `AppInboxRecordSheetComponent` (`app-inbox-record-sheet`): `card = input.required<InboxCard>()`, `currency = input.required<string>()`,
    `confirmed = output<{ category_id: number; name: string | null; project_id: number | null; learn: boolean }>()`, `closed = output<void>()`.
    Title `記為${支出|收入}`, inline category picker for the line's kind, optional name input (placeholder = the line merchant),
    project select from `AccountingService.getProjects()` (optional, default none), the learn checkbox when `card.learnable`
    (pre-ticked + disabled on merged cards), buttons 取消 / 確定 (確定 disabled until a category is picked).
  - `AppInboxMatchSheetComponent` (`app-inbox-match-sheet`): `card = input.required<InboxCard>()`, `account = input.required<InboxAccount>()`,
    `confirmed = output<{ entry_ids: Record<string, number> }>()`, `closed = output<void>()`.
    Lists `candidates` (`${slashDate(date)} ${title} ${formatMoney(amount)}`) as radio rows; a `搜尋這個帳戶的記錄` button loads
    entries of the account within ±14 days of the line date with the same sign, excluding ids already listed; on a merged
    card the sheet steps through members (`第 k/n 筆`) collecting one entry per case; 確定 emits the map.
  - Panel wiring: `openRecord` → sheet → `inbox.apply({ action: 'record', params: {category_id, name, project_id}, answer: '記為支出 ' + categoryName, learn })`;
    `openMatch` → sheet → `inbox.apply({ action: 'match_candidate', params: { entry_ids }, answer: '對到 ' + title })`.

- [ ] **Step 1: Write the failing tests** — record sheet: `emits category, name and learn on 確定`, `確定 disabled without a category`, `merged card locks the learn tick`; match sheet: `lists candidates and emits the picked entry per case`, `search loads the account's entries in the window and excludes listed ids`; panel: `record flow posts create params and the answer text`.

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run** `--include='src/app/components/accounting/inbox/**/*.spec.ts'` — pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/inbox
git commit -m "feat(spa): inbox record and match sheets"
```

---

### Task 11: 已處理 list, revert, and the bell count

**Files:**
- Create: `frontend/src/app/components/accounting/inbox/handled-list/handled-list.{ts,html,spec.ts}`
- Modify: `reminders.html` (render under the inbox on the 待處理 tab, collapsed `<details>`), `reminders.ts` (disable-rule follow-up)
- Modify: `frontend/src/app/components/accounting/timeline/timeline.ts:382-385,515-520,868-880` (inbox ask count in `reminderCount`)
- Test: `handled-list.spec.ts`, `timeline.spec.ts` (append near line 1569)

**Interfaces:**
- Produces: `AppInboxHandledListComponent` (`app-inbox-handled-list`): loads `inbox.actions(50)` on init and whenever
  `inbox.changed()` bumps; rows `${who} · ${answer ?? action} · ${slashDate(created_at)}` where who =
  `label?.startsWith('rule:') ? '規則 #' + id : actor === 'policy' ? '系統' : '你'`; 撤銷 button (hidden when
  `reverted_by_action_id` or `status !== 'applied'`) → `inbox.revert(id)`; on a rule row the button text is
  `撤銷` and after success a second button `同時停用這條規則` appears → `inbox.patchRule(ruleId, false)`;
  a `not_revertable` 422 shows `不能撤銷` inline. `changed = output<void>()` after a revert.
- Timeline: `private readonly inboxAsk = signal(0)`; `loadInboxCount()` next to `loadQueue()` calling
  `inbox.get({ limit: 1 })`, errors swallowed; `reminderCount` adds `this.inboxAsk()`; called where `loadQueue()` is.

- [ ] **Step 1: Write the failing tests** — handled list: `labels rule, policy and owner rows`, `revert posts and emits changed`, `rule row offers 同時停用這條規則 after revert`, `not_revertable shows 不能撤銷`; timeline: `bell adds the inbox ask count` (flush `GET /api/accounting/reconciliation/inbox?limit=1` with `counts.ask: 2` → aria-label `提醒中心，2 項` when nothing else is due).

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run** the inbox specs, `reminders.spec.ts`, `timeline.spec.ts` — pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/inbox/handled-list frontend/src/app/components/accounting/reminders frontend/src/app/components/accounting/timeline/timeline.ts frontend/src/app/components/accounting/timeline/timeline.spec.ts
git commit -m "feat(spa): 已處理 list with revert; bell counts inbox cards"
```

---

### Task 12: Rules block in 記帳設定

**Files:**
- Modify: `frontend/src/app/components/accounting/accounting-settings/accounting-settings.{ts,html}`
- Test: `accounting-settings.spec.ts` (append)

**Interfaces:**
- Consumes: `InboxService.rules/patchRule/deleteRule`; the page's `confirming` signal and `confirmTap(key)` two-tap pattern (`accounting-settings.html:17-19`).
- Produces: a `<details class="data-section rules-section">` titled `對帳規則 (${n})` listing each rule as
  `${accountName} · 「${merchant_pattern}」 ${flow_sign}${amount ?? ''} · ${answerText} · 用過 ${applied_count} 次${last_applied_at ? ' · ' + slashDate(last_applied_at) : ''}`
  where answerText = `record` → `記為 ${category name or '#' + category_id}`, `dismiss` → `忽略`, `accept_bank_amount` → `用銀行的數`;
  a toggle `停用`/`啟用` (`patchRule`) and the two-tap 🗑 (`confirmTap('rule:' + id) && deleteRule(id)`); account names from the page's accounts.

- [ ] **Step 1: Write the failing tests** — `lists rules with the answer text and applied count`, `two-tap delete removes the row`, `toggle patches enabled`.

- [ ] **Step 2: Run** `npx ng test --include='src/app/components/accounting/accounting-settings/accounting-settings.spec.ts'` — FAIL.

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run** — pass. Then the frontend gate: `npx ng test 2>&1 | tail -5` → 0 failed (the 57 pre-existing portfolio unit failures live in the Python portfolio service, not here; if an unrelated spec fails, record it and do not fix it).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/accounting-settings
git commit -m "feat(spa): 對帳規則 block in 記帳設定"
```

---

## Rollout notes (not tasks)

Backend deploy (API only; no worker change) → `alembic upgrade head` (one new table) → `GET /reconciliation/inbox`
on prod and compare `counts` with spec §10 → SPA release (retire the oldest release or raise `max_releases` first;
store is 7/7). Rules start empty.
