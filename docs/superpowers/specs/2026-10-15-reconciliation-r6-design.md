# Statement reconciliation R6 — matching rules and statement-as-truth (design)

Date: 2026-10-15. Status: design approved in conversation (owner), spec for Multica review.
Parent spec: `2026-10-08-statement-reconciliation-design.md` (§6 matching engine, §7 cases/actions/policy, §17 R1c/R1d/R5 notes). This document amends §6 and §7; where the two disagree, this one wins for R6 and later.

## 0. Why

After R5 (OCR) every live statement parses. The open-case population on 2026-10-15 (354 cases over 58 live statements, 20 accounts) was classified by distance to the nearest ledger entry (report `~/reports/home-hub-cases-20261015T033113Z/`, aggregates only). The population is dominated by rule gaps, not by owner mistakes:

| class | cases | cause found in code |
|---|---|---|
| same amount, 1–3 days apart | 26 | `exact_window_days` is already 3, but the score formula (`0.60 + 0.30·(1−dist/4) + 0.10·text`, accept 0.80) cannot reach 0.80 at distance 3 and needs merchant-text overlap at distance 2. The window is effectively ±1 day. A single candidate at distance 2–3 scores ≥ 0.50 and becomes an `ambiguous` case with one candidate. |
| same day, same amount, not matched | 19 | `receivable` / `payable` entries are absent from every gate (`CARD_GATES`, `BANK_GATES`); card purchase accepts only `expense`. |
| same day, amount within 5 % | 29 | `_near_case` requires merchant-token overlap ≥ half (MOZE memo ≠ statement merchant text), tolerance is 3 %, and it never claims. |
| same amount, 4–15 days apart | 11 | no rule between the exact window (3) and nothing. |
| bank-side automatic lines with no ledger entry | 63 | `bank_only` hint is merchant-pattern based (6 Chinese patterns); only 14 of 63 matched a pattern, so the policy's `create_system_entry` saw 14. |
| `balance_gap`, every bank month | 27 | ledger balance = MOZE opening balance + entries; never aligned with the statements; 5 of 10 bank accounts have no ledger entries at all. |

Owner decisions taken in the brainstorm (2026-10-15):

