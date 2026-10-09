## ADDED Requirements

### Requirement: Token scopes

`ACCOUNTING_TOKEN_SCOPES` SHALL be a `;`-separated list of `label=scope,scope` items over the scopes `read`, `propose`, `write`, `admin`, `enqueue`, `ingest` and `legacy`; an unknown scope name, an empty scope list, a duplicate label or a malformed item SHALL stop the service at startup. When scopes are set, startup SHALL also refuse: a bare (unlabelled) token in `ACCOUNTING_API_TOKENS`, a duplicate label, a duplicate token value, a token label without a scope entry, a scope entry without a token, the feature enabled with no tokens at all, and a label that holds `legacy` together with `ingest` or `enqueue`. When scopes are unset, startup SHALL refuse `ACCOUNTING_RECONCILIATION_ENABLED=true`, and SHALL refuse any token labelled `hermes`, `worker` or a label named in `ACCOUNTING_RESTRICTED_LABELS`. With scopes unset and no restricted label, behaviour SHALL be unchanged (compatibility mode, every token passes every check).

`require(*scopes)` SHALL pass when scopes are unconfigured, or the caller's label holds `legacy`, or holds at least one of the named scopes; otherwise it SHALL answer HTTP 403 with message `scope`. Every router SHALL carry a scope dependency: legacy routers use `method_scope()` (`GET`/`HEAD` need `read`, every other method needs `write`), the settings routers need `admin` for writes (including `PUT /settings/reconciliation`), and the statements routes name their own scope. On the legacy and settings routers the scope checks SHALL apply whether or not the reconciliation feature is enabled; on `/statements/*` and the statement-read routes the router-level feature gate answers 404 before any scope check. Error envelope: 401/403/404/409 bodies are `{"code": <status>, "message": "<text>", "trace_id": …}`; 422 keeps FastAPI's `{"detail": [{"loc": […, "<field>"], …}]}` shape, so "naming a field" below means that field is the last `loc` element.

#### Scenario: Complete scope map is accepted
- **GIVEN** `ACCOUNTING_API_TOKENS=spa:spa-token,hermes:hermes-token,worker:worker-token,ops:ops-token`
- **AND** `ACCOUNTING_TOKEN_SCOPES=spa=legacy; hermes=read,propose; worker=ingest; ops=legacy,admin`
- **AND** `ACCOUNTING_RECONCILIATION_ENABLED=true`
- **WHEN** the service starts
- **THEN** startup validation SHALL pass

#### Scenario: Feature on without scopes is refused
- **GIVEN** `ACCOUNTING_RECONCILIATION_ENABLED=true` and `ACCOUNTING_TOKEN_SCOPES` unset
- **WHEN** the service starts
- **THEN** it SHALL refuse to start with an error naming `ACCOUNTING_TOKEN_SCOPES`

#### Scenario: Restricted label without scopes is refused
- **GIVEN** `ACCOUNTING_API_TOKENS` contains the label `hermes` and `ACCOUNTING_TOKEN_SCOPES` is unset
- **WHEN** the service starts
- **THEN** it SHALL refuse to start with an error naming the restricted label

#### Scenario: Unsafe or incomplete scope maps are refused
- **GIVEN** tokens `spa`, `hermes`, `worker`, `ops`
- **WHEN** `ACCOUNTING_TOKEN_SCOPES` is `spa=legacy; hermes=read,propose` (labels missing), or `spa=legacy,ingest; hermes=read; worker=ingest`, or `spa=legacy; hermes=read,enqueue,legacy; worker=ingest`, or adds `ghost=read` for a label with no token, or names `spa=nope`
- **THEN** startup SHALL refuse each configuration

#### Scenario: Bare tokens and duplicate token values are refused under scopes
- **GIVEN** `ACCOUNTING_TOKEN_SCOPES=spa=legacy`
- **WHEN** `ACCOUNTING_API_TOKENS=spa:a,bare-token` or `spa:x,hermes:x`
- **THEN** startup SHALL refuse with an error naming the bare token or the duplicate token value

