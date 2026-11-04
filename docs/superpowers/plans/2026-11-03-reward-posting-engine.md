# Reward Posting Engine (回饋規則 phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compute reward accruals for card spends linked to reward rules. Post them as real reward rows the way MOZE
did, track cap usage per window, and let the card statement's reward lines win.

**Architecture:** A new `reward_accrual` table holds estimates off the ledger. Pure modules handle windows and
amounts (`cycles`, `reward_windows`, `reward_math`). `reward_engine` keeps accruals in sync on every entry, rule and
account write. `reward_posting` turns due accruals into ledger rows; a daily job and a manual endpoint drive it.
`reward_bank_wins` runs inside `_match_and_record` before matching. The SPA reads a new `/reward-usage` endpoint and
the accruals on entry detail.

**Tech Stack:** Python 3.13 / FastAPI / SQLAlchemy / Alembic / APScheduler / PostgreSQL. Angular 21 + Vitest.

**Spec:** `docs/superpowers/specs/2026-11-03-reward-posting-engine-design.md`, rev 2.2 `32f5a656` (owner approved
2026-11-03; Multica AGENT-393/394 accepted). Section references (§) below point into it.

**Plan rev 2** folds the Multica plan review AGENT-396 (dev-grok, CHANGES at `4cf7dec4`, findings 1–9 + nits) and
the P5 gap (a dismiss-only early case would leave a bank-paid reward at 0). Mapping at the end.

## Global Constraints

- Worktree `~/workspace/home-hub-reward-engine`, branch `feat/reward-posting-engine`, base `origin/main` `99ddee79`.
  Never checkout or reset `main`. Never bare `git stash`. `rm -rf` only on literal paths.
- Never copy or print any `.env`. Backend tests run from `services/accounting-service` with this exact prefix (the
  values stay inside the shell):
  `set -a; eval "$(grep -E '^(POSTGRES_(HOST|PORT|USER|PASSWORD|DB)|DB_HOST|ACCOUNTING_DB)=' ~/workspace/home-hub/.env)"; set +a; OTEL_SDK_DISABLED=true OTEL_SERVICE_NAME_ACCOUNTING=test OTEL_COLLECTOR_ENDPOINT_GRPC=http://127.0.0.1:1 /home/opc/workspace/home-hub/services/accounting-service/.venv/bin/python -m pytest <args>`
  Below, this prefix is written `$PYTEST`.
- Frontend tests: the controller sets up once before Task 10. It symlinks `frontend/node_modules` to
  `/home/opc/workspace/home-hub/frontend/node_modules`, after checking that `package-lock.json` is identical. It also
  writes a placeholder `frontend/src/environments/environment.ts`, untracked and gitignored, with no token. Run
  `cd frontend && npx ng test --watch=false --include <spec>`. Never run `npm run config`, `set-env.js`,
  `npm run build` or `npm start` in the worktree.
- Fixtures are fictional: card names like 「測試卡」 and ids such as 101/102/301. No owner card names, rule ids or
  amounts in code, tests, commits, the PR or Multica briefs.
- Money is `Numeric(20,4)`. Every Decimal written is quantized to 4 places (`Decimal("0.0001")`).
- Dates use Asia/Taipei: `ledger_service._today()` for "today"; jobs use `TZ_NAME = "Asia/Taipei"`.
- Error shapes as today: `ValidationError(field, msg)` → 422; `CodedConflictError(code, msg)` / `ConflictError`
  → 409; `NotFoundError` → 404 (`app/services/errors.py`).
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Conventional commits.
- A new table must be added to `LEDGER_TABLES` in `tests/conftest.py:41-48`. Model and migration must agree for
  `tests/integration/test_migration.py::test_head_schema_matches_the_models`.
- Names fixed by the spec, used verbatim:
  - window `calendar_month`, method `per_unit`, column `unit_spend`
  - states `estimated`, `posted`, `bank_confirmed`, `forfeited`
  - `review_reason` value `reward_posted`, coverage `match_rule` `reward_engine`
  - case hints `reward_surplus` and `reward_early`
  - index `ux_ledger_entry_reward_source_rule`
  - 422 copy "rules sharing a cap must use the same window"
  - 422 copy for `fixed_amount`: "must be greater than 0"

## Plan rulings (where the spec leaves a choice or is silent)

| # | Ruling | Why |
|---|---|---|
| P1 | `delete_entry` keeps refusing reward rows **without** an accrual (MOZE era, `entry_write_service.py:649`). It allows reward rows **with** an accrual, which forfeits the accrual (§5.4). | §5.4 presumes owner deletes; MOZE rows stay history (§3). |
| P2 | A `fixed` rule on a partial refund: estimate = −`fixed_amount` × refund / purchase amount, then `txn_rounding`. `percent` / `per_unit` use the refund amount. | Proportional claw-back; a full refund gives back exactly the purchase's estimate. |
| P3 | Refund `post_on` = `post_on_for(rule, purchase window, refund.entry_date)`. A date already past posts on the next daily run. | Same window as the purchase (§5.1); simplest posting rule. |
| P4 | Rounding modes act on whole units: `round` = ROUND_HALF_UP to 0 places, `floor`, `ceil`; `keep` = 4 places. After `total_rounding`, the window sum is clamped to `≤ cap`. The delta never takes a positive-estimate slot below 0; any leftover delta is dropped. | §5.2.4, P4 fills the edge. |
| P5 | Lead carry 1 (rev 3 after AGENT-396 re-check A/B). A `reward_early` case offers **`accept_early_reward` and `dismiss` only**; `create_entry` and `create_system_entry` stay off even though `_system_line` is true for reward lines (`reconciliation_actions/__init__.py:63-64`). Context: `early_accrual_ids`, `rule_ids`, `default_accrual_ids` (the one early accrual whose `estimate` is closest to the line flow, then `post_on`, id) and `note` = 「銀行提早入帳：請修正規則的入帳日，或按「接受提早入帳」」. **The action** (owner actor only; params `accrual_ids`, default `default_accrual_ids`):<br>(1) 409 `accrual_not_estimated` when any named id is not in the case's `early_accrual_ids` or is no longer an unconfirmed early accrual (`estimated`, or `posted` and not yet `bank_confirmed`; ledger ruling, Task 9), so the whole action is refused; an accepted job-posted accrual's reward row takes the line's `posted_date`;<br>(2) 409 `line_outside_period` unless `period_start <= line.posted_date <= period_end`;<br>(3) the params are recorded on the `reconciliation_action` row (`models/statements.py:351`), never on the case (`_write_cases` keys on context);<br>(4) `rec.reconcile(db, statement.id, locked=True, barrier_wait=False)` runs in the same transaction, and the case's status and version are restored if that reconcile superseded it, as `link_installment_plan` does (`case_actions.py:149-156`).<br>Bank wins (Task 9) reads the applied, unreverted `accept_early_reward` rows of the statement as an **accepted map** `line_id → accrual_ids`. Each accepted line is paired only with its named ids, which are posted on the reconcile session with `on = line.posted_date`. Accepted ids are then left out of the general candidate set, and the statement's other reward lines follow §7 on what remains, so an untouched `reward_early` line keeps its case. Other early accruals keep their formula `post_on`. Revert of the action → 409 `not_revertible`. | The reward is booked once, through the accrual, and never left at 0. Posting happens inside reconcile, in reconcile's own lock order (P12), so a later rate PUT cannot move it back. Pairing by the accepted map replaces the reviewer's "hold until every early line is decided": each line is pinned to its own ids, so one accept cannot claim another line. |
| P6 | Lead carry 2: a rule PUT or a `closing_day` change sets `needs_recheck = true` on the card's statements that hold an open `reward_early` case. The re-run then supersedes the case (`_write_cases`), because the line is now pulled and claimed. | Reuses the existing recheck flag (`models/statements.py:167`). |
| P7 | Daily job `CronTrigger(hour=6, minute=15, timezone="Asia/Taipei")`, id `reward_post_daily`. Startup run after 30 s. Retry every 10 min on `busy`/`crashed`. Lock key `REWARD_POST_JOB_LOCK_KEY = 0x52574450` ("RWDP") in `schedule_locks.py`. Gated by `schedule_job.is_enabled()`. | Runs after auto-pay (06:00) and follows its pattern (§5.1). |
| P8 | Shared cycle helpers live in `app/services/cycles.py`, which imports only the stdlib. `notify/cycles.py` re-imports `Period`, `statement_period` and `shift_period` from it and keeps the rest. Do not add `app/services/__init__.py`. | `app/__init__.py` is empty and `app/services/` is a namespace package, so notify can import the module without pulling in SQLAlchemy. |
| P9 | `RewardRuleIn` stays for POST. A new `RewardRuleUpdateIn(RewardRuleIn)` overrides `fixed_amount: NonNegativeMoney \| None` and is used by the PUT route. | Spec §11 leaves the split to the builder. |
| P10 | `review_reason` is a free `String(32)`, not an enum. | One value today; no enum migration for future reasons. |
| P12 | **Lock order**, on every path: (1) the statement row (reconcile and actions) or the purchase / candidate ledger rows, as today; (2) accrual rows, every one the path needs, in **one** `SELECT … WHERE id IN (…) ORDER BY id FOR UPDATE`; (3) reward ledger rows (update or delete). `recompute_window(db, rule_id, window_start, *, locked=False)` takes the window's (group's) accrual locks itself; with `locked=True` it locks nothing and asserts that the caller holds them. `post_accrual(..., locked=False)` calls `recompute_window` before touching its accrual. Bank wins locks the union of its candidates' window accruals once, then calls `post_accrual(..., locked=True)`. An engine reward delete (re-check finding 5) does one window `SELECT … ORDER BY id FOR UPDATE` of the accrual's window (group), forfeits, then calls `locked_with_legs` (`entry_write_service.py:642`), then `recompute_window(..., locked=True)`. | One global order, so the daily job, reconcile and deletes cannot deadlock. |
| P11 | Engine reward rows get `source = "rule"` (existing `ENTRY_SOURCES` value, `models/ledger.py:45`; no enum change), name = the rule name, category = `system_category_id(db, "reward")`. "Has an accrual" is what marks an engine row (§4.1), not the source. | Rule-generated rows; avoids an enum migration. |