1. **Tiered risk (B).** Exact amount within 3 days and receivable↔purchase auto-claim. Residual ≤ 5 % and 4–15-day drift become proposals on a case; the owner confirms.
2. **`deposit` / `unknown` lines (A, superseded in part by 4).** Not system entries. `unknown` is never `bank_only`.
3. **Statement as truth for bank accounts (owner's reframing, option A).** Instead of aligning MOZE opening balances by hand, the ledger is backfilled from statement lines and anchored at the first live statement's opening balance; from there every month's gap is a real missing entry. Scope: all bank accounts, all live months.
4. **Cards too, with a grace period (C).** Card purchase lines still unmatched 14 days after the statement's period end are backfilled as uncategorised expenses. The grace keeps late MOZE entries from colliding with backfilled ones; when they do collide, the owner's entry wins.
5. **Two stacks (A).** R6a (matcher rules, no ledger writes) ships and runs first; R6b (backfill, anchor, owner-wins) follows. Backfilling before the scoring fix would book lines that should have matched.

Balance-gap evidence (`~/reports/home-hub-balance-gaps-20261015T050149Z/`): only one of ten accounts has a constant gap; the rest drift month to month, so an opening-balance edit alone would not close them. This is why 3 replaced the "align opening balance in MOZE" option.

## 1. R6a — matcher rules (`app/services/statements/matching.py`, settings)

All of R6a is pure matching logic plus rule defaults. No ledger writes, no migration, no new case kind.

### R6a.1 Score of exact-amount representations

Today `_score` returns `0.60 + 0.30·max(0, 1 − dist/4) + 0.10·text`. New:

```
score = 0.80 + 0.10 · (1 − dist / exact_window_days) + 0.10 · text      (dist ≤ exact_window_days)
score = 0                                                                 (dist > exact_window_days)
```

`text` is the existing Jaccard term in [0, 1]. Any conserved exact-amount representation inside the window scores ≥ `accept` (0.80); the date term only orders candidates. The quantum `Q` and `_installment_score` (0.90 + text inside `candidate_window_days`) are unchanged.

`margin` default changes 0.15 → 0.05 on the new scale: two candidates whose date distances differ by ≥ 2 days (window 3: 0.033 per day) resolve to the closer one; two candidates on the same day or one day apart stay `ambiguous`. `accept` stays 0.80, `ambiguous_floor` 0.50.

Settings compatibility: `rules_version` becomes `r6a-1`. A stored override of `accept`, `margin` or `ambiguous_floor` written under an `r1b-*` version is ignored by `rules_for()` with one warning log line naming the key (the old scale is meaningless); the owner re-sets it if wanted. Other stored overrides keep applying.

### R6a.2 Ambiguity needs two candidates

`match()` emits `ambiguous` only when `len(reps) ≥ 2` and the best is ≥ `ambiguous_floor` and the margin test fails. With R6a.1 a single in-window exact representation always claims, so the single-candidate `ambiguous` disappears by construction; the explicit `≥ 2` guard makes that a contract and a test, not a side effect. The installment path keeps its own rule (`_installment_case`).

### R6a.3 Gates: receivable and payable

```
CARD_GATES["purchase"]     = {expense, receivable}
CARD_GATES["installment"]  = {expense, receivable}
BANK_GATES["withdrawal"]  += {receivable}
BANK_GATES["deposit"]     += {payable}
```

Only non-settlement rows reach these gates: `_entry_class` already maps `is_settlement` rows to `settlement_in`/`settlement_out`, and the ledger constraint forces a receivable with amount > 0 and a payable with amount < 0 to be settlements. So a receivable in the purchase gate is "I paid for someone" (amount < 0, conserved against a purchase flow) and a payable in the deposit gate is "someone paid me / lent me" (amount > 0). `conserved` keeps sign and amount checks. Near, drift and foreign paths use `eligible_kinds`, so they inherit the change.

### R6a.4 Near rule (`_near_case`)

- `near_tolerance_pct` default 0.03 → 0.05 (`near_tolerance_abs` stays 10, `near_window_days` stays 5).
- Merchant-token rule applies only when `dist ≥ 1`. At `dist == 0` (same day) a candidate inside the tolerance needs no text overlap.
- `payment` lines are not special-cased; they pass through the gate and `_peer_ok` exactly as today (card payment ↔ `transfer_in` whose peer is known not to be a card).
- The case stays `amount_delta` with `fee_matches: False`, `residual`, ordered candidates; it never claims. Resolution: owner `match` / `adjust_amount` / `attach_fee` / `split_entry` as today. The policy's `attach_fee` (fee_matches only) is unchanged.

### R6a.5 Date drift (`_drift_case`, new)

New rule key `drift_window_days: int = 15` (settings-checked like the other int keys, 0..10000). For a line that reached the fallback chain (no claim, no ambiguity, no `claimed_by_earlier_line`, no foreign, no near): candidates are standalone, gated, `_peer_ok` entries with `_same(|flow|)` and conserved sign, not claimed, with `exact_window_days < dist ≤ drift_window_days`. If any: case kind `amount_delta`, `entry_id` = closest (ties: lower entry id), `context = {"hint": "date_drift", "date_distance": dist, "entry_id": id, "residual": "0", "fee_matches": False}`, candidates `[{entry_id, reason: "date_drift", date_distance}]` ordered by distance then id. Reusing `amount_delta` avoids a new enum value and migration; `KIND_ACTIONS["amount_delta"]` already allows `match` and `dismiss`. The policy never acts on it (`_attach_fee_candidate` requires `fee_matches is True`; nothing else consumes `amount_delta`).

Order in `match()` after R6a: claim → installment → ambiguous (≥ 2) → claimed_by_earlier_line → foreign → near → drift → bank_only → `line_unmatched {}`.

### R6a.6 `bank_only` by line kind

`_bank_only(line, rules)` returns a hint whenever `line.line_kind ∈ {fee, interest, reward}`; the context becomes `{"hint": "bank_only", "pattern": <first matching pattern or null>}`. `bank_only_patterns` stays in `Rules` (stored settings may carry it) and only annotates. `unknown`, `deposit`, `withdrawal` lines are never `bank_only` (decision 2). `policy_service._system_entry_candidate` and `_unchanged` already key on the hint plus `line_kind ∈ SYSTEM_LINE_KINDS`, so the policy picks up all 63 bank-side automatic lines with no code change on its side; `reconciliation_actions._system_line` is already kind-based.

### R6a.7 Evidence harness (`services/accounting-service/scripts/r6_dryrun.py`)

Read-only. Opens a DB session, and for every live statement computes the desired case set twice — with the rules the deployed service would use (stored settings over current defaults) and with the R6a rules — through the existing `_desired_cases` path, then rolls back. Output (file, mode 600, under `~/reports/home-hub-r6a-dryrun-<ts>/`): counts by (case kind, hint, matcher rule) before/after, and the per-line delta as `(statement_id, line_id, before, after, rule that changed it)`. No amounts, no merchants in the summary; the detail file is for the owner. Merge gate for R6a: every delta is attributed to one of R6a.1–R6a.6; no `ambiguous` case with one candidate remains; no exact-amount claim present before is lost after. A `--policy` mode is added by R6b (§2.5).

### R6a.8 Tests

`tests/unit/test_statement_matching.py` (helpers `L`, `E`, `run`): distance 3 same amount claims; distance 2 with no text claims; two same-day exact candidates stay ambiguous; candidates at distance 0 and 2 claim the closer; single candidate never ambiguous; receivable (non-settlement) matches a purchase, receivable settlement does not; payable matches a deposit; near at distance 0 without token overlap, at distance 1 still requires overlap; near at 4.9 % passes, 5.1 % fails; payment line near via `_peer_ok`; drift at 4 and 15 days emits `amount_delta` with `date_drift`, at 16 does not; drift ordering; `bank_only` for an interest line without any pattern, never for `unknown`. `tests/integration/test_reconciliation_service.py`: a drift case round-trips through `_desired_cases` and resolves with `match`; a bank-only-by-kind case is a `create_system_entry` candidate in `test_reconciliation_policy.py`. `settings_service` tests: `drift_window_days` validated; stale `r1b-*` override of `margin` ignored with a log record.

## 2. R6b — statement as truth (policy actions, settings)

R6b adds three policy-driven actions. They reuse the booking and revert machinery of the existing actions (`_Creation._book`, `balance_adjustment_asof`, `engine.revert_action`), the policy caps and the audit rows. No migration: `reconciliation_action.action` is `String(32)`, so new action names need no enum change.

### R6b.1 `backfill_entry`

Policy-only action (not in `KIND_ACTIONS` for the owner; the owner keeps `create_entry` with a chosen category).

Candidate: a case with `kind == "line_unmatched"`, `status == "open"`, empty `candidates`, no `hint`, on a statement in `live` mode, whose `period_end + backfill_grace_days ≤ today` (`backfill_grace_days` policy setting, default 14), and whose line kind maps to an entry kind:

| statement | line kind | entry kind |
|---|---|---|
| card | `purchase` | `expense` |
| bank | `withdrawal`, `purchase` | `expense` |
| bank | `deposit` | `income` |
| any | `unknown` with flow < 0 / > 0 | `expense` / `income` |

Not backfilled, case gets `context.hint = "needs_peer"` (set by the matcher's fallback when the line kind is one of these): `transfer_in`, `transfer_out`, `payment`, `installment`, `balance_adjustment`, `refund` (a refund needs its parent; `create_refund` stays an owner action). Transfer entries are two-legged (`transfer_group_id`); a one-legged transfer is not in the model, and pairing a bank payment line with the card's payment line is a separate feature (R6c, out of scope). On accounts with no ledger entries the residual balance gap after backfill is therefore the sum of unbooked transfers — visible in the gap, by design.

Booking: `_Creation._book` with `kind`, `category_id = backfill_category_id(db, kind)` (a fixed category `對帳待分類` per kind, created on demand like `system_category_id`), `needs_review=True`, `source="statement"`, merchant/description/dates from the line as today. The line is claimed (`coverage_service.write_claim`, rule `backfill_entry`), the case resolves, the action row records `params = {kind, category_id}` and `inverse = {created_entry_id}` so `revert_action` deletes the entry while it is unchanged.

Policy integration (`policy_service`): `POLICY_ACTIONS += {"backfill_entry"}`, candidate builder `_backfill_candidate` with the amount `|flow|`; caps as for the other actions (`max_amount`, `per_statement_count`, `per_statement_total`, monthly); over cap → proposal for the owner (`_prefilter` as today); `_unchanged` compares status, kind, absence of hint/candidates, line kind and amount. Account scoping: policy setting `backfill_accounts: list[int]` (empty = every account); the rollout enables banks first.

### R6b.2 `anchor_opening`

Policy-only action on `balance_gap` cases, once per bank account.

Target statement: the account's earliest live statement whose `opening_balance` is not null (all 30 live bank statements carry it on 2026-10-15). `as_of = period_start − 1 day`, `delta = statement.opening_balance − ledger_service.account_balance(db, account_id, as_of)`. Books a `balance_adjustment` entry (`system_category_id(db, "balance_adjustment")`) with `entry_date = posted_date = as_of`, description `期初對齊 <as_of>`, `source="statement"`, `needs_review=True`. Lock and barrier as `balance_adjustment_asof` (`LockSet(account_lock=True, barrier=True)`, refused while `dirty_enabled` is off).

Idempotence: refused (`already_anchored`) when the account already has a `balance_adjustment` entry with `source="statement"` dated `as_of` whose description starts with `期初對齊`; refused (`zero_delta`) when `delta == 0`. The policy maps the action to the `balance_gap` case of the target statement only; `balance_gap` cases of later months are left to the next run, where after backfill they close by themselves or stay open as real gaps. Cap: `actions.anchor_opening.max_amount` (default "200000"); above it the owner gets a proposal.

The owner's existing `balance_adjustment_asof` (adjust at period end) stays available for later months.

### R6b.3 `replace_backfill` — the owner's entry wins

Matcher side (`reconciliation_service._desired_cases`, entry loop): for an unmatched claimable entry `e` (not deferred, not an uncovered child) find a line on the same statement with `_same(|flow|)`, conserved sign, gate containing `e`'s class, `dist ≤ exact_window_days`, whose current coverage is held by an entry `s` with `source == "statement"` created by a `backfill_entry` action (join coverage → entry → action by `created_entry_ids`). If exactly one such line: the `entry_unmatched` case carries `context = {"hint": "shadowed_by_backfill", "line_id", "system_entry_id", "action_id"}`. Fee/interest/reward system entries and balance adjustments are never shadows (no MOZE twin exists for them).

Action `replace_backfill` (policy, enabled by default, no amount cap — the net ledger change is the owner's own entry replacing an uncategorised stand-in): in one transaction, `engine.revert_action(action_id)` (refuses when the backfilled entry no longer equals its `after` — the owner edited it — and the case stays open with the hint), then the existing `match` of `e` to the line (rule `replace_backfill`). The audit shows both rows. `_unchanged` compares entry amount/date, line id and the shadow's action id.

MOZE backup imports do not disturb this: `delete_moze_entries` removes only entries with a MOZE source or `moze_id`, and the import's balance arithmetic (`_moze_part`) sums MOZE-source entries only, so statement-source entries survive imports and the next run sees the owner's new entry as `entry_unmatched` → shadow → replace. The same holds for the anchor of R6b.2: every import resets `account.opening_balance` from the backup's `originalAmount`, which is why the anchor is an entry and not an opening-balance edit.

### R6b.4 Settings (`settings_service`)

`POLICY_DEFAULTS` gains:

```
"backfill_grace_days": 14,
"backfill_accounts": [],
"actions": {
  ...,
  "backfill_entry":   {"enabled": False, "max_amount": "5000"},
  "anchor_opening":   {"enabled": False, "max_amount": "200000"},
  "replace_backfill": {"enabled": True}
}
```

`_check_policy` validates the new keys (int 0..365, list of int, action blocks with `enabled` bool and optional `max_amount` Decimal string). `policy_version` prefix becomes `r6b` (`r6b-<version>`). `SYSTEM_LINE_KINDS` and `create_system_entry` are unchanged.

### R6b.5 Evidence

`scripts/r6_dryrun.py --policy`: lists, per account, what `backfill_entry`, `anchor_opening` and `replace_backfill` would do under the current settings (counts in the summary; amounts only in the detail file). Run before each enable step; the release record keeps the summary.

### R6b.6 Tests

`tests/integration/test_reconciliation_policy.py`: backfill eligible after grace, refused before grace, transfer/payment line gets `needs_peer` and is skipped, over-cap becomes a proposal, account scoping, category created once, `_unchanged` refuses a changed amount; anchor books at `period_start − 1` with the opening-balance delta, idempotent on rerun, refused with dirty triggers off, over cap → proposal; replace: owner entry supersedes the backfilled one (entry deleted, line re-claimed, two action rows), refused when the backfilled entry was edited, never triggered by a fee system entry. `tests/unit/test_statement_matching.py`: `needs_peer` hint for each excluded line kind. Matcher/service test for the shadow detection with two candidate lines (no hint when not unique).

## 3. Evidence, rollout, process

### 3.1 Rollout

1. R6a: merge → deploy worker + API (detached checkouts, constraints file, `gate.sh`) → one manual run → open-case delta against the 354 baseline → owner ok. Release record `~/reports/home-hub-r6a-release-<ts>/` with a pytest-free evidence step (the deploy venv has no pytest; the README gets that step).
2. R6b: merge → deploy; all new actions off except `replace_backfill`. Backup (`docker exec … pg_dump`, as on 2026-10-15). Enable `backfill_entry` with `backfill_accounts` = bank accounts → run → per-account `balance_gap` check → enable `anchor_opening` → run → cards (`backfill_accounts` emptied). Each step is one wholesale `PUT /settings/reconciliation` (ADMIN token) and a dry-run before it.
3. The sweep does not revisit untouched statements; after each enable the operator reconciles the affected statements explicitly (`POST /accounts/{acct}/statements/{id}/reconcile`) or waits for the daily run, which re-derives cases for statements the dirty triggers marked.

### 3.2 Process

Spec review by Multica first (lead-claude, non-Claude reviewer at the exact SHA), then a plan per stack, then PRs `feat/reconciliation-r6a` and `feat/reconciliation-r6b` (R6b branches from main after R6a merges). Worktrees carry a copied `.env` (mode 600). R5 worktrees are removed before R6a starts.

### 3.3 Non-goals

Case UI (R3a, PR #69); MOZE write-back; historical statements (`period_end ≤ 2026-06-30`); owner-vs-owner MOZE duplicates (43 suspected, list file for the owner); auto-booking `unknown` lines beyond expense/income by sign, transfers, payments, installments; cross-statement transfer pairing (R6c); OCR changes; AGENT-126 (401 → `auth`) stays a separate maintenance PR.

### 3.4 Success criteria

- After R6a: open cases ≤ 290 (from 354): the 1–3-day, receivable/payable and bank-only-by-kind classes close or become policy-eligible; near/drift classes move to `amount_delta` with candidates; zero single-candidate `ambiguous`; no previously claimed line lost (dry-run gate).
- After R6b on bank accounts: bank `line_unmatched` without `needs_peer` ≈ 0; `balance_gap` only where `needs_peer` lines remain, with the gap equal to their sum (checked per account in the release record).
- After R6b on cards: a MOZE entry recorded after a backfill replaces it within one run (integration test plus one real case in the release record).

## 4. Risks and rulings

- **Backfill books something the owner would have matched.** Mitigated by order (R6a first), grace (14 d), owner-wins (R6b.3) and caps. Remaining: an owner entry recorded > `exact_window_days` from the line after a backfill is not a shadow; it shows as `entry_unmatched` next to a backfilled line — the owner dismisses or reverts by hand.
- **Score rescale changes historical behaviour.** Historical statements are informational; the dry-run reports live statements only, and the service never rewrites coverage of claims already written.
- **Transfers unbooked on 0-entry accounts.** Accepted; the gap is the measure. R6c when wanted.
- **Stored rule overrides on the old scale.** Ignored with a warning, not migrated (ruling: the owner has no overrides today; settings v4 only changed policy keys).