#### Scenario: Restricted token is refused on every legacy mutation route
- **GIVEN** scopes `spa=legacy; hermes=read,propose; worker=ingest` and the feature on
- **WHEN** the `hermes` token calls any mutation route of the full route table that is not a `propose` route (none exist in R1a) (including `POST /entries`, `PUT /accounts/{id}`, `POST /transfers`, `POST /imports/moze` and the settings writes)
- **THEN** each response SHALL be HTTP 403 with message `scope`

#### Scenario: Ingest token cannot read the ledger
- **GIVEN** the same scope map
- **WHEN** the `worker` token calls `GET /entries`
- **THEN** the response SHALL be HTTP 403

#### Scenario: Legacy token keeps working
- **GIVEN** the same scope map
- **WHEN** the `spa` token calls `POST /entries` for a valid expense of `10`
- **THEN** the response SHALL be HTTP 201

#### Scenario: Settings mutation needs admin
- **GIVEN** `hermes=read,propose` and `ops=legacy,admin`
- **WHEN** `hermes` calls `PUT /settings/reconciliation` with `{"account_map": {}}`
- **THEN** the response SHALL be HTTP 403
- **AND** the same call by the `admin` holder SHALL succeed

#### Scenario: Feature off keeps restrictions
- **GIVEN** scopes configured and `ACCOUNTING_RECONCILIATION_ENABLED=false`
- **WHEN** the `hermes` token calls `POST /entries`
- **THEN** the response SHALL still be HTTP 403

### Requirement: Reconciliation feature gate

Every route of the statements router SHALL sit behind a feature dependency: while `ACCOUNTING_RECONCILIATION_ENABLED` is not `true` (`1`/`true`/`yes`, case-insensitive), each of those routes SHALL answer HTTP 404 with message `reconciliation disabled`. The variable SHALL be read per request.

#### Scenario: Routes hidden while the feature is off
- **GIVEN** `ACCOUNTING_RECONCILIATION_ENABLED` unset
- **WHEN** an authorised caller requests `POST /statements/ingest/run`
- **THEN** the response SHALL be HTTP 404

#### Scenario: Routes appear when the feature is on
- **GIVEN** `ACCOUNTING_RECONCILIATION_ENABLED=true`
- **WHEN** the same caller requests it again
- **THEN** the route SHALL be served

### Requirement: Ingest runs and leases

`POST /statements/ingest/run` (scope `enqueue`) SHALL insert a `queued` run with `trigger = 'enqueue'` and `mode = 'live'`, or, when a `queued` run or a `claimed`/`running` run whose lease has not expired exists, SHALL return that run with the request appended to `summary.coalesced` (an `expired` run or a dead lease SHALL never be a coalescing target); the check SHALL be serialised by an advisory lock. A worker SHALL claim a run with one atomic conditional update: the run moves to `claimed`, receives a fresh `lease_token`, `lease_expires_at = now + 30 minutes` and `attempt = attempt + 1`. A claim on a run whose lease is still live SHALL fail with HTTP 409 and change nothing. A claim on a `claimed` or `running` run whose lease has expired SHALL first expire it by a conditional update, so that one expiry bumps `attempt` once and a concurrent fresh claim is never overwritten. Every submission (`renew`, `finish`, files, sources, `mark-removed`, revisions) SHALL carry `run_id` and `lease_token`; it SHALL be refused with HTTP 409 with message `lease` when the run is not `claimed`/`running`, the token differs (compared in constant time), the lease is expired, or the caller's label is not the label that claimed the run. The first valid submission moves `claimed` to `running`; `renew` SHALL extend the lease by 30 minutes; `finish` SHALL accept only `done` or `failed` and merge the worker's summary over the stored one.

#### Scenario: Enqueue coalesces
- **GIVEN** a queued run exists
- **WHEN** a token holding `enqueue` (here `hermes=read,propose,enqueue`) enqueues twice
- **THEN** both calls SHALL return the same run id
- **AND** `summary.coalesced[0].principal` SHALL be `hermes`

#### Scenario: Enqueue never coalesces into a dead lease
- **GIVEN** a claimed run whose lease has been moved into the past
- **WHEN** a token holding `enqueue` enqueues
- **THEN** a new `queued` run SHALL be created with a different run id

