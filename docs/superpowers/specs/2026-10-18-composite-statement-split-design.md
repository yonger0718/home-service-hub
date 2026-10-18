# Composite bank statement split (國泰世華 綜合月對帳單) — design

Status: rev 6 (accepted under the owner ruling of 2026-10-18: rev 5 `5d5282c` came back with one P2 only, folded in here), 2026-10-18. Rev 1 `d1de321` CHANGES-REQUESTED (AGENT-153), rev 2 `0680f1d`
CHANGES-REQUESTED (AGENT-154), rev 3 `82a44d4` CHANGES-REQUESTED (AGENT-155) and superseded by the owner's decision
to drop the model call for composite sections, rev 4 `5e0aef0` CHANGES-REQUESTED (AGENT-156, four regressions of
the model-free body); §10 maps every finding to its change. Base: main `5636f5a` (R6b + R3a deployed). Follows the statement
reconciliation design (`2026-10-08-statement-reconciliation-design.md`) and R6 (`2026-10-15-reconciliation-r6-design.md`).

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
and the `StatementParse` schema do not change; **the composite path never calls the model** (§4).

The one-folder-one-account assumption lives in exactly four places, all changed below:

1. `worker/pipeline.py` `process_file`: `account_id = account_map.get(_folder_key(listed))`, one `_parse_submit` per file.
2. `worker/parse_schema.py`: `StatementParse` is one statement per model call (kept for every other folder).
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

## 4. Worker: deterministic split, no model call

New module `worker/composite.py` (standard library + the existing `pdf.Extracted`). The split, the rows and the
revision bodies are all computed from the PDF's text and tables; the Claude CLI is never invoked for a composite
folder. Rationale (owner decision 2026-10-18, after the rev 2/3 reviews): once the worker derives every row's date,
direction, amount and balance and verifies the balance chain, the only thing a model could add is a rewording of the
bank's own 交易說明 and a finer `line_kind` — not worth a model call per section, its failure modes (timeout, latch,
schema, header / line mismatch) and the tests for each. Every ambiguity is a `SplitError`, never a drop or a
first-match.

