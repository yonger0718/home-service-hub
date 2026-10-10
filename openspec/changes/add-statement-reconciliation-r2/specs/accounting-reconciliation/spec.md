## ADDED Requirements

### Requirement: Drive acquisition

The worker SHALL list both roots (`<root>/銀行` and `<root>/手動下載`) with `rclone lsjson`, recursively and including the Drive ID and the MD5 of every PDF (extension compared case-insensitively, MD5 lower-cased). A listing in which either root fails, or any row lacks an ID or MD5, SHALL be a listing error: nothing is downloaded, nothing is removed and the run reports it. A file SHALL be downloaded by its Drive ID, never by path. The bytes SHALL be staged, verified against the listed MD5 and size, then published atomically to `by-sha/<sha256>.pdf` in a private (0700) inbox; a mismatch (the file changed between listing and download) SHALL be re-listed once and then skipped as `transient`, never published under the wrong identity. A listed PDF larger than 20 MB SHALL be skipped before download and counted `too_large`. Every complete listing SHALL re-register every listed source through `POST /statements/sources` (idempotent upsert; path history; `removed_at` reset), whatever the local download cache says; a source SHALL be marked removed only after a complete listing in which it is absent.

#### Scenario: Both roots are listed
- **GIVEN** PDFs in the mail root (including an upper-case `.PDF` extension and an upper-case MD5) and in the manual root
- **WHEN** the worker lists
- **THEN** every PDF of both roots SHALL be returned with a lower-case MD5, and the `lsjson` call SHALL carry the recursive, Drive-ID and hash flags

#### Scenario: One failing root fails the whole listing
- **GIVEN** the mail root lists fine and the manual root exits non-zero
- **WHEN** the worker lists
- **THEN** a listing error SHALL be raised and no partial listing SHALL be used

#### Scenario: A replaced file is never published under the wrong identity
- **GIVEN** a Drive file whose downloaded bytes do not match the listed MD5
- **WHEN** the worker processes it
- **THEN** nothing SHALL be published, the file SHALL be re-listed once, and the outcome SHALL be `transient`

#### Scenario: A rename updates path history without a download
- **GIVEN** a file already downloaded and registered
- **WHEN** it is renamed in Drive and the next complete listing runs
- **THEN** its source SHALL be re-registered with the new path and no download SHALL happen

### Requirement: Unlock and masking

The worker SHALL try the folder's password candidates in file order (aliases resolved, tokens such as `$ID`, `$BIRTH8` expanded from the identity block) and SHALL NOT persist the index of the winning candidate. A file no candidate opens SHALL be `failed/password` until the password file's `credential_version` changes. Password values, the ID number, the birth date, holder names, decrypted bytes and unmasked text SHALL never be logged, written outside the private state directories, sent to the API, the object store or the parser; error text about passwords says only `password`. Before any text leaves the worker it SHALL be masked, in text and in every table cell: card and account numbers keep the last 4 digits; ID numbers; digit runs of at least 6 contiguous digits, or at least 2 groups of at least 3 digits separated by one space or dash with at least 8 digits in total; the birth date in `YYYYMMDD`, `YYYY/MM/DD`, `YYYY-MM-DD` and `DDMMYY`; holder names; e-mail addresses; phone numbers. Dates, comma amounts, short numbers and an exactly 8-digit token that is a valid `YYYYMMDD` date in 1990..2039 SHALL stay intact. Secrets adjacent to CJK characters SHALL be masked. A PDF over 20 MB or 40 pages, an extraction over 60 s, a first page with fewer than 200 characters (`no_text_layer`) or masked text over 400 KB (`too_large`) SHALL not be parsed.

#### Scenario: Canary masks every class
- **GIVEN** a synthetic text containing an ID number, birth date, holder name, card number, account number, phone and e-mail, some directly adjacent to CJK characters
- **WHEN** the text is masked
- **THEN** none of the synthetic values SHALL remain and the replacement tokens SHALL be the documented ones

