# Composite bank statement split (國泰世華 綜合月對帳單) — design

Status: rev 3 for Multica re-review, 2026-10-18 (rev 1 `d1de321` CHANGES-REQUESTED in AGENT-153, rev 2 `0680f1d`
CHANGES-REQUESTED in AGENT-154; §10 maps every finding to its change). Base: main `5636f5a` (R6b + R3a deployed). Follows the statement reconciliation design
(`2026-10-08-statement-reconciliation-design.md`) and R6 (`2026-10-15-reconciliation-r6-design.md`).

## 1. Problem

The manual Drive folder `手動下載/國泰世華/` holds twelve 國泰世華 綜合月對帳單 (`YYMM.pdf`, 2025-10 … 2026-09). Each
PDF covers several deposit accounts of one customer: two TWD 活期存款 with transaction sections, one TWD 子帳戶 and two
外匯綜合活期存款 (JPY, USD) without sections, plus a 持有明細 table with every account's closing balance. The pipeline
assumes one folder → one HomeHub account → one statement per file, so all twelve files sit at `ignored / mapping` and the
owner's 國泰 accounts have no statement side to reconcile against.

Verified on `2609.pdf` (text layer, 4 pages, 27 tables, no OCR): sections `活期存款 帳號 ……91` (屏東分行, ~16 rows/month)
and `活期存款 帳號 ……59` (四維分行, ~12 rows/month); row shape `YYYY/MM/DD 交易說明 幣別 提出 存入 餘額` with exactly one of
提出/存入 filled and a running balance; holdings rows `活期存款 ……91 屏東分行 <balance>`, `子帳戶 ……68 屏東分行 0`,
`外匯綜合活期存款 ……50 屏東分行 JPY <balance>` / `… USD <balance>`. Page breaks (`\f`, kept by `pdf.extract`) fall inside
the holdings table and inside transaction sections; the page number is glued to the first token of the next page
(`1持有明細`, `2活期存款 帳號 …`, `32026/09/08 …` — a page digit in front of a dated row), the column header
`日期 交易說明 幣別 提出 存入 餘額` repeats after a break, and pdfplumber returns a section's table as two tables.

Owner mapping (2026-10-18): `91/TWD` → 國泰 (67); `59/TWD` → 國泰薪轉戶 (76); `68/TWD` → 國泰子 (68); `50/JPY` →
出國用日幣 (81); `50/USD` → no HomeHub account, skipped. 國泰高雄 (117) is the securities side and is out of scope (its
market-value binding to the portfolio service is a separate issue).

## 2. What already works (no change)

`_file_statement_id` and the statement identity (`uq_account_statement_identity` = account, currency, period_end) are
already per account: one file can back N statements if N different `account_id`s are submitted. `period_blocker`,
bank guardrails (`opening_present`, `equation` opening + Σ flow = statement_total, `currency`, `period_length` ≤ 62 days,
`dates_in_window` ±5 days, `line_count` ≤ 2000), `becomes_current`, live/historical per account
(`account.statement_live_from`), retire and the UI need nothing. No database migration. `parser_version`, `INSTRUCTION`
and the `StatementParse` schema do not change.

The one-folder-one-account assumption lives in exactly four places, all changed below:

1. `worker/pipeline.py` `process_file`: `account_id = account_map.get(_folder_key(listed))`, one `_parse_submit` per file.
2. `worker/parse_schema.py`: `StatementParse` is one statement per model call (kept; the worker calls it per section).
3. `settings_service._check_account_map` / `AccountMapOut`: a folder maps to one int.
4. `statement_revision_service._assert_folder_maps` only accepts `account_map[folder] == payload.account_id`, and
   `submit_revision` overwrites the singular `statement_file.account_id`.

## 3. Settings: `composite_map`

New top-level key in `reconciliation_settings.data`, beside `account_map` (which is unchanged):

```json
"composite_map": {
  "manual/國泰世華": {
    "91/TWD": {"account_id": 67},
    "59/TWD": {"account_id": 76},
    "68/TWD": {"account_id": 68, "anchor_only": true},
    "50/JPY": {"account_id": 81, "anchor_only": true}
  }
}
```

