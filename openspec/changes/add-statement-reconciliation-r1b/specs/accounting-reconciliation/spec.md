## ADDED Requirements

### Requirement: Eligibility gates

A statement line SHALL be matched only against ledger rows whose class is admitted by the gate of its statement kind and line kind. Card gate: `purchase` and `installment` admit `expense`, `refund` admits `refund`, `payment` admits `transfer_in`, `fee` admits `fee`, `interest` admits `interest`, `reward` admits `reward`. Bank gate: `deposit` admits `income`, `transfer_in`, `interest` and settlement inflows; `withdrawal` admits `expense`, `transfer_out`, `fee` and settlement outflows; `transfer_in`, `transfer_out`, `fee`, `interest`, `refund` and `reward` admit their own kind. A settlement entry (`is_settlement`) takes the class `settlement_in` when its flow is positive and `settlement_out` otherwise. A card `payment` line SHALL only match a `transfer_in` whose peer leg is a non-card account. An `installment` line SHALL be matched only through a confirmed plan map (`installment_plan_map`, key `<merchant_norm>|<installment_total>|<abs flow at 4 dp>`) to the schedule definition of an entry's posted instance with the same sequence and flow; without a mapping the line is never auto-claimed. A split group SHALL be admissible only when every top-level member is a candidate, none is claimed and none belongs to an installment instance: a partial group is never admissible. Children (`parent_entry_id` set) SHALL reach a line only through the child path (as part of their parent's representation, or alone as an uncovered fee/discount child claimed by a separately printed non-purchase, non-payment line), never as standalone rows. Candidates are the participating accounts' entries (the statement's account plus accounts combined into it) dated by `posted_date` (else `entry_date`) within `candidate_window_days` (10) of the period, excluding `balance_adjustment`, excluding `reward` unless the statement prints a reward line, and excluding rows actively claimed by another statement.

#### Scenario: Exact standalone expense is claimed
- **GIVEN** a card line `purchase` of `-580` on 2026-09-03 and one expense entry of `-580` on 2026-09-03
- **WHEN** the line is matched
- **THEN** the line SHALL be claimed by that entry with rule `exact` and no case SHALL be opened

#### Scenario: Payment needs a non-card transfer peer
- **GIVEN** a card `payment` line of `5000` and a `transfer_in` entry of `5000` whose peer leg is a non-card account
- **WHEN** the line is matched
- **THEN** it SHALL be claimed with rule `payment`
- **AND** a `transfer_in` whose peer is a card or unknown SHALL NOT be eligible

#### Scenario: Installment is gated by the confirmed plan
- **GIVEN** an `installment` line for `APPLE`, 12 instalments, `-1000`, sequence 2, and an entry of `-1000` that is instance 2 of definition 7
- **WHEN** no plan is confirmed for `APPLE|12|1000.0000`
- **THEN** the line SHALL stay a `line_unmatched` case with hint `installment_unmapped` and candidates `[{"definition_id": 7}]`
- **AND** once `APPLE|12|1000.0000` maps to definition 7 the line SHALL be claimed with rule `installment`

#### Scenario: Plan mapped to another definition stays a case
- **GIVEN** the plan key `APPLE|12|1000.0000` maps to definition 7 and the only instance entry belongs to definition 8
- **WHEN** the line is matched
- **THEN** it SHALL NOT be claimed and SHALL be a `line_unmatched` case

#### Scenario: Partial group is never admissible
- **GIVEN** a split group with members `-300` and `-280` where only the `-300` member is inside the candidate window
- **WHEN** a `-300` line is matched
- **THEN** the line SHALL NOT be claimed and SHALL be a `line_unmatched` case

#### Scenario: Bank withdrawal respects groups and installment members
- **GIVEN** a bank statement and a split group {`-300`, `-280`}
- **WHEN** a `withdrawal` of `-300` is matched it SHALL NOT claim the lone member, and a `withdrawal` of `-580` SHALL claim both members with rule `group`
- **AND** a `withdrawal` of `-1000` SHALL NOT claim an entry that is an installment instance

#### Scenario: Orphan refund is eligible
- **GIVEN** a refund entry of `200` with no `refunds_entry_id`
- **WHEN** a card `refund` line of `200` on the same date is matched
- **THEN** it SHALL be claimed with rule `refund`

### Requirement: Representations and scoring