#### Scenario: Claim is atomic and bumps the attempt after expiry
- **GIVEN** run 1 claimed by `worker` with attempt 1
- **WHEN** a second claim arrives while the lease is live
- **THEN** it SHALL fail with 409 and attempt SHALL stay 1
- **AND WHEN** the lease is moved one hour into the past and `worker` claims again
- **THEN** attempt SHALL be 2 and the lease token SHALL differ from the first

#### Scenario: Wrong or expired lease is refused
- **GIVEN** a claimed run
- **WHEN** a submission carries the token `wrong-token`, or the right token after the lease expired, or the right token from a different label
- **THEN** each response SHALL be HTTP 409 with message `lease`

#### Scenario: Live lease is not expired by another claim
- **GIVEN** run 1 claimed by `worker` with a live lease
- **WHEN** label `other` tries to claim it
- **THEN** the claim SHALL fail with 409 and the stored status, attempt 1 and token SHALL be unchanged

#### Scenario: Finished run refuses further submissions
- **GIVEN** a run finished as `done` with summary `{"files": 1}`
- **WHEN** the same token submits again
- **THEN** the response SHALL be HTTP 409 with message `lease`

### Requirement: Statement files and sources

A statement file SHALL be identified by the lowercase `sha256` of its bytes: registering the same sha256 again (in any letter case, concurrently or not) SHALL insert nothing and return the existing row. A statement source SHALL be identified by `(drive_file_id, drive_md5)` and SHALL name the file it points to; registering an existing identity SHALL update `last_seen_at`, clear `removed_at`, and, when `drive_path` differs, append `{path, until}` of the old path to `path_history` before storing the new one. A new identity SHALL supersede older sources of the same `drive_file_id` (`superseded_by_source_id`). A source whose identity already belongs to another file SHALL be refused with HTTP 409. `POST /statements/sources/mark-removed` SHALL set `removed_at` only on sources whose `drive_file_id` is absent from the posted `seen_drive_file_ids`, SHALL be sent only after a complete listing, and an empty seen list SHALL be refused with HTTP 422 unless `allow_empty` is true.

#### Scenario: File registration is idempotent by sha256
- **WHEN** a file with sha256 `ab`×32 is registered, then again with the upper-case spelling
- **THEN** both calls SHALL return the same file id, status `new`, and one row SHALL exist

#### Scenario: Path change keeps history
- **GIVEN** a source with `drive_file_id = drive-1`, `drive_md5 = cd`×16 and path `信用卡/國泰世華/2026-09.pdf`
- **WHEN** the same identity is registered with a different `drive_path`
- **THEN** the old path SHALL be appended to `path_history` and `drive_path` SHALL be the new one

#### Scenario: New bytes supersede the old source
- **GIVEN** a source `(drive-1, md5-A)`
- **WHEN** `(drive-1, md5-B)` is registered
- **THEN** the md5-A source SHALL have `superseded_by_source_id` set to the new source

#### Scenario: Mark-removed refuses an empty seen set
- **WHEN** `mark-removed` is posted with `seen_drive_file_ids = []` and no `allow_empty`
- **THEN** the response SHALL be HTTP 422 naming `seen_drive_file_ids`
- **AND** with `allow_empty = true` the response SHALL be `{"removed": 0}` for an empty source table

### Requirement: Flow sign derivation

The server SHALL derive each line's ledger `flow_amount` from the printed amount, the statement `kind` and the `line_kind`; the worker's own sign SHALL NOT be trusted. For a `card` statement a charge (`purchase`, `fee`, `interest`, `installment`, `balance_adjustment`) printed positive SHALL have a negative flow and a credit (`payment`, `refund`, `reward`) printed negative SHALL have a positive flow: `flow = −printed`. For a `bank` statement the flow SHALL equal the printed amount, credit kinds (`deposit`, `interest`, `transfer_in`, `refund`, `reward`) positive and debit kinds (`withdrawal`, `fee`, `transfer_out`, `purchase`, `payment`) negative. A line whose sign contradicts its kind, or a zero amount on a known kind, SHALL be rejected with HTTP 422 on field `lines` and nothing SHALL be stored. Subtotal lines SHALL be dropped. Amounts SHALL be rounded half-up to 0.0001.

#### Scenario: Card charge is negative and payment positive
- **GIVEN** a card statement with a `purchase` of `580` and a `payment` of `-500`
- **WHEN** the revision is submitted
- **THEN** the stored flows SHALL be `-580.0000` and `500.0000`

