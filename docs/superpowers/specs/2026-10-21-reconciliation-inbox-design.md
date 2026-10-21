# Reconciliation inbox (待處理) and owner-taught rules — design

Status: rev 2 (2026-10-21, after the builder check; calls marked *[call]* are mine), from the owner's brainstorm of 2026-10-21 (visual companion session, options A + merge + fold
chosen; focus mode rejected). Replaces the "R3b action sheet on the statement page" item of the reconciliation spec
(`2026-10-08-statement-reconciliation-design.md` §17) — the statement page stays read-only.

## 1. Intent

The owner does not want to study bank lines. When the daily reconcile cannot pair a bank line with a HomeHub entry, the
system should ask one plain-language question with two or three answers, the owner taps one, and the ledger is fixed.
When the same question recurs every month (a loan repayment, a card autopay that differs by the reward offset), the
owner answers once and ticks "以後都這樣"; the daily reconcile then applies that answer by itself. Today every one of
these decisions goes through the API by hand, and nothing is remembered.

Scope note: bank lines that are transfers or card/loan payments (`line_kind` payment / transfer_in / transfer_out,
hint `needs_peer`) cannot be booked from a case today — the engine reserves them for R6c (transfers). This round shows
them as cards with 對到… / 忽略 only; booking a transfer stays a timeline action. Most of today's 20 such cases are the
mis-dated recurring entries that PR #97 fixes at the 2026-10-24 import.

Success: after cutover the owner opens 🔔 → 待處理 a few times a month, answers the handful of cards that need a human,
and the recurring ones never come back.

## 2. Goals, non-goals, anti-goals

Goals
- A 待處理 tab in the existing 提醒中心 (🔔) that lists every open or proposed reconciliation case as a card in plain
  language, grouped by account, with the actions that case allows reduced to at most three buttons.
- Recurring same-shape cases of one account merged into one card that is answered once.
- Cases the owner need not decide are folded: policy proposals (one "全部套用"), balance gaps, settlement lines.
- "以後都這樣": an owner-taught rule, created from the answer, that the daily sweep applies before the policy runs.
- An 已處理 view of what the rules and the policy did, with revert.
- A list of rules in 記帳設定 with delete.

Non-goals
- No new matching heuristics; the rule engine only re-applies owner answers.
- No "focus mode" (one card at a time).
- No action sheet on the statement detail page; it stays read-only. No change to the statement list.
- Settlement (交割股款) cases are folded, not handled: Phase 1 of the portfolio integration owns them.
- Agent (hermes) proposals keep their existing apply/reject semantics; they appear in the folded "有提議" section.

Anti-goals (a failure even if it works)
- A card the owner cannot understand without knowing what a "case kind" is.
- A rule that applies to a line the owner never saw (rules are narrow: one account, one description pattern, one answer).
- An automatic action that cannot be reverted from the UI.
- More than one API round trip per tap for the common cards.

## 3. Facts the design rests on (verified 2026-10-21 on main `9c634f6`)

- Cases: `reconciliation_case` (kind, status open/proposed/resolved/dismissed/superseded, line_id, entry_id,
  candidates JSON, context JSON, version). `GET /reconciliation/cases` returns bare `CaseOut` rows (no line text, no
  account, no entries); only `GET /reconciliation/cases/{id}` carries the line and `actions_allowed`. Prod today
  (all on live statements; historical statements have no open cases): 251 open/proposed — line_unmatched 58 open
  (hints: `needs_peer` 20 = payment 15 / transfer_in 4 / transfer_out 1, `installment_unmapped` 8,
  `stand_in_deleted` 5, `bank_only` 2, `claimed_by_earlier_line` 2, none 21) + 17 proposed (withdrawal 11,
  deposit 6 — the 交割 lines), entry_unmatched 98 (entries: expense 50, transfer 23, receivable 13, fee 9…),
  amount_delta 32 (entry: expense 16, transfer 10 = protected, none 6; 5 carry hint `date_drift`), ambiguous 12,
  balance_gap 34. `near` / `foreign` / `date_drift` are candidate `reason` values, not hints (`date_drift` is both).
  `merchant_norm` is NFKC upper-cased, prefix-stripped, whitespace-collapsed and KEEPS digits
  (`statements/merchant.py`).