For each line the matcher SHALL build the representations that conserve the line's flow: a standalone entry; a principal plus its uncovered children (`principal_children`, rule `exact_children`); a whole split group (`group`, only where the gate admits `expense`: card purchase, bank withdrawal); a whole group plus the members' children (`group_children`); a child alone (`child`); an installment instance (`installment`). A representation's score SHALL be `0.60 + 0.30 × max(0, 1 − d/4) + 0.10 × J`, where `d` is the smallest day distance between the line and the entries (posted date to posted date, and, when the line has a transaction date, transaction date to entry date) and `J` is the Jaccard overlap of the merchant tokens (the non-empty space-separated parts of the normalised merchant; single-character tokens are kept); a distance above `exact_window_days` (3) scores 0. An `installment` representation with a confirmed plan SHALL score `0.90 + 0.10 × J` when the distance is within `candidate_window_days` (10), otherwise 0. A line SHALL be claimed only when the best score is at least `accept` (0.80) and it leads the second-best by at least `margin` (0.15) (or is the only one). When the best score is at least `ambiguous_floor` (0.50) but the line is not claimed, the line SHALL become an `ambiguous` case listing up to five candidates and processing of the line SHALL stop (no foreign or near fallback). Lines are processed in `id` order and rows claimed by an earlier line are unavailable to later lines (the greedy limit is accepted). Otherwise, in this order: a non-installment line left with no representation whose shadow re-derivation (ignoring claims) yields exactly one representation holding a row claimed by an earlier line SHALL become `line_unmatched` with hint `claimed_by_earlier_line` and that entry id; a foreign line (foreign amount and currency printed) whose single standalone candidate has the same original amount and currency within `foreign_window_days` (5) SHALL become `amount_delta` with `residual = line flow − entry flow`, `fee_expected` (the proposed FX fee quantised half-up to 1 for TWD/JPY, else 0.01) and `fee_matches` true exactly when `residual + fee_expected = 0`; two or more such candidates SHALL make the case `ambiguous` (reason `foreign`, no entry bound); a same-merchant entry within `near_tolerance_abs` (10) or `near_tolerance_pct` (3 %) of the amount, whichever is larger, and within `near_window_days` (5) SHALL become `amount_delta` bound to the smallest absolute delta (then nearest date) with every near candidate listed, and a near or foreign amount is NEVER auto-claimed; a `fee`, `interest` or `reward` line whose merchant contains one of `bank_only_patterns` (年費, 循環利息, 現金回饋, 跨行手續費, 轉帳手續費, 利息) SHALL become `line_unmatched` with hint `bank_only`; any other line SHALL become a plain `line_unmatched`. An installment line without a claim SHALL become a case only through the plan gate: `ambiguous` when several mapped candidates reach the floor, `installment_unmapped`, `installment_date_drift` (entry and definition named, when the mapped entry lies beyond the candidate window), or plain `line_unmatched`.

#### Scenario: Two identical candidates are ambiguous
- **GIVEN** a line of `-580` on 2026-09-03 without merchant text and two entries of `-580` on 2026-09-03
- **WHEN** the line is matched
- **THEN** no claim SHALL be made and one `ambiguous` case SHALL list both entry ids

#### Scenario: Same day, same amount, different merchants is ambiguous
- **GIVEN** a line `全聯 大安` of `-580` and entries `全聯 大安` and `家樂福` of `-580` on the same day
- **WHEN** the line is matched
- **THEN** the text term (at most 0.10) SHALL be below the 0.15 margin and the line SHALL be an `ambiguous` case

#### Scenario: Merchant text decides across different dates
- **GIVEN** a line `全聯 大安` of `-580` on 2026-09-03 and entries `全聯 大安` on 2026-09-03 and `家樂福` on 2026-09-04
- **WHEN** the line is matched
- **THEN** the first entry SHALL be claimed

#### Scenario: Split group of -300 and -280 conserves -580
- **GIVEN** a split group {`-300`, `-280`} and lines `-580` and `-300` on 2026-09-03
- **WHEN** the lines are matched
- **THEN** line one SHALL claim both members with rule `group`
- **AND** line two SHALL be a `line_unmatched` case, and a group of {`-300`, `-281`} SHALL NOT satisfy `-580`

#### Scenario: Group with a child is a separate representation
- **GIVEN** a group with one member of `-100` and a fee child of `-2`
- **WHEN** a line of `-102` is matched it SHALL claim member and child with rule `group_children`
- **AND** two lines `-100` and `-2` (fee) SHALL claim the member (`group`) and the child (`exact`)

#### Scenario: Installment inside the candidate window
- **GIVEN** the confirmed plan `APPLE|12|1000.0000` and an instance entry of `-1000`
- **WHEN** the line is dated 2026-09-17 and the entry 2026-09-16 or 2026-09-08 (1 or 9 days)
- **THEN** the line SHALL be claimed with rule `installment` (score 0.90 plus text)
- **AND** an entry on 2026-09-05 (12 days) SHALL give a `line_unmatched` case with context `{"hint": "installment_date_drift", "entry_id": 40, "definition_id": 7}`

