# Tasks: add-accounting-entry

> Outline only. After the spec is approved, the detailed implementation plan (superpowers:writing-plans) replaces this file, following the phase 1 plan's conventions (one worktree, TDD per task, commit per task, implementer models noted per task).

## 1. Setup
- [ ] 1.1 Worktree `~/workspace/home-hub-entry` on `feat/accounting-entry`, venv, frontend deps, baseline test run (110 backend, 141 frontend).
- [ ] 1.2 Add `tools/moze-realm-export/` with pinned `realm`; CI-free smoke script.

## 2. Data model and migration
- [ ] 2.1 Models: `account_group`, `counterparty`, `entry_group`, `reward_rule`, `entry_reward_rule`, `preference`; account, category, project and ledger_entry columns; enum additions.
- [ ] 2.2 Alembic migration with back-fill (`posted_date`, counterparty rows) and downgrade guard.
- [ ] 2.3 Balance computation uses `posted_date ≤ today`; canonical order unchanged.

## 3. Backup converter and importer
- [ ] 3.1 Converter: unzip, copy, open, export listed classes, exit. Tests on a synthetic Realm fixture built by the tool's own test helper.
- [ ] 3.2 Importer mapping: records, children, FX, links, groups, rules, attachments, counterparties, accounts, groups, categories, projects, preferences.
- [ ] 3.3 Full replace, lock, dry run, report, strict balance check against `balanceInfo`; CSV importer updated to delete both sources and preserve settings.
- [ ] 3.4 Real-backup acceptance run against a verify database; confirm due-rule, rounding map, refund direction and tag delimiter; record in the report.

## 4. Write API
- [ ] 4.1 Entry create / update / delete with children, FX, rules, lock.
- [ ] 4.2 Transfers, splits, settlements, refunds, balance adjustments.
- [ ] 4.3 Settings CRUD: accounts, groups, categories, projects, counterparties, preference; order endpoints.
- [ ] 4.4 Read endpoints: cross-account listing, summary, detail, account detail with rules, fx-rate.

## 5. Frontend
- [ ] 5.1 Models, service, routes, navigation (sub-nav, centre "+", FAB), manifest shortcut.
- [ ] 5.2 Timeline home with month summary, filters, day groups, infinite load.
- [ ] 5.3 Category picker, keypad and expression evaluator (shared pure function with tests), FX sheet, fee sheet.
- [ ] 5.4 Entry form (all kinds, transfer mode, split lines, advanced, lock banner, continuous entry, copy).
- [ ] 5.5 Entry detail with links, settle and refund flows, delete confirmation.
- [ ] 5.6 Accounts list grouped with master trees and totals; passbook history with period navigator and inline FX.
- [ ] 5.7 Account settings form; accounting settings page (data lists, display, backup import with dry run).
- [ ] 5.8 Responsive containers: phone, sheet (760–1023), two panes (≥1024); keyboard shortcuts.

## 6. Integration and release
- [ ] 6.1 Caddy `@hub_spa` routes; `docs/accounting-service-improvements.md` marked superseded.
- [ ] 6.2 Verify run: backend on :8010, SPA build served on :4300 over Tailscale; owner checks timeline, entry, detail, accounts on phone and desktop.
- [ ] 6.3 PR, Multica review, CodeRabbit, merge; deploy; real backup import; agentmemory note.