#### Scenario: Sign-inconsistent line is rejected
- **WHEN** a card `purchase` is printed as `-580`
- **THEN** the response SHALL be HTTP 422 naming `lines`

#### Scenario: Rounding is half-up
- **WHEN** a card `purchase` is printed as `1.00005`
- **THEN** its flow SHALL be `-1.0001`

### Requirement: Line keys

Each line SHALL carry a `logical_key` `<posted_date>|<flow>|<occurrence>`, where `occurrence` counts the line's position among earlier lines sharing the same posted date and flow in print order, and a `canonical_key`, the SHA-256 hex of `posted_date|txn_date|flow|foreign_amount|foreign_currency|line_kind|installment_seq|installment_total|merchant_norm`. `merchant_norm` is the normalised merchant text, so whitespace and letter-case differences in `merchant_raw` SHALL NOT change the canonical key; a different merchant, date or amount SHALL.

#### Scenario: Whitespace change keeps the canonical key
- **GIVEN** a stored line with merchant `全聯` on 2026-09-03 for `580`
- **WHEN** a re-parse prints the merchant as `全聯 `
- **THEN** the new line SHALL have the same `canonical_key` and the same `logical_key` `2026-09-03|-580.0000|0`

#### Scenario: Identical twins get distinct logical keys
- **WHEN** two lines are printed on 2026-09-03 for `100`
- **THEN** their logical keys SHALL end in `|0` and `|1`

### Requirement: Guardrails

For every revision the server SHALL compute guardrails. A `card` statement SHALL satisfy `statement_total == opening_balance − Σflow`; a missing opening balance SHALL be assumed zero and flagged `opening_assumed_zero`. A `bank` statement SHALL carry an opening balance (else `opening_present` fails) and SHALL satisfy `closing == opening + Σflow`. The revision currency SHALL equal the account currency; the period SHALL be 0 to 62 days; every posted date SHALL fall within 5 days of the period; an installment sequence SHALL be between 1 and its total; the revision SHALL have at most 2000 lines. A failing guardrail SHALL NOT reject the submission: the revision and its lines SHALL be stored with `guardrail_ok = false` and the per-check results in `guardrail`.

#### Scenario: Card equation holds
- **GIVEN** a card statement with a `purchase` of `580` and total `580`, opening absent
- **WHEN** it is submitted
- **THEN** `guardrail_ok` SHALL be true and `opening_assumed_zero` SHALL be recorded

#### Scenario: Wrong total is stored but flagged
- **GIVEN** the same line with total `581`
- **WHEN** it is submitted
- **THEN** the revision and its line SHALL be stored with `guardrail_ok = false` and `checks.equation = false`

#### Scenario: Bank statement without opening fails
- **GIVEN** a `bank` statement with no `opening_balance`
- **WHEN** it is submitted
- **THEN** `checks.opening_present` SHALL be false and `guardrail_ok` SHALL be false

#### Scenario: Out-of-range inputs fail their checks
- **WHEN** a revision has a period of 63 days, or a line posted 6 days after `period_end`, or `installment_seq = 4` with `installment_total = 3`, or 2001 lines, or a currency other than the account's
- **THEN** the matching check (`period_length`, `dates_in_window`, `installments`, `line_count`, `currency`) SHALL be false and `guardrail_ok` SHALL be false

### Requirement: Statement identity, revisions and current revision

A statement SHALL be identified by `(account_id, currency, period_end)`; the first submission creates it (concurrent creators SHALL NOT raise), later ones add revisions to it under a row lock. A revision SHALL be immutable and numbered from 1 within its statement. An unknown account SHALL answer HTTP 404 and `period_start` after `period_end` HTTP 422 on `period_start`. The statement's `kind` SHALL match the account (`card` iff `is_credit`), else HTTP 422 on `kind`; a file-backed revision SHALL come from a folder `<root>/<first two folders>` that `account_map` maps to the same account, else HTTP 422 whose `detail[0].loc` ends with `account_id` and whose `msg` is `folder maps elsewhere`.

