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
