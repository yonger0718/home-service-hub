# Missing-merchant OCR repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A text-layer parse whose purchase/refund lines have an empty or country-code-only merchant starts the existing #206 OCR merchant repair, so HSBC statements get merchant names.

**Architecture:** Add one predicate, `missing_merchant`, plus a combined `needs_merchant_repair` in `worker/pipeline.py`. Swap it into the three places #206 uses `garbled_merchant` for the trigger, the repair list and the merge test. Tighten the clean-OCR-merchant rule so it also rejects a missing merchant. Nothing else changes.

**Tech Stack:** Python 3.13, pydantic `ParsedLine`, pytest (`tests/worker`).

**Spec:** `docs/superpowers/specs/2026-11-18-missing-merchant-ocr-repair-design.md`

## Global Constraints

- Kinds checked: `("purchase", "refund")` only.
- Missing means `merchant_raw.strip()` is empty, or fully matches `[A-Z]{2,3}`.
- The merge key stays `(txn_date, posted_date, printed_amount, line_kind)`, and it must be unique on both the text side and the OCR side.
- Amounts, dates, kinds and line count always come from the text layer.
- No `parser_version` or fingerprint change, and no backfill.
- These stay byte-identical: the note format `ocr: merchant repair {repaired}/{len(targets)} lines …`, the counters `ocr_merchant_repair` and `merchant_lines_repaired`, and the label `claude-cli+ocr-merchants`.
- Test recipe: from `services/accounting-service`, eval the DB vars plus `OTEL_SERVICE_NAME_ACCOUNTING|OTEL_COLLECTOR_ENDPOINT_GRPC`, then run `PYTHONPATH=$PWD/../shared-python-lib .venv/bin/python -m pytest tests/worker -q`.

## Review Focus

- Lines of the kinds `deposit`/`withdrawal`/`transfer_*`/`payment`/`fee` print no merchant. The gate is the line kind (`MERCHANT_KINDS`: purchase / refund), not the statement type. They must never start OCR. Covered by `test_blank_payment_or_deposit_never_starts_the_repair`.
- A lowercase or mixed-case short merchant (`Uber`, `ikea`) is a real name and is not missing. Covered by the predicate unit test.
- `KFC` counts as missing (accepted). The worst case is one OCR read; it is replaced only by a clean OCR reading. Covered by the predicate unit test, which states the accepted case.
- Whitespace-only merchants (`"  "`) count as missing. Covered by the predicate unit test.
- An OCR merchant that is itself only `JPN` must not replace. Covered by `test_country_code_ocr_merchant_does_not_replace`.

---

### Task 1: missing-merchant trigger for the #206 repair

**Files:**
- Modify: `services/accounting-service/worker/pipeline.py` (`garbled_merchant` area ~:415, `_repair_merchants` ~:432-486, the `_parse_submit` trigger ~:515)
- Test: `services/accounting-service/tests/worker/test_pipeline.py` (next to the #206 tests ~:1009-1110)

**Interfaces:**
- Produces:
  - `missing_merchant(line: parse_schema.ParsedLine) -> bool`
  - `needs_merchant_repair(line: parse_schema.ParsedLine) -> bool`
  - `garbled_merchant(merchant: str) -> bool` is unchanged.

- [ ] **Step 1: Generalise the test helper.** `_garbled_text_parse` gains these keyword arguments, and its defaults keep every existing #206 test unchanged:
  - `text_merchant="(cid:222)(cid:221)X"`: line 1's text-side merchant.
  - `text_kind=None`: overrides line 1's `line_kind` on both sides when given.
  - `second_text_merchant=None` / `second_ocr_merchant=None`: line 2's merchant on the text and OCR sides, when given.

- [ ] **Step 2: Write the failing tests**

```python
@pytest.mark.parametrize("merchant,kind,expected", [
    ("", "purchase", True), ("  ", "refund", True), ("JPN", "purchase", True), ("US", "purchase", True),
    ("KFC", "purchase", True),  # accepted false positive (spec): costs one OCR read
    ("Uber", "purchase", False), ("ikea", "purchase", False), ("USAA", "purchase", False), ("咖啡店", "purchase", False),
    ("", "payment", False), ("", "deposit", False), ("", "fee", False), ("JPN", "transfer_in", False),
])
def test_missing_merchant(merchant, kind, expected): ...  # build a ParsedLine from a fixture line; assert pipeline.missing_merchant(line) is expected

def test_empty_merchant_is_repaired_from_the_ocr_parse(...):          # text_merchant=""  → calls == ["text","ocr"], merchants == "咖啡店|BOOKS",
                                                                       # parser "claude-cli+ocr-merchants", notes start "ocr: merchant repair 1/1 lines"
def test_country_code_merchant_is_repaired(...):                      # text_merchant="JPN" → same expectations
def test_empty_refund_merchant_is_repaired(...):                      # text_merchant="", text_kind="refund" → merchant repaired (guardrail may hold the
                                                                       # revision; assert merchants and merchant_lines_repaired == 1)
def test_blank_payment_or_deposit_never_starts_the_repair(...):       # parametrize text_kind in ("payment","deposit"), text_merchant="" →
                                                                       # calls == ["text"], services.ocr.starts == 0, summary["ocr_merchant_repair"] == 0
def test_country_code_ocr_merchant_does_not_replace(...):             # text_merchant="", ocr_merchant="JPN" → merchants starts with "|"... i.e. line 1
                                                                       # merchant stays "", parser "claude-cli", notes start "ocr: merchant repair 0/1 lines"
def test_garbled_and_empty_lines_repair_in_one_ocr_read(...):         # line1 garbled, second_text_merchant="", second_ocr_merchant="書店" →
                                                                       # services.ocr.starts == 1, merchant_lines_repaired == 2, merchants == "咖啡店|書店",
                                                                       # notes start "ocr: merchant repair 2/2 lines"
```

- [ ] **Step 3: Confirm the new tests fail.** Run `pytest tests/worker/test_pipeline.py -k "missing_merchant or empty or country_code or blank_payment or one_ocr_read" -q`. Expected: FAIL, with `missing_merchant` undefined and no OCR start for empty merchants.

- [ ] **Step 4: Implement in `worker/pipeline.py`:**
  - Add `MERCHANT_KINDS = ("purchase", "refund")` and a module regex for the country code (`[A-Z]{2,3}`, full match).
  - Add `missing_merchant(line)` and `needs_merchant_repair(line)`.
  - In `_parse_submit`, the trigger becomes `any(needs_merchant_repair(l) for l in parsed.lines)`.
  - In `_repair_merchants`, `garbled = [...]` becomes `targets = [l for l in parsed.lines if needs_merchant_repair(l)]`, and the note uses `len(targets)`.
  - The merge test uses `needs_merchant_repair(line)`.
  - An OCR candidate is clean when all of these hold: it is `not garbled_merchant(...)`, `found[0].merchant_raw.strip()` is non-empty, and `not missing_merchant(found[0])`.
  - Update the docstrings of `garbled_merchant` and `_repair_merchants` to name #387.

- [ ] **Step 5: Run `tests/worker` in full.** Expected: all pass, the #206 tests included.

- [ ] **Step 6: Commit**

```bash
git add services/accounting-service/worker/pipeline.py services/accounting-service/tests/worker/test_pipeline.py
git commit -m "fix(worker): OCR-repair missing card merchants, not only garbled ones (#387)"
```