```python
@dataclass(frozen=True)
class Row:
    txn_date: date
    description: str             # the bank's 交易說明 cell, verbatim
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
  row outside any section → `SplitError`. Any other line inside a section that is not a recognised footer / header
  shape → `SplitError`.
- Rows come from the tables, not from the text (currency and columns are explicit cells): a table whose header row
  is `日期 | 交易說明 | 幣別 | 提出 | 存入 | 餘額` (cells, after whitespace normalisation) is a transaction table; each
  data row gives a `Row` with the currency from its own cell, the description cell verbatim and the amounts from
  their own cells (empty cell → None; exactly one of 提出/存入 must be set, else `SplitError`). Attribution of a
  table row to a section: the section whose text rows contain a line with the same date, the same amount token and
  the same balance token. Zero matches or more than one match → `SplitError` (no first-match). A text row with no
  table row → `SplitError`. A section's currency is the one its rows carry; rows of mixed currency → `SplitError`;
  the key's currency must match.
- Declaration check: a section with rows whose key is declared `anchor_only` → `SplitError`; a declared
  `anchor_only: false` key without a section → `SplitError`. Declared `anchor_only: true` keys become anchor sections.
- Balance chain (AGENT-153 #2): for a section with rows, in document order,
  `balance[i] == balance[i-1] - withdrawal[i] + deposit[i]` for every i ≥ 1 (Decimal, exact),
  `opening = balance[0] + withdrawal[0] - deposit[0]`, and `balance[-1] == holdings closing`. Any violation →
  `SplitError` naming the key and the row index (no amounts in the message). Anchor sections:
  `opening = closing = holdings balance`.
- `SplitReport`: `sections`, `anchor`, `skipped_keys`, `rows_per_key`. Counts and keys only; never amounts, merchants
  or account numbers.

`SplitError` is a parse failure of the file (`failed / parse`, below), never a crash of the run.

### Revision body per section (no model)

For every declared section the worker builds the `POST /statements/revisions` body itself:

- header: `kind = "bank"`, `currency = section.currency`, `period_start/period_end` from the statement header,
  `closing_date = period_end`, `due_date = None`, `opening_balance`, `statement_total = closing_balance`,
  `minimum_payment = None`;
- lines, one per `Row` in document order: `seq = i + 1`, `txn_date = posted_date = row.txn_date`,
  `foreign_amount/foreign_currency = None`, `installment_* = None`, `is_subtotal = False`, and:
  - **signed amount (AGENT-156 F1):** the server's `flow_amount` does not add signs for bank debits — a positive
    withdrawal or fee is a `SignError` (422). So `printed_amount` is signed by the worker from **which cell is
    present**, never from truthiness: `+deposit` when the 存入 cell is set, `-withdrawal` when the 提出 cell is set
    (exactly one is set, §4 split). Amounts are non-negative decimals with at most two fraction digits as printed; a
    negative printed cell is a `SplitError`. A row whose set cell is `0` (the bank prints zero-amount notices) gets
    `printed_amount = 0` and `line_kind = "unknown"` — the only kind the server accepts for zero — and contributes
    nothing to the chain. Before posting, the worker asserts for every line `printed_amount == deposit - withdrawal`
    (None read as 0) and that the sum of the lines equals `closing - opening`; a violation is a programming error
    raised as `SplitError`. Cancelling rows (+10 then −10) are two persisted lines, never collapsed;
  - **`line_kind` (AGENT-156 F2):** direction first, classification second, and the classification never changes
    the direction. Default: `deposit` for a 存入 row, `withdrawal` for a 提出 row. A finer kind is assigned only when
    the classification view of the description **equals** an entry of a fixed table in `composite.py` —
    `{"存款利息": "interest", "利息": "interest"}` for 存入 rows, `{"手續費": "fee", "轉帳手續費": "fee"}` for 提出 rows —
    exact string equality, no substring match, direction-specific. The classification view is
    `" ".join(unicodedata.normalize("NFKC", row.description).split())` (NFKC folds full-width characters and spaces,
    `split()`/`join` trims and collapses every whitespace run to one ASCII space, internal spaces survive), computed
    on the **original, unmasked** description — classification runs before and independently of masking, so a
    configured holder name that happens to equal a table key cannot change the kind. A zero-amount row is
    `unknown` regardless of the table (the zero rule wins). `轉帳備註含利息` stays
    `deposit`/`withdrawal` by its cell, `手續費退回` on a 存入 row stays `deposit`, `利息` on a 提出 row stays
    `withdrawal`. The table is a code constant and grows only by a reviewed change (`matching.py` restricts which
    ledger kinds a `fee`/`interest` line may match and routes them to bank-only hints, so a wrong kind is not
    cosmetic);
  - **`merchant_raw` (AGENT-156 F3):** `services.masker.text(row.description, extra_literals=candidates)` — the
    run's `Masker` instance and the same password `candidates` the single path passes to `render`, so a candidate
    secret or the configured holder names inside a description are masked exactly as on every other statement.
    Name protection is the existing one (configured holder names, not arbitrary counterparty names); the test plants
    a configured name and a candidate, not an unknown name;
- `parser = "composite-rows"`, `parser_version` = the run's parser version (the file-level retry rules key on it
  and the composite path must not look like a different parser generation), `raw = {"section": key, "anchor":
  false, "rows": len(rows)}`; anchor sections post `lines = []`, `opening_balance = statement_total =
  closing_balance`, `parser = "composite-anchor"`, `raw = {"section": key, "anchor": true, "rows": 0}`.

Transfers between the two 國泰 accounts appear as `withdrawal` on one side and `deposit` on the other; their pairing
is R6c and out of scope — the matcher treats them like any other line today. The server's `flow_amount` sign rule
(`BANK_CREDIT` / `BANK_DEBIT`) accepts these kinds; the `equation` guardrail re-verifies opening + Σ flow = closing
independently of the worker's chain check.

### Posting, section outcomes and file status (AGENT-153 #3)

One `POST` per section, same `file_id`, `account_id` from the map. Sections are processed in document order; a
failure of one section does not stop the others — every section gets an outcome, and the file status is written
**once, after the loop** (`_parse_submit` is not reused).

| section event | outcome | file effect |
|---|---|---|
| posted, `guardrail_ok` | `parsed` | — |
| posted, `guardrail_ok = false` | `guardrail` | `needs_review / guardrail` |
| API 422 (`kind`, `lines`, `statement_retired`, `account_id` folder check, …) | `parse` | `failed / parse` |
| posted and the API answered `replayed: true` (§5) | `replayed` | — |
| key not in the map (`50/USD`) | `skipped` | — |

File status = the worst class present: `parse` > `guardrail` > `parsed`. `SplitError` and a missing text layer are
file-level `parse` / `no_text_layer` before any section runs (no OCR fallback: OCR output has no reliable section
structure). A file whose folder is in `composite_map` but matched no declared key is `ignored / mapping`. API
transport errors (`ApiError` other than 422, `LeaseLost`) propagate exactly as on the single path.

**PDF-level failures stay (AGENT-156 F4).** The composite path reuses the single path's acquire / unlock / extract
steps unchanged, so their outcomes keep their classes and recovery: no password matched → `failed / password`
(re-tried on a `credential_version` change), malformed PDF → `failed / parse`, `pdf.TooLarge` →
`needs_review / too_large`, extraction timeout → `failed / transient` (retry ladder, `attempts`). Only the
**parser-origin** outcomes do not apply — parser `timeout` / `exit`, `sandbox` / `auth` / `cli_version`,
`stdin_too_large`, `schema`, `output_too_large` and the parser latch — because there is no parser call; a composite
file is processed even while the parser is latched for the run.

Run summary gains `composite_files`, `sections_parsed`, `sections_anchor`, `sections_replayed`,
`sections_skipped`, `sections_failed`, and a per-file list `composite: [{file_id, sections: [{key, outcome,
reason}]}]` (keys, outcomes and the reason label only). The worker log line per section carries the same fields.

**Recovery contract.** `retry_due` is unchanged: `parse` / `guardrail` retry only on a parser-version change,
`parsed` never. Because this work must not bump `parser_version`, the documented recovery for a composite file
after a split or 422 failure — or after the operator adds a key to the map for a file that is already `parsed` —
is the operator re-queue already used for the Cube re-parses: set the file's `status` to `new` and `failure` to
`NULL` (the owner's `psql` command; the README gets the exact statement). The next poll re-processes every section;
sections whose statement already holds that revision are idempotent (§5). No automatic rescue through a global
version change.

### `process_file` control flow

```text
account_id = account_map.get(folder)
sections_map = composite_map.get(folder)
if account_id is None and sections_map is None: ignored/mapping (as today)
elif sections_map is not None: _process_composite(...)  # above, no parser
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
  `replayed: true` and creates no revision, no case and no lineage. Persisted-equal means (AGENT-155 R3: the
  columns below are the ones `StatementRevision` / `StatementLine` actually store):
  - revision: same `file_id`, `parser_version` and `parser`; `payload.kind == statement.kind` and
    `payload.currency == statement.currency` (both live on the statement, not the revision); every column
    `_header_columns` writes (`period_start`, `period_end`, `closing_date`, `due_date`, `opening_balance`,
    `statement_total`, `minimum_payment`) equal;
  - lines: the ordered list of the payload's derived lines (after `derive_lines`, i.e. subtotal rows already
    removed and `flow_amount` already signed) equals the stored lines ordered by `seq`, position by position, over
    `seq`, `txn_date`, `posted_date`, `merchant_raw` (raw text), `printed_amount`, `foreign_amount`,
    `foreign_currency`, `flow_amount`, `line_kind`, `installment_seq`, `installment_total`. The derived columns
    `merchant_norm`, `canonical_key` and `logical_key` are functions of the compared columns under one
    `parser_version` and are therefore implied equal and not compared; the generated columns `id`, `revision_id`
    and `event_id` are excluded; `is_subtotal` is not a stored column (subtotals never reach the table).
  This is a direct tuple comparison, not `lineage.pair` twinning (twinning keys omit `seq` and the raw text, so a
  seq swap or a merchant-text correction would otherwise be swallowed). It runs before the `conflict` test, so a
  re-run after a partial failure cannot open `statement_conflict` on a statement the owner already reconciled, and
  a replay on a statement with a pending conflict changes nothing — no case, no `conflict_open` change, no current
  pointer change. Any difference — a seq swap, a foreign amount, raw merchant text, one header field, another file
  or parser version — takes the existing path unchanged (conflict, twins, header gates). `RevisionOut` gains
  `replayed: bool` (default false); the worker counts it. The replay rule applies to every revision submission,
  not only composite ones (it is a property of the route).
