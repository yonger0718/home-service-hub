# Reward posting engine (回饋規則 phase 2) — design

Status: draft rev 2.2, 2026-11-03 (builder check folded, §13; Multica design review AGENT-394, its re-check and confirm folded, §14). Issues: #288 (calendar-month window), #289 (MOZE miles rules cannot be edited).
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
| `source_entry_id` | the purchase or refund; FK `ledger_entry`, **nullable, `ON DELETE SET NULL`**. The entry delete path deletes the entry's `estimated` accruals itself before the row goes; `posted`, `bank_confirmed` and `forfeited` accruals survive with a null source (§5.4) |
| `rule_id` | FK `reward_rule` (RESTRICT, matching `entry_reward_rule`) |
| `window_start`, `window_end` | the window this accrual counts in (dates, inclusive) |
| `post_on` | the computed posting date (null for `manual` posting) |
| `estimate` | per-transaction amount after `txn_rounding`, before the window cap; signed (refunds negative) |
| `amount` | the amount the window allots to this row (§5.2). Recomputed while `estimated`; fixed from `posted` on. Bank wins (§7) changes the ledger row, never this column, so a re-run distributes the same way |
| `state` | `estimated` → `posted` → `bank_confirmed` or `forfeited` (未獲得); `estimated` → `forfeited` directly for a 0 allotment at posting (§5.1) or a deleted reward row (§5.4) |
| `ledger_entry_id` | the reward row once posted; FK `ledger_entry`, unique, `ON DELETE SET NULL` |
| `confirmed_statement_id` | the statement whose reconcile set `bank_confirmed` / `forfeited`; FK `account_statement`, `ON DELETE SET NULL`. A re-run of that statement reads these rows again (§7) |
| `updated_at` | |

Unique (`source_entry_id`, `rule_id`) (Postgres treats null sources as distinct, so surviving accruals of deleted
purchases never collide).

Ledger index (dev-grok finding 7): a partial unique index on `ledger_entry (reward_source_entry_id, reward_rule_id)`
`WHERE reward_source_entry_id IS NOT NULL AND reward_rule_id IS NOT NULL`. The migration first counts duplicate
pairs. With none it creates the index; with any it skips the index, logs the count and the entry ids, and the
release notes carry them for the owner. The engine's own duplicate guard (§5.1) does not depend on the index.

Why a table rather than a state column on `ledger_entry`: the 10-31 ruling says balances and reports ignore 預估
rows. Keeping estimates off the ledger meets that without touching every balance, report and reconciliation query.
The ledger only ever holds posted rewards, exactly as it held MOZE's. MOZE-era reward rows have no accrual, and
"has an accrual" is what marks an engine row.

### 4.2 Rule changes

- `window`: enum adds `calendar_month` (alembic enum migration; `REWARD_WINDOWS`, `schemas/writes.py`,
  `schemas/ledger.py`, the SPA editor).
- `method`: adds **`per_unit`**. One unit is earned per `unit_spend` of spend (`unit_spend` Numeric(20,4), new
  nullable column), e.g. `unit_spend = 20` for "NT$20 = 1 mile". `txn_rounding` applies to the units (banks usually
  floor). `percent` and `fixed` keep their meaning.
- Method validation (finding 9), 422 per field: `per_unit` requires `unit_spend > 0` and forbids `rate` and
  `fixed_amount`; `percent` and `fixed` forbid `unit_spend`.
- #289 (finding 9): the PUT body takes `fixed_amount ≥ 0` (`NonNegativeMoney`); POST keeps `Money` (> 0). The
  service accepts 0 on PUT only when the stored rule is `method = fixed` with `fixed_amount = 0` and the payload keeps
  `method = fixed`; any other 0 is 422 `fixed_amount` "must be greater than 0". The owner can then edit a MOZE
  placeholder's description before migrating it.
- Shared caps (finding 6): rules sharing a cap must use the same `window`. A PUT that joins a cap
  (`share_cap_with`) or changes the `window` of a group member, leaving the group with two window values, is 422
  `share_cap_with` / `window` "rules sharing a cap must use the same window". Groups are already one account's rules
  (`reward_rule_service.py:108`), so one window value means the same window dates. The group's cap is the smallest
  non-null `total_cap` of its rules; all null means uncapped (my call; today each rule stores its own `total_cap`).
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

- **On entry write** (POST / PUT / DELETE `/entries`, split, transfer create/update, every path that calls
  `write_rule_links`): within the same transaction, the touched entry's accruals are created, updated or deleted,
  and every window it touches (old and new) is recomputed.
