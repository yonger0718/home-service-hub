# Reconciliation inbox (待處理) and owner-taught rules — design

Status: rev 1 (2026-10-21), from the owner's brainstorm of 2026-10-21 (visual companion session, options A + merge + fold
chosen; focus mode rejected). Replaces the "R3b action sheet on the statement page" item of the reconciliation spec
(`2026-10-08-statement-reconciliation-design.md` §17) — the statement page stays read-only.

## 1. Intent

The owner does not want to study bank lines. When the daily reconcile cannot pair a bank line with a HomeHub entry, the
system should ask one plain-language question with two or three answers, the owner taps one, and the ledger is fixed.
When the same question recurs every month (a loan repayment, a card autopay that differs by the reward offset), the
owner answers once and ticks "以後都這樣"; the daily reconcile then applies that answer by itself. Today every one of
these decisions goes through the API by hand, and nothing is remembered.

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
  account, no entries); only `GET /reconciliation/cases/{id}` carries the line and `actions_allowed`. Prod today:
  251 open/proposed (line_unmatched 58 + 17 proposed, entry_unmatched 98, amount_delta 32, ambiguous 12,
  balance_gap 34); top hints `needs_peer` 20, `foreign` 17, `near` 12, `installment_unmapped` 8.
- Actions: the registry in `app/services/reconciliation_actions/` (17 actions, `allowed(case)` per kind + conditions);
  `engine.apply_action(db, case_id, action, params, actor, label, request_id, proposal_id, run_id, budget, precheck)`;
  idempotency key `(case_id, version, action, params)`; `actor` enum `owner | policy`; `revert` refused after a later
  action touched the same rows. `adjust_amount` is refused on MOZE rows until `ACCOUNTING_IMPORT_LOCKED=true` (cutover
  2026-10-24).
- Proposals: `reconciliation_proposal` (author `policy` for over-cap policy candidates, agent labels otherwise);
  `POST /reconciliation/proposals/{id}/apply|reject`.
- Policy: `policy_service.run_policy_safe(db, statement_id)` runs after each reconcile and in the daily
  `POST /reconciliation/sweep` (`routers/statements.py:290-316`: `reconcile_each` → commit → `run_policy_safe`).
  Policy actors are budget-capped; owner actions are not.
- Settings: one JSON row `reconciliation_settings.data` (`account_map`, `composite_map`, `dirty_enabled`, `rules` =
  matching.Rules overrides — the name is taken, `policy`, `account_siblings`); PUT replaces it wholesale (ADMIN).
  `installment_plan_map` is the precedent for a per-account owner-taught mapping in its own table.
- SPA: 提醒中心 is `AccountingRemindersComponent` (`/accounting/reminders?tab=all|cards|debts|pending`), loads with one
  `forkJoin`, reloads on `accounting.entriesChanged()` / `accountsChanged()` / `statements.changed()`; the 🔔 count is
  `TimelineComponent.reminderCount`. Sheets share `sheet.scss` + the `[data-overlay]` focus contract; `CategoryPicker`
  is an inline drill-in grid; `AccountPicker` is a popover/sheet. The SPA has no reconciliation calls today. Caddy's
  `@hub_spa` allowlist does not include new paths: a tab on `/accounting/reminders` needs no Caddy change.
- Auth: the SPA token has scope `legacy` (read + write); `/reconciliation/*` additionally needs the feature flag
  `ACCOUNTING_RECONCILIATION_ENABLED` (on in prod); settings PUT needs `admin` (the SPA does not have it — see §6.4).

## 4. The inbox read model — `GET /reconciliation/inbox`

One composite endpoint (READ scope) so a card costs no extra round trip. Query: `status=open,proposed` (default both),
`account_id` (optional), `limit` (default 500, max 1000).

Response `InboxOut`:
```
{
  "generated_at": ISO, "counts": {"ask": n, "proposed": n, "balance_gap": n, "settlement": n, "other": n},
  "accounts": [ {"id", "name", "currency", "is_credit", "items": [InboxItem…]} … ]   // accounts with items, by name
}
InboxItem = {
  "case_id", "case_version", "kind", "status",
  "question": "bank_only" | "ledger_only" | "amount_differs" | "date_differs" | "ambiguous" | "balance_gap"
            | "settlement" | "proposed" | "installment" | "other",
  "folded": bool,                       // true for proposed / balance_gap / settlement / other
  "group_key": str | null,              // §4.2; same key = one merged card
  "statement": {"id", "period_end"},
  "line": {"id", "date", "merchant", "amount", "line_kind"} | null,     // date = posted_date, amount = flow_amount
  "entry": {"id", "date", "title", "amount", "account_id"} | null,      // the case's entry (entry cases) — title = name|merchant|category
  "candidates": [{"entry_id", "date", "title", "amount", "reason", "residual", "date_distance"} …],  // resolved, ≤ 5
  "proposal": {"id", "action", "params", "author", "rationale"} | null, // the pending proposal on a proposed case
  "actions": ["record", "match", "accept_bank_amount", "match_candidate", "dismiss", "apply_proposal", …],  // §4.3
  "rule_hint": {"merchant_pattern", "line_kind"} | null                 // what "以後都這樣" would store (§6)
}
```