## Review Focus

1. **Purchase edited after its reward posted.** Owner fixes the amount the week after posting: the posted reward
   keeps its amount, the purchase shows `needs_review` with reason `reward_posted`, and a later description edit does
   not clear it. Test in Task 6.
2. **Foreign-currency spend on a TWD card.** The accrual uses the entry's TWD `amount`, not `original_amount`.
   Test in Task 5.
3. **Two reward lines and one engine row on a statement.** One line is claimed. The other becomes a
   `reward_surplus` case, inbox question `other`, and no owner rule books it. Test in Task 9.
4. **Statement re-run after the bank figure was applied.** It rebuilds identical claims, coverage rows are kept, and
   no ledger amount changes. Test in Task 8.
5. **A rule edited from `statement_cycle` to `calendar_month` with posted accruals in place.** Posted ones keep
   their window. Estimated ones move, and their old window recomputes. Test in Task 5.

---

### Task 1: Schema: `reward_accrual`, enums, columns, ledger index

**Files:**
- Create: `services/accounting-service/alembic/versions/a3d9e5c1f7b2_reward_accrual.py` (down_revision
  `e9b1c3d7a5f2`)
- Modify: `services/accounting-service/app/models/ledger.py` (tuples :51-52, enums :65-66, `RewardRule` :219-250,
  `LedgerEntry` :253-314, new `RewardAccrual`), `app/models/__init__.py`
- Modify: `services/accounting-service/tests/conftest.py:41-48` (`LEDGER_TABLES` += `reward_accrual`)
- Test: `services/accounting-service/tests/integration/test_reward_accrual_schema.py`

**Interfaces:**
- Produces:
  - `REWARD_METHODS = ("percent", "fixed", "per_unit")`, `REWARD_WINDOWS = ("statement_cycle", "calendar_month")`
  - `REWARD_ACCRUAL_STATES = ("estimated", "posted", "bank_confirmed", "forfeited")`
  - `RewardRule.unit_spend` (Numeric(20,4), nullable); `LedgerEntry.review_reason` (String(32), nullable)
  - `class RewardAccrual` with columns:
    - `id`
    - `source_entry_id` (FK `ledger_entry.id`, ON DELETE SET NULL, nullable)
    - `rule_id` (FK `reward_rule.id` RESTRICT, not null)
    - `window_start`, `window_end` (Date, not null), `post_on` (Date, nullable)
    - `estimate`, `amount` (Numeric(20,4), not null)
    - `state` (enum `reward_accrual_state`, default `estimated`)
    - `ledger_entry_id` (FK `ledger_entry.id` SET NULL, unique, nullable)
    - `confirmed_statement_id` (FK `account_statement.id` SET NULL, nullable)
    - `updated_at` (timestamptz, server default now)

    Unique `ux_reward_accrual_source_rule` (`source_entry_id`, `rule_id`); indexes `ix_reward_accrual_rule_window`
    (`rule_id`, `window_start`), `ix_reward_accrual_state_post_on` (`state`, `post_on`).
  - `LedgerEntry.__table_args__` += `Index("ux_ledger_entry_reward_source_rule", "reward_source_entry_id",
    "reward_rule_id", unique=True, postgresql_where=text("reward_source_entry_id IS NOT NULL AND reward_rule_id IS NOT NULL"))`

- [ ] **Step 1: Write failing tests** in `test_reward_accrual_schema.py`:
  - `test_accrual_round_trip`: seed a card + rule + entry, insert a `RewardAccrual` (estimate 10, amount 10). Read
    it back: `state == "estimated"`, `updated_at` is not None.
  - `test_accrual_unique_per_source_rule`: a second accrual with the same (source, rule) raises `IntegrityError`.
    Two accruals with `source_entry_id=None` and the same rule do not.
  - `test_source_delete_sets_null`: delete the source entry with SQL → `source_entry_id is None`, row survives.
  - `test_ledger_reward_pair_unique`: two reward entries with the same non-null (`reward_source_entry_id`,
    `reward_rule_id`) raise `IntegrityError`; with `reward_rule_id=None` both insert.
  - `test_new_enum_values_accepted`: a rule with `window="calendar_month"`, `method="per_unit"`, `unit_spend=20`
    flushes.
- [ ] **Step 2:** `$PYTEST tests/integration/test_reward_accrual_schema.py -v` → FAIL (no `RewardAccrual`).
- [ ] **Step 3: Write the migration.**
  - Enum values follow `d5e1f7a3b9c2_settlement_booking.py:27-32`: `ALTER TYPE reward_window ADD VALUE IF NOT
    EXISTS 'calendar_month'` and `ALTER TYPE reward_method ADD VALUE IF NOT EXISTS 'per_unit'` in
    `op.get_context().autocommit_block()`.
  - Create `reward_accrual_state` and the table, and add the two columns.
  - Ledger index preflight: `SELECT count(*) FROM (SELECT 1 FROM ledger_entry WHERE reward_source_entry_id IS NOT
    NULL AND reward_rule_id IS NOT NULL GROUP BY 1,2 HAVING count(*) > 1)`, written with real column names. When 0,
    `op.create_index(..., unique=True, postgresql_where=...)`. Otherwise print
    `"ux_ledger_entry_reward_source_rule skipped: N duplicate pairs"` plus the entry ids to stdout, and skip.
  - Downgrade drops the table, columns, index and state enum, and leaves the enum values (as `d5e1f7a3b9c2:6`).
  - Then update the models to match.
- [ ] **Step 4:** run Step 2's command plus
  `$PYTEST tests/integration/test_migration.py tests/integration/test_models_schema.py -v` → PASS.
- [ ] **Step 5:** commit `feat(accounting): reward_accrual table, calendar_month, per_unit, review_reason`.

### Task 2: Shared cycles, reward windows and posting dates (pure)

**Files:**
- Create: `services/accounting-service/app/services/cycles.py`,
  `services/accounting-service/app/services/reward_windows.py`
- Modify: `services/accounting-service/notify/cycles.py` (import `Period`, `statement_period` and `shift_period`
  from `app.services.cycles`; delete their local bodies and the now-unused private helpers they alone used)
- Test: `services/accounting-service/tests/unit/test_reward_windows.py`; existing `tests/notify/test_cycles.py`
  must pass unchanged.

