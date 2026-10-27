# Accounting notifications (扣款提醒 + 週報) — design

Rev 1, 2026-10-27. Owner-approved direction (2026-10-27): deterministic digest computed from the HomeHub
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
- Business days come from the API (`GET /calendar/business-days`), so the notifier, the app and the auto-pay job
  agree on the debit day; the notifier never ships its own holiday table.
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
  homehub-notify-daily.service/.timer      # OnCalendar=*-*-* 07:00 Asia/Taipei
  homehub-notify-weekly.service/.timer     # OnCalendar=Sat *-*-* 10:00 Asia/Taipei
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
- Statement match: the live statement (`mode = live`, status not retired) whose `period_end` equals the expected
  cycle's end (±3 days tolerance for banks that close on business days). When present, its `due_date`,
  `statement_total`, `open_case_count` and id are used; the rule's due date is only a fallback.
- Debit day = first business day on or after the due date (the API calendar; same as auto-pay `pay_date_for` and the
  app's `payDateOf`).
- In window when `business_days_between(today, debit_day) <= lead` where `lead = lead_overrides[account] or
  lead_business_days`. `business_days_between(today, today) = 0`; a debit day that is today is in the window.
- No statement in window → the item reads 「📄 帳單未到 · 到期 MM/DD（規則）· 金額未知，請自行確認 · 不會自動扣繳」,
  goes into the manual block regardless of `auto_pay_account_id`, is excluded from the projection (§5.3) and is
  included every day until the statement arrives or the debit day passes. The weekly 該來沒來的帳單 section (§6.1)
  still lists it afterwards.
- Statement present with `statement_total <= 0` → nothing to pay; dropped (same as auto-pay).
- Paid when the sum of `transfer_in` entries on the master + 副卡 with `entry_date` in `[period_end, today]`
  is `>= statement_total` (statement present). A positive sum below the total is a partial payment: the item stays
  and shows 「已繳 X / Y」; a minimum-only payment is partial. Payments before `period_end` do not count (same as the
  app's pill); an overpayment is simply paid. For a rule-only item there is no amount, so it is never "paid" and
  stays until the statement arrives or the debit day passes. Paid statements are dropped. A statement the auto-pay
  job already booked is therefore dropped the moment the legs exist (booking happens on the debit day at 06:00; the
  07:00 reminder that day shows nothing for it). `GET /accounts/{id}/statements` has no status filter; the client
  keeps `mode = live` and drops retired statements. (Builder-check items; my calls.)
- 逾期 rule (§5.4) looks back 30 days from today over the same cycle logic (previous closed cycle too).

### 5.2 Grouping and classification

- **Auto-paid item**: the card has `auto_pay_account_id`, `auto_pay.enabled` is true in `/settings/reconciliation`,
  the statement exists, and its due date is on/after `auto_pay.enabled_from`. Everything else — manual cards,
  rule-only items (帳單未到), statements before the cutoff, any statement while auto-pay is disabled, cards whose
  link the job rejected (`link_ineligible`) — is a **manual item**: it is reminded as 「手動繳」, never projected
  (§5.3), and follows the manual overdue rule (§5.4). The settings read is therefore not informational: it decides
  the class. (Builder-check item; my call.)
- Items are grouped by source account: all auto-paid items with `auto_pay_account_id = X` whose debit day is in
  the window form one block for X, ordered by debit day. Manual items form their own block.
- When a block is rendered it lists **every** in-window unpaid item of that source (full picture, with the balance
  line counting all of them); a block is rendered only when at least one of its items passes the dedupe trigger
  (§5.6). The shortfall line belongs to the block (the source account), not to a card. (My call.)
- `business_days_between(today, debit_day)` = number of business days strictly after `today` up to and including
  `debit_day` (0 when equal, Fri→Mon = 1, Sat→Mon = 1, Sat→Tue = 2). With `lead = 2` a Wednesday debit is first
  reminded on Monday 07:00; a Tuesday debit on Saturday 07:00. (My call.)
- Lead is in business days of the TW calendar for every card; the planned US card gets `lead_overrides` (e.g. 4).
  A card's currency is the account's `currency`. A foreign-currency card paid from a source in the same currency
  is projected normally; a currency mismatch is reported without a projection (§5.3). (My call.)

### 5.3 Projected balance (auto-pay cards)

For source account X and the window's last debit day D:

```
projected(X, D) = balance(X, today)
                − Σ statement_total of unpaid card statements with auto_pay_account_id = X and debit_day ≤ D
                − Σ amount of pending schedule instances debiting X with due_date ≤ D
```

- `balance(X, today)` = the `balance` field of `GET /accounts?as_of=<today>` for X (posted entries through today).
- The first sum includes cards outside the lead window whose debit day is still ≤ D (a 3-card Friday). It excludes
  statements the auto-pay job will skip for a terminal reason the owner has already been told about? No — it
  includes them; a skip the owner fixes before the debit day still needs the money.
- Shortfall = `min_buffer − projected` when positive. Items read 「缺 X 元」 and the block is marked ⚠.
- Currency: each card statement is in the card's currency; a source account in another currency (foreign card paid
  from a TWD account) is reported without a projection: 「幣別不同，請自行確認」. (Builder keeps this simple; FX
  projection is out of scope.)

### 5.4 Manual cards and overdue

- Manual card (`auto_pay_account_id` null) in window: 「手動繳 · 到期 MM/DD · 金額」. No balance line.
- Manual card whose due date has passed, unpaid (§5.1 rule), due within the last 30 days: 「⚠ 逾期未繳 N 天」, every
  day until paid.
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

State file (§6.3) keyed by statement id: `first_notified`, `last_shortfall`, `last_overdue_day`. Rules:
- A statement in the window with no shortfall is included on the day it first enters the window and then only if the
  debit day is today (a final 「今天扣款」 line). Not on the days between.
- A statement with a shortfall is included every day while the shortfall persists; when it clears, one 「已補足」
  line, then silence.
- Overdue / 未扣款 lines are included every day.
- Skips are included once per audit event id.

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

1. **對不上的交易** — `GET /reconciliation/inbox` grouped by account; per account: open count, oldest case age
   (from `created_at` via `/reconciliation/cases?status=open`), then up to `weekly_open_case_limit` items with
   date, amount, merchant/description and the case kind in words (ask / proposed / balance_gap / settlement), and
   「+N 筆」 when truncated. Link to the statement page.
2. **該來沒來的帳單** — for every account that has at least one live statement: expected next `period_end` =
   last live `period_end` + cadence, where cadence = the account's `closing_day` month step for cards, else the
   median gap of its last three live statements (one statement → 1 month). Listed when
   `expected + expected_statement_grace_days < today` and no live statement with `period_end > last` exists.
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
quiet when nothing happens, which the daily job honours.)

