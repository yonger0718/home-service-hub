# Loan liabilities: attach a loan to an existing installment schedule, count open payables as 負債 — design

Status: rev 4 (2026-10-23, after the builder check and the AGENT-227/228 reviews). Owner's problem (after the MOZE cutover): "帳務沒算到我有負債". Decisions below marked
*[call]* are mine; the owner opted out of the question round ("ok go").

## 1. Intent

HomeHub already models a loan as an installment schedule whose repayment line points at a `payable` entry
(`loan_entry_id`); every posted repayment settles the payable, its `open_amount` is the remaining debt, and the lender
shows up in 🔔 借還款追蹤. The owner's three bank loans (schedule definitions 40 / 43 / 46, all named 信貸, 84 / 36 / 120
periods, imported from MOZE, 22 / 14 / 1 periods already posted, all `active`, `auto`) came in WITHOUT a payable, so
nothing in the app knows the owner owes anything, and the 帳戶 page's 總負債 counts only negative account balances
(credit cards) — never payables.

Success: the owner attaches each loan once (remaining principal + the bank), 借還款追蹤 shows the three debts going down
every month, and the 帳戶 page shows 總負債 including them and a 淨值 line.

## 2. Goals, non-goals, anti-goals

Goals
- A `repayment` line WITHOUT a loan posts as a plain expense (today it fails validation at posting time — the three
  imported 信貸 would stop posting on 2026-10-25 / 10-26 / 11-08). The same rule covers periods before the attach
  boundary, so history can be reposted or caught up safely.
- `POST /schedules/definitions/{id}/loan`: create the payable and link it to an existing installment schedule from a
  period boundary (`loan_from_seq`) onwards.
- 掛上貸款 in the schedule sheet (排程 sheet in 🔔) for installments without a loan.
- `GET /accounts/debts`: open payable / receivable totals in the main currency.
- 帳戶 page: 總負債 = negative account balances + open payables; a 淨值 line; a one-line note when rates are missing.

Non-goals
- No new amortisation model: the schedule's own per-period `repayment` (principal) amounts settle the debt and its
  `interest` line stays an expense — exactly HomeHub's existing loan semantics. The three 信貸 definitions already carry
  MOZE's per-period principal/interest split (84 / 36 / 120 distinct overrides), so the tracked debt follows the bank's
  table; the owner only confirms the starting figure.
- Receivables (people owe the owner) are NOT added to 總資產 *[call]*; they stay in 借還款追蹤 only.
- No new account kind, no "loan account" with a negative balance, no change to the loan creation path in the entry form.
- No back-dating of already-posted periods: the payable starts at the entered remaining amount as of the entry date.

Anti-goals
- A loan that disappears or double-counts when the schedule is edited, paused, ended or reposted.
- An attach that silently changes `times` / `total_amount` so later edits fail `_check_total`.
- Showing a 負債 figure that mixes currencies without saying so.

## 3. Facts the design rests on (verified 2026-10-23 on main 616f3d4)

- `schedule_service.create_definition` (services/accounting-service/app/services/schedule_service.py:70-125) creates
  the payable from `payload.loan: LoanIn` (`prepare_entry(EntryIn(kind="payable", account_id, amount, entry_date,
  category_id, counterparty_id, name))` + `insert_prepared(remember=False)`, source `manual`), sets
  `line["loan_entry_id"]` on every `repayment` line, and sets `total_amount = loan.amount`. `_check_total` (:61-65)
  refuses `per_period * (times-1) >= total`.
- `update_definition` (:128-205) cannot set a loan (`DefinitionUpdateIn` has no `loan`, `extra="forbid"`); it raises
  `EditLockedError` for an imported definition (`moze_id` set) unless `import_locked()` — the cutover lock is ON since
  2026-10-23 09:31 UTC, so imported definitions are editable now; `ConflictError("definition_ended")` when ended.
- `validate_template` (`schedule_templates.py:63-123`): one `repayment`/`collection` line; `_check_loan` requires the
  linked entry to be `payable`/`receivable`, not a settlement, not a child, same currency as the line.
- Posting (`schedule_posting.py:152-163, 205-236`): the repayment line settles the loan with
  `min(amount, open_amount)`; when `open_amount == 0` the outcome is `loan_closed`, remaining pending instances are
  skipped and the definition is `ended`. So an attached loan ends the schedule by itself when the debt is paid.