- **Refunds** (finding 4): `settlement_service.refund` (`settlement_service.py:94`) writes no rule links, and refund
  rows carry none. `refund()` itself creates the refund's accruals, one per rule linked to the refunded purchase,
  in the **purchase's** window, and recomputes those windows. A change to the purchase's links re-derives its
  refunds' `estimated` accruals the same way.
- **On rule or account write** (§13 Q5): a reward rule PUT (rate, cap, method, `unit_spend`, window, posting, dates,
  shared cap) or a card's `closing_day` change re-buckets and recomputes that rule's / card's `estimated` accruals.
  Frozen accruals (`posted` and later) are never repriced or moved.
- **Daily job**, in the API process like `auto_pay_job` / `schedule_job` (`app/main.py`), Asia/Taipei (finding 7):
  - one `pg_try_advisory_lock` on a new key per run, skipping the run when busy, as `auto_pay_job.py:213` does;
  - each `estimated` accrual with `post_on <= today` is posted in its own transaction: lock the accrual row
    `FOR UPDATE`; skip it unless it is still `estimated` with `ledger_entry_id` null; recompute its window; insert
    the reward row and set `ledger_entry_id` and `posted` in that same transaction;
  - the reward row: kind `reward`, account = the rule's `reward_account_id` or the card, **currency = that
    account's currency**, amount = the accrual's `amount`, `entry_date = posted_date = post_on`,
    `reward_source_entry_id`, `reward_rule_id`, the system reward category, and the project per #262 inheritance
    (the rule's `reward_project_id`, else the purchase's project);
  - an accrual whose allotted `amount` is 0 at posting (past the cap) becomes `forfeited` without a ledger row (my
    call: no 0 rows for purchases past a cap).
  - The manual post endpoint (§6) runs the same per-accrual step.
- **Freezing is per accrual, not per window** (§13 Q1). A posted accrual's amount is fixed. While a window still has
  `estimated` accruals (later purchases, `after_transaction` or `manual` rules, refunds), they are allotted the cap
  left after the frozen amounts. An edit that changes a frozen accrual's purchase does not reprice it.
- **Review flag** (finding 10): `_apply` clears `needs_review` on every entry PUT (`entry_write_service.py:227`), and
  the split paths do the same (`split_service.py:323`, `:332`). A new nullable `review_reason` column holds one
  reason name, `reward_posted`. It is set, with `needs_review = true`, **after** `_apply` in the same transaction,
  when an edit changes the amount, date, account or rule links of a purchase that has a frozen accrual. A later
  purchase PUT that changes none of those keeps the flag and the reason (they are restored after `_apply`). The flag
  clears when the accrual's reward row is edited, or when bank wins confirms or forfeits the accrual.
- **Project changes** (10-31 ruling, in scope): a PUT that changes a purchase's project moves the project of its
  rewards, estimated and posted, when the reward's project is null or equal to the old one. A project is not a price,
  so freezing does not block it and it does not set the review flag.

### 5.2 Computing a window

For one rule (or one shared-cap group) and one window:
1. **Estimates.** `percent` → |amount| × rate / 100; `fixed` → `fixed_amount`; `per_unit` → |amount| / `unit_spend`
   units. Apply `txn_rounding`. A refund's estimate uses the refunded purchase's rule and is negative.
2. **Used cap.** The cap is the rule's `total_cap`, or the group's cap (§4.2). `used` = the amounts of the frozen
   accruals of the group's rules in the window (for `bank_confirmed` and `forfeited`, the ledger row's amount, 0 when
   it has none) + the no-accrual reward rows of the group's rules whose source purchase is in the window (§5.3).
3. **Allotment** (finding 4), `estimated` accruals only, sorted by the source row's `entry_date`, then id:
   `remaining = cap − used`. A negative estimate is allotted in full and raises `remaining`. A positive estimate is
   allotted `min(estimate, max(remaining, 0))`, and `remaining` drops by that amount. Uncapped: `amount = estimate`.
4. **Rounding under freeze** (finding 3): frozen amounts keep `txn_rounding` only and are never rewritten.
   `total_rounding` applies to the window sum (frozen + estimated, clamped so it never exceeds the cap). The difference
   between the rounded sum and the raw sum is added to the **latest still-estimated accrual** in the sort above;
   with `after_window` or `after_transaction` posting that row is also the last to post. A window with no estimated
   accrual left gets no adjustment.
