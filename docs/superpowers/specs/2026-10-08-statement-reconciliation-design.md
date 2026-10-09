# Statement reconciliation (對帳) — design

Status: **v2** for Multica review (2026-10-09). v2 answers review rounds 1 (M1–M11, S1–S5, N1–N2) and 1.1 (D1–D9) on AGENT-83; the answer map is in §15. Owner decisions are marked **[owner]**; defaults I chose are **[default]**.

## 1. Problem

The reminder centre's 信用卡帳單 only says how much to pay; nothing checks the ledger against what the bank billed. The owner's Hermes mail automation already lands every card and bank e-statement in Google Drive:

| Drive path (under `財務對帳單/`) | content (2026-10-09) |
|---|---|
| `銀行/信用卡/<issuer>/` | 14 card folders, emailed e-statements, `YYYY-MM_<issuer>_…pdf` |
| `銀行/銀行帳戶/<bank>/` | 12 bank folders, emailed e-statements |
| `手動下載/國泰世華/` | 12 monthly Cathay 綜合月結單, `YYMM.pdf`, owner-downloaded (Cathay does not email them) |
| `投資/`, `電子發票/` | out of scope (portfolio service has broker imports; e-invoice CSV is a later feature) |

496 bank PDFs + 12 manual. Every PDF is password-protected. A read-only spike on one or two files per folder unlocked every folder with the owner's password rules and found a text layer in every file (pdfplumber), so **v1 has no OCR and no vision path**. Two folders use uppercase `.PDF`. 台新 changed its password rule in 2024-12 (two candidates).

Goals, in priority order:
1. Per statement period: every statement line is matched to a ledger object, explained (bank fee, interest, reward, deferred to next period) or an open case; same in the reverse direction.
2. The ledger converges to the statement only through explicit, validated, reversible, audited actions. Nothing writes money without the owner's grant.
3. The pipeline runs unattended for new statements and is idempotent over history.
4. The triage "brain" is replaceable: Hermes today, anything that can call two HTTP endpoints later.

Non-goals (v1): bank logins/scraping (the all-set-tw approach is rejected **[owner]**), OCR/vision, investment and e-invoice files, chat-based approval, historical ledger repair (match/summary only before the per-account live period).

## 2. Shape

| Layer | Runs as | Handles |
|---|---|---|
| Rules | API process, `reconciliation_service` (pure, deterministic) | specialised identities first (payment, refund, installment, group), then exact, foreign, near; bank-only lines; deferral; cross-account hints; never auto-resolves ambiguity |
| Model as a function | parser child of the ingest worker, sandboxed, tool-less | masked statement text → `StatementParse` JSON; nothing else |
| Agent triage | Hermes skill with a `read,propose` token | proposals on open cases; cannot write the ledger, cannot run ingestion |
| Apply | API process only, owner tap or the policy engine under owner grants | one transaction per action, audit row, operation-specific inverse |

## 3. Processes, identities, access (D2, M10)

| Process | Linux user | Has | Does not have |
|---|---|---|---|
| accounting-service API (pm2) | `opc` (today) | DB role `accounting` (full), `ACCOUNTING_API_TOKENS`, `ACCOUNTING_TOKEN_SCOPES` | password file, Drive, MinIO write, parser login |
| ingest worker (`homehub-statements.timer`/`.service`, systemd user unit of a dedicated user) | `homehub-worker` (new system user, no login shell) | `/etc/home-hub-production/statement-passwords.env` (root:homehub-worker 0440), rclone config with a **read-only** Drive token, MinIO key scoped to bucket `homehub-statements` (put/get), API token with scope `ingest` (§8.1), `/var/lib/home-hub-statements/` (0700) | DB credentials (never talks to Postgres), the API's token file, `/home/opc` |
| parser child (`claude -p`) | `homehub-worker`, inside `bwrap` | its own `CLAUDE_CONFIG_DIR=/var/lib/home-hub-parser/claude` (owner logs in once as this user, subscription), stdin = masked text, a private tmpfs `/tmp`, network | password file, inbox, MinIO, API token, worker env (scrubbed to `PATH`, `HOME=/tmp`, `CLAUDE_CONFIG_DIR`), any repo checkout (cwd = empty tmpfs) |
| Hermes | `opc` (today) | API token with scopes `read,propose` and, separately granted, `ingest` (§8.1) | worker execution, worker user, any file above |
| owner CLI (`verify`, `backfill`) | `opc` via `sudo -u homehub-worker` | same as worker; `verify` additionally uses a read-only DB role `accounting_ro` | — |

All ledger writes happen in the API process, through the same lock order as every existing write path (D32: import advisory key → schedule definition → instances → entry groups ascending → target entries with transfer legs in one ordered statement). The worker is an API client; it never opens a DB session. Operator evidence required before real input: `sudo -u homehub-worker cat` of the password file succeeds, the same as `opc` fails, the parser child cannot read the inbox (bwrap test), and the API user cannot read the password file. Tests: synthetic injection PDFs whose text says "run tools / read credentials" produce schema-only output; a secret canary string placed in the inbox never appears in parser output or logs.

## 4. Data model (accounting-service, Alembic `statement_tables`)

Types follow the ledger: money `Numeric(20, 4)`, ids `Integer` identity (existing FKs are `Integer`), dates `date`, timestamps `timestamptz`. New enums get their own PostgreSQL types with upgrade/downgrade; `ENTRY_SOURCES` gains `statement` via `ALTER TYPE entry_source ADD VALUE` (downgrade recreates the type after rewriting rows to `manual`). FKs from audit tables are `ON DELETE RESTRICT` so history cannot vanish; entries referenced by coverage can still be deleted by the existing routes, which triggers coverage invalidation (§4.6). The migration is tested from the real schema head (`c4e8b2f1a7d3`) up and down.