#### Scenario: Dates and amounts survive
- **GIVEN** the text `30 2026`, `000 2026`, `2026/09/30`, `1,234,567.00` and `20260930`
- **WHEN** the text is masked
- **THEN** all of them SHALL be unchanged, while `20260930` followed by one more digit or an 8-digit non-date token SHALL be masked

#### Scenario: Wrong password, then a changed password file
- **GIVEN** a file no candidate opens
- **WHEN** the worker processes it
- **THEN** it SHALL be `failed/password` with no password text in any log, summary or API call
- **AND** after the password file changes (new `credential_version`) the file SHALL be retried

### Requirement: Parser sandbox and gate

The parser SHALL run as `bwrap --unshare-all --share-net --die-with-parent --new-session --cap-drop ALL` with read-only system paths, the CLI binary at `/opt/claude/claude`, the login directory at `/cfg` (only `/cfg/.credentials.json` writable, and not even that for verify), tmpfs `/tmp` and `/work`, and the environment exactly `HOME=/tmp CLAUDE_CONFIG_DIR=/cfg PATH=/opt/claude:/usr/bin`. The CLI SHALL be run with `-p --safe-mode --tools "" --disallowedTools "mcp__*" --strict-mcp-config --mcp-config '{"mcpServers":{}}' --permission-prompts none --no-session-persistence --max-turns 1 --output-format stream-json --verbose --json-schema <schema> --model <pinned>`. Only the pinned CLI version (2.1.296) SHALL parse; any other version disables parsing. The stream SHALL be validated line by line while it is read: the init tools equal `["StructuredOutput"]`, the assistant message holds exactly one `tool_use` named `StructuredOutput`, the user message is exactly one matching `tool_result`, the result is `success` with `num_turns == 2` and a `structured_output`; the first violation kills the process group at once. Input to the child is bounded at 400 KB, stdout and stderr at 2 MB each while streaming, one attempt at 120 s, 3 attempts; the whole process group is killed and reaped on expiry. Every `run`, `poll`, `backfill` and `verify` SHALL call the gate before its first parse; a violation anywhere writes the latch `parser-disabled.json`, while which no process parses and runs finish `failed` with `parser_disabled`. Only a successful operator `python -m worker gate` (worker canary, verify canary and host checks) removes it. A file that failed `sandbox` SHALL become due again when a newer successful gate exists.

#### Scenario: A second tool call latches
- **GIVEN** a parser child that calls a second tool
- **WHEN** the gate canary or a real parse reads the stream
- **THEN** the outcome SHALL be `failed/sandbox`, the latch SHALL exist and later runs SHALL finish `failed` with `parser_disabled`

#### Scenario: A timeout kills the whole group
- **GIVEN** a child that spawns a grandchild and never answers
- **WHEN** the attempt time expires
- **THEN** the process group SHALL be killed and reaped and no process SHALL remain

#### Scenario: Operator gate clears the latch only when everything passes
- **GIVEN** a latch and an operator gate whose host checks or verify canary fail
- **WHEN** `python -m worker gate` runs
- **THEN** the latch SHALL remain and the command SHALL exit 1
- **AND** when every check passes the latch SHALL be removed

### Requirement: Worker runs under a lease

Every submission SHALL carry `run_id` and `lease_token`; the worker SHALL renew the lease every 5 minutes (one immediate retry before declaring it lost) and SHALL check a guard before every write, so a lease lost while a parse is in flight blocks the submission that follows. A 409 on renew or submit SHALL abort the run (`failed`, reason `lease`). The claimed lease SHALL be kept on disk (0600) so a restarted worker resumes its own run; an expired lease is reclaimed, and a resume discards the stored run only on 404 or 409. At most one worker SHALL run at a time (`flock` on `<state_dir>/.lock`); a second prints `already running` and exits 0. Per-file failures SHALL be contained as `failed` or `transient` and the run goes on; only a lost lease, a transport error or an `auth` failure aborts it. Transient outcomes SHALL follow a retry table keyed by the worker epoch, which moves only after the API accepted it. The full run SHALL end with `POST /reconciliation/sweep` under its lease.

#### Scenario: Lease lost during a parse
- **GIVEN** a lease that the API reports lost while the parser is running
- **WHEN** the parse returns
- **THEN** no submission SHALL be sent and the run SHALL end `failed` with reason `lease`

