# Composite bank statement split (國泰世華 綜合月對帳單) — design

Status: draft for owner review, 2026-10-18. Base: main `5636f5a` (R6b + R3a deployed). Follows the statement
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
`外匯綜合活期存款 ……50 屏東分行 JPY <balance>` / `… USD <balance>`.

Owner mapping (2026-10-18): `91/TWD` → 國泰 (67); `59/TWD` → 國泰薪轉戶 (76); `68/TWD` → 國泰子 (68); `50/JPY` →
出國用日幣 (81); `50/USD` → no HomeHub account, skipped. 國泰高雄 (117) is the securities side and is out of scope (its
market-value binding to the portfolio service is a separate issue).

## 2. What already works (no change)

`_file_statement_id` and the statement identity (`uq_account_statement_identity` = account, currency, period_end) are
already per account: one file can back N statements if N different `account_id`s are submitted. `period_blocker`,
bank guardrails (`opening_present`, `equation` opening + Σ flow = statement_total, `currency`, `period_length` ≤ 62 days,
`dates_in_window` ±5 days, `line_count` ≤ 2000), `becomes_current`, live/historical per account
(`account.statement_live_from`), retire and the UI need nothing. No database migration.

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
  "manual/國泰世華": {"91/TWD": 67, "59/TWD": 76, "68/TWD": 68, "50/JPY": 81}
}
```

- Folder key: same grammar as `account_map` keys (`manual/<folder>` or `mail/<folder>/<subfolder>`, no empty or
  padded parts). A folder may appear in `account_map` or `composite_map`, never both (422 `composite_map`).
- Section key: `^[0-9]{2,4}/[A-Z]{3}$` — the digits the bank prints after the masked run of the account number
  (the visible suffix), then the currency. Distinct keys may share a suffix (the FX account prints one number for
  every currency).
- Value: an existing, non-archived account with `is_credit = false` whose `currency` equals the key's currency
  (422 `composite_map` naming the key otherwise). Non-bool int, as in `account_map`.
- `mapping_version` = sha256[:16] of the canonical JSON of `{"account_map": …, "composite_map": …}`. Any change to
  either map re-queues every `failure = mapping` file (existing `retry_due` rule), so the twelve files re-parse by
  themselves once the owner PUTs the map.
- `GET /statements/account-map` returns `composite_map` beside `account_map`, `mapping_version`, `closing_days`. The
  worker's `ApiClient.account_map()` tolerates its absence (older API) as it does for `closing_days`.
- `PUT /settings/reconciliation` (admin) accepts `composite_map` in `ReconciliationSettingsIn`; the PUT replaces it
  wholesale like the other keys. Defaults: `{}`.

## 4. Worker: deterministic split

New module `worker/composite.py` (standard library + the existing `pdf.Extracted`). No model involvement in the split.

```python
@dataclass(frozen=True)
class Section:
    key: str                 # "91/TWD"
    currency: str
    period_start: date
    period_end: date
    closing_balance: Decimal # from 持有明細
    text: str                # the section's own lines (header + rows); "" for anchor-only sections
    tables: list[list[str]]  # the table rows attributed to this section
    rows: int                # dated rows found in text