- Actions: the registry in `app/services/reconciliation_actions/` (17 actions, `allowed(case)` per kind + conditions);
  `engine.apply_action(db, case_id, action, params, actor, label, request_id, proposal_id, run_id, budget, precheck)`;
  idempotency key `(case_id, version, action, params)` — the key uses the CURRENT version; the owner path has no
  expected-version check (`engine.py:219-221`, `routers/reconciliation.py:34-38`); `actor` enum `owner | policy`;
  `revert` refused after a later action touched the same rows; `revert_action` reopens the case at a new version and
  sets `context.reverted_action_id`. `allowed(case)` filters by kind + context only: it ignores the statement mode
  (the engine refuses everything but match/unmatch on historical statements) and `adjust_amount`'s own refusals
  (`protected` for settlement/refund/transfer/system/scheduled-loan entries via `split_protection.protected_reason`,
  `not_standalone`, `entry_out_of_scope`). `create_entry` params: `kind ∈ {expense, income}`, `category_id`, `name?`,
  `project_id?`; it is not offered on `needs_peer` lines (`_no_peer_hint`). `adjust_amount` is refused on MOZE rows
  until `ACCOUNTING_IMPORT_LOCKED=true` (cutover 2026-10-24). `ActionOut` has `actor` but no label (the token label is
  only in `reconciliation_audit.actor`); the `action_applied` audit detail has no free-text slot.
- Proposals: `reconciliation_proposal` (author `policy` for over-cap policy candidates, agent labels otherwise);
  `POST /reconciliation/proposals/{id}/apply|reject`.
- Policy: `policy_service.run_policy_safe(db, statement_id)` is called at FOUR sites — after `submit_revision`
  (`routers/statements.py:205`), after a manual statement (`:220`), after a single reconcile (`:286`) and in the daily
  `POST /reconciliation/sweep` (`:290-316`: `reconcile_each` → commit → `run_policy_safe`). New statements get their
  cases at ingest; the sweep only revisits dirty / `needs_recheck` statements. Policy actors are budget-capped (cap 5
  per statement); owner actions are not. The policy's `backfill_entry` (grace `backfill_grace_days` = 14) and
  `create_system_entry` skip a line when an owner entry may already record it (`stand_ins.owner_may_record`;
  precheck `policy_service.refuse_owner_recorded`). A plain owner `create_entry` has no such guard.
- Settings: one JSON row `reconciliation_settings.data` (`account_map`, `composite_map`, `dirty_enabled`, `rules` =
  matching.Rules overrides — the name is taken, `policy`, `account_siblings`); PUT replaces it wholesale (ADMIN).
  `installment_plan_map` is the precedent for a per-account owner-taught mapping in its own table. Matching `Rules`
  near bounds: `near_tolerance_abs` 10, `near_tolerance_pct` 0.05, `near_window_days` 5 — an `amount_delta` case
  exists only inside them.
- SPA: 提醒中心 is `AccountingRemindersComponent` (`/accounting/reminders?tab=all|cards|debts|pending`), loads with one
  `forkJoin`, reloads on `accounting.entriesChanged()` / `accountsChanged()` (`statements.changed()` only re-reads the
  card statement lists for the 對帳 chip, `reminders.ts:399-408`); the 🔔 count is
  `TimelineComponent.reminderCount`. Sheets share `sheet.scss` + the `[data-overlay]` focus contract; `CategoryPicker`
  is an inline drill-in grid; `AccountPicker` is a popover/sheet. The SPA has no reconciliation calls today
  (`statements.service.ts` lives in `components/accounting/statements/`). Caddy's
  `@hub_spa` allowlist does not include new paths: a tab on `/accounting/reminders` needs no Caddy change.