### 4.1 `ingest_run` (D7)
`id, trigger ('timer'|'owner_cli'|'hermes'|'api'), principal (token label or unix user), mode ('live'|'verify'|'backfill'), parser_version, rules_version, policy_config_sha256, mapping_version, credential_version, started_at, finished_at, summary JSONB, status`. Every statement revision, case, proposal, policy apply and audit row references the run that produced it. `verify` runs never create this row (§6.7); they write a report file instead.

### 4.2 `statement_file`
`id, sha256 (unique), source_root ('mail'|'manual'), drive_path (unique), object_key, account_id (nullable), kind ('card'|'bank'), status ('new'|'unlocked'|'parsed'|'needs_review'|'failed'|'ignored'), failure ('password'|'no_text_layer'|'parse'|'guardrail'|'mapping'|'transient'), has_text_layer, text_chars, pages, credential_version, mapping_version, parser_version, attempts, next_retry_at, first_seen_at, parsed_at, run_id`.
Retry triggers (S3): a file is retried when its `failure` is `transient` (backoff 1h/6h/24h, max 5), or when the matching version changed: `credential_version` (sha256 of the password file) for `password`, `mapping_version` (sha256 of the account map) for `mapping`, `parser_version` for `parse`/`guardrail`. An unchanged, successful file is a no-op. Drive deletions are recorded (`status=ignored`, `failure=null`, `drive_path` kept) and never delete statements; a rename is a new path with the same sha256 → the existing row's `drive_path` is updated. Upload-success/DB-failure: the object key is the sha256, so a retry is idempotent. One worker at a time: `flock` on `/var/lib/home-hub-statements/.lock`.

### 4.3 `account_statement`
`id, account_id, kind, currency, period_start, period_end, closing_date, due_date, opening_balance, statement_total, minimum_payment, source ('manual'|'import'|'agent'), mode ('historical'|'live') immutable at creation (§6.7), status ('open'|'reconciled'|'disputed'), current_revision_id, matched_count, explained_count, open_case_count, note, created_run_id`.
Identity: unique `(account_id, currency, period_end)`. A re-ingest of the same identity is a new **revision**, never an overwrite (§4.4). A manual statement later covered by an import, or two files for the same identity with different bytes, creates revision N+1 with `conflict=true` and a `statement_conflict` case; nothing transfers automatically while the conflict is open.

### 4.4 `statement_revision` (M5)
`id, statement_id, revision (1..n), file_id (nullable), parser, parser_version, raw JSONB (full parser output), header fields as parsed, guardrail_ok, conflict, created_at, run_id`. Immutable. Lines belong to a revision.

### 4.5 `statement_line`
`id, revision_id, seq, logical_key, txn_date, posted_date, merchant_raw, merchant_norm, printed_amount, flow_amount, foreign_amount, foreign_currency, line_kind, installment_seq, installment_total`.
- `printed_amount`: as the issuer prints it (card: charges positive, credits negative; bank: as printed).
- `flow_amount` (M1): the **account cash-flow sign the ledger uses** (`SIGN_BY_KIND`: expense −, income +; a card payment received is + on the card; a refund is +; fees/interest −; rewards +; bank deposit +, withdrawal −). Derived once by the parser adapter from `line_kind` and `printed_amount`, verified by the guardrail equation, and the only amount the matcher compares. A statement amount never becomes a ledger amount directly; actions compute from `flow_amount` plus the action's own validation.
- `logical_key` = `(posted_date, flow_amount, occurrence_index)` where `occurrence_index` numbers identical `(posted_date, flow_amount)` pairs in print order. Whitespace or merchant text changes keep the key; an inserted line shifts only the occurrence index of identical later twins, which is exactly the uncertain case (§4.4 diff).
- `line_kind`: `purchase|refund|payment|fee|interest|reward|installment|balance_adjustment|transfer_in|transfer_out|unknown` (`adjustment` renamed to the ledger kind, M3).

### 4.6 `statement_coverage` (M4)
One row per **ledger row** a line covers: `id, statement_id, line_id, entry_id, role ('principal'|'child'|'member'), group_id (nullable), match_kind ('auto'|'manual'|'agent'), match_rule, status ('active'|'stale'), run_id, created_at`. Unique `(entry_id) WHERE status='active'` — one active claim per ledger row across all statements, groups and individual matches. A line matched to a split group reserves every member (and their fee/discount children) as `member`/`child` rows in one insert, so neither another line nor another statement can claim a member. Invalidation: the existing write paths (entry update/delete, `split_service` member add/update/remove/dissolve, `transfer_service`, settlement) call `coverage.invalidate(entry_ids)` inside their transaction; it marks the rows `stale`, clears the line's match, reopens or creates the case, and bumps the statement counts. A metadata-only `match` on a protected entry (transfer leg, settlement, live-schedule loan) is allowed and still invalidates on edit.

### 4.7 `reconciliation_case`
`id, statement_id, revision_id, kind ('line_unmatched'|'entry_unmatched'|'amount_delta'|'ambiguous'|'duplicate_claim'|'balance_gap'|'statement_conflict'|'parse_review'), line_id, entry_id, candidates JSONB, context JSONB, status ('open'|'proposed'|'resolved'|'dismissed'|'superseded'), explanation ('deferred_next_period'|'accepted_exception'|null), deferred_to_statement_id, resolved_by ('owner'|'policy'|null), resolved_action_id, version, created_at, resolved_at`. `resolved_by` is never `agent` (S5): an agent proposal is applied by the owner or by policy, and the audit row says which.