**Interfaces:**
- Produces:
  - `app.services.cycles`: `Period = tuple[date, date]`,
    `statement_period(closing_day: int | None, today: date) -> Period`,
    `shift_period(closing_day: int | None, period: Period, delta: int) -> Period`,
    `calendar_month(day: date) -> Period`.
  - `app.services.reward_windows`:
    - `window_for(window: str, closing_day: int | None, entry_date: date) -> Period`: `statement_cycle` →
      `statement_period(closing_day, entry_date)`; `calendar_month` → `calendar_month(entry_date)`.
    - `post_on_for(posting: str, delay_days: int | None, post_month_offset: int | None, post_day: int | None, window: Period, entry_date: date) -> date | None`
      per §4.4:
      - `after_window` → `post_day` of the month `post_month_offset` months after `window[1]`'s month, clamped to
        that month's last day, then `max(..., window[1])`;
      - `after_transaction` → `entry_date + delay_days`;
      - `manual` → None.

- [ ] **Step 1: Failing tests:**
  - `test_window_statement_cycle_uses_closing_day`: `window_for("statement_cycle", 15, date(2026,11,20))` →
    `(date(2026,11,16), date(2026,12,15))`.
  - `test_window_no_closing_day_is_calendar_month`: `window_for("statement_cycle", None, date(2026,2,10))` →
    `(date(2026,2,1), date(2026,2,28))`.
  - `test_window_calendar_month`: `window_for("calendar_month", 15, date(2026,11,20))` → Nov 1–30.
  - `test_post_after_window_clamps_day`: offset 1, day 31, window ending 2027-01-15 → `date(2027,2,28)`.
  - `test_post_after_window_raised_to_window_end`: offset 0, day 5, window ending 2026-11-30 → `date(2026,11,30)`.
  - `test_post_after_transaction`: delay 3, entry 2026-11-29 → `date(2026,12,2)`.
  - `test_post_manual_is_none`.
- [ ] **Step 2:** `$PYTEST tests/unit/test_reward_windows.py -v` → FAIL.
- [ ] **Step 3:** Move the period functions into `app/services/cycles.py` verbatim (stdlib only). Implement
  `reward_windows.py`, and point `notify/cycles.py` at the shared module.
- [ ] **Step 4:** `$PYTEST tests/unit/test_reward_windows.py tests/notify -q` → PASS.
- [ ] **Step 5:** commit `refactor(accounting): shared cycles module; reward windows and posting dates`.

### Task 3: Rule API: `calendar_month`, `per_unit`, #289, shared-cap window

**Files:**
- Modify: `app/schemas/writes.py:256-290` (`method` and `window` literals; `unit_spend: Money | None = None`;
  new `RewardRuleUpdateIn`), `app/schemas/ledger.py:17-41` (`RewardRuleOut.method`/`window` literals,
  `unit_spend: Decimal | None`), `app/services/ledger_service.py:45` (`REWARD_RULE_FIELDS` += `unit_spend`)
- Modify: `app/services/reward_rule_service.py`:
  - `_WRITE_FIELDS` :19-23 += `unit_spend`
  - `_check_payload` :40 gains `current: RewardRule | None`
  - `_apply_share_cap` :100-111
  - `update` :137
- Modify: `app/routers/accounts.py:76` (PUT body `RewardRuleUpdateIn`)
- Test: `tests/unit/test_reward_rule_schema.py`, `tests/integration/test_reward_rules_api.py`

**Interfaces:**
- Produces:
  - `RewardRuleUpdateIn`
  - `reward_rule_service.group_cap(db, rule: RewardRule) -> Decimal | None`: the smallest non-null `total_cap` in
    the rule's `shared_cap_id` group (the rule alone when ungrouped); None when all are null.
  - `reward_rule_service.group_rule_ids(db, rule: RewardRule) -> list[int]`: the group's rule ids, or `[rule.id]`
    when ungrouped.

- [ ] **Step 1: Failing tests** (API, using `_body`/`_post` helpers):
  - `test_per_unit_requires_unit_spend_and_forbids_rate`:
    - POST `method="per_unit"` without `unit_spend` → 422 `unit_spend`;
    - with `unit_spend=20, rate=1` → 422 `rate`;
    - with `unit_spend=20` → 201, response `unit_spend == "20.0000"` (match the existing Decimal serialization).
  - `test_percent_forbids_unit_spend` → 422 `unit_spend`.
  - `test_calendar_month_window_round_trip` → 201 and `window == "calendar_month"`.
  - `test_put_keeps_stored_zero_fixed_amount`: seed a rule `method="fixed", fixed_amount=0` with SQL (placeholder
    shape). PUT the full body with `fixed_amount=0` and a new description → 200. PUT `method="percent", rate=1,
    fixed_amount=0` → 422 `fixed_amount`.
  - `test_post_refuses_zero_fixed_amount` → 422 `fixed_amount`.
  - `test_put_zero_fixed_amount_on_nonzero_rule_refused`: stored 5, PUT 0 → 422 `fixed_amount` "must be greater
    than 0".
  - `test_shared_cap_requires_same_window`: rule A `statement_cycle`, B `calendar_month`. POST/PUT B with
    `share_cap_with=A` → 422 `share_cap_with` "rules sharing a cap must use the same window". With A and B grouped
    (same window), PUT A `window="calendar_month"` → 422 `window`.
  - `test_group_cap_is_minimum`: A cap 300, B cap 200, C cap None, grouped → `group_cap == Decimal("200")`. All
    None → None.
- [ ] **Step 2:** `$PYTEST tests/unit/test_reward_rule_schema.py tests/integration/test_reward_rules_api.py -v`
  → new tests FAIL.
- [ ] **Step 3:** Implement. `_check_payload(payload, current)` raises per §4.2. The zero check: 0 is allowed only
  when `current is not None and current.method == "fixed" and current.fixed_amount == 0 and payload.method == "fixed"`.
  The window check runs after `_apply_share_cap`, over the group's rules with the payload's window applied to this
  rule.
- [ ] **Step 4:** Step 2's command → PASS; also `$PYTEST tests/integration/test_reward_rules_overview.py -q`.
- [ ] **Step 5:** commit `feat(accounting): per_unit and calendar_month rules; #289 placeholder edits; shared-cap window`.

### Task 4: Reward math (pure): estimates, allotment, rounding

**Files:**
- Create: `services/accounting-service/app/services/reward_math.py`
- Test: `services/accounting-service/tests/unit/test_reward_math.py`