def split(extracted: pdf.Extracted) -> tuple[list[Section], SplitReport]
```

Recognition rules (國泰世華 layout; every regex is anchored to a whole line of `extracted.text`):

- Period: the first line matching `綜合月對帳單 (\d{4}/\d{2}/\d{2}) ~ (\d{4}/\d{2}/\d{2})`. Missing → `SplitError`.
- Holdings (closing balances): between the line `持有明細` (a leading page digit is tolerated: `^\d*持有明細$`) and the
  line `交易明細`, every line matching
  `^(活期存款|子帳戶|外匯綜合活期存款) (\S+) (\S+分行) (?:([A-Z]{3}) )?(-?[\d,]+(?:\.\d+)?)$`.
  Suffix = the digits after the last `*` of the printed number (the whole number when it has no `*`); currency = the
  optional code, else `TWD`. Key = `suffix/currency`. One holdings row per key; a duplicate key → `SplitError`.
- Transaction sections: after `交易明細`, a header `^\d*(?:活期存款|外匯綜合活期存款) 帳號 (\S+)$` opens a section; its
  rows are the following lines matching `^\d{4}/\d{2}/\d{2}` followed by a space up to the next header or the first line that is neither
  a row nor the column header `日期 交易說明 幣別 提出 存入 餘額`. The section's currency is the row's currency column
  (third token) — all rows of a section must agree, else `SplitError`. A section whose key has no holdings row →
  `SplitError`.
- Anchor-only sections: every holdings key without a transaction section becomes a `Section` with `text = ""`,
  `tables = []`, `rows = 0`.
- Table attribution: a table row whose first cell is a `YYYY/MM/DD` date belongs to the section that has a text row
  starting with that date and ending with the same balance token (last cell, separators normalised). Rows that match
  no section are dropped and counted (`SplitReport.unattributed_rows`); this survives page breaks, where pdfplumber
  splits one section's table in two.
- Closing consistency: for a section with rows, the last row's balance must equal the holdings balance; a mismatch is
  recorded (`SplitReport.closing_mismatch` contains the key) and the section is still processed — the server's
  `equation` guardrail decides.
- `SplitReport`: `sections`, `anchor_only`, `unattributed_rows`, `closing_mismatch`, `period`. Counts only; it never
  carries amounts, merchants or account numbers into logs.

`SplitError` is a parse failure of the file (`failed / parse`), never a crash of the run.

### Masking and the model call

For each mapped section with rows the worker builds `pdf.Extracted(text=section.text, tables=section.tables, …)` and
masks it with the unchanged `Masker.render` (the account number in the header becomes `[NUM…3456]****91`; the
transaction rows carry no card codes, so `_TXN_ROW` never fires). It prepends one operator line in the existing
`[hint]` family (the instruction already says the input may begin with a `[hint]` line, so `INSTRUCTION`, the
schema and therefore `parser_version` do not change — no global re-queue):

```text
[hint] This is one deposit-account section of a composite bank statement (國泰世華 綜合月對帳單). Statement period
2026-09-01 ~ 2026-09-30. Account suffix 91, currency TWD. The printed closing balance of this account is 12,345.00:
statement_total must equal it and opening_balance is the balance before the first row. Report only this section's
rows; kind is bank.
```

Then `services.parser.parse(masked)` as today, one call per section, same `STDIN_CAP`, same OCR fallback rules
(not expected: the files have a text layer; an OCR fallback runs on the whole file and is treated as `failed / parse`
for composite folders — OCR output has no reliable section structure).

Anchor-only sections (子帳戶, JPY, USD when mapped) skip the model: the worker posts `lines = []`,
`opening_balance = statement_total = closing_balance`, `period_start/period_end` from the header, `parser =
"composite-anchor"`, the current `parser_version`, `raw = {"section": key, "anchor": true}`. The server's guardrails
accept this (equation holds with zero flow; `line_count` has no minimum).

### Posting and file status

One `POST /statements/revisions` per mapped section, same `file_id`, `account_id = composite_map[folder][key]`,
`kind = "bank"`, `raw.section = key`. Sections whose key is not in the map are skipped and counted; the USD account
is the expected case.

File outcome = the worst section outcome:

| sections | file status |
| --- | --- |
| every mapped section posted with `guardrail_ok` | `parsed` |
| any posted section with `guardrail_ok = false` | `needs_review / guardrail` |
| any section 422 / parser failure / `SplitError` | `failed / parse` |
| no mapped section at all (map has the folder but no key matched) | `ignored / mapping` |

Sections that succeeded before a later section failed stay posted; a re-run re-submits them as a new revision of the
same statement (identical content → current, no cases). `retry_due` keeps its rules (`failed / parse` re-queues on a
parser-version change; `mapping` on a map change).

Run summary gains `composite_files`, `sections_parsed`, `sections_anchor`, `sections_skipped`, `sections_failed`.
Logs name the section key and counts only.

### `process_file` control flow

```text
account_id = account_map.get(folder)
sections_map = composite_map.get(folder)
if account_id is None and sections_map is None: ignored/mapping (as today)
elif sections_map is not None: _process_composite(...)  # above
else: existing single-statement path, unchanged
```

## 5. API

- `statement_revision_service._assert_folder_maps(db, payload, account_map, composite_map)`: accept when
  `account_map.get(folder) == payload.account_id` **or** `payload.account_id in composite_map.get(folder, {}).values()`.
  Otherwise the existing `ValidationError("account_id", "folder maps elsewhere")`. The router passes both maps from
  the settings.
- `submit_revision`: `StatementFile.account_id` is set only on the single-account path; for a composite folder it stays
  `NULL` (no reader depends on it — verified by grep; `GET /statements/files` shows it as null for these files).
- `RevisionIn.parser` accepts `"composite-anchor"` beside the existing values; `RevisionIn.raw` keeps carrying
  `section` through to `statement_revision.raw` (already free-form JSON).
- Everything downstream — identity per (account, currency, period_end), `period_blocker`, guardrails, live/historical
  by the section account's `statement_live_from`, reconciliation hook, cases, confirm, retire, UI — is untouched.

## 6. Rollout

1. Deploy worker + API (same merge; API first, worker tolerates the old shape).
2. Owner sets `statement_live_from = 2026-07-01` on accounts 67, 76, 68, 81 (existing account API, as for the other
   banks) so 2025-10 … 2026-06 periods land historical and 2026-07 … 2026-09 live.
3. Owner PUTs `composite_map` (above). The map change bumps `mapping_version`; the next poll re-queues the twelve
   files; each produces up to four statements (two with rows, two anchors) — at most 48 statements, 12 live.
4. Expect a batch of `line_unmatched` / `entry_unmatched` cases on 國泰 and 國泰薪轉戶 for July–September (the ledger
   has 174 / 330 entries on those accounts); transfers between the two accounts match on each side independently.
5. Verification: twelve files `parsed`; per account the live statements show the September closing balances from the
   holdings table; the equation guardrail passes on every section (any `closing_mismatch` in the run log is the first
   thing to inspect).

## 7. Out of scope

USD section (no account); 國泰高雄 / portfolio market value; other composite layouts (`銀行帳戶/臺灣銀行一般`, 21 files
ignored for a different reason — the owner's 臺灣銀行 split); OCR composites; any change to card parsing, the period
derivation backlog, or the sibling-candidate mechanism (ruling 4 of the base design stays as is — it still helps
statements whose counterpart account has no statement feed).

## 8. Tests

- `tests/worker/test_composite.py` (fixture: the masked, name-stripped text and tables of `2609.pdf` stored under
  `tests/fixtures/composite-cathay-2609.json`, plus a synthetic two-page variant whose first section's table is split
  across pages): period, holdings keys and currencies, two sections with the right row counts, anchor-only sections
  for `68/TWD`, `50/JPY`, `50/USD`, table attribution across the page break, `unattributed_rows`, `closing_mismatch`
  on a doctored balance, `SplitError` on a missing period / duplicate key / mixed-currency rows.
- `tests/worker/test_pipeline.py`: composite folder → one `submit_revision` per mapped section with the right
  `account_id` and `raw.section`; USD skipped and counted; anchor sections skip the parser; file status table (all
  pass → parsed; one guardrail fail → needs_review; one parser failure → failed/parse; `SplitError` → failed/parse;
  nothing mapped → ignored/mapping); the `[hint]` line carries period, suffix, currency and closing balance; the
  `mapping_version` change re-queues an ignored composite file.
- `tests/integration/test_statement_ingest_api.py`: `composite_map` validation (grammar, currency, credit account,
  archived, overlap with `account_map`), `GET /statements/account-map` carries it, `mapping_version` folds it in.
- `tests/integration/test_statement_ingest_service.py`: a file-backed revision for a composite folder is accepted for
  each mapped account and rejected for an unmapped one; `statement_file.account_id` stays null; two sections of one
  file become two statements with independent periods, guardrails and modes; anchor-only revision passes guardrails.
- README (`services/accounting-service/README.md`): a "Composite statements" paragraph (settings key, section keys,
  file status rule, anchor revisions) and the `account-map` route's new field.

## 9. Review focus (for Multica)

1. A row whose 提出/存入 column is ambiguous in text form — the tables keep the columns, and the equation guardrail
   catches a flipped sign; confirm the fixture covers a withdrawal and a deposit on the same day.
2. Page-break table attribution: a date that appears in two sections on the same day with the same balance is
   theoretically ambiguous; the rule attributes to the first match — fixture must include a same-date row in both
   sections with different balances.
3. The `[hint]` line must never leak into `parser_version` and must not change the single-statement path.
4. Partial failure: a later section's 422 must not roll back earlier sections and must leave the file re-runnable.
5. `composite_map` and `account_map` overlap and the `mapping_version` change must re-queue only `mapping` failures.