### 4.8 `reconciliation_proposal`
`id, case_id, case_version, action, params JSONB, rationale (≤1,000), confidence, author (token label or 'policy'), status ('pending'|'applied'|'rejected'|'superseded'), created_at, decided_at, applied_action_id`. One pending proposal per (case, author); a newer one supersedes. A proposal whose `case_version` is behind the case is `superseded` on read and cannot be applied.

### 4.9 `reconciliation_action` + `reconciliation_audit` (M8)
`reconciliation_action`: `id, idempotency_key (unique), case_id, proposal_id, action, params, actor ('owner'|'policy'), run_id, status ('applied'|'reverted'|'failed'), created_entry_ids int[], deleted_entry_ids int[], touched_entry_ids int[], before JSONB, after JSONB, inverse JSONB, reverted_by_action_id, created_at`. `reconciliation_audit` is the append-only log of every state change (actions, matches, invalidations, confirms, dismissals, forced confirms with note) with `actor`, `run_id`, `request_id`.

### 4.10 Ledger changes
- `ENTRY_SOURCES` + `statement`.
- `account` + `statement_password_rule` (rule name only), `statement_live_from` (date, nullable: first period end treated as live, S2/M11), `statement_source_root` (`mail`/`manual`).
- Settings (`settings.reconciliation`): thresholds, allowlist, caps, system categories, account map (§5.2).

## 5. Ingest worker (`services/accounting-service/worker/statements/`, CLI `python -m worker.statements`)

Separate package from `app/`; shares only the schemas module. Timer daily 07:30 local; on-demand via the API's enqueue (§8.1) which touches `/var/lib/home-hub-statements/run.request` watched by a `.path` unit; owner CLI directly.

### 5.1 Sync
`rclone sync` of `gdrive:財務對帳單/銀行` and `gdrive:財務對帳單/手動下載` into `inbox/mail` and `inbox/manual` with a read-only Drive token, `--immutable`, never deleting locally. Globs are case-insensitive (`.pdf`/`.PDF`). Each unseen sha256 → `statement_file(new)` via `POST /statements/files` (scope `ingest`) and `PUT` of the original bytes to MinIO (bucket private, SSE on, key = sha256).

### 5.2 Account mapping
`settings.reconciliation.account_map`: `{ "mail/信用卡/國泰世華": 12, "manual/國泰世華": 7, … }`, edited in 記帳設定 (scope `admin`), versioned (`mapping_version`). Unmapped folders: `status=ignored, failure=mapping`, listed in settings; mapping them re-queues their files (S3). Manual-root files carry no period in the name; account and period come from the parsed header, validated against the mapped account's `kind`.

### 5.3 Unlock
Password file format: `<root>/<folder>=<value>`, `.alt`, `.alt2`… extra candidates, `@<other folder>` alias, identity fields `STATEMENT_ID_NUMBER`, `STATEMENT_BIRTH_DATE`, `STATEMENT_HOLDER_NAMES`; value grammar `$ID`, `$ID[a:b]`, `$BIRTH8|6|4`, `$BIRTH{DDMMYY…}`, `+` concat, literal. Candidates are tried in order; the winning index is recorded, not the value. `pypdf` + `cryptography`. Decrypted bytes live only in memory and the bwrap tmpfs. Wrong password → `failed/password`, retried on `credential_version` change (S3). Nothing from this file is ever logged; errors say `password`.

### 5.4 Probe and extract
`pdfplumber` text + tables, page cap 40, byte cap 20 MB, 60 s. `text_chars < 200` on page 1 → `needs_review/no_text_layer` (fail closed; no vision in v1). Masking before anything leaves the worker: card/account numbers (keep last 4), the ID number, birth date, holder names, email addresses, phone numbers; a canary test proves the masks apply to both text and table output.

### 5.5 Parse (model as a function; D1, D3)
Regex parsers per bank (`worker/statements/parsers/<bank>.py`, versioned) run first when registered and win when their self-check passes; v1 ships none. Fallback: the parser child.

Command (pinned CLI version, checked at start; any other version → `failed/parse`):
```
bwrap --unshare-all --share-net --die-with-parent --new-session --cap-drop ALL \
  --ro-bind /usr /usr --ro-bind /lib /lib --ro-bind /lib64 /lib64 --ro-bind <node> /node \
  --tmpfs /tmp --tmpfs /work --chdir /work --bind /var/lib/home-hub-parser/claude /cfg \
  --setenv HOME /tmp --setenv CLAUDE_CONFIG_DIR /cfg --setenv PATH /node/bin:/usr/bin \
  -- claude -p --safe-mode --tools "" --disallowedTools "mcp__*" --strict-mcp-config --mcp-config '{"mcpServers":{}}' \
     --permission-prompts none --no-session-persistence --output-format json --json-schema <StatementParse schema> \
     --model <pinned> --max-turns 1 "<fixed instruction>"
```
`--safe-mode` disables CLAUDE.md, skills, plugins, hooks, MCP servers and custom agents while keeping subscription auth (`--bare` would drop it); `--tools ""` removes built-ins and `--disallowedTools "mcp__*"` plus the empty strict MCP config deny MCP; cwd is an empty tmpfs so no project instructions exist. The masked text goes on stdin; the schema is the only output contract: the worker reads `structured_output` from the JSON envelope, validates it with the pydantic `StatementParse` model (field length caps, ≤ 2,000 lines, decimal strings), and treats a missing/invalid `structured_output`, non-zero exit, auth error, or output over 2 MB as `failed/parse`. Limits: 120 s per attempt wall clock, 3 attempts, the whole process group killed on expiry (`start_new_session`, `killpg`), stdout/stderr capped while streaming. Environment scrubbed to the three variables above. Images are not an input in v1 (no stdin encoding is defined); the `anthropic-api` backend is a separately configured, owner-authorised fallback for a future key-based deployment.