5. Amounts use the source entry's account-currency `amount` (TWD for TWD cards; the ledger already stores the
   converted amount for foreign spends).

### 5.3 Which entries accrue

Only entries with `entry_reward_rule` links, and refunds of them (§5.1). The engine never picks rules. Links are
validated as today (`check_rules`: rule of the same account, enabled on `entry_date`).

- **No double booking** (§13 Q4): a (purchase, rule) that already has a reward row with that `reward_source_entry_id`
  and `reward_rule_id` (MOZE era) gets no accrual, whatever the edit.
- **Straddling windows** (finding 5): only reward rows **without** an accrual (MOZE era) are read this way. Those of
  the group's rules whose source purchase falls in the window count as used cap, read-only. Engine rows count through
  their accruals.
- Rules still in the MOZE placeholder shape (`method = fixed`, `fixed_amount = 0`) produce no accrual until they are
  migrated (§10.3).

### 5.4 Deletes (finding 5)

- **Purchase or refund deleted:** the delete path deletes its `estimated` accruals and recomputes their windows.
  Frozen accruals survive with `source_entry_id` null and keep counting in their window; their reward row stays
  (its `reward_source_entry_id` is already `ON DELETE SET NULL`) and gets `needs_review` with `reward_posted`.
- **Reward row deleted by the owner:** the delete path sets its accrual to `forfeited` (amount kept, for the record;
  `used` reads 0 for it, §5.2.2). The owner deleting a reward means they did not get it.
- Cap use is always the surviving accruals plus the no-accrual reward rows, so a delete never lets a window exceed
  the cap twice.

## 6. API

- `GET /accounts/{id}/reward-usage?date=YYYY-MM-DD` (read scope): one item per in-date rule, a shared-cap group once
  (finding 6), with the window containing `date` (`window_start`, `window_end`), `used` (frozen, §5.2.2),
  `estimated` (the `estimated` amounts), `cap` (null when uncapped), `remaining` = `cap − used − estimated` (null
  when uncapped), `shared_cap_id` and the member rule ids. Default `date` = today (Asia/Taipei).
- `GET /entries/{id}` and list endpoints expose an entry's accruals (rule, estimate, amount, state, post_on) so the
  SPA and Hermes can show 「預估回饋」.
- `POST /reward-accruals/{id}/post` (write scope): posts a `manual` accrual today or on a given date, through the
  daily job's per-accrual step (§5.1); 409 when the accrual is not `estimated`.
- Reward rule API: `calendar_month`, `per_unit` + `unit_spend`, the method, shared-cap and #289 validation (§4.2).

## 7. Bank wins (statement reconciliation)

Scope: card statements, and the rules whose reward account is the card (or null, meaning the card). Engine reward
rows (rows with an accrual) are **never matcher candidates**: `load_candidates` leaves them out, and they meet
statements only through this step. MOZE-era reward rows (no accrual) stay candidates as today.

**The step** (findings 1 and 2, re-check N1–N3) runs in `_match_and_record`, in the reconcile transaction, before
`pass_two_holders` (`reconciliation_service.py:553`, called at `:1119`) and the pass-2 `matching.match` (`:1121`).
It never commits and never writes coverage itself.
1. **Candidates** = the accruals whose reward account is one of the statement's participating accounts and that are
   - (a) `estimated` or `posted`, with `post_on` in `[period_start, period_end]`; or
   - (b) **carried**: `posted`, `confirmed_statement_id` null, `post_on` before `period_start` and on or after the
     `period_start` of the account's previous statement. With no earlier statement, (b) is empty; or
   - (c) already confirmed or forfeited by this statement (`confirmed_statement_id` = this one: a re-run).
2. **Empty candidate set:** the statement's reward lines go to the matcher as today. A reward line it leaves
   unmatched becomes `bank_only` (→ `create_system_entry`) **unless the card has early candidates**: `estimated`
   accruals with the card as reward account and `post_on` after `period_end`, up to the end of the next cycle
   (`shift_period`). Then the line's case gets the hint `reward_early` instead: the bank credited before the rule's
   posting date, and booking the line would count the reward a second time when the engine posts it. The owner
   decides; the usual fix is the rule's posting fields, after which the estimated accruals move (§5.1) and a re-run
   of the statement finds them in (a). So a reward line reaches `bank_only` only with an empty candidate set and no
   early candidates.