#### Scenario: Crash after claim resumes the same run
- **GIVEN** a worker that crashed after claiming a run
- **WHEN** it starts again
- **THEN** it SHALL resume the stored run with the same `run_id` instead of claiming a new one

#### Scenario: One bad file does not abort the run
- **GIVEN** a file whose processing raises an unexpected error
- **WHEN** the run continues
- **THEN** that file SHALL be `failed` or `transient`, the other files SHALL be processed, and the run SHALL finish `done`

#### Scenario: A second worker exits cleanly
- **GIVEN** a worker holding the lock
- **WHEN** a second worker starts
- **THEN** it SHALL print `already running` and exit 0

### Requirement: Account map route and read-only settings reader

`GET /statements/account-map` (scope `ingest`) SHALL return `{"account_map": ..., "mapping_version": <sha256 prefix>}` from the stored settings, applying defaults when no settings row exists, and SHALL be served only while the reconciliation feature is on. The settings readers `read_reconciliation_settings(db)` and `read_reconciliation_rules(db)` SHALL never insert; `GET /settings/reconciliation`, the account-map route and verify SHALL use them. An account-map key SHALL be `mail/<folder>/<sub>` (three parts) or `manual/<folder>` (two parts); other shapes SHALL be rejected with 422, so a manual-root file can be bound to an account.

#### Scenario: Worker token reads the map
- **GIVEN** a stored account map and a token with only the `ingest` scope
- **WHEN** the token calls `GET /statements/account-map`
- **THEN** the response SHALL carry the map and its `mapping_version`

#### Scenario: Both key shapes are accepted
- **WHEN** an account map with `mail/銀行/卡` and `manual/國泰世華` keys is stored
- **THEN** it SHALL be accepted, and a map with a one-part or four-part key SHALL be refused with 422

#### Scenario: Reading settings does not write
- **GIVEN** no settings row
- **WHEN** `GET /settings/reconciliation` or verify reads the settings
- **THEN** defaults SHALL be returned and no row SHALL be inserted

### Requirement: Verify mode writes nothing

`export-masked --for-verify` SHALL download, unlock and mask into private snapshots under the verify directory and SHALL NOT write to the API, the database or the object store. `verify` SHALL require `STATEMENT_VERIFY_DB_URL` (no fallback to the application URL; without it the command refuses with exit 2) and SHALL parse first (cached by sha256 and parser version), then open one short `REPEATABLE READ READ ONLY` transaction for matching, rolling it back. For a statement whose `(account, currency, period_end)` already exists the stand-in statement SHALL carry the existing statement's id, so its own claims are excluded as in a re-reconcile; otherwise the stand-in id is 0. When the guardrails fail, matching SHALL be skipped as `reconcile` does. The parser child SHALL use the read-only login copy and an expired token SHALL fail with `auth`, never refresh. `verify` SHALL latch on the same violations as `run`, and the command SHALL exit 1 whenever its totals contain errors. The report (`<verify_dir>/reports/<timestamp>.json`, 0600) is the only thing written, and only its aggregates SHALL be quoted outside the machine.

#### Scenario: A dry run claims without writing
- **GIVEN** a parsed statement line of `-580` and one exact ledger entry
- **WHEN** verify matches it
- **THEN** the report SHALL show one claim and every table's row count SHALL be unchanged

#### Scenario: Guardrail failure skips matching
- **GIVEN** a parse whose guardrails fail
- **WHEN** verify runs
- **THEN** matching SHALL be skipped for that statement and `guardrail_failed` SHALL be counted

#### Scenario: Existing statement identity excludes its own claims
- **GIVEN** a statement with the same account, currency and period end whose lines already hold claims
- **WHEN** verify matches the re-parsed statement
- **THEN** the existing claims SHALL NOT make the entries unavailable

#### Scenario: Read-only role is required
- **GIVEN** `STATEMENT_VERIFY_DB_URL` unset
- **WHEN** `verify` runs
- **THEN** it SHALL refuse with exit 2 and open no connection
