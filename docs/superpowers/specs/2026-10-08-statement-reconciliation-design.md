# Statement reconciliation (對帳) — design

Status: **v3** for Multica review (2026-10-09). v3 answers round 2 (A1–A11, B1–B5) on AGENT-83 on top of v2's answers to rounds 1/1.1; the cumulative answer map is §15. Owner decisions are marked **[owner]**; defaults I chose are **[default]**; items the owner still has to decide are in §14.

## 1. Problem

The reminder centre's 信用卡帳單 only says how much to pay; nothing checks the ledger against what the bank billed. The owner's Hermes mail automation already lands every card and bank e-statement in Google Drive:

| Drive path (under `財務對帳單/`) | content (2026-10-09) |
|---|---|
| `銀行/信用卡/<issuer>/` | 14 card folders, emailed e-statements, `YYYY-MM_<issuer>_…pdf` |
| `銀行/銀行帳戶/<bank>/` | 12 bank folders, emailed e-statements |
| `手動下載/國泰世華/` | 12 monthly Cathay 綜合月結單, `YYMM.pdf`, owner-downloaded |
| `投資/`, `電子發票/` | out of scope |

496 bank PDFs + 12 manual. Every PDF is password-protected. A read-only spike (one or two files per folder) unlocked every folder with the owner's password rules and found a text layer in every file, so **v1 has no OCR and no vision path**. Two folders use uppercase `.PDF`. 台新 changed its password rule in 2024-12.

Goals, in priority order:
1. Per statement period: every statement line is matched to ledger rows, explained (bank fee, interest, reward, deferred) or an open case; same in reverse.
2. The ledger converges to the statement only through explicit, validated, reversible, audited actions. Nothing writes money without the owner's grant.
3. Unattended for new statements; idempotent over history.
4. The triage brain is replaceable.

Non-goals (v1): bank logins/scraping **[owner]**, OCR/vision, investment and e-invoice files, chat-based approval, historical ledger repair.

## 2. Shape

| Layer | Runs in | Handles |
|---|---|---|
| Rules | API process, `reconciliation_service` (pure, deterministic) | eligibility gates, competing representations compared globally per line, monetary conservation, bank-only lines, deferral, cross-account hints; never resolves ambiguity |
| Model as a function | sandboxed, tool-less parser child of the ingest worker | masked statement text → `StatementParse` JSON |
| Agent triage | Hermes skill, `read,propose` token | proposals on open cases |
| Apply | API process, owner tap or policy engine under owner grants | one transaction per action, lineage-aware idempotency, audit, operation-specific inverse |

## 3. Processes, identities, access (D2, M10, A9, A10)

| Process | Linux user | Has | Does not have |
|---|---|---|---|
| accounting-service API | `opc` today (migration to `homehub-api` is a separate ops task, §14) | DB role `accounting`, token files | password file, Drive, MinIO write key, parser login, sudo |
| ingest worker (`homehub-statements.timer`/`.path`/`.service`, user units of the dedicated user) | `homehub-worker` | password file (root:homehub-worker 0440), rclone config with a read-only Drive token, MinIO key (put/get on bucket `homehub-statements`), API token label `worker` with scope `ingest`, `/var/lib/home-hub-statements/` 0700 | DB credentials, API's token file, `/home/opc` |
| parser child (`claude -p` in bwrap) | `homehub-worker` | `CLAUDE_CONFIG_DIR=/var/lib/home-hub-parser/claude` (owner logs in once as `homehub-worker`), stdin, tmpfs, network | everything else: `/etc` is not bound (so no managed settings, no hooks), no inbox, no keys, no repo |
| verify runner (`homehub-statements-verify.service`, run on demand) | `homehub-verify` | read-only Drive token, DB role `accounting_ro` (SELECT only, `default_transaction_read_only`), report dir `/var/lib/home-hub-verify/` 0700, the same parser login dir mounted read-only | API token, MinIO key, write DB role, the live inbox |
| Hermes | **own user `hermes` (prerequisite for any grant beyond `read,propose`)** | API token `hermes` with `read,propose` and, once it runs as its own user and the owner grants it, `enqueue` | worker execution, sudo, the worker/verify users' files |
| owner shell | `opc` | `sudo -u homehub-worker` / `sudo -u homehub-verify` for the CLI entry points (sudoers lines name the exact commands) | — |

Why the Hermes user matters (A10): today Hermes runs as `opc`, the same uid the owner uses for `sudo`; a uid cannot tell them apart, so no sudo-based broker can exclude Hermes. Until Hermes has its own user it gets `read,propose` only, and runs happen on the timer. The API also runs as `opc` today; it never has sudo and the sudoers rules are user-specific commands, but moving it to `homehub-api` is listed as an ops follow-up.

