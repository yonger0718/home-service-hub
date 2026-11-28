## ADDED Requirements

### Requirement: E-invoice preferences

`GET /einvoices/preferences` and `PATCH /einvoices/preferences` (write scope) SHALL read and update:

- `remind_unmatched` (boolean);
- `date_window_days` (integer 0–7);
- `mute_prompt_after` (integer 1–10).

Every PATCH field SHALL be optional. A PATCH SHALL merge only the fields it sends into the stored `einvoice` settings and bump the settings `version`. It SHALL NOT replace the rest of the document. A value out of range SHALL answer 422.

#### Scenario: Read
- **GIVEN** stored settings `{remind_unmatched: true, date_window_days: 3, mute_prompt_after: 3}`
- **WHEN** `GET /einvoices/preferences` is called
- **THEN** the response SHALL carry those three values with `enabled` and `version`

#### Scenario: Partial update
- **GIVEN** stored settings `{remind_unmatched: true, date_window_days: 3, mute_prompt_after: 3}`
- **WHEN** `PATCH /einvoices/preferences {"date_window_days": 5}` is sent with a write-scope token
- **THEN** the stored settings SHALL be `{remind_unmatched: true, date_window_days: 5, mute_prompt_after: 3}` and `version` SHALL increase by one

#### Scenario: Out of range
- **WHEN** `mute_prompt_after: 0` is sent
- **THEN** the endpoint SHALL answer 422 and store nothing

### Requirement: E-invoice link status

`GET /einvoices/preferences` SHALL also return `last_import_at` (the time the newest stored invoice was imported, or null when none is stored) and `auto_linked_count` (the number of invoices whose link was made by the matcher). Both SHALL be read-only: a PATCH that sends either SHALL answer 422.

#### Scenario: Status after an import
- **GIVEN** 40 stored invoices, the newest imported at 2026-11-20T06:40:00Z, 31 of them linked by the matcher
- **WHEN** `GET /einvoices/preferences` is called
- **THEN** it SHALL return `last_import_at: "2026-11-20T06:40:00Z"` and `auto_linked_count: 31`

#### Scenario: Nothing imported
- **GIVEN** no stored invoice
- **THEN** `last_import_at` SHALL be null and `auto_linked_count` SHALL be 0

### Requirement: Link an invoice when its entry is saved

`POST /entries` with an `invoice_number` SHALL, in the same transaction, link the open invoice carrying that number (states `unmatched`, `ambiguous`, `near`, and `dismissed` by a seller mute) whose period holds the entry's date, when the new entry is the only eligible entry stamped with that number in the period. Only this exact rule SHALL apply: no amount or date fallback, and no other invoice SHALL change. Anything else SHALL wait for the daily pass.

#### Scenario: Recorded from an invoice
- **GIVEN** an `unmatched` invoice AB00000001 issued 2026-10-05
- **WHEN** `POST /entries` saves an expense dated 2026-10-06 with `invoice_number: "AB00000001"`
- **THEN** the response's `einvoice` SHALL be AB00000001 with `linked_by: "auto"`, and the invoice SHALL be `linked`

#### Scenario: Closed invoice
- **GIVEN** invoice AB00000001 is `dismissed` by the owner, `void`, or linked to another entry
- **WHEN** an entry is saved with its number
- **THEN** the entry SHALL be created and the invoice SHALL not change

#### Scenario: Outside the period
- **GIVEN** an `unmatched` invoice issued 2026-10-05 (period September–October)
- **WHEN** an entry dated 2026-11-02 is saved with its number
- **THEN** the invoice SHALL stay `unmatched`

#### Scenario: Same number and amount, next period
- **GIVEN** an `unmatched` $85 invoice issued 2026-02-28 (period January–February)
- **WHEN** an $85 entry dated 2026-03-01 is saved with its number
- **THEN** nothing SHALL link at save

#### Scenario: Lock conflict
- **GIVEN** the matcher lock or the invoice row is held, or linking meets a deadlock or a concurrent link
- **THEN** the save SHALL not wait for the lock, the entry SHALL still be created and the invoice SHALL wait for the daily pass

#### Scenario: Edit
- **WHEN** `PUT /entries/{id}` adds an invoice number
- **THEN** nothing SHALL link until the daily pass

### Requirement: Inbox fields for invoices without an entry

The reconciliation inbox SHALL return, for each listed invoice, `seller_dismissed`: the number of that seller's invoices (same tax id) the owner marked 不用記帳. It SHALL be 0 for an invoice without a tax id and for `ambiguous` / `near` invoices. `counts.einvoice_unmatched` SHALL count every `unmatched` invoice, whatever the page limit. It SHALL be 0 while `remind_unmatched` is off, while e-invoices are disabled, and on a read filtered by account. Neither field SHALL add to any count the bell shows.

#### Scenario: Earlier dismissals
- **GIVEN** the owner marked two invoices of seller 00000001 不用記帳 and a third was dismissed by the runbook
- **THEN** an `unmatched` invoice of 00000001 SHALL carry `seller_dismissed: 2`

#### Scenario: Cut page
- **GIVEN** one `ambiguous` and two `unmatched` invoices
- **WHEN** the inbox is read with `limit=1`
- **THEN** only the `ambiguous` invoice SHALL be listed and `counts.einvoice_unmatched` SHALL be 2

### Requirement: Entry invoice recorded amount

The entry detail's `einvoice` SHALL carry `recorded_amount`: what the linked entry or group records in the invoice's terms, by the rules the matcher compares with. For an entry linked by its foreign original amount it SHALL be that amount; otherwise the entry's amount plus its fee and discount children, as a positive figure. For a group it SHALL be the net of its expense and receivable members when they share one currency, and null otherwise.

#### Scenario: Discount on the entry
- **GIVEN** an entry of −130 with a fee child of −10 and a discount child of +20, linked to an invoice of 125
- **THEN** `recorded_amount` SHALL be 120

#### Scenario: Group in one currency
- **GIVEN** a split group of −2,000 and −460 with a −3 fee on the second, linked to an invoice of 2,466
- **WHEN** either member's detail is read
- **THEN** `recorded_amount` SHALL be 2,463

#### Scenario: Mixed-currency group
- **GIVEN** a group with a TWD member and a USD member
- **THEN** `recorded_amount` SHALL be null
