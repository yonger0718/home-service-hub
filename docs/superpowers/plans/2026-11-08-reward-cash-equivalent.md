# Cash-equivalent reward accounts (polish-reward-and-account-settings PR A2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An account can be marked 「1 點 = 1 元」 (`cash_equivalent`); rewards paid into it count as money everywhere the reward unit is decided, so the overview's 「本期回饋合計」 is meaningful, and the header folds the remaining points units into 「＋ N 種點數」.

**Architecture:** One nullable-free boolean column on `account`, readable on every account read, writable through the existing `PUT /accounts/{id}` / `POST /accounts` without clearing it when a client omits it. `reward_engine`'s usage unit adds it to the money test. The openspec change `polish-reward-and-account-settings` (merged, not archived) is amended in the same PR so PR B / PR C / PR D argue from it.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, pytest; openspec CLI 1.15.

**Spec:** `openspec/changes/polish-reward-and-account-settings/` — `design.md` D1a, `specs/accounting-ledger/spec.md` "Reward usage unit", `specs/frontend-accounting-ledger/spec.md` "Reward rules overview page", "Reward overview units and windows", "Account settings page", "Entry reward card". Owner ruling 2026-11-08: cash-convertible points count as money via a per-account setting the owner ticks; the header shows money plus 「＋ N 種點數」.

## Global Constraints

- Column `account.cash_equivalent BOOLEAN NOT NULL DEFAULT false`; migration revises the current head `b6e2c8f4a9d1`; downgrade drops the column.
- `AccountIn.cash_equivalent: bool | None = None`. Create: `None` → false. Update: `None` keeps the stored value (an older SPA or Hermes PUT that omits the field never clears it); `true`/`false` writes it. It is not a MOZE field: neither MOZE import touches it.
- `reward_is_money` = reward account is the card itself or unset, or `is_credit`, or `cash_equivalent` — archived or not.
- Copy (spec): setting label `1 點 = 1 元`, helper `回饋入這個帳戶時當成金額計算`; header suffix `＋ N 種點數` (N = distinct non-money reward accounts among the shown cards' usage rows, omitted when 0).
- Fixtures fictional. Never run alembic or uvicorn from the worktree. Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. The current SPA's account settings PUT (no `cash_equivalent` in its body) on a flagged account → flag stays true (Task 2 `put_without_the_field_keeps_it`).
2. A flagged account that is archived still makes its rules money (Task 3 `archived_cash_equivalent`).
3. A shared-cap group whose first rule pays into a flagged account → the row is money (unit still from the first rule; Task 3 reuses the group test shape).
4. MOZE backup re-import over a flagged account → flag unchanged (Task 2 `moze_import_keeps_cash_equivalent`).
5. Spec validation of the amended change with the 1.15 CLI (`--strict`), requirement bodies ≤ 500 characters (Task 1).

---

### Task 1: Spec amendment

**Files:** `openspec/changes/polish-reward-and-account-settings/{design.md,tasks.md}`, `specs/accounting-ledger/spec.md`, `specs/frontend-accounting-ledger/spec.md`

- [ ] **Step 1:** design.md D1a: the money test gains 「or an account marked `cash_equivalent` (1 點 = 1 元, owner ruling 2026-11-08)」; the header bullet becomes 「the header's 本期回饋合計 sums money only, followed by 「＋ N 種點數」 when other units exist (each unit's figure stays on its card)」; the entry bullet becomes 「the entry reward cards use `rewardAmount`, which also treats a `cash_equivalent` account as money」. D3 header line gets the suffix.
- [ ] **Step 2:** accounting-ledger spec: "Reward usage unit" body adds `cash_equivalent`; scenario 「Cash-equivalent points account」 (GIVEN a rule paying into the non-credit account 測試現金點 marked `cash_equivalent` → `reward_is_money` true). New requirement "Cash-equivalent accounts": `cash_equivalent` on account reads (list, detail), written by `POST`/`PUT /accounts`, omitted on `PUT` keeps the stored value, untouched by MOZE imports; scenario 「PUT without the field」.
- [ ] **Step 3:** frontend spec: overview header sentence and "Reward overview units and windows" body → 「followed by 「＋ N 種點數」 counting the other units」; scenario "Points rule on the overview" THEN adds 「and the header SHALL end with ＋ 1 種點數」. "Account settings page" 基本 bullet adds 「and, on a non-credit account, a `1 點 = 1 元` switch with the helper 回饋入這個帳戶時當成金額計算」. "Entry reward card" body: money also when the reward account is `cash_equivalent`.
- [ ] **Step 4:** tasks.md: add `1.3 cash_equivalent column, account read/write, usage unit (PR A2)` and `1.4 Tests (PR A2)`; under 4.x (settings, PR C) add the switch; under 5.x (entry, PR D) add the `rewardAmount` money test.
- [ ] **Step 5:** `openspec validate polish-reward-and-account-settings --strict` → valid. Commit `docs(openspec): cash-equivalent reward accounts and the folded points header (#323)`.