- `RevisionIn.parser` accepts `"composite-rows"` and `"composite-anchor"` beside the existing values;
  `RevisionIn.raw.section`, `raw.anchor` and `raw.rows` are carried through to `statement_revision.raw` (already
  free-form JSON).
- Everything downstream — identity per (account, currency, period_end), `period_blocker`, guardrails, live/historical
  by the section account's `statement_live_from`, reconciliation hook, cases, confirm, retire, UI — is untouched.

## 6. Rollout

1. Deploy API then worker (the worker tolerates an API without `composite_map`; the API tolerates old workers).
   The deploy's `mapping_version` change re-tries the 33 `mapping` failures once (§3); the 21 臺灣銀行一般 files return
   to `ignored / mapping`.
2. Owner sets `statement_live_from` on the mapped accounts (existing account API, as for the other banks). Owner
   choice recorded 2026-10-18: 國泰薪轉戶 (76) from 2026-09-01 (one live period before the cutover); 國泰 (67) stays
   historical for now (its ledger has 1–2 entries a month against ~16 statement rows, mostly securities
   settlements, which would only produce transfer cases that wait for R6c); 國泰子 (68) and 出國用日幣 (81) are the
   owner's call (live gives only the balance-gap check). Periods before `statement_live_from` land historical.
3. Owner PUTs `composite_map` (§3). The map change bumps `mapping_version`; the next poll re-queues the twelve
   files; each produces up to four statements (two with rows, two anchors) — at most 48 statements.
