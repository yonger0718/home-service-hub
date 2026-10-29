# Accounting notifications (扣款提醒 + 週報) — design

Rev 3, 2026-10-27 (rev 1 dba71677; rev 2 folds the Multica spec review AGENT-314: 11 majors, 3 minors; rev 3 adds the systemd `OnFailure` backstop, the linked-cards-only note in §5.3 and the rollout link check, per the owner 2026-10-27; rev 3 also folds the AGENT-314 re-check minors: all three `find_twins` shapes, the window predicate, no 帳單未到 beside an off-cycle statement); rev 3.1 (closeout) bounds that suppression to statements newer than the cycle-before-last's match zone; rev 3.2 (owner ruling 2026-10-29) drops 帳單未到 and 該來沒來的帳單 for a quiet cycle (§5.1). Owner-approved direction (2026-10-27): deterministic digest computed from the HomeHub
accounting API, pushed to a dedicated Discord channel through `hermes send`, no LLM in the loop.

## 1. Intent

HomeHub became the primary ledger and card statements are reconciled and auto-paid by jobs. Two things the owner
used to get from MOZE and from the monthly reconciliation hour are now gone:

1. **The "即將扣帳" bell.** MOZE told the owner a card was about to debit; the owner then checked the source
   account. Without it the owner forgets, or finds out from a bank app at the last minute. The auto-pay job books the
   transfer on the debit day, but it cannot put money in the account. Lead time the owner needs: **1–2 days** for TWD
   (same-day transfers); a planned US card will need **more than 2 business days**.
2. **Someone looking at what automation could not resolve.** Open reconciliation cases, statements that never
   arrived, auto-pay skips (no link, over cap, suspected twin) and cards without a debit account used to surface when
   the owner sat down to reconcile. Nobody sits down any more.

Success: the owner never again learns about a card debit from a bank app, and never has an unresolved
reconciliation item older than a week without having been told.

## 2. Goals / non-goals / anti-goals

Goals
- Daily (07:00 Asia/Taipei, after the 06:00 auto-pay run) **debit reminder**: every card whose debit day is within
  the lead window, with the source account's projected balance and the shortfall if any; manual-pay cards get a
  "手動繳" reminder instead of a balance check. The due date comes from the card's own rule (closing day + due rule),
  so a month whose statement never arrived still gets a reminder — flagged 帳單未到 — instead of silence.
- Weekly (Saturday 10:00 Asia/Taipei) **digest** of what needs a human: open cases with full detail, missing
  statements, auto-pay skips, unlinked cards, statements without a due date.
- The debit reminder carries the statement's open-case count so the owner clears it while already looking.
- Messages are computed by code; `hermes send` only delivers. No LLM, no token spend, no hallucinated numbers.
- Quiet when there is nothing to say (daily). The weekly digest is always sent (see §6.2) so silence never hides a
  broken pipeline.
- Every number in a message is reproducible from the API with the same inputs (`--dry-run --as-of`).