3. **Pulled lines.** Otherwise the statement's `reward` lines are taken out of the claimable lines, except
   - lines with owner coverage (they already leave the matcher input), and
   - a line with exactly one amount-for-amount match among the no-accrual (MOZE-era) reward rows in the population:
     it stays in the matcher, so a MOZE row keeps explaining its own line.

   The policy validator builds pass 2 through the same `pass_two_holders`, so it applies the same filter and both
   agree. If every reward line stayed in the matcher, the step stops here.
4. **Early post** (N2): candidates still `estimated` are posted on the reconcile session, inside the reconcile
   transaction: row lock `FOR UPDATE`, skip when `ledger_entry_id` is set, insert the reward row (§5.1 shape), set
   `posted`. No commit and no advisory lock here. A reconcile rollback undoes the post with everything else. The daily
   job and the manual post endpoint keep their advisory lock and per-accrual transaction (§5.1).
5. **Pairing** (N1): pulled lines in line id order, candidate rows in `post_on`, id order. Line k (k < L, L = number
   of pulled lines) gets row k; the last line gets all remaining rows. Within a line, the line's amount is spread over
   its rows in proportion to their accrual `amount`, with the remainder on the line's last row; when all of them are
   0, the whole line goes on the last row. So each line's rows sum to that line, and the existing per-line
   conservation (`coverage_service.py:88-94`, `:163-180`) holds unchanged.
   - **More lines than rows** (my call, instead of a group-sum conservation rule): the lines left without a row are
     not covered. Each one becomes a `line_unmatched` case with the hint `reward_surplus` (never `bank_only`), so no
     policy books it (`policy_service.py:154` requires `bank_only`). The owner decides it. Rare: it needs more reward
     lines on one statement than engine rows behind them.
   - **No pulled line** (the statement prints no reward line; B = 0): every candidate is assigned 0.
   - **Inbox** (confirm minor, my call): cases with the hints `reward_surplus` and `reward_early` (§7.2) are
     classed as question `other`, as `installment_unmapped` is (`inbox_service.py:76-77`), not `bank_only`. A learned
     owner `record` rule therefore never books them on its own; the owner's manual buttons stay.
6. **Outcome per row:**
   - assigned more than 0: the ledger row takes the assigned amount (updated **before** the claims are built), the
     accrual becomes `bank_confirmed`, `confirmed_statement_id` = this statement;
   - assigned 0 (N3), judged by the row's own `post_on`, on a first run and on a re-run alike: `post_on` inside this
     period → stays `posted` (`confirmed_statement_id` cleared on a re-run), the bank may credit it one cycle late;
     `post_on` before this period (carried) → `forfeited`, ledger amount 0, kept and visible as 未獲得,
     `confirmed_statement_id` = this statement.
7. **Coverage** (N1): the step appends one claim per paired line to the `MatchResult` that `_write_coverage` persists
   (`reconciliation_service.py:573-589`): `match_kind = auto`, `match_rule = reward_engine`, the line's rows as
   principals. Written through `_write_coverage`, the claims are not released by the same reconcile, and a re-run
   rebuilds the same claims, so `_claim_unchanged` (`reconciliation_service.py:472-481`) keeps them. The builder
   pins with a test that the next sweep keeps these claims (the dirty rows from the amount updates predate the claims
   and equal their snapshots).
8. **The one-cycle balance gap** (finding 2, N4): a reward the bank credits one cycle late is in the ledger (posted on
   `post_on`) but not on the first statement, so that statement's balance check is off by the carried rows until the
   next statement settles them. The `balance_gap` case it raises names the carried accruals in its context. Such a
   case offers **`dismiss` only** (not `balance_adjustment_asof` or `anchor_opening`, which would book the reward a
   second time), and no automatic action (policy, owner rule, auto-pay) books anything for it. The owner dismisses
   it once the next statement has confirmed or forfeited the rows.

Engine rows on points or miles accounts never meet a card statement: they stay `posted`. The 「leave the case open
until the daily job」 option of rev 1 is gone: step 4 posts early.

## 8. SPA and Hermes

- **Account settings 回饋規則 block:** each in-date rule row shows 「本期 {used}／{cap}」 (or 「本期 {used}」 when
  uncapped) and the window dates, from `/reward-usage` (`used` + `estimated`). A shared cap shows once, on the first
  rule of the group.
