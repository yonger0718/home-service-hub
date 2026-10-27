## ADDED Requirements

### Requirement: Read-only notifier
The notifier SHALL read HomeHub only through GET routes with a token of scope `read`, and SHALL NOT create, change or delete any HomeHub data.

#### Scenario: Only GET requests are issued
- **WHEN** any notifier command runs
- **THEN** every API request it makes uses the GET method

### Requirement: Daily debit reminder
The daily run SHALL list card debits due within the configured business-day lead time, and SHALL send a message only when at least one item is rendered; a same-day repeat of the same triggers SHALL NOT be resent.

#### Scenario: Quiet day
- **WHEN** no debit is due and nothing else is rendered
- **THEN** nothing is sent and the run exits 0

#### Scenario: Repeated triggers
- **WHEN** the daily run repeats with the same triggers on the same day
- **THEN** no second message is sent

### Requirement: Weekly digest
The weekly run SHALL always send a digest of open reconciliation work, missing settings and the time of the last daily check, split into messages of at most 2000 characters on line boundaries.

#### Scenario: Nothing pending
- **WHEN** the weekly run finds no pending work
- **THEN** a digest saying so is still sent

### Requirement: Dry run writes nothing
Without `--send` and with `--dry-run`, a run SHALL print its message and SHALL NOT deliver anything or write the state file.

#### Scenario: Dry run
- **WHEN** `python -m notify daily --dry-run` runs
- **THEN** the message is printed and the state file is unchanged

### Requirement: Failures are delivered by systemd
A failing `--send` run SHALL exit 1, write a one-line reason to `last_failure.txt` and leave the state untouched; the `OnFailure` unit SHALL deliver that text or a generic line and exit 0.

#### Scenario: API unreachable
- **WHEN** the API cannot be reached during a send run
- **THEN** the run exits 1 and the failure unit sends the stored reason once

### Requirement: Capped reads are failures
The notifier SHALL page `/entries` to completion and SHALL treat a full page of a capped route whose oldest row is still inside the needed window as a failure.

#### Scenario: Truncated audit page
- **WHEN** an audit page is full and its oldest row is inside the window
- **THEN** the run fails instead of rendering from the truncated page
