# Statement reconciliation (對帳) — design

Status: draft v1.1 for Multica review (2026-10-08; owner decisions on provider, policy defaults, backfill and date handling folded in). Owner decisions taken so far are marked **[owner]**; defaults I chose are marked **[default]** and are open to change at review.

## 1. Problem

The reminder centre's 信用卡帳單 only says how much to pay; nothing checks the ledger against what the bank actually billed. The owner already has every card and bank e-statement landing in Google Drive (`財務對帳單/銀行/{信用卡,銀行帳戶}/<bank>/…pdf`, 395 PDFs across 19 account groups as of 2026-10-08, plus 48 investment statements) through a Hermes mail automation. All sampled PDFs are password-protected. What is missing is: get the lines out of those PDFs, compare them with the ledger, and surface what differs so the owner can fix the ledger with one tap, or let a bounded agent propose the fix.

Goals, in priority order:

1. Per card statement period: every statement line is either matched to a ledger entry, explained (fee, interest, reward, next period), or shown as an open case. Same for the reverse direction.
2. The ledger converges to the statement with explicit, reversible actions. Nothing silently writes money.
3. The pipeline runs unattended for new statements and can be re-run over history without duplicating anything.
4. The "brain" for the hard residue is replaceable: Hermes today, anything that can call two HTTP endpoints tomorrow.

Non-goals (v1): bank logins/scraping (the all-set-tw approach is explicitly rejected **[owner]**), OCR of scanned paper, investment statements (the portfolio service has its own broker imports), multi-currency bank accounts beyond displaying the foreign amount, chat-based approval.

## 2. Shape

Three layers, in order, each handling what the previous one could not:

| Layer | Who | Handles |
|---|---|---|
| Rules | HomeHub matching service (pure Python, deterministic) | exact/near matches, posted-date drift, FX fee, transfer fee, split groups, installments, card payments, bank-only lines (年費/利息/回饋/跨行手續費), refunds, next-period, duplicates |
| Model as a function | ingest worker calling a model with a JSON schema, no tools | PDF text → statement lines; merchant normalisation; category suggestion for unseen merchants |
| Agent triage | Hermes skill (v1) with a read-only scoped token, writing proposals only | residue: unexplained deltas, many-to-one, two plausible candidates, closing-balance gaps |

Apply = owner tap in the SPA, or the HomeHub policy engine for an allow-listed set of low-risk actions under thresholds. Every apply writes an audit row and is reversible.

## 3. Data model (accounting-service, Alembic migration `statement_tables`)

All money columns are `Numeric(18, 2)` like `ledger_entry.amount`; dates are `date`; ids are `BigInteger` identity; timestamps are `timestamptz` UTC.

### 3.1 `statement_file`
One row per PDF/CSV seen by the ingest worker. Idempotency anchor.

| column | notes |
|---|---|
| id | |
| sha256 | unique; the raw (still encrypted) file bytes |
| drive_path | `銀行/信用卡/國泰世華/….pdf` relative to the Drive folder; unique |
| object_key | MinIO key in bucket `homehub-statements` (private); the decrypted PDF is **not** stored — only the original bytes, so the password stays necessary to read it |
| account_id | nullable until mapped (see 4.2) |
| kind | `card` / `bank` from the Drive path |
| status | `new` / `unlocked` / `parsed` / `needs_review` / `failed` / `ignored` |
| text_chars, has_text_layer | probe result; `has_text_layer=false` routes to the vision path |
| parser | `model:<model id>` / `regex:<bank>` / `manual` |
| error | last error text, redacted (no statement content) |
| first_seen_at, parsed_at | |

### 3.2 `account_statement`
One row per account per period. Replaces nothing: the reminder centre keeps computing the live "應繳" from the ledger; this table is what the bank said.