- Auth: the SPA token has scope `legacy`, which satisfies EVERY scope incl. `admin` (`auth.py:240`);
  `/reconciliation/*` additionally needs the feature flag `ACCOUNTING_RECONCILIATION_ENABLED` (on in prod).
- Portfolio Phase 1 (case kinds `settlement`/`dividend`/`wire`, `settlement_labels` in settings) is NOT on main and
  comes after cutover; the settings PUT is `extra="forbid"`, so `settlement_labels` cannot be set today.

## 4. The inbox read model — `GET /reconciliation/inbox`

One composite endpoint (READ scope) so a card costs no extra round trip. Query: `status=open,proposed` (default both),
`account_id` (optional), `limit` (default 500, max 1000). Only cases on LIVE statements *[call]*: historical
statements allow nothing but match/unmatch, and none has an open case today.

Response `InboxOut`:
```
{
  "generated_at": ISO,
  "counts": {"ask": n, "ask_items": n, "proposed": n, "balance_gap": n, "settlement": n, "other": n},
                                        // ask = unfolded CARDS after merging (the bell number); ask_items = rows;
                                        // computed server-side over the whole set regardless of limit
  "accounts": [ {"id", "name", "currency", "is_credit", "items": [InboxItem…]} … ]   // accounts with items, by name
}
InboxItem = {
  "case_id", "case_version", "kind", "status",
  "question": "bank_only" | "transfer" | "ledger_only" | "amount_differs" | "date_differs" | "ambiguous"
            | "balance_gap" | "settlement" | "proposed" | "other",
  "folded": bool,                       // true for proposed / balance_gap / settlement / other
  "group_key": str | null,              // §4.2; same key = one merged card
  "statement": {"id", "period_end"},
  "line": {"id", "date", "merchant", "amount", "line_kind"} | null,     // date = posted_date, amount = flow_amount
  "entry": {"id", "date", "title", "amount", "account_id"} | null,      // the case's entry (entry cases) — title = name|merchant|category
  "candidates": [{"entry_id", "date", "title", "amount", "reason", "residual", "date_distance"} …],  // resolved, ≤ 5
  "proposal": {"id", "action", "params", "author", "rationale"} | null, // the pending proposal on a proposed case
  "actions": ["record", "match", "accept_bank_amount", "match_candidate", "dismiss", "apply_proposal", …],  // §4.3
  "rule_hint": {"merchant_pattern", "line_kind", "flow_sign", "amount"} | null   // what "以後都這樣" would store (§6);
                                        // null when the answer is not learnable (transfer, ledger_only, protected)
}
```

### 4.1 `question` (the plain-language class) — derived server-side, one rule each
| question | from |
|---|---|
| `settlement` | `line_unmatched` (open or proposed) on a bank account whose line merchant contains one of the labels `["交割股款", "申購股款"]` — a module constant until portfolio Phase 1 adds `settlement_labels` to settings and its own case kinds (follow-up: map those kinds here) |
| `proposed` | status `proposed` (a pending proposal exists) and not `settlement` |
| `transfer` | `line_unmatched` with hint `needs_peer` (transfer / payment line; see §1 scope note) |
| `bank_only` | `line_unmatched` with `create_entry` in `allowed(case)` (bank has it, ledger does not) |
| `ledger_only` | `entry_unmatched` |
| `date_differs` | `amount_delta` with hint `date_drift` |
| `amount_differs` | `amount_delta` otherwise |
| `ambiguous` | `ambiguous` |
| `balance_gap` | `balance_gap` |
| `other` | everything else: `line_unmatched` with hint `installment_unmapped` (needs `link_installment_plan`, API only) or otherwise without `create_entry`, `duplicate_claim`, `recheck`, `parse_review`, `statement_conflict` |

`folded` = question ∈ {proposed, balance_gap, settlement, other} *[call: installment folded — its only button would be 忽略]*.
`counts.ask` = unfolded cards.