### Task 2: Column, reads and writes

**Files:** `app/models/ledger.py` (Account), new `alembic/versions/<rev>_account_cash_equivalent.py`, `app/schemas/writes.py` (`AccountIn`), `app/schemas/ledger.py` (account out), `app/services/ledger_service.py` (`ACCOUNT_FIELDS`), `app/services/settings_service.py` (`create_account`, `update_account`, `ACCOUNT_COLUMNS_EXCLUDE`); tests `tests/integration/test_accounts_api.py`, `tests/integration/test_models_schema.py` (column default), the MOZE backup import test module.

**Interfaces:** Produces `Account.cash_equivalent: bool`; `AccountOut.cash_equivalent: bool` (list and detail); `AccountIn.cash_equivalent: bool | None`.

- [ ] **Step 1: Failing tests** (`test_accounts_api.py`): `test_cash_equivalent_defaults_false_and_is_listed` (POST without it → detail and list `false`); `test_put_sets_cash_equivalent` (PUT `true` → `true`, PUT `false` → `false`); `test_put_without_the_field_keeps_it` (set `true`, PUT body from the detail minus `cash_equivalent` → still `true`); MOZE backup import module: `test_moze_import_keeps_cash_equivalent` (flagged account matched by `moze_id` → still `true` after import). Extend the `set(body[0]) >= {...}` key list with `cash_equivalent`.
- [ ] **Step 2: Run** `PYTHONPATH=.:../shared-python-lib .venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_accounts_api.py <moze module>` → FAIL.
- [ ] **Step 3: Implement.** `cash_equivalent` joins `ACCOUNT_COLUMNS_EXCLUDE`; `create_account` writes `bool(payload.cash_equivalent)`; `update_account` writes it only when not `None`. Migration: `op.add_column("account", sa.Column("cash_equivalent", sa.Boolean(), nullable=False, server_default=sa.text("false")))`.
- [ ] **Step 4: Run** → PASS; also `tests/integration/test_models_schema.py` and the alembic upgrade/downgrade test module if one exists for heads.
- [ ] **Step 5: Commit** `feat(accounting): account cash_equivalent flag (1 點 = 1 元) (#323 PR A2)`

### Task 3: Usage unit

**Files:** `app/services/reward_engine.py` (~1044); test `tests/integration/test_reward_usage_api.py` (D1a block, `_pay_into`, `_unit`).

- [ ] **Step 1: Failing tests:** `test_usage_unit_cash_equivalent_account_is_money` (non-credit `測試現金點` with `cash_equivalent=True` → `(id, "測試現金點", True)`); `test_usage_unit_archived_cash_equivalent` (also archived → `True`); `test_overview_usage_unit_cash_equivalent` (same row on `GET /reward-rules`'s account `usage`).
- [ ] **Step 2: Run** `tests/integration/test_reward_usage_api.py` → FAIL on the three.
- [ ] **Step 3: Implement:** `or bool(payee.cash_equivalent)`.
- [ ] **Step 4: Run** `tests/integration/test_reward_usage_api.py tests/integration/test_reward_rules_overview.py` → PASS; then the full accounting suite (~29 min) → all pass, count recorded.
- [ ] **Step 5: Commit** `feat(accounting): cash-equivalent reward accounts count as money (#323 PR A2)`

## After merge (outside the repo, owner go)

Deploy API + worker (`alembic upgrade` on the API host per the PR A record script, then restart); Hermes `accounting-api.md` gains the field; data pass: the owner names the accounts to tick (candidates from 紅利點數 and the bank account 台新 that rules pay into), each set by GET → PUT with `cash_equivalent: true` (PUT marks `settings_locally_edited`, as any settings save does).