#### Scenario: Foreign residual equals the fee
- **GIVEN** an entry of `-660` with original `-3000 JPY`, a line of `-670` with foreign `-3000 JPY` and an expected fee of `10`
- **WHEN** the line is matched
- **THEN** no claim SHALL be made and an `amount_delta` case SHALL carry `residual = "-10.0000"` and `fee_matches = true`
- **AND** a line of `-690` SHALL give `fee_matches = false`; a fee of `10.5` SHALL quantise to `11` (no match) and `10.4` to `10` (match)

#### Scenario: Foreign line closes once the fee child exists
- **GIVEN** the same entry with a fee child of `-10`
- **WHEN** the `-670` line is matched
- **THEN** it SHALL be claimed with rule `exact_children` and no case

#### Scenario: Foreign twins are ambiguous
- **GIVEN** two entries of `-660` and `-661` with original `-3000 JPY` inside the foreign window
- **WHEN** the `-670` foreign line is matched
- **THEN** an `ambiguous` case SHALL list entries 70 and 72 with reason `foreign`

#### Scenario: Near amount is always a case
- **GIVEN** a line `全聯 大安` of `-585` and an entry of `-580` on the same day
- **WHEN** the line is matched
- **THEN** no claim SHALL be made and an `amount_delta` case SHALL carry `residual = "-5.0000"`
- **AND** with several near entries the case SHALL bind the smallest absolute delta then nearest date and list every candidate

#### Scenario: Bank-only line is hinted
- **GIVEN** a `fee` line `跨行手續費` of `-5` and no entries
- **WHEN** the line is matched
- **THEN** a `line_unmatched` case with hint `bank_only` SHALL open

#### Scenario: Second line on a claimed entry is hinted
- **GIVEN** two lines of `-580` on 2026-09-03 and one entry of `-580`
- **WHEN** the lines are matched
- **THEN** line one SHALL claim the entry and line two SHALL be `line_unmatched` with context `{"hint": "claimed_by_earlier_line", "entry_id": 10}`

### Requirement: Monetary conservation and unique claims

A claim SHALL consist of coverage rows whose flows sum, at 4 decimals, to the line's `flow_amount`; `write_claim` SHALL refuse otherwise with HTTP 409 `coverage not conserved`, and also when a row's flow differs from its entry's current amount (`coverage snapshot drift`). Each coverage row SHALL carry a `snapshot` of its entry (`id`, `amount`/`flow` as 4-decimal strings, currency, original amount and currency, entry and posted date, account, kind, group, parent, transfer group, refund and settlement links, `is_settlement`, name, merchant); deleting the entry SET NULLs the `entry_id` and `group_id` columns but the snapshot keeps the ids. A ledger row SHALL have at most one active claim across all statements (partial unique index `ux_statement_coverage_active_entry`); violating it SHALL answer HTTP 409 with code `duplicate_claim` naming the entry. After every reconcile `assert_conserved` SHALL verify every line with active coverage and fail with 409 otherwise.

#### Scenario: Exact claim of -580
- **GIVEN** a line of `-580` and one entry of `-580`
- **WHEN** the claim is written
- **THEN** one active coverage row SHALL exist with snapshot flow `-580.0000`

#### Scenario: Non-conserved claim is refused
- **GIVEN** a line of `-580` and rows summing to `-579`
- **WHEN** the claim is written
- **THEN** the write SHALL fail with HTTP 409 and no row SHALL be stored

#### Scenario: Second active claim is a duplicate
- **GIVEN** an entry actively claimed by one statement
- **WHEN** another line or statement claims it
- **THEN** the response SHALL be HTTP 409 with code `duplicate_claim`, and the other statement's coverage SHALL stay untouched

### Requirement: Dirty sweep

