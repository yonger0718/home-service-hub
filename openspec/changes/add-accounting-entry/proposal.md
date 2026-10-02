## Why

Phase 1 (`rebuild-accounting-moze-ledger`, shipped 2026-10-02) gave HomeHub a ledger that reconciles with MOZE, but it is read-only and fed only by MOZE's CSV export. The owner still records everything in MOZE. To cut over by 2026-10-31 HomeHub must become the place where records are entered, on the phone, with the account settings and record links MOZE has.

Two findings from the design pass (2026-10-02) shape this change:

- **The MOZE backup is a readable database.** `MOZE_4.0.zip` holds `moze.realm`, an unencrypted Realm file that opens with the Realm JS SDK on this host. It contains everything the CSV export drops: explicit transfer pairs (544), MOZE's own converted amounts for foreign-currency records (192), reward-rule attachments on records (1,900) and reward records linked to their source and rule (2,401), counterparties (306), multi-category groups (974), refund and repayment links, invoice numbers, 72 account settings (closing day, due-day rule, limit, 主帳戶, shared limits, auto-debit, foreign-fee %) and 101 reward rules with every field. The backup therefore replaces the CSV as the cutover import source, and seeds account settings and reward rules without screenshots.
- **The owner's real usage narrows the scope.** All reward rules use the statement-cycle window; posting is "after the window ends" (79), "N days after the transaction" (20) or manual (1); methods are percent (93) or fixed (7); 56 rules have a total cap and 10 share a cap; thresholds, calendar-month windows, ratio rules and per-transaction caps are never used.

This change is phase 2a. It delivers data entry, the data model that 2b's reward engine and statements need, the backup importer, and the settings pages. Phase 2b (`add-accounting-card-rules`) delivers the reward engine, statements, card payments and the cutover runbook.

## What Changes

- **Data model** (accounting-service): account settings (group, credit fields, 主帳戶, shared limit, auto-debit, foreign-fee %, include-in-total, icon, colour), `account_group`, `counterparty`, `entry_group` (splits, reward claims), `reward_rule` (stored as data in 2a; evaluated in 2b), `entry_reward_rule`, `preference`; `ledger_entry` gains `posted_date`, group, counterparty FK, settlement / refund / reward links, invoice fields and `moze_id`. Category gets icon, colour, order, hidden flag.
- **MOZE backup importer**: a Node converter (`tools/moze-realm-export`) turns the backup zip into JSON; a Python importer loads it with the same full-replace, advisory-lock, dry-run, rename and `ACCOUNTING_IMPORT_LOCKED` semantics as the CSV importer, replacing all MOZE-sourced rows. The CSV importer stays as a fallback.
- **Write API**: create, update and delete entries (expense, income, receivable, payable, balance adjustment) with fee and discount children and foreign-currency fields; transfers; splits; settlements (收款 / 還款); refunds; CRUD for accounts, groups, categories, projects, counterparties; a preference endpoint; a cross-account entry listing and an entry detail endpoint. MOZE-sourced rows are read-only until `ACCOUNTING_IMPORT_LOCKED = true`.
- **Frontend**: `紀錄` timeline becomes the accounting home; a MOZE-style entry page with a calculator keypad on phones and expression input on desktop; an entry detail view; account settings; an accounting settings page (groups, categories, projects, counterparties, preferences, backup import); account list grouped by account group with 主帳戶 trees; two-pane layout at ≥ 1024 px; a centre "+" in the mobile tab bar; a PWA shortcut to the entry page. Phase 1's phone gaps (original currency only in a tooltip, running balance hidden) are closed.
- **Shell**: the mobile tab bar gains a centre "+" button.

## Capabilities

### New Capabilities
- `accounting-moze-backup-import`: backup zip acceptance, Realm→JSON conversion, record and settings mapping, links, full replace, report.

### Modified Capabilities
- `accounting-ledger`: extended models; write endpoints; cross-account listing; entry detail; edit lock for imported rows.
- `accounting-moze-import`: the CSV full replace now also deletes `moze_backup` rows, so the two importers never coexist in the ledger.
- `frontend-accounting-ledger`: timeline home, entry page, detail, settings, responsive layout, keypad, PWA shortcut; accounts list grouped.
- `frontend-app-shell`: centre "+" in the mobile tab bar.

## Impact

- **Code**: `services/accounting-service/app/{models,schemas,routers,services}/` extended; new Alembic migration; new `tools/moze-realm-export/` (Node, pinned `realm`); `frontend/src/app/components/accounting/` gains timeline, entry, entry-detail, account-settings, settings, keypad and category-picker components; `app.routes.ts`, `navigation.ts`, `mobile-nav`, `manifest.json` updated.
- **APIs**: new `POST/PUT/DELETE /api/accounting/entries…`, `/transfers`, `/settlements`, `/refunds`, `/accounts` (write), `/account-groups`, `/categories`, `/projects`, `/counterparties`, `/preference`, `GET /api/accounting/entries`, `GET /api/accounting/entries/{id}`, `POST /api/accounting/imports/moze-backup`. Existing GET endpoints keep their shape and gain fields.
- **Runtime**: accounting-service runs under pm2 on the host where Node 22 exists; the converter is invoked as a subprocess with a configurable path (`MOZE_REALM_EXPORTER`). The converter needs a writable scratch directory because Realm upgrades the file format in place on a copy.
- **Caddy**: `@hub_spa` gains the new accounting routes.
- **Data**: the next import replaces the CSV-sourced ledger with the backup-sourced one. Manual rows are untouched. Receipt photos in the backup are not imported.
- **Out of scope (2b or later)**: reward computation and posting, statements and card payments, auto-debit, recurring and installment generation, e-invoice carrier sync (a monthly CSV import of e-invoices is planned for phase 4), net worth with broker links, reports, Hermes API, receipt images.