### 5.6 Guardrails (hard; any failure → `needs_review`, lines stored on the revision, **no matching, no cases except `parse_review`**)
- Equations in flow terms, exact `Decimal`, no rounding: card `statement_total_debt == opening_debt − Σ flow_amount` (charges reduce flow, payments increase it); bank `closing == opening + Σ flow_amount`. Printed subtotals/section totals are excluded from lines by the schema (`is_subtotal` lines are dropped before the sum).
- `posted_date ∈ [period_start − 5 d, period_end + 5 d]`; `period_end − period_start ≤ 62 d`; `currency` equals the mapped account's currency or the statement is `needs_review`.
- `line_kind` and sign consistency: a `payment` must have `flow_amount > 0` on a card, etc. (table in the parser adapter, tested per kind).
- Installment lines must carry `installment_seq ≤ installment_total`.
Parsed-total agreement is reported as **arithmetic consistency**, not accuracy (D9); accuracy is measured against golden synthetic fixtures per bank layout (§12).

### 5.7 Revisions and diff (M5)
A new revision for an existing statement identity is diffed against the current revision by `logical_key`:
- same key → the line id is kept; coverage, cases, proposals stay.
- key only in the new revision → new line, matched normally.
- key only in the old revision → the old line is retired: its active coverage becomes `stale`, its cases `superseded`, pending proposals `superseded`; applied actions keep their links (`reconciliation_action.line_logical_key`) and are **never** re-executed or reversed automatically.
- any change in the count of identical `(posted_date, flow_amount)` twins, or a header change (totals, dates) → `parse_review` case and no automatic transfer of matches; the owner confirms which revision is current.
Statements already `reconciled` get a new revision only with `conflict=true` and a `statement_conflict` case; status does not change until the owner acts.

### 5.8 Modes (M11, D6)
- `live`: for statements whose `period_end ≥ account.statement_live_from` (inclusive) **[owner picks per account]**; full pipeline. The decision is persisted as `account_statement.mode` at creation and never changes on rerun.
- `historical`: `period_end < statement_live_from`: parse, revisions, matching and coverage are persisted (needed for cross-period deferral), but **no cases, no proposals, no policy**, and the UI shows only the per-period summary (matched/explained/unmatched counts, consistency flag). Historical repair is a separate, later, owner-authorised operation.
- `verify` (owner CLI only, `python -m worker.statements verify --until <date> --report <path>`): zero application-state writes — read-only DB role, no MinIO upload, no `statement_file` rows, parsing in memory, pure matching against a read-only snapshot, report written to the given path under the worker's directory, never to the DB or Hermes. Tested with both policies enabled and post-cutover inputs: zero rows change.
- `backfill` (owner CLI, explicit): same as `live`/`historical` by period, used once after R2 merges to load the Drive history; it is an ordinary authorised ingest, not the verify run.

## 6. Matching engine (`app/services/reconciliation_service.py`)

Pure `match(revision, lines, candidate_entries, groups, account_set, settings) -> MatchResult`, DB layer applies it inside one transaction per statement with the D32 lock order (statement row FOR UPDATE → groups → entries). Idempotent: re-running on an unchanged revision yields the same coverage.

### 6.1 Candidate population
Participating accounts (S2): the statement's account plus every account whose `combined_account_id` points at it (child cards billed on the master). Entries of those accounts with `posted_date` (fallback `entry_date`) in `[period_start − 10 d, period_end + 10 d]`, without an active coverage row, excluding `reward` entries unless the line is a reward, excluding `balance_adjustment`. The reverse population (entries that should appear on this statement) is narrower: `posted_date ∈ [period_start, period_end]`.

### 6.2 Compatibility matrix (M2)
A line may only match entries whose kind/direction agrees:

| line_kind | entry kinds | direction |
|---|---|---|
| purchase | expense; split group of expenses; installment group member | flow − |
| refund | refund (`refunds_entry_id` set) | flow + |
| payment (card) | transfer_in on the card whose paired leg is a bank/cash account | flow + |
| transfer_in / transfer_out (bank) | transfer_in / transfer_out on this account | sign equal |
| fee / interest | fee / interest entries (children or standalone) | flow − |
| reward | reward entries | flow + |
| installment | member of an `installment` group / posted schedule instance entry | flow − |
| unknown | none (case) | — |

Same currency as the account; `foreign_amount` compares against `original_amount`/`original_currency`.

### 6.3 Rules, in order
Each rule produces scored candidates from the compatible set. A line is consumed when exactly one candidate has `score ≥ 0.80` and beats the runner-up by `≥ 0.15`; otherwise, if any candidate scored `≥ 0.50`, the line becomes an `ambiguous` case **and stops** (no later rule, no policy); if none, the next rule runs.

Score = `amount_term + date_term + text_term`, with `amount_term = 0.60` for exact `flow_amount` equality (else the rule does not apply, except R7), `date_term = 0.30 × max(0, 1 − |posted_date − entry.posted_date| / 4)` (`txn_date` vs `entry_date` when both exist, best of the two), `text_term = 0.10 × Jaccard(tokens(merchant_norm), tokens(entry.merchant ∪ entry.name))` with tokens of length ≥ 2 after normalisation.

