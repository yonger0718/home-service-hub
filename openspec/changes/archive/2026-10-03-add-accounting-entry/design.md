## Context

Phase 1 is live: 61 accounts, 7,168 entries imported from the CSV, balances match MOZE except the 9 accounts with foreign-currency rows converted via `fx_api`. This change makes HomeHub the system of record. Design decisions D1–D11 in the archived phase 1 design remain in force; numbering continues here.

Facts this design relies on (from the backup `~/workspace/moze-backup/MOZE_4.0.zip`, exported 2026-10-01, and the MOZE docs digest):

- **Backup format.** A zip with `moze.realm` (Realm file format 22, unencrypted, "info" file says `version: 205`) and `re/*.jpg` receipt photos. Realm JS 12.x opens it on this host after an in-place file-format upgrade, so the converter works on a copy. The process must `process.exit()` after closing, because Realm keeps the event loop alive.
- **Classes used** (live rows, `isDeleted == false`): `AHAccount` 72, `AHAccountGroup` 11, `AHCategory` 25 (main categories, with `colorHex` and `imageName`), `AHClassification` 120 (sub-categories, with defaults for account and project), `AHProject` 7, `AHTarget` 306 (counterparties), `AHRecord` 7,553, `AHTransfer` 544, `AHPackage` 974, `AHBonusReward` 100, `AHBonusRewardSharing` 16, `AHCreditSharing` 60, `AHCurrencyConversion` 210, `AHPeriod` 11, `AHInstallment` 14, `AHPreference` 1, `AHAppConfig` 1.
- **Record `type`** (verified by sign, category type and links):

  | type | Meaning | Sign of `price` | Links |
  |---|---|---|---|
  | 0 | expense | − | `feeID` → auto FX-fee record; `refundID`; `packageID` |
  | 1 | income | + | |
  | 2 | transfer leg | − out / + in (`isTransferIn`) | `transferID`; category type 7 marks a card payment |
  | 3 | receivable (借出/代付/報帳) | − | `target` |
  | 5 | collection of a receivable (收款) | + | `target` |
  | 4 | payable (借入) | + | `target` |
  | 6 | repayment of a payable (還款) | − | `target`, `relatedID` → the payable, `eventID` (installments) |
  | 7 | balance adjustment | ± | |
  | 12 | manual fee record (退刷, 年費) | − | |
  | 16 | automatic foreign-transaction fee | − | referenced by the parent's `feeID` |
  | 13 | discount record | + | |
  | 14 | reward | + | `rewardID` → `AHBonusReward`, `rewardRecordID` → source record |
  | 15 | installment interest | − | `eventID` |

  `total = price + fee + bonus` (fee ≤ 0, bonus ≥ 0), confirming D4. `date` is the consumption time; `chargeDate` is the posting time and differs on 2,791 rows (328 postponed expenses, 1,876 rewards, 240 installment repayments). `currencyConversion.exchangeRate` is the main-currency (TWD) value of one unit of the record currency, e.g. a ¥5,390 record carries `0.2163`, so the TWD amount is `5390 × 0.2163 = 1,166`.