### 4.2 `group_key` (merged cards)
For `bank_only` only *[call]*: `f"{account_id}|{line_kind}|{pattern}|{signed amount}"` where `pattern` is
`merchant_norm` with digit runs removed (the same text `rule_hint.merchant_pattern` carries). Two or more unfolded items
with one key → the SPA renders one card with "×N 筆" and the member dates; an answer applies to every member (§5).
The amount IS part of the key *[call, after a reviewer note]*: bank descriptions are often generic (「轉帳支取」,
「他行轉入」) and would merge unrelated payments; a fixed monthly amount (loan, subscription) still merges. Measured on
today's cases: 58 bank lines → 37 cards with the digit-stripped pattern alone. `ledger_only` and `transfer` cards never
merge.

### 4.3 `actions` — the owner's buttons, mapped onto registry actions
| button (SPA label) | item.actions value | registry action and params | when offered |
|---|---|---|---|
| 記為支出／收入… | `record` | `create_entry {kind, category_id, name?, project_id?}` (kind from the line's sign; the sheet picks the category, defaults to the last category used with this `merchant_pattern` on this account) | `bank_only` |
| 已記在別處，對到… | `match_candidate` | `match {line_id, entry_id}` on a candidate the owner picks from `candidates` (or from a search of that account's entries ±14 days, same sign, unclaimed) | `bank_only`, `transfer`, `ambiguous`, `date_differs`, `amount_differs` with `match` allowed |
| 改成銀行的數 | `accept_bank_amount` | `adjust_amount {entry_id, line_id}` | `amount_differs` whose single candidate entry is standalone and `protected_reason()` is None *[call: the server runs the same checks the action does, so transfer/settlement/loan entries get no button]* |
| 對到 M/D 那行 | `match_candidate` | `match {line_id, entry_id}` | `date_differs` |
| 等下期 | `defer` | `defer_next_period {entry_id}` | `ledger_only` |
| 不是同一筆 / 忽略 | `dismiss` | `dismiss {note}` — note = the SPA's fixed text `"inbox: <button label>"` | every kind that allows `dismiss` |
| 全部套用 / 套用 / 退回 | `apply_proposal` / `reject_proposal` | existing proposal routes | `proposed` |
| 對齊銀行期初 | `anchor` | `anchor_opening {}` | `balance_gap` when allowed |

Everything else the registry offers (`create_system_entry`, `backfill_entry`, `split_entry`, `move_account`,
`create_refund`, `attach_fee`, `link_installment_plan`, `replace_stand_in`, `balance_adjustment_asof`, `unmatch`,
`resolve_conflict`) is NOT surfaced in this round; a case with none of the table's buttons shows as `other` with only
忽略, and the card says "需要在 API 處理" with the case id. `actions` is computed from `registry.allowed(case)` filtered
through this table plus the extra checks named above, so the SPA never offers what the engine would refuse.

## 5. Acting — `POST /reconciliation/inbox/apply`

One WRITE call per tap, also for merged cards:
```
{ "cases": [{"case_id": int, "case_version": int}, …],   // 1..50, all the same question and (for learn) the same group_key
  "action": "record" | "match_candidate" | "accept_bank_amount" | "defer" | "dismiss" | "anchor",
  "params": {…},                        // per the table in §4.3; for match_candidate on a merged card: {"entry_ids": {case_id: entry_id}}
  "answer": "記為支出 餐飲",             // the button text the owner saw, ≤ 80 chars, for the audit (§7.3)
  "learn": false | {"amount_exact": bool}   // §6: create a rule from this answer
}
→ { "results": [{"case_id", "status": "applied"|"replayed"|"refused", "action_id"?, "code"?}], "rule_id": int|null }
```
Each case is applied in its own transaction through `engine.apply_action(actor="owner", label=<token label>)` — exactly
what the single-case route does — with a `precheck` that refuses `stale_case` when `case.version != case_version`
*[call: the engine has no expected-version check on the owner path; the card was rendered from a version and must not
apply to a newer one]*. One refusal (`stale_case`, `candidate_changed`, `not_conserved`, `protected`, a MOZE row before
cutover…) does not undo the others; the response names the refused ones with the engine's code and the SPA shows them
as "沒成功：…" cards that stay. `apply_proposal` / `reject_proposal` use the existing proposal routes (the SPA calls
them per proposal; "全部套用" is a loop with a progress count — proposals are few).

Every member of a merged card is applied by the owner's tap (actor `owner`, the token label), not by the new rule
*[call]*; the rule (§6) exists for future lines. The `answer` text is written as its own audit event
`inbox_answer {case_id, action_id, answer}` right after the action row, so the 已處理 list can show it.

## 6. Owner-taught rules

### 6.1 Table `reconciliation_owner_rule` (one migration; the `installment_plan_map` precedent)
```
id, account_id (FK account, not null), enabled bool default true,
match: line_kind text (not null), merchant_pattern text (not null; the digit-stripped merchant_norm, matched as a
       substring of the candidate line's digit-stripped merchant_norm), flow_sign ('+'|'-', not null),
       amount numeric(14,4) null          // when set: the line's flow_amount must equal it exactly
answer: action text ('record'|'accept_bank_amount'|'dismiss'),
        params jsonb ({category_id, name?, project_id?} for record; {} otherwise),
created_from_case_id, created_at, last_applied_at, applied_count int default 0, note text null
UNIQUE (account_id, line_kind, merchant_pattern, flow_sign, amount)
```
A rule is one account, one bank-line pattern, one answer. Rules are learned only from BANK lines *[call]*:
`bank_only` → `record` / `dismiss`; `amount_differs` → `accept_bank_amount` (no tolerance column: an `amount_delta`
case already sits inside the matcher's near bounds, which is the tolerance). `ledger_only` answers (`defer`, `dismiss`)
and `match_candidate` are never learned — an entry has no bank text to match on, and a recurring ledger-only entry is a
template to fix, not a rule. `amount` defaults by answer *[call, after a reviewer note]*: for `record` / `dismiss`
it is the line's amount (`learn.amount_exact` true, the sheet's default; pre-ticked and locked on a merged card, whose
members share it; unticking stores null, 「金額不同也算」) — the narrow default keeps a generic description like
「轉帳支取」 from booking unrelated lines; for `accept_bank_amount` it is null (`amount_exact` false and not offered —
a card-fee offset differs every month, and the matcher's near bound already limits what the rule can touch).

### 6.2 Creating a rule
`learn` on `/inbox/apply`: the server derives `merchant_pattern` (digit runs removed from `merchant_norm`; shown on the
card as the exact text the rule will store, NOT editable in this round *[call]*), `line_kind`, `flow_sign` and `amount`
from the case's line, and the answer from the request. For a merged card every member must share the key, else 422
`rule_pattern_mismatch`. A duplicate (same unique key) is updated, not duplicated. The rule is created only when every
member applied (a refused member means the answer was wrong for at least one line). It applies to future lines only.

### 6.3 Applying rules — `owner_rule_service.apply(db, statement_id, *, run_id)`
Runs at all four `run_policy_safe` call sites, immediately before the policy (`routers/statements.py:205`, `:220`,
`:286`, `:290-316`), on live statements only. For each open case the rule can answer (`line_unmatched` with
`create_entry` allowed → record/dismiss; `amount_delta` with one standalone unprotected candidate →
accept_bank_amount): the first enabled rule of the statement's account whose `line_kind`, `flow_sign`, pattern and
(when set) `amount` match (longest pattern wins, then lowest id) is applied through
`engine.apply_action(actor="owner", label=f"rule:{rule.id}", precheck=policy_service.refuse_owner_recorded)` — the same
owner-twin guard the policy's creations use, so a rule never double-books a line the owner already recorded — with an
audit event `rule_applied {rule_id, case_id, action}`; a refusal is audited `rule_skipped {rule_id, case_id, code}` and
never retried in the same run. Rules never touch cases on historical statements, settlement lines, cases with a pending
proposal, or a case whose `context.reverted_action_id` is set *[call: a case the owner reverted is the owner's; without
this the next sweep would redo what 撤銷 undid]*. `actor="owner"`: a rule is the owner's standing instruction, so the
policy caps and budgets do not apply and `resolved_by` stays `owner`; the audit label distinguishes it. Rules run before
the policy, so a line with a rule is booked with the owner's category rather than the policy's generic backfill.

### 6.4 Managing rules
`GET /reconciliation/owner-rules` (READ), `DELETE /reconciliation/owner-rules/{id}` (WRITE), `PATCH … {enabled}` (WRITE).
Rules live in their own routes and table rather than the settings JSON *[call: the settings PUT replaces the whole
object; per-row delete/disable and `applied_count` fit a table]*. 記帳設定 gains a `<details>` block "對帳規則" listing
account · pattern · amount · answer · applied_count · last_applied_at with the two-tap delete used by the 對象 list.
撤銷 on a rule's action (§7.3) also offers 「同時停用這條規則」 (PATCH enabled=false).

## 7. The SPA

### 7.1 Tab and count
- `ReminderTab` gains `'todo'` (label 待處理), shown between 全部 and 信用卡帳單; `?tab=todo` deep-links it. 全部 shows the
  first three unfolded cards with a "還有 N 張 →" link to the tab.
- `TimelineComponent.reminderCount` adds `inbox.counts.ask` (one `GET /reconciliation/inbox?limit=1` whose `counts` are
  computed server-side regardless of `limit`). The bell count is "cards to decide", not all cases.

### 7.2 The tab (option A of the mockups)
- Header line: "要你決定 N · 其他 M"; then per account (name, currency, is_credit icon) its unfolded cards, newest
  statement first, then within a statement by line date.
- A card: first line bold = date + merchant + signed amount (`formatMoney(sign)`); second line muted = the question text
  (table below); then the buttons; then, when `rule_hint` is present and the action is learnable, "☐ 以後「<pattern>」
  都這樣" (ticked by default on a merged card). A merged card shows "×N 筆" and the member dates/amounts collapsed.
- Question texts (zh-TW, one per `question`): bank_only 「銀行有、你沒記」; transfer 「轉帳／繳費，你沒記 —
  在時間軸記一筆轉帳，或對到已記的」; ledger_only 「你記了，銀行沒有」; amount_differs 「銀行 X，你記 Y（差 Z）」;
  date_differs 「你記 M/D，銀行 M/D 扣」; ambiguous 「有 N 筆可能是它」; other 「需要在 API 處理（案件 #id）」.
- Folded sections under the cards, each a `<details>`: 「系統有提議 N（全部套用）」, 「期初差額 N」(each with 對齊銀行期初
  when allowed, else 忽略), 「國泰交割款 N — 等證券連動」(no buttons), 「其他 N」.
- Sheets (reuse `sheet.scss` + the overlay contract): **record** = category picker (inline `CategoryPicker` for the line's
  kind, default from `rule_hint` → last used; optional name; optional project) + the learn tick + 確定; **match_candidate**
  = list of candidates (date · title · amount) + "搜尋這個帳戶的記錄" (14-day window, same sign) + 確定;
  **accept_bank_amount** and **dismiss** need no sheet (one tap; dismiss on a merged card asks 確定 once).
- After a successful apply the card leaves the list immediately and the component calls `accounting.entriesChanged()`
  (dismiss/defer/match write no entry, so the inbox reload cannot rely on the timeline's own signal); the existing
  reminders `load()` effect gains the inbox call so entry edits elsewhere refresh it too.
- Errors: a refused member stays as a card with the engine code mapped by `actionErrorMessage` (+ new codes
  `stale_case` / `candidate_changed` 「對象已變，重新整理」, `rule_pattern_mismatch`, `not_conserved` 「金額對不上，不能直接對」,
  `protected` 「這筆是轉帳／交割，不能改金額」, `owner_may_record` 「你已經記過了」).

### 7.3 已處理
A third section on the tab (collapsed): the last 50 `GET /reconciliation/actions` rows. `ActionOut` gains `label`
(the audit actor label of the `action_applied` event) and `answer` (the `inbox_answer` text, null for policy and rule
rows); the SPA shows `rule:<id>` → 「規則 #id」, `policy` → 「系統」, else 「你」, the answer or the action name, and 撤銷
(existing revert route; a `not_revertable` refusal is shown as text; on a rule row 撤銷 also offers 同時停用這條規則).

## 8. Failure modes
- Case changed under the card (`case_version` moved): the apply is refused `stale_case` by the precheck; the SPA
  reloads the inbox and keeps the card with the message. No silent re-targeting.
- A rule matches the wrong line: every rule application is one audit row and one revertable action; the 已處理 list
  shows it with 撤銷 (+ disable the rule); a reverted case is never re-answered by a rule. A rule never matches across
  accounts, and by default only an exact amount.
- A generic description merged with unrelated lines: the amount in the group key keeps 「轉帳支取 −N」 and 「轉帳支取 −M」
  apart; transfer-kind lines never get a 記為 button at all.
- MOZE rows before cutover: `adjust_amount` (accept_bank_amount) is refused `locked_until_cutover`; the card shows
  「cutover 後才能改」. After 2026-10-24 this disappears.
- Two owners' sessions / a sweep racing an apply: the engine's idempotency key and effect slots already serialise;
  the batch endpoint applies per case and reports.
- Inbox size: 500 items hard limit; the response is ≤ a few hundred KB; counts are computed with one grouped query.

## 9. Testing (named so the plan can pin them)
- Backend unit/integration: `question` classification per kind/hint (table-driven, incl. `needs_peer` → transfer,
  installment → other, settlement label on a proposed case); `group_key` merges same pattern+amount and never merges
  across accounts, signs, amounts or digit-only differences; `actions` is a subset of `registry.allowed` and drops
  `accept_bank_amount` on a protected or non-standalone candidate; historical-statement cases are absent; `counts.ask`
  counts cards; `/inbox/apply` applies N cases in N transactions, refuses `stale_case` on a moved version, reports a
  refused member without undoing the others, writes `inbox_answer`; `learn` creates a rule with the line's amount,
  null amount when `amount_exact` false, updates a duplicate, refuses a pattern mismatch, and is skipped when a member
  was refused; `owner_rule_service.apply` books a matching `line_unmatched` at ingest and on the next sweep as
  `actor=owner` with label `rule:<id>` and audit `rule_applied`, skips historical statements / settlement lines /
  proposed cases / reverted cases / lines an owner entry may record (`rule_skipped owner_may_record`) / amount
  mismatch when `amount` set, longest pattern wins, runs before the policy; rule routes list/delete/patch;
  `ActionOut.label`/`answer`; `adjust_amount` via a rule respects the cutover lock.
- SPA (Vitest): the tab renders accounts and cards from an inbox fixture; merged card shows ×N; folded sections; record
  sheet posts `create_entry` params + learn; match sheet posts the picked candidate; refused member stays with the
  message; 已處理 lists actions and reverts; bell count = `counts.ask`; 設定 lists and deletes rules.

## 10. Rollout
Backend first (inbox read + apply + rules + hooks; no data migration beyond the new table), deploy, verify the
inbox JSON against the open cases (on today's 251: bank_only ≈ 25 items, transfer 20, other ≈ 13, ledger_only 98,
amount_differs 27, date_differs 5, ambiguous 12 → ask_items ≈ 187; proposed 17 / settlement, balance_gap 34; the
10-24 import with PR #97 is expected to clear most ledger_only + transfer pairs); then the SPA release (store is 7/7 —
retire the oldest release or raise `max_releases` first). Rules start empty; the owner teaches them from the first
cards after cutover.

## 11. Out of scope / later
Focus mode; the statement-page action sheet; booking transfers / payments from a card (R6c); surfacing
split/move/refund/installment/backfill actions; editing a rule's pattern; rules learned from ledger-only cases; rules
shared across accounts; portfolio Phase 1 case kinds (follow-up mapping into `settlement`); notifications.