- `open_amount(db, entry)` = `abs(amount + Σ settlements in the same currency)` (`settlement_service.py:21-28`).
  `GET /entries?open=true` lists open receivable/payable originals with `open_amount`; `GET /counterparties` carries
  per-currency `open_amounts` (receivable and payable net per counterparty, no conversion).
- `loan_schedule_for(db, entry_id)` (`schedule_read.py:297`) finds the definition by `template.lines[].loan_entry_id`
  and returns `LoanScheduleOut{definition_id, name, status, posting_mode, posted_count, times, next_due_date,
  next_amount, remaining, repaid, needs_check}` — the builder checks how `remaining`/`repaid` are derived (settlements
  vs posted instances) and makes them settlement-based for an attached loan (§4.1).
- Accounts page: `GET /accounts` rows have `balance_main` (None without a rate), `include_in_total`, `is_credit`;
  `accounts.ts:152-160` computes `total/assets/liabilities` client-side; `accounts.html:63` renders
  `總資產 … · 總負債 …`; `load()` re-runs on `accountsChanged`/`entriesChanged`/`preferenceChanged`.
- Schedule sheet (`reminders/schedule-sheet/schedule-sheet.ts`): `definitionId = input.required<number>()`,
  `closed = output<void>()`, actions pause/resume/mode/catch-up/edit/end/remove + per-period amount edits; it does not
  call `updateScheduleDefinition`. `DefinitionOut.loan_entry_id` exists (from the loan line). SPA models:
  `ScheduleLoanInput {account_id, counterparty_id, category_id|null, amount, entry_date, name|null}`.
- Rates: `ledger_service` has `latest_rates` / `main_currency` helpers used for `balance_main`.
- Definitions 40 / 43 / 46: `kind=installment`, `status=active`, `posting_mode=auto`, `moze_id` set, `total_amount`
  set, `first_seq=1`, two template lines (line 0 `repayment` on account 78 / 78 / 82, line 1 interest or similar),
  posted 22 / 1 / 14, pending 62 / 119 / 22.

## 4. The design

### 4.0 Backend — a loan line without a loan (prerequisite, also the fix for the imminent posting failure)
`schedule_templates._check_loan` runs only when `loan_entry_id` is set; a `repayment`/`collection` line with
`loan_entry_id = None` is valid ("no loan yet"). At posting time (`schedule_posting`, the `kind in LOAN_LINE_KINDS`
branch) a loan line that has NO applicable loan — `loan_entry_id` None, or the instance's `seq < line["loan_from_seq"]`
(§4.1) — writes a plain entry the way the `interest` branch does (a direct `LedgerEntry(...)` + `db.flush()`, NOT
`prepare_entry`, which refuses a counterparty on expense/income): `kind="expense"` for repayment / `"income"` for
collection, `amount = -amount` / `+amount`, the line's account and its currency, `entry_date = posted_date = due`,
`category_id = None` *[call: the repayment line's category is a payable/receivable-kind category and must not be
reused on an expense; there is no system "repayment" category — the owner may categorise the rare plain entry later]*,
`project_id`, `name`, `merchant`, `tags`, `counterparty_id = line["counterparty_id"]`, `source = SOURCE`; no settlement,
no loan lock; the entry joins the period group like every other line. Repost (`repost_instance`) and reopen→post go through
the same branch, so periods before the boundary never touch the loan. `PostResult` outcome stays `posted`.
Template line key `loan_from_seq: int | None` (≥ 1; only on loan-kind lines; carried through `LINE_KEYS`,
`normalize_template`, `validate_template`, `TemplateLineIn`/`TemplateLineOut`, additive). Lifecycle guard: in
`update_definition`, when the incoming loan line keeps the same `loan_entry_id` as the stored line but carries no
`loan_from_seq`, the stored boundary is copied forward (a client that does not know the key cannot erase it); the SPA's
`schedule-save.ts` line whitelist (`LINE_INPUT_KEYS`) and `ScheduleLineInput` gain `loan_from_seq` so a load → edit →
PUT round trip preserves it.
Any other `loan_from_seq` in a PUT — a moved boundary, one introduced (None → N), or one sent with a new or different
loan — is refused with 422 `template.lines[i].loan_from_seq` 「起算期只能由掛上貸款設定」 (only §4.1 sets it).
A loan-kind line's `counterparty_id`, when set, must name an existing counterparty (422 「找不到對象」); the interest
line names the loan's counterparty when the loan applies to the period, else falls back to the loan line's own
`counterparty_id`.