Operator evidence before real input (checklist in PR-R2's deploy notes): password file readable by `homehub-worker`, unreadable by `opc`, `hermes`, `homehub-verify`; parser child cannot read the inbox or `/etc` (bwrap test prints `ENOENT`); verify user cannot reach MinIO or the API (no key, no token, test attempts logged as refused); `hermes` cannot run the worker; the parser smoke test (§5.5) passes in the deployed sandbox. The review does not attest host state; the checklist does.

## 4. Data model (Alembic `statement_tables`)

Types follow the ledger: money `Numeric(20, 4)`, ids `Integer`, dates `date`, timestamps `timestamptz`. New enums get their own types. `ENTRY_SOURCES` gains `statement` via `ALTER TYPE … ADD VALUE`. **Downgrade refuses** while any `ledger_entry.source='statement'`, coverage, action or audit row exists (B3); the operator confirms the live head with `alembic current` before upgrade, and the migration test suite runs upgrade/downgrade from the repository head on a populated database (including A3's deletion cases).

### 4.1 `ingest_run` (D7, B1)
`id, trigger ('timer'|'owner_cli'|'enqueue'), principal (token label for enqueue; 'worker' for timer/CLI with `initiator_hint` = the unix user the worker observed, marked unauthenticated), mode ('live'|'backfill'), status ('queued'|'claimed'|'running'|'done'|'failed'), claimed_by (token label), parser_version, rules_version, policy_config_sha256, mapping_version, credential_version, requested_at, started_at, finished_at, summary JSONB`. Enqueue (`POST /statements/ingest/run`, scope `enqueue`) creates the row with `status=queued` and writes only the run id into `/var/lib/home-hub-statements/run.request`; if a run is already queued/claimed/running, enqueue returns that run (coalesced, recorded in `summary.coalesced_requests[]` with principal and time). The worker claims a run (`POST /statements/ingest-runs/{id}/claim`, scope `ingest`); every file/revision submission carries the run id and is accepted only from the claiming label. Timer and CLI runs are created by the worker itself (`trigger` timer/owner_cli). Verify never creates a run.

### 4.2 `statement_file` and `statement_source` (S3, A11)
`statement_file`: `id, sha256 (unique), kind, account_id (nullable), object_key, status ('new'|'unlocked'|'parsed'|'needs_review'|'failed'|'ignored'), failure, has_text_layer, text_chars, pages, credential_version, mapping_version, parser_version, attempts, next_retry_at, first_seen_at, parsed_at, run_id`. Content-addressed; never mutated into a different content.
`statement_source`: `id, file_id, root ('mail'|'manual'), drive_file_id, drive_path, drive_md5, first_seen_at, last_seen_at, removed_at, superseded_by_source_id`. Many sources per file (same bytes at two paths), many files per path over time (same path, corrected bytes → a new file row and a new source row; the old source gets `superseded_by`). Rename = same `drive_file_id`, new path → the source row's path is updated and the old path kept in `summary`. Deletion on Drive → `removed_at`; nothing local or in the DB is deleted.
Retry triggers: `transient` (backoff 1h/6h/24h, max 5), `password` on `credential_version` change, `mapping` on `mapping_version` change, `parse`/`guardrail` on `parser_version` change. Unchanged successful files are no-ops. Worker singleton via `flock`.

### 4.3 `account_statement`
`id, account_id, kind, currency, period_start, period_end, closing_date, due_date, opening_balance, statement_total, minimum_payment, source ('manual'|'import'), mode ('historical'|'live') immutable, status ('open'|'reconciled'), conflict_open bool, needs_recheck bool, current_revision_id, matched_count, explained_count, open_case_count, note, created_run_id`. Unique `(account_id, currency, period_end)`. Derived display status: `reconciled` ∧ `conflict_open` → shown as 已確認（有新版本待處理）; confirm is invalid while `conflict_open` or `needs_recheck` (A2, S5).

### 4.4 `statement_revision` and `statement_line` (M5, A2)
`statement_revision`: `id, statement_id, revision, file_id, parser, parser_version, raw JSONB, header fields as parsed, guardrail JSONB (server-computed), guardrail_ok, conflict, created_at, run_id`. Immutable.
`statement_line`: **immutable rows per revision**: `id, revision_id, seq, canonical_key, logical_key, txn_date, posted_date, merchant_raw, merchant_norm, printed_amount, flow_amount, foreign_amount, foreign_currency, line_kind, installment_seq, installment_total`.
- `flow_amount`: the ledger's cash-flow sign (`SIGN_BY_KIND`: expense −, income +; a card payment received +, refund +, fee/interest −, reward +; bank deposit +, withdrawal −). Derived by the server-side adapter from `line_kind` and `printed_amount` (the worker submits printed values and the parser's kind; the API derives and checks, never trusting submitted flags).
- `canonical_key` = sha256 of `(posted_date, txn_date, flow_amount, foreign_amount, foreign_currency, line_kind, installment_seq, installment_total, merchant_norm)`.
- `logical_key` = `(posted_date, flow_amount, occurrence_index)`, used only to pair candidate lines across revisions before the semantic comparison.
- `line_kind`: `purchase|refund|payment|fee|interest|reward|installment|balance_adjustment|deposit|withdrawal|transfer_in|transfer_out|unknown`.

### 4.5 `line_lineage` (A2)
`id, old_line_id, new_line_id, equivalence ('identical'|'text_only'|'changed'|'unpaired'), transferred bool, created_at`. Built at every re-parse (§5.7). Coverage, explanations and dismissals transfer only on `identical`; `text_only` (merchant text differs, everything else identical) transfers coverage but supersedes pending proposals and flags the line 文字已變更; `changed`/`unpaired` never transfer.

### 4.6 `statement_coverage` (M4, A3, A5)
`id, statement_id, line_id, entry_id (nullable), group_id (nullable), role ('principal'|'child'|'member'), snapshot JSONB (entry amount, dates, account, group, kind, parent), match_kind, match_rule, status ('active'|'stale'), stale_reason, run_id, created_at`. Unique `(entry_id) WHERE status='active' AND entry_id IS NOT NULL`. Monetary conservation invariant (A5): for every line with active coverage, `Σ snapshot.flow of its rows == line.flow_amount` exactly; the DB layer asserts it before commit.
Deletion: `entry_id`/`group_id` FKs are `ON DELETE SET NULL`; the snapshot keeps the history; a coverage row whose entry becomes null is `stale/deleted`.

**Invalidation surface (A3)** — two nets, both required:
1. **Database triggers (completeness net):** `AFTER UPDATE OF account_id, amount, original_amount, original_currency, entry_date, posted_date, kind, group_id, parent_entry_id, transfer_group_id, refunds_entry_id, settles_entry_id, is_settlement OR DELETE ON ledger_entry`, `AFTER INSERT ON ledger_entry`, `AFTER UPDATE OR DELETE ON entry_group`, `AFTER UPDATE OF opening_balance, currency, combined_account_id, closing_day, is_credit ON account` → insert into `coverage_dirty (kind, row_id, account_id, posted_date, happened_at)`. Bulk deletes (`delete_moze_entries`), schedule repost/reopen, settlement relinks, child replacement, member detachment, account configuration edits and inserts into reconciled periods are all caught here regardless of code path.
2. **Service hooks (immediacy):** `entry_write_service`, `split_service`, `transfer_service`, `settlement_service`, `schedule_posting`, `moze_import_service`, `settings_service` call `coverage.touch()` with the affected ids they already know, after their own locks, without locking statements (A6).
`coverage.sweep(run or request)` drains `coverage_dirty` at the start of every reconcile/apply/confirm and in the daily worker: changed or deleted rows → their active coverage `stale`, the line's claim released atomically (all rows of that line), the case reopened or created; inserts with `posted_date` inside a `live` statement's period or account-level changes → `needs_recheck=true` on the affected statements; in `historical` mode the sweep only updates the summary. Tests cover every path in the list above, plus historical mode producing no cases.

### 4.7 `reconciliation_case`
`id, statement_id, revision_id, kind ('line_unmatched'|'entry_unmatched'|'amount_delta'|'ambiguous'|'duplicate_claim'|'balance_gap'|'statement_conflict'|'parse_review'|'recheck'), line_id, entry_id, candidates JSONB, context JSONB, status ('open'|'proposed'|'resolved'|'dismissed'|'superseded'), explanation ('deferred_next_period'|'accepted_exception'|null), deferred_to_period_end (date), deferred_to_statement_id, resolved_by ('owner'|'policy'|null), resolved_action_id, version, created_at, resolved_at`.

### 4.8 `reconciliation_proposal`
As v2, plus `line_canonical_key`; superseded when the case version moves or the line's lineage is not `identical`.

### 4.9 `reconciliation_action` + `reconciliation_audit` (M8, A2, A8)
`reconciliation_action`: `id, idempotency_key (unique), effect_key (indexed), case_id, proposal_id, action, params, actor, run_id, status ('applied'|'reverted'|'failed'), created_entry_ids, deleted_entry_ids, touched_entry_ids, touched_group_ids, before JSONB (full snapshots of every touched row, its children and group memberships), after JSONB, inverse JSONB (operation-specific), reverted_by_action_id, created_at`.
- `idempotency_key = sha256(case_id, case_version, action, canonical params)`: a replay returns the stored result.
- `effect_key = sha256(statement identity, line canonical_key, action, effect target)`: a new action whose `effect_key` matches an `applied`, non-reverted action is refused (422 `duplicate effect`) even across revisions and cases — a corrected line cannot create a second fee child or entry for the same statement line.
- Conflict detection without version columns: the validator re-reads every touched row (and children/members) and compares with `before`; any difference → 409. `updated_at` is used as a fast pre-check only.
`reconciliation_audit`: append-only log of every state change with `actor`, `run_id`, `request_id`.

### 4.10 `policy_budget` (A6)
`(month, currency) → used_amount, used_count`, one row per month per currency, locked FOR UPDATE at the start of every policy phase; reservations are made inside the apply transaction and never reset by reparse or retry (an `effect_key` replay reuses its reservation).

### 4.11 Ledger changes
`ENTRY_SOURCES` + `statement`; `account` + `statement_password_rule`, `statement_live_from` (date, **null = never live**: historical only until the owner sets it), `statement_source_root`; `settings.reconciliation` (thresholds, policy, caps, categories, account map, issuer pattern tables, all versioned).

## 5. Ingest worker (`services/accounting-service/worker/statements/`)

### 5.1 Acquisition (A11)
No `rclone sync`. The worker lists `gdrive:財務對帳單/銀行` and `手動下載` with `rclone lsjson --hash --recursive` (fields `ID`, `Path`, `Hashes.md5`, `ModTime`, `Size`), case-insensitive `.pdf` filter, and compares `(drive_file_id, md5)` with `statement_source`. New pairs are downloaded with `rclone copyto` into `inbox/by-sha/<sha256>.pdf` (content-addressed, never deleted by the worker), registered via `POST /statements/files` (sha256, kind) and `POST /statements/sources` (file id, drive id, path, md5), and the original bytes are put in MinIO (key = sha256; idempotent). Paths no longer listed → `removed_at`. Same bytes at two paths → one file, two sources. Same path, new bytes → new file + new source superseding the old one → a new revision for the statement identity (§5.7).

### 5.2 Account mapping — as v2 (`settings.reconciliation.account_map`, versioned, `admin` scope; unmapped folders listed; manual-root files take account/period from the parsed header validated against the mapped account).

### 5.3 Unlock — as v2 (candidate order, `.altN`, `@alias`, rule grammar; winning index recorded; nothing logged).

### 5.4 Probe, extract, mask — as v2, with bounds: ≤ 40 pages, ≤ 20 MB, 60 s; extracted text ≤ 400 KB (else `needs_review/too_large`); masking of card/account numbers (last 4 kept), ID numbers (`[A-Z][12]\d{8}` and separator-tolerant digit runs ≥ 6), birth date, holder names, emails, phones, applied to text and table cells; canary tests.

### 5.5 Parse (D1, D3, B2, A10)
Regex parsers first when registered (none in v1). Fallback: the parser child.

Supported CLI version: **2.1.295** (installed; the worker refuses any other version until the pin is bumped with a passing smoke test). Command:
```
bwrap --unshare-all --share-net --die-with-parent --new-session --cap-drop ALL \
  --ro-bind /usr /usr --ro-bind /lib /lib --ro-bind /lib64 /lib64 --ro-bind <node> /node \
  --tmpfs /tmp --tmpfs /work --chdir /work --bind /var/lib/home-hub-parser/claude /cfg \
  --setenv HOME /tmp --setenv CLAUDE_CONFIG_DIR /cfg --setenv PATH /node/bin:/usr/bin \
  -- claude -p --safe-mode --tools "" --disallowedTools "mcp__*" --strict-mcp-config --mcp-config '{"mcpServers":{}}' \
     --permission-prompts none --no-session-persistence --max-turns 1 \
     --output-format json --json-schema "$(cat schema.json)" --model <pinned model id> "<fixed instruction>"
```
`/etc` is not bound inside the sandbox, so `/etc/claude-code/managed-settings.json` (managed hooks/policy) cannot load; `--safe-mode` disables user/project customisations; the empty cwd has no project instructions. **Effective-policy gate**: at worker start and after any CLI/config change, a smoke run with a canary prompt must return `num_turns == 1`, zero tool-use events in the envelope, `structured_output` matching the schema, and no file outside `/tmp` touched (bwrap read-only elsewhere); failure disables parsing (`failed/sandbox`) until an operator fixes it. Output contract: read `structured_output` only, validate with pydantic `StatementParse` (decimal strings, field caps, ≤ 2,000 lines, `is_subtotal` dropped), treat non-zero exit, `max_turns` hit, missing/invalid `structured_output`, output > 2 MB or stdin > 400 KB as `failed/parse`. 120 s per attempt, 3 attempts, process group killed and reaped on expiry, scrubbed environment. No image input in v1.

### 5.6 Submission and server-side guardrails (A1)
The worker submits `POST /statements/revisions` with the parsed header, printed lines (`printed_amount`, `line_kind`, dates, merchant, foreign fields) and the file/run ids. The API, not the worker, derives `flow_amount`, computes the guardrails, assigns `mode`, validates the account mapping and schema, and stores the computed guardrail result. Guardrails (hard; failure → `parse_review`, lines stored, no matching, no cases in historical mode):
- Exact `Decimal` equations: card `statement_total_debt == opening_debt − Σ flow_amount`; bank `closing == opening + Σ flow_amount`.
- Dates within `[period_start − 5 d, period_end + 5 d]`; period ≤ 62 d; currency equals the account's.
- Kind/sign consistency table per `line_kind`; installment `seq ≤ total`.
Arithmetic consistency is a consistency flag, not accuracy (D9).

### 5.7 Re-parse and lineage (A2)
A new revision for an existing identity: pair lines by `logical_key`, then classify each pair by `canonical_key`: identical → lineage `identical`, coverage/explanations/dismissals transfer, pending proposals stay; text-only difference → `text_only`, coverage transfers, proposals superseded, line flagged; any other difference or unpaired → no transfer, old coverage `stale/superseded`, old cases `superseded`, new line matched normally **except** that its validator refuses any action whose `effect_key` already exists (duplicate creation). Twins with identical canonical keys are interchangeable and paired in print order; a changed twin count → `parse_review`, no transfers. Header change (totals, dates) → `parse_review`. A revision for a `reconciled` statement sets `conflict_open=true` and a `statement_conflict` case; the UI qualifies the status until the owner resolves it (accept new revision → re-run; keep old → mark the new revision `rejected`).

### 5.8 Modes (M11, A9, D6)
- `live`: `account.statement_live_from` is set and `period_end ≥` it. Persisted as `mode` at creation.
- `historical`: everything else, including every account with `statement_live_from = null`. Parsing, revisions, matching and coverage persist (needed for deferral and summaries); **no cases of any kind** (guardrail failures, conflicts and invalidations are recorded as non-actionable diagnostics in `summary`), no proposals, no policy.
- `verify`: a distinct runtime (§3) with no API token, no MinIO key, read-only DB role: lists Drive, parses in memory, runs the pure matcher against a read-only snapshot, writes a report under `/var/lib/home-hub-verify/`. Zero application-state writes by construction (no write capability exists in that process); tested with policies enabled and post-cutover inputs.
- `backfill`: an ordinary worker run over history (`trigger=owner_cli`). Because it can include live periods once `statement_live_from` is set, the CLI requires `--acknowledge-live-periods` and prints the live-period count before proceeding; policy stays under its own gate (§7.3).

## 6. Matching engine

Pure `match(...) -> MatchResult`; the DB layer runs it inside one transaction per statement with the lock order of §7.4. Idempotent.

### 6.1 Populations (S2)
Participating accounts: the statement's account plus accounts whose `combined_account_id` points at it. Candidate entries: those accounts, `posted_date` (fallback `entry_date`) in `[period_start − 10 d, period_end + 10 d]`, no active coverage, excluding `balance_adjustment` and (unless the line is a reward) `reward`. Reverse population: `posted_date ∈ [period_start, period_end]`. The participating set is recomputed on every run; a change (combined membership edited) marks the statement `needs_recheck` through the account trigger.

### 6.2 Eligibility gates (A4)
Eligibility is a gate, evaluated before any scoring:

| line_kind | eligible ledger representations |
|---|---|
| payment (card) | `transfer_in` on the card whose paired leg is not a card |
| refund | `refund` entries, with or without `refunds_entry_id` (orphans allowed) |
| installment (`seq/total` present) | an entry listed in `schedule_instance.posted_entry_ids` whose instance has `seq == installment_seq` and whose definition is an installment with `times == installment_total` and whose per-instance amount equals the line; no verified instance → `line_unmatched` case with hint 分期未對應, never a generic match |
| purchase | standalone `expense` entries that are not members of any group and not listed in any `posted_entry_ids`; complete split groups (every member unclaimed); never partial groups, never installment members |
| fee / interest | `fee` / `interest` entries (standalone or children not yet covered) |
| reward | `reward` entries |
| deposit (bank) | `income`, `transfer_in`, settlement inflows (`is_settlement` on receivable/payable with positive flow), `interest` |
| withdrawal (bank) | `expense`, `transfer_out`, settlement outflows, `fee` |
| transfer_in / transfer_out (bank, when the statement names it) | the matching transfer leg |
| balance_adjustment / unknown | nothing (case) |

Same currency; `foreign_amount` compares against `original_amount`/`original_currency`.

### 6.3 Representations, scoring, decision (A4, A5)
For each line, every eligible representation is scored, **across all gates at once**: a standalone entry, a complete group (members only), a complete group with its fee/discount children, an installment instance entry, a transfer leg, a refund. Monetary conservation is part of eligibility: a representation is admissible only if `Σ flow of its rows == line.flow_amount` exactly, except the foreign representation below.

Score = `0.60 (amount exact) + 0.30 × max(0, 1 − date_distance/4) + 0.10 × Jaccard(merchant tokens)`; `date_distance` = min over (`posted_date` vs entry `posted_date`, `txn_date` vs `entry_date`). Decision per line: the best representation is accepted when `score ≥ 0.80` **and** it beats every other admissible representation of any kind by `≥ 0.15`; otherwise, if any representation scored `≥ 0.50`, the line becomes `ambiguous` and stops; otherwise it falls to the foreign and near evaluations, then to a case. A group accepted as representation reserves exactly the rows it was scored with (members, plus children only in the "with children" variant), so a separately printed fee line can still claim an uncovered child.

**Foreign representation (R6, A5):** eligible when `foreign_amount == original_amount` and currencies match, date ≤ 5 d, and `line.flow − entry.flow == −fee_expected` where `fee_expected = proposed_fx_fee(account, entry.amount)` quantised with the existing `_round_amount`/whole-unit rules (`fx_fee_rounding` is a mode, the quantum is the currency's). When the equality holds: score as above using the foreign amount, and the accepted coverage is principal + a **proposed** fee child (`attach_fee`, policy-eligible); the line is not consumed until the child exists (owner or policy), so the conservation invariant holds. When the residual is any other value → `amount_delta` case with the residual, no coverage.

**Near (R7):** merchant token overlap ≥ 0.5, `|delta| ≤ max(10 TWD, 3 %)`, ≤ 5 d → always an `amount_delta` case.

**Bank-only (R8):** `fee/interest/reward` lines matching the issuer's versioned pattern table (`年費`, `循環利息`, `現金回饋`, `跨行手續費`, …) with no eligible existing entry within ±5 d → proposal `create_system_entry`.

**Deferral (R9, B4):** reverse-population entry unmatched with `posted_date > period_end − 2 d` → explanation `deferred_next_period` with `deferred_to_period_end` computed from the account's cycle (`closing_day`, or the bank's calendar month); when a statement with that `period_end` is created the link is filled and its matcher tries deferred entries first. Confirm of the next statement first resolves/reopens its deferrals; an unmatched deferred entry reopens as `entry_unmatched` on the earlier statement **before** the next statement can be confirmed, so a confirmation is never invalidated by the reopen.

**Cross-account (R10):** as v2; `move_account` targets may be outside the participating set (typed exception in validation).

Duplicate claims are impossible (unique active entry); a second line scoring a covered row → `duplicate_claim`. Thresholds in settings, versioned.

### 6.4 Bank statements — as v2; `balance_gap = statement_total − balance_asof(period_end)` with `posted_date ≤ period_end`.

## 7. Cases, actions, policy

### 7.1 Case lifecycle — as v2, plus `recheck` cases from the sweep in live mode and `superseded` by lineage.

### 7.2 Actions (M6, M7, A5, A7, A8)
Every action: pydantic params (`extra='forbid'`), **prepare phase** outside any lock (FX rate fetch through `get_rate`, which commits its cache; category/project/account existence), then **one transaction** in the §7.4 lock order: re-validate against current rows (ownership, participating set with typed exceptions, protected-entry rules → 409, import lock, scheduled-entry restrictions, kind/currency compatibility, bounded amounts, id scoping: entry/group/line ids must come from the case's candidates/context or be created by this action; category/project/account ids are validated references), check `effect_key` (422 duplicate effect) and `before` snapshots (409 conflict), apply, write coverage with the conservation assertion, record `reconciliation_action` and audit, recompute counts.

| action | params | effect | inverse |
|---|---|---|---|
| `match` | `line_id, entry_id` or `group_id` (+ `with_children`) | coverage only; allowed on protected entries; conservation must hold | delete coverage |
| `unmatch` | `line_id` | release the line's claim | re-claim if rows unchanged |
| `adjust_amount` | `entry_id, line_id` | `amount := |line.flow|` with the ledger sign; when the line prints a foreign amount, `original_amount := foreign_amount` and `fx_rate` from the prepared rate; previous values to `before` and `description`; date unchanged **[owner]** | restore if snapshot unchanged |
| `attach_fee` | `entry_id, line_id?` | `fee := residual` where residual = `entry.flow − line.flow` (bundled, no `line_id`) or `|fee line.flow|` (separate, `line_id` of the fee line); must equal `fee_expected` within one quantum; entry has no existing `國外交易手續費` child; writes the child through the existing child path; coverage: bundled → principal + child on the purchase line; separate → child on the fee line only | delete the child if unchanged |
| `create_entry` | `line_id, kind ∈ {expense, income}, category_id, name?, project_id?` | account = statement account, amount from `flow_amount`, dates from the line, `source='statement'`, `needs_review` unless owner-chosen category | delete if unchanged |
| `create_refund` | `line_id, original_entry_id` | amount `|line.flow|`, date `posted_date`, account = statement account; original must be in the participating set with remaining refundable ≥ amount (`settlement_service` rules) | delete if unchanged |
| `create_system_entry` | `line_id, kind ∈ {fee, interest, reward}` | as v2; category from settings | delete if unchanged |
| `move_account` | `entry_id, account_id` | target any non-archived account of compatible kind/currency; existing entry-write validation; both statements re-run | move back if unchanged |
| `defer_next_period` | `entry_id` | explanation + `deferred_to_period_end` | clear |
| `split_entry` | `entry_id, parts[]` | via `split_service` conversion; `before` stores the anchor and all member/child rows | restore the anchor exactly and remove members when every member/child is unchanged, else refuse |
| `balance_adjustment_asof` | `case_id` only | owner only; account, period and closing amount are taken from the case's statement (never from params); runs in a `SERIALIZABLE` transaction with up to 3 retries, locks the account row, computes `delta = statement_closing − balance_asof(period_end)` over `posted_date ≤ period_end`, refuses zero, inserts a `balance_adjustment` dated `period_end` with `needs_review=true`; concurrent edits/deletes of summed rows abort the transaction (SSI), which retries and recomputes | delete if unchanged |
| `revert` | `action_id` | runs the stored inverse only when every touched row still equals `after` and no later applied action references its rows; refuses otherwise | — |

Tests (§12) include concurrent edit/delete during `balance_adjustment_asof`, split revert after a member edit (refuse), duplicate effect across revisions, FX prepare before locks.

### 7.3 Policy engine (D4, A6)
Shipped off; enabling per action requires the §12 money/concurrency/revision/auth fixtures green, the operator checklist signed, and the owner's review of the first complete live period. Caps: per action amount, per statement count/total, per month per currency via `policy_budget` (atomic reservation; non-TWD budgets are per currency; foreign-currency accounts stay excluded until a budget exists for their currency). Exclusions as v2. `policy.accept_agent_proposals` off; even when on, only `match` on `ambiguous` with a validator-confirmed single admissible representation.

### 7.4 Lock order (A6) — extension of D32
`import advisory key (shared) → policy_budget row(s) (FOR UPDATE, by month/currency) → account_statement row(s) (FOR UPDATE, ascending id; both statements for move_account) → schedule definition/instances (as D32) → entry_group rows (ascending) → entries with legs (one ordered statement)`. The account row keeps its existing separate class (only `balance_adjustment*` paths, before any entry lock). Existing edit paths never lock statements: they emit dirty rows (§4.6) and the next reconcile/apply/confirm sweeps them, so no cycle exists. Reconcile discovers affected statements before taking entry locks and re-checks membership after locking. Tests: edit-vs-reconcile, schedule repost-vs-reconcile, import-vs-reconcile, two statements at the monthly cap.

## 8. API (A1, B1)

| method | path | scope |
|---|---|---|
| GET statements/revisions/cases/actions (as v2) | | `read` |
| POST `/accounts/{id}/statements` (manual), `/…/reconcile`, `/…/confirm`, proposals apply/reject, case actions/dismiss, action revert | | `write` |
| POST `/reconciliation/cases/{id}/proposals` | | `propose` |
| POST `/statements/ingest/run` | enqueue only; no arguments; returns the queued/coalesced run | `enqueue` |
| POST `/statements/ingest-runs/{id}/claim`, `/statements/files`, `/statements/sources`, `/statements/revisions`, PATCH `/statements/files/{id}`, GET `/statements/files?status=` | worker only; every submission carries a run id claimed by the same label | `ingest` |
| GET/PUT `/settings/reconciliation` | | `read` / `admin` |

Confirm: atomic over the sweep, deferral resolution, current revision, `guardrail_ok`, `conflict_open=false`, `needs_recheck=false`, no open/proposed cases, no pending actionable proposals; `force` requires a note and is audited.

### 8.1 Token scopes (M9, A1)
`ACCOUNTING_TOKEN_SCOPES="spa=legacy; ops=legacy,admin; hermes=read,propose; worker=ingest"`.
- Scopes: `read`, `propose`, `write`, `admin`, `enqueue`, `ingest`, `legacy` (= today's full access, explicit).
- **Reconciliation is a gated feature**: `ACCOUNTING_RECONCILIATION_ENABLED=true` is required for the statement/reconciliation routers to be mounted at all, and it refuses to start unless `ACCOUNTING_TOKEN_SCOPES` is set, every configured label is listed, no bare tokens exist, `ACCOUNTING_API_TOKENS` is non-empty, and no label holds `legacy` together with `ingest`/`enqueue`. With the flag off the service behaves exactly as today (legacy label-only auth) and no restricted principal can exist. An unset scopes variable can therefore never grant Hermes anything: the routes it would use are not mounted.
- Enforcement on all routers via a per-route method→scope table; `ingest` cannot read ledger routes; `enqueue` cannot submit files/revisions; `propose` writes proposal rows only.
- Tests: forged revision submission from `hermes` (403), `enqueue` with arguments (422/ignored), unset scopes with the flag on (startup refusal), unknown labels, duplicate labels, `legacy+ingest` (startup refusal), every legacy mutation route against each restricted token.

## 9. Agent contract (Hermes)
### 9.1 `homehub-statements`: `POST /statements/ingest/run` (needs `enqueue`, hence the dedicated Hermes user, §3), then counts only.
### 9.2 `homehub-reconcile` (D8, B5): as v2, with the outbound DTO masked field by field through the same masker as §5.4 (`account_name` included, emails/phones/IDs/separator-tolerant digit runs), history window ≤ 180 days and ≤ 10 rows, ≤ 5 candidates, ≤ 5 cross-account rows; synthetic canary tests on every string field. Provider: Hermes keeps `openai-codex` **[owner]**; both flows stated in settings.

## 10. UI — as v2 (both route tables, sibling chip, sheet-before-form, dirty registry, focus/Escape, keyboard force-confirm with note, Caddy for all deep links), plus: the header labels the comparison 帳單應繳 vs 帳本本期 (the period measure, not today's remaining payment); a 已確認（有新版本）/ 需重新檢查 chip when `conflict_open`/`needs_recheck`; lineage flags (文字已變更) on transferred rows; historical periods show the summary table only.

## 11. Privacy and security — as v2, with §3's identities, §5.5's effective-policy gate, §8.1's feature gate, and the verify runtime's capability absence.

## 12. Testing — v2 list plus: A2 scenarios (same-count twin reorder, changed-kind same logical key, corrected date after an applied create → duplicate effect refused), A3 trigger coverage for bulk delete, schedule repost, settlement relink, child replacement, member detachment, account config edit, insert into a reconciled period, historical mode without cases; A4 collisions (group vs standalone same amount/date, installment of another plan, orphan refund, bank income/settlement lines); A5 conservation (group with separately printed fee child; foreign residual ≠ fee); A6/A7 concurrency (edit-vs-reconcile, repost-vs-reconcile, two statements at the cap, concurrent edit/delete during as-of adjustment); A8 inverses (split revert refusal, snapshot conflict); A9 verify call graph with no write capability; A11 acquisition (same path new bytes, same bytes two paths, rename, delete); B1 run ownership (submission with a foreign run id refused); B2 sandbox smoke test; B5 DTO canaries.

## 13. Rollout — as v2, plus the ops prerequisites: `homehub-worker`, `homehub-verify`, `hermes` users; sudoers entries; the parser login as `homehub-worker`; `ACCOUNTING_RECONCILIATION_ENABLED` and scopes in `.env`; operator checklist signed before `backfill`.

## 14. Decisions
Taken **[owner]**: statement-driven; parser on the Claude CLI inside the worker; Hermes keeps its provider for triage; matched = marked; amount-only adjustments; entry point 提醒中心; bank accounts in v1.
Changed after review, pending the owner's confirmation: policy ships off with caps (owner wanted on); Hermes gets `enqueue` only after it runs as its own user (owner wanted chat-triggered runs; until then the timer runs ingestion); per-account `statement_live_from` with null = historical.
Owner input needed: `statement_live_from` per account; create the `hermes` user (yes/no); schedule the API's move to `homehub-api` (yes/later).

## 15. Answer map
Rounds 1/1.1 (v2): M1 §4.4/§5.6 · M2 §6.2–6.3 · M3 §6.3/§7.2/§7.3 · M4 §4.6 · M5 §4.4–4.5/§5.7 · M6 §7.2 · M7 §7.2 · M8 §4.9/§7.2/§7.4 · M9 §8.1 · M10 §3/§5.4–5.5 · M11 §5.8 · S1 §4 · S2 §6.1/§6.3 · S3 §4.2 · S4 §10 · S5 §4.3/§4.7/§8 · N1 §6.3 · N2 §12 · D1–D3 §5.5 · D4 §7.3 · D5 §8/§8.1 · D6 §5.8 · D7 §4.1 · D8 §9.2 · D9 §5.6.
Round 2 (v3): A1 §5.6/§8/§8.1 (worker-only `ingest`, `enqueue` separate, server-side derivation, feature gate makes unset scopes impossible) · A2 §4.4/§4.5/§4.9/§5.7 (immutable lines, lineage, `effect_key`) · A3 §4.6 (triggers + hooks, nullable FKs with snapshots, account/insert triggers, historical summary-only) · A4 §6.2–6.3 (gates incl. instance-verified installments, orphan refunds, bank taxonomy, global comparison across representations, partial groups excluded) · A5 §4.6/§6.3/§7.2 (conservation invariant, children variants, foreign residual equality, quantum) · A6 §4.10/§7.4 (extended order, dirty queue instead of statement locks from edit paths, atomic budget) · A7 §7.2 (serializable as-of adjustment bound to the case's statement) · A8 §4.9/§7.2 (snapshot conflicts, split inverse, typed id exceptions, prepare phase for FX, refund derivation) · A9 §3/§5.8 (verify runtime without write capability, null live-from, historical overrides, backfill acknowledgement) · A10 §3/§5.5 (Hermes user prerequisite, `/etc` unbound + smoke gate) · A11 §4.2/§5.1 (lsjson + copyto, content-addressed, source history) · B1 §4.1 (server-owned runs, claim, coalescing) · B2 §5.5 (2.1.295, bounds, smoke test, turn-limit = failure) · B3 §4 (downgrade refusal, operator head check) · B4 §6.3 R9/§8 (period-end key, deferrals before confirm, labelled period measure) · B5 §9.2.