Non-goals (follow-up issues, not this change)
- Reports / periodic Hermes LLM review of the ledger (owner: "報表會是下一個", "定期可以找 hermes 來進行帳務的
  review … 未來 follow up").
- Grafana alerting. Grafana stays for ops (worker health, OCR failure rate, API 5xx).
- Replying to the owner in Discord, or acting on reactions.
- Other channels (DM, LINE, email).
- Weekday-evening reminders. The owner listed evenings/weekends/pre-debit as all acceptable; this design uses
  pre-debit (daily) and Saturday morning (weekly). The timers are plain systemd `OnCalendar` strings, so moving the
  weekly digest to a weekday evening is a one-line edit.

Anti-goals (would make it a failure even if it "works")
- A reminder that says 不足 when the account is fine, or 足夠 when it is not. Projection rules are in §5.3 and are
  tested against fixtures.
- A message every morning. The daily job sends nothing on a day with nothing actionable.
- Owner financial data in the repo, tests, logs or commit messages. Fixtures use fictional accounts and amounts.
- The notifier writing to the ledger. It holds a read-only token and makes GET requests only.

## 3. Constraints

- The Hermes bot (gateway running as `opc`) is the only Discord sender on the host. `hermes send -t discord:<chat_id>`
  works without the agent loop and verified on 2026-10-27 against the owner's new channel. The channel id lives in
  the notifier's config file, never in the repo.
- Discord messages are at most 2000 characters; a long digest is sent as several messages in order.
- The accounting API is token-scoped (`ACCOUNTING_TOKEN_SCOPES`). The notifier gets its own label `notify` with
  scope `read` only; the token lives in `~/.config/homehub-notify-token` (0600). Granting the label is an owner step
  (API `.env` edit + `homehub-api-ctl restart`), as for the `hermes` label.
- `hermes` and its config are owned by `opc`; `opc` already runs user systemd units with linger (pm2). The notifier
  therefore runs as `opc` from a release checkout (`~/releases/home-hub-notify/<sha>`, own venv), never from a dev
  worktree, and never as `homehub-api`/`homehub-worker`.
- Asia/Taipei everywhere. "Today" is the Taipei date at run time (or `--as-of`).
- Business days come from the API (`GET /calendar/business-days?from&to`, span ≤ 1100 days), so the notifier, the
  app and the auto-pay job agree on the debit day; the notifier never ships its own holiday table. The response is
  `{holidays, workdays, last_known_day}`, not a list of business days: a day is a business day when it is a weekday
  not in `holidays`, or any day in `workdays` (a workday wins). `last_known_day` (2027-12-31 today) ends the table;
  the job only shifts weekends past it. The notifier refuses to state a debit day past it: any due date in use
  after `last_known_day` is a §7 failure (exit 1, failure message), never a guessed date.
- No new database tables or migrations. All state the notifier needs is a small local JSON file (§6.3).
- Python 3.13, `httpx`, stdlib otherwise; tests with `pytest` and recorded fixtures; same layout conventions as
  `services/accounting-service/worker/` (config from env, `python -m … <cmd>`).

## 4. Shape

```
services/accounting-service/notify/        # new package, sibling of worker/
  __main__.py  cli.py  config.py  api.py   # python -m notify daily|weekly [--dry-run] [--as-of D] [--send]
  calendar.py  debits.py  digest.py        # pure functions over API payloads → items
  render.py    state.py   send.py          # items → Discord text; dedupe state; hermes send wrapper
deploy/notify/                             # unit files + install script (opc user units)
  homehub-notify-daily.service/.timer      # ExecStart=… -m notify daily --send ; OnCalendar=*-*-* 07:00 Asia/Taipei
  homehub-notify-weekly.service/.timer     # ExecStart=… -m notify weekly --send ; OnCalendar=Sat *-*-* 10:00 Asia/Taipei
  homehub-notify-failed@.service           # OnFailure target: ExecStart=… -m notify failed %i (§7)
                                           # both services: Environment=TZ=Asia/Taipei; "today" is always ZoneInfo("Asia/Taipei")
  install.sh                               # copies units to ~/.config/systemd/user, enables timers
```

Config file `~/.config/homehub-notify.toml` (0600, owner-edited, not in the repo; the repo ships an example with
placeholder values):

```toml
api_base = "http://127.0.0.1:8000"
token_file = "~/.config/homehub-notify-token"
hub_base_url = "https://<private host>/hub"       # for links in messages
discord_target = "discord:<channel id>"
lead_business_days = 2                            # debit reminder window
[lead_overrides]                                  # per account id, e.g. a US card
# "123" = 4
weekly_open_case_limit = 10                       # items listed per account before "+N more"
expected_statement_grace_days = 7
min_buffer = "0"                                  # projected balance must stay >= this (account currency)
```

Why a local file and not the API's reconciliation settings: the notifier is an `opc`-side tool like Hermes; a config
change should not need an admin token or an API deploy. (My call.)

## 5. Daily debit reminder

Runs 07:00 Asia/Taipei. Inputs, all `GET` with the `notify` token:
`/settings/reconciliation` (`auto_pay.enabled` and `enabled_from` decide the item class, §5.2), `/calendar/business-days`
(today−7 … today+60), `/accounts?include_archived=false&as_of=<today>`, `/accounts/{id}/statements` for each credit
card, `/entries` (card payments), `/schedules/instances` (pending debits), `/reconciliation/audit`
(`auto_pay_skipped` since yesterday 06:00).

### 5.1 Which cards, and where the due date comes from

The reminder is **rule-based first, statement-enriched second**. The auto-pay job can only book from an ingested
statement; if the statement never arrived (no file in Drive, 0-byte PDF, OCR failure) there is no auto-pay that
month, and a reminder that also depended on the statement would be silent exactly when the owner most needs it.
The app's reminders page already derives dues from the card's rule without a statement; the notifier does the same.

- Cards: accounts with `is_credit = true`, not archived, with `closing_day`, `due_rule` and `due_value` set (cards
  without a rule are listed in the weekly 設定缺漏 section instead). 副卡 (`combined_account_id` set) are folded
  into their master: payments on either count for the master (same rule as the app's pill, #196).
- Expected cycle: the last closed cycle relative to today — port of the app's `statementPeriod(closing_day, today)`
  shifted by −1 (`cycle.ts`): the cycle ends on `closing_day` (clamped to the month's last day); today on or before
  that day belongs to the open cycle, so the last closed cycle ended last month. Rule due date = `dueDate(period_end,
  due_rule, due_value)` ported from `cycle.ts`: `days_after_closing` → `period_end + value` days; `fixed_day` → day
  `value` of the closing month when that day (clamped) is after the closing day, else day `value` of the next month.
- Statement match: among the card's live statements (`mode = live` and `retired = false`; `status` is open/reconciled
  and irrelevant here) the one whose `period_end` is within ±3 days of the expected cycle's end — closest
  `period_end` first, then highest id. When present, its `due_date`, `statement_total`, `open_case_count` and id are
  used; the rule's due date is only a fallback. A matched statement with `due_date` null is a **manual** line
  「到期日未辨識 · 金額 X · 請自行確認」 (the job never books it) and is not a projection input.
- Every other live, non-retired `kind = card` statement of the card with `statement_total > 0` and a due date is
  **also** an item (its own due/debit day, same class rules): the job books by statement, not by rule cycle, so a
  statement the rule did not expect still debits. 帳單未到 is emitted only when no statement matched the cycle
  **and** the card has no other live, non-retired, unmatched statement with `statement_total > 0` and a due date
  whose `period_end` is newer than the end of the cycle before it + 3 days, decided per cycle (for the last closed
  cycle C the C−1 end + 3; for C−1 the C−2 end + 3) and only for a statement that still yields an item (not paid or
  twinned): a statement covering only the older cycle does not clear the current one; an off-cycle
  statement is the bill for that cycle as far as the owner is concerned, and 「不會自動扣繳」 next to an auto item
  for the same card would contradict it. An older statement never silences the line (rev 3.1: implementation
  Ruling 5 — the rev 3 wording let any old live statement suppress 帳單未到 forever).
- **Quiet cycle (rev 3.2, owner ruling 2026-10-29).** A bank sends no statement for a cycle that opens with nothing
  owed and has nothing on the card, so such a cycle gets no 帳單未到. A cycle is quiet when the last live,
  non-retired statement of the card whose `period_end` is more than 3 days before the cycle end has
  `statement_total <= 0`, that `period_end` is inside the entry read window (today − 120), and no top-level entry of
  the card or its 副卡 other than a `balance_adjustment` falls after that `period_end` through the cycle end, dated
  by the later of `entry_date` and `posted_date` (a purchase the bank posts after the closing day is on the next
  bill). Payments, refunds, fees, rewards and split parents all count as activity; split children do not. The
  entries are read once per card group, every kind, over the read window. No such statement, a statement outside
  the window, or unread activity (no list at all, or none for the card group) → not quiet (the reminder stays). The check trusts the ledger: a purchase
  missing from HomeHub makes a cycle look quiet.
- Debit day = first business day on or after the due date (the API calendar; same as auto-pay `pay_date_for` and the
  app's `payDateOf`).
- In window when `debit_day >= today` **and** `business_days_between(today, debit_day) <= lead` where
  `lead = lead_overrides[account] or lead_business_days`. `business_days_between(today, today) = 0`; a debit day
  that is today is in the window; a past debit day is never in the window (`business_days_between` is not called
  for it) — past items are rendered only through §5.4's overdue set: `debit_day < today`, unpaid, and
  `0 < (today − due).days <= 30`.
- No statement in window → the item reads 「📄 帳單未到 · 到期 MM/DD（規則）· 金額未知，請自行確認 · 不會自動扣繳」,
  goes into the manual block regardless of `auto_pay_account_id`, is excluded from the projection (§5.3) and is
  included every day. After the debit day it stays while `0 < (today − due).days <= 30` as 「⚠ 逾期未繳 · 帳單未到 ·
  到期 MM/DD（規則）」 until a statement matches the cycle — a missing statement must not go quiet on the day it mattered. Rule-only items have
  no statement id; state keys them by `-card_id` (§6.3). The weekly 該來沒來的帳單 section (§6.1) lists them too.
- Statement present with `statement_total <= 0` → nothing to pay; dropped (same as auto-pay).
- Payment window (both classes): `[period_end, min(today, max(debit_day, next_closing − 1))]` where `next_closing`
  is the next cycle's end — the app's `paymentWindowEnd` — so a later cycle's payment never clears this one.
  `paid` = Σ `transfer_in` on the master + non-archived 副卡 with `entry_date` in that window.
- **Auto item** is dropped only when the debit already exists as the job would see it (`find_twins` rule), i.e.
  an entry with `entry_date` within `auto_pay.twin_window_days` of the debit day in any of the three shapes the
  job matches: (a) a `transfer_in` on the master + 副卡 of exactly `statement_total`; (b) a `transfer_out` on the
  source account of exactly `−statement_total` whose `transfer_group_id` is null or matches a `transfer_in` on the
  master + 副卡 (the job also accepts a peerless `transfer_out`; the notifier cannot see peers on other accounts
  and treats a grouped `transfer_out` whose peer is elsewhere as not a twin — the error is on the warning side);
  (c) an `expense` on the source account of exactly `−statement_total` with `source = "statement"`. The job's own
  booked legs satisfy (a) on the debit day, so the 07:00 reminder that day shows nothing for it. Otherwise it stays, the line may show 「已繳 X / Y」, and the projection subtracts the **full**
  `statement_total` because that is what the job will debit.
- **Manual item** is dropped when `paid >= statement_total`; a positive sum below the total shows 「已繳 X / Y」; a
  minimum-only payment is partial; an overpayment is simply paid. For a rule-only item there is no amount, so it is
  never "paid" and stays until a statement matches.
- `GET /accounts/{id}/statements` has no status filter; the client keeps `mode = live` and `retired = false`.
- 逾期 rule (§5.4) looks back 30 days from today over the same cycle logic (previous closed cycle too).

### 5.2 Grouping and classification

- **Auto item**: the card has `auto_pay_account_id`, `auto_pay.enabled` is true in `/settings/reconciliation`, the
  statement exists with a due date, its **debit day** is on/after `auto_pay.enabled_from` (the job's `cutoff` gate
  compares `pay_date`, not the due date), and the link is eligible by the job's own predicate evaluated now from
  `/accounts` — the target exists, is not archived, is not a credit account and has the card's currency
  (`_link_ineligible`); the lead window has no audit row yet, so the notifier must not wait for one. Everything
  else — manual cards, rule-only items (帳單未到), statements before the cutoff, any statement while auto-pay is
  disabled, ineligible links — is a **manual item**: reminded as 「手動繳」, never projected (§5.3), manual overdue
  rule (§5.4). An ineligible link because of currency adds 「幣別不同，請自行確認」 to the line. The settings read
  therefore decides the class.
- Items are grouped by source account: all auto-paid items with `auto_pay_account_id = X` whose debit day is in
  the window form one block for X, ordered by debit day. Manual items form their own block.
- When a block is rendered it lists **every** in-window unpaid item of that source (full picture, with the balance
  line counting all of them); a block is rendered only when at least one of its items passes the dedupe trigger
  (§5.6). The shortfall line belongs to the block (the source account), not to a card. (My call.)
- `business_days_between(today, debit_day)` = number of business days strictly after `today` up to and including
  `debit_day` (0 when equal, Fri→Mon = 1, Sat→Mon = 1, Sat→Tue = 2). With `lead = 2` a Wednesday debit is first
  reminded on Monday 07:00; a Tuesday debit on Friday 07:00 (Fri→Tue = Mon, Tue = 2). (My call.)
- Lead is in business days of the TW calendar for every card; the planned US card gets `lead_overrides` (e.g. 4).
  A card's currency is the account's `currency`. A foreign-currency card paid from a source in the same currency
  is projected normally; a currency mismatch makes the link ineligible (manual item, see above). (My call.)

### 5.3 Projected balance (auto-pay cards)

For source account X and the window's last debit day D:

```
projected(X, D) = balance(X, today)
                − Σ statement_total of auto items (§5.2) with auto_pay_account_id = X and debit_day ≤ D
                − Σ |amount| of pending schedule-instance lines with account_id = X, amount < 0, due_date ≤ D
```

`GET /schedules/instances?status=pending&until=<D>` (the default `until` is today + 30, so `until` must be passed);
`lines[].amount` is signed — outflows negative, income/payable/collection positive — so only negative lines count
and positive ones are left out.

- `balance(X, today)` = the `balance` field of `GET /accounts?as_of=<today>` for X (posted entries through today).
- Only linked cards (`auto_pay_account_id = X`) are subtracted. A card the bank debits automatically but that has
  no link in HomeHub is a manual item (§5.4) and reduces no projection, so the reminder can read 「餘額足夠」 while
  the bank still debits that card. The weekly 設定缺漏 section (§6.1) lists such cards; §9 step 0 clears them
  before the first run.
- The first sum includes cards outside the lead window whose debit day is still ≤ D (a 3-card Friday) and every
  auto item regardless of partial payments (§5.1: the job debits the full total). It includes statements the job
  may later skip for `over_cap`/`monthly_budget`/`twin`: a skip the owner fixes before the debit day still needs
  the money.
- Shortfall = `min_buffer − projected` when positive. Items read 「缺 X 元」 and the block is marked ⚠.
- Currency: the source account's currency is the block's currency; a card in another currency never reaches the
  block (its link is ineligible, §5.2). FX projection is out of scope.

### 5.4 Manual cards and overdue

- Manual card (`auto_pay_account_id` null) in window: 「手動繳 · 到期 MM/DD · 金額」. No balance line.
- Manual item whose debit day has passed, unpaid (§5.1 rule), due within the last 30 days: 「⚠ 逾期未繳 N 天」, every
  day until paid; a rule-only item reads 「⚠ 逾期未繳 · 帳單未到」 (§5.1).
- Auto-paid item whose debit day has passed and is unpaid (the job skipped it for a terminal reason other than
  `cutoff`): 「⚠ 未扣款 · 原因 <reason>」 using the latest `auto_pay_skipped` audit detail for that statement; every
  day until paid or until the statement is retired. A `cutoff` skip makes the item manual (§5.2), so it follows the
  manual overdue line instead and never says 「原因 cutoff」.

### 5.5 Auto-pay skips

`auto_pay_skipped` audit events since yesterday 06:00 Asia/Taipei with a reason other than `cutoff` are listed
(card, amount, reason in words). Reasons map to one line each: `no_link` 卡片沒有設定扣款帳戶, `link_ineligible`
扣款帳戶不可用, `over_cap` 超過單筆上限, `monthly_budget` 超過當月總額, `twin` 疑似已手動繳過, `duplicate_statement`
重複帳單, `header_disputed` 帳單有爭議, `expired` 超過重試期限.

### 5.6 Dedupe

Shortfall is a property of the source block, not of a statement. State file (§6.3) keyed by statement id (rule-only
items by `-card_id`): `first_notified`; per source account: `last_shortfall`. A block **fires** — and then lists
every in-window unpaid item with the full projection (§5.2) — when any of:
- an item is new in the window (no `first_notified`);
- any item's debit day is today (a final 「今天扣款」 marker on that line);
- the block's shortfall is positive (every day while it persists);
- the shortfall just cleared since `last_shortfall` (one 「✅ <source> 已補足」 line, then silence);
- any item is overdue / 未扣款 / 帳單未到 (every day).
On the days between, a block with none of these is omitted entirely — never a subset. The manual block follows the
same triggers (new / debit today / overdue / 帳單未到). Skips are included once per audit event id.
Same-day re-run: state records the Taipei date and the set of trigger keys of the last send; a run on the same date
with an identical trigger set sends nothing (exit 0). Any difference (a new item, a cleared shortfall) sends again.

### 5.7 Message

Plain text with Discord markdown, one message per run (split at 2000 characters on block boundaries). Example with
fictional data:

```
🔔 扣款提醒 2026-11-05（四）

**薪轉戶 A**  餘額 52,300 → 扣款後 −3,900 ⚠ 缺 3,900
• 旅遊卡 · 48,200 · 到期 11/05 · 扣款 11/06（五）· 這期還有 2 筆對不上 → <link>
• 加油卡 · 8,000 · 到期 11/07 · 扣款 11/09（一）

**外幣戶 B**  餘額 1,200 → 扣款後 1,050
• 海外卡 · 150 · 扣款 11/06（五）

**手動繳**
• 黑啤卡 · 4,378 · 到期 11/06（五）
• 📄 超市卡 · 帳單未到 · 到期 11/07（規則）· 金額未知，請自行確認 · 不會自動扣繳

⏭ 自動扣繳跳過
• 聯名卡 · 3,840 · 卡片沒有設定扣款帳戶 → <settings link>
```

Links: `{hub_base_url}/accounting/accounts/{account_id}/statements/{statement_id}` for a statement,
`{hub_base_url}/accounting/accounts/{account_id}/settings` for a card. Both routes exist today
(`frontend/src/app/app.routes.ts`: `accounts/:id/statements/:sid`, `accounts/:id/settings`); no frontend change.
`open_case_count` is a field of `StatementOut` on the statement list, so no per-statement call is needed.

## 6. Weekly digest

Runs Saturday 10:00 Asia/Taipei. Sections, each omitted when empty:

### 6.1 Sections

1. **對不上的交易** — `GET /reconciliation/inbox` (already grouped by account; items carry `question`, not a case
   kind): per account the open count, the oldest age from `GET /reconciliation/cases?status=open&account_id=<id>`
   (`created_at`, newest first), then up to `weekly_open_case_limit` items with date, amount, merchant/description
   and the `question` in words — `bank_only` 只有銀行有, `ledger_only` 只有帳本有, `transfer` 疑似轉帳, `amount_differs`
   金額不同, `date_differs` 日期不同, `ambiguous` 多個候選, `balance_gap` 餘額差, `settlement` 結算, `proposed` 已提議,
   `other` 其他 — and 「+N 筆」 when truncated. Folded items (`folded: true`, split members shown under their
   group) are skipped, except that a group whose members are ALL folded keeps its first member, so no counted case
   is hidden (#203). Link to the statement page. Reading the inbox lets the API supersede
   stale proposal rows (`supersede_stale`); that read-scope side effect is accepted — it posts nothing to the ledger.
2. **該來沒來的帳單** — for every account that has at least one live statement: expected next `period_end` =
   last live `period_end` + cadence, where cadence = the account's `closing_day` month step for cards, else the
   median gap of its last three live statements (one statement → 1 month). Listed when
   `expected + expected_statement_grace_days < today` and no live statement with `period_end > last` exists.
   For a card with a `closing_day`, a quiet expected cycle (§5.1) moves `expected` to the next cycle, repeatedly, so
   the line names the first cycle with activity, or is dropped while that cycle is inside the grace days.
   Shows account, last period end, expected date, days late.
3. **自動扣繳本週跳過** — `auto_pay_skipped` events in the last 7 days with reason ≠ `cutoff`, one line each
   (same wording as §5.5), deduped per statement (latest reason).
4. **設定缺漏** — credit cards with live statements and no `auto_pay_account_id`; credit cards (not archived,
   with live statements or entries in the last 90 days) missing `closing_day`/`due_rule`/`due_value`, since the
   daily reminder cannot cover them; live statements with `statement_total > 0` and `due_date` null (the auto-pay
   job's `no_due_date` list, reachable through `/accounts/{id}/statements`).

### 6.2 Always sent

The weekly digest is sent even when every section is empty: 「✅ 本週沒有待處理 · 最近一次每日檢查 MM/DD HH:MM」.
This is the liveness signal: a quiet week and a dead timer look identical otherwise. (My call; the owner asked for
quiet when nothing happens, which the daily job honours.) `最近一次每日檢查` is `last_daily_run`, which every daily
run that exits 0 writes — including a run that sent nothing — so a healthy quiet week never reads as a dead timer.

### 6.3 State file

`~/.local/state/homehub-notify/state.json` (0600): `{"statements": {<id or -card_id>: {"first_notified": date}},
"sources": {<account id>: {"last_shortfall": amount|null}}, "skips_seen": [event ids], "last_daily_run": iso,
"last_weekly_run": iso, "last_send": {"date": YYYY-MM-DD, "triggers": [keys]}}`. Written atomically on every exit 0
(sent or nothing to send); untouched on failure (§7). Missing or corrupt file → treated as empty (first-run
behaviour: every window statement is "new"; acceptable).

## 7. Failure behaviour

- API unreachable / non-2xx / token rejected / calendar exhausted / bad config: the run writes one line
  「❌ 帳務通知失敗：<short reason>」 (no payload echo, no token) to `last_failure.txt` next to the state file
  (0600), logs it to stderr, and exits 1. The state file is not touched. The run does **not** deliver the failure
  itself.
- Delivery of failures is systemd's job: both `.service` units carry `OnFailure=homehub-notify-failed@%n.service`.
  That template unit runs `python -m notify failed <unit>`, which sends the text of `last_failure.txt` when it
  exists (then deletes it) or the generic line 「❌ 帳務通知失敗：<unit> 非正常結束，請看 journalctl --user -u <unit>」
  when it does not (crash before the handler, missing venv, OOM, signal), then exits 0 whatever happened. One
  failure → one message, also for failures the program never saw.
- `hermes send` fails inside a run: exit 1, state not written, so the next run resends; the `OnFailure` unit then
  tries `hermes send` once more and gives up silently if Hermes is still down. A dead Hermes gateway is therefore
  invisible until the Saturday 「最近一次每日檢查」 stamp; the Hermes-independent heartbeat alert over Loki is
  issue #200 (after the first real run).
- A partial API failure (one account's statements 500) fails the whole run; no half-digests.
- Capped reads are failures too, not short successes: `/entries` is paged by `offset` until `offset + len(items)
  == total`; for `/reconciliation/audit` (newest first, `limit` ≤ 1000; read with `since=<window start>` and
  `event=auto_pay_skipped` since #201 — an older API ignores both and the cap rule still holds),
  `/reconciliation/inbox` (≤ 1000) and `/reconciliation/cases` (≤ 1000, no offset), a full page whose oldest row is
  still inside the window the run needs → §7 failure. Never render from a truncated page.
- A due date after the calendar's `last_known_day` → §7 failure (§3).
- Logs: stderr only, one line per run (`daily: 3 items, sent 1 message`), no amounts.

## 8. CLI and tests

```
python -m notify daily  [--as-of YYYY-MM-DD] [--dry-run] [--send]
python -m notify weekly [--as-of YYYY-MM-DD] [--dry-run] [--send]
python -m notify failed <unit>
```
`failed` is only run by the `OnFailure` unit (§7): it reads config for the Discord target, sends the stored or
generic failure line, and always exits 0.

`--dry-run` prints the rendered message(s) to stdout and does not touch the state file; `--send` delivers and
updates state; neither flag → prints and exits 0 (safe default). Exit codes: 0 sent or nothing to send, 1 failure,
2 bad arguments.

Tests (`services/accounting-service/tests/notify/`): pure-function tests for the window, projection, dedupe and
cadence rules with fictional fixtures; golden-text tests for `render`; an API-client test against recorded JSON
(`respx` or a tiny fake); a CLI test that `--dry-run` never calls `send`. No live API in tests.

## 9. Rollout

0. Owner: every card the bank debits automatically has its 扣款帳戶 link set in HomeHub (the weekly 設定缺漏 list
   is the checklist; on 2026-10-27 the one unlinked card with live statements was confirmed by the owner as paid by hand,
   so it stays a 手動繳 reminder and the checklist is clear). Unlinked cards are reminded as 手動繳 and never reduce a projection.
1. Owner: add label `notify` with scope `read` to `ACCOUNTING_TOKEN_SCOPES` + token to `ACCOUNTING_API_TOKENS` in the
   API `.env`, restart via `homehub-api-ctl`; write the token to `~/.config/homehub-notify-token` (0600).
2. Owner: write `~/.config/homehub-notify.toml` from the example (channel id, hub URL).
3. Release checkout `~/releases/home-hub-notify/<sha>` + venv; `deploy/notify/install.sh` installs the five `opc`
   user units and enables the two timers.
4. First run by hand: `python -m notify daily --dry-run`, then `--send`; same for `weekly`. The owner confirms the
   two messages in the channel.
5. Follow-up issues: #200 Loki/Grafana heartbeat alert (no `daily:` log line in 30 h, Hermes-independent); Hermes
   periodic LLM review of the digest; reports; LINE/DM if ever wanted.

## 10. Left to the builder

- Exact httpx client shape (reuse `worker/api.py` patterns: bearer header, redaction, no retries beyond one).
- The Python port of `cycle.ts` (`statementPeriod`, `shiftPeriod`, `dueDate`, month clamping) lives in
  `notify/cycles.py` with tests mirroring `cycle.spec.ts` cases; the frontend stays the reference implementation.
- `GET /accounts?as_of=` returns `balance` (posted on/before `as_of`) — confirmed; `/reconciliation/audit` takes
  `since`/`event`/`offset` since #201 (the notifier passes the window start and `auto_pay_skipped`); the §7 cap
  rule stays as the guard and `auto_pay_skipped` is still filtered client-side for an older API.
- Message splitting and emoji choices; Discord markdown escaping of account names.
- The `install.sh` idempotency and the release-checkout layout (mirror `~/releases/home-hub-portfolio`).