### 4.1 Backend — `POST /schedules/definitions/{definition_id}/loan` (WRITE)
Body = `LoanIn` (reuse: `account_id`, `counterparty_id`, `category_id?`, `amount` (> 0, the REMAINING principal),
`entry_date` (required by the API; the SPA defaults it to today), `name?`). Response: `DefinitionDetailOut`
(the same serializer as `GET /schedules/definitions/{id}`, which gains `pending_principal` and `loan_remaining`, §4.1b),
status 201.

`schedule_service.attach_loan(db, definition_id: int, loan: LoanIn) -> int` (returns the payable id):
1. `take_import_key_shared`, `lock_definition`; the `moze_id`/`import_locked()` guard exactly as `update_definition`.
2. Refuse: `kind != "installment"` → `ValidationError("loan", "只有分期可以掛貸款")`; `status == "ended"` →
   `ConflictError("definition_ended")`; no line with `kind == "repayment"` (collection lines do not qualify) →
   `ValidationError("loan", "排程沒有還款那一行")`; that line already has `loan_entry_id` →
   `ConflictError("loan_already_attached")`; `loan.account_id != line["account_id"]` → `ValidationError("account",
   "貸款要掛在還款帳戶")` (the payable is created in that account's currency, which is the line's currency).
3. Boundary: `loan_from_seq` = the lowest `pending` instance's `seq`; none pending → `ConflictError("nothing_pending")`.
   Refuse while any pending instance's 到期日早於起算日 (`due_date < loan.entry_date`) → `ConflictError("catch_up_first")`
   *[call: the owner posts (補記) those periods first — they post as plain expenses under §4.0 — then attaches with the
   principal remaining after them; a period due ON `entry_date` is not overdue — attach, then post it, settles it]*.
   Then refuse while any instance with `seq > loan_from_seq` is `posted` or `skipped` →
   `ConflictError("later_periods_settled: 之後已有入帳或略過的期數，請先還原")` *[call: such a period sits inside the
   loan's range but never settles it; the owner reopens (還原) it first. Step 4's status filter stays as defence]*.
4. Remaining principal series = for every `seq` from `loan_from_seq` to `times` (inclusive): the repayment line's
   amount for that period — the instance's `amount_override[line index]` when the instance exists and has one, else
   the template amount (periods beyond the generation horizon count too); only periods that will still settle the
   loan count — an existing `pending` instance or a seq with no instance yet — so instances in that range already
   `posted` (early, as a plain entry) or `skipped` are left out. `due_total` = Σ series; `all_but_last` =
   `due_total` minus the last period's amount. Refuse `loan.amount > due_total` → `ValidationError("amount",
   "超過之後各期本金合計")` and `loan.amount <= all_but_last` → `ValidationError("amount", "剩餘期數乘以每期金額已達剩餘本金")`
   *[call: the debt must be cleared by the last period and not before the last one; a smaller remaining than the
   series (an early repayment) is allowed and ends the schedule early through `loan_closed`]*. `pending_principal`
   (§4.1b) is this `due_total`, so the prefilled value always passes.
5. Create the payable exactly as `create_definition` does (kind `payable`, source `manual`, account = the repayment
   line's account) AND, in the same transaction, a `balance_adjustment` entry on the same account with the same
   `entry_date` AND `posted_date`, amount `-payable.amount`, name 「掛帳調整：<loan.name or definition.name>」 *[call,
   builder check: the cash was received months ago and sits in the balance; the pair nets to zero on every date]*.
   Set `line["loan_entry_id"]` and `line["loan_from_seq"]` on the repayment line only, `definition.template_owner_edited
   = True`; `validate_template`. Do NOT change `times`, `total_amount`, `first_seq`, `end_date`, instances (their
   `amount_override` per-period principal/interest split stays intact) or `generated_until`.
6. `db.flush()`; the route commits and returns the detail.

### 4.1b Read model
- `DefinitionDetailOut.pending_principal: Decimal | None` = `due_total` of §4.1 step 4 for an installment with a
  repayment line (None otherwise); `DefinitionDetailOut.loan_remaining: Decimal | None` = `open_amount` of the linked
  loan (None when unlinked). Both additive.
- `loan_summary` (`schedule_read.py:100-112`): `remaining` and `repaid` become POSITIVE magnitudes for payables and
  receivables alike; the SPA consumers that showed the signed value (`entry-detail.ts:129`, `schedule-queue.ts:117`,
  `entry-detail-schedule.spec.ts:96`) switch to the magnitude with the kind's own wording (欠 / 待收) — same PR.
  In the schedule list row, 欠 / 待收 apply only to loan-kind templates (from the line carrying `loan_entry_id`, else
  the repayment / collection line's own kind); a template without such a line (card installment) keeps 「剩餘」.

### 4.2 Backend — `GET /accounts/debts` (READ)
```
{ "main_currency": "TWD",
  "payables":    { "open_main": Decimal, "count": int, "unconverted": [{"currency": "JPY", "amount": Decimal}] },
  "receivables": { "open_main": Decimal, "count": int, "unconverted": [...] } }