| # | rule | specifics |
|---|---|---|
| R1 payment | card `payment` line ↔ `transfer_in` on the card whose paired `transfer_out` leg is not a card; amount exact; ±5 d | metadata-only coverage (transfer legs stay protected) |
| R2 refund | `refund` line ↔ `refund` entry; orphan refunds (original deleted) stay candidates | |
| R3 installment | line with `installment_seq/total` ↔ entry in an `installment` group (schedule posting creates one per instance) whose definition matches by plan total and `seq`; amount exact | plan identity = `schedule_definition.id` carried on the group |
| R4 group | `purchase` line `flow_amount` == Σ members of a split group on the account set; ±3 d | reserves all members + children |
| R5 exact | compatible kinds, amount exact, scoring as above | |
| R6 foreign | `foreign_amount == original_amount` and currency equal, ±5 d; TWD difference `delta = line.flow − entry.flow` recorded | emits `amount_delta` with `fee_expected = proposed_fx_fee(account, entry.amount)` (existing helper: `|amount| × fx_fee_pct / 100` with `fx_fee_rounding`) and whether the entry already has an `國外交易手續費` child |
| R7 near | same `merchant_norm` token set overlap ≥ 0.5, `|delta| ≤ max(10 TWD, 3 %)`, ±5 d; amount_term scaled by `1 − |delta|/tolerance` | always a case (`amount_delta`), never auto |
| R8 bank-only | `fee/interest/reward` lines whose `merchant_norm` matches the issuer's versioned pattern table (`年費`, `循環利息`, `現金回饋`, `跨行手續費`, …) **and** no compatible existing entry within ±5 d | proposal `create_system_entry` (policy-eligible, §7.3); `balance_adjustment` lines are never auto-created |
| R9 deferral | reverse population entry unmatched with `posted_date > period_end − 2 d` | `explanation=deferred_next_period`, `deferred_to_statement_id` = next period once it exists; the next statement's matcher tries deferred entries first; if the next statement is reconciled without them the case reopens as `entry_unmatched` |
| R10 cross-account | reverse population entry unmatched; another account **outside the participating set** has an unmatched line with equal `flow_amount` ±3 d | `entry_unmatched` case with `move_account` hint; never for combined child cards |

Leftovers: `line_unmatched` / `entry_unmatched`. Two active claims on one row cannot happen (unique index); a second line scoring an already-covered entry gets `duplicate_claim`. Thresholds live in `settings.reconciliation` and are versioned (`rules_version`).

### 6.4 Bank statements
Same engine; R1 maps to the mirrored transfer leg, R2/R5/R7/R9/R10 apply; after matching, `balance_gap = statement_total − ledger_balance_asof(period_end)` where the ledger balance uses `posted_date ≤ period_end`; a non-zero gap after explained items opens a `balance_gap` case (owner-only action, §7.2).

## 7. Cases, actions, policy

### 7.1 Case lifecycle
`open` → `proposed` (pending proposal exists) → `resolved` (owner/policy apply, or an explanation) / `dismissed` (owner: `accepted_exception`, stored durably; a dismissed arithmetic gap stays visible in the header delta) / `superseded` (revision retired the line). Counts and statement status are recomputed after every apply, unmatch, invalidation, reparse and revert (S5).

### 7.2 Actions (M6, M7)
Every action has a pydantic params schema with `extra='forbid'`, a validator run at proposal time **and again at apply time** against current rows, and an applier that runs in one transaction with the D32 lock order, writes `reconciliation_action` (idempotency key = `sha256(case_id, case_version, action, canonical params)`; a repeat returns the existing result) and an audit row. Validation always checks: case/statement/line ownership, account ∈ participating set, currency, protected-entry rules (409 as today), import lock, scheduled-entry restrictions, category/project existence and kind compatibility, bounded amounts, all ids (including nested) belong to the case's candidates/context or are newly created by this action.

| action | params | effect | inverse |
|---|---|---|---|
| `match` | `line_id, entry_id` or `group_id` | coverage rows only; allowed on protected entries | delete coverage |
| `unmatch` | `line_id` | coverage → stale | re-insert if rows unchanged |
| `adjust_amount` | `entry_id, line_id` | sets `amount` (and `original_amount` when the line prints a foreign amount, `fx_rate` recomputed by the existing FX path); prior values into `before` and `description` (`對帳前 NT$…`); date never changes **[owner]** | restore if version unchanged |
| `attach_fee` | `entry_id, line_id?, fee_amount` | `fee_amount == fee_expected` (± `fx_fee_rounding` unit), entry has no `國外交易手續費` child, `0 < fee ≤ 5 % of |amount|`; writes the fee child via the existing child path; with `line_id` (separate fee line) covers it; without (bundled) the purchase line's coverage gains the child row | delete the created child |
| `create_entry` | `line_id, kind ∈ {expense, income}, category_id, name?, project_id?` | amount from `flow_amount`, `source='statement'`, `needs_review=true` unless the category came from the owner; covers the line | delete the created entry if untouched |
| `create_refund` | `line_id, original_entry_id` | via `settlement_service` refund creation (checks remaining refundable amount); covers the line | delete if untouched |
| `create_system_entry` | `line_id, kind ∈ {fee, interest, reward}` | category from settings; only when R8 found no compatible existing entry; covers the line | delete if untouched |
| `move_account` | `entry_id, account_id` | existing entry-write validation; re-runs matching on both statements | move back if untouched |
| `defer_next_period` | `entry_id` | sets explanation and the forward link | clear |
| `split_entry` | `entry_id, parts[]` | `PUT /entries/{id}/split` semantics (ids in `parts` must be categories/projects; amounts sum to the entry) | dissolve if members untouched |
| `balance_adjustment_asof` | `account_id, period_end, line? ` | owner only, never policy: in one transaction, lock the account row, compute `delta = statement_closing − balance_asof(period_end)` from `posted_date ≤ period_end`, refuse zero, write a `balance_adjustment` entry dated `period_end` with `needs_review=true`; does not use the existing today-relative route | delete if untouched |
| `revert` | `action_id` | runs the recorded inverse; refuses when any touched row's version changed since, when created rows were edited, or when a later action depends on it | — |