- **Scheduled future instances.** Installments and recurring records exist as future-dated rows (e.g. 240 repayment + 240 interest rows of loans). The CSV export omits them. MOZE excludes future rows from balances.
- **Account settings in use.** 29 credit accounts; 20 have a 主帳戶 (`combinedAccount`); 58 belong to a shared-limit set; 25 have auto-debit; 4 have a foreign-transaction fee (1.5 % or 2 %); `startDay` (closing day) takes values 1, 7, 8, 10, 11, 23, 24, 25, 27, 28, 29; `paymentDeadlineType` 0 or 1 with `paymentDeadline` 7–22. All accounts have `isBalanceIncluded = true`. Groups are MOZE's defaults keyed `APP_GROUP_CASH`, `APP_GROUP_BANK`, `APP_GROUP_CREDIT_CARD`, `APP_GROUP_STORED_VALUE_CARD`, `APP_GROUP_GIFT_VOUCHER`, `APP_GROUP_REWARD`, `APP_GROUP_INSURANCE_POLICY`, `APP_GROUP_SECURITIES`, `APP_GROUP_CRYPTO_CURRENCY`, `ACCOUNT_OTHERS`, `ARCHIVE`.
- **Reward rules in use.** `type` 0 (percent, 93) or 1 (fixed, 7); `rewardPeriodType` 0 (statement cycle) only; `rewardTimeType` 0 (N days after the transaction, delay 0 or 2), 2 (after the window ends; month offset 0–2, day 1–31) or 3 (manual, 1 rule); `rewardCalculation` 0/1/4 and `totalRewardCalculation` 0/1/2 (rounding); `totalRewardLimit` on 56; `rewardSharingID` on 10; `isBasic` on 2; reward account = the card on 57, another account on 43; `rewardProjectID` on 10. Thresholds (`spendThreshold`, `totalSpendThreshold`, `minCountThreshold`) and per-transaction caps (`rewardLimit`) are 0 everywhere.
- **Preferences.** Expense red / income green; calculator keypad layout; week starts on Sunday; main currency TWD; totals abbreviated.
- **Frontend shell.** 72 px icon dock and frosted header at ≥ 760 px; bottom tab bar with group sub-nav below 760 px; PrimeNG 21 and Bootstrap 5 available; design tokens in `styles.scss`.

Decisions made with the owner on 2026-10-02: split phase 2 into 2a/2b (Q1); mirror MOZE's entry flow (Q2); floating "+" on desktop and a centre "+" in the mobile tab bar (Q3); the timeline is the accounting home (Q4); emoji category icons (Q5); custom keypad on phones, expression input on desktop (Q6); MOZE-sourced rows read-only until import lock (Q7); backup replaces CSV as the cutover source (Q8); build the used reward subset first (Q9); add a posting date (Q10); split entry UI in 2a (Q11); settle flow in 2a (Q12); e-invoice out of scope, later via monthly CSV (Q13).

## Goals / Non-Goals

**Goals (2a)**
- The owner can enter every daily record kind on the phone as fast as in MOZE, and edit or delete it.
- The ledger holds the fields and links 2b needs: posting date, rules attached to entries, reward entries linked to source and rule, statements' inputs (closing day, due rule, 主帳戶, limits), counterparties and settlements.
- The backup import reproduces MOZE's ledger with its own FX amounts and all links, and seeds settings and rules, so cutover needs no manual setup.
- Phase 1's phone gaps are closed.

**Non-Goals (2a)**
- Computing or posting rewards; statements; card payments as a distinct kind; auto-debit; recurring and installment generation; e-invoice sync; receipt photos; net worth with broker links; reports; Hermes.

## Decisions