`coverage_service.sweep(statement)` SHALL apply every `coverage_dirty` event past the statement's `swept_through_event_id` that touches it and then advance the watermark to the highest event id scanned (relevant or not); it SHALL never delete events and never commit. Precondition: the caller holds the statement row `FOR UPDATE`. Relevance: an `update` or `delete` of an entry or group actively covered (by column or, after a delete, by snapshot id) SHALL release the covering line(s) as a whole with reason `dirty:<op>:<id>` (a group event: `dirty:group:<event id>`); in live mode a released line SHALL then follow the event's newest case: an `open`/`proposed` case has its `version` bumped and the event noted in its context (no second case); a `resolved` case is reopened with its resolution moved into `context.previous_resolution`; none, `dismissed` or `superseded` gets a new `recheck` case. A historical statement releases only, with no case. Population changes SHALL set `needs_recheck` in live mode only: an entry insert, move, edit or delete of an uncovered row in the period on a participating account (old or new side), and an account event (opening balance, currency, closing/due fields, credit or archive flags, or `combined_account_id` relinked to or away from the statement's account). Events of an action applied on this same statement SHALL be skipped; events of an action applied on another statement SHALL NOT. The dirty triggers return early, before taking the barrier, while the settings' `dirty_enabled` is false (no events are written then). The sweep SHALL scan only ids up to a committed cap: every dirty trigger takes `pg_advisory_xact_lock_shared(0x44495254)` before it inserts, and the sweep takes that key exclusively at session level, reads `max(coverage_dirty.id)` and unlocks at once (a failed or false unlock invalidates the connection). This requires READ COMMITTED isolation and `coverage_dirty_id_seq` with CACHE 1. Events are scanned in batches of 1000. `sweep_pending` and `has_pending_events` SHALL use one superset relevance (events on the account or a combined child on either side, account events relinking to the account, groups and entries held by active coverage by column or snapshot).

#### Scenario: Amount edit on a claimed entry
- **GIVEN** a live statement whose line 1 is claimed by an entry of `-580`
- **WHEN** the entry is edited to `-600` and the statement is swept
- **THEN** the line SHALL be released, a `recheck` case SHALL open and `swept_through_event_id` SHALL equal the newest event id

#### Scenario: Edit reopens the resolved case
- **GIVEN** the line's newest case is `resolved`
- **WHEN** its claimed entry is edited and the statement is swept
- **THEN** that case SHALL be `open` with `version` bumped and `context.previous_resolution` set, and no second case SHALL be created

#### Scenario: Open case is bumped, closed newest case gets a recheck
- **GIVEN** the newest case is `open`/`proposed`
- **WHEN** the covered entry is edited
- **THEN** its `version` SHALL increase without a new row
- **AND** when the newest case is `dismissed` or `superseded` a new `recheck` case SHALL open

#### Scenario: Historical statement releases without a case
- **GIVEN** a historical statement with a claimed entry
- **WHEN** the entry is edited and swept
- **THEN** the coverage SHALL be `stale` and no case SHALL exist

#### Scenario: Delete keeps the snapshot and releases
- **WHEN** a claimed entry is deleted and the statement is swept
- **THEN** the row's `entry_id` SHALL be NULL, its snapshot id kept, and the line released

#### Scenario: Group dissolve releases as a whole
- **GIVEN** a line claimed by the split group {`-300`, `-280`}
- **WHEN** the split is dissolved or the group is edited
- **THEN** the line's coverage SHALL be released as a whole

#### Scenario: Population change flags a recheck in live mode only
- **WHEN** an entry is inserted inside the period on the account, or the opening balance is edited
- **THEN** a live statement SHALL get `needs_recheck = true` and a historical one SHALL NOT
- **AND** an insert outside the period SHALL NOT flag it, and an insert on a combined child account SHALL

#### Scenario: Self-generated events are skipped
- **GIVEN** an event written with the id of an action applied on this statement
- **WHEN** the statement is swept
- **THEN** the event SHALL NOT release the line; the same event for an action applied on another statement SHALL

#### Scenario: Scan crosses batches
- **GIVEN** 2500 unrelated events followed by the relevant edit
- **WHEN** the statement is swept
- **THEN** the line SHALL be released, `swept_through_event_id` SHALL equal the highest id and the 2501 events SHALL remain

#### Scenario: Sweep waits for an uncommitted writer
- **GIVEN** a writer transaction holds a dirty event and the shared barrier
- **WHEN** the sweep starts
- **THEN** it SHALL wait for the writer to finish and SHALL NOT advance past that event

#### Scenario: Sequence is not cached
- **WHEN** the schema is inspected
- **THEN** `coverage_dirty_id_seq` SHALL have CACHE 1

### Requirement: Coverage transfer and quarantine

When a revision becomes current, each paired old event SHALL be handled by equivalence: `identical` SHALL move the event to the new line and transfer its active coverage rows to that line (lineage `transferred = true`, meaning the event pointer moved); `normalised` SHALL do the same, set `statement_event.flag = 'text_changed'` (UI: 文字已變更) and supersede the event's pending proposals; `changed` and unpaired old events SHALL supersede pending proposals, set `current_line_id` NULL and, when they hold active coverage or an applied action, release that coverage (reason `lineage:<equivalence>`), become `quarantined` and, in live mode, get one open `parse_review` case (an open/proposed one for the same event is reused: context merged, version bumped); otherwise they become `retired`. Event-level `parse_review` cases are never superseded by a later revision; only the owner resolves a quarantine. Events and coverage of a revision that does not become current SHALL NOT move.

#### Scenario: Identical re-parse keeps coverage
- **GIVEN** a covered line and a re-parse that pairs `identical`
- **WHEN** the new revision becomes current
- **THEN** the coverage rows SHALL point at the new line and stay active, and the lineage row SHALL have `transferred = true`

#### Scenario: Normalised re-parse flags the event
- **WHEN** a re-parse pairs `normalised`
- **THEN** the coverage SHALL transfer and the event's flag SHALL be `text_changed`

#### Scenario: Changed covered line is quarantined
- **GIVEN** a covered line in live mode
- **WHEN** a re-parse changes its merchant
- **THEN** its coverage SHALL be `stale` with reason `lineage:changed`, the old event `quarantined` with `current_line_id` NULL and one open `parse_review` case SHALL exist
- **AND** the reconcile pass SHALL skip with `parse_review` until the owner resolves it

#### Scenario: Uncovered changed event is retired
- **GIVEN** a changed or unpaired old event with no coverage and no applied action
- **WHEN** the revision becomes current
- **THEN** the event SHALL be `retired`

#### Scenario: Historical mode re-matches
- **GIVEN** a historical statement with a covered line
- **WHEN** a changed re-parse becomes current
- **THEN** no case SHALL open and the new line SHALL be matched afresh

### Requirement: Reconcile

`reconcile(statement)` SHALL run in one transaction and never commit. Lock order: the shared import key (`take_import_key_shared`, a non-waiting try-lock on every path: while an import holds the key it raises `ImportRunningError` at once; the revision hook (`locked=True`) catches it and sets `needs_recheck`, the reconcile route answers HTTP 409 `import_running`, and the batch records `error: "ImportRunningError"` for that statement and goes on) → the statement row `FOR UPDATE` → flush → sweep (before any `entry_group` or `ledger_entry` lock, so the barrier cannot deadlock a writer) → `entry_group` rows ascending → the candidates, their children and their transfer legs in ONE ordered `SELECT … FOR UPDATE` → re-read the population under the locks (rows that joined, or whose group changed, in between are left to the next sweep) → match → coverage → cases → counts. Reconcile SHALL write no ledger rows. It SHALL skip with `no_current_revision` when the statement has no current revision or the current one failed its guardrails, and with `parse_review` when a `parse_review` case is open/proposed (both after the sweep). Release and re-derive: a line whose active claim equals the new claim (same rows, roles, rule and snapshots) keeps its coverage rows; every other active row (including orphans whose entry was deleted) is released with reason `reconcile` and the new claim written. Matcher cases (`line_unmatched`, `ambiguous`, `amount_delta`, `entry_unmatched`, `duplicate_claim`, `balance_gap`, `recheck`) SHALL be written in live mode only: an open/proposed case equal to a newly derived one (kind, event, entry, candidates and context without the carried `sweep` note) keeps its id (its line pointer refreshed), every other open matcher case becomes `superseded` (version bumped), the rest are created, a superseded case's sweep event is carried to its successor as `context.sweep`, and a `dismissed` case of the same kind on the same event, entry or gap is never reopened. `parse_review` and `statement_conflict` cases are not touched. Reverse population (entries in the period not claimed): an entry posted later than `period_end − deferral_days` (2; with `period_end` 2026-09-30 only 2026-09-29 and 2026-09-30) SHALL be explained as `deferred_next_period` and opens no case; a claimed parent's unclaimed child SHALL open `entry_unmatched` with hint `uncovered_child`; any other becomes `entry_unmatched`, with hint `move_account` (statement id and line id) when an uncovered line of another live statement outside the participating accounts, same currency and flow, is printed within ±3 days of the entry. Deferral cases (live mode only, like all cases) are `resolved` `entry_unmatched` cases with explanation `deferred_next_period`, `resolved_by` NULL and `deferred_to_period_end` from the account's cycle (card: the next closing date after `period_end`, the day clamped to the month; bank: the last day of the next month). An earlier statement SHALL NOT lock or reference a later one: `deferred_to_statement_id` is filled only by the later statement's reconcile (a case-rows-only update matching account, currency and `deferred_to_period_end = period_end`) or by `fill_deferral_links` (batch), an existing link is kept and cleared only when `deferred_to_period_end` itself changes, and a deferral whose entry was evaluated and is no longer deferred is superseded. A bank statement SHALL open a `balance_gap` case with context `{"gap": "<statement_total − account balance as of period_end>"}` (4 decimals) when the gap is non-zero. After every run `matched_count` SHALL be the number of lines with active coverage, `explained_count` the number of cases `resolved` with an explanation plus `dismissed` cases, `open_case_count` the open/proposed cases, and a run that is not skipped SHALL clear `needs_recheck` (a skipped run returns before the recount and leaves it set). Re-running with unchanged inputs SHALL be idempotent.

#### Scenario: Card statement end to end
- **GIVEN** a live card statement with matching, ambiguous and unmatched lines and entries
- **WHEN** it is reconciled
- **THEN** claims, cases and counts SHALL be written and `assert_conserved` SHALL pass

#### Scenario: Re-reconcile is idempotent
- **WHEN** the same statement is reconciled twice
- **THEN** coverage row ids and open case ids SHALL be unchanged

#### Scenario: Changed outcome supersedes the stale case and keeps dismissed ones
- **GIVEN** an open case that the new run no longer derives and a dismissed case of the same kind on the same event
- **WHEN** the statement is reconciled
- **THEN** the open case SHALL be `superseded` and the dismissed one SHALL stay dismissed with no replacement

#### Scenario: Historical statement gets coverage but no cases
- **GIVEN** a historical statement
- **WHEN** it is reconciled
- **THEN** coverage SHALL be written and no case SHALL exist

#### Scenario: Rows claimed by another statement are not stolen
- **GIVEN** an entry actively claimed by statement A
- **WHEN** statement B is reconciled
- **THEN** the entry SHALL NOT be a candidate of B and A's coverage SHALL stay active

#### Scenario: Combined child spend matches on the master
- **GIVEN** a child card combined into the master card
- **WHEN** the master's statement is reconciled
- **THEN** the child's spend SHALL be matchable on the master's statement

#### Scenario: Uncovered child is an entry case
- **GIVEN** a claimed group member with an unclaimed fee child of `-2`
- **WHEN** the statement is reconciled
- **THEN** an `entry_unmatched` case with hint `uncovered_child` SHALL open for the child

#### Scenario: Installment matches through the confirmed plan
- **GIVEN** a confirmed plan map for the line's key
- **WHEN** the statement is reconciled
- **THEN** the line SHALL be claimed with rule `installment`

#### Scenario: Changed re-parse waits for the quarantine
- **WHEN** a live statement receives a changed re-parse of a covered line
- **THEN** the revision hook SHALL leave the pass to the open `parse_review` and the next reconcile SHALL report `skipped = "parse_review"`

#### Scenario: Deferral is explained then linked by the later statement
- **GIVEN** a card with `closing_day` 25, an entry of `-90` on 2026-09-24 and a September statement (2026-08-26 to 2026-09-25) that does not print it
- **WHEN** the statement is reconciled
- **THEN** a `resolved` `entry_unmatched` case with explanation `deferred_next_period`, `deferred_to_period_end = 2026-10-25` and `deferred_to_statement_id` NULL SHALL exist and `explained_count = 1`
- **AND** when the next statement (2026-09-26 to 2026-10-25) prints the entry and is reconciled, the case SHALL link to it and survive a re-reconcile of the first statement

#### Scenario: Batch fills the link when the later statement already exists
- **GIVEN** a bank with October already imported and a September statement leaving a `-90` entry on 2026-09-30 deferred
- **WHEN** `fill_deferral_links` runs
- **THEN** it SHALL link exactly 1 case to the October statement and a second call SHALL link 0

#### Scenario: Next period end rules
- **THEN** a closing day of 30 after 2026-09-30 SHALL give 2026-10-30, 31 after 2026-01-31 SHALL give 2026-02-28, a bank after 2026-09-30 SHALL give 2026-10-31

#### Scenario: Cross-account hint
- **GIVEN** an entry of `-444` on 2026-09-08 on card A and an uncovered line of `-444` on 2026-09-10 on another live card's statement
- **WHEN** card A's statement is reconciled
- **THEN** the entry's `entry_unmatched` case SHALL carry context `{"hint": "move_account", "statement_id": <other>, "line_id": <line>}`

#### Scenario: Bank balance gap
- **GIVEN** a bank account opening at `1000`, a `500` income entry matched on 2026-09-05, a `-70` entry on 2026-10-02 and a statement printing `deposit` lines of `500` and `100`
- **WHEN** the statement is reconciled
- **THEN** a `balance_gap` case with context `{"gap": "100.0000"}` and no line or entry SHALL be open, kept by id on a re-run, and a balanced statement SHALL have none

#### Scenario: Skips
- **WHEN** the current revision failed its guardrails, or a `parse_review` is open
- **THEN** reconcile SHALL return `skipped` `no_current_revision` or `parse_review` after sweeping

#### Scenario: needs_recheck is cleared only by a run that is not skipped
- **GIVEN** `needs_recheck = true`
- **WHEN** the statement is reconciled
- **THEN** it SHALL be false
- **AND** when the run skips (`no_current_revision` or `parse_review`) it SHALL stay true

#### Scenario: Import in progress defers the hook
- **GIVEN** an import holds the import key exclusively
- **WHEN** a revision becomes current
- **THEN** the hook SHALL set `needs_recheck` and return no cases
- **AND** the reconcile route SHALL answer 409 `import_running` and the batch SHALL list `ImportRunningError` for that statement

#### Scenario: Edit waits for the reconcile
- **GIVEN** a reconcile holds the entry locks
- **WHEN** a writer edits a claimed entry
- **THEN** the edit SHALL wait for the reconcile to commit and the next sweep SHALL release the line

### Requirement: Reconcile and sweep routes

The statements router SHALL add these routes (feature gate first, then scope):

| Route | Scope | Success | Notes |
|---|---|---|---|
| `POST /accounts/{id}/statements/{statement_id}/reconcile` | `write` | 200 | body `claims`, `cases_opened` (ids), `explained`, `unmatched_entries`, `skipped`; 404 when the statement belongs to another account |
| `POST /reconciliation/sweep` | `ingest` | 200 | body `{run_id, lease_token}`; returns `statements`, `claims`, `cases_opened` (count), `errors`, `links_filled` |
| `GET /reconciliation/cases` | `read` | 200 | filters `status`, `account_id`, `kind`, `limit` |
| `GET /settings/reconciliation` | `read` | 200 | includes `rules` and `rules_version` |
| `PUT /settings/reconciliation` | `admin` | 200 | |

The sweep SHALL verify the caller's lease, commit to release the lease row lock, then reconcile every pending statement (any mode, with a dirty event that may touch it or with `needs_recheck`, ascending) each inside a savepoint and commit after each item, so finished items stay committed and an item's failure is reported as `{statement_id, error: <exception class>}` without stopping the batch (a guardrail skip is processed, not an error). After each item except the last the lease SHALL be re-checked read-only; if it is no longer live (expired, finished or another label) the sweep SHALL roll back and answer HTTP 409 with message `lease`, leaving unstarted statements flagged. After the loop `fill_deferral_links` SHALL fill the remaining links. A missing or wrong lease SHALL answer 409; a token without `ingest` SHALL answer 403. `GET /reconciliation/cases` SHALL list newest first (`created_at` then id, descending), filter by status (`open`, `proposed`, `resolved`, `dismissed`, `superseded`), by account and by kind, default `limit` 200, accept 1 to 1000, and answer 422 for an unknown status or kind or an out-of-range limit. `PUT /settings/reconciliation` SHALL replace `rules`, `account_map` and `dirty_enabled` wholesale (an omitted field reverts to its default); `rules` keys SHALL be those of the matcher (`exact_window_days`, `foreign_window_days`, `near_window_days`, `near_tolerance_abs`, `near_tolerance_pct`, `accept`, `margin`, `ambiguous_floor`, `candidate_window_days`, `deferral_days`, `bank_only_patterns`), never `period_end`; integers SHALL be non-boolean in 0..10000, decimals strings, `accept`, `margin`, `ambiguous_floor` and `near_tolerance_pct` within 0..1, `near_tolerance_abs` at least 0, `bank_only_patterns` a list of non-empty strings; violations answer 422 on `rules`. `rules_version` SHALL start at `r1b-1` and become `r1b-<settings version>` only when a PUT changes the effective rules.

#### Scenario: Reconcile route needs write and the statement's account
- **WHEN** a token without `write` calls the route, or the statement id is used with another account id
- **THEN** the response SHALL be 403 or 404 respectively, and a valid call SHALL answer 200

#### Scenario: Sweep reports the batch
- **GIVEN** a statement with `needs_recheck` and a matching `-580` entry
- **WHEN** the worker posts its lease to `/reconciliation/sweep`
- **THEN** the body SHALL be `{"statements": 1, "claims": 1, "cases_opened": 0, "errors": [], "links_filled": 0}` and `needs_recheck` SHALL be false
- **AND** `hermes` SHALL get 403 and a wrong lease token 409

#### Scenario: Sweep lists errors and goes on
- **GIVEN** three pending statements of which one raises `RuntimeError` and one fails its guardrails
- **WHEN** the sweep runs
- **THEN** `errors` SHALL be `[{"statement_id": <broken>, "error": "RuntimeError"}]`, `statements` SHALL be 2 and `claims` 1

#### Scenario: Lease expiry stops the sweep
- **GIVEN** two pending statements and a lease that expires after the first is reconciled
- **WHEN** the sweep runs
- **THEN** the response SHALL be HTTP 409 with message `lease`, the first statement SHALL stay committed (`needs_recheck` false) and the second SHALL never start (`needs_recheck` true)

#### Scenario: Cases listing filters and limits
- **GIVEN** three open cases
- **WHEN** `GET /reconciliation/cases` is called with `read`, then with `limit=2`, `limit=1`, `limit=1000`
- **THEN** it SHALL return 3, 2, the newest 1 and 3 respectively
- **AND** `status=bogus`, `kind=bogus`, `limit=0`, `limit=1001` and `limit=x` SHALL answer 422, a token without `read` 403, and `status=resolved` or an unknown account SHALL return `[]`

#### Scenario: Settings expose and validate rules
- **WHEN** the settings are read
- **THEN** they SHALL contain `account_map`, `dirty_enabled`, `rules`, `rules_version` and `version`, with `rules.accept = "0.80"` and no `period_end`
- **AND** `{"nope": 1}`, `{"period_end": …}`, `{"deferral_days": "x"}`, `{"deferral_days": true}`, `{"accept": 0.8}`, `{"accept": "1.5"}`, `{"margin": "-0.1"}`, `{"near_tolerance_abs": "-5"}` and `{"bank_only_patterns": "年費"}` SHALL answer 422 naming `rules`
- **AND** `{"deferral_days": 3, "accept": "0.90"}` SHALL be stored with `rules_version` `r1b-<version>`, repeated unchanged, and edge values `accept 1`, `margin 0`, `ambiguous_floor 0.5` SHALL be accepted

### Requirement: Statement read model

A statement detail (`GET /accounts/{id}/statements/{statement_id}`) SHALL return, besides the R1a fields, for each current line `matched` (true when it has active coverage) and `coverage[]` (each active row's `entry_id`, `group_id`, `role`, `match_rule`, `match_kind`, `status` and snapshot `flow`), the statement's `matched_count`, `explained_count` and `open_case_count`, and `stale_events_pending`. `stale_events_pending` SHALL be true exactly when `sweep_pending` would list the statement, i.e. a dirty event past its watermark that may touch it, not any ledger change on the account.

#### Scenario: Unrelated and related events
- **GIVEN** dirty tracking on and a freshly submitted statement
- **THEN** `stale_events_pending` SHALL be false
- **WHEN** an entry of `-5` is added on the account inside the period
- **THEN** it SHALL be true

#### Scenario: Matched line carries coverage
- **GIVEN** a line claimed by an entry
- **THEN** its `matched` SHALL be true and `coverage` SHALL list that entry with its role and rule

## MODIFIED Requirements

### Requirement: Events and lineage

Each printed transaction SHALL have one `statement_event` that persists across revisions. When a revision is submitted, its lines SHALL be paired to the current revision's lines by `logical_key` in print order and classified: `identical` (same canonical key), `normalised` (same kind fields and same merchant tokens), `changed` (any other difference) or `unpaired` (no partner on either side). An `identical` or `normalised` pair SHALL transfer the old event to the new line, together with the event's active coverage; a `normalised` transfer SHALL also set the event's `flag` to `text_changed`. A `changed` pair and an unpaired old line SHALL take the event out of the current revision (`current_line_id` NULL): when the event holds active coverage or an applied action it SHALL be `quarantined` (coverage released, one open `parse_review` case in live mode), otherwise `retired`. A `changed` new line, like an unpaired new line, SHALL get a new event; the `changed` lineage row records the old and new line with `transferred = false`, and `transferred = true` on an `identical`/`normalised` row means the event pointer moved. Events move (current line, quarantine, retirement) only when the revision becomes current; a revision that does not become current SHALL record lineage but move nothing, except that its unpaired and `changed` new lines still create events with status `retired`, whose `current_line_id` stays NULL. A change in the number of identical twins (lines sharing posted date and flow) between the current and the new revision SHALL prevent the new revision from becoming current and, in live mode, open a `parse_review`. The first revision of a statement SHALL write no lineage rows and open no case for twins.

#### Scenario: Whitespace-only re-parse pairs identical
- **GIVEN** a stored line `全聯` on 2026-09-03 for `580`
- **WHEN** a re-parse prints `全聯 `
- **THEN** the lineage counts SHALL be `identical = 1` with no `changed`
- **AND** the event SHALL be unchanged with its current line moved to the new line and the new revision SHALL be current

#### Scenario: Normalised re-parse flags the event
- **GIVEN** a covered line `全聯 大安`
- **WHEN** a re-parse prints the same tokens in different text
- **THEN** the lineage SHALL be `normalised`, the coverage SHALL move to the new line and the event's `flag` SHALL be `text_changed`

#### Scenario: Merchant change on an uncovered line retires the old event
- **GIVEN** a stored uncovered line `PAYPAL *Spotify`
- **WHEN** a re-parse prints `PAYPAL *Netflix` on the same date and amount
- **THEN** `changed = 1` and two events SHALL exist: the old one `retired` with `current_line_id` NULL, and a new `live` one whose first and current line is the new line
- **AND** one `changed` lineage row SHALL record the old and new line with `transferred = false`

#### Scenario: Merchant change on a covered line quarantines the old event
- **GIVEN** a stored line claimed by an entry in a live statement
- **WHEN** a re-parse changes its merchant
- **THEN** the old event SHALL be `quarantined`, its coverage `stale`, and one open `parse_review` case SHALL exist

#### Scenario: Moved date retires the old event
- **GIVEN** a stored uncovered line on 2026-09-03 for `580`
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