A revision SHALL become the statement's current revision when any of these holds: it creates the statement (even if its guardrails failed); it is a correction, i.e. the current revision failed its guardrails and this one passes, and the statement is not reconciled; or it passes its guardrails, the statement is not reconciled, the twin counts are unchanged and no compared header field (`period_start`, `closing_date`, `due_date`, `opening_balance`, `statement_total`, `minimum_payment`) changed. Otherwise the revision SHALL be stored and the current revision SHALL stay. A correction SHALL also supersede the statement's still-open `parse_review` cases (status `superseded`, version incremented). A revision submitted for a statement whose `status` is `reconciled` SHALL be stored with `conflict = true`, SHALL set `conflict_open`, SHALL open one `statement_conflict` case (live mode) and SHALL NOT change the current revision.

#### Scenario: First revision becomes current even when it fails
- **GIVEN** a live card account
- **WHEN** its first revision for 2026-09 is submitted with a wrong total of `999`
- **THEN** the statement's current revision SHALL be that revision and `guardrail_ok` SHALL be false

#### Scenario: Failed re-parse never replaces a good current revision
- **GIVEN** a statement whose current revision passed with total `580`
- **WHEN** a revision with total `581` is submitted
- **THEN** it SHALL be stored with `guardrail_ok = false`
- **AND** the current revision SHALL still be the first one
- **AND** exactly one `parse_review` case SHALL exist and `open_case_count` SHALL be 1

#### Scenario: Corrected parse replaces a failed current revision
- **GIVEN** a statement whose current revision failed with total `999` and has an open `parse_review`
- **WHEN** a revision of the same lines with total `580` is submitted
- **THEN** it SHALL become current and the statement total SHALL be `580`
- **AND** the `parse_review` case SHALL be `superseded` at version 2, `open_case_count` SHALL be 0, and no new case SHALL be opened

#### Scenario: Reconciled statement opens a conflict
- **GIVEN** a live statement with status `reconciled` and a current revision with one line of `580`
- **WHEN** a revision with a line of `581` is submitted
- **THEN** the revision SHALL have `conflict = true`, `conflict_open` SHALL be true and the current revision SHALL be unchanged
- **AND** the only case SHALL be a `statement_conflict`

#### Scenario: Kind and folder must match the account
- **GIVEN** a bank account and a card revision, or a file whose folder maps to account N+1
- **WHEN** the revision is submitted for account N
- **THEN** the response SHALL be 422 on `kind`, respectively 422 whose `detail[0].loc` ends with `account_id` and whose `msg` is `folder maps elsewhere`

### Requirement: Events and lineage

Each printed transaction SHALL have one `statement_event` that persists across revisions. When a revision is submitted, its lines SHALL be paired to the current revision's lines by `logical_key` in print order and classified: `identical` (same canonical key), `normalised` (same kind fields and same merchant tokens), `changed` (any other difference) or `unpaired` (no partner on either side). An `identical` or `normalised` pair SHALL transfer the old event to the new line; a `changed` pair and an unpaired old line SHALL retire the old event (status `retired`, `current_line_id` NULL), and a `changed` new line, like an unpaired new line, SHALL get a new event; the `changed` lineage row records the old and new line with `transferred = false`. Quarantine of a `changed`/unpaired event that has applied effects or active coverage is R1b. Events move (current line, retirement) only when the revision becomes current; a revision that does not become current SHALL record lineage but move nothing, except that its unpaired and `changed` new lines still create events with status `retired`, whose `current_line_id` stays NULL. A change in the number of identical twins (lines sharing posted date and flow) between the current and the new revision SHALL prevent the new revision from becoming current and, in live mode, open a `parse_review`. The first revision of a statement SHALL write no lineage rows and open no case for twins.

#### Scenario: Whitespace-only re-parse pairs identical
- **GIVEN** a stored line `全聯` on 2026-09-03 for `580`
- **WHEN** a re-parse prints `全聯 `
- **THEN** the lineage counts SHALL be `identical = 1` with no `changed`
- **AND** the event SHALL be unchanged with its current line moved to the new line and the new revision SHALL be current