**Interfaces:**
- Produces:
  - `apply_rounding(value: Decimal, mode: str) -> Decimal` (P4).
  - `estimate(*, method: str, rate: Decimal | None, fixed_amount: Decimal | None, unit_spend: Decimal | None, txn_rounding: str, amount: Decimal, refund_of: Decimal | None = None) -> Decimal`.
    `amount` is the entry's account-currency amount (sign ignored). `refund_of` is the refunded purchase's |amount|;
    when given, the result is negative and `fixed` prorates (P2).
  - `@dataclass(frozen=True) class Slot: key: int; estimate: Decimal; frozen: Decimal | None` (`frozen` is set for a
    frozen accrual's counted amount, None for an estimated one; slots arrive pre-sorted by source `entry_date`, id).
  - `allot(slots: Sequence[Slot], *, cap: Decimal | None, external_used: Decimal, total_rounding: str) -> dict[int, Decimal]`:
    amounts for the estimated slots only, per §5.2.2–§5.2.4. `external_used` = the no-accrual reward rows (§5.3).

- [ ] **Step 1: Failing tests:**
  - `test_estimate_percent`: rate 1.2, amount 512, keep → `Decimal("6.1440")`; `round` → `Decimal("6")`.
  - `test_estimate_per_unit_floor`: unit 20, amount 1999, floor → `Decimal("99")`.
  - `test_estimate_fixed`: fixed 50 → 50.
  - `test_estimate_refund_negative_and_prorated_fixed`: fixed 50, refund 300 of 1200 → `Decimal("-12.5000")`
    (keep). Percent rate 2, refund 300 → `Decimal("-6.0000")`.
  - `test_allot_uncapped_passes_estimates`.
  - `test_allot_cap_in_order`: cap 100, estimates 60, 60, 60 → 60, 40, 0.
  - `test_allot_frozen_counts_first`: frozen slot 80 + estimated 60 under cap 100 → estimated gets 20.
  - `test_allot_external_used`: external 90, cap 100, estimate 30 → 10.
  - `test_allot_negative_in_full_raises_remaining`: cap 100, estimates 100, −20, 50 → 100, −20, 20.
  - `test_allot_total_rounding_delta_on_last_estimated`: estimates 1.4, 1.4, 1.4 (keep), total round → sum 4.2 →
    4, delta −0.2 on the last → 1.4, 1.4, 1.2.
  - `test_allot_total_rounding_never_exceeds_cap`: cap 10, estimates 6.6, 3.3 (keep), total ceil → sum stays
    `≤ 10` (allotted 6.6, 3.3 → sum 9.9 → ceil 10, fits → last 3.4).
  - `test_allot_no_estimated_no_adjustment`: frozen only → `{}`.
  - `test_allot_negative_delta_stops_at_zero`: estimates 0.3, 0.3 (keep), total floor → sum 0.6 → 0. The delta −0.6
    takes the last slot to 0 (not −0.3); the leftover −0.3 is dropped → 0.3, 0.
  - `test_allot_rounding_clamped_to_cap`: cap 10, frozen 9.6, estimate 0.4 (keep), total ceil → 10 fits → 0.4.
    Cap 10, frozen 9.7, estimate 0.2, total round → sum 9.9 → 10, so 0.3 lands on the last slot. Cap 9.8 with the
    same input → the rounded sum is clamped to 9.8 → 0.1.
- [ ] **Step 2:** `$PYTEST tests/unit/test_reward_math.py -v` → FAIL.
- [ ] **Step 3:** Implement with `decimal` only (ROUND_HALF_UP, ROUND_FLOOR, ROUND_CEILING). Quantize results to 4
  places.
- [ ] **Step 4:** Step 2's command → PASS.
- [ ] **Step 5:** commit `feat(accounting): reward math (estimates, cap allotment, rounding)`.

### Task 5: Engine: sync accruals and recompute windows

**Files:**
- Create: `services/accounting-service/app/services/reward_engine.py`
- Test: `services/accounting-service/tests/integration/test_reward_engine.py`

**Interfaces:**
- Consumes: `reward_windows.window_for`, `post_on_for` (Task 2); `reward_math.estimate`, `allot`, `Slot`
  (Task 4); `reward_rule_service.group_cap`, `group_rule_ids` (Task 3); `RewardAccrual` (Task 1).
- Produces:
  - `FROZEN = ("posted", "bank_confirmed", "forfeited")`
  - `card_closing_day(db, account: Account) -> int | None`: own `closing_day`, else the `combined_account_id`
    parent's, else None.
  - `sync_entry(db, entry_id: int) -> None`: for a purchase, one accrual per linked rule (skipping placeholder-shape
    rules and (purchase, rule) pairs that already have a no-accrual reward row, §5.3). For a `refund` with
    `refunds_entry_id`, one per rule linked to the purchase (P2/P3). Estimated accruals of unlinked rules are
    deleted; estimated ones are re-bucketed. Every touched window (old and new) is recomputed. Frozen accruals are
    never changed. For a purchase, it ends by calling `sync_entry` on each row with `refunds_entry_id` = the
    purchase, so refunds follow its links, date and amount (§5.1, P2's base).
  - `recompute_window(db, rule_id: int, window_start: date, *, locked: bool = False) -> None` (P12): group-aware (`group_rule_ids`). It reads
    frozen amounts per §5.2.2 (ledger amount for `bank_confirmed`/`forfeited`, 0 without a row) and the external
    no-accrual rows of the group whose source purchase `entry_date` is in the window. It writes `amount` on
    estimated accruals.
  - `recompute_rules(db, rule_ids: Iterable[int]) -> None`: re-bucket + re-estimate every estimated accrual of those
    rules, then recompute.
  - `recompute_account(db, account_id: int) -> None`: `recompute_rules` over the card's rules (closing-day change).
  - `delete_estimated_for(db, entry_ids: Iterable[int]) -> set[tuple[int, date]]`: deletes the estimated accruals of
    these source entries and returns the (rule_id, window_start) pairs to recompute afterwards.

- [ ] **Step 1: Failing tests** (fictional card closing_day 15, percent 1 % rule, cap where noted):
  - `test_linked_purchase_creates_accrual`: 1,000 spend on 11-20 linked → one accrual, window 11-16..12-15,
    estimate 10, amount 10, `post_on` per the rule.
  - `test_unlinked_purchase_no_accrual`.
  - `test_placeholder_rule_no_accrual`: rule `fixed`, `fixed_amount=0` → none.
  - `test_moze_reward_row_blocks_accrual`: a reward row with (`reward_source_entry_id`=purchase, `reward_rule_id`)
    and no accrual → none.
  - `test_cap_shared_across_window`: cap 15, two 1,000 spends → amounts 10 and 5.
  - `test_moze_rows_count_as_used`: no-accrual reward row 12 for an earlier purchase in the same window, cap 15,
    new 1,000 spend → amount 3.
  - `test_shared_cap_group_min_cap`: A (cap 20) and B (cap 30) grouped. Spends on A and B, 1,500 each → total
    allotted 20, in entry order.
  - `test_relink_removes_estimated`: links → [] → accrual gone, window recomputed.
  - `test_frozen_accrual_untouched_by_sync`: set state `posted`, amount 10. Change the purchase amount → accrual
    unchanged.
  - `test_refund_accrual_in_purchase_window`: purchase 11-20 1,000, refund 300 on 12-20 (next cycle) → refund
    accrual window 11-16..12-15, estimate −3.
  - `test_foreign_spend_uses_account_amount` (Review Focus 2): TWD card entry with `original_amount=100`,
    `original_currency="USD"`, `amount=3200` → estimate 32.
  - `test_rule_window_change_moves_estimated_only` (Review Focus 5): one posted and one estimated accrual. PUT
    rule `window="calendar_month"` (via `recompute_rules`) → posted keeps the old window, estimated moves, and the
    old window's estimated remainder is recomputed.
  - `test_combined_card_uses_parent_closing_day`.
  - `test_refund_follows_purchase_relink` (finding 2): purchase + refund on rule A; relink the purchase to rule B →
    the refund's estimated accrual moves to B, A's is gone. A purchase date move into the next cycle moves the refund
    accrual's window with it.
  - `test_bank_confirmed_and_forfeited_count_ledger_amount` (finding 9): cap 20, a `bank_confirmed` accrual whose
    ledger row is 8 (accrual amount 10) and a `forfeited` one with no row → a new 1,500 spend gets 12. The posted
    engine row is not also counted as external `used`.
- [ ] **Step 2:** `$PYTEST tests/integration/test_reward_engine.py -v` → FAIL.
- [ ] **Step 3:** Implement. Sort slots by the source row's `entry_date`, then id. Orphan frozen accruals (null
  source) count as frozen. Lock order: take accrual rows `FOR UPDATE` in id order inside the caller's transaction.
  Never commit.
- [ ] **Step 4:** Step 2's command → PASS.
- [ ] **Step 5:** commit `feat(accounting): reward engine sync and window recompute`.

### Task 6: Engine hooks in write paths, review flag, deletes, project moves

**Files:**
- Modify: `app/services/entry_write_service.py`:
  - `insert_prepared` :320, `apply_prepared_update` :353
  - `delete_entries_cascade` :524, `delete_entry` :625 (guard :649 per P1)
- Modify: `app/services/transfer_service.py` (`_write_leg_extras` :127); `app/services/settlement_service.py`
  (`refund` :94); `app/services/split_service.py` (`_apply_meta` :319, `_apply_keep` :326, only for flag
  restoration)
- Modify: `app/services/reward_rule_service.py` (`update` :137 → `reward_engine.recompute_rules` + P6);
  `app/services/settings_service.py` (`update_account` :719: when `closing_day` changes, `recompute_account` on
  the account **and on every child whose `card_closing_day` resolves through it** (`combined_account_id`, own
  `closing_day` null), + P6 for each)
- Modify: `app/services/transfer_service.py` `update_transfer` :178: capture `before_key`, `before_project_id` and
  `before_flag` per leg **before** the setattr loop (:191-193), and pass them to `after_write`
- Modify: `app/services/split_service.py` `_apply_meta` :319: a project change there also moves reward projects
  (§5.1)
- Modify: `app/schemas/ledger.py` (`EntryOut` += `review_reason: str | None`)
- Test: `tests/integration/test_reward_engine_hooks.py`

**Interfaces:**
- Consumes: Task 5 functions.
- Produces:
  - `reward_engine.financial_key(entry: LedgerEntry, rule_ids: Collection[int]) -> tuple`: (amount, entry_date,
    account_id, sorted rule ids).
  - `reward_engine.after_write(db, entry: LedgerEntry, *, before_key: tuple | None, before_project_id: int | None, before_flag: tuple[bool, str | None]) -> None`:
    called after `_apply` and `write_rule_links`. It syncs, then sets or restores the flag per §5.1:
    - the financial key changed and the entry has a frozen accrual → `needs_review = True`,
      `review_reason = "reward_posted"`;
    - the key is unchanged and the prior reason was `reward_posted` → the prior flag is restored.

    It also moves the project of the reward rows (§5.1) when the project changed.
  - `reward_engine.mark_early_rechecks(db, account_id: int) -> None` (P6).

- [ ] **Step 1: Failing tests:**
  - `test_create_entry_with_rules_accrues` (API POST `/entries` with `reward_rule_ids`).
  - `test_put_after_post_flags_and_keeps_amount` (Review Focus 1): accrual set `posted` with a reward row. PUT
    amount → accrual amount unchanged, entry `needs_review is True`, `review_reason == "reward_posted"`. Then PUT
    with only `description` changed → still flagged.
  - `test_flag_clears_when_reward_row_edited` (PUT the reward row → the purchase flag clears). The bank-wins clear
    is `test_flag_clears_on_bank_confirm` in Task 8.
  - `test_transfer_leg_rules_accrue`.
  - `test_refund_route_creates_negative_accrual` (POST `/entries/{id}/refund`).
  - `test_delete_purchase_drops_estimated_keeps_posted`: one estimated (rule A), one posted (rule B). DELETE
    purchase → A gone; B survives with `source_entry_id None`; B's reward row flagged `reward_posted`.
  - `test_delete_engine_reward_row_forfeits` (P1): DELETE the reward row → 204, accrual `forfeited`,
    `ledger_entry_id None`.
  - `test_delete_moze_reward_row_still_refused` → 409.
  - `test_project_change_moves_reward_project`: reward row project null → becomes the new project. A reward row
    with a different project stays.
  - `test_rule_put_recomputes_estimated`: rate 1 → 2 → estimate doubles.
  - `test_closing_day_change_rebuckets`.
  - `test_split_paths_accrue`: create_split with a member linked to a rule → accrual for the member.
  - `test_parent_closing_day_change_rebuckets_children` (finding 3): child card with `closing_day` null and
    `combined_account_id` = parent; PUT the parent's `closing_day` → the child's estimated accruals move.
  - `test_transfer_update_after_post_flags` (finding 4): a posted accrual on a transfer leg; PUT the transfer amount →
    `review_reason == "reward_posted"`, accrual amount unchanged.
  - `test_project_change_moves_old_project_reward` (finding 9): a reward row whose project equals the purchase's old
    project moves to the new one; the same through a split `keep_full` member edit (`_apply_meta`).
  - `test_engine_reward_delete_lock_order` (P12, re-check 5; lead carry): record every `FOR UPDATE` statement with a
    SQLAlchemy `before_cursor_execute` listener during DELETE of an engine reward row → order is schedule locks,
    `lock_group` (`entry_group`), then the accrual-window `SELECT … ORDER BY id` (placed after `lock_group`, before
    `locked_with_legs`), the `ledger_entry` locks come after it, and no accrual lock follows a ledger lock. The
    same listener over `post_accrual` (daily-job path) and bank wins `prepare` → accrual locks are one statement, in
    id order, before any reward-row update. Those two halves land with their code: `test_post_accrual_lock_order`
    in Task 7 and `test_prepare_lock_order` in Task 8.
- [ ] **Step 2:** `$PYTEST tests/integration/test_reward_engine_hooks.py -v` → FAIL.
- [ ] **Step 3:** Implement the hooks. `insert_prepared` / `apply_prepared_update` call `after_write` after
  `write_rule_links`. Capture `before_*` at the start of `apply_prepared_update`. `delete_entries_cascade` calls
  `delete_estimated_for` before deleting, flags the surviving frozen accruals' reward rows, and recomputes after.
  `refund()` calls `sync_entry(db, entry.id)` after its flush.
- [ ] **Step 4:** Step 2's command, then the regression suites
  `$PYTEST tests/integration/test_entry_writes.py tests/integration/test_entries_api.py tests/integration/test_settlements.py tests/integration/test_splits.py tests/integration/test_reward_rules_api.py -q`
  → PASS.
- [ ] **Step 5:** commit `feat(accounting): run the reward engine on entry, rule and account writes`.

### Task 7: Posting: per-accrual post, daily job, manual endpoint

**Files:**
- Create: `app/services/reward_posting.py`, `app/services/reward_post_job.py`, `app/routers/reward_accruals.py`
- Modify: `app/services/schedule_locks.py:18-20` (`REWARD_POST_JOB_LOCK_KEY = 0x52574450`), `app/main.py:13,22-25,40-57`
  (router + start/stop)
- Test: `tests/integration/test_reward_posting.py`, `tests/unit/test_reward_post_job_scheduler.py`

**Interfaces:**
- Produces:
  - `reward_posting.post_accrual(db, accrual_id: int, *, on: date | None = None) -> str`. It returns `"posted"`,
    `"forfeited"` (amount 0, no row) or `"skipped"` (not `estimated`, or `ledger_entry_id` set).
    - Calls `recompute_window` first (it takes the window's accrual locks in id order, P12), re-reads the accrual
      under that lock, skips per the guard, then inserts the reward row per §5.1. Signature
      `post_accrual(db, accrual_id, *, on=None, locked=False)`; with `locked=True` it skips the recompute's locking
      (bank wins already holds the window, P12): account =
      `reward_account_id` or the rule's card; currency = that account's; `entry_date = posted_date = on or post_on`;
      `reward_source_entry_id`, `reward_rule_id`; project = rule `reward_project_id` else the purchase's; P11 fields.
    - Sets `ledger_entry_id` and `posted`. Never commits.
  - `reward_post_job.run(engine, trigger: str, *, today: date | None = None) -> dict`. It returns
    `{"status": "ok"|"busy"|"crashed", "posted": n, "forfeited": n, "skipped": n}`.
    - Takes the advisory lock as `auto_pay_job.run` (:206-221).
    - Each due accrual (`state='estimated' AND post_on <= today`, id order) runs in its own session + commit.
  - `reward_post_job.build_scheduler(engine)`, `start(engine)`, `stop()` (P7).
  - `POST /reward-accruals/{accrual_id}/post` body `{"on": date | null}`: write scope, 200 `{"result": ...,
    "ledger_entry_id": ...}`. 404 when the accrual is missing; 409 `accrual_not_estimated`, or when the rule's
    posting is not `manual`.

- [ ] **Step 1: Failing tests:**
  - `test_post_accrual_lock_order` (P12, lead carry): a `before_cursor_execute` listener over `post_accrual` → the
    accrual locks are one `SELECT … WHERE id IN (…) ORDER BY id FOR UPDATE`, before any reward-row insert or update.
  - `test_post_creates_reward_row`: fields as above, accrual `posted`, `ledger_entry_id` set, row currency = reward
    account currency (a points account in `PTS`).
  - `test_post_zero_amount_forfeits_without_row` (past cap).
  - `test_post_twice_is_skipped`: second call → `"skipped"`, one ledger row.
  - `test_job_posts_due_only`: due and future accruals → only due posted; result counts.
  - `test_job_busy_when_lock_held`: hold `REWARD_POST_JOB_LOCK_KEY` via `tests/helpers.advisory_locks` →
    `status == "busy"`, nothing posted.
  - `test_manual_endpoint_posts_manual_rule` and `test_manual_endpoint_refuses_non_manual` (409).
  - `test_post_project_inheritance`: rule project wins over the purchase's; else the purchase's.
  - `test_job_and_reconcile_same_window_no_deadlock` (P12): `tests/helpers.race` the job's `post_accrual` and a
    reconcile of a statement whose candidates share that window → both commit or one returns `skipped`; no deadlock
    error.
  - `test_scheduler_triggers` (unit): job id `reward_post_daily`, hour 6, minute 15, tz Asia/Taipei;
    `start()` returns without scheduling when `is_enabled()` is false.
- [ ] **Step 2:** `$PYTEST tests/integration/test_reward_posting.py tests/unit/test_reward_post_job_scheduler.py -v` → FAIL.
- [ ] **Step 3:** Implement, following `auto_pay_job.py` structure (`build_scheduler` :272, `start` :285, `_after_run`
  retry :247-264).
- [ ] **Step 4:** Step 2's command plus `$PYTEST tests/integration/test_scope_enforcement.py -q` (the new POST route
  joins its mutation matrix) → PASS.
- [ ] **Step 5:** commit `feat(accounting): reward posting job and manual post endpoint`.

### Task 8: Bank wins: candidates, pull, early post, pairing, claims

**Files:**
- Create: `app/services/reward_bank_wins.py`
- Modify: `app/services/reconciliation_service.py`:
  - `load_candidates` :261: exclude reward rows that have an accrual
  - `_match_and_record` :1082: call the step before `pass_two_holders` :1119; append claims to `result.claims`
    before `_write_coverage` :1124; apply outcomes
- Modify: `app/services/policy_service.py:612` (`_single_admissible`: same pulled-line filter)
- Test: `tests/integration/test_reward_bank_wins.py` (reuse `tests/integration/statement_fixtures.py`)

**Interfaces:**
- Produces:
  - `@dataclass class BankWinsPlan`:
    - `pulled_line_ids: set[int]`
    - `claims: list[matching.Claim]`
    - `surplus_line_ids: list[int]`
    - `early: dict[int, list[int]]` (line id → early accrual ids; filled in Task 9)
    - `locked_rows: dict[int, LedgerEntry]` (claimed reward rows, locked)
  - `reward_bank_wins.prepare(db, statement: AccountStatement, accounts: list[int], lines: list[StatementLine], manual_line_ids: set[int]) -> BankWinsPlan | None`.
    It returns None when the candidate set is empty (§7.2). Otherwise it does, in this order:
    - posts estimated candidates via `reward_posting.post_accrual` on the same session (no commit, no advisory
      lock, §7.4);
    - pairs and spreads (§7.5);
    - updates ledger amounts (the bank figure goes **only** on the ledger row; `reward_accrual.amount` keeps the
      pre-bank allotment, spec §4.1);
    - re-selects the claimed reward rows `FOR UPDATE` in id order and returns them as `BankWinsPlan.locked_rows`.
      `_match_and_record` merges them into the `locked` dict passed to `_write_coverage`
      (`reconciliation_service.py:588`). Without that, `_claim_unchanged` (`:477-478`) and `write_claim` cannot see
      the rows (finding 1);
    - sets states and `confirmed_statement_id`, or clears it per §7.6;
    - clears the `reward_posted` flag on the purchases it confirms or forfeits;
    - builds claims with `Representation(kind="reward_engine", rows=(Row(entry_id, "principal", flow), ...), score=Decimal(1), rule="reward_engine", reasons=())`.
  - `reward_bank_wins.pulled_filter(db, statement, accounts, lines) -> set[int]`: the line ids to remove, read-only,
    shared by reconcile and the policy validator. A line stays in the matcher when exactly one no-accrual reward
    row in the population has an equal flow (§7.3).

- [ ] **Step 1: Failing tests** (fictional card; statement period 10-16..11-15; rule posts in period):
  - `test_bank_amount_replaces_engine_amount`: engine row 10, statement reward line 9 → ledger row amount 9,
    accrual `amount` still 10, `bank_confirmed`, coverage active with `match_rule == "reward_engine"`, no
    `bank_only` case, no new reward row.
  - `test_flag_clears_on_bank_confirm`: the purchase had `reward_posted` → cleared after confirm; same for a forfeit.
  - `test_reward_engine_claim_survives_sweep` (finding 9, §7.7): after the first reconcile, run
    `coverage_service.sweep` for the statement → the `reward_engine` coverage rows stay active (the dirty events of
    the amount update predate the claim and equal its snapshot).
  - `test_estimated_candidate_posted_early_in_reconcile`: accrual still estimated with `post_on` in period → posted
    inside reconcile; a forced rollback (raise after `prepare`) leaves it estimated and no row.
  - `test_two_lines_two_rows_pair_in_order`: lines 5 and 7 (ids ascending); rows by `post_on`, id → each line's
    rows sum to that line; `coverage_service.assert_conserved` passes.
  - `test_three_rows_one_line_spread_proportionally`: rows 10, 20, 30, line 30 → 5, 10, 15.
  - `test_no_reward_line_keeps_current_posted`: no line → current-period rows stay `posted`.
  - `test_carried_row_confirmed_next_statement`: unpaid on statement 1, line on statement 2 → confirmed by
    statement 2.
  - `test_carried_row_forfeited_when_assigned_zero`: no line on either → after statement 2, `forfeited`, ledger
    amount 0.
  - `test_first_statement_has_no_carry`: an older posted row with no earlier statement is not a candidate.
  - `test_rerun_rebuilds_identical_claims` (Review Focus 4): reconcile twice → coverage row ids unchanged, ledger
    amounts unchanged.
  - `test_moze_exact_match_stays_in_matcher`: a no-accrual reward row equal to a line → matched by the matcher as
    today, not pulled.
  - `test_empty_candidates_unchanged_behaviour`: card with no accruals → `bank_only` case as today.
  - `test_engine_rows_never_matcher_candidates`: an engine row outside the period with an equal amount is not
    claimed by the matcher.
  - `test_policy_validator_agrees`: `_single_admissible` sees the same claimable lines (no pulled line).
  - `test_prepare_lock_order` (P12, lead carry): a `before_cursor_execute` listener over a reconcile with candidates
    → the statement row lock first, then the candidates' window accruals in one `SELECT … WHERE id IN (…) ORDER BY id
    FOR UPDATE`, then reward ledger row locks/updates; no accrual lock follows a ledger lock.
- [ ] **Step 2:** `$PYTEST tests/integration/test_reward_bank_wins.py -v` → FAIL.
- [ ] **Step 3:** Implement. Candidate sets (a)/(b)/(c) follow §7.1. "Previous statement" = the account's latest
  non-retired statement with `period_end < this.period_start`.
- [ ] **Step 4:** Step 2's command plus
  `$PYTEST tests/integration/test_reconciliation_service.py tests/integration/test_reconciliation_policy.py tests/integration/test_coverage_sweep.py -q`
  → PASS.
- [ ] **Step 5:** commit `feat(accounting): bank wins for engine rewards in reconciliation`.

### Task 9: Bank wins: surplus and early cases, inbox, carry gap

**Files:**
- Modify: `app/services/reward_bank_wins.py` (surplus, early detection), `app/services/reconciliation_service.py`
  (`_desired_cases` :657: `line_unmatched` cases for surplus lines with hint `reward_surplus`; hint `reward_early`
  on unmatched reward lines **only when `prepare` returned None** and early candidates exist (finding 7; surplus
  lines keep `reward_surplus`); `balance_gap` :715-718 context gains `carried_accrual_ids`)
- Modify: `app/services/inbox_service.py`: `classify` :76-77 (`reward_surplus`, `reward_early` → `"other"`; this
  branch stays ahead of the `create_entry` → `bank_only` check at :78-79); `BUTTONS_TO_ACTIONS` :37-44 +=
  `"accept_early": "accept_early_reward"`; `buttons` :108 for `other` adds `accept_early` (needs
  `accept_early_reward`) before `dismiss`; `translate` :450 maps `accept_early` to params
  `{"accrual_ids": context.default_accrual_ids}`; the inbox item carries `note` (from the case context)
- Modify: `app/schemas/inbox.py:94-110` (`InboxItem.note: str | None = None`)
- Not touched (re-check A): `POLICY_ACTIONS` (`policy_service.py:384`), `QUESTION_ACTIONS`
  (`owner_rule_service.py:31`); `learnable()` stays false for `accept_early`
- Modify: `app/services/reconciliation_actions/__init__.py`: `KIND_ACTIONS["line_unmatched"]` :27-28 +=
  `accept_early_reward`; `CONDITIONS` :130-153: on `reward_early` cases only `accept_early_reward` and `dismiss`
  are true (P5), and `accept_early_reward` is false on any other hint; `balance_gap` with `carried_accrual_ids` →
  `dismiss` only (:138-139). There is no `policy_service` edit (finding 6: `:315` is `_anchor_candidate`, already
  None for card statements).
- Create: `AcceptEarlyRewardAction` (name `accept_early_reward`, `case_kinds={"line_unmatched"}`, owner actor
  only, not revertible) in `app/services/reconciliation_actions/ledger_actions.py`, registered like
  `CreateSystemEntryAction` (:206); behaviour per P5, reconcile call as `case_actions.py:149-156`
- Modify: `app/services/reward_bank_wins.py` `prepare`: the accepted map (P5) is paired first; accepted ids leave
  the general candidate set
- Modify (SPA, re-check B): `frontend/src/app/components/accounting/inbox/inbox.model.ts` (`InboxButton` :16-24 +=
  `'accept_early'`; `InboxItem.note?: string | null`), `inbox-panel/inbox-panel.ts` (`OneTap` :51 +=
  `'accept_early'`; `FOLD_BUTTONS.other` :56-61 → `['accept_early', 'dismiss']`; label 「接受提早入帳」; the `note`
  rendered under the line in the folded card), `inbox-view.ts` label map; specs `inbox-panel.spec.ts`
  (`folded other card with accept_early renders 接受提早入帳 and 略過, and shows the note`;
  `accept_early applies one-tap`)
- Test: `tests/integration/test_reward_bank_wins_cases.py`, `tests/unit/test_inbox_service.py`

**Interfaces:**
- Consumes: `BankWinsPlan` (Task 8), `mark_early_rechecks` (Task 6).
- Produces:
  - `reward_bank_wins.early_candidates(db, statement, accounts) -> list[int]`: estimated accrual ids, reward account
    in `accounts`, with `post_on` in `(period_end, shift_period(closing_day, period, 1)[1]]`.
  - Case contexts: `{"hint": "reward_surplus"}`; `{"hint": "reward_early", "early_accrual_ids": [...], "rule_ids":
    [...], "default_accrual_ids": [...], "note": ...}`; balance gap `{"gap": ..., "card": True,
    "carried_accrual_ids": [...]}`.
  - `reward_bank_wins.accepted_map(db, statement) -> dict[int, list[int]]` (P5).

- [ ] **Step 1: Failing tests:**
  - `test_surplus_line_becomes_owner_case` (Review Focus 3): two lines, one row → one claim, one `line_unmatched`
    with hint `reward_surplus`. `inbox_service.classify` → `"other"`. `owner_rule_service.apply_rules` with a learned
    `record` rule for that merchant books nothing.
  - `test_early_credit_is_owner_case_not_bank_only`: no in-period candidates, an estimated accrual with `post_on`
    next cycle, statement reward line → case hint `reward_early`, context `early_accrual_ids`, `rule_ids` and the
    exact P5 `note`. Policy run books nothing. `actions.allowed(case) == ["accept_early_reward", "dismiss"]` (registry
    order). The inbox buttons are `["accept_early", "dismiss"]`.
  - `test_accept_early_reward_books_once` (P5): one early accrual, one line. Apply `accept_early_reward` → in that
    transaction the accrual is `posted` with `post_on` = the line's `posted_date`, then `bank_confirmed` at the bank
    figure; the line is claimed `reward_engine`; the case is resolved by the action (status restored, not left
    superseded); one reward row. A later daily job run posts nothing more.
  - `test_accept_rerun_keeps_claim` (lead carry, AGENT-395): after the accept, a second `reconcile` of the
    statement keeps the same `reward_engine` coverage row ids, posts nothing new and changes no ledger amount.
  - `test_accept_names_one_of_two` (re-check A): two next-cycle accruals, one line; accept the default id → that one
    is `bank_confirmed`; the other stays `estimated` at its formula `post_on` and is a candidate (a) on the next
    statement. Neither statement gets `create_system_entry` or a `bank_only` case.
  - `test_accept_first_of_two_lines` (re-check A): two `reward_early` lines; accept the first → only its line is
    claimed; the second case stays `reward_early` and still offers `accept_early_reward`; accepting it claims its
    own ids.
  - `test_accept_then_rate_put_keeps_line_date` (re-check A): accept, then PUT the rule's rate → the posted
    accrual keeps `post_on` = line date and its amount (frozen).
  - `test_accept_refuses_line_outside_period`: `posted_date = period_end + 1 day` → 409 `line_outside_period`,
    accrual unchanged.
  - `test_accept_early_reward_refuses_moved_accrual`: a named accrual already posted, or not in the case list → 409
    `accrual_not_estimated`, nothing changed.
  - `test_accept_not_policy_or_rule_learnable`: a policy run and `owner_rule_service.apply_rules` never apply it;
    `inbox_service.learnable` is false; dismiss writes no ledger row.
  - `test_extra_line_with_candidates_stays_surplus` (finding 7): in-period candidates plus early candidates, two
    lines and one row → the extra line has hint `reward_surplus`, not `reward_early`.
  - `test_early_case_superseded_after_posting_fix` (lead carry 2): PUT the rule's posting fields so `post_on` falls
    in the period → the statement has `needs_recheck` → reconcile → case `superseded`, line claimed `reward_engine`.
  - `test_no_early_candidates_still_bank_only`: unchanged behaviour.
  - `test_carry_gap_offers_dismiss_only`: a posted current-period row with no line raises a balance gap on a card
    statement → context has `carried_accrual_ids`, `allowed == ["dismiss"]`, and the policy books nothing.
  - unit `test_classify_reward_hints_other`.
- [ ] **Step 2:** `$PYTEST tests/integration/test_reward_bank_wins_cases.py tests/unit/test_inbox_service.py -v` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Step 2's command plus Task 8's regression suites → PASS.
- [ ] **Step 5:** commit `feat(accounting): reward surplus/early owner cases; carry-gap dismiss only`.

### Task 10: Read API: reward usage and accruals on entries

**Files:**
- Modify: `app/routers/accounts.py` (`GET /{account_id}/reward-usage`), `app/services/reward_engine.py`
  (`usage(db, account_id: int, on: date) -> list[dict]`)
- Modify: `app/schemas/ledger.py` (`RewardUsageOut`; `AccrualOut`; `EntryOut.accruals: list[AccrualOut] = []`),
  `app/services/ledger_service.py` (`_entry_rows` :475 one batched accrual query; `get_entry_detail` :781)
- Test: `tests/integration/test_reward_usage_api.py`

**Interfaces:**
- Produces:
  - `RewardUsageOut`:
    - `rule_ids: list[int]` and `shared_cap_id: UUID | None`
    - `window_start`, `window_end`
    - `used`, `estimated: Decimal`
    - `cap`, `remaining: Decimal | None`
    - `name: str` (the first rule's name)

    One item per in-date enabled rule; a shared group appears once.
  - `AccrualOut`:
    - `id`, `rule_id`, `rule_name`
    - `estimate`, `amount`
    - `state`, `post_on`
    - `ledger_entry_id`

- [ ] **Step 1: Failing tests:**
  - `test_usage_reports_used_estimated_remaining`: cap 100, frozen 30, estimated 20 → used 30, estimated 20,
    remaining 50.
  - `test_usage_shared_group_once`.
  - `test_usage_uncapped_nulls`.
  - `test_usage_default_date_today` (monkeypatch `ledger_service._today`).
  - `test_usage_read_scope_ok_for_hermes` (scoped client `hermes=read,propose,write`).
  - `test_entry_detail_lists_accruals` and `test_entry_list_includes_accruals`.
- [ ] **Step 2:** `$PYTEST tests/integration/test_reward_usage_api.py -v` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Step 2's command plus `$PYTEST tests/integration/test_entries_api.py -q` → PASS.
- [ ] **Step 5:** commit `feat(accounting): reward usage endpoint and accruals on entries`.

### Task 11: SPA: rule editor, 本期 usage, entry 預估回饋

**Files:**
- Modify: `frontend/src/app/models/accounting.model.ts`:
  - `RewardMethod` :22 += `'per_unit'`
  - `RewardRule.window` :36 → `'statement_cycle' | 'calendar_month'`
  - `unit_spend: string | null` on `RewardRule` and `RewardRuleInput`
  - new `RewardUsage`, `RewardAccrual`; `EntryDetail.accruals`
- Modify: `frontend/src/app/services/accounting.service.ts` (`getRewardUsage(accountId: number, date?: string): Observable<RewardUsage[]>`)
- Modify: `components/accounting/reward-rule-sheet/reward-rule-sheet.{ts,html}`:
  - window selector 帳單週期 / 自然月
  - method option 每 NT$X 一單位, with a `unit_spend` input shown only for `per_unit`
- Modify: `components/accounting/reward-rule-text.ts` (`postingSummary` / summary text for `per_unit`: 「每 NT${unit_spend} 一單位」; window label)
- Modify: `components/accounting/account-settings/account-settings.{ts,html}` (`account-settings.html:182`
  回饋規則 head; `rules` signal at `account-settings.ts:348`):
  - per in-date rule: 「本期 {used+estimated}／{cap}」, or 「本期 {used+estimated}」 when uncapped, plus the window
    dates, from `getRewardUsage`
  - a shared group shows once, on its first rule
- Modify: `components/accounting/entry-detail/entry-detail.{ts,html}` (:99-103): 預估回饋 lines `{rule_name}
  {amount} · {state label}`, labels 預估 / 已入帳 / 銀行確認 / 未獲得
- Test: the matching `.spec.ts` files and `testing/fixtures.ts`

- [ ] **Step 1: Failing specs:**
  - `reward-rule-sheet.spec.ts`:
    - `per_unit shows unit_spend and submits it`
    - `calendar_month window round-trips`
  - `account-settings.spec.ts`:
    - `shows 本期 used/cap from reward usage` (fixture used 30 + estimated 20, cap 100 → 「本期 50／100」)
    - `uncapped shows 本期 only`
    - `shared group shown once`
  - `entry-detail.spec.ts`: `lists accruals with state labels` (one each of the four states → the four labels).
  - `reward-rule-text.spec.ts`: `per_unit summary`.
- [ ] **Step 2:** run each spec with `npx ng test --watch=false --include <spec>` → FAIL.
- [ ] **Step 3:** Implement. Follow existing component patterns (signals, PrimeIcons only — the
  `theme/line-icons.spec.ts` gate).
- [ ] **Step 4:** the five specs plus `theme/line-icons.spec.ts`, `accounting.service.spec.ts` → PASS; then the full
  `npx ng test --watch=false` → PASS.
- [ ] **Step 5:** commit `feat(frontend): reward windows, per_unit rules, cap usage and accrual lines`.

### Task 12: Oracle script (read-only) and docs

**Files:**
- Create: `services/accounting-service/scripts/reward_oracle.py`,
  `services/accounting-service/scripts/README-reward-oracle.md`
- Test: `services/accounting-service/tests/unit/test_reward_oracle.py`

**Interfaces:**
- Consumes: `reward_math.estimate`, `allot`; `reward_windows.window_for`, `post_on_for`; maps
  `moze_backup_json.ROUNDING_MAP` :304 and `POSTING_MAP` :305.
- Produces: `reward_oracle.score(pairs: Sequence[Pair], *, tolerance: Decimal = Decimal("1")) -> Report` and
  `main(argv) -> int`.
  - `Report` fields:
    - `maps` (printed header)
    - `amount_ok`, `amount_total`, `excluded`
    - `largest: list[(key, engine, moze)]`
    - `window_totals: list` (capped / shared)
    - `posting_exact`, `posting_same_period`, `posting_total`
    - per-card posting mismatch counts
    - `placeholders_excluded`
  - `main` opens `SessionLocal()` with `SET TRANSACTION READ ONLY` and rolls back (as `scripts/r6_case_dump.py:190-201`).
    It writes `~/reports/home-hub-reward-oracle-<UTC ts>/report.md`: directory 700, file 600, counts and keys only.

- [ ] **Step 1: Failing unit tests** (pure `score` over fictional pairs):
  - `test_amount_bar_counts_uncapped_percent_only`: capped pairs go to `window_totals`, not to `amount_total`.
  - `test_flagged_and_refunded_excluded_and_counted`.
  - `test_within_one_dollar_counts_ok`: 6.144 vs 6 ok; 8 vs 6 not.
  - `test_posting_scored_separately`: an exact miss inside the same period → `posting_same_period` counts, not
    `posting_exact`.
  - `test_placeholders_excluded`.
  - `test_report_header_prints_maps`.
- [ ] **Step 2:** `$PYTEST tests/unit/test_reward_oracle.py -v` → FAIL.
- [ ] **Step 3:** Implement. Pairs come from MOZE reward rows with both `reward_source_entry_id` and
  `reward_rule_id` set, joined to the purchase and the rule. The README states the run command
  (`PYTHONPATH=<tree>/services/accounting-service python scripts/reward_oracle.py`, no `-I`, no `.env` copying) and
  the ≥ 95 % bar.
- [ ] **Step 4:** Step 2's command → PASS; then the **full backend suite** `$PYTEST -q` → PASS (record the count).
- [ ] **Step 5:** commit `feat(accounting): read-only reward oracle script`.

---

## After the tasks (not SDD tasks; owner-gated, from spec §10)

1. Final whole-branch review; PR; Multica PR review (one round + one re-check); owner merge.
2. Deploy API and worker. The migration prints whether the ledger index was created. Notify is rebuilt from the
   new release because `notify/cycles.py` now imports `app.services.cycles`; smoke `python -m notify --help` in the
   release.
3. Oracle on production (read-only). The owner reads the summary; the posting section gates each card (§9.4, §10.3).
4. Data pass with the owner, one case per question:
   - posting fixes for flagged cards first;
   - placeholder → `per_unit` (3 cards);
   - `calendar_month` switches (2 cards / 4 rules).
5. Backfill per §10.4: Hermes proposes links, the owner confirms per case, the engine computes. The seed run uses
   `reward_engine.sync_entry` over linked spends since 2026-10-01 that have no reward row.
6. Hermes `homehub-api` skill update (§8), with text drafted in the PR body and applied on the owner's go.
7. SPA release (stop preview 18081 first; never touch 18080), using the pins in the memory file.

## Plan review mapping (AGENT-396, dev-grok, CHANGES at `4cf7dec4`)

| # | finding | where folded |
|---|---|---|
| 1 | reward rows missing from `locked` → `_write_coverage` KeyError, re-run releases | Task 8 `locked_rows`; accrual amount stays pre-bank; tests |
| 2 | refunds not re-derived on purchase edits | Task 5 `sync_entry` tail + `test_refund_follows_purchase_relink` |
| 3 | parent `closing_day` edit leaves combined children | Task 6 `update_account` children + test |
| 4 | `update_transfer` writes before the helper sees the old key | Task 6 capture before setattr + test |
| 5 | lock-order deadlocks (job vs reconcile, delete vs bank wins) | P12; Task 7 / Task 6 race tests |
| 6 | wrong policy cite for balance gap | Task 9: no policy edit; gate in `CONDITIONS` |
| 7 | `reward_early` override too broad | Task 9: only when `prepare` is None + test |
| 8 | P5 copy never written | P5 `note` in the case context; asserted |
| 9 | weak tests | Task 4 (negative delta, cap clamp), Task 5 (ledger amount used), Task 6 (old-project move, split), Task 8 (flag clear, sweep pin) |
| nits | P8 wording; Task 11 anchor | P8; Task 11 |
| author | P5 dismiss-only leaves a bank-paid reward at 0 | P5 `accept_early_reward`; Task 9 action + tests + SPA button |

## Re-check mapping (AGENT-396, dev-grok, CHANGES at `27ffa2c6`) → rev 3

| item | finding | where folded |
|---|---|---|
| 1–4, 6–9 | resolved at `27ffa2c6` | unchanged |
| 5 | delete path locks one accrual then the reward row, then the rest of the window | P12 rewritten: window `SELECT … ORDER BY id` first, `recompute_window(locked=True)`; `test_engine_reward_delete_lock_order` (statement listener; the race helper cannot see order) |
| A | accept moved every early id; date could be re-bucketed before the re-run; line date unchecked | P5 rewritten: named ids (default closest), 409s, params on the action row, reconcile in the action transaction, accepted map pairing; five tests |
| B | inbox panel fold filter drops the button; note never reaches the card | Task 9 SPA: `InboxButton`, `OneTap`, `FOLD_BUTTONS.other`, `InboxItem.note`; panel spec |
| steer reply | same as A/B, plus no policy / owner-rule path | Task 9 "Not touched" + `test_accept_not_policy_or_rule_learnable` |

Deviation, noted for the lead: re-check A asked to hold the reconcile until every `reward_early` line on the statement
is decided. Rev 3 instead pins each accepted line to its own named ids (the accepted map) and leaves the other lines on
the normal §7 path, so one accept cannot claim or close another line, with no hold state.

**Lead ruling (AGENT-395, 2026-11-03):** rev 3 `7b95c550` accepted for 「照計畫」, deviation accepted. Carry items are
folded above as tests: delete-path order group → accrual window → ledger; accepted-map re-run keeps the claim. The
implementation PR says in one line that P12 places `entry_group` locks before the accrual tier. The PR review goes to
dev-astra (non-Claude, one round). Its mandatory focus: P12 on every path, the `accept_early_reward` transaction, the
accepted-map pairing, and the inbox/SPA wiring.