4. Expect a small batch of `line_unmatched` / `entry_unmatched` cases on the live periods (薪轉戶 September:
   ~12 rows against ~11 ledger entries); transfers between the two accounts match on each side independently.
5. Verification: twelve files `parsed`; the run summary's `composite` list shows every section `parsed` / `anchor`
   and `50/USD` `skipped`; per account the statements show the closing balances from the holdings table;
   `equation` passes on every section. A `SplitError` in the summary is the first thing to inspect (the file stays
   `failed / parse` until the operator re-queue).

## 7. Out of scope

USD section (no account); 國泰高雄 / portfolio market value; other composite layouts (`銀行帳戶/臺灣銀行一般`, 21 files
ignored for a different reason — the owner's 臺灣銀行 split); OCR composites; model-based reading of composite
sections (a later change may add a merchant-normalising model pass if it ever earns its cost); any change to card
parsing, the period derivation backlog, or the sibling-candidate mechanism (ruling 4 of the base design stays as
is — it still helps statements whose counterpart account has no statement feed).

## 8. Tests

- `tests/worker/test_composite.py` with fixtures under `tests/fixtures/composite/` built from the masked,
  name-stripped text and tables of `2609.pdf` (de-identified: the owner's name removed, account numbers already
  masked by the bank and the masker, amounts kept — they are the test subject) plus synthetic variants:
  - period, holdings keys and currencies, two sections with the fixture's row counts, anchor sections for `68/TWD`
    and `50/JPY`, `50/USD` skipped;
  - page handling: a page digit glued to `持有明細`, to a section header and to a dated row; a repeated column header;
    a section whose table is split across pages (two tables, one section);
  - attribution: the same date in both sections with different balances (resolved), the same date, amount and
    balance in both sections (`SplitError`), a dated row before any header (`SplitError`), an unrecognised line
    inside a section (`SplitError`), a table row with no text row and a text row with no table row (`SplitError`);
  - balance chain: a doctored running balance, a doctored holdings balance, both 提出 and 存入 filled, mixed
    currency within a section (each `SplitError` naming the key and row index, message free of amounts);
  - declaration: rows under an `anchor_only` key, a declared non-anchor key without a section, an undeclared key
    with rows (each `SplitError`); an undeclared key without rows is skipped;
  - revision bodies: `seq` order, `txn_date == posted_date`, signed `printed_amount` chosen by the present cell
    (a normal withdrawal → negative, a deposit → positive, a fee row → negative `fee`, a zero-amount row →
    `0` / `unknown`, cancelling +10 / −10 rows → two persisted lines, a negative printed cell → `SplitError`, the
    per-line `printed_amount == deposit - withdrawal` and sum == closing − opening assertions); the `line_kind`
    table by exact description and direction (`存款利息` on 存入 → `interest`; `手續費` on 提出 → `fee`;
    `轉帳備註含利息` → direction kind; `手續費退回` on 存入 → `deposit`; `利息` on 提出 → `withdrawal`; ` 利息 ` with
    leading/trailing spaces and `存款　利息` with a full-width space → `interest`; a configured holder name equal to
    `利息` still classifies as `interest` and is masked in `merchant_raw`; a zero-amount `利息` row → `unknown`;
    a row with neither 提出 nor 存入 → `SplitError`);
    `merchant_raw` via the run masker with the file's candidates (a planted candidate password and a configured
    holder name in a description are masked; a ≥6-digit run is masked by the generic rule); header fields,
    `parser` values, `raw` fields; the anchor body;
  - `SplitReport` carries counts and keys only.
