## MODIFIED Requirements

### Requirement: Transactional full replace

Every import entry point (CLI and REST) SHALL first acquire one shared PostgreSQL advisory lock (a fixed key, shared with the backup importer), held until the import finishes.

If the lock is already held, the second import SHALL be refused immediately:

- REST: HTTP 409;
- CLI: non-zero exit with an "import already running" message.

A refused import SHALL write nothing, not even an `import_run` row. Dry runs SHALL take the same lock.

An import SHALL write the ledger in a single database transaction (the *ledger transaction*) that performs these steps in order:

1. Apply any account renames supplied with the import (see "Account renames and disappearing accounts").
2. Delete every `ledger_entry` and `entry_group` whose `source` is `moze_import` or `moze_backup`, or whose `moze_id` is set, together with their `entry_reward_rule` rows. `reward_rule` rows SHALL NOT be touched by the CSV importer.
3. For every account named in the file:
   - if it exists, update its `opening_balance` and set `is_archived = false`; its settings columns SHALL be preserved; `currency` SHALL be updated only when the account has no remaining entries, and a differing currency on an account with manual entries SHALL fail the import naming the account;
   - otherwise, create it.
4. For every account **not** named in the file that has no remaining entries: set `opening_balance = 0` and `is_archived = true`.
5. Insert all entries parsed from the file with `source = 'moze_import'`, `posted_date = entry_date`, and the row's 對象 resolved to a `counterparty` row (created when missing).
6. Delete categories, projects and counterparties that are no longer used. A category counts as used if any remaining entry references it **or any of its descendants**. A main category is therefore kept while any of its sub-categories is used.

Accounts SHALL NOT be deleted by an import. Entries whose `source` is `manual`, `hermes` or `rule` SHALL NOT be touched. Any error SHALL roll back the entire ledger transaction.

When the configuration flag `ACCOUNTING_IMPORT_LOCKED = true` is set, the importer SHALL refuse to run.

#### Scenario: Parent category kept while only its sub-category is used
- **GIVEN** every expense in category `飲食` references the sub-category `飲食/午餐`, and none references `飲食` directly
- **WHEN** the cleanup step runs
- **THEN** both `飲食` and `飲食/午餐` SHALL remain

#### Scenario: Unused branch is removed
- **GIVEN** a previous import created `娛樂/電影`, and the new file has no entry in `娛樂` or any of its sub-categories
- **WHEN** imported
- **THEN** both `娛樂/電影` and `娛樂` SHALL be deleted

#### Scenario: Re-import reflects edits made in MOZE
- **GIVEN** a previous import contained an expense of `-120` that was later changed to `-150` in MOZE
- **WHEN** the new export is imported
- **THEN** the ledger SHALL contain the `-150` expense and not the `-120` one

#### Scenario: Failure leaves the previous import intact
- **GIVEN** a successful earlier import
- **WHEN** a new import fails while parsing row 4,000
- **THEN** every entry from the earlier import SHALL still be present and unchanged

#### Scenario: Concurrent imports are serialised by refusal
- **GIVEN** a CLI import is running
- **WHEN** a REST import is requested before it finishes
- **THEN** the REST request SHALL receive HTTP 409
- **AND** the ledger SHALL afterwards contain exactly the data of the CLI import's file

#### Scenario: Import is locked after cutover
- **GIVEN** `ACCOUNTING_IMPORT_LOCKED = true`
- **WHEN** an import is requested by any path (CLI or REST)
- **THEN** it SHALL be refused with a message that import is locked, and nothing SHALL change

#### Scenario: CSV import replaces a backup import
- **GIVEN** the ledger was last filled by a backup import, plus 2 manual entries, one attached to an imported rule
- **WHEN** a CSV import runs
- **THEN** every `moze_backup` entry and group SHALL be gone, the CSV entries SHALL be present, the 2 manual entries SHALL be unchanged, and the rule and its attachment SHALL still exist

#### Scenario: Account settings survive a CSV re-import
- **GIVEN** an account with `is_credit = true` and `closing_day = 15`
- **WHEN** a CSV import updates that account's opening balance
- **THEN** `is_credit` and `closing_day` SHALL be unchanged