### D12. The MOZE backup is the import source; a Node converter feeds a Python importer
The backup carries links and settings the CSV lacks, and MOZE's own converted amounts. Reading Realm from Python is not supported, so a small Node tool (`tools/moze-realm-export`, `realm` pinned) does one job: unzip, copy `moze.realm` to a scratch directory, open it (which upgrades the copy's file format), and write one JSON document with the live rows of the classes listed in the spec, with object links flattened to identifiers. The Python importer (`moze_backup_import_service`) reads that JSON. The converter is invoked as a subprocess from the CLI and the REST endpoint; its path comes from `MOZE_REALM_EXPORTER` (default: the repo tool run with the host's `node`). Converter failures fail the import before any lock or transaction. The CSV importer is kept unchanged as a fallback; both share the lock, the `import_run` table and the `ACCOUNTING_IMPORT_LOCKED` flag, and each full replace deletes rows of **both** MOZE sources so the two never coexist.

### D13. MOZE identifiers give stable identity
Every imported account, group, category, project, counterparty, rule, group and entry stores its Realm identifier in `moze_id`. Re-imports match accounts by `moze_id` first, then by name (for accounts created by the CSV importer or by hand). This removes the rename problem of D6 for backup imports; `--rename` stays for name collisions. Entries are still fully replaced, not upserted, so manual edits to imported rows are impossible by construction (D19).

### D14. Posting date
`ledger_entry.posted_date DATE NOT NULL DEFAULT entry_date`. Statement membership, reward windows (2b) and card balances use `posted_date`; the timeline and the canonical order (D10) use `entry_date`. Import maps `chargeDate` → `posted_date`. The entry form exposes it under 進階 as 入帳日 (default = date).

### D15. Splits and claims are entry groups, not parent entries
A MOZE multi-category record is a parent with child records that each move one account. The parent moves nothing. So a group is a separate table `entry_group (id, kind: split | reward_claim | installment, name, merchant, description, moze_id)` and children carry `group_id`. Balances, running balances and listings are unchanged; the timeline shows a group as one row with the sum and a count, and the detail lists the children. Fee and discount children keep `parent_entry_id` (D4); a child of a group may itself have fee children.

### D16. Counterparties and settlements
`counterparty (id, name UNIQUE, moze_id)`. `ledger_entry.counterparty_id` replaces the free-text column. A collection or repayment entry carries `settles_entry_id` when MOZE links it (`relatedID`) or when created through the settle endpoint; otherwise the open amount is computed per counterparty as the sum of its receivable, payable, collection and repayment entries, which is what the owner approved for the POC. The receivables view (phase 3) builds on both.

### D17. Reward rules are data in 2a, engine in 2b
Rules are upserted by `moze_id` on re-import, never deleted and recreated, so attachments made by manual entries survive. A rule that disappears from the backup is deleted only when nothing references it; otherwise it is disabled and reported. The CSV importer never touches rules. `reward_rule` stores the fields the owner uses (method, rate or fixed amount, window = statement cycle, posting timing, delay days, month offset and day, per-transaction and total rounding, total cap, shared-cap set, basic flag, reward account, reward project, start and end, enabled, `moze_id`). Unused MOZE fields (thresholds, per-transaction cap, calendar-month and campaign windows, ratio method) are not modelled; they are listed in the 2b backlog. `entry_reward_rule (entry_id, rule_id)` records which rules a source entry carries. Reward entries (`kind = reward`) carry `reward_rule_id` and `reward_source_entry_id`. 2a imports all of this and shows it read-only (chips on entries, rule list per account). Nothing computes rewards in 2a. Manual entries can attach rules so 2b can compute them later.

### D18. Future-dated scheduled rows are skipped in 2a but preserved
Records whose `entry_date` is after the backup's export date are not imported as entries; the report counts them per type. The importer stores the raw `AHPeriod` and `AHInstallment` rows and every skipped record as JSON in a `moze_schedule` table (class, `moze_id`, payload, `import_run_id`), replaced on each import, so phase 4 can regenerate recurring and installment instances without the original backup. Consequence: loan balances in HomeHub exclude future repayments, which matches MOZE's displayed balances. Between cutover and phase 4 the owner enters due installments by hand from the 分期 list the settings page shows from `moze_schedule` (a handful per month).

### D19. Imported rows are read-only until cutover; settings follow a precedence rule
While `ACCOUNTING_IMPORT_LOCKED` is false, update and delete of any entry or group that came from MOZE (`source IN ('moze_import','moze_backup')`, or a row with `moze_id`) return HTTP 409 with a message naming the lock. Manual rows (`source = 'manual'`) are always editable and are never touched by imports. After the flag is set, everything is editable and no import runs. This removes the silent-wipe risk during the mirror period.

Account **settings** are editable at any time. Precedence on re-import: an account created by an import takes its settings from the backup until the owner edits it locally, which sets `settings_locally_edited = true`; from then on a re-import updates only `opening_balance`, `is_archived` and `name` (by `moze_id`) and leaves every other column alone, and the dry-run report lists the accounts whose backup settings now differ from the local ones. Clearing the flag (a button in account settings, 還原 MOZE 設定) lets the next import overwrite again. Currency is never changed by import or by edit while the account has entries.

### D20. Balance adjustment is a checkpoint at save time
The endpoint takes a target balance; the server computes `delta = target − current balance` at that moment and stores a `balance_adjustment` entry with `amount = delta` and the target in `description`. Back-dated edits made later do not re-assert the checkpoint. MOZE's stricter semantics are not needed for the owner's own reconciliation (Q9 of phase 1).

### D21. Foreign-currency entry on manual records
The form takes the original amount and currency. The server proposes today's rate from the `fx_rate` cache (fetched on demand, D11) and the converted amount; the owner may overwrite either the rate or the converted amount, which sets `fx_source = 'manual'`. Accounts with a foreign-fee setting get a fee child proposed automatically (`name = 國外交易手續費`, amount = converted × pct, rounded per the account setting); the owner can remove it. The backup import stores MOZE's own amounts with `fx_source = 'moze_backup'`.

### D22. Frontend structure
- Routes: `/accounting` (timeline, home), `/accounting/accounts`, `/accounting/accounts/:id` (passbook), `/accounting/accounts/:id/settings`, `/accounting/entry` (new; `?kind=&copy=`), `/accounting/entries/:id`, `/accounting/entries/:id/edit`, `/accounting/settings`. Old redirects stay.
- Breakpoints: < 760 px phone (one screen per route, bottom tab bar); 760–1023 px one pane with detail and entry as a right-side sheet; ≥ 1024 px two persistent panes (list left, detail / entry / settings right) inside the existing dock shell. Row selection swaps the right pane without navigation on ≥ 1024 px; the URL still updates so links work.
- Components: `ledger-timeline`, `entry-form` (kind tabs, category picker, amount tile, tiles, chips, note, advanced), `category-picker` (grid on phone, chip bar + dropdown on desktop), `amount-keypad` (calculator layout: `÷ × − +`, digits, `00`, `⌫`, `C`, `↵`, `✓`; evaluates left to right with standard precedence), `fx-sheet`, `fee-sheet`, `split-lines`, `entry-detail`, `account-settings-form`, `accounting-settings`, `ledger-accounts` (grouped). Shared `format.ts` is extended with per-currency decimals from the account.
- Expression input on desktop: a text field whose value is evaluated on blur or Enter with the same evaluator as the keypad; invalid input keeps the field in error.
- Last-used account and project per category are remembered client-side (`localStorage`) and server-side (`category.default_account_id`, `default_project_id`, seeded from `AHClassification`).

### D23. Mobile tab bar centre "+"
The tab bar below 760 px gets a fifth slot in the centre: a raised "+" that navigates to `/accounting/entry` from any group. Entry is the most frequent action in the app, so the button is global, not accounting-only. The dock (≥ 760 px) is unchanged; accounting pages render a floating "+" bottom-right.

### D24. PWA shortcut
`manifest.json` gains `shortcuts: [{ name: '記一筆', url: '/accounting/entry' }]`. No service worker is added; the app stays Tailscale-only (Q23 of phase 1).

### D25. Icons and colours
`category.icon` is an emoji string, `category.color` a hex string. Import maps MOZE `imageName` of main categories to emoji by a fixed table (Food 🍜, Transport 🚌, Entertainment 🎮, Shopping 🛍️, Personal 💇, Medical 🩺, House 🏠, Family 👨‍👩‍👧, Living 🧾, Learning 📚, Others 📦, Income 💰, Transfer ⇄, Receivable 🤝, Payable 🏦, Adjustment ⚖️, fee / discount / reward / interest 🧾) and copies `colorHex`. Sub-categories inherit the parent's colour and icon unless set. Accounts get an emoji by group (cash 👛, bank 🏦, credit card 💳, stored-value 🚌, voucher 🎟️, reward points ⭐, insurance 🛡️, securities 📈, crypto 🪙, other 📁). All editable in settings.

### D26. Account groups
`account_group (id, name UNIQUE, sort_order, moze_id)`. Import maps the MOZE keys to Chinese names (現金, 銀行, 信用卡, 電子票證, 禮券, 紅利點數, 保單, 證券, 加密貨幣, 其他); `ARCHIVE` is not a group, it is `is_archived`. The accounts page groups by `account_group.sort_order`, with a collapsed 封存 section.

### D27. Preference row
A single-row `preference` table: `expense_income_colors` (`red_green` | `green_red`), `keypad_layout` (`calculator` | `phone`), `week_start` (0–6), `main_currency`, `hide_rewards_on_timeline`, `abbreviate_totals`. Seeded from `AHPreference`. The frontend reads it once per session.

## Risks / Trade-offs

- **[Realm SDK breaks on the host]** → The converter is pinned (`realm@12.x`, verified on aarch64 with Node 22). The CSV importer remains as fallback, with the phase 1 FX path.
- **[`type` decoding is wrong for a rare type]** → The importer rejects unknown types, and the report cross-checks per-type counts and per-account balances against `AHAccount.balanceInfo` from the backup. The comparison uses only MOZE-sourced entries posted up to the backup's cache date, so manual entries and the import's own timing cannot cause false mismatches; a mismatch on a comparable account fails strict mode. Accounts without a comparable cached balance, and MOZE's two system accounts, are reported for the owner's manual check instead. Whether `balanceInfo` is comparable at all is confirmed on the real backup in the first implementation task; until then the manual balance check of phase 1 stays in the acceptance list.
- **[Settlements and refunds across currencies]** → 2a allows them only on accounts in the original entry's currency; counterparty open amounts are reported per currency. Cross-currency settlement is a 2b/3 item.
- **[FX direction misread]** → Acceptance scenario with a known record (¥5,390 → $1,166). The importer also sanity-checks each conversion against the `fx_rate` cache within a tolerance and fails the import on a gross mismatch.
- **[Two-pane complexity on desktop]** → Panes are the same components as the phone screens; only the container differs. The phone layout is the acceptance bar; desktop is best-effort within this change.
- **[Keypad evaluator edge cases]** → Pure function with unit tests (precedence, division, leading operator, repeated equals, locale).
- **[Edit lock confuses the owner during mirroring]** → The detail view shows a banner "MOZE 匯入資料，切換後可編輯" on locked rows, and the settings page shows the lock state.
- **[Skipping future rows diverges from MOZE for loans]** → Only displayed balances matter until phase 4; the report lists the skipped counts.

## Migration Plan

1. Alembic migration adds the new tables and columns, back-fills `posted_date = entry_date`, migrates `counterparty` text into `counterparty` rows, and adds the enum values. Downgrade reverses it and refuses if any `manual` row exists.
2. Deploy backend and frontend together; update Caddy `@hub_spa`.
3. Run `python -m app.services.moze_backup_import_service ~/workspace/moze-backup/MOZE_4.0.zip --dry-run`, review the report (per-type counts, skipped future rows, balance comparison), then run it for real.
4. The owner spot-checks the timeline and a few account passbooks against MOZE.
5. Rollback: `alembic downgrade` to the phase 1 head and republish the previous SPA build. The CSV import remains available.

## Phasing (updated)

- **2a `add-accounting-entry`** (this change): above.
- **2b `add-accounting-card-rules`**: reward engine over `reward_rule` (percent / fixed; statement-cycle window; post after window end, N days after transaction, or manual; per-transaction and total rounding; total and shared caps; basic rules auto-attached; expected-reward footer; recomputation of a window when a source entry changes; refund clawback), statement cycles from closing day and due rule with per-cycle override, card payment as a distinct kind linked to a statement, 主帳戶 combined statements and payment allocation, auto-debit simulation, the cutover runbook (final backup → import → `ACCOUNTING_IMPORT_LOCKED=true` → 3-day rollback window). Backlog carried from MOZE but unused by the owner: thresholds, calendar-month and campaign windows, ratio method, per-transaction cap, billing deduction, interest-free-period suggestion.
- **3 `add-accounting-insights`**: net worth with linked broker accounts, reports, receivables view per counterparty.
- **4 `add-accounting-agent-integration`**: Hermes read / write API and activity view, the weekly MOZE changelog digest, recurring and installment generation from the imported `AHPeriod` / `AHInstallment` data, and a monthly e-invoice CSV import (Ministry of Finance carrier export) that matches invoices to entries.

## Open Questions

None blocking. The exact tag delimiter in `AHRecord.tags` and the meaning of `AHAccount.balanceInfo` keys are confirmed in the first implementation task by inspecting aggregate values, never by printing rows.

## Implementation notes

Confirmed by Task 11 on the real backup in a disposable database on 2026-10-02; the importer reports the same values in `confirmed_maps`.

- **Due rule:** `paymentDeadlineType` 0 → `fixed_day`, 1 → `days_after_closing` (MOZE docs, 帳戶設定 › 繳款期限); owner check on three cards in Task 31.
- **Rounding:** `ROUNDING_MAP` follows MOZE's UI order: 0 → keep (保留小數), 1 → round (四捨五入), 2 → floor (無條件捨去), 3 → ceil (無條件進位), 4 → round (the 2-decimal variant). The same map serves `rewardCalculation`, `totalRewardCalculation` and `feeCalculation`. Evidence from per-transaction rewards on the real backup: `rewardCalculation` 0 → keep 1434/1456 and 1 → round 283/286 (floor 108/286, which replaced the earlier 1 → floor). Only codes 0 and 1 are evidence-backed; 2, 3 and 4 are provisional until 2b computes rewards. `totalRewardCalculation` and `feeCalculation` cannot be observed per transaction.
- **Reward percentage:** `AHBonusReward.rewardPercentage` is a fraction (0.01 = 1 %). The importer stores `reward_rule.rate = rewardPercentage × 100` (percent, 4 dp), so `rule_summaries` read e.g. "… 2%". On the real backup the 88 percent rules span 0.3 %–10 % (none below 0.1 %), and there are 7 fixed rules.
- **Refund direction:** `None`. The backup has one `isRefund` record; it carries no `refundID` and no record points to it, so it is imported with `needs_review` `refund_original_missing` and no direction is observable.
- **Tag delimiter:** `None`. No record in the backup has tags.
- **balanceInfo:** not comparable. The best rules, `max_key` and `last_key_before_cache_date`, each matched 14 of 60 accounts (`first_key_after_cache_date` 0); `compared_accounts` 0 of 72.
- **FX conversions:** MOZE's `exchangeRate` is units of `baseCurrencyCode` per 1 `targetCurrencyCode`. The importer uses the rate for base = account currency and target = record currency, and 1/rate for the reverse pair. A rate not > 0, or any other pair, falls back to the cached `fx_api` rate and is reported in `fx_backup_rate_missing` with a reason.
  - On the real backup, 1 record's conversion was stored in the reverse orientation (JPY→TWD) and is read inverted, keeping MOZE's own rate.
  - 1 record had a zero MOZE rate (`fx_backup_rate_missing` 1, reason `zero_rate`). It is `fx_api`-converted, so its account is excluded from the exact-match list and joins the FX-converted accounts.
- **Counts:**
  - Kinds: expense 2534, reward 2338, receivable 618, transfer_in 509, transfer_out 509, fee 315, income 217, balance_adjustment 70, payable 42, interest 36, discount 12, refund 1.
  - Skipped future rows per type: 0 → 65, 2 → 70, 3 → 1, 6 → 204, 14 → 63, 15 → 204.
  - needs_review 33 entries: disabled_record 26, reward_source_missing 6, reward_rule_missing 2, refund_original_missing 1 (reasons can overlap on one entry). Of the 6 missing reward sources, 3 rewards name deleted packages, 2 have no id and 1 names a future record.
  - reward_source_from_package 368: these rewards name an `AHPackage` (a split purchase) and link to the split's primary member, the first by `entry_date`, `entry_time` NULLS FIRST, `seq`, with fee and discount members excluded. They were 368 of the previously flagged 374.
  - groups 716; transfers 509 of 544 (the 35 others are future pairs, 70 skipped type-2 rows); rules 95 of 100 (5 unsupported); attachments 2796; counterparties 10; accounts created 11.
  - FX outliers 0; transfer rate mismatches 1; FX-converted accounts 4 (the contract expected 9).
- **Settlement links (2026-10-03, after archiving):** on the real backup the 308 collections (type 5) carry an empty `relatedID` and only the 240 repayments (type 6) name their payable, so following `relatedID` alone linked no receivable (312 open rows, against 297 of 306 targets MOZE marks settled). Every settlement shares its `AHTarget` with its original (548 of 548). `_link_settlements` therefore keeps `relatedID` first and otherwise links to the imported original of the matching type (3 for a collection, 4 for a repayment) with the same `target`; a target with several originals (4 groups) allocates its settlements FIFO by `date` then identifier to the earliest original still open in the settlement's currency, reviewing `settlement_overflow` when a settlement exceeds what is left (or nothing is open, then linking to the last original). A cross-currency link (10 groups mix currencies) is kept and reviewed as `cross_currency_settlement`; no original gives `settlement_original_missing`. Settlements dated before their original (3) still link. The report adds `settlements_linked: {by_related_id, by_target}`.
- **Balance adjustments:** for `AHRecord.type` 7, `price` is the account balance after the adjustment and `total` is the delta (fee and bonus are 0), so the importer posts `total` as the `balance_adjustment` amount. Verified on the real backup: 7 of 7 adjustments on one account.
- **Previous CSV ledger:** 22 accounts unchanged, 40 moved, 10 had no previous CSV balance. The per-cause split of the 40 (new MOZE records, MOZE FX amounts, skipped future rows) is pending the owner in Task 31.
- **Timing:** conversion 2 s; import including conversion 15 s (dry run 12 s, idempotent re-import 17 s, identical entries, ids and groups).

### Owner balance acceptance (prepared in Task 11, completed by the owner in Task 31)

Environment: the verify server of Task 31, running on the database `accounting_backup_verify` populated by Task 11 (never the live `accounting_db`).

- **Comparison date (`as_of`):** 2026-10-01, the backup's `exported_at` date in Asia/Taipei, written in the checklist header.
- **HomeHub value:** the import report's `moze_part`, equal to `balance` from `GET /accounts?as_of=2026-10-01` on the verify database (checked in 11.7a: 14 checked, 0 mismatches). The UI's balance (today) may differ when postponed entries fall due between Task 11 and Task 31, and it is not the acceptance value.
- **MOZE value:** the balance in the MOZE app's account list.
- **Pass rule:** all 10 accounts below equal (difference 0). The 4 FX-converted accounts (LB, 匯, 華, 去) are listed separately in the checklist with their differences and pass only if the owner accepts each difference. 匯 is there because of the zero-rate record above.
- **Recording:** the owner records pass/fail per account (initials only) in the PR description. Amounts stay in `~/reports/home-hub-2a-verify/checklist.md` (outside the repo), never here.

For each account (initials from the acceptance run, chosen among accounts with no FX-converted rows):

- [ ] C (TWD, card): balance; closing day, due rule and value, credit limit, 主帳戶; one reward entry shows its rule and source.
- [ ] M (TWD, card): same checks.
- [ ] M#2 (TWD, card): same checks; one foreign-currency charge shows MOZE's TWD amount (e.g. ¥5,390 → 1,166).
- [ ] 出 (JPY): balance in JPY; one transfer from a TWD account shows both amounts.
- [ ] 錢, 國, S, L, 旅, 小 (6 accounts, as printed): balance equals MOZE's; the latest five entries match by date, amount and category.