#### Scenario: Merchant change pairs changed and retires the old event
- **GIVEN** a stored line `PAYPAL *Spotify`
- **WHEN** a re-parse prints `PAYPAL *Netflix` on the same date and amount
- **THEN** `changed = 1` and two events SHALL exist: the old one `retired` with `current_line_id` NULL, and a new `live` one whose first and current line is the new line
- **AND** one `changed` lineage row SHALL record the old and new line with `transferred = false`

#### Scenario: Moved date retires the old event
- **GIVEN** a stored line on 2026-09-03 for `580`
- **WHEN** the re-parse prints it on 2026-09-04
- **THEN** the counts SHALL be `unpaired_old = 1`, `new = 1`, the old event SHALL be `retired` with `current_line_id` NULL, and two `unpaired` lineage rows SHALL exist

#### Scenario: Reordered identical twins stay identical
- **GIVEN** two identical lines on 2026-09-03 for `100`
- **WHEN** a re-parse prints them in either order
- **THEN** both pairs SHALL be `identical`

#### Scenario: Twin count change blocks the current revision
- **GIVEN** a current revision with two identical lines of `100`
- **WHEN** a revision with one such line is submitted
- **THEN** `guardrail.twins_changed` SHALL be true, the current revision SHALL stay and, in live mode, a `parse_review` SHALL open

#### Scenario: First revision writes no lineage
- **WHEN** a statement's first revision is submitted with two identical twins
- **THEN** no `line_lineage` row and no case SHALL exist and the revision SHALL be current

### Requirement: Modes

A statement SHALL be `live` when its account's `statement_live_from` is set and the revision's `period_end` is on or after it, otherwise `historical`; an existing statement keeps the mode it was created with. A historical statement SHALL store revisions, events and lineage but SHALL NOT open `parse_review` or `statement_conflict` cases; its diagnostics SHALL stay in `revision.guardrail`. A live statement SHALL open `parse_review` for a failed guardrail, a twin-count change or a header change on a non-current-replacing revision, and `statement_conflict` for a revision against a reconciled statement.

#### Scenario: Null live-from is historical
- **GIVEN** a card account with `statement_live_from` NULL
- **WHEN** a revision with a wrong total is submitted
- **THEN** the statement mode SHALL be `historical`, `guardrail_ok` SHALL be false and no case SHALL be opened

#### Scenario: Live statement opens a parse review
- **GIVEN** a card account with `statement_live_from = 2026-09-01`
- **WHEN** a first revision for period end 2026-09-30 fails its guardrails
- **THEN** the mode SHALL be `live` and exactly one `parse_review` case SHALL exist

### Requirement: Dirty events

`ledger_entry` and `entry_group` SHALL carry row-level `AFTER INSERT/UPDATE/DELETE` triggers and `account` an `AFTER UPDATE` trigger; each appends to `coverage_dirty` and never writes to its source table. An entry row SHALL record `op`, `old_account_id`/`new_account_id`, `old_date`/`new_date` (posted dates) and the full `old_row`/`new_row` as JSON (the missing side NULL for insert and delete). An update that changes nothing (ignoring `updated_at` on entries) SHALL record nothing. An account update SHALL record only when `opening_balance`, `currency`, `combined_account_id`, `closing_day`, `due_rule`, `due_value`, `is_credit` or `is_archived` changed. Each row SHALL carry `action_id` from the transaction-local setting `app.reconciliation_action_id`, NULL when unset. Every trigger SHALL record nothing unless `reconciliation_settings.data.dirty_enabled` is true (an absent settings row or key means off), checked with one primary-key lookup per changed row.

#### Scenario: Dirty events are off until enabled
- **GIVEN** no settings row, or `dirty_enabled` false
- **WHEN** an entry is moved to another account
- **THEN** no `coverage_dirty` row SHALL be written
- **AND WHEN** `PUT /settings/reconciliation` stores `{"dirty_enabled": true}`
- **THEN** the next change SHALL be recorded

#### Scenario: Cross-account move records both sides
- **GIVEN** an entry of `-10` on account A
- **WHEN** it is moved to account B
- **THEN** one `coverage_dirty` row SHALL exist with `kind = entry`, `op = update`, `old_account_id = A`, `new_account_id = B`
- **AND** `old_row.account_id` SHALL be A and `new_row.account_id` SHALL be B

#### Scenario: No-op update is ignored
- **WHEN** `UPDATE ledger_entry SET name = name` runs
- **THEN** no `coverage_dirty` row SHALL be written