```
Open receivable/payable originals (`open_debt_filter`), each `open_amount` converted with `latest_rates` into the main
currency; a currency without a rate is listed under `unconverted` (and not added). Amounts are positive magnitudes.
`ledger_service.debts_summary(db) -> dict`. No schema change. Route declared BEFORE `/accounts/{account_id}` in
`routers/accounts.py` (path precedence).

### 4.3 SPA — 帳戶 page totals
- `AccountingService.getDebts(): Observable<DebtsSummary>` (`models/accounting.model.ts`: `DebtsSummary {main_currency,
  payables: DebtTotal, receivables: DebtTotal}`, `DebtTotal {open_main: string, count: number, unconverted: {currency,
  amount: string}[]}`).
- `accounts.ts`: `debts = signal<DebtsSummary | null>(null)` loaded in the same `load()` (errors → null, totals fall back
  to today's behaviour); `liabilities = Σ max(0, −balance_main) + payables.open_main`; `assets` unchanged; new
  `netWorth = assets − liabilities`; `total` (總額) unchanged *[call: 總額 keeps meaning "sum of included accounts"]*.
- `accounts.html` totals line becomes two lines: `總資產 X · 總負債 Y（含借貸 Z 筆）` and `淨值 X−Y`; when
  `payables.unconverted` is non-empty append `· 另有 N 種幣別未換算`; when `debts` failed to load, render the old line
  unchanged (no 淨值).
- As built, the unconverted note is its own `.unconverted-hint` line under the totals (「另有 N 種幣別未換算」), not
  appended to the totals line.

### 4.4 SPA — 掛上貸款 in the schedule sheet
- Condition: `detail.kind === 'installment' && detail.loan_entry_id === null && detail.status !== 'ended'` AND the
  template has a `repayment` line (no form without one).
- A button `掛上貸款` in the actions row opens an inline form inside the sheet (no new overlay): 對象 (counterparty
  select from `getCounterparties()`, with the existing "新增對象" path if the sheet already has one; otherwise a text
  hint to create the bank under 記帳設定 first — the builder picks whichever exists), 剩餘本金 (decimal input, required,
  > 0, PREFILLED with `detail.pending_principal` = 之後各期本金合計 — the owner may override it, e.g. after an early
  repayment), 起算日 (date, default today in the SPA — `LoanIn.entry_date` is required), 名稱 (default = definition name), 分類 (optional category select for kind
  `payable` if the picker is cheap to embed; otherwise omitted and the backend's default applies). 帳戶 is not asked:
  the request sends the repayment line's `account_id` (the backend requires it; the balance is neutralised by the
  adjustment, §4.1 step 5). The form explains in one line: 「輸入到上一期為止還欠多少；若有逾期未補的期數先補記。起算日＝掛帳日，不是當初借款日」.
  起算日's `min` is the latest posted instance's `due_date` when the definition has posted instances (else no min).
- 確定 → `AccountingService.attachScheduleLoan(id, input: ScheduleLoanInput): Observable<ScheduleDefinitionDetail>`
  (`POST …/schedules/definitions/{id}/loan`, through `bump()` so pages reload) → the sheet re-reads the definition,
  shows `已掛上貸款：剩餘 <formatMoney(detail.loan_remaining)>` in its header area, and the button disappears. Errors through the
  sheet's existing error line with codes: `definition_ended` 「排程已結束」, `loan_already_attached` 「已經有貸款了」,
  `catch_up_first` 「先補記逾期的期數」, `nothing_pending` 「沒有待入帳的期數」, `later_periods_settled`
  「之後已有入帳或略過的期數，請先還原」 and the 422 messages verbatim.
- The 借還款追蹤 tab needs no change: the bank counterparty appears there with its open amount once the payable exists.

### 4.5 Data step (owner, after deploy)
The owner attaches 40 / 43 / 46 in the sheet with each bank's remaining principal as of the last posted period (or
today). Nothing is migrated automatically *[call: the remaining principal is not derivable from MOZE data]*.

## 5. Failure modes
- Attach twice → 409 `loan_already_attached`; the button is gone after the first success anyway.
- Overdue pending periods → 409 `catch_up_first` 「先補記逾期的期數」; 補記 works because an unlinked repayment line posts
  as a plain expense (§4.0).
- A period after the boundary already posted early or skipped → 409 `later_periods_settled`
  「之後已有入帳或略過的期數，請先還原」; the owner reopens it, then attaches.
- Reposting / reopening a period before `loan_from_seq` → plain expense again, the loan untouched (§4.0); a period at
  or after the boundary settles the loan as usual.
- Editing the definition later (`update_definition`) deletes and regenerates future pending instances and keeps only
  the last period's override — the imported per-period principal/interest split would be flattened. This is existing
  behaviour and OUT of this change's scope; recorded as a follow-up (backlog: preserve overrides on edit). The attach
  itself never goes through `update_definition`.
- The attach-day pair (payable + adjustment) nets to zero on the bank account; deleting one of them later is the
  owner's own edit (the payable is protected by `assert_not_referenced`; the adjustment is an ordinary entry).
- Remaining principal too small for the remaining periods → 422 with the message above; the owner fixes the number.
- Editing the schedule later (`update_definition`): `_check_total` unchanged (we never touched `total_amount`);
  `validate_template` keeps the `loan_entry_id` because the SPA's update strips only `kind`/`loan`, not the template
  line field — the builder verifies `definitionUpdateFrom` preserves `loan_entry_id` on the line.
- Deleting the payable → refused by `assert_not_referenced` (existing). Deleting the schedule → the payable stays as an
  open debt (existing behaviour for created-with-loan schedules).
- Missing FX rate for a foreign loan → listed as unconverted, 總負債 says so, nothing silently dropped.
- Statement matching after the attach: the payable (+X) is a `payable` entry and so a statement-matching candidate;
  the next bank statement on that account shows one `ledger_only` case for it, which the owner dismisses in 待處理
  (the −X adjustment is excluded from matching and `balance_gap` nets to zero; `anchor_opening` is blocked only for
  statements whose as_of is on/before the attach date). Accepted, not special-cased.

## 6. Testing (names for the plan)
- Backend: `test_unlinked_repayment_posts_as_expense` (a repayment line with no loan posts a plain expense; repost and
  reopen→post of a period before `loan_from_seq` do the same after an attach; the payable untouched);
  `test_attach_loan_links_and_settles` (attach to an installment with posted periods → payable created,
  `loan_entry_id` on the repayment line only, `total_amount`/`times` unchanged, the account balance unchanged by the
  attach (payable + adjustment), posting the next instance settles the payable; `loan_schedule.remaining` = |open
  amount|, `repaid` = settlements only, both positive); refusals (recurring kind, ended, already attached,
  remaining-period rules incl. `amount > due_total`, periods beyond the horizon counted, zero pending →
  `nothing_pending`, account/currency mismatch, collection-only/expense-only templates, overdue pending →
  `catch_up_first` then a real 補記 + attach end to end, imported definition with the lock OFF → 409 lock; attach
  keeps every instance's `amount_override`);
  `test_accounts_debts_summary` (two payables in TWD + one JPY without a rate → `open_main` sums the TWD ones,
  `unconverted` lists JPY, `count` = 3 (every open original, converted or not), receivables separate; a fully settled
  original and a closed one excluded, a partly settled one counts its open part, a JPY one WITH a cached rate converts).
- SPA: accounts page totals with debts (liabilities include payables, 淨值 line, unconverted note, fallback when
  `getDebts` fails); schedule sheet shows 掛上貸款 only under the condition, posts the body with the repayment line's
  account, hides the button after success, maps the error codes.

## 7. Rollout
API deploy (no migration) → SPA release → owner attaches the three loans. One PR. Multica: plan review (one round) and
PR review (one round + one re-check) per the standing process.
