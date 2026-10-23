# Loan liabilities: attach a loan to an existing installment schedule, count open payables as 負債 — design

Status: rev 2 (2026-10-23, after the builder check). Owner's problem (after the MOZE cutover): "帳務沒算到我有負債". Decisions below marked
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
- `POST /schedules/definitions/{id}/loan`: create the payable and link it to an existing installment schedule.
- 掛上貸款 in the schedule sheet (排程 sheet in 🔔) for installments without a loan.
- `GET /accounts/debts`: open payable / receivable totals in the main currency.
- 帳戶 page: 總負債 = negative account balances + open payables; a 淨值 line; a one-line note when rates are missing.

Non-goals
- No principal/interest split: a repayment reduces the loan by its full amount (HomeHub's existing loan semantics).
  *[call: banks' amortisation tables are not modelled; the owner enters the remaining principal and the app tracks
  it from there.]*
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

### 4.1 Backend — `POST /schedules/definitions/{definition_id}/loan` (WRITE)
Body = `LoanIn` (reuse: `account_id`, `counterparty_id`, `category_id?`, `amount` (> 0, the REMAINING principal),
`entry_date` (default today), `name?`). Response: `DefinitionDetailOut` (as `GET /definitions/{id}`), status 201.

`schedule_service.attach_loan(db, definition_id: int, loan: LoanIn) -> None`:
1. `take_import_key_shared`, `lock_definition`; the `moze_id`/`import_locked()` guard exactly as `update_definition`.
2. Refuse: `kind != "installment"` → `ValidationError("loan", "只有分期可以掛貸款")`; `status == "ended"` →
   `ConflictError("definition_ended")`; `loan_line(template)` is None → `ValidationError("loan", "排程沒有還款那一行")`;
   the loan line already has `loan_entry_id` → `ConflictError("loan_already_attached")`; `loan.amount` currency must
   equal the loan line's currency (the payable is created in the line's currency: `EntryIn` takes the account's
   currency — the builder checks how `prepare_entry` derives currency and refuses a mismatch with the same message
   `_check_loan` uses).
3. Refuse while any `pending` instance has `due_date <= loan.entry_date` → `ConflictError("catch_up_first")`
   *[call, builder check: an overdue period would post against the new payable and subtract itself twice; the owner
   catches up (補記) first, then attaches with the principal remaining AFTER those periods]*. The remaining principal is
   therefore defined as "owed after the last posted period, before the next pending one".
4. Remaining periods = `times - posted_count` (posted instances); refuse when
   `line_amount * (remaining_periods - 1) >= loan.amount` → `ValidationError("amount", "剩餘期數乘以每期金額已達剩餘本金")`
   *[call: the mirror of `_check_total` for a mid-way attach — the debt must outlast all but the last period, so the
   schedule ends by `loan_closed` rather than leaving debt behind]*.