Concurrency (M8): proposals carry `case_version`; apply fails with 409 if the case or statement revision moved; two applies of the same key return one result; owner edits racing a worker reconcile are serialised by the statement row lock. Tests: concurrent apply, stale proposal after reparse, reparse-vs-apply interleaving, revert after an intervening edit (must refuse), double submit of `attach_fee`.

### 7.3 Policy engine (M3, D4)
Runs after matching, before the agent, only on `live` statements, only when `settings.reconciliation.policy.enabled` and the action is individually enabled with caps. **Shipped off** for every action; the owner enables `create_system_entry` and `attach_fee` after the first live period has been reviewed by hand and the adversarial fixtures (§12) pass. This defers the owner's "on" choice rather than overriding it (§14). Caps when enabled: per action max amount (default 500 TWD for fees, 2,000 TWD for system entries), per statement max count (5) and total (5,000 TWD), per month aggregate (20,000 TWD); anything over → proposal for the owner. Policy never touches `ambiguous`, `amount_delta` from R7, `balance_gap`, `statement_conflict`, `parse_review`, historical statements, or `balance_adjustment` lines. Consuming agent proposals (`policy.accept_agent_proposals`) is a separate explicit grant, off, and even then only for `match` on `ambiguous` with validator-confirmed single eligibility; confidence is informational, never authorisation.

### 7.4 Agent proposals
Only `pending` proposals from a `propose`-scoped label appear in the inbox. A proposal is validated at POST (schema, ids within candidates/context, action allowed for the case kind) and again at apply.

## 8. API

| method | path | scope | purpose |
|---|---|---|---|
| GET | `/accounts/{id}/statements` | read | periods with counts, mode, status |
| GET | `/accounts/{id}/statements/{sid}` | read | header, current revision lines with coverage, cases |
| GET | `/accounts/{id}/statements/{sid}/revisions` | read | |
| POST | `/accounts/{id}/statements` | write | manual statement (header + optional lines) |
| POST | `/accounts/{id}/statements/{sid}/reconcile` | write | re-run rules (+ policy if enabled) |
| POST | `/accounts/{id}/statements/{sid}/confirm` | write | atomic: current revision, `guardrail_ok`, no open/proposed cases, no pending actionable proposals, delta explained; `force=true` requires `note` and is audited |
| POST | `/statements/files` | ingest | worker registers a file (sha256, path, root) |
| POST | `/statements/revisions` | ingest | worker posts a parsed revision (header + lines + guardrail result); the API creates/links the statement, runs matching and policy, returns counts |
| PATCH | `/statements/files/{id}` | ingest | status/failure updates |
| GET | `/statements/files?status=` | ingest, read | retry queue |
| POST | `/statements/ingest/run` | ingest | enqueue a `live` run (fixed arguments; no mode/config overrides); 202 with run id |
| GET | `/reconciliation/cases?status=&account_id=` | read | cases with candidates + context DTO (§9.2) |
| POST | `/reconciliation/cases/{id}/proposals` | propose | |
| POST | `/reconciliation/proposals/{id}/apply` | write | |
| POST | `/reconciliation/proposals/{id}/reject` | write | |
| POST | `/reconciliation/cases/{id}/actions` | write | direct owner action |
| POST | `/reconciliation/cases/{id}/dismiss` | write | `accepted_exception` with note |
| GET | `/reconciliation/actions?statement_id=` | read | |
| POST | `/reconciliation/actions/{id}/revert` | write | |
| GET/PUT | `/settings/reconciliation` | read / admin | thresholds, policy, caps, categories, account map, live-from dates |