- `tests/worker/test_pipeline.py`: composite folder → one `submit_revision` per declared section with the right
  `account_id`, `raw.section`, `raw.anchor`, `raw.rows`; **the parser is never called** (a parser stub that raises
  on use); the outcome table (all parsed → `parsed`; one guardrail → `needs_review`; one 422 → `failed/parse` with
  the other sections still posted; `SplitError` → `failed/parse`; no text layer → `failed/no_text_layer` without
  OCR; nothing declared matched → `ignored/mapping`; PDF-level: no password → `failed/password`, malformed →
  `failed/parse`, `pdf.TooLarge` → `needs_review/too_large`, extraction timeout → `failed/transient` with the
  retry ladder, each with synthetic inputs); a composite file is processed while the parser is latched;
  file status written once after the loop; `replayed` counted; the summary's `composite` list; the
  `mapping_version` change re-queues an ignored composite file and nothing with another failure class;
  `ApiError` ≠ 422 and `LeaseLost` propagate as on the single path.
- `tests/integration/test_statement_ingest_api.py`: `composite_map` validation (grammar, currency, credit account,
  archived, missing account, overlap with `account_map`, duplicate target account naming both keys, bad
  `anchor_only` type), `GET /statements/account-map` carries it, `mapping_version` folds it in with stable key
  order, the empty-map upgrade changes the hash exactly once, `parse` / `guardrail` / `parsed` files are not
  re-queued by a map change; `RevisionIn.parser` accepts the two composite values.
- `tests/integration/test_statement_ingest_service.py`: a file-backed revision for a composite folder is accepted
  for its declared (section, account, currency) and rejected for an unmapped key, a mismatched account or a
  mismatched currency; `statement_file.account_id` is NULL after a composite submission even when the row carried a
  value before; two sections of one file become two statements with independent periods, guardrails and modes;
  anchor-only revision passes guardrails; idempotent replay: identical resubmission returns `replayed: true` with no
  new revision or case, including on a `reconciled` live statement (no `statement_conflict`), while a one-line
  difference on a reconciled statement still opens the conflict; a seq-only swap, a `foreign_amount`-only change
  and a `merchant_raw`-only change are each **not** replayed; replay compares `current_revision_id` even when a
  newer non-current revision exists (the newer one being equal to the payload must not trigger replay); a replay
  on a statement with a pending `statement_conflict` leaves the case, `conflict_open` and
  `current_revision_id` untouched; the "A reconciled, B failed, re-run" scenario; replay also applies to an
  ordinary (non-composite) resubmission.
- README (`services/accounting-service/README.md`): a "Composite statements" section (settings key with
  `anchor_only`, section keys, the split's fail-closed rules, the no-model revision body and its `line_kind` table,
  the outcome/file-status table, the operator re-queue statement, the one-time `mapping_version` fan-out) and the
  `account-map` route's new field; the revision route's `replayed` field and the two new `parser` values.

## 9. Review focus (for Multica)

1. Page normalisation: every shape the fixture shows (page digit glued to a row, repeated column header, note lines
   inside the transaction area) must be covered; any unknown line inside a section is a `SplitError`, not skipped.
2. Attribution and the balance chain together must make a dropped, duplicated, misplaced, redistributed or
   sign-flipped row impossible to post silently — the tests enumerate each; with no model in the loop the rows the
   API receives are exactly the rows the split produced.
3. The composite path must not touch the single-statement path: `INSTRUCTION`, `parser_version`, OCR and the parser
   latch stay as they are; the parser is provably never called for a composite folder.
4. Partial failure: a later section's failure must not roll back earlier sections, the file status must reflect the
   worst class and be written once, and the documented re-queue must be the only recovery path (no version bump).