5. Create the payable exactly as `create_definition` does (kind `payable`, source `manual`, account = the repayment
   line's account, currency = that account's; `LoanIn.account_id` must equal it, else 422 `account` 「貸款要掛在還款帳戶」)
   AND, in the same transaction, a `balance_adjustment` entry on the same account, same `entry_date`, amount
   `-payable.amount` (equal and opposite), name 「掛帳調整：<loan name>」 *[call, builder check: a payable carries an
   amount on its account (`_account_rows` sums every entry), but the cash was already received months ago and sits in
   the balance — without the offset the attach would move the bank balance by the whole principal. The pair nets to
   zero; the payable's `open_amount` still tracks the debt; the timeline shows the two lines once]*. Then set
   `loan_entry_id` on the repayment line (and ONLY there), `template_owner_edited = True`. Do NOT change `times`,
   `total_amount`, `first_seq`, `end_date`, instances or `generated_until` *[call: `total_amount` keeps its MOZE
   meaning (the original principal) so later `update_definition` calls still pass `_check_total`; the payable's
   `open_amount` is the live debt]*.
6. `db.flush()`; the route commits and returns the detail.

`loan_schedule_for` / `LoanScheduleOut` (`schedule_read.py:107 loan_summary` already derives `remaining`/`repaid` from
settlements): `remaining` = `abs(open_amount)` and `repaid` = Σ |settlements| — both POSITIVE magnitudes for payables and
receivables alike *[call: the builder found `remaining` negative for a payable today; the SPA shows magnitudes]*.
`posted_count` and `times` stay as they are (they describe the schedule, not the debt). The entry detail of the payable
already shows `loan_schedule`.

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

### 4.4 SPA — 掛上貸款 in the schedule sheet
- Condition: `detail.kind === 'installment' && detail.loan_entry_id === null && detail.status !== 'ended'`.
- A button `掛上貸款` in the actions row opens an inline form inside the sheet (no new overlay): 對象 (counterparty
  select from `getCounterparties()`, with the existing "新增對象" path if the sheet already has one; otherwise a text
  hint to create the bank under 記帳設定 first — the builder picks whichever exists), 剩餘本金 (decimal input, required,
  > 0), 起算日 (date, default today in the SPA — `LoanIn.entry_date` is required), 名稱 (default = definition name), 分類 (optional category select for kind
  `payable` if the picker is cheap to embed; otherwise omitted and the backend's default applies). 帳戶 is not asked:
  the request sends the repayment line's `account_id` (the backend requires it; the balance is neutralised by the
  adjustment, §4.1 step 5). The form explains in one line: 「輸入到上一期為止還欠多少；若有逾期未補的期數先補記」.
- 確定 → `AccountingService.attachScheduleLoan(id, input: ScheduleLoanInput): Observable<ScheduleDefinitionDetail>`
  (`POST …/schedules/definitions/{id}/loan`, through `bump()` so pages reload) → the sheet re-reads the definition,
  shows `已掛上貸款：剩餘 <formatMoney(open)>` in its header area, and the button disappears. Errors through the
  sheet's existing error line with codes: `definition_ended` 「排程已結束」, `loan_already_attached` 「已經有貸款了」,
  the two 422 messages verbatim.
- The 借還款追蹤 tab needs no change: the bank counterparty appears there with its open amount once the payable exists.

### 4.5 Data step (owner, after deploy)
The owner attaches 40 / 43 / 46 in the sheet with each bank's remaining principal as of the last posted period (or
today). Nothing is migrated automatically *[call: the remaining principal is not derivable from MOZE data]*.

## 5. Failure modes
- Attach twice → 409 `loan_already_attached`; the button is gone after the first success anyway.
- Overdue pending periods → 409 `catch_up_first` 「先補記逾期的期數」; the sheet's 補記 action already exists.
- The attach-day pair (payable + adjustment) nets to zero on the bank account; deleting one of them later is the
  owner's own edit (the payable is protected by `assert_not_referenced`; the adjustment is an ordinary entry).
- Remaining principal too small for the remaining periods → 422 with the message above; the owner fixes the number.
- Editing the schedule later (`update_definition`): `_check_total` unchanged (we never touched `total_amount`);
  `validate_template` keeps the `loan_entry_id` because the SPA's update strips only `kind`/`loan`, not the template
  line field — the builder verifies `definitionUpdateFrom` preserves `loan_entry_id` on the line.
- Deleting the payable → refused by `assert_not_referenced` (existing). Deleting the schedule → the payable stays as an
  open debt (existing behaviour for created-with-loan schedules).
- Missing FX rate for a foreign loan → listed as unconverted, 總負債 says so, nothing silently dropped.

## 6. Testing (names for the plan)
- Backend: `test_attach_loan_links_and_settles` (attach to an installment with posted periods → payable created,
  `loan_entry_id` on the repayment line only, `total_amount`/`times` unchanged, the account balance unchanged by the
  attach (payable + adjustment), posting the next instance settles the payable; `loan_schedule.remaining` = |open
  amount|, `repaid` = settlements only, both positive); refusals (recurring kind, ended, already attached,
  remaining-period rule, account/currency mismatch, overdue pending → `catch_up_first`, imported definition with the
  lock OFF → 409 lock);
  `test_accounts_debts_summary` (two payables in TWD + one JPY without a rate → `open_main` sums the TWD ones,
  `unconverted` lists JPY, receivables separate, closed/settled ones excluded).
- SPA: accounts page totals with debts (liabilities include payables, 淨值 line, unconverted note, fallback when
  `getDebts` fails); schedule sheet shows 掛上貸款 only under the condition, posts the body with the repayment line's
  account, hides the button after success, maps the error codes.

## 7. Rollout
API deploy (no migration) → SPA release → owner attaches the three loans. One PR. Multica: plan review (one round) and
PR review (one round + one re-check) per the standing process.