#### Scenario: Action id comes from the transaction setting
- **GIVEN** `SET LOCAL app.reconciliation_action_id = '42'`
- **WHEN** the entry's amount changes in that transaction
- **THEN** the row's `action_id` SHALL be 42
- **AND** without the setting `action_id` SHALL be NULL

#### Scenario: Account change classification
- **WHEN** an account is archived, or its `opening_balance` changes
- **THEN** a row SHALL be recorded
- **AND WHEN** only its name changes
- **THEN** no row SHALL be recorded

#### Scenario: Insert and delete record one side
- **WHEN** an entry is inserted, then deleted
- **THEN** the insert row SHALL have only `new_row` and the delete row only `old_row`

### Requirement: Ingest API

The statements router SHALL expose these routes, all behind the feature gate, each with the scope listed (a `legacy` holder passes every scope check):

| Route | Scope | Success status |
|---|---|---|
| `POST /statements/ingest/run` | `enqueue` | 202 |
| `POST /statements/ingest-runs` (worker creates a `timer`/`owner_cli` run and claims it in one transaction) | `ingest` | 201 |
| `GET /statements/ingest-runs`, `POST …/{run_id}/claim`, `…/renew`, `…/finish` | `ingest` | 200 |
| `POST /statements/files` | `ingest` | 201 |
| `PATCH /statements/files/{file_id}`, `GET /statements/files` | `ingest` | 200 |
| `POST /statements/sources` | `ingest` | 201 (also when the identity already exists) |
| `POST /statements/sources/mark-removed` | `ingest` | 200 |
| `POST /statements/revisions` | `ingest` | 201 |
| `GET /accounts/{id}/statements`, `GET /accounts/{id}/statements/{statement_id}` | `read` | 200 |
| `GET /settings/reconciliation` | `read` | 200 |
| `PUT /settings/reconciliation` | `admin` | 200 |

An `enqueue`-only token SHALL NOT be able to submit revisions, and an `ingest`-only token SHALL NOT read ledger or statement data. A statement detail requested through the wrong account id SHALL answer 404. `PUT /settings/reconciliation` SHALL accept `account_map` keys of the form `<root>/<folder>/<subfolder>` mapping to an account id and a boolean `dirty_enabled` (default false), return the stored settings with an incremented `version`, and refuse malformed keys or values with HTTP 422 on `account_map`. A revision submission SHALL answer 201 with the statement id, revision, lineage counts and opened case ids.

#### Scenario: Enqueue returns 202
- **GIVEN** the feature on and a token holding only `enqueue`
- **WHEN** it calls `POST /statements/ingest/run`
- **THEN** the response SHALL be HTTP 202

#### Scenario: Enqueue token cannot submit
- **WHEN** the same token calls `POST /statements/revisions`
- **THEN** the response SHALL be HTTP 403 with message `scope`

#### Scenario: Hermes cannot submit revisions
- **WHEN** the `hermes` token (`read,propose`) calls `POST /statements/revisions`
- **THEN** the response SHALL be HTTP 403

#### Scenario: Worker cannot read statements
- **WHEN** the `worker` token calls `GET /accounts/{id}/statements`
- **THEN** the response SHALL be HTTP 403
- **AND** the `hermes` token SHALL receive `[]` for an account without statements

#### Scenario: Worker submits and owner reads
- **GIVEN** a claimed run and a live card account
- **WHEN** the worker posts a revision and the owner reads the statement through its own account
- **THEN** the post SHALL answer 201 and the read 200
- **AND** reading it through a different account id SHALL answer 404

#### Scenario: Folder map is enforced
- **GIVEN** a file whose source folder `mail/信用卡/國泰世華` maps to another account
- **WHEN** a file-backed revision is posted for the card
- **THEN** the response SHALL be HTTP 422 naming `account_id`

#### Scenario: Settings validate the account map
- **WHEN** `PUT /settings/reconciliation` carries `{"account_map": {"信用卡": 1}}` or a non-integer value
- **THEN** the response SHALL be HTTP 422 naming `account_map`
- **AND** a valid `{"mail/信用卡/國泰世華": <card id>}` SHALL be stored with `version` one higher than before