### 4.1 `question` (the plain-language class) — derived server-side, one rule each
| question | from |
|---|---|
| `settlement` | `line_unmatched` on a bank account whose line merchant contains one of `settlement_labels` (`["交割股款", "申購股款"]`, the same list the portfolio spec §8 defines; read from settings when present, else the default) |
| `proposed` | status `proposed` (a pending proposal exists) and not `settlement` |
| `installment` | `line_unmatched` with hint `installment_unmapped` |
| `bank_only` | `line_unmatched` otherwise (bank has it, ledger does not) |
| `ledger_only` | `entry_unmatched` |
| `amount_differs` | `amount_delta` with candidate reason `near` or `foreign` |
| `date_differs` | `amount_delta` with candidate reason `date_drift` |
| `ambiguous` | `ambiguous` |
| `balance_gap` | `balance_gap` |
| `other` | everything else (`duplicate_claim`, `recheck`, `parse_review`, `statement_conflict`) |

`folded` = question ∈ {proposed, balance_gap, settlement, other}. `counts.ask` = unfolded items.

### 4.2 `group_key` (merged cards)
For `bank_only` and `ledger_only` only: `f"{account_id}|{line_kind}|{merchant_norm or title}|{sign(amount)}"`
(ledger_only uses the entry's title). Two or more unfolded items with one key → the SPA renders one card with
"×N 筆" and the member dates; an answer applies to every member (§5). Amount is NOT in the key: a loan repayment is
the same answer whether it is 3,865 or 3,870; the card shows each member's amount.

### 4.3 `actions` — the owner's buttons, mapped onto registry actions
| button (SPA label) | item.actions value | registry action and params | when offered |
|---|---|---|---|
| 記為支出／收入… | `record` | `create_entry {kind, category_id, name?, project_id?}` (kind from the line's sign; the sheet picks the category, defaults to the last category used with this `merchant_pattern` on this account) | `bank_only` with `create_entry` in `actions_allowed` |
| 已記在別處，對到… | `match_candidate` | `match {line_id, entry_id}` on a candidate the owner picks from `candidates` (or from a search of that account's entries ±14 days, same sign) | `bank_only`, `ambiguous`, `date_differs` with `match` allowed |
| 改成銀行的數 | `accept_bank_amount` | `adjust_amount {entry_id, line_id}` | `amount_differs` with `adjust_amount` allowed |
| 對到 M/D 那行 | `match_candidate` | `match {line_id, entry_id}` | `date_differs` |
| 等下期 | `defer` | `defer_next_period {entry_id}` | `ledger_only` |
| 不是同一筆 / 忽略 | `dismiss` | `dismiss {note}` — note = the SPA's fixed text `"inbox: <button label>"` | every kind that allows `dismiss` |
| 全部套用 / 套用 / 退回 | `apply_proposal` / `reject_proposal` | existing proposal routes | `proposed` |
| 對齊銀行期初 | `anchor` | `anchor_opening {}` | `balance_gap` when allowed |

Everything else the registry offers (`split_entry`, `move_account`, `create_refund`, `attach_fee`, `link_installment_plan`,
`replace_stand_in`, `balance_adjustment_asof`, `unmatch`, `resolve_conflict`) is NOT surfaced in this round; such a case
shows as `other` with only 忽略, and the card says "需要在 API 處理" with the case id. `actions` is computed from
`registry.allowed(case)` filtered through this table, so the SPA never offers what the engine would refuse.

## 5. Acting — `POST /reconciliation/inbox/apply`

One WRITE call per tap, also for merged cards:
```
{ "case_ids": [int, …],                 // 1..50, all the same question and (for learn) the same group_key
  "action": "record" | "match_candidate" | "accept_bank_amount" | "defer" | "dismiss" | "anchor",
  "params": {…},                        // per the table in §4.3; for match_candidate on a merged card: {"entry_ids": {case_id: entry_id}}
  "learn": false | {"scope": "account"} // §6: create a rule from this answer
}
→ { "results": [{"case_id", "status": "applied"|"replayed"|"refused", "action_id"?, "code"?}], "rule_id": int|null }
```
Each case is applied in its own transaction through `engine.apply_action(actor="owner", label=<token label>)` — exactly
what the single-case route does — so one refusal (`candidate_changed`, `not_conserved`, a MOZE row before cutover…)
does not undo the others; the response names the refused ones with the engine's code and the SPA shows them as
"沒成功：…" cards that stay. `apply_proposal` / `reject_proposal` use the existing proposal routes (the SPA calls them
per proposal; "全部套用" is a loop with a progress count — proposals are few).

The owner's answer in plain words (`"inbox: 記為支出 餐飲"`) goes into the audit detail so the 已處理 list can show it.

## 6. Owner-taught rules

### 6.1 Table `reconciliation_owner_rule` (one migration; the `installment_plan_map` precedent)
```
id, account_id (FK account, not null), enabled bool default true,
match: line_kind text|null, merchant_pattern text (not null; matched case-insensitively as a substring of
       statement_line.merchant_norm, NFKC-normalised, whitespace collapsed), flow_sign ('+'|'-'|null),
       amount_tolerance numeric(12,2) null   // only for accept_bank_amount: |line − entry| ≤ tolerance
answer: action text ('record'|'accept_bank_amount'|'dismiss'|'defer'),
        params jsonb ({category_id, name?, project_id?} for record; {} otherwise),
created_from_case_id, created_at, last_applied_at, applied_count int default 0, note text null
UNIQUE (account_id, merchant_pattern, line_kind, flow_sign)
```
A rule is one account, one pattern, one answer. `match_candidate` is never learned (the candidate is a specific entry).

### 6.2 Creating a rule
`learn: {"scope": "account"}` on `/inbox/apply`: the server derives the pattern from the case's line — `merchant_norm`
with digits and card-number masks removed (`rule_hint.merchant_pattern`, shown on the card as the exact text the rule
will store, editable in the sheet), `line_kind`, `flow_sign` — and the answer from the request. For a merged card the
pattern comes from the first member and must match every member, else 422 `rule_pattern_mismatch`. A duplicate
(same unique key) is updated, not duplicated. The rule applies to the OTHER members of the merged card in the same
request (so the owner sees the rule work immediately) and to future lines.

### 6.3 Applying rules — `owner_rule_service.apply(db, statement_id, *, run_id)`
Runs in the daily sweep and after a reconcile, between `reconcile_each` → commit and `policy_service.run_policy_safe`
(`routers/statements.py:290-316`, `:286`), on live statements only. For each open case of a kind a rule can answer
(`line_unmatched` → record/dismiss; `amount_delta` with a single candidate → accept_bank_amount within tolerance;
`entry_unmatched` → dismiss/defer): the first enabled rule of the statement's account whose pattern matches (longest
pattern wins, then lowest id) is applied through `engine.apply_action(actor="owner", label=f"rule:{rule.id}")` with an
audit event `rule_applied {rule_id, case_id, action}`; a refusal is audited `rule_skipped {rule_id, code}` and never
retried in the same run. Rules never touch cases on historical statements, settlement lines, or cases with a pending
proposal. `actor="owner"`: a rule is the owner's standing instruction, so the policy caps and budgets do not apply and
`resolved_by` stays `owner`; the audit label distinguishes it.

### 6.4 Managing rules
`GET /reconciliation/owner-rules` (READ), `DELETE /reconciliation/owner-rules/{id}` (WRITE), `PATCH … {enabled}` (WRITE).
The SPA's token has write but not admin, so rules live in their own routes instead of the ADMIN-only settings PUT.
記帳設定 gains a `<details>` block "對帳規則" listing account · pattern · answer · applied_count · last_applied_at with the
two-tap delete used by the 對象 list.

## 7. The SPA

### 7.1 Tab and count
- `ReminderTab` gains `'todo'` (label 待處理), shown between 全部 and 信用卡帳單; `?tab=todo` deep-links it. 全部 shows the
  first three unfolded cards with a "還有 N 張 →" link to the tab.
- `TimelineComponent.reminderCount` adds `inbox.counts.ask` (one `GET /reconciliation/inbox?limit=1` whose `counts` are
  computed server-side regardless of `limit`). The bell count is "things to decide", not all cases.

### 7.2 The tab (option A of the mockups)
- Header line: "要你決定 N · 其他 M"; then per account (name, currency, is_credit icon) its unfolded cards, newest
  statement first, then within a statement by line date.
- A card: first line bold = date + merchant + signed amount (`formatMoney(sign)`); second line muted = the question text
  (table below); then the buttons; then, when `rule_hint` is present and the action is learnable, "☐ 以後「<pattern>」
  都這樣" (ticked by default on a merged card). A merged card shows "×N 筆" and the member dates/amounts collapsed.
- Question texts (zh-TW, one per `question`): bank_only 「銀行有、你沒記」; ledger_only 「你記了，銀行沒有」;
  amount_differs 「銀行 X，你記 Y（差 Z）」; date_differs 「你記 M/D，銀行 M/D 扣」; ambiguous 「有 N 筆可能是它」;
  installment 「分期，還沒對到計畫」; other 「需要在 API 處理（案件 #id）」.
- Folded sections under the cards, each a `<details>`: 「系統有提議 N（全部套用）」, 「期初差額 N」(each with 對齊銀行期初
  when allowed, else 忽略), 「國泰交割款 N — 等證券連動」(no buttons), 「其他 N」.
- Sheets (reuse `sheet.scss` + the overlay contract): **record** = category picker (inline `CategoryPicker` for the line's
  kind, default from `rule_hint` → last used; optional name; optional project) + the learn tick + 確定; **match_candidate**
  = list of candidates (date · title · amount) + "搜尋這個帳戶的記錄" (14-day window, same sign) + 確定;
  **accept_bank_amount** and **dismiss** need no sheet (one tap; dismiss on a merged card asks 確定 once).
- After a successful apply the card leaves the list immediately; the page reloads through the existing
  `entriesChanged()` effect (every apply bumps it, since it writes entries) and `statements.changed()`.
- Errors: a refused member stays as a card with the engine code mapped by `actionErrorMessage` (+ new codes
  `candidate_changed` 「對象已變，重新整理」, `rule_pattern_mismatch`, `not_conserved` 「金額對不上，不能直接對」).

### 7.3 已處理
A third section on the tab (collapsed): the last 50 `GET /reconciliation/actions` rows with actor/label (`rule:<id>` →
「規則」, `policy` → 「系統」, else 「你」), the plain answer from the audit detail, and 撤銷 (existing revert route; a
`not_revertable` refusal is shown as text).

## 8. Failure modes
- Case changed under the card (`case_version` moved): the apply is refused `candidate_changed` / `proposal_superseded`;
  the SPA reloads the inbox and keeps the card with the message. No silent re-targeting.
- A rule matches the wrong line: every rule application is one audit row and one revertable action; the 已處理 list
  shows it with 撤銷; deleting the rule stops it. A rule never matches across accounts.
- MOZE rows before cutover: `adjust_amount` (accept_bank_amount) is refused `locked_until_cutover`; the card shows
  「cutover 後才能改」. After 2026-10-24 this disappears.
- Two owners' sessions / a sweep racing an apply: the engine's idempotency key and effect slots already serialise;
  the batch endpoint applies per case and reports.
- Inbox size: 500 items hard limit; the response is ≤ a few hundred KB; counts are computed with one grouped query.

## 9. Testing (named so the plan can pin them)
- Backend unit/integration: `question` classification per kind/hint (table-driven); `group_key` merges same-shape lines
  and never merges across accounts or signs; `actions` is a subset of `registry.allowed`; `/inbox/apply` applies N cases
  in N transactions and reports a refused member without undoing the others; `learn` creates a rule, updates a
  duplicate, refuses a pattern mismatch, and applies to the other members in the same request;
  `owner_rule_service.apply` books a matching `line_unmatched` on the next sweep as `actor=owner` with label
  `rule:<id>` and audit `rule_applied`, skips historical statements / settlement lines / proposed cases, longest pattern
  wins, refusal audited `rule_skipped`; rule routes list/delete/patch; `adjust_amount` via a rule respects the cutover lock.
- SPA (Vitest): the tab renders accounts and cards from an inbox fixture; merged card shows ×N; folded sections; record
  sheet posts `create_entry` params + learn; match sheet posts the picked candidate; refused member stays with the
  message; 已處理 lists actions and reverts; bell count = `counts.ask`; 設定 lists and deletes rules.

## 10. Rollout
Backend first (inbox read + apply + rules + sweep hook; no data migration beyond the new table), deploy, verify the
inbox JSON against today's 251 cases (counts per question — expected roughly: ask ≈ 90, proposed 17, balance_gap 34,
settlement 14); then the SPA release (store is 7/7 — retire the oldest release or raise `max_releases` first).
Rules start empty; the owner teaches them from the first cards after cutover.

## 11. Out of scope / later
Focus mode; the statement-page action sheet; surfacing split/move/refund/installment actions; rules with amount ranges
beyond the tolerance; rules shared across accounts; a rules editor beyond enable/delete; notifications.