- Folder key: same grammar as `account_map` keys (`manual/<folder>` or `mail/<folder>/<subfolder>`, no empty or
  padded parts). A folder may appear in `account_map` or `composite_map`, never both (422 `composite_map`).
- Section key: `^[0-9]{2,4}/[A-Z]{3}$` — the digits the bank prints after the masked run of the account number
  (the visible suffix), then the currency. Distinct keys may share a suffix (the FX account prints one number for
  every currency).
- Value: `{"account_id": int, "anchor_only": bool (default false)}`. The account must exist, be non-archived, have
  `is_credit = false` and a `currency` equal to the key's currency (422 `composite_map` naming the key otherwise).
  `anchor_only: true` declares that this account never prints a transaction section in this statement (子帳戶, FX
  accounts); `false` declares that it always does. The worker fails closed on either mismatch (§4). Changing the flag
  is an operator decision, not an inference from one file.
- **Target uniqueness (AGENT-153 #5):** within one folder every `account_id` appears at most once (422 `composite_map`
  naming both keys). Two sections for one account would otherwise land as two revisions of one statement, because
  the file-backed identity is (file, account, currency) and knows nothing of section keys.
- `mapping_version` = sha256[:16] of the canonical JSON (sorted keys) of `{"account_map": …, "composite_map": …}`.
  Any change to either map re-queues every `failure = mapping` file (existing `retry_due` rule). **Accepted one-time
  effect (AGENT-153 #6):** the envelope itself changes the hash on deploy, so the first poll after the deploy re-tries
  every `mapping` failure once (today 33 files: the 12 國泰 files and the 21 `銀行帳戶/臺灣銀行一般` files, which fail
  `mapping` again and stay ignored). Later map edits keep the same fan-out semantics as today: all `mapping` failures,
  never `parse` / `guardrail` / `parsed` files. Tests pin key-order stability, the empty-map upgrade hash and that no
  other failure class is re-queued.
- `GET /statements/account-map` returns `composite_map` beside `account_map`, `mapping_version`, `closing_days`. The
  worker's `ApiClient.account_map()` tolerates its absence (older API) as it does for `closing_days`.
- `PUT /settings/reconciliation` (admin) accepts `composite_map` in `ReconciliationSettingsIn`; the PUT replaces it
  wholesale like the other keys. Defaults: `{}`.

## 4. Worker: deterministic split

New module `worker/composite.py` (standard library + the existing `pdf.Extracted`). No model involvement in the split,
and no guessing: every ambiguity is a `SplitError`, never a drop or a first-match.

```python
@dataclass(frozen=True)
class Row:
    txn_date: date
    description: str
    currency: str
    withdrawal: Decimal | None   # 提出
    deposit: Decimal | None      # 存入
    balance: Decimal             # 餘額

@dataclass(frozen=True)
class Section:
    key: str                     # "91/TWD"
    currency: str
    period_start: date
    period_end: date
    closing_balance: Decimal     # from 持有明細; equals rows[-1].balance when rows exist
    opening_balance: Decimal     # rows[0].balance + withdrawal - deposit, or closing_balance for anchor sections
    rows: tuple[Row, ...]        # () for anchor sections
    text: str                    # the section's own text lines (header + rows), "" for anchor sections
    tables: list[list[str]]      # the table rows attributed to this section, same order as rows

def split(extracted: pdf.Extracted, declared: dict[str, bool]) -> tuple[list[Section], SplitReport]
    # declared = {section key: anchor_only} from composite_map[folder]
```

### Page normalisation (AGENT-153 #1)

Before any recognition the text is normalised page by page: `\f` is removed; a leading page digit is stripped from a
line when the remainder matches a known shape (`^\d(?=持有明細$)`, `^\d(?=交易明細$)`, `^\d(?=(?:活期存款|外匯綜合活期存款) 帳號 )`,
`^\d(?=\d{4}/\d{2}/\d{2} )`); a repeated column header `日期 交易說明 幣別 提出 存入 餘額` is dropped; the page footer /
header lines the fixture shows (the bank's note lines, `台幣存款 餘額 … 相較上月 …`, `外幣存款 餘額折合台幣 …`) are
recognised by shape and dropped only inside the transaction area. A section that is cut by a page break therefore
continues on the next page; a second header with the same key (the bank repeats it after a break) merges into the
open section instead of opening a new one; a header with a different key closes the current section.

### Recognition rules (whole-line regexes on the normalised text)

- Period: the first line matching `綜合月對帳單 (\d{4}/\d{2}/\d{2}) ~ (\d{4}/\d{2}/\d{2})`. Missing → `SplitError`.
- Holdings: between `持有明細` and `交易明細`, every line matching
  `^(活期存款|子帳戶|外匯綜合活期存款) (\S+) (\S+分行) (?:([A-Z]{3}) )?(-?[\d,]+(?:\.\d+)?)$`.
  Suffix = the digits after the last `*` of the printed number (the whole number when it has no `*`); currency = the
  optional code, else `TWD`. Key = `suffix/currency`. Duplicate key → `SplitError`. A declared key with no holdings
  row, or a holdings key with rows and no declaration, → `SplitError` (undeclared keys without rows are skipped and
  counted, e.g. `50/USD`).
- Transaction area: from `交易明細` to the end of the deposit part (the first line of the securities / card parts, by
  their printed titles). A header `^(?:活期存款|外匯綜合活期存款) 帳號 (\S+)$` opens or continues a section. Every line
  in the area that starts with `\d{4}/\d{2}/\d{2} ` is a dated row and **must** belong to the open section; a dated
  row outside any section → `SplitError`.
- Rows come from the tables, not from the text (AGENT-153 #1, currency and columns): a table whose header row is
  `日期 | 交易說明 | 幣別 | 提出 | 存入 | 餘額` (cells, after whitespace normalisation) is a transaction table; each data
  row gives `Row` with the currency from its own cell and the amounts from their own cells (empty cell → None;
  exactly one of 提出/存入 must be set, else `SplitError`). Attribution of a table row to a section: the section
  whose text rows contain a line with the same date, the same amount token and the same balance token. Zero matches
  or more than one match → `SplitError` (no first-match). A text row with no table row → `SplitError`. A section's
  currency is the one its rows carry; rows of mixed currency → `SplitError`; the key's currency must match.
- Declaration check: a section with rows whose key is declared `anchor_only` → `SplitError`; a declared
  `anchor_only: false` key without a section → `SplitError`. Declared `anchor_only: true` keys become anchor sections.
- Balance chain (AGENT-153 #2): for a section with rows, in document order,
  `balance[i] == balance[i-1] - withdrawal[i] + deposit[i]` for every i ≥ 1 (Decimal, exact),
  `opening = balance[0] + withdrawal[0] - deposit[0]`, and `balance[-1] == holdings closing`. Any violation →
  `SplitError` naming the key and the row index (no amounts in the message). Anchor sections:
  `opening = closing = holdings balance`.
- `SplitReport`: `sections`, `anchor`, `skipped_keys`, `rows_per_key`. Counts and keys only; never amounts, merchants
  or account numbers.

`SplitError` is a parse failure of the file (`failed / parse`, §4 file status), never a crash of the run.

### Masking and the model call

For each section with rows the worker builds `pdf.Extracted(text=section.text, tables=section.tables, …)` and
masks it with the unchanged `Masker.render` (the account number in the header becomes `[NUM…3456]****91`; the
transaction rows carry no card codes, so `_TXN_ROW` never fires). It prepends one operator line in the existing
`[hint]` family (the instruction already says the input may begin with a `[hint]` line, so `INSTRUCTION`, the
schema and therefore `parser_version` do not change — no global re-queue):

```text
[hint] This is one deposit-account section of a composite bank statement (國泰世華 綜合月對帳單). kind is bank.
Statement period 2026-09-01 ~ 2026-09-30, currency TWD, account suffix 91. opening_balance is 10,000.00 and
statement_total (closing balance) is 12,345.00; the section has 16 transaction rows. Report every row of this
section and nothing else.
```

Then `services.parser.parse(masked)` as today, one call per section, same `STDIN_CAP`. OCR never applies: composite
folders require a text layer; a file without one is `failed / no_text_layer` (no OCR fallback — OCR output has no
reliable section structure).

**Header enforcement (AGENT-153 #2):** the model's output is accepted only when `kind == "bank"`,
`currency == section.currency`, `period_start/period_end` equal the header period, `opening_balance ==
section.opening_balance`, `statement_total == section.closing_balance` and `len(lines) == len(section.rows)`.
Otherwise the section fails with reason `header_mismatch` (a parse failure, §4 file status) — the worker never
patches the model's header to the derived values, so a disagreement is visible, not hidden.

**Row-by-row enforcement (AGENT-154 R1):** a matching header and total do not prove the lines (two deposits of 10
and 20 reported as 15 and 15 balance; a dropped 10 plus a duplicated 10 on another date balance). So, before
posting, the worker compares the model's lines with `section.rows` **in order**: for every index i, the line's
`txn_date` must equal `rows[i].txn_date` and its signed amount (`printed_amount` with the sign the line's `line_kind`
implies under the bank sign rule: deposit / transfer_in / interest / refund / reward positive, withdrawal /
transfer_out / fee / purchase / payment negative) must equal `+deposit` or `-withdrawal` of `rows[i]`; the line's
`seq` must be `i + 1`. Sequence equality keeps duplicates (two identical rows need two identical lines in the same
positions). Any difference fails the section with reason `line_mismatch` (a parse failure). Merchant text is not
compared (it is the model's reading of the masked description). The server-side `equation` guardrail stays as the
second, independent check; it is no longer the only one.

Anchor sections skip the model: the worker posts `lines = []`, `opening_balance = statement_total = closing_balance`,
period from the header, `parser = "composite-anchor"`, the current `parser_version`, `raw = {"section": key,
"anchor": true}`. The server's guardrails accept this (equation holds with zero flow; `line_count` has no minimum).

### Posting, section outcomes and file status (AGENT-153 #3)

One `POST /statements/revisions` per section, same `file_id`, `account_id` and `kind = "bank"` from the map,
`raw = {"section": key, "anchor": false}`. Sections are processed in document order; a failure of one section does
not stop the others — every section gets an outcome, and the file status is written **once, after the loop**
(`_parse_submit`'s early `parsed` write is not reused; the composite path has its own submit helper).

Section outcome and its failure class (the existing classes keep their meaning):

| section event | outcome | file effect |
|---|---|---|
| posted, `guardrail_ok` | `parsed` | — |
| posted, `guardrail_ok = false` | `guardrail` | `needs_review / guardrail` |
| API 422 (`kind`, `lines`, `statement_retired`, …), `header_mismatch`, `line_mismatch`, parser `schema`, parser `output_too_large` | `parse` | `failed / parse` |
| masked section over `STDIN_CAP` (parser `stdin_too_large`, or the pre-check) | `too_large` | `needs_review / too_large` (as the single path) |
| parser `timeout` / `exit` | `transient` | `failed / transient` (retry ladder, `attempts` as today) |
| parser `sandbox` / `auth` / `cli_version` | `sandbox` | `failed / sandbox`, parser latched (as today) |
| model section not attempted because the parser is latched (this file or earlier) | `deferred` (reason `parser_disabled`) | see below |
| posted and the API answered `replayed: true` (§5) | `replayed` | — |
| key not in the map (`50/USD`) | `skipped` | — |

File status = the worst class present: `sandbox` > `transient` > `parse` > `too_large` > `guardrail` > `parsed`.
`SplitError` and a missing text layer are file-level `parse` / `no_text_layer` before any section runs. A file whose
folder is in `composite_map` but matched no declared key is `ignored / mapping`.

**Parser latch inside the loop (AGENT-154 R2).** The single path never calls the parser once
`services.parser_disabled` is set (`pipeline.py` defers the file). The composite loop keeps that contract: a
`sandbox` / `auth` / `cli_version` error in section k latches the parser exactly as today (gate latch,
`parser_disabled` in the run errors) and the remaining model sections k+1… are not attempted — each gets outcome
`deferred` with reason `parser_disabled` and no parser call (tests assert zero calls after the latch). Anchor
sections need no parser and still post. The file ends `failed / sandbox` (the worst class), written once after the
loop; its already-posted sections are idempotent on the re-run. A composite file that starts while the parser is
already latched is deferred as a whole, like a single-account file (no status write, `summary.deferred += 1`).
Every `ParseError` reason the parser can raise is in the table above; an unlisted reason is a programming error and
is treated as `parse`.

Run summary gains `composite_files`, `sections_parsed`, `sections_anchor`, `sections_replayed`,
`sections_skipped`, `sections_failed`, and a per-file list `composite: [{file_id, sections: [{key, outcome,
reason}]}]` (keys, outcomes and the reason label only). The worker log line per section carries the same fields.

**Recovery contract.** `retry_due` is unchanged: `transient` retries on its ladder, `parse` / `guardrail` retry only
on a parser-version change, `parsed` never. Because this work must not bump `parser_version`, the documented recovery
for a composite file after a split, header or 422 failure — or after the operator adds a key to the map for a file
that is already `parsed` — is the operator re-queue already used for the Cube re-parses: set the file's `status`
to `new` and `failure` to `NULL` (the owner's `psql` command; the README gets the exact statement). The next poll
re-processes every section; sections whose statement already holds that revision are idempotent (§5). No automatic
rescue through a global version change.

### `process_file` control flow

```text
account_id = account_map.get(folder)
sections_map = composite_map.get(folder)
if account_id is None and sections_map is None: ignored/mapping (as today)
elif sections_map is not None: _process_composite(...)  # above
else: existing single-statement path, unchanged
```

## 5. API

- `statement_revision_service._assert_folder_maps(db, payload, account_map, composite_map)`: a composite folder
  accepts the payload only when `payload.raw["section"]` is a key of `composite_map[folder]` whose `account_id ==
  payload.account_id` and whose currency equals `payload.currency` (AGENT-153 #5: membership of the values is not
  enough — a misplaced section must be refused). Otherwise the existing `ValidationError("account_id", "folder maps
  elsewhere")`. The router passes both maps from the settings.
- `submit_revision`: on the composite path `StatementFile.account_id` is **explicitly set to `NULL`** (AGENT-153 Q4:
  `register_file` reuses the row by sha256, so a stale singular value could survive); on the single-account path the
  assignment stays as today. No reader depends on the column (model nullable, serializer `int | None`, `list_files`
  filters by status, identity uses `AccountStatement.account_id`).
- **Idempotent replay (AGENT-153 #4, AGENT-154 R3):** before step 5 of `submit_revision`, when the statement exists
  and the revision referenced by `statement.current_revision_id` (never the highest revision number — a newer
  non-current revision may exist) is **persisted-equal** to the payload, the call returns that revision with
  `replayed: true` and creates no revision, no case and no lineage. Persisted-equal means: same `file_id`,
  `parser_version`, `parser` and `kind`; every header column `_header_columns` writes (`currency`, `period_start`,
  `period_end`, `closing_date`, `due_date`, `opening_balance`, `statement_total`, `minimum_payment`) equal; and the
  ordered list of the payload's derived lines equals the stored lines ordered by `seq`, comparing every persisted
  line column — `seq`, `txn_date`, `posted_date`, `merchant_raw` (raw text, not `merchant_norm`), `printed_amount`,
  `foreign_amount`, `foreign_currency`, `flow_amount`, `line_kind`, `installment_seq`, `installment_total`,
  `is_subtotal` — position by position. This is a direct tuple comparison, not `lineage.pair` twinning (twinning
  keys omit `seq` and the raw text, so a seq swap or a merchant-text correction would otherwise be swallowed). It
  runs before the `conflict` test, so a re-run after a partial failure cannot open `statement_conflict` on a
  statement the owner already reconciled. Any difference — a seq swap, a foreign amount, raw merchant text, one
  header field, another file or parser version — takes the existing path unchanged (conflict, twins, header
  gates, pending conflicts untouched). `RevisionOut` gains `replayed: bool` (default false); the worker counts it.
- `RevisionIn.parser` accepts `"composite-anchor"` beside the existing values; `RevisionIn.raw.section` and
  `raw.anchor` are carried through to `statement_revision.raw` (already free-form JSON).
- Everything downstream — identity per (account, currency, period_end), `period_blocker`, guardrails, live/historical
  by the section account's `statement_live_from`, reconciliation hook, cases, confirm, retire, UI — is untouched.

## 6. Rollout

1. Deploy API then worker (the worker tolerates an API without `composite_map`; the API tolerates old workers).
   The deploy's `mapping_version` change re-tries the 33 `mapping` failures once (§3); the 21 臺灣銀行一般 files return
   to `ignored / mapping`.
2. Owner sets `statement_live_from = 2026-07-01` on accounts 67, 76, 68, 81 (existing account API, as for the other
   banks) so 2025-10 … 2026-06 periods land historical and 2026-07 … 2026-09 live.
3. Owner PUTs `composite_map` (§3). The map change bumps `mapping_version`; the next poll re-queues the twelve
   files; each produces up to four statements (two with rows, two anchors) — at most 48 statements, 12 live.
4. Expect a batch of `line_unmatched` / `entry_unmatched` cases on 國泰 and 國泰薪轉戶 for July–September (the ledger
   has 174 / 330 entries on those accounts); transfers between the two accounts match on each side independently.
5. Verification: twelve files `parsed`; the run summary's `composite` list shows every section `parsed` / `anchor`
   and `50/USD` `skipped`; per account the live statements show the September closing balances from the holdings
   table; `equation` passes on every section. A `SplitError` or `header_mismatch` in the summary is the first thing
   to inspect (the file stays `failed / parse` until the operator re-queue).

## 7. Out of scope

USD section (no account); 國泰高雄 / portfolio market value; other composite layouts (`銀行帳戶/臺灣銀行一般`, 21 files
ignored for a different reason — the owner's 臺灣銀行 split); OCR composites; any change to card parsing, the period
derivation backlog, or the sibling-candidate mechanism (ruling 4 of the base design stays as is — it still helps
statements whose counterpart account has no statement feed).

## 8. Tests

- `tests/worker/test_composite.py` with fixtures under `tests/fixtures/composite/` built from the masked,
  name-stripped text and tables of `2609.pdf` (de-identified: the owner's name removed, account numbers already
  masked by the bank and the masker, amounts kept — they are the test subject) plus synthetic variants:
  - period, holdings keys and currencies, two sections with the fixture's row counts, anchor sections for `68/TWD`
    and `50/JPY`, `50/USD` skipped;
  - page handling: a page digit glued to `持有明細`, to a section header and to a dated row; a repeated column header;
    a section whose table is split across pages (two tables, one section);
  - attribution: the same date in both sections with different balances (resolved), the same date, amount and
    balance in both sections (`SplitError`), a dated row before any header (`SplitError`), a table row with no text
    row and a text row with no table row (`SplitError`);
  - balance chain: a doctored running balance, a doctored holdings balance, both 提出 and 存入 filled, mixed
    currency within a section (each `SplitError` naming the key and row index, message free of amounts);
  - declaration: rows under an `anchor_only` key, a declared non-anchor key without a section, an undeclared key
    with rows (each `SplitError`); an undeclared key without rows is skipped;
  - `SplitReport` carries counts and keys only.
- `tests/worker/test_pipeline.py`: composite folder → one `submit_revision` per declared section with the right
  `account_id`, `raw.section`, `raw.anchor`; anchor sections skip the parser; the `[hint]` line carries period,
  currency, suffix, opening, closing and row count; header enforcement (`header_mismatch` on a wrong opening, a
  wrong period, a wrong line count); row-by-row enforcement (`line_mismatch` on two deposits of 10 and 20 reported
  as 15 and 15, on a dropped 10 plus a duplicated 10 on another date, on a sign flip, on two rows swapped in
  order — all with a matching header and total); the outcome table (all parsed → `parsed`; one guardrail →
  `needs_review`; one 422 → `failed/parse` with the other sections still posted; one `schema` error →
  `failed/parse`; one section over `STDIN_CAP` → `needs_review/too_large`; one timeout → `failed/transient`;
  `SplitError` → `failed/parse`; no text layer → `failed/no_text_layer` without OCR; nothing declared matched →
  `ignored/mapping`); latch: an `auth` error in the first model section leaves zero further parser calls, the
  remaining model sections `deferred`, anchors still posted, file `failed/sandbox` written once; a file started
  while latched is deferred with no status write; `replayed` counted; the summary's `composite` list; the
  `mapping_version` change re-queues an ignored composite file and nothing with another failure class.
- `tests/integration/test_statement_ingest_api.py`: `composite_map` validation (grammar, currency, credit account,
  archived, missing account, overlap with `account_map`, duplicate target account naming both keys, bad
  `anchor_only` type), `GET /statements/account-map` carries it, `mapping_version` folds it in with stable key
  order, the empty-map upgrade changes the hash exactly once, `parse` / `guardrail` / `parsed` files are not
  re-queued by a map change.
- `tests/integration/test_statement_ingest_service.py`: a file-backed revision for a composite folder is accepted
  for its declared (section, account, currency) and rejected for an unmapped key, a mismatched account or a
  mismatched currency; `statement_file.account_id` is NULL after a composite submission even when the row carried a
  value before; two sections of one file become two statements with independent periods, guardrails and modes;
  anchor-only revision passes guardrails; idempotent replay: identical resubmission returns `replayed: true` with no
  new revision or case, including on a `reconciled` live statement (no `statement_conflict`), while a one-line
  difference on a reconciled statement still opens the conflict; a seq-only swap, a `foreign_amount`-only change
  and a `merchant_raw`-only change are each **not** replayed; replay compares `current_revision_id` even when a
  newer non-current revision exists (the newer one being equal to the payload must not trigger replay); the
  "A reconciled, B failed, re-run" scenario.
- README (`services/accounting-service/README.md`): a "Composite statements" section (settings key with
  `anchor_only`, section keys, the split's fail-closed rules, header enforcement, the outcome/file-status table, the
  operator re-queue statement, the one-time `mapping_version` fan-out) and the `account-map` route's new field;
  the revision route's `replayed` field.

## 9. Review focus (for Multica)

1. Page normalisation: every shape the fixture shows (page digit glued to a row, repeated column header, note lines
   inside the transaction area) must be covered; any unknown line inside a section is a `SplitError`, not skipped.
2. Attribution, the balance chain and the row-by-row comparison together must make a dropped, duplicated,
   misplaced, redistributed or sign-flipped row impossible to post silently — the tests enumerate each.
3. The `[hint]` line must never leak into `parser_version` and must not change the single-statement path; header
   and row enforcement must refuse, never correct.
4. Partial failure: a later section's failure must not roll back earlier sections, the file status must reflect the
   worst class and be written once, the latch must stop every further parser call, and the documented re-queue must
   be the only recovery path (no version bump).
5. Idempotent replay must be persisted-equal against `current_revision_id` only; `composite_map` validation must
   refuse duplicate targets and cross-map overlap; the one-time `mapping_version` fan-out must touch `mapping`
   failures only.

## 10. AGENT-153 findings → rev 2 changes

| # | finding | change |
|---|---|---|
| 1 | split not fail-closed (page breaks, first-match attribution, silent anchors) | §4 page normalisation, continuation and merge rules; rows from table cells; unique attribution or `SplitError`; `anchor_only` declared in the map, both mismatches `SplitError`; currency from the cell |
| 2 | closing hint leaves `opening` to the model; equation not independent | §4 worker derives opening and closing from the rows and the holdings, checks the running-balance chain, passes both in the hint, refuses a header or line-count mismatch |
| 3 | no recovery contract; early `parsed` write; failure classes | §4 outcome table with the existing classes, file status once after the loop, run-summary `composite` list, operator re-queue as the documented recovery |
| 4 | identical replay is not harmless on a reconciled statement | §5 idempotent replay before the conflict gate, `replayed: true`; test on a reconciled statement |
| 5 | same target account under two keys | §3 uniqueness 422; §5 folder check by (section, account, currency) |
| 6 | envelope hash fans out once | §3 accepted and documented, tests for key order, empty-map upgrade and class isolation |
| Q4 | NULL `statement_file.account_id` must be explicit | §5 explicit NULL on the composite path |
| R1 (AGENT-154) | header + total + count do not prove the lines | §4 row-by-row enforcement: ordered (date, signed amount, seq) comparison against `Section.rows`, `line_mismatch` fails closed; equation demoted to the second check |
| R2 (AGENT-154) | loop vs parser latch; `ParseError` classes incomplete | §4 outcome table covers `schema`, `output_too_large`, `stdin_too_large`/cap, `deferred`; latch stops further parser calls, anchors continue, file `failed / sandbox` once; latched-at-start files deferred |
| R3 (AGENT-154) | line-set equality via twinning swallows seq / raw-text changes | §5 persisted-equal against `current_revision_id`: every header column and every line column incl. `seq`, `merchant_raw`, `foreign_*`, position by position; tests for seq-only, foreign-only, merchant-only and newer-non-current |
