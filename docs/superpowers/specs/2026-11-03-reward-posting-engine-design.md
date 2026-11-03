# Reward posting engine (回饋規則 phase 2) — design

Status: draft rev 1.1, 2026-11-03 (builder check folded, §13). Issues: #288 (calendar-month window), #289 (MOZE miles rules cannot be edited).
Builds on `docs/superpowers/specs/2026-10-31-reward-rules-design.md` §9.1. Its owner rulings of 2026-10-31 stay in
force, and this document extends them.

## 1. Why

MOZE is gone. The owner records every entry through Hermes into HomeHub; the last MOZE backup was imported
2026-10-23. Until then, MOZE computed every reward row and HomeHub only stored the result
(`moze_backup_import_service.py` `_link_rewards_and_attachments`, amount = MOZE's `price`). Nothing computes rewards
now. Counts from production on 2026-11-03:

| month | reward rows | card spends | spends linked to a rule |
|---|---|---|---|
| 2026-09 | 48 | 61 | 45 |
| 2026-10 | 55 | 106 | 65 |
| 2026-11 (to the 3rd) | 0 | 5 | 0 |

The owner wants the engine for **reference, not accounting truth**: they check the bank app before they rely on a
number. They do want to know when a card is close to a cap, and they want Hermes to pick the right rule and rate at
recording time and watch the cap for them (owner, 2026-11-03).

## 2. Goals

1. A card spend linked to reward rules produces reward rows the way MOZE did. There is one reward row per (purchase,
   rule), in the rule's reward account, dated on the rule's posting date, with `reward_source_entry_id` and
   `reward_rule_id` set. Owner ruling 2026-11-03: write real reward rows, as MOZE did.
2. Caps are applied per window, shared caps across their rules, and `txn_rounding` / `total_rounding` as
   the rule says.
3. Windows: `statement_cycle` (today's only value) **and `calendar_month`** (#288).
4. Miles and points-per-spend rules ("NT$X = 1 mile, first N miles a month") can be expressed and computed. Today
   they are MOZE placeholders (`method = fixed`, `fixed_amount = 0`) that the API refuses to edit (#289).
5. **Bank wins** when the statement arrives (owner ruling 2026-11-03, option A, restating 2026-10-31). A statement
   reward line is matched to the engine's rows for that window. Their amount becomes the bank's figure. Rows the bank
   did not pay become 未獲得 (kept, amount 0, visible). No duplicate reward row is booked.
6. **Cap usage is readable**: per rule, the current window's estimated reward vs its cap, through the API (Hermes)
   and on the account settings 回饋規則 block (SPA).
7. Hermes knows which rules a card has and picks rules when recording. When it is unsure, it asks the owner, and if
   asking is not possible it guesses (owner, 2026-11-03). It warns when a pick would land past or near a cap.
8. Backfill: card spends recorded after MOZE stopped get links and reward rows, decided case by case with the owner.

## 3. Non-goals

- Exact agreement with the bank. Bank wins replaces estimates when a statement shows the figure; points and miles
  accounts that never appear on a card statement stay estimates, posted on their date.
- Recomputing MOZE-era reward rows. Rows with no accrual (§4) are history and never touched. The engine only runs
  over them in the oracle check (§9).
- Merchant scopes, MCC codes, spend thresholds, tiers (CUBE levels), and automatic rule choice inside HomeHub. The
  choice of rule stays with Hermes and the owner (phase 3 per the 10-31 design).
- Points-account redemptions (小樹點 → 哩程): 餘額調整 as today.
- The terms-check changes of #287. That issue gets its own small change; once this ships, `window` findings become
  real findings again.
- The weekly digest line 預估 / 實得 / 差 from the 10-31 rulings is deferred to a follow-up issue (my call: the data
  will exist after this ships; the copy is its own small change).

## 4. Model

### 4.1 Accruals live outside the ledger until they post (my call)

New table `reward_accrual`, one row per (source entry, rule):

| column | meaning |
|---|---|
| `id` | |
| `source_entry_id` | the purchase or refund; FK `ledger_entry`, `ON DELETE CASCADE` |
| `rule_id` | FK `reward_rule` (RESTRICT, matching `entry_reward_rule`) |
| `window_start`, `window_end` | the window this accrual counts in (dates, inclusive) |
| `post_on` | the computed posting date (null for `manual` posting) |
| `estimate` | per-transaction amount after `txn_rounding`, before the window cap; signed (refunds negative) |
| `amount` | the amount the window allots to this row after cap / shared cap / `total_rounding`; recomputed |
| `state` | `estimated` → `posted` → `bank_confirmed` or `forfeited` (未獲得) |
| `ledger_entry_id` | the reward row once posted; FK `ledger_entry`, unique, `ON DELETE SET NULL` |
| `updated_at` | |

Unique (`source_entry_id`, `rule_id`).

Why a table rather than a state column on `ledger_entry`: the 10-31 ruling says balances and reports ignore 預估
rows. Keeping estimates off the ledger meets that without touching every balance, report and reconciliation query.
The ledger only ever holds posted rewards, exactly as it held MOZE's. MOZE-era reward rows have no accrual, and
"has an accrual" is what marks an engine row.

### 4.2 Rule changes

- `window`: enum adds `calendar_month` (alembic enum migration; `REWARD_WINDOWS`, `schemas/writes.py`,
  `schemas/ledger.py`, the SPA editor).
- `method`: adds **`per_unit`**. One unit is earned per `unit_spend` of spend (`unit_spend` Numeric(20,4) > 0,
  new column), e.g. `unit_spend = 20` for "NT$20 = 1 mile". `txn_rounding` applies to the units (banks usually floor).
  `percent` and `fixed` keep their meaning.
- Validation (#289): an update that leaves a stored `fixed_amount = 0` unchanged is accepted, so the owner can still
  edit a MOZE placeholder's description before migrating it. New writes keep `fixed_amount > 0`.
- Rollout (§10) migrates the known MOZE miles placeholders to `per_unit`, rule by rule, with the owner.

### 4.3 Windows

- `statement_cycle`: the cycle of the rule's card containing the purchase's `entry_date`, from the card's
  `closing_day`. If the card has no `closing_day` and is `combined_account_id` into a parent, use the parent's
  `closing_day`. If there is still none, use the calendar month (`notify/cycles.statement_period` already behaves
  this way). Move `statement_period` / `shift_period` into a shared `app/services/cycles.py` and have notify import
  it. The SPA's `cycle.ts` is the reference port; behaviour must stay identical.
- `calendar_month`: the calendar month of `entry_date`.
- Window membership is by `entry_date` (the purchase date; "以刷卡日為準"), not `posted_date`.

### 4.4 Posting date

As specified in the 10-31 design table:
- `after_window`: `post_day` of the month `post_month_offset` months after the month the window **ends** in, clamped
  to that month's last day, and never earlier than `window_end` (raised to it). The rule API rejects nothing new here,
  because existing rules may hit the clamp (§13 Q6).
- `after_transaction`: `entry_date + delay_days`.
- `manual`: no `post_on`. The accrual stays `estimated` until someone posts it through the API (§6) or it is
  forfeited.

## 5. Engine behaviour

### 5.1 When it runs

- **On entry write** (POST / PUT / DELETE `/entries`, split, refund, transfer create/update, every path that calls
  `write_rule_links`): within the same transaction, the touched entry's accruals are created, updated or deleted,
  and every window it touches (old and new) is recomputed.
- **On rule or account write** (§13 Q5): a reward rule PUT (rate, cap, method, `unit_spend`, window, posting, dates,
  shared cap) or a card's `closing_day` change re-buckets and recomputes that rule's / card's `estimated` accruals.
  Posted accruals are never repriced.
- **Daily job**, in the API process like `auto_pay_job` / `schedule_job` (`app/main.py`), Asia/Taipei. For every
  `estimated` accrual with `post_on <= today`, it recomputes the window, creates the reward ledger row (kind
  `reward`, account = the rule's `reward_account_id` or the card, `entry_date = posted_date = post_on`, project per
  #262 inheritance: the rule's `reward_project_id`, else the purchase's project), links it, and sets `posted`.
- **Freezing is per accrual, not per window** (§13 Q1). A posted accrual's amount is fixed. While a window still has
  `estimated` accruals (later purchases, `after_transaction` or `manual` rules, shared-cap members with other
  posting), they are allotted the cap left after the posted amounts. An edit that changes a posted accrual's purchase
  (amount, rules, date) does not reprice it. The edit sets the purchase's `needs_review` and records the reason
  `reward_posted` in a new nullable `review_reason` column (my call; today `needs_review` has no reason storage).
- **Project changes** (10-31 ruling, in scope): a PUT that changes a purchase's project moves the project of its
  rewards, estimated and posted, when the reward's project is null or equal to the old one. A project is not a price,
  so freezing does not block it.

### 5.2 Computing a window

For one rule (or one shared-cap group) and one window, in `entry_date`, `id` order:
1. Each accrual's `estimate`: `percent` → |amount| × rate / 100; `fixed` → `fixed_amount`; `per_unit` →
   |amount| / `unit_spend` units. Apply `txn_rounding`. Refunds (`kind = refund` with `refunds_entry_id`) carry the
   refunded purchase's rules and produce negative estimates in the window of the **purchase** (my call; banks claw
   back from the original window when they can).
2. Allot in order against `total_cap` (or the shared cap's remaining amount across the group's rules). An accrual's
   `amount` is the part that still fits. Past the cap it is 0.
3. `total_rounding` applies to the window total. The rounding difference goes on the window's last accrual (my
   call; it keeps rows summing to the rounded total).
4. Amounts use the source entry's account-currency `amount` (TWD for TWD cards; the ledger already stores the
   converted amount for foreign spends).

### 5.3 Which entries accrue

Only entries with `entry_reward_rule` links. The engine never picks rules. Links are validated as today
(`check_rules`: rule of the same account, enabled on `entry_date`).

- **No double booking** (§13 Q4): a (purchase, rule) that already has a reward row with that `reward_source_entry_id`
  and `reward_rule_id` (MOZE era) gets no accrual, whatever the edit.
- **Straddling windows:** existing reward rows of a rule (MOZE era, no accrual) whose source purchase falls in an open
  window count as already-used cap in that window, read-only.
- Rules still in the MOZE placeholder shape (`method = fixed`, `fixed_amount = 0`) produce no accrual until they are
  migrated (§10.3).

## 6. API

- `GET /accounts/{id}/reward-usage?date=YYYY-MM-DD` (read scope): for each in-date rule of the card, gives the
  window containing `date`, the estimated total, the cap, the remaining amount, and the shared-cap group. Default
  `date` = today (Asia/Taipei).
- `GET /entries/{id}` and list endpoints expose an entry's accruals (rule, estimate, amount, state, post_on) so the
  SPA and Hermes can show 「預估回饋」.
- `POST /reward-accruals/{id}/post` (write scope): posts a `manual` accrual today or on a given date.
- Reward rule API: `calendar_month`, `per_unit` + `unit_spend`, and the #289 relaxation.

## 7. Bank wins (statement reconciliation)

Scope: a card whose statement prints reward lines (cash back credited to the card). These are rules whose reward
account is the card itself.
- **Which statement pays which rows** (§13 Q2): a reward is credited to the card on its `post_on`, so it belongs to
  the statement whose period contains `post_on`. The candidates for a statement's reward lines are that card's
  engine rows (posted, or posted early, below) with `post_on` in the statement period. Match by account and period,
  not by exact amount.
- **Several lines, several rules** (§13 Q3): all of the statement's `reward` lines are summed, and so are all the
  candidate rows. The bank total is distributed over the rows in proportion to their amounts, with the remainder on
  the last row by `post_on`, `id`. Each row's ledger amount is updated, its accrual set to `bank_confirmed`, and the
  lines close as matched. Rows that come out at 0 become `forfeited` (ledger amount 0, kept, visible as 未獲得).
- **No reward line at all:** a statement whose period contains `post_on` but which prints no reward line leaves
  the rows `posted`. When the **next** statement is reconciled and still shows no matching line, they become
  `forfeited` (my call: banks sometimes credit one cycle late).
- A statement reward line with no engine rows behind it is booked as today (`create_system_entry`).
- Engine rows on points or miles accounts never meet a card statement: they stay `posted`.
- Statements whose posting runs ahead of the engine (line arrives before `post_on`): the reconciler may post the
  window early (run §5.1's posting step for it) and then match. It is the builder's call whether to do this or to
  leave the case open until the daily job runs; pick whichever keeps the reconciliation code smallest.

## 8. SPA and Hermes

- **Account settings 回饋規則 block:** each in-date rule row shows 「本期 {used}／{cap}」 (or 「本期 {used}」 when
  uncapped) and the window dates. A shared cap shows once, on the first rule of the group.
- **Rule editor:** window selector (帳單週期 / 自然月); method gets 每 NT$X 一單位 (`per_unit`) with `unit_spend`.
- **Entry detail:** the 預估回饋 lines with their state (預估 / 已入帳 / 銀行確認 / 未獲得).
- **Hermes `homehub-api` skill** (outside the repo, `~/.hermes/skills/productivity/homehub-api/`):
  - recording a card spend reads the card's in-date rules (GET `/accounts/{id}/reward-rules`) and picks the
    applicable ones, always including the basic rule;
  - when unsure, it asks the owner, and if it cannot ask it guesses and says so;
  - after picking, it reads `/reward-usage` and warns at ≥ 80 % of a cap or when the purchase would cross it;
  - it documents `per_unit`, `calendar_month` and bank wins.

## 9. Oracle check (acceptance)

Before rollout, run the engine read-only (a script, no writes) over the MOZE era: every linked purchase with a
MOZE reward row of the same (purchase, rule). Report per rule how many rows match within NT$1 (or 1 unit), how many
differ, and the largest differences. This decides the ambiguous MOZE mappings (rounding codes, posting codes noted
as provisional in `moze_backup_json.py:302-308`). The bar: ≥ 95 % of rows within tolerance for percent rules.
Differences explained by known MOZE quirks are recorded in the release notes. The engine does not have to match
the placeholder miles rules until they are migrated.

## 10. Rollout

1. Merge; deploy API + worker; migration adds the table, enums and column. No engine output yet for existing data.
2. Oracle check (§9) on production data; owner reads the summary.
3. Data pass with the owner, card by card (owner ruling 2026-10-26: one case per question):
   - migrate the MOZE miles placeholders to `per_unit` (3 cards, found by the 2026-11-03 data pass);
   - switch the calendar-month programmes the 2026-11-03 terms check found (2 cards, 4 rules) to `calendar_month`;
   - card names, rule ids and rates live in the owner's data-pass log under `~/reports/`, not in the repo.
4. Backfill: the 27 card spends since 2026-10-01 on cards with rules and no links (count on 2026-11-03), plus a
   one-off seed run over spends since 2026-10-01 that already have links but no reward row (§13 Q4). Windows whose
   statement reward line was already booked by `create_system_entry` are decided per case with the owner, so the same
   reward is never counted twice.
   - Hermes proposes links per card.
   - The owner confirms, again case by case.
   - The engine computes. Windows already past their posting date post on the next daily run.
5. Hermes skill update; the next terms-check run sees the switched windows and stops reporting them.

## 11. Left to the builder

- The shape of the shared cycle helper and how notify imports it.
- Whether recompute-on-write runs inline or via a small deferred queue in the same transaction.
- Review-flag plumbing for `reward_window_posted`.
- The reconciliation change's exact place (candidate builder vs a pre-match step).
- Test fixtures: fictional cards with a percent rule, a capped rule, a shared cap, a `per_unit` rule, a
  calendar-month rule, a refund and a `manual` rule.

## 12. Decisions made in this document (owner can veto)

- Accruals in their own table; the ledger holds only posted rewards (§4.1).
- `per_unit` method added in this phase, which also closes #289 (§4.2).
- Refunds claw back in the purchase's window (§5.2.1).
- The rounding remainder goes on the window's last row (§5.2.3).
- Posted windows are frozen, and later edits raise a review flag (§5.1).
- Hermes warns at 80 % of a cap (§8).
- The weekly digest line is deferred (§3).
- Freezing per accrual; new purchases get the cap left after posted amounts (§5.1, §13 Q1).
- A statement pays the rows whose `post_on` is in its period; lines and rows are matched as totals; a missing line
  forfeits only after the next statement (§7).
- Rule and closing-day edits recompute estimated accruals (§5.1).
- `post_on` is raised to the window end, not rejected (§4.4).

## 13. Builder check (2026-11-03): questions and how they were settled

1. Windows posting on different days (`after_transaction`, `manual`, mixed shared caps): freeze per accrual; the
   rest of the window is allotted the cap left after posted amounts (§5.1).
2. Which statement pays a window: the one whose period contains `post_on`. A missing line forfeits after the next
   statement (§7).
3. Several reward lines or rules: match statement totals to row totals and distribute proportionally (§7).
4. MOZE-era purchases and straddling windows: no accrual where a reward row already exists for the (purchase,
   rule); existing rows count as used cap; seed run for linked spends since 10-01; double-booked statement lines
   decided case by case (§5.3, §10.4).
5. Rule and closing-day edits recompute estimated accruals (§5.1).
6. A `post_on` earlier than the window end is raised to it (§4.4). Project propagation is in scope (§5.1). The daily
   job runs in the API process like the existing jobs, not on a worker timer.