- **Rule editor:** window selector (帳單週期 / 自然月); method gets 每 NT$X 一單位 (`per_unit`) with `unit_spend`.
- **Entry detail:** the 預估回饋 lines with their state (預估 / 已入帳 / 銀行確認 / 未獲得).
- **Hermes `homehub-api` skill** (outside the repo, `~/.hermes/skills/productivity/homehub-api/`):
  - recording a card spend reads the card's in-date rules (GET `/accounts/{id}/reward-rules`) and picks the
    applicable ones, always including the basic rule;
  - when unsure, it asks the owner, and if it cannot ask it guesses and says so;
  - after picking, it reads `/reward-usage` and warns at ≥ 80 % of a cap or when the purchase would cross it;
  - it documents `per_unit`, `calendar_month` and bank wins.

## 9. Oracle check (acceptance)

Before rollout, run the engine read-only (a script, no writes) over the MOZE era (finding 8):
1. **Lock the maps first.** The MOZE code maps used for import (`ROUNDING_MAP` / `POSTING_MAP`,
   `moze_backup_json.py:302-305`, codes 2–4 provisional) are fixed for the run and printed in the report header. A
   different mapping is a new run, never a mid-run change.
2. **Amount check (the bar):** uncapped `percent` rules only, over the (purchase, rule) pairs whose MOZE reward row
   has both `reward_source_entry_id` and `reward_rule_id` set. The engine's frozen-time amount vs MOZE's, within
   NT$1. Rows flagged `needs_review`, and purchases with a refund, are excluded and counted. The report gives the
   numerator, the denominator and the excluded count. **Bar: ≥ 95 % of the denominator.**
3. **Capped and shared-cap rules:** scored on the window total (engine sum vs MOZE sum per window), reported, no bar.
4. **Posting check:** predicted `post_on` vs MOZE's `posted_date`, both as exact dates and as the same statement
   period; reported separately and per card, no bar. A card whose predictions land in a different statement period
   than MOZE's is a rollout gate (§10.3): its rules' posting fields are fixed before its backfill (§7.2 explains the
   double count a wrong posting date would cause).
5. Placeholder miles rules are excluded and counted; after their migration (§10.3) the run can be repeated for them.

Differences explained by known MOZE quirks are recorded in the release notes.

## 10. Rollout

1. Merge; deploy API + worker; migration adds the table, enums and columns, and the ledger partial unique index
   when the duplicate preflight finds none (§4.1; otherwise the counts go in the release notes). No engine output
   yet for existing data.
2. Oracle check (§9) on production data; owner reads the summary.
3. Data pass with the owner, card by card (owner ruling 2026-10-26: one case per question):
   - first, the cards the posting check (§9.4) flags: fix their rules' posting fields, then re-run the check for
     them; a card stays out of step 4 until its posting periods agree;
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
- The PUT schema split for #289 (a separate update schema or a validator on the shared one).
- Test fixtures: fictional cards with a percent rule, a capped rule, a shared cap, a `per_unit` rule, a
  calendar-month rule, a refund, a `manual` rule, a late (carried) bank credit and a re-run.

## 12. Decisions made in this document (owner can veto)

- Accruals in their own table; the ledger holds only posted rewards (§4.1).
- `per_unit` method added in this phase, which also closes #289 (§4.2).
- Refunds claw back in the purchase's window (§5.1, §5.2).
- The `total_rounding` difference goes on the latest still-estimated accrual (§5.2.4).
- Posted accruals are frozen, and later financial edits raise the `reward_posted` review flag (§5.1).
- Hermes warns at 80 % of a cap (§8).
- The weekly digest line is deferred (§3).
- Freezing per accrual; new purchases get the cap left after posted amounts (§5.1, §13 Q1).
- A statement pays the rows whose `post_on` is in its period; each pulled line is paired with its own rows (§7.5);
  a missing line forfeits only after the next statement (§7).
- Rule and closing-day edits recompute estimated accruals (§5.1).
- `post_on` is raised to the window end, not rejected (§4.4).
- Rules sharing a cap must share a window; the group cap is the smallest member cap (§4.2).
- A purchase past the cap at posting gets no reward row (accrual `forfeited`) (§5.1).
- A reward the bank credits one cycle late stays `posted` for one statement; that statement's balance gap is left to
  the owner, never booked (§7.8).
- Bank lines are paired with rows in order; each line is spread over its rows in proportion to the engine amounts
  (§7.5). Lines beyond the row count go to the owner as cases (§7.5).