| column | notes |
|---|---|
| id | |
| account_id | FK account; card or bank |
| file_id | FK statement_file, nullable (manual statements have none) |
| period_start, period_end | inclusive |
| closing_date | card: statement date; bank: last day covered |
| due_date | card only |
| currency | statement currency (TWD for every sampled issuer; HSBC/DBS may show foreign sub-totals as lines) |
| statement_total | card: 本期應繳總額; bank: closing balance |
| opening_balance | bank only; card: 上期應繳 if printed |
| minimum_payment | card only, nullable |
| source | `manual` / `import` / `agent` |
| status | `open` / `reconciled` / `disputed` |
| raw | JSONB: the parser's full output, for audit and re-matching |
| note | |
| unique (account_id, period_end) | re-ingesting the same statement updates lines in place (see 4.6) |

### 3.3 `statement_line`

| column | notes |
|---|---|
| id | |
| statement_id | FK |
| line_hash | sha256 of (statement_id, posted_date, txn_date, amount, merchant_raw, seq); unique within statement |
| seq | order as printed |
| txn_date | 消費日, nullable |
| posted_date | 入帳日 |
| merchant_raw | as printed, trimmed |
| merchant_norm | normalised (uppercase, punctuation stripped, known prefixes like `PAYPAL *`, `AMZN Mktp` collapsed); filled by the parser, overridable |
| amount | signed in statement currency: expense positive, credit/refund negative, consistent with how the ledger signs `expense` |
| foreign_amount, foreign_currency | when printed |
| line_kind | `purchase` / `refund` / `payment` / `fee` / `interest` / `reward` / `installment` / `adjustment` / `transfer_in` / `transfer_out` / `unknown`; parser's classification, rules may override |
| installment_seq, installment_total | `3/12` when printed |
| matched_entry_id | FK ledger_entry, nullable |
| match_kind | `auto` / `manual` / `agent` / `none` |
| match_rule | which rule matched (string, for the UI's "why") |
| group_id | FK entry_group when the line matched a split group as a whole |

A ledger entry can be matched by at most one line (partial unique index on `matched_entry_id` where not null). A split group can be matched by one line (its members then count as matched through the group).

### 3.4 `reconciliation_case`
One row per unresolved thing after the rules ran.

| column | notes |
|---|---|
| id | |
| statement_id | FK |
| kind | `line_unmatched` (statement has it, ledger does not) / `entry_unmatched` (ledger has it, statement does not) / `amount_delta` (matched by merchant+date but amount differs beyond tolerance) / `ambiguous` (2+ candidates with the same score) / `duplicate_entries` (2+ entries claim one line) / `balance_gap` (bank: opening + lines ≠ closing or ledger balance ≠ closing) |
| line_id, entry_id | whichever applies |
| candidates | JSONB: `[{entry_id, score, reasons[]}]` or `[{line_id, …}]` |
| context | JSONB: computed once for the agent — same-merchant entries last 6 months (ids, dates, amounts), other accounts' entries with the same amount ±3 days, FX rate used, card's `fx_fee_pct` |
| status | `open` / `proposed` / `resolved` / `dismissed` |
| resolved_by | `owner` / `policy` / `agent` (never `agent` alone: agent proposals always go through policy or owner) |
| created_at, resolved_at | |

### 3.5 `reconciliation_proposal`

| column | notes |
|---|---|
| id | |
| case_id | FK |
| action | one of §6.2 |
| params | JSONB per action |
| rationale | text ≤ 1,000 chars |
| confidence | 0–1 |
| author | token label (`reconcile`) or `policy` |
| status | `pending` / `applied` / `rejected` / `superseded` |
| created_at, decided_at | |

### 3.6 `reconciliation_audit`
Append-only: `{id, proposal_id nullable, case_id, action, params, before JSONB, after JSONB, actor, created_at}`. `before` holds the affected entries' prior field values so every apply can be reverted by a `revert` action that writes its own audit row.

### 3.7 Ledger changes
- `ENTRY_SOURCES` gains `statement` (entries created from a statement line).
- `ledger_entry.posted_date` already exists; the matcher reads it. No new entry columns.
- `account` gains `statement_password_rule` (nullable text, e.g. `id`, `id+birth`, `birth8`, `custom`) — the rule name only, never the secret.

## 4. Ingest worker (`app/statements/ingest.py`, CLI `python -m app.statements.ingest`)

Runs as a systemd user timer daily at 07:30 local (after Hermes' mail sweep) and on demand. On demand means the Hermes skill `homehub-statements` (§8) or the owner running the CLI; both execute the same command, so chat ("對帳一下") and the timer are indistinguishable in the audit. Lives in the accounting-service so it has the DB session, MinIO client and the FX service. Never imported by the API process.

### 4.1 Sync
`rclone sync gdrive:財務對帳單/銀行 <state>/inbox` (read-only on the Drive side; `--immutable`). The inbox lives in `/var/lib/home-hub-statements/` mode 700, owned by `opc`. Every file's sha256 is checked against `statement_file`; unseen files get a row with `status=new` and the original bytes are put in MinIO.

### 4.2 Account mapping
Drive subfolder → account by a YAML map in `/etc/home-hub-production/statement-accounts.yaml` (`銀行/信用卡/國泰世華: 12`, …). Unmapped folders → `status=ignored` with a once-per-folder log line; the SPA settings page lists unmapped folders so the owner can map them (writes the YAML through a settings endpoint, owner-only token).

### 4.3 Unlock
Passwords come from `/etc/home-hub-production/statement-passwords.env` (root:opc 0440, never read by the API process, never logged). Format: `<folder>=<password>` and optionally `<folder>.alt=<password>`. The worker tries the folder's values, then the account's `statement_password_rule` derivations from `STATEMENT_ID_NUMBER` / `STATEMENT_BIRTH_DATE` in the same file. Wrong password → `status=failed`, error `password`, retried next run only if the file changed. Decryption with `pypdf` (+ `cryptography` for AES). The decrypted bytes exist only in memory and in a `tmpfs` scratch dir for the vision path.

### 4.4 Probe and extract
`pdfplumber`: text chars and tables on page 1. `has_text_layer = text_chars ≥ 200`. Text path: full text with page markers plus `extract_tables()` output. Vision path: pages rendered at 150 dpi (`pypdfium2`) and sent as images. Both paths mask before leaving the box: card numbers (`\d{4}[- *]+\d{4}…` → keep last 4), ID numbers, the holder's name (from the passwords file's `STATEMENT_HOLDER_NAMES`).

### 4.5 Parse (model as a function)
One request per statement with a strict JSON schema (`StatementParse`): the §3.2 header fields plus `lines[]` with the §3.3 fields. Parser backend is pluggable (`claude-cli` / `codex-cli` / `anthropic-api`). **[owner]** v1 uses `claude-cli`: the worker shells out to `claude -p --output-format json` with the schema, authenticated by the VPS's existing Claude Code login (Max subscription, no API key in the service). Temperature 0 where the backend allows it, 3 retries on schema failure, 120 s timeout per statement, the CLI runs with `--tools ""` (no tool use) and only the masked text or page images on stdin. `anthropic-api` stays as the fallback backend for a future key-based deployment. Per-bank regex parsers (`app/statements/parsers/<bank>.py`) are tried **first** when registered and win when their self-check passes; the model is the fallback. v1 ships with no regex parsers; each gets added once three statements of that bank have parsed and the format is known.

Guardrails, all hard:
- `opening_balance + sum(lines.amount) == statement_total` for both cards and banks, with the §3.3 sign convention (purchases/fees/interest positive, payments/refunds/rewards negative; for banks deposits negative and withdrawals positive is **not** used — bank lines keep the account's own sign, deposits positive). A missing card `opening_balance` is treated as 0 only when the statement prints no 上期應繳; else `needs_review`. On failure lines are stored but **no matching runs**.
- Every line's `posted_date` within `[period_start − 5d, period_end + 5d]`.
- `len(lines) ≤ 2,000`; duplicates by `line_hash` dropped.
- The model never sees the ledger. Parsing and matching are separate steps.

### 4.6 Idempotency
Statement upsert on `(account_id, period_end)`. On re-parse: lines are diffed by `line_hash`; unchanged lines keep their matches; removed lines release their match and close their cases as `superseded`; new lines get matched. Re-running the worker on an unchanged Drive folder is a no-op.

### 4.7 Backfill mode
`--backfill --until 2026-10-31`: ingests and matches but **never** creates cases of kind `line_unmatched` for periods before the cutover (the MOZE history is known-incomplete); instead writes a per-period 對帳率 summary (`matched / total lines`) into `account_statement.raw.backfill`. The SPA shows it as a read-only table under the card's statements. Cases are produced from the first post-cutover period on.

## 5. Matching engine (`app/services/reconciliation_service.py`)

Pure function `match(statement, lines, entries, groups, account) -> MatchResult` plus a thin DB layer. Entries considered: the account's entries with `entry_date` or `posted_date` in `[period_start − 10d, period_end + 10d]`, not already matched by another statement, not `kind in ('reward',)` unless the line is a reward.

Rules run in order; a line is consumed by the first rule that produces exactly one candidate above threshold. Ties fall through to the next rule, and finally to a case.

| # | rule | candidates | consumes |
|---|---|---|---|
| R1 | payment | line_kind `payment` ↔ `transfer_in` on the card whose `transfer_group_id` out-leg is a bank account, amount equal, date ±5d | line + entry |
| R2 | exact | amount equal (same currency), `posted_date` ±3d or `txn_date` = `entry_date` ±1d | line + entry; score = 1 − date_distance/4 + merchant bonus |
| R3 | foreign | line has `foreign_amount` and entry `original_amount == foreign_amount` and `original_currency == foreign_currency`, date ±5d | line + entry; if TWD differs → **also** emits `amount_delta` with `params.delta` and `params.fee_expected = foreign × rate × fx_fee_pct` so the proposal can say "手續費" |
| R4 | group | line amount == sum of a split group's members on this account, date ±3d | line + group |
| R5 | installment | line `installment_seq/total` ↔ installment group member or schedule instance for the same plan, amount equal | line + entry |
| R6 | bank-only | line_kind in `fee/interest/reward/adjustment` and merchant_norm matches the issuer's regex table (`年費`, `循環信息`, `現金回饋`, `跨行`…) | line → **policy action** `create_system_entry` (kind = line_kind, category from `settings.statement_system_categories`) |
| R7 | near | same `merchant_norm` tokens (≥ 1 token of length ≥ 2 shared) and amount within `max(10 TWD, 3%)` and date ±5d | line + entry → emits `amount_delta` |
| R8 | refund | negative line ↔ `refund` entry with `refunds_entry_id` on this account, amount equal, ±10d | line + entry |
| R9 | next period | entry unmatched with `entry_date > period_end − 2d` | entry → `explained_next_period`, not a case |
| R10 | cross-account | entry unmatched, another account has a statement line with equal amount ±3d unmatched | entry → case `entry_unmatched` with `candidates` pointing at the other account's line, proposal hint `move_account` |

Everything still unmatched: `line_unmatched` or `entry_unmatched` cases. 2+ candidates with equal top score in R2/R7: `ambiguous`. 2+ entries claiming one line: `duplicate_entries`.

Thresholds (`settings.reconciliation`): date windows, the near tolerance, and the auto-apply allowlist (§6.3) — editable in 記帳設定 → 對帳.

Bank accounts (v1 scope: same engine, fewer rules): R1 maps to the mirror transfer leg, R2/R7/R9/R10 apply, closing balance check adds `balance_gap` when `ledger balance at period_end ≠ statement_total` after all explained items.

## 6. Cases, proposals, policy

### 6.1 Case lifecycle
`open` → (`proposed` when a proposal exists) → `resolved` (owner tap, policy apply, or an apply from a proposal) / `dismissed` (owner: "ignore this line", stored so it never reopens). Re-parsing a statement supersedes cases whose line disappeared.

### 6.2 Actions
Each action has a validator and an applier in `reconciliation_actions.py`; both the UI and the policy engine go through the same code.

| action | params | effect |
|---|---|---|
| `match` | `line_id, entry_id` or `group_id` | sets `matched_entry_id`, `match_kind` |
| `unmatch` | `line_id` | releases |
| `adjust_amount` | `entry_id, amount, original_amount?` | updates the entry's TWD amount (and `fx_rate` recomputed when `original_amount` is set); the previous amount goes to `description` as `對帳前 NT$1,234` and to the audit `before` |
| `attach_fee` | `entry_id, amount` | writes a `fee` child entry on the same account (existing `write_children` path), matches the fee line to it |
| `create_entry` | `line_id, category_id, name?, project_id?` | creates an `expense`/`income`/`refund` entry from the line, `source='statement'`, `needs_review=true` unless the category came from the owner, then matches |
| `create_system_entry` | `line_id, kind` | R6: fee/interest/reward/balance_adjustment entry, category per settings |
| `move_account` | `entry_id, account_id` | changes the entry's account (uses the existing entry-write validation incl. protected entries) and re-runs matching on both statements |
| `mark_next_period` | `entry_id` | records `explained_next_period` on the case; the entry is offered to the next statement first |
| `split_entry` | `entry_id, parts[]` | converts an entry to a split group so lines can match members (uses `PUT /entries/{id}/split`) |
| `balance_adjustment` | `account_id, amount, date` | bank only: posts through the existing balance-adjustment route, `needs_review=true` |
| `revert` | `audit_id` | restores `before` |

Entries protected today (settlements, transfer legs beyond R1, live-schedule loans, referenced originals) stay protected: actions that would touch them fail validation with the existing 409 messages, and the case stays open with the reason shown.

### 6.3 Policy engine (auto-apply)
Runs after matching, before the agent. Allowlist, each with a threshold, all default **off** except the first two **[owner: on]**:

- `create_system_entry` for R6 lines (fee/interest/reward) — on.
- `attach_fee` when R3's `delta` equals `fee_expected` within 1 TWD — on.
- `adjust_amount` for R7 deltas ≤ 30 TWD — off.
- `match` for `ambiguous` cases when the agent's proposal confidence ≥ 0.9 — off.

Every policy apply is an audit row with `actor='policy'` and shows in the proposal inbox as "已自動套用 (可還原)".

### 6.4 Agent proposals
Only `pending` proposals from the `reconcile` token label are shown in the inbox; the owner applies or rejects. A proposal must reference entries/lines that are in the case's `candidates` or `context`, or be a `create_entry`; anything else is rejected at POST with 422 so the agent cannot invent ids. One pending proposal per case per author; a new one supersedes.

## 7. API (prefix `/reconciliation`, plus statement routes under `/accounts`)

| method | path | token scope | purpose |
|---|---|---|---|
| GET | `/accounts/{id}/statements` | read | list periods with `matched/total`, status |
| GET | `/accounts/{id}/statements/{sid}` | read | header + lines (+ match info) + cases |
| POST | `/accounts/{id}/statements` | write | manual statement (header + optional lines); also what a future importer uses |
| POST | `/accounts/{id}/statements/{sid}/reconcile` | write | re-run rules + policy |
| POST | `/accounts/{id}/statements/{sid}/confirm` | write | status → `reconciled` (requires no open cases, or `force=true` with a note) |
| GET | `/reconciliation/cases?status=open&account_id=` | read | cases with candidates and context |
| POST | `/reconciliation/cases/{id}/proposals` | propose | agent/owner proposal |
| POST | `/reconciliation/proposals/{id}/apply` | write | owner apply |
| POST | `/reconciliation/proposals/{id}/reject` | write | |
| POST | `/reconciliation/cases/{id}/actions` | write | direct owner action without a proposal |
| POST | `/reconciliation/cases/{id}/dismiss` | write | |
| GET | `/reconciliation/audit?statement_id=` | read | |
| POST | `/reconciliation/audit/{id}/revert` | write | |
| GET/PUT | `/settings/reconciliation` | read/write | thresholds, allowlist, system categories, unmapped folders |
| POST | `/statements/ingest/run` | write | enqueue a worker run (writes a flag file the timer honours; the API never runs the worker in-process) |

### 7.1 Token scopes (`app/auth.py`)
New env `ACCOUNTING_TOKEN_SCOPES`: `label=scope[,scope]` list, e.g. `hermes=read,propose; spa=read,write; ops=read,write,admin`. Scopes: `read` (GET on everything but `/settings/*` secrets), `propose` (POST proposals only), `write` (everything the SPA does today), `admin` (settings writes, ingest run). A label without a scope entry keeps today's behaviour (full access) so the current SPA/API tokens keep working; the Hermes token gets `read,propose`. Enforced by a dependency on each router; 403 `{"detail": "scope"}` when missing. Covered by unit tests per route group.

## 8. Agent contract (Hermes skill `homehub-reconcile`)

- Two Hermes skills, both thin wrappers around repo-owned scripts: `homehub-statements` runs the ingest worker CLI and reports "N files, M parsed, K need review, cases opened"; `homehub-reconcile` is the triage below. Trigger: Hermes cron daily 08:00 after the worker, or the owner saying 對帳 in chat (runs both in sequence).
- Steps the skill script runs (deterministic shell/python inside the skill, not free-form tool use): `GET /reconciliation/cases?status=open` → for each case build a prompt from `kind`, the line/entry, `candidates`, `context` (ids kept, amounts and merchants included, nothing else from the ledger) → model answer in the proposal schema → `POST …/proposals` → summary message to the gateway: "N cases, M proposals, K need you".
- The skill never calls `write` routes; its token cannot.
- Limits: ≤ 50 cases per run, ≤ 2 proposals per case, rationale ≤ 1,000 chars, confidence required.
- Swap: the skill's prompt + schema live in the repo (`docs/agents/reconcile-skill.md`); running it under Claude Code headless or a plain cron with the Anthropic SDK is the same two endpoints.
- Provider: Hermes' current provider is `openai-codex` and stays **[owner]**. Case text (merchant names, amounts, dates of the involved candidates) therefore goes to OpenAI for triage; statement parsing does not (it runs on the Claude CLI inside the worker).

## 9. UI (Angular, `components/accounting/reconciliation/`)

Entry point **[default]**: 提醒中心 → 信用卡帳單 row gets a `對帳 k/n` pill (n = lines, k = matched or explained) and, when the bank statement for the period exists, the statement's 應繳 next to the ledger's. Tap → `/accounting/accounts/:id/statements/:sid` (new route; Caddy regex extended like `/group` was).

Statement page, phone-first like the rest of the ledger UI:
- Header: period, 應繳 (bank) vs 帳本 (ledger), delta in the warning tone, status chip, `全部確認` (disabled while cases are open, long-press → force with note).
- Three segments: 已對帳 / 僅帳單有 / 僅帳本有, each a list using `entry-row` styling; a matched row shows the "why" (`match_rule`) on tap.
- Case row actions are the §6.2 actions as a bottom sheet (same `sheet.scss` pattern): candidates first ("配對到 10/03 全聯 −580"), then 新增支出 (opens the entry form prefilled from the line, category suggestion first), 下期, 忽略, 移到其他卡.
- Proposal inbox: `/accounting/reconciliation` lists pending proposals across accounts; each shows rationale + confidence; 套用 / 拒絕; auto-applied rows show 還原.
- Settings: 記帳設定 → 對帳: thresholds, allowlist toggles, system-entry categories, unmapped Drive folders → account map, "立即執行" (ingest run).
- Backfill table under the card's statements: period × 對帳率, read-only.
- Bank accounts: same statement page; header shows 期末餘額 (statement) vs 帳本餘額; `balance_gap` case offers 餘額調整.

## 10. Privacy and security

- Statement PDFs never enter the repo, the demo DB, or Multica's reach; the Multica implementation uses fixtures only (synthetic PDFs generated by a script in `tests/fixtures/statements/`, with a fake bank layout).
- Passwords, ID number, birth date, holder names: one root:opc 0440 file, read by the worker only; the API process has no path to it; nothing is logged.
- Masking before any model call (§4.4); the matcher never calls a model; the agent gets ids + amounts + merchants of the involved candidates, not the ledger.
- MinIO bucket private, server-side encryption on, objects named by sha256; the SPA never links to raw PDFs in v1.
- Scoped tokens (§7.1); the Hermes token can propose, not write.
- Owner financial data in reviews and PR descriptions only as aggregates (counts, rates), consistent with the existing rule.

## 11. Testing

- Matching: table-driven unit tests per rule with synthetic statements; property test that `match` is idempotent and that re-running after `unmatch` reproduces the same result.
- Parser: schema validation + guardrail tests with fixture texts for three synthetic bank layouts (text, table-heavy, vision-only); the model call is mocked in CI; a `--live` marker runs one real call locally.
- Worker: sync/unlock/probe with generated encrypted PDFs (pypdf can write them); idempotency across runs; backfill mode produces no `line_unmatched` cases before the cutover.
- API: scope enforcement per route group; proposal POST rejects ids outside the case; apply/revert round-trip restores `before`.
- Frontend: Vitest for the statement page states (no statement, open cases, all matched, force confirm), the sheet actions, the inbox, the settings page; screenshots at 390/760/1280 from the 18080 demo with fixture statements.

## 12. Rollout and PR stack

1. **PR-R1 backend core** (owner session, needs Postgres): migration, models, matching service + rules R1–R10, actions, policy engine, routes, token scopes, openspec `accounting-reconciliation` + `accounting-ledger` deltas. Tests above.
2. **PR-R2 ingest worker** (owner session): sync/unlock/probe/parse/upsert, backfill mode, systemd timer unit, settings file formats. Runs once in `--backfill` against the real Drive folder on the VPS after merge; the result table is the acceptance check.
3. **PR-R3 frontend** (Multica): statement page, pill, inbox, settings, routes, Caddy regex note for the release.
4. **PR-R4 Hermes skill** (owner session): skill files + the token with `read,propose`; first run on real cases after R2's backfill reaches the first post-cutover period.
5. Release: one SPA publish + backend restart + migration, same procedure as the split releases; `ACCOUNTING_TOKEN_SCOPES` added to `.env` by the owner.

## 13. Decisions and open points

Taken:
- Statement-driven, no bank logins **[owner]**.
- Matched entries are marked, not locked; editing a matched entry's amount/date unmatches it and reopens a case **[default, MOZE behaviour]**.
- Card/bank statement is the source of truth for amounts; `adjust_amount` keeps the prior amount in the description and audit **[default]**.
- Entry point in 提醒中心 **[default]**; a card's account page links to the same statement list.
- Hermes is the v1 triage agent through the proposal contract; the brain is swappable **[owner]**.
- Bank accounts included in v1 with the same engine; investment statements out.

Decided by the owner on 2026-10-08:
1. Parser runs on the Claude CLI (subscription) inside the worker; Hermes wraps the worker for chat access and keeps its OpenAI provider for triage.
2. Policy defaults: `create_system_entry` and `attach_fee` on; everything else off.
3. Backfill: the full Drive history is ingested now as a verification run (parse accuracy = parsed total vs printed total, match rate per period), cases only from statements whose period ends after `2026-10-31`.
4. `adjust_amount` changes the amount only; `entry_date` is never touched by reconciliation (no setting for it in v1).

Open for the reviewer (Multica):
- Rule order and thresholds in §5; anything that can mis-match money silently.
- Whether the `line_hash` definition survives re-parses with a different parser (merchant_raw spacing) — proposal: hash on `(posted_date, amount, seq)` only.
- Scope model in §7.1 vs. the existing label-only tokens.
- Anything in §9 that fights the current phone layout rules (dirty-form registry, group view, sheet focus handling).