### 8.1 Token scopes (M9, D5) — `app/auth.py`
`ACCOUNTING_TOKEN_SCOPES="spa=legacy; ops=legacy,admin; hermes=read,propose,ingest; worker=ingest,read"`.
- Scopes: `read` (GET on ledger and reconciliation routes, not settings secrets), `propose` (POST proposals only), `write` (every mutation the SPA performs today except settings), `admin` (settings writes), `ingest` (the worker routes and the enqueue route only), `legacy` (= everything, explicit).
- **Fail closed**: when `ACCOUNTING_TOKEN_SCOPES` is set, every configured label must appear in it, bare tokens are rejected at startup, an unknown or duplicate label is a startup error, and `ACCOUNTING_API_TOKENS` must be non-empty (no unauthenticated mode with scopes on). When unset, behaviour is unchanged (today's label-only auth) and the deploy notes migrate the SPA/ops tokens to `legacy` explicitly.
- Enforcement is a router-level dependency applied to **all** routers (`entries`, `accounts`, `splits`, `transfers`, `schedules`, `imports`, `settings`, `balance-adjustments`, statements, reconciliation) with a per-route method→scope table; 403 `{"detail": "scope"}`.
- `read,propose` can write proposal rows only; it cannot mutate the ledger, cannot run ingestion, cannot change settings. `ingest` is a separate explicit grant: it can register files/revisions and enqueue a `live` run, nothing else; the enqueue takes no arguments. Hermes gets `ingest` only if the owner wants chat-triggered runs; otherwise the timer alone.
- Tests: the restricted tokens against every legacy mutation route, settings, ingest, apply, revert; malformed/missing mappings refuse startup.

## 9. Agent contract (Hermes skills)

### 9.1 `homehub-statements` (trigger + report)
`POST /statements/ingest/run` with the `ingest` token, poll `GET /statements/files?run_id=` counts, post to the gateway: "run <id>: N new files, M parsed, K needs review, C cases opened". Counts only; no filenames, no amounts, no parser output, no stdout. Verification runs are not reachable from Hermes.

### 9.2 `homehub-reconcile` (triage)
Cron after the worker, or on the owner's 對帳 in chat. Script-driven (not free-form tool use): `GET /reconciliation/cases?status=open` → per case the **outbound DTO** (D8): `case.kind`, the line (`posted_date, txn_date, flow_amount, foreign_amount+currency, merchant_norm, line_kind`), the entry (`posted_date, entry_date, amount, original_amount+currency, merchant, name, kind`), ≤ 5 candidates (same fields + `score, reasons`), ≤ 10 same-merchant history rows (`posted_date, amount` only), ≤ 5 cross-account equal-amount rows (`account_name, posted_date, amount`), FX rate and `fx_fee_pct`. Nothing else: no descriptions, tags, counterparties, invoice numbers, ids beyond the ones needed to reference candidates. Merchant/name text is re-masked (holder names, digits ≥ 6) before leaving. → model answer in the proposal schema → `POST …/proposals` (≤ 50 cases/run, ≤ 2 proposals/case) → gateway summary with counts. The prompt, schema and DTO builder live in the repo (`docs/agents/reconcile-skill.md`, `worker/agent/dto.py`) so the same skill runs under Claude headless or a cron with the SDK.
Provider: Hermes keeps `openai-codex` **[owner]**; the DTO above is therefore what reaches OpenAI. Parsing never does (Claude CLI inside the worker). Both flows are stated in the settings page's privacy note. Synthetic outbound-payload tests assert the DTO field set.

## 10. UI (Angular, `components/accounting/reconciliation/`)

Routes in **both** tables (phone and wide, derived from the single list PR #62 introduces): `accounts/:id/statements` (list), `accounts/:id/statements/:sid` (detail), `reconciliation` (inbox), `settings/reconciliation`. Caddy `@hub_spa` regex extended for all four deep links in the release notes. No raw PDF route.

- 提醒中心 → 信用卡帳單 row: the row stays a single button; a sibling `對帳 k/n` chip (not nested) links to the statement detail; when a statement exists for the period its 應繳 shows next to the ledger's, labelled 帳單 / 帳本.
- Statement detail (phone-first): header (period, 帳單 vs 帳本, delta in warning tone, mode chip `歷史`/`對帳中`/`已確認`, revision badge when conflict), three segments 已對帳 / 僅帳單有 / 僅帳本有 using `entry-row`; a matched row's tap shows `match_rule` and the covered rows; a case row's tap opens the action bottom sheet (candidates first, then 新增支出 / 退款 / 下期 / 移到其他卡 / 忽略). The sheet closes before any form opens; the entry form is reached through the existing group view when the target is a group (no stacked detail); return state goes back to the statement with the same scroll position (`listOpenState` pattern). Every draft registers with `DirtyFormRegistry`; focus trap + return and Escape order follow `schedule-sheet`. `全部確認` is a normal button: disabled with reason when cases are open; `強制確認` is a separate keyboard-accessible action that opens a note dialog.
- Inbox `/accounting/reconciliation`: pending proposals across accounts (rationale, confidence, author), 套用 / 拒絕; policy applies listed with 還原.
- Settings → 對帳: thresholds, policy toggles + caps (with the "shipped off" explanation), system categories, account map with unmapped folders, per-account 對帳起算期, privacy note (which data goes to which provider), 立即執行 (enqueue).
- Historical periods: per-period summary table only (matched / explained / unmatched, consistency ✓/✗), no actions.
- Bank accounts: same detail; header 期末餘額 vs 帳本餘額; `balance_gap` offers 餘額調整（截至期末）.

## 11. Privacy and security
- PDFs never enter the repo, the demo DB or Multica's reach; Multica implements against synthetic fixtures (`tests/fixtures/statements/`, generated encrypted PDFs with fake layouts).
- Secrets and isolation per §3; nothing from the password file or statement text is logged; worker logs carry file ids and counts only.
- Masking in the worker before the parser child (§5.4); DTO minimisation before triage (§9.2); the parser child is tool-less, MCP-less, instruction-less, filesystem-isolated (§5.5).
- Scoped tokens fail closed (§8.1); policy is off until the owner enables it with caps (§7.3); every money action is validated twice, idempotent, audited, reversible (§7.2).
- MinIO bucket private with SSE; objects keyed by sha256; the SPA never links to raw files.
- Owner financial data in reviews, PRs and Multica issues only as aggregates.

## 12. Testing
- Rules: table-driven unit tests per rule and per compatibility-matrix cell; adversarial fixtures: equal same-day purchases (must be `ambiguous`), split member vs standalone candidates, installment of a different plan with equal amount, orphan refund, bundled vs separate FX fee, duplicate claim, combined child card spend, deferral then reopen, cross-account hint on a child card (must not fire); property test: `match` idempotent and stable under line reordering.
- Revisions: whitespace-only reparse keeps every match; inserted twin → `parse_review`, no transfers; retired line supersedes its case, applied action untouched; reconciled statement + changed file → conflict.
- Actions: each action's validator (ownership, ids, protected entries, caps), idempotency key replay, revert refusal after intervening edit, `attach_fee` double submit, `balance_adjustment_asof` with later income (the M6 example: period-end 100, later +50, statement 110 → +10 dated period end).
- Concurrency: two applies, worker reconcile vs owner edit, stale proposal after reparse.
- Auth: scope matrix over every router; startup refusal on malformed mappings; `read,propose` token cannot mutate; `ingest` token cannot read entries.
- Worker: unlock with candidate order and aliases, retry on credential/mapping/parser version change, flock singleton, case-insensitive globs, canary masking (text and tables), parser child: schema failure, timeout kill, oversize output, injection fixture → schema-only output; `verify` mode leaves zero rows changed with policies on.
- Parser accuracy: golden synthetic fixtures for three layouts (line, table-heavy, multi-page) with field-level diffs, plus the arithmetic consistency flag.
- Frontend: Vitest for the detail states, sheet actions, inbox, settings, routes in both tables, dirty-form registration, focus trap; screenshots 390/760/1280 on the 18080 demo with fixture statements.
- openspec: `accounting-reconciliation` spec in the existing SHALL/scenario style with the adversarial scenarios above; `accounting-ledger` delta for `statement` source and coverage invalidation.

## 13. Rollout and PR stack
1. **PR-R1 backend core** (owner session, Postgres): migration, models, scopes (fail-closed), matching + rules, actions, policy engine (off), routes, openspec, tests.
2. **PR-R2 worker** (owner session): worker package, parser child, verify/backfill/live modes, systemd units for `homehub-worker`, password/mapping formats, deploy notes incl. the operator evidence checklist (§3). After merge: `verify` over the Drive history → report; owner sets `statement_live_from` per account; `backfill`.
3. **PR-R3 frontend** (Multica): routes, detail, inbox, settings, chip; Caddy note.
4. **PR-R4 Hermes skills** (owner session): the two skills, tokens `hermes=read,propose[,ingest]`.
5. Release: migration + backend restart + SPA publish; `.env` gains `ACCOUNTING_TOKEN_SCOPES` with the SPA/ops labels on `legacy`.

## 14. Decisions
Taken by the owner: statement-driven, no bank logins; parser on the Claude CLI inside the worker; Hermes wraps the worker for chat access and keeps its OpenAI provider for triage; matched entries marked not locked; `adjust_amount` amount-only; entry point 提醒中心; bank accounts in v1 on the same engine.
Changed in v2 after review, pending the owner's confirmation: policy actions **ship off** (owner wanted `create_system_entry` + `attach_fee` on); they are enabled per action with caps after the first live period is reviewed. The owner's choice is kept as the intended steady state.
Owner input still needed: `statement_live_from` per account (first complete ledger period, likely the first period ending after the 2026-10-31 cutover); whether Hermes gets the `ingest` scope.

## 15. Answer map to AGENT-83 findings
| finding | answer |
|---|---|
| M1 signs | §4.5 `flow_amount`, §5.6 equations, §6.2 matrix, §7.2 actions compute from flow |
| M2 rule order / thresholds | §6.2–6.3: specialised rules first, explicit score/threshold/margin, ambiguity stops, installment via definition id, orphan refunds kept, fixtures §12 |
| M3 fee formula / coverage | §6.3 R6 uses `proposed_fx_fee`; §7.2 `attach_fee` bundled vs separate, child guard, bounds, idempotency key; R8 searches existing rows, `balance_adjustment` never auto; policy off §7.3 |
| M4 group coverage | §4.6 coverage table, global unique active claim, invalidation hooks on all split/entry writes |
| M5 revisions | §4.4, §4.5 logical key, §5.7 diff rules, conflict handling, applied actions never replayed |
| M6 balance adjustment | §7.2 `balance_adjustment_asof` (as-of delta, account lock, owner only) |
| M7 validation | §7.2 per-action schemas, ownership/id checks at proposal and apply, `create_refund` separate, metadata-only match |
| M8 concurrency / revert | §4.9, §7.2 single transaction, versions, idempotency keys, operation-specific inverses, tests §12 |
| M9 scopes | §8.1 fail closed, all routers, `legacy` explicit, `ingest` separate, no agent authority via confidence |
| M10 isolation / masking | §3 process matrix, §5.4 masking + canary, §5.5 bwrap parser, no vision in v1, mapping via settings not `/etc` |
| M11 backfill | §5.8 modes, per-account `statement_live_from` persisted as `mode`, historical = no cases/policy |
| S1 schema | §4 types, enum migration, FK restrict, head-to-head migration tests |
| S2 accounts / deferral | §6.1 participating set, R9 durable deferral + reopen, R10 excludes children, period measure 帳單 vs 帳本 |
| S3 retries | §4.2 versions and triggers, flock, rename/delete |
| S4 UI | §10 both route tables, return state, sheet-before-form, dirty registry, focus, force-confirm dialog, sibling chip, Caddy for all deep links |
| S5 states | §4.7 explanation fields, `resolved_by` owner/policy only, §8 confirm atomic checks |
| N1 | `循環利息` |
| N2 | §12 openspec scenarios |
| D1 CLI hardening | §5.5 `--safe-mode`, `--tools ""`, `--disallowedTools mcp__*`, strict empty MCP config, empty cwd, own config dir, bwrap |
| D2 process matrix | §3 |
| D3 I/O contract | §5.5 `--json-schema` + `structured_output` + pydantic validation, caps, timeouts, process-group kill, no images |
| D4 policy safety | §7.3 shipped off, caps, exclusions, fixtures |
| D5 Hermes write path | §8.1 `ingest` as a separate grant, enqueue with no arguments, worker runs as its own user |
| D6 verify zero-write | §5.8 `verify` |
| D7 provenance | §4.1 `ingest_run`, every row carries `run_id`, verify reports external |
| D8 outbound DTO | §9.2 |
| D9 metric | §5.6 arithmetic consistency vs §12 golden accuracy |