- A carry-gap `balance_gap` case offers dismiss only (§7.8).
- A reward line credited before the rule's posting date is an owner case (`reward_early`), never auto-booked (§7.2).
- An owner-deleted reward row forfeits its accrual (§5.4).

## 13. Builder check (2026-11-03): questions and how they were settled

1. Windows posting on different days (`after_transaction`, `manual`, mixed shared caps): freeze per accrual; the
   rest of the window is allotted the cap left after posted amounts (§5.1).
2. Which statement pays a window: the one whose period contains `post_on`. A missing line forfeits after the next
   statement (§7).
3. Several reward lines or rules: pair lines to rows in line-id order and `post_on`, id order; each line's rows sum
   to that line; lines past the row count are owner cases (§7.5).
4. MOZE-era purchases and straddling windows: no accrual where a reward row already exists for the (purchase,
   rule); existing rows count as used cap; seed run for linked spends since 10-01; double-booked statement lines
   decided case by case (§5.3, §10.4).
5. Rule and closing-day edits recompute estimated accruals (§5.1).
6. A `post_on` earlier than the window end is raised to it (§4.4). Project propagation is in scope (§5.1). The daily
   job runs in the API process like the existing jobs, not on a worker timer.

## 14. Multica design review (AGENT-394, dev-grok, on rev 1.1 5cc4cb54): findings and where they landed

1. Exact-amount matching would send mismatched reward lines to `create_system_entry` (double booking): reward lines
   are taken out before matching when the statement has engine candidates, except a line with exactly one amount
   match to a no-accrual reward row, which stays in the matcher (§7.3); engine rows are never matcher candidates
   (§7).
2. Late credits: carried `posted` rows of the previous period are candidates; forfeit only when the next pass assigns
   0; a statement with no reward line still runs the step; the one-cycle balance gap is named (§7.1, §7.6, §7.8).
3. Rounding under freeze: frozen amounts keep `txn_rounding` only; the `total_rounding` delta goes on the latest
   estimated accrual (§5.2.4).
4. Refunds: built in `refund()` from the purchase's links, in the purchase's window; ordering by source `entry_date`,
   id; negative estimates allotted in full (§5.1, §5.2.3).
5. Deletes: estimated accruals go, frozen ones survive with a null source; the straddle read covers no-accrual rows
   only (§4.1, §5.3, §5.4).
6. Shared caps: same window required (422), group cap = smallest member cap, group-wide `used`, usage returns
   used / estimated / cap / remaining (§4.2, §5.2.2, §6).
7. Duplicate posts: advisory lock, per-accrual transaction with a row lock and a `ledger_entry_id` guard, partial
   unique index after a duplicate preflight (§4.1, §5.1, §10.1).
8. Oracle: maps locked first; the bar applies to uncapped percent pairs only, with denominator and exclusions;
   capped rules by window total; posting scored separately (§9).
9. Schema: 0 accepted on PUT only for a stored 0 placeholder; `per_unit` validation; posted row currency (§4.2,
   §5.1).
10. Review flag: one reason `reward_posted`, set after `_apply`; non-financial edits keep it (§5.1).

Re-check on rev 2 (`d7743c79`): findings 1–10 resolved; four new items, folded in rev 2.1:
- N1 (blocker) coverage written inside the step would be released by `_write_coverage` and could not conserve per
  line: claims go through the `MatchResult`; lines pair with rows in order and each line sums to itself; a line with
  one exact MOZE-row match stays in the matcher; surplus lines become owner cases (§7.3, §7.5, §7.7).
- N2 (major) the early post committed on its own: it runs on the reconcile session without a commit (§7.4).
- N3 (minor) zero assignment on a re-run and the missing predecessor: judged by the row's `post_on`; (b) empty with
  no earlier statement (§7.1, §7.6).
- N4 (minor) the carry gap offered `balance_adjustment_asof`: dismiss only (§7.8).

Confirm on rev 2.1 (`8fa8f152`): PASS-with-minor, N1–N4 resolved. Folded in rev 2.2:
- the three summary sentences that still described the rev 2 total spread (§12, §13 Q3, §14 item 1);
- the inbox classing of `reward_surplus` so a learned owner rule cannot book it (the confirm noted the inbox would
  class it `bank_only`) (§7.5);
- not from the review: a reward line credited before the rule's posting date would reach `bank_only` and be booked
  again when the engine posts. Early candidates now give the hint `reward_early` (§7.2), and the posting check is a
  per-card rollout gate (§9.4, §10.3).