### 6.3 State file

`~/.local/state/homehub-notify/state.json` (0600): `{"statements": {<id>: {...§5.6}}, "skips_seen": [event ids],
"last_daily_run": iso, "last_weekly_run": iso}`. Written atomically after a successful send. Missing or corrupt
file → treated as empty (first-run behaviour: every window statement is "new"; acceptable).

## 7. Failure behaviour

- API unreachable / non-2xx / token rejected: the run sends one message 「❌ 帳務通知失敗：<short reason>」 (no
  payload echo, no token) and exits 1. The state file is not touched.
- `hermes send` fails: exit 1, state not written, so the next run resends. systemd `OnFailure` is not used; the
  weekly liveness line covers a dead daily timer.
- A partial API failure (one account's statements 500) fails the whole run; no half-digests.
- Logs: stderr only, one line per run (`daily: 3 items, sent 1 message`), no amounts.

## 8. CLI and tests

```
python -m notify daily  [--as-of YYYY-MM-DD] [--dry-run] [--send]
python -m notify weekly [--as-of YYYY-MM-DD] [--dry-run] [--send]
```
`--dry-run` prints the rendered message(s) to stdout and does not touch the state file; `--send` delivers and
updates state; neither flag → prints and exits 0 (safe default). Exit codes: 0 sent or nothing to send, 1 failure,
2 bad arguments.

Tests (`services/accounting-service/tests/notify/`): pure-function tests for the window, projection, dedupe and
cadence rules with fictional fixtures; golden-text tests for `render`; an API-client test against recorded JSON
(`respx` or a tiny fake); a CLI test that `--dry-run` never calls `send`. No live API in tests.

## 9. Rollout

1. Owner: add label `notify` with scope `read` to `ACCOUNTING_TOKEN_SCOPES` + token to `ACCOUNTING_API_TOKENS` in the
   API `.env`, restart via `homehub-api-ctl`; write the token to `~/.config/homehub-notify-token` (0600).
2. Owner: write `~/.config/homehub-notify.toml` from the example (channel id, hub URL).
3. Release checkout `~/releases/home-hub-notify/<sha>` + venv; `deploy/notify/install.sh` installs and enables the
   two `opc` user timers.
4. First run by hand: `python -m notify daily --dry-run`, then `--send`; same for `weekly`. The owner confirms the
   two messages in the channel.
5. Follow-up issues: Hermes periodic LLM review of the digest; reports; LINE/DM if ever wanted.

## 10. Left to the builder

- Exact httpx client shape (reuse `worker/api.py` patterns: bearer header, redaction, no retries beyond one).
- The Python port of `cycle.ts` (`statementPeriod`, `shiftPeriod`, `dueDate`, month clamping) lives in
  `notify/cycles.py` with tests mirroring `cycle.spec.ts` cases; the frontend stays the reference implementation.
- Whether `GET /accounts?as_of=` already returns a `balance` field (the design assumes it; if not, derive from
  `/accounts/{id}/summary … end_balance` with `date_from = opening`).
- Audit endpoint filters (`/reconciliation/audit` params) for `auto_pay_skipped`; fall back to fetching recent audit
  rows and filtering client-side.
- Message splitting and emoji choices; Discord markdown escaping of account names.
- The `install.sh` idempotency and the release-checkout layout (mirror `~/releases/home-hub-portfolio`).
