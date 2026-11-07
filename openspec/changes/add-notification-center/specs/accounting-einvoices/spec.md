## ADDED Requirements

### Requirement: E-invoice preferences

`GET /einvoices/preferences` and `PATCH /einvoices/preferences` (write scope) SHALL read and update:

- `remind_unmatched` (boolean);
- `date_window_days` (integer 0–7);
- `mute_prompt_after` (integer 1–10).

Every PATCH field SHALL be optional. A PATCH SHALL merge only the fields it sends into the stored `einvoice` settings and bump the settings `version`. It SHALL NOT replace the rest of the document. A value out of range SHALL answer 422.

#### Scenario: Partial update
- **GIVEN** stored settings `{remind_unmatched: true, date_window_days: 3, mute_prompt_after: 3}`
- **WHEN** `PATCH /einvoices/preferences {"date_window_days": 5}` is sent with a write-scope token
- **THEN** the stored settings SHALL be `{remind_unmatched: true, date_window_days: 5, mute_prompt_after: 3}` and `version` SHALL increase by one

#### Scenario: Out of range
- **WHEN** `mute_prompt_after: 0` is sent
- **THEN** the endpoint SHALL answer 422 and store nothing