5. Idempotent replay must be persisted-equal against `current_revision_id` only; `composite_map` validation must
   refuse duplicate targets and cross-map overlap; the one-time `mapping_version` fan-out must touch `mapping`
   failures only.

## 10. Review findings → changes

| # | finding | change |
|---|---|---|
| 153-1 | split not fail-closed (page breaks, first-match attribution, silent anchors) | §4 page normalisation, continuation and merge rules; rows from table cells; unique attribution or `SplitError`; `anchor_only` declared in the map, both mismatches `SplitError`; currency from the cell |
| 153-2 | closing hint leaves `opening` to the model; equation not independent | §4 worker derives opening and closing from the rows and the holdings and checks the running-balance chain; rev 4 removes the model, so the rows posted are the derived rows |
| 153-3 | no recovery contract; early `parsed` write; failure classes | §4 outcome table, file status once after the loop, run-summary `composite` list, operator re-queue as the documented recovery |
| 153-4 | identical replay is not harmless on a reconciled statement | §5 idempotent replay before the conflict gate, `replayed: true`; test on a reconciled statement |
| 153-5 | same target account under two keys | §3 uniqueness 422; §5 folder check by (section, account, currency) |
| 153-6 | envelope hash fans out once | §3 accepted and documented, tests for key order, empty-map upgrade and class isolation |
| 153-Q4 | NULL `statement_file.account_id` must be explicit | §5 explicit NULL on the composite path |
| 154-R1 | header + total + count do not prove the model's lines | rev 4: no model; the posted lines are the split's rows by construction (§4 revision body), `equation` re-verifies server-side |
| 154-R2 | section loop vs parser latch; `ParseError` classes incomplete | rev 4: no parser call on the composite path; outcome table reduced to posted / 422 / replayed / skipped; composite files run while the parser is latched |
| 154-R3 | line-set equality via twinning swallows seq / raw-text changes | §5 persisted-equal against `current_revision_id`: every header column and every line column incl. `seq`, `merchant_raw`, `foreign_*`, position by position; tests for seq-only, foreign-only, merchant-only and newer-non-current |
| 155-R1 | model could mark cancelling rows `is_subtotal` and pass every check | moot in rev 4: no model; the worker sets `is_subtotal = False` on every row it posts (§4 revision body) |
| 155-R2 | — (resolved) | moot in rev 4: no parser call on the composite path |
| 155-R3 | `is_subtotal` is not a stored column; derived `merchant_norm`/`canonical_key`/`logical_key` and generated ids unaddressed; `currency`/`kind` live on the statement | §5 persisted-equal rewritten over the real columns: statement-level `kind`/`currency`, revision header columns, stored line columns; derived keys implied, generated ids excluded; test for replay with a pending conflict |
| owner 2026-10-18 | a model call per section adds cost and failure modes for a rewording of 交易說明 | rev 4 §4: deterministic revision bodies, `line_kind` keyword table, `parser = composite-rows`; model-based reading listed in §7 as a possible later change |
| 156-F1 | unsigned `printed_amount` is a `SignError` for every debit; truthy cell choice; zero policy undefined | rev 5 §4: sign chosen by the present cell (`+deposit` / `-withdrawal`), zero rows → `0` / `unknown`, negative cells `SplitError`, per-line and per-section sum assertions, cancelling rows kept |
| 156-F2 | substring keyword table can flip direction / misclassify memos | rev 5 §4: direction first; finer kind only on exact, direction-specific description equality from a code constant; three counter-examples in tests |
| 156-F3 | `Masker.text` without the run instance and the file's `candidates` | rev 5 §4: `services.masker.text(description, extra_literals=candidates)`; name protection scoped to configured holder names; tests plant a candidate and a configured name |
| 156-F4 | dropping parser failures read as dropping PDF failures | rev 5 §4: PDF-level password / malformed / TooLarge / extraction-timeout outcomes and their recovery kept verbatim; only parser-origin outcomes removed; synthetic tests |
| 157-F2 (P2, only open item of rev 5) | "whitespace normalisation" undefined; masking-before-classification ambiguity | rev 6 §4: classification view = NFKC + split/join on the original description, before and independent of masking; zero rule wins; tests for whitespace variants, a configured-name collision and a neither-filled row. Accepted by the owner's ruling (PASS-or-minor → plan) without a further Codex round |
