# Statement worker: OCR repair for missing merchants (#387)

Status: short design (small change), approved approach 2026-11-18.

## Why

HSBC card statements (accounts 94 HSBC Live+ and 100 匯豐旅人) reach reconciliation with no merchant names. Their PDF text layer carries no CJK text at all. File 28250 extracts 1,899 chars, with 0 CJK characters across 56 dated rows. The CJK glyphs are drawn in a font that has no Unicode map, and pdfplumber drops them silently.

As a result:
- the parser sets `merchant_raw` to an empty string, or to the printed country code (`USA`, `JPN`, `GBR`);
- owner rules (keyed on `merchant_pattern`) cannot learn anything for these cards;
- every inbox question shows only a date and an amount.

The OCR child does read these statements. On 2026-11-18 it read file 28250 in 46 s at mean confidence 0.96, and 45 of 53 dated rows came back with CJK text.

#206 already repairs merchants from an OCR parse. Today it triggers only on `(cid:N)` garbage, which is AMEX's symptom. HSBC's symptom is a merchant that is missing rather than garbled, so the repair never starts.

## Goals

- A text-layer parse in which some purchase or refund line has a missing merchant starts the existing #206 repair.
- The repair fills those lines' merchants from the OCR parse.
- Amounts, dates, kinds and line count stay the text layer's, as in #206.

## Non-goals / anti-goals

- **No backfill.** This is the owner ruling of 2026-11-18. Parsed files never retry (`retry_due` returns False for `parsed`), and re-revising old statements would release coverage on every line through lineage `changed`. Only files parsed from now on benefit.
- No full-OCR mode for HSBC. Text-layer amounts stay authoritative.
- No change to lineage, masking, the OCR child, the composite path or the R5.3 fallback.
- No `parser_version` bump. That would only re-run failed files, and nothing in this change affects why they failed.

## Approaches

1. **Widen the #206 trigger to missing merchants** (picked). This reuses the OCR read, the masked OCR parse, the unique-key merge and the failure handling. The only new thing is a predicate.
2. **Force full OCR for HSBC accounts.** Rejected. It replaces exact text-layer amounts with OCR-read digits and needs a per-account setting.
3. **Change the parse prompt.** Rejected. The names are not in the text the parser receives.

## Design

- **New predicate** `missing_merchant(line) -> bool` in `worker/pipeline.py`. It is true when:
  - `line.line_kind` is in `("purchase", "refund")`, and
  - `line.merchant_raw.strip()` is empty or fully matches `[A-Z]{2,3}` (a bare country code).

  Other kinds are excluded because a blank merchant is normal for them. Payment rows are blank on HSBC, and bank `deposit`/`withdrawal`/`transfer_*` rows are blank too; including them would start OCR on most bank statements.
- **New predicate** `needs_merchant_repair(line) = garbled_merchant(line.merchant_raw) or missing_merchant(line)`. It replaces `garbled_merchant` in three places:
  - the trigger in `_parse_submit`;
  - the list of lines to repair in `_repair_merchants`;
  - the per-line repair test in the merge loop.

  `garbled_merchant` keeps its meaning and its other callers.
- **Clean-candidate rule for the OCR merchant.** It must not be garbled, must be non-empty after strip, and must not itself be a missing merchant (`missing_merchant` applied to the OCR line). An OCR line that also reads only `USA` is no repair.
- **Unchanged behaviour:**
  - The merge rule stays: a unique `(txn_date, posted_date, printed_amount, line_kind)` key on both the text side and the OCR side.
  - The note text, the counters (`ocr_merchant_repair`, `merchant_lines_repaired`) and the parser label `claude-cli+ocr-merchants` stay.
  - Every OCR or parser failure still submits the text parse unchanged.
- **Accepted false positive.** A real three-letter all-caps merchant such as `KFC` counts as missing. The repair then replaces it only with a clean OCR reading of the same row, so the worst case is one extra OCR read.

## Tests (tests/worker/test_pipeline.py, alongside the #206 tests)

- An empty-merchant purchase line is repaired from the OCR parse.
- A country-code-only (`JPN`) purchase line is repaired.
- A refund line with an empty merchant is repaired.
- An empty-merchant `payment` line (and a `deposit` line) never starts the repair.
- An OCR merchant that is itself only a country code does not replace.
- A mixed parse (one garbled line, one empty line) repairs both in one OCR read.
- The existing #206 tests stay green.

## Left to the builder

- The exact regex spelling.
- Unit tests for the predicates, in addition to the pipeline tests.
