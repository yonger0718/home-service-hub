# Composite Statement Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One 國泰世華 綜合月對帳單 PDF becomes one bank statement per mapped deposit account, built deterministically from the PDF's word geometry, with no model call.

**Architecture:** The worker gains `pdf.extract_lines` (words with x-ranges, grouped into lines) and `worker/composite.py` (split into sections, balance-chain verification, revision bodies). The API gains the `composite_map` setting, a section-aware folder check, an explicit NULL `statement_file.account_id` on composite files and an idempotent replay of persisted-equal resubmissions. The pipeline posts one revision per section and writes the file status once after the loop. No migration; `parser_version`, `INSTRUCTION` and the parser schema are untouched.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy, pydantic v2, pdfplumber 0.11.10, pytest (serial suite ≈ 12 min: `timeout 1500 .venv/bin/python -m pytest -q -p no:cacheprovider tests`).

**Spec:** `docs/superpowers/specs/2026-10-18-composite-statement-split-design.md` (rev 7).

All paths below are relative to `services/accounting-service/` unless they start with `docs/`.

## Global Constraints

- No database migration. No change to `parser_version`, `worker/parse_schema.INSTRUCTION`, `StatementParse`, OCR or the parser latch on the single-statement path. The parser is never called for a composite folder (spec §4).
- Logs, exception messages, `SplitReport` and the run summary carry section keys, counts, outcomes and reason labels only — never amounts, descriptions, dates of rows or account numbers (spec §4; the repo's R5a rule: statement content never prints through a repr or a log).
- Statement data never enters the repo: every fixture is synthetic (owner rule; spec §8).
- Money is `Decimal`; amounts are quantised to 4 places by the API (`derive.QUANTUM`); the worker sends amounts as strings.
- Bank sign rule (`app/services/statements/derive.py:13-14, 77-87`): `deposit`/`interest` positive, `withdrawal`/`fee` negative, a zero `printed_amount` only with `line_kind = "unknown"`.
- `retry_due` (`worker/pipeline.py:151-172`) is unchanged; `mapping_version` is the only re-queue trigger for `mapping` failures.
- Commits end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Never print `.env` values; no real PDFs in tests.

## Review Focus

1. A dated row that the geometry assigns to the wrong column on the first row of a section (the chain cannot confirm it) — Task 2 tests a first-row withdrawal and a first-row deposit with the real x-ranges.
2. A section cut by a page break whose continuation page starts with a page number and a repeated header — Task 2 fixture page 3.
3. A replay against a statement whose newest revision is not the current one — Task 6 test.
4. A composite file whose folder is in both maps or whose two keys point at one account — Task 4 tests (422 naming both keys).
5. A later section's 422 after an earlier section posted, then an operator re-queue — Task 7 test (file `failed/parse`, earlier statement kept, re-run replays it).

---

### Task 1: `pdf.extract_lines` and the word-placing PDF generator

**Files:**
- Modify: `worker/pdf.py` (after `Extracted`, before `unlock`)
- Modify: `tests/worker/pdfgen.py`
- Test: `tests/worker/test_pdf.py`

**Interfaces:**
- Produces: `pdf.Word(text: str, x0: float, x1: float)`, `pdf.Line(page: int, top: float, words: tuple[Word, ...])` with property `text`, `pdf.extract_lines(data: bytes, password: str | None, *, max_pages=40, max_bytes=20_000_000, timeout_s=60) -> list[Line]`; `pdfgen.make_pdf_words(pages: list[list[tuple[float, float, str]]]) -> bytes` (x, y, ASCII text per word).

- [ ] **Step 1: Write the failing tests**

Append to `tests/worker/test_pdf.py`:

```python
from worker.pdf import extract_lines
from pdfgen import make_pdf_words


def test_extract_lines_groups_words_by_top_and_sorts_by_x():
    page1 = [(40, 780, "2026/09/01"), (229, 780, "TWD"), (330, 780, "1,000"), (530, 780, "9,000"),
             (40, 760, "2026/09/02"), (229, 760, "TWD"), (420, 760, "500"), (530, 760, "9,500")]
    page2 = [(40, 780, "3"), (60, 780, "2026/09/03")]
    data = make_pdf_words([page1, page2])
    lines = extract_lines(data, None)
    assert [line.page for line in lines] == [1, 1, 2]
    first = lines[0]
    assert [w.text for w in first.words] == ["2026/09/01", "TWD", "1,000", "9,000"]
    assert first.text == "2026/09/01 TWD 1,000 9,000"
    assert first.words[2].x0 < first.words[3].x0 and first.words[2].x1 < first.words[3].x1
    assert lines[1].top > first.top and [w.text for w in lines[2].words] == ["3", "2026/09/03"]


def test_extract_lines_keeps_the_extract_bounds():
    data = make_pdf_words([[(40, 780, "x")]])
    with pytest.raises(pdf.TooLarge):
        extract_lines(data, None, max_bytes=10)
    with pytest.raises(pdf.Malformed):
        extract_lines(b"%PDF-1.4 not a pdf", None)
    locked = encrypt(make_pdf([statement_lines()]), "pw")
    with pytest.raises(pdf.PasswordError):
        extract_lines(locked, "wrong")
    assert extract_lines(locked, "pw")[0].words[0].text.startswith("2026/09/")


def test_extract_lines_timeout(monkeypatch):
    import time
    import pdfplumber

    def slow(self, *a, **k):
        time.sleep(3)
        return []

    monkeypatch.setattr(pdfplumber.page.Page, "extract_words", slow)
    with pytest.raises(pdf.ExtractTimeout):
        extract_lines(make_pdf_words([[(40, 780, "x")]]), None, timeout_s=1)
```

(`pytest`, `pdf`, `encrypt`, `make_pdf`, `statement_lines` are already imported at the top of the file — check the import block and add only what is missing.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/accounting-service && .venv/bin/python -m pytest -q -p no:cacheprovider tests/worker/test_pdf.py -k extract_lines`
Expected: FAIL with `ImportError: cannot import name 'extract_lines'`.

- [ ] **Step 3: Add `make_pdf_words` to `tests/worker/pdfgen.py`**

```python
def _words_stream(words: list[tuple[float, float, str]]) -> bytes:
    body = ["BT", "/F1 11 Tf"]
    for x, y, text in words:
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        body.append(f"1 0 0 1 {x} {y} Tm ({safe}) Tj")
    body.append("ET")
    return "\n".join(body).encode("latin-1")


def make_pdf_words(pages: list[list[tuple[float, float, str]]]) -> bytes:
    """One page per list; each word is placed at (x, y) in PDF points (y grows upwards). ASCII only (Helvetica)."""
    return _build(pages, _words_stream)
```

Refactor `make_pdf` so both share the object assembly: extract its body into `_build(pages, stream)` where `stream(page_content) -> bytes`, and let `make_pdf(pages)` call `_build(pages, _page_stream)`. The existing tests must keep passing.

- [ ] **Step 4: Implement `extract_lines` in `worker/pdf.py`**

Add after the `Extracted` dataclass:

```python
@dataclass(frozen=True)
class Word:
    text: str
    x0: float
    x1: float


@dataclass(frozen=True)
class Line:
    page: int
    top: float
    words: tuple[Word, ...]  # sorted by x0

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


LINE_TOLERANCE = 2.0  # pt: words whose `top` differ by at most this much are one line


def extract_lines(data: bytes, password: str | None, *, max_pages: int = 40, max_bytes: int = 20_000_000,
                  timeout_s: int = 60) -> list[Line]:
    """Words with their x-ranges, grouped into lines by `top`, in reading order. Same bounds and exception
    mapping as `extract`; used by the composite split only. Line text never reaches logs."""
    if len(data) > max_bytes:
        raise TooLarge(f"{len(data)} bytes") from None
    with _Alarm(timeout_s):
        try:
            doc = pdfplumber.open(BytesIO(data), password=password)
            with doc:
                if len(doc.pages) > max_pages:
                    raise TooLarge(f"{len(doc.pages)} pages") from None
                out: list[Line] = []
                for number, page in enumerate(doc.pages, start=1):
                    rows: list[tuple[float, list[dict]]] = []
                    for word in sorted(page.extract_words() or [], key=lambda w: (w["top"], w["x0"])):
                        if rows and abs(rows[-1][0] - word["top"]) <= LINE_TOLERANCE:
                            rows[-1][1].append(word)
                        else:
                            rows.append((word["top"], [word]))
                    for top, words in rows:
                        ordered = sorted(words, key=lambda w: w["x0"])
                        out.append(Line(number, float(top), tuple(Word(w["text"], float(w["x0"]), float(w["x1"]))
                                                                  for w in ordered)))
                return out
        except (PasswordError, TooLarge, ExtractTimeout):
            raise
        except PdfminerException as exc:
            if isinstance(exc.args[0] if exc.args else None, PDFPasswordIncorrect):
                raise PasswordError("password") from None
            raise Malformed("malformed") from None
        except Exception:  # noqa: BLE001
            raise Malformed("malformed") from None
```

Note: `extract` raises `NoTextLayer`; `extract_lines` does not (the composite path calls `extract` first, spec §4).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/worker/test_pdf.py`
Expected: PASS (all, including the pre-existing ones).

- [ ] **Step 6: Commit**

```bash
git add worker/pdf.py tests/worker/pdfgen.py tests/worker/test_pdf.py
git commit -m "worker: pdf.extract_lines with word geometry (composite split, spec §4)"
```

---

### Task 2: `worker/composite.py` — the split

**Files:**
- Create: `worker/composite.py`
- Create: `tests/worker/composite_fixture.py`
- Test: `tests/worker/test_composite.py`

**Interfaces:**
- Consumes: `pdf.Line`, `pdf.Word` (Task 1).
- Produces: `composite.SplitError(RuntimeError)`, `composite.Row`, `composite.Section`, `composite.SplitReport`, `composite.split(lines: list[pdf.Line], declared: dict[str, bool]) -> tuple[list[Section], SplitReport]`, the geometry constants used by the fixture (`HEADER`).

- [ ] **Step 1: Write the synthetic fixture builder `tests/worker/composite_fixture.py`**

```python
"""A synthetic 綜合月對帳單 as pdf.Line objects: the layout of the real statement, fictional content (owner rule)."""
from __future__ import annotations

from worker.pdf import Line, Word

# right edges measured on the real file: withdrawals end under 提出, deposits under 存入, balances under 餘額
X_DATE, X_DESC, X_CUR, X_OUT, X_IN, X_BAL = 38, 90, 229, 356, 446, 555
HEADER_WORDS = (("日期", 51, 67), ("交易說明", 132, 164), ("幣別", 229, 245), ("提出", 304, 319), ("存入", 393, 409),
                ("餘額", 498, 514))


def W(text: str, x0: float, x1: float | None = None) -> Word:
    return Word(text, x0, x1 if x1 is not None else x0 + 6 * len(text))


def L(page: int, top: float, *words: Word) -> Line:
    return Line(page, top, tuple(sorted(words, key=lambda w: w.x0)))


def header(page: int, top: float) -> Line:
    return L(page, top, *(W(t, a, b) for t, a, b in HEADER_WORDS))


def amount(text: str, right: float) -> Word:
    return Word(text, right - 6 * len(text), right)


def row(page: int, top: float, day: str, desc: str, out: str | None, inn: str | None, bal: str, cur="TWD",
        extra: tuple[Word, ...] = ()) -> Line:
    words = [W(day, X_DATE), *(W(part, X_DESC + 30 * i) for i, part in enumerate(desc.split(" "))), W(cur, X_CUR)]
    if out is not None:
        words.append(amount(out, X_OUT))
    if inn is not None:
        words.append(amount(inn, X_IN))
    words.append(amount(bal, X_BAL))
    return L(page, top, *words, *extra)


def holding(page: int, top: float, kind: str, number: str, branch: str, bal: str, cur: str | None = None) -> Line:
    words = [W(kind, 40), W(number, 120), W(branch, 240)]
    if cur:
        words.append(W(cur, 330))
    words.append(W(bal, 400))
    return L(page, top, *words)


def section_header(page: int, top: float, number: str, kind="活期存款") -> Line:
    return L(page, top, W(kind, 40), W("帳號", 100), W(number, 130))


def page_no(n: int, top: float, page: int) -> Word:
    return W(str(n), 20, 26)


# 91: 16 rows whose chain closes from opening 30,000 to closing 34,431; 59: 12 rows from 50,000 to 52,752
ROWS_91 = [("2026/09/01", "交割股款", "2,000", None, "28,000"), ("2026/09/02", "交割股款", None, "1,500", "29,500"),
           ("2026/09/03", "網銀轉帳", "500", None, "29,000"), ("2026/09/04", "交割股款", None, "3,000", "32,000"),
           ("2026/09/05", "交割股款", "1,000", None, "31,000"), ("2026/09/08", "網銀轉帳", None, "2,000", "33,000"),
           ("2026/09/09", "交割股款", "2,500", None, "30,500"), ("2026/09/10", "薪資獎金", None, "4,000", "34,500"),
           ("2026/09/11", "交割股款", "1,200", None, "33,300"), ("2026/09/12", "網銀轉帳", None, "700", "34,000"),
           ("2026/09/15", "交割股款", "3,000", None, "31,000"), ("2026/09/16", "交割股款", None, "2,600", "33,600"),
           ("2026/09/17", "存款利息", None, "31", "33,631"), ("2026/09/18", "交割股款", "1,000", None, "32,631"),
           ("2026/09/22", "網銀轉帳", None, "2,000", "34,631"), ("2026/09/23", "轉帳手續費", "200", None, "34,431")]
ROWS_59 = [("2026/09/01", "網銀轉帳", "1,000", None, "49,000"), ("2026/09/02", "電子預約", "2,000", None, "47,000"),
           ("2026/09/05", "薪資獎金", None, "8,000", "55,000"), ("2026/09/08", "無卡提款", "3,000", None, "52,000"),
           ("2026/09/10", "信用卡款", "4,000", None, "48,000"), ("2026/09/12", "網銀轉帳", None, "6,000", "54,000"),
           ("2026/09/15", "轉帳支取", "1,000", None, "53,000"), ("2026/09/18", "無卡提款", "2,000", None, "51,000"),
           ("2026/09/19", "網銀轉帳", None, "3,000", "54,000"), ("2026/09/22", "電子預約", "1,248", None, "52,752"),
           ("2026/09/25", "網銀轉帳", None, "1,000", "53,752"), ("2026/09/30", "無卡提款", "1,000", None, "52,752")]
HOLDINGS = {"91/TWD": "34,431", "59/TWD": "52,752", "68/TWD": "0", "50/JPY": "150,000", "50/USD": "2.47"}
DECLARED = {"91/TWD": False, "59/TWD": False, "68/TWD": True, "50/JPY": True}


def statement(rows_91=ROWS_91, rows_59=ROWS_59, holdings=HOLDINGS, *, split_at=10) -> list[Line]:
    """Pages: 1 title; 2 holdings (page-number word before 持有明細); 3 交易明細, header, 91 rows up to `split_at`;
    4 page number, repeated header, the rest of 91, 59 header, header, 59 rows, a trailing note line."""
    lines = [L(1, 50, W("國泰世華銀行綜合月對帳單", 40), W("2026/09/01", 300), W("~", 370), W("2026/09/30", 380)),
             L(1, 70, W("親愛的客戶您好", 40)),
             L(2, 50, page_no(1, 50, 2), W("持有明細", 40)),
             L(2, 70, W("台幣存款", 40), W("餘額", 100), W("87,183", 140), W("相較上月", 200), W("+1.00%", 260)),
             holding(2, 90, "活期存款", "123456****91", "屏東分行", holdings["91/TWD"]),
             holding(2, 100, "子帳戶", "123456****68", "屏東分行", holdings["68/TWD"]),
             holding(2, 110, "活期存款", "123456****59", "四維分行", holdings["59/TWD"]),
             L(2, 130, W("外幣存款", 40), W("餘額折合台幣", 100), W("30,000", 180), W("相較上月", 240), W("+0.10%", 300)),
             holding(2, 150, "外匯綜合活期存款", "123456****50", "屏東分行", holdings["50/JPY"], "JPY"),
             holding(2, 160, "外匯綜合活期存款", "123456****50", "屏東分行", holdings["50/USD"], "USD"),
             L(3, 50, W("交易明細", 40)),
             section_header(3, 70, "123456****91"), header(3, 90)]
    top = 110
    for r in rows_91[:split_at]:
        lines.append(row(3, top, *r))
        top += 12
    lines.append(L(3, top, W("註：上述帳戶如已動用透支額度", 40)))
    lines.append(L(4, 50, page_no(2, 50, 4), header(4, 50).words[0], *header(4, 50).words[1:]))
    top = 70
    for r in rows_91[split_at:]:
        lines.append(row(4, top, *r))
        top += 12
    lines.append(section_header(4, top, "123456****59"))
    lines.append(header(4, top + 12))
    top += 24
    for r in rows_59:
        lines.append(row(4, top, *r))
        top += 12
    lines.append(L(4, top, W("國泰綜合證券股份有限公司下單之相關交易", 40)))
    lines.append(L(4, top + 12, W("本行重要訊息", 40)))
    return lines
```

(`L(4, 50, page_no(2, 50, 4), header(4, 50).words[0], ...)` builds the "page number + repeated header on one line" case; keep it exactly: the page number word sits at x 20–26, left of the header.)

- [ ] **Step 2: Write the failing tests `tests/worker/test_composite.py`**

```python
from decimal import Decimal

import pytest

from composite_fixture import (DECLARED, HOLDINGS, ROWS_59, ROWS_91, L, W, X_BAL, X_IN, X_OUT, amount, header, row,
                               section_header, statement)
from worker import composite
from worker.composite import SplitError


def _split(lines=None, declared=DECLARED):
    return composite.split(lines if lines is not None else statement(), declared)


def test_fixture_splits_into_two_row_sections_and_two_anchors():
    sections, report = _split()
    by_key = {s.key: s for s in sections}
    assert list(by_key) == ["91/TWD", "59/TWD", "68/TWD", "50/JPY"]
    assert len(by_key["91/TWD"].rows) == 16 and len(by_key["59/TWD"].rows) == 12
    assert by_key["91/TWD"].period_start.isoformat() == "2026-09-01" and by_key["91/TWD"].period_end.isoformat() == "2026-09-30"
    assert by_key["91/TWD"].opening_balance == Decimal("30000") and by_key["91/TWD"].closing_balance == Decimal("34431")
    assert by_key["59/TWD"].opening_balance == Decimal("50000") and by_key["59/TWD"].closing_balance == Decimal("52752")
    assert by_key["68/TWD"].rows == () and by_key["68/TWD"].opening_balance == by_key["68/TWD"].closing_balance == Decimal("0")
    assert by_key["50/JPY"].currency == "JPY" and by_key["50/JPY"].closing_balance == Decimal("150000")
    assert report.skipped_keys == ["50/USD"] and report.rows_per_key == {"91/TWD": 16, "59/TWD": 12}
    assert report.sections == 4 and report.anchor == 2


def test_rows_carry_direction_from_the_column_geometry():
    sections, _ = _split()
    first, second = sections[0].rows[0], sections[0].rows[1]
    assert first.withdrawal == Decimal("2000") and first.deposit is None and first.balance == Decimal("28000")
    assert second.deposit == Decimal("1500") and second.withdrawal is None
    assert first.description == "交割股款" and first.currency == "TWD" and first.txn_date.isoformat() == "2026-09-01"


def test_first_row_deposit_is_read_from_geometry():
    rows = [("2026/09/01", "薪資獎金", None, "1,000", "31,000")] + [("2026/09/02", "交割股款", "1,000", None, "30,000")]
    holdings = {**HOLDINGS, "91/TWD": "30,000"}
    sections, _ = _split(statement(rows_91=rows, holdings=holdings, split_at=2))
    assert sections[0].rows[0].deposit == Decimal("1000") and sections[0].opening_balance == Decimal("30000")


def test_report_and_errors_carry_keys_and_counts_only():
    _, report = _split()
    assert "34,431" not in repr(report) and "交割" not in repr(report)
    bad = [("2026/09/01", "交割股款", "2,000", None, "99,999")] + ROWS_91[1:]
    with pytest.raises(SplitError) as info:
        _split(statement(rows_91=bad))
    assert "91/TWD" in str(info.value) and "99,999" not in str(info.value) and "交割" not in str(info.value)


@pytest.mark.parametrize("mutate, message", [
    (lambda lines: [l for l in lines if "綜合月對帳單" not in l.text], "period"),
    (lambda lines: lines + [lines[4]], "duplicate holdings"),
])
def test_period_and_duplicate_holdings_fail(mutate, message):
    with pytest.raises(SplitError, match=message):
        _split(mutate(statement()))


def test_balance_chain_violations_fail_closed():
    doctored = [*ROWS_91[:5], ("2026/09/08", "網銀轉帳", None, "2,000", "33,001"), *ROWS_91[6:]]
    with pytest.raises(SplitError, match=r"91/TWD.*row 5"):
        _split(statement(rows_91=doctored))
    with pytest.raises(SplitError, match=r"91/TWD.*closing"):
        _split(statement(holdings={**HOLDINGS, "91/TWD": "34,432"}))


def test_row_shape_violations_fail_closed():
    both = row(3, 110, "2026/09/01", "交割股款", "2,000", "1,000", "28,000")
    neither = row(3, 110, "2026/09/01", "交割股款", None, None, "28,000")
    two_balances = row(3, 110, "2026/09/01", "交割股款", "2,000", None, "28,000", extra=(amount("1", X_BAL + 40),))
    no_currency = L(3, 110, W("2026/09/01", 38), W("交割股款", 90), amount("2,000", X_OUT), amount("28,000", X_BAL))
    trailing_word = row(3, 110, "2026/09/01", "交割股款", "2,000", None, "28,000", extra=(W("x", 600),))
    negative = row(3, 110, "2026/09/01", "交割股款", "-2,000", None, "28,000")
    for bad in (both, neither, two_balances, no_currency, trailing_word, negative):
        lines = statement()
        index = next(i for i, l in enumerate(lines) if l.page == 3 and l.top == 110)
        lines[index] = bad
        with pytest.raises(SplitError, match="91/TWD"):
            _split(lines)


def test_mixed_currency_and_unknown_currency_fail():
    lines = statement()
    index = next(i for i, l in enumerate(lines) if l.page == 3 and l.top == 122)
    lines[index] = row(3, 122, *ROWS_91[1][:4], ROWS_91[1][4], cur="JPY")
    with pytest.raises(SplitError, match="currency"):
        _split(lines)


def test_page_number_and_repeated_header_continue_the_section():
    sections, _ = _split(statement(split_at=3))
    assert len(sections[0].rows) == 16  # 3 rows on page 3, 13 after the page number + repeated header on page 4


def test_dated_row_outside_a_section_and_before_a_header_fail():
    lines = statement()
    lines.insert(11, row(3, 60, "2026/09/01", "交割股款", "1", None, "1"))  # after 交易明細, before any section header
    with pytest.raises(SplitError, match="outside"):
        _split(lines)
    lines = statement()
    header_index = next(i for i, l in enumerate(lines) if l.page == 3 and l.top == 90)
    del lines[header_index]
    with pytest.raises(SplitError, match="geometry"):
        _split(lines)


def test_unknown_line_closes_the_section_and_a_later_row_fails():
    lines = statement()
    index = next(i for i, l in enumerate(lines) if l.page == 3 and l.top == 134)
    lines.insert(index, L(3, 133, W("本月優惠活動", 40)))
    with pytest.raises(SplitError, match="outside"):
        _split(lines)


def test_declaration_mismatches_fail_and_undeclared_without_rows_is_skipped():
    with pytest.raises(SplitError, match="anchor_only"):
        _split(declared={**DECLARED, "91/TWD": True})
    with pytest.raises(SplitError, match="no section"):
        _split(declared={**DECLARED, "68/TWD": False})
    with pytest.raises(SplitError, match="undeclared"):
        _split(declared={k: v for k, v in DECLARED.items() if k != "59/TWD"})
    with pytest.raises(SplitError, match="holdings"):
        _split(declared={**DECLARED, "77/TWD": True})
    _, report = _split(declared={k: v for k, v in DECLARED.items() if k != "50/JPY"})
    assert sorted(report.skipped_keys) == ["50/JPY", "50/USD"]


def test_same_suffix_header_repeated_merges_rows():
    lines = statement(split_at=10)
    index = next(i for i, l in enumerate(lines) if l.page == 4 and l.top == 50)
    lines.insert(index + 1, section_header(4, 55, "123456****91"))
    sections, _ = _split(lines)
    assert len(sections[0].rows) == 16


def test_section_with_rows_of_several_currencies_in_one_suffix_fails():
    lines = statement()
    index = next(i for i, l in enumerate(lines) if l.page == 4 and l.top == 70)
    lines[index] = row(4, 70, *ROWS_91[10][:4], ROWS_91[10][4], cur="USD")
    with pytest.raises(SplitError, match="currency"):
        _split(lines)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/worker/test_composite.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'worker.composite'`.

- [ ] **Step 4: Implement `worker/composite.py` (split half)**

```python
"""Composite bank statement split (國泰世華 綜合月對帳單): one PDF, one section per deposit account, built from the
word geometry of the text layer — no model call. Spec: docs/superpowers/specs/2026-10-18-composite-statement-split-design.md §4.

Every SplitError names a section key and a row index at most; never amounts, descriptions or account numbers.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from worker.pdf import Line, Word

PERIOD = re.compile(r"綜合月對帳單 (\d{4}/\d{2}/\d{2}) ~ (\d{4}/\d{2}/\d{2})")
HOLDING = re.compile(r"^(活期存款|子帳戶|外匯綜合活期存款) (\S+) (\S+分行) (?:([A-Z]{3}) )?(-?[\d,]+(?:\.\d+)?)$")
SECTION = re.compile(r"^(?:活期存款|外匯綜合活期存款) 帳號 (\S+)$")
DATE = re.compile(r"^\d{4}/\d{2}/\d{2}$")
CURRENCY = re.compile(r"^[A-Z]{3}$")
AMOUNT = re.compile(r"^-?\d{1,3}(?:,\d{3})*(?:\.\d+)?$")
PAGE_NO = re.compile(r"^\d{1,2}$")
HEADER = ("日期", "交易說明", "幣別", "提出", "存入", "餘額")
FOOTER = re.compile(r"^(註：|台幣存款 餘額|外幣存款 餘額折合台幣)")
AFTER_PAGE_NO = ("持有明細", "交易明細", "活期存款", "外匯綜合活期存款", "日期")


class SplitError(RuntimeError):
    pass


@dataclass(frozen=True)
class Row:
    txn_date: date
    description: str
    currency: str
    withdrawal: Decimal | None
    deposit: Decimal | None
    balance: Decimal


@dataclass(frozen=True)
class Section:
    key: str
    currency: str
    period_start: date
    period_end: date
    closing_balance: Decimal
    opening_balance: Decimal
    rows: tuple[Row, ...]


@dataclass
class SplitReport:
    sections: int = 0
    anchor: int = 0
    skipped_keys: list[str] = field(default_factory=list)
    rows_per_key: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Geometry:
    x_in: float   # x0 of 存入
    x_bal: float  # x0 of 餘額


def _words(line: Line) -> tuple[Word, ...]:
    words = line.words
    if len(words) >= 2 and PAGE_NO.match(words[0].text) and (
            words[1].text in AFTER_PAGE_NO or DATE.match(words[1].text) or "綜合月對帳單" in words[1].text):
        return words[1:]
    return words


def _date(text: str) -> date:
    y, m, d = (int(p) for p in text.split("/"))
    return date(y, m, d)


def _amount(text: str, key: str, index: int) -> Decimal:
    if not AMOUNT.match(text):
        raise SplitError(f"{key}: row {index} has a non-numeric column")
    value = Decimal(text.replace(",", ""))
    if value < 0:
        raise SplitError(f"{key}: row {index} prints a negative amount")
    return value


def _suffix(number: str) -> str:
    tail = number.rsplit("*", 1)[-1]
    return tail if tail else number


def _geometry(words: tuple[Word, ...]) -> Geometry:
    by_text = {w.text: w for w in words}
    return Geometry(x_in=by_text["存入"].x0, x_bal=by_text["餘額"].x0)


def _row(words: tuple[Word, ...], geometry: Geometry, suffix: str, index: int) -> Row:
    key = f"{suffix}/?"
    cur = next((i for i, w in enumerate(words) if i > 0 and CURRENCY.match(w.text)), None)
    if cur is None:
        raise SplitError(f"{key}: row {index} has no currency word")
    key = f"{suffix}/{words[cur].text}"
    description = " ".join(w.text for w in words[1:cur])
    out = inn = bal = None
    for w in words[cur + 1:]:
        value = _amount(w.text, key, index)
        if w.x1 <= geometry.x_in:
            out = value if out is None else _dup(key, index)
        elif w.x1 <= geometry.x_bal:
            inn = value if inn is None else _dup(key, index)
        else:
            bal = value if bal is None else _dup(key, index)
    if bal is None or (out is None) == (inn is None):
        raise SplitError(f"{key}: row {index} does not have one amount column and one balance")
    return Row(_date(words[0].text), description, words[cur].text, out, inn, bal)


def _dup(key: str, index: int):
    raise SplitError(f"{key}: row {index} fills a column twice")


def split(lines: list[Line], declared: dict[str, bool]) -> tuple[list[Section], SplitReport]:
    period: tuple[date, date] | None = None
    holdings: dict[str, Decimal] = {}
    in_holdings = in_area = False
    geometry: Geometry | None = None
    open_suffix: str | None = None
    rows: dict[str, list[Row]] = {}  # suffix -> rows, in document order of first appearance
    for line in lines:
        words = _words(line)
        if not words:
            continue
        text = " ".join(w.text for w in words)
        if period is None:
            match = PERIOD.search(text)
            if match:
                period = (_date(match.group(1)), _date(match.group(2)))
            continue
        if text == "持有明細":
            in_holdings = True
            continue
        if text == "交易明細":
            in_holdings, in_area = False, True
            continue
        if in_holdings:
            match = HOLDING.match(text)
            if match:
                key = f"{_suffix(match.group(2))}/{match.group(4) or 'TWD'}"
                if key in holdings:
                    raise SplitError(f"duplicate holdings row for {key}")
                holdings[key] = _amount(match.group(5), key, 0) if not match.group(5).startswith("-") else Decimal(
                    match.group(5).replace(",", ""))
            continue
        if not in_area:
            continue
        if tuple(w.text for w in words) == HEADER:
            geometry = _geometry(words)
            continue
        match = SECTION.match(text)
        if match:
            open_suffix = _suffix(match.group(1))
            rows.setdefault(open_suffix, [])
            continue
        if DATE.match(words[0].text):
            if open_suffix is None:
                raise SplitError("dated row outside any section")
            if geometry is None:
                raise SplitError(f"{open_suffix}/?: dated row before any column header (no geometry)")
            rows[open_suffix].append(_row(words, geometry, open_suffix, len(rows[open_suffix])))
            continue
        if open_suffix is not None and FOOTER.match(text):
            continue
        open_suffix = None  # any other line closes the section
    if period is None:
        raise SplitError("no statement period line")
    report = SplitReport()
    sections: list[Section] = []
    for suffix, section_rows in rows.items():
        if not section_rows:
            raise SplitError(f"{suffix}/?: section header without rows")
        currencies = {r.currency for r in section_rows}
        if len(currencies) != 1:
            raise SplitError(f"{suffix}/?: rows of mixed currency")
        key = f"{suffix}/{section_rows[0].currency}"
        if key not in holdings:
            raise SplitError(f"{key}: no holdings row")
        if key not in declared:
            raise SplitError(f"{key}: undeclared section with rows")
        if declared[key]:
            raise SplitError(f"{key}: rows under an anchor_only key")
        sections.append(_section(key, section_rows, holdings[key], period))
        report.rows_per_key[key] = len(section_rows)
    for key, anchor_only in declared.items():
        if key not in holdings:
            raise SplitError(f"{key}: declared key has no holdings row")
        if not anchor_only and key not in report.rows_per_key:
            raise SplitError(f"{key}: declared section has no section")
        if anchor_only:
            sections.append(Section(key, key.split("/")[1], period[0], period[1], holdings[key], holdings[key], ()))
            report.anchor += 1
    report.skipped_keys = [key for key in holdings if key not in declared]
    report.sections = len(sections)
    return sections, report


def _section(key: str, rows: list[Row], closing: Decimal, period: tuple[date, date]) -> Section:
    previous: Decimal | None = None
    for index, r in enumerate(rows):
        delta = (r.deposit or Decimal(0)) - (r.withdrawal or Decimal(0))
        if previous is not None and r.balance != previous + delta:
            raise SplitError(f"{key}: running balance breaks at row {index}")
        previous = r.balance
    if previous != closing:
        raise SplitError(f"{key}: last balance differs from the holdings closing balance")
    first = rows[0]
    opening = first.balance + (first.withdrawal or Decimal(0)) - (first.deposit or Decimal(0))
    return Section(key, first.currency, period[0], period[1], closing, opening, tuple(rows))
```

Note on the holdings amount: `HOLDING` allows a leading `-` (an overdrawn balance prints negative there); `_amount` refuses negatives for rows only, so holdings parse the sign directly as shown.

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/worker/test_composite.py`
Expected: PASS. If a `match=` regex in a test does not hit the message you wrote, align the message to the test (the messages above are the contract: key, row index, reason words `period`, `duplicate holdings`, `row N`, `closing`, `currency`, `outside`, `geometry`, `anchor_only`, `no section`, `undeclared`, `holdings`).

- [ ] **Step 6: Commit**

```bash
git add worker/composite.py tests/worker/composite_fixture.py tests/worker/test_composite.py
git commit -m "worker: composite split of 綜合月對帳單 sections from word geometry (spec §4)"
```

---

### Task 3: `worker/composite.py` — revision bodies

**Files:**
- Modify: `worker/composite.py`
- Test: `tests/worker/test_composite.py`

**Interfaces:**
- Consumes: `Section`, `Row` (Task 2); `mask.Masker.text(s, extra_literals)` (`worker/mask.py:186`).
- Produces: `composite.bodies(sections, *, file_id: int, targets: dict[str, dict], parser_version: str, masker, candidates: list[str]) -> list[tuple[str, dict]]` — `(section key, POST /statements/revisions body)` for every section whose key is in `targets`; `composite.line_kind(row) -> str`; constants `PARSER_ROWS = "composite-rows"`, `PARSER_ANCHOR = "composite-anchor"`, `KIND_TABLE`.

- [ ] **Step 1: Write the failing tests** (append to `tests/worker/test_composite.py`)

```python
from worker import mask, passwords
from worker.composite import PARSER_ANCHOR, PARSER_ROWS, Row, bodies, line_kind


def _row(desc, out=None, inn=None, cur="TWD"):
    from datetime import date as _d
    return Row(_d(2026, 9, 1), desc, cur, Decimal(out) if out else None, Decimal(inn) if inn else None, Decimal(1))


def test_line_kind_is_direction_first_and_exact_match_only():
    assert line_kind(_row("交割股款", out="10")) == "withdrawal"
    assert line_kind(_row("薪資獎金", inn="10")) == "deposit"
    assert line_kind(_row("存款利息", inn="10")) == "interest"
    assert line_kind(_row(" 利息 ", inn="10")) == "interest"
    assert line_kind(_row("存款　利息", inn="10")) == "interest"
    assert line_kind(_row("手續費", out="10")) == "fee"
    assert line_kind(_row("轉帳備註含利息", inn="10")) == "deposit"
    assert line_kind(_row("手續費退回", inn="10")) == "deposit"
    assert line_kind(_row("利息", out="10")) == "withdrawal"
    assert line_kind(_row("利息", inn="0")) == "unknown"


def _masker(names=()):
    return mask.Masker(passwords.Identity(id_number=None, birth_date=None, holder_names=list(names)))


def test_bodies_sign_amounts_by_the_present_cell_and_mask_descriptions():
    sections, _ = _split()
    targets = {"91/TWD": {"account_id": 67, "anchor_only": False}, "59/TWD": {"account_id": 76, "anchor_only": False},
               "68/TWD": {"account_id": 68, "anchor_only": True}, "50/JPY": {"account_id": 81, "anchor_only": True}}
    out = dict(bodies(sections, file_id=5, targets=targets, parser_version="v1", masker=_masker(), candidates=[]))
    body = out["91/TWD"]
    assert body["file_id"] == 5 and body["account_id"] == 67 and body["kind"] == "bank" and body["parser"] == PARSER_ROWS
    assert body["parser_version"] == "v1" and body["currency"] == "TWD"
    assert body["period_start"] == "2026-09-01" and body["period_end"] == "2026-09-30" == body["closing_date"]
    assert body["opening_balance"] == "30000" and body["statement_total"] == "34431" and body["minimum_payment"] is None
    assert body["raw"] == {"section": "91/TWD", "anchor": False, "rows": 16}
    lines = body["lines"]
    assert [l["seq"] for l in lines] == list(range(1, 17))
    assert lines[0]["printed_amount"] == "-2000" and lines[0]["line_kind"] == "withdrawal"
    assert lines[1]["printed_amount"] == "1500" and lines[1]["line_kind"] == "deposit"
    assert lines[12]["line_kind"] == "interest" and lines[15]["line_kind"] == "fee" and lines[15]["printed_amount"] == "-200"
    assert all(l["txn_date"] == l["posted_date"] and l["is_subtotal"] is False and l["foreign_amount"] is None for l in lines)
    anchor = out["68/TWD"]
    assert anchor["parser"] == PARSER_ANCHOR and anchor["lines"] == [] and anchor["opening_balance"] == anchor["statement_total"] == "0"
    assert anchor["raw"] == {"section": "68/TWD", "anchor": True, "rows": 0}
    assert out["50/JPY"]["currency"] == "JPY" and out["50/JPY"]["account_id"] == 81
    assert "50/USD" not in out


def test_bodies_mask_candidates_and_configured_names_in_descriptions():
    rows = [("2026/09/01", "轉帳 王小明 1234", None, "1,000", "31,000"), ("2026/09/02", "交割股款", "1,000", None, "30,000")]
    sections, _ = _split(statement(rows_91=rows, holdings={**HOLDINGS, "91/TWD": "30,000"}, split_at=2))
    targets = {"91/TWD": {"account_id": 67, "anchor_only": False}, "59/TWD": {"account_id": 76, "anchor_only": False},
               "68/TWD": {"account_id": 68, "anchor_only": True}, "50/JPY": {"account_id": 81, "anchor_only": True}}
    out = dict(bodies(sections, file_id=5, targets=targets, parser_version="v1", masker=_masker(["王小明"]),
                      candidates=["1234"]))
    merchant = out["91/TWD"]["lines"][0]["merchant_raw"]
    assert "王小明" not in merchant and "1234" not in merchant and "轉帳" in merchant


def test_bodies_zero_row_is_unknown_and_cancelling_rows_stay():
    rows = [("2026/09/01", "利息", None, "0", "30,000"), ("2026/09/02", "交割股款", "10", None, "29,990"),
            ("2026/09/03", "交割股款", None, "10", "30,000")]
    sections, _ = _split(statement(rows_91=rows, holdings={**HOLDINGS, "91/TWD": "30,000"}, split_at=3))
    targets = {"91/TWD": {"account_id": 67, "anchor_only": False}, "59/TWD": {"account_id": 76, "anchor_only": False},
               "68/TWD": {"account_id": 68, "anchor_only": True}, "50/JPY": {"account_id": 81, "anchor_only": True}}
    lines = dict(bodies(sections, file_id=5, targets=targets, parser_version="v1", masker=_masker(), candidates=[]))["91/TWD"]["lines"]
    assert lines[0]["printed_amount"] == "0" and lines[0]["line_kind"] == "unknown"
    assert [l["printed_amount"] for l in lines[1:]] == ["-10", "10"]
```

(`passwords.Identity` — check its constructor in `worker/passwords.py` and adjust the keyword names; the test needs an identity with `holder_names` set.)

- [ ] **Step 2: Run the tests to verify they fail**

Expected: FAIL with `ImportError: cannot import name 'bodies'`.

- [ ] **Step 3: Implement `line_kind` and `bodies`** (append to `worker/composite.py`)

```python
PARSER_ROWS = "composite-rows"
PARSER_ANCHOR = "composite-anchor"
# direction-specific, exact match on the classification view (spec §4): never a substring, never a direction change
KIND_TABLE = {"deposit": {"存款利息": "interest", "利息": "interest"}, "withdrawal": {"手續費": "fee", "轉帳手續費": "fee"}}


def _view(description: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", description).split())


def line_kind(row: Row) -> str:
    direction = "deposit" if row.deposit is not None else "withdrawal"
    amount = row.deposit if row.deposit is not None else row.withdrawal
    if amount == 0:
        return "unknown"
    return KIND_TABLE[direction].get(_view(row.description), direction)


def _money(value: Decimal) -> str:
    return format(value.normalize(), "f") if value != 0 else "0"


def _header(section: Section, *, file_id: int, account_id: int, parser: str, parser_version: str) -> dict:
    return {"file_id": file_id, "account_id": account_id, "kind": "bank", "parser": parser,
            "parser_version": parser_version, "currency": section.currency,
            "period_start": section.period_start.isoformat(), "period_end": section.period_end.isoformat(),
            "closing_date": section.period_end.isoformat(), "due_date": None,
            "opening_balance": _money(section.opening_balance), "statement_total": _money(section.closing_balance),
            "minimum_payment": None}


def bodies(sections: list[Section], *, file_id: int, targets: dict[str, dict], parser_version: str, masker,
           candidates: list[str]) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for section in sections:
        target = targets.get(section.key)
        if target is None:
            continue
        account_id = int(target["account_id"])
        if not section.rows:
            out.append((section.key, {**_header(section, file_id=file_id, account_id=account_id, parser=PARSER_ANCHOR,
                                                parser_version=parser_version),
                                      "lines": [], "raw": {"section": section.key, "anchor": True, "rows": 0}}))
            continue
        lines = []
        total = Decimal(0)
        for index, row in enumerate(section.rows):
            printed = (row.deposit if row.deposit is not None else -row.withdrawal)
            if printed != (row.deposit or Decimal(0)) - (row.withdrawal or Decimal(0)):
                raise SplitError(f"{section.key}: row {index} sign assertion failed")
            total += printed
            lines.append({"seq": index + 1, "txn_date": row.txn_date.isoformat(), "posted_date": row.txn_date.isoformat(),
                          "merchant_raw": masker.text(row.description, candidates)[:256],
                          "printed_amount": _money(printed), "foreign_amount": None, "foreign_currency": None,
                          "line_kind": line_kind(row), "installment_seq": None, "installment_total": None,
                          "is_subtotal": False})
        if total != section.closing_balance - section.opening_balance:
            raise SplitError(f"{section.key}: section sum assertion failed")
        out.append((section.key, {**_header(section, file_id=file_id, account_id=account_id, parser=PARSER_ROWS,
                                            parser_version=parser_version),
                                  "lines": lines, "raw": {"section": section.key, "anchor": False, "rows": len(lines)}}))
    return out
```

`masker.text(row.description, candidates)` — the second positional argument is `extra_literals` (`worker/mask.py:186`). Classification runs on the original description (`line_kind(row)`), masking only for `merchant_raw`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/worker/test_composite.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/composite.py tests/worker/test_composite.py
git commit -m "worker: composite revision bodies — signed by column, exact-match line kinds, masked descriptions"
```

---

### Task 4: API settings — `composite_map`, `mapping_version`, `account-map` route

**Files:**
- Modify: `app/schemas/statements.py` (`AccountMapOut` at :131, `ReconciliationSettingsIn` :404, `ReconciliationSettingsOut` :416)
- Modify: `app/services/settings_service.py` (`_reconciliation_dict` :144, `_check_account_map` :157, `mapping_version` :337, `update_reconciliation_settings` :343)
- Modify: `app/routers/statements.py` (`read_account_map`, the route at the `/statements/account-map` decorator)
- Test: `tests/integration/test_statement_ingest_api.py`

**Interfaces:**
- Produces: `CompositeTarget(account_id: Int32, anchor_only: bool = False)`; `ReconciliationSettingsIn.composite_map: dict[str, dict[str, CompositeTarget]] = {}`; `ReconciliationSettingsOut.composite_map: dict[str, dict[str, CompositeTarget]]`; `AccountMapOut.composite_map: dict[str, dict[str, CompositeTarget]] = {}`; `settings_service.mapping_version(account_map, composite_map=None) -> str`; `settings_service._check_composite_map(db, composite_map, account_map) -> dict`.

- [ ] **Step 1: Write the failing tests** (append to `tests/integration/test_statement_ingest_api.py`; `S` is the spa header, `W` the worker header, `_put` exists at the bottom of the file)

```python
def _bank(seed, db_session, name, currency="TWD", **extra):
    account = seed.account(name, is_credit=False, currency=currency, **extra)
    db_session.commit()
    return account


COMPOSITE = "manual/國泰世華"


def test_settings_put_validates_composite_map(client, card, seed, db_session):
    a, b, jpy = _bank(seed, db_session, "國泰"), _bank(seed, db_session, "薪轉"), _bank(seed, db_session, "日幣", "JPY")
    good = {COMPOSITE: {"91/TWD": {"account_id": a.id}, "59/TWD": {"account_id": b.id, "anchor_only": False},
                        "50/JPY": {"account_id": jpy.id, "anchor_only": True}}}
    ok = _put(client, {"composite_map": good})
    assert ok.status_code == 200, ok.text
    assert ok.json()["composite_map"][COMPOSITE]["91/TWD"] == {"account_id": a.id, "anchor_only": False}
    assert client.get("/settings/reconciliation", headers=S).json()["composite_map"] == ok.json()["composite_map"]
    bad = [
        ({"bad key": {"91/TWD": {"account_id": a.id}}}, "composite_map"),
        ({COMPOSITE: {"91": {"account_id": a.id}}}, "composite_map"),
        ({COMPOSITE: {"91/TWD": {"account_id": card.id}}}, "composite_map"),          # a card
        ({COMPOSITE: {"91/JPY": {"account_id": a.id}}}, "composite_map"),             # currency mismatch
        ({COMPOSITE: {"91/TWD": {"account_id": 999999}}}, "composite_map"),           # missing account
        ({COMPOSITE: {"91/TWD": {"account_id": a.id}, "59/TWD": {"account_id": a.id}}}, "composite_map"),  # duplicate target
        ({COMPOSITE: {"91/TWD": {"account_id": a.id, "anchor_only": "yes"}}}, "composite_map"),
        ({COMPOSITE: {}}, "composite_map"),
    ]
    for composite_map, loc in bad:
        response = _put(client, {"composite_map": composite_map})
        assert response.status_code == 422, (composite_map, response.text)
        assert response.json()["detail"][0]["loc"][-1] == loc, response.text
    overlap = _put(client, {"account_map": {COMPOSITE: card.id}, "composite_map": good})
    assert overlap.status_code == 422 and overlap.json()["detail"][0]["loc"][-1] == "composite_map"
    duplicate = _put(client, {"composite_map": {COMPOSITE: {"91/TWD": {"account_id": a.id}, "59/TWD": {"account_id": a.id}}}})
    assert "91/TWD" in duplicate.json()["detail"][0]["msg"] and "59/TWD" in duplicate.json()["detail"][0]["msg"]
    archived = _bank(seed, db_session, "舊", is_archived=True)
    assert _put(client, {"composite_map": {COMPOSITE: {"91/TWD": {"account_id": archived.id}}}}).status_code == 422


def test_account_map_route_carries_the_composite_map_and_versions_it(client, card, seed, db_session):
    a = _bank(seed, db_session, "國泰")
    from app.services import settings_service
    empty = settings_service.mapping_version({"mail/x/y": 1})
    assert empty == settings_service.mapping_version({"mail/x/y": 1}, {}) != settings_service.mapping_version({"mail/x/y": 1}, None) or True
    assert settings_service.mapping_version({"b": 2, "a": 1}, {COMPOSITE: {"91/TWD": {"anchor_only": False, "account_id": 1}}}) == \
        settings_service.mapping_version({"a": 1, "b": 2}, {COMPOSITE: {"91/TWD": {"account_id": 1, "anchor_only": False}}})
    before = client.get("/statements/account-map", headers=W).json()
    assert before["composite_map"] == {}
    _put(client, {"account_map": {"mail/信用卡/國泰世華": card.id}, "composite_map": {COMPOSITE: {"91/TWD": {"account_id": a.id}}}})
    after = client.get("/statements/account-map", headers=W).json()
    assert after["composite_map"] == {COMPOSITE: {"91/TWD": {"account_id": a.id, "anchor_only": False}}}
    assert after["mapping_version"] != before["mapping_version"] and len(after["mapping_version"]) == 16
    assert after["account_map"] == {"mail/信用卡/國泰世華": card.id}
```

Replace the throw-away `or True` line with the real contract: `mapping_version(m)` must equal `mapping_version(m, {})` (the envelope is always used, so an absent composite map hashes like an empty one) — write it as `assert settings_service.mapping_version({"mail/x/y": 1}) == settings_service.mapping_version({"mail/x/y": 1}, {})`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_statement_ingest_api.py -k composite`
Expected: FAIL (422 on `composite_map` as an unknown field, or 200 with no `composite_map` key).

- [ ] **Step 3: Schemas** (`app/schemas/statements.py`)

Add before `AccountMapOut`:

```python
class CompositeTarget(BaseModel):
    """One section of a composite statement folder: the account it posts to, and whether the section never
    prints transaction rows (子帳戶, foreign-currency accounts) — see the design spec §3."""
    model_config = Strict
    account_id: Int32
    anchor_only: bool = False
```

Then: `AccountMapOut.composite_map: dict[str, dict[str, CompositeTarget]] = {}`; `ReconciliationSettingsIn.composite_map: dict[str, dict[str, CompositeTarget]] = {}` (docstring: "`composite_map`: manual folder → section key `<suffix>/<CUR>` → target; a PUT replaces it"); `ReconciliationSettingsOut.composite_map: dict[str, dict[str, CompositeTarget]]`.

- [ ] **Step 4: Service** (`app/services/settings_service.py`)

Factor the key-shape check out of `_check_account_map` into `_check_folder_key(field: str, key) -> None` (same message, parameterised field name) and use it in both. Add:

```python
SECTION_KEY = re.compile(r"^[0-9]{2,4}/[A-Z]{3}$")


def _check_composite_map(db: Session, composite_map, account_map: dict) -> dict:
    """`{folder: {"<suffix>/<CUR>": {"account_id", "anchor_only"}}}`: folder keys as in account_map and never in
    both maps; targets exist, are not archived, are deposit accounts in the key's currency; one account per folder."""
    if not isinstance(composite_map, dict):
        raise ValidationError("composite_map", "must be an object")
    out: dict = {}
    for folder, targets in composite_map.items():
        _check_folder_key("composite_map", folder)
        if folder in (account_map or {}):
            raise ValidationError("composite_map", f"folder {folder!r} is also in account_map")
        if not isinstance(targets, dict) or not targets:
            raise ValidationError("composite_map", f"folder {folder!r} needs at least one section key")
        seen: dict[int, str] = {}
        for key, target in targets.items():
            if not isinstance(key, str) or not SECTION_KEY.match(key):
                raise ValidationError("composite_map", f"section key {key!r} is not <suffix>/<CUR>")
            if not isinstance(target, dict):
                raise ValidationError("composite_map", f"target for {key!r} is not an object")
            account_id, anchor_only = target.get("account_id"), target.get("anchor_only", False)
            if isinstance(account_id, bool) or not isinstance(account_id, int):
                raise ValidationError("composite_map", f"{key!r}: account_id is not an account id")
            if not isinstance(anchor_only, bool):
                raise ValidationError("composite_map", f"{key!r}: anchor_only is not a boolean")
            account = db.get(Account, account_id)
            if account is None or account.is_archived:
                raise ValidationError("composite_map", f"{key!r}: account {account_id} is missing or archived")
            if account.is_credit:
                raise ValidationError("composite_map", f"{key!r}: account {account_id} is a card")
            if account.currency != key.split("/")[1]:
                raise ValidationError("composite_map", f"{key!r}: account {account_id} is not a {key.split('/')[1]} account")
            if account_id in seen:
                raise ValidationError("composite_map", f"account {account_id} appears under {seen[account_id]!r} and {key!r}")
            seen[account_id] = key
            out.setdefault(folder, {})[key] = {"account_id": account_id, "anchor_only": anchor_only}
    return out
```

(`Account` is imported in this module already for `_check_siblings`; `re` may need importing.) In `update_reconciliation_settings` add `composite = _check_composite_map(db, data.get("composite_map", {}), data.get("account_map", {}))` after `_check_account_map` and store `"composite_map": composite` in `row.data`. In `_reconciliation_dict` add `"composite_map": data.get("composite_map") or {}`. Change `mapping_version`:

```python
def mapping_version(account_map: dict, composite_map: dict | None = None) -> str:
    """Stable 16-hex digest of both maps (the worker's `mapping_version` retry trigger). The envelope is hashed even
    when `composite_map` is empty, so the first deploy of this change re-tries every `mapping` failure once (spec §3)."""
    canonical = json.dumps({"account_map": account_map or {}, "composite_map": composite_map or {}}, sort_keys=True,
                           ensure_ascii=False, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()[:16]
```

- [ ] **Step 5: Route** (`app/routers/statements.py`, `read_account_map`)

```python
    composite_map = data.get("composite_map") or {}
    return AccountMapOut(account_map=account_map, composite_map=composite_map,
                         mapping_version=settings_service.mapping_version(account_map, composite_map),
                         closing_days=closing_days)
```

Grep for other `mapping_version(` callers (`git grep -n "mapping_version(" app tests worker`) and pass the composite map where a settings dict is at hand; a test that hashes only `account_map` keeps working (the default).

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_statement_ingest_api.py tests/integration/test_settings_api.py tests/integration/test_reconciliation_api.py`
Expected: PASS (existing settings tests may assert the exact settings payload — add `"composite_map": {}` where an exact equality fails).

- [ ] **Step 7: Commit**

```bash
git add app/schemas/statements.py app/services/settings_service.py app/routers/statements.py tests/integration/test_statement_ingest_api.py
git commit -m "api: composite_map setting, folded into mapping_version and the account-map route (spec §3)"
```

---

### Task 5: API revision service — section-aware folder check, NULL `statement_file.account_id`

**Files:**
- Modify: `app/services/statement_revision_service.py` (`_assert_folder_maps` :121, `submit_revision` :558-573)
- Modify: `app/routers/statements.py` (`submit_revision` route)
- Test: `tests/integration/test_statement_ingest_service.py`, `tests/integration/test_statement_ingest_api.py`

**Interfaces:**
- Produces: `_assert_folder_maps(db, file_id, account_id, account_map, composite_map, *, section, currency) -> bool` (True when the folder is composite); `submit_revision(db, run, payload, *, account_map, composite_map=None, origin=None)`.

- [ ] **Step 1: Write the failing tests**

In `tests/integration/test_statement_ingest_api.py` (reuse `_claim`, `_revision`, `_bank`, `COMPOSITE`):

```python
def _composite_file(client, lease, sha, path="國泰世華/2609.pdf"):
    ids = {"run_id": lease["run_id"], "lease_token": lease["lease_token"]}
    file = client.post("/statements/files", headers=W, json=ids | {"sha256": sha * 64, "size": 10, "kind": "bank", "object_key": sha * 64}).json()
    source = client.post("/statements/sources", headers=W, json=ids | {"file_id": file["id"], "root": "manual", "drive_file_id": "m" + sha,
                                                                     "drive_path": path, "drive_md5": "b" * 32, "drive_size": 10})
    assert source.status_code == 201, source.text
    return file


def _bank_revision(lease, account, section, **override):
    body = _revision(lease, account, [], "0") | {"kind": "bank", "opening_balance": "0", "currency": account.currency,
                                                 "raw": {"section": section, "anchor": True, "rows": 0}}
    return body | override


def test_composite_folder_accepts_each_declared_section_and_refuses_the_rest(client, seed, db_session):
    a, b, jpy = _bank(seed, db_session, "國泰"), _bank(seed, db_session, "薪轉"), _bank(seed, db_session, "日幣", "JPY")
    other = _bank(seed, db_session, "別家")
    assert _put(client, {"composite_map": {COMPOSITE: {"91/TWD": {"account_id": a.id}, "59/TWD": {"account_id": b.id},
                                                       "50/JPY": {"account_id": jpy.id, "anchor_only": True}}}}).status_code == 200
    lease = _claim(client)
    file = _composite_file(client, lease, "e")
    ok = client.post("/statements/revisions", headers=W, json=_bank_revision(lease, a, "91/TWD") | {"file_id": file["id"]})
    assert ok.status_code == 201, ok.text
    ok2 = client.post("/statements/revisions", headers=W, json=_bank_revision(lease, b, "59/TWD") | {"file_id": file["id"]})
    assert ok2.status_code == 201 and ok2.json()["statement_id"] != ok.json()["statement_id"]
    ok3 = client.post("/statements/revisions", headers=W, json=_bank_revision(lease, jpy, "50/JPY") | {"file_id": file["id"]})
    assert ok3.status_code == 201
    for body in (_bank_revision(lease, other, "91/TWD"),            # wrong account for the key
                 _bank_revision(lease, a, "59/TWD"),                # key points elsewhere
                 _bank_revision(lease, a, "77/TWD"),                # unmapped key
                 _bank_revision(lease, a, "91/TWD", raw={}),        # no section
                 _bank_revision(lease, jpy, "50/JPY", currency="TWD")):  # currency mismatch with the key
        response = client.post("/statements/revisions", headers=W, json=body | {"file_id": file["id"]})
        assert response.status_code == 422 and response.json()["detail"][0]["loc"][-1] in ("account_id", "kind"), response.text
    row = db_session.execute(text("select account_id from statement_file where id = :id"), {"id": file["id"]}).scalar_one()
    assert row is None


def test_composite_submission_clears_a_stale_file_account_id(client, seed, db_session):
    a = _bank(seed, db_session, "國泰")
    assert _put(client, {"composite_map": {COMPOSITE: {"91/TWD": {"account_id": a.id}}}}).status_code == 200
    lease = _claim(client)
    file = _composite_file(client, lease, "f")
    db_session.execute(text("update statement_file set account_id = :a where id = :id"), {"a": a.id, "id": file["id"]})
    db_session.commit()
    assert client.post("/statements/revisions", headers=W, json=_bank_revision(lease, a, "91/TWD") | {"file_id": file["id"]}).status_code == 201
    assert db_session.execute(text("select account_id from statement_file where id = :id"), {"id": file["id"]}).scalar_one() is None
```

(`text` from `sqlalchemy` — add the import if the file lacks it.) In `tests/integration/test_statement_ingest_service.py`, add one service-level test that the two sections of one file become two statements with independent periods/modes (one account with `statement_live_from` set, one without) using `revs.submit_revision(..., account_map={}, composite_map={...})` with a registered file + source (follow the pattern of `test_same_file_for_another_account_follows_the_folder_map` at :747 for creating the file/source rows).

- [ ] **Step 2: Run the tests to verify they fail**

Expected: 422 `account_id` on the first accepted submission (`folder maps elsewhere`).

- [ ] **Step 3: Implement**

`_assert_folder_maps`:

```python
def _assert_folder_maps(db: Session, file_id: int, account_id: int, account_map: dict[str, int],
                        composite_map: dict | None = None, *, section=None, currency: str | None = None) -> bool:
    """The file's newest source (non-removed preferred) folder `root/<first two folders>` must map to the account:
    through `account_map`, or — a composite folder — through the submitted `raw.section` key, whose target must be
    this account in this currency (spec §5). Returns True for a composite folder."""
    ... (existing lookup of `source` and `folder` unchanged) ...
    targets = (composite_map or {}).get(folder)
    if targets is not None:
        target = targets.get(section) if isinstance(section, str) else None
        if target is None or target.get("account_id") != account_id or section.split("/")[1] != currency:
            raise ValidationError("account_id", "folder maps elsewhere")
        return True
    if account_map.get(folder) != account_id:
        raise ValidationError("account_id", "folder maps elsewhere")
    return False
```

In `submit_revision` (signature `..., *, account_map, composite_map: dict | None = None, origin=None`):

```python
    if payload.file_id is not None:
        section = payload.raw.get("section") if isinstance(payload.raw, dict) else None
        composite = _assert_folder_maps(db, payload.file_id, payload.account_id, account_map, composite_map,
                                        section=section, currency=payload.currency)
        db.get(StatementFile, payload.file_id).account_id = None if composite else payload.account_id
```

Router `submit_revision`: `settings = settings_service.get_reconciliation_settings(db)`; `result = ingest.submit_revision(db, run, body, account_map=settings.get("account_map", {}), composite_map=settings.get("composite_map", {}))`.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_statement_ingest_api.py tests/integration/test_statement_ingest_service.py tests/integration/test_statement_manual.py tests/integration/test_statement_retire.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/statement_revision_service.py app/routers/statements.py tests/integration/test_statement_ingest_api.py tests/integration/test_statement_ingest_service.py
git commit -m "api: composite folders map by section key; statement_file.account_id NULL for composite files (spec §5)"
```

---

### Task 6: API idempotent replay

**Files:**
- Modify: `app/services/statement_revision_service.py` (`SubmitResult` :93, `submit_revision` after the retired check at :586)
- Modify: `app/schemas/statements.py` (`RevisionOut` :275), `app/routers/statements.py` (`_revision_out`, `submit_revision` policy call)
- Test: `tests/integration/test_statement_ingest_service.py`

**Interfaces:**
- Produces: `SubmitResult.replayed: bool = False`; `RevisionOut.replayed: bool = False`; `_replayed(db, statement, payload, derived) -> StatementRevision | None`.

- [ ] **Step 1: Write the failing tests** (append to `tests/integration/test_statement_ingest_service.py`; use `statement_fixtures._submit`, `_line`, `run`, `_card` and the module's existing imports — `_submit` returns `(statement, lines, res)` and passes `file_id=None`, which the replay compares as `None == None`)

```python
def _again(db, run, account, lines, **kw):
    return _submit(db, run, account, lines, **kw)[2]


def test_identical_resubmission_is_replayed_without_a_revision_or_a_case(seed, db_session):
    account = _card(seed, db_session, live=True)
    r = run(db_session)
    lines = [_line(1, SEP[0], "A", 100), _line(2, SEP[0], "B", 50)]
    first = _submit(db_session, r, account, lines)[2]
    replay = _again(db_session, r, account, lines)
    assert replay.replayed and replay.revision.id == first.revision.id and replay.case_ids == [] and replay.lineage_counts == {}
    assert db_session.query(StatementRevision).filter_by(statement_id=first.statement.id).count() == 1
    before = db_session.query(ReconciliationCase).count()
    first.statement.status = "reconciled"
    db_session.commit()
    replay2 = _again(db_session, r, account, lines)
    assert replay2.replayed and db_session.query(ReconciliationCase).count() == before
    assert not first.statement.conflict_open and first.statement.current_revision_id == first.revision.id
    changed = _again(db_session, r, account, [_line(1, SEP[0], "A", 100), _line(2, SEP[0], "B", 51)])
    assert not changed.replayed and changed.revision.conflict and changed.statement.conflict_open


@pytest.mark.parametrize("variant", ["seq", "foreign", "merchant", "header", "parser"])
def test_replay_needs_persisted_equality(seed, db_session, variant):
    account = _card(seed, db_session, live=True)
    r = run(db_session)
    base = [_line(1, SEP[0], "A", 100), _line(2, SEP[0], "B", 50)]
    first = _submit(db_session, r, account, base)[2]
    if variant == "seq":
        lines, kw = [_line(2, SEP[0], "A", 100), _line(1, SEP[0], "B", 50)], {}
    elif variant == "foreign":
        lines, kw = [_line(1, SEP[0], "A", 100, foreign_amount=Decimal("3"), foreign_currency="USD"), base[1]], {}
    elif variant == "merchant":
        lines, kw = [_line(1, SEP[0], "A ", 100), base[1]], {}
    elif variant == "header":
        lines, kw = base, {"closing_date": SEP[1]}
    else:
        lines, kw = base, {}
    if variant == "parser":
        rev = RevisionIn(run_id=r[0].id, lease_token=r[1], file_id=None, account_id=account.id, kind="card", parser="other",
                         parser_version="1", currency=account.currency, period_start=SEP[0], period_end=SEP[1],
                         opening_balance=None, statement_total=first.revision.statement_total, lines=base, raw={})
        res = revs.submit_revision(db_session, r[0], rev, account_map={})
    else:
        res = _again(db_session, r, account, lines, **kw)
    assert not res.replayed and res.revision.id != first.revision.id


def test_replay_compares_the_current_revision_not_the_newest(seed, db_session):
    account = _card(seed, db_session, live=True)
    r = run(db_session)
    base = [_line(1, SEP[0], "A", 100), _line(2, SEP[0], "B", 50)]
    first = _submit(db_session, r, account, base)[2]
    first.statement.status = "reconciled"
    db_session.commit()
    newer = _again(db_session, r, account, [_line(1, SEP[0], "A", 100), _line(2, SEP[0], "B", 60)])  # conflict, not current
    assert newer.revision.conflict and first.statement.current_revision_id == first.revision.id
    same_as_newer = _again(db_session, r, account, [_line(1, SEP[0], "A", 100), _line(2, SEP[0], "B", 60)])
    assert not same_as_newer.replayed  # equal to the newest, not to the current
    same_as_current = _again(db_session, r, account, base)
    assert same_as_current.replayed and same_as_current.revision.id == first.revision.id
    assert first.statement.conflict_open  # the pending conflict is untouched
```

(`StatementRevision`, `ReconciliationCase`, `RevisionIn`, `revs`, `SEP`, `Decimal` — import what the module does not already have; `_line(..., **extra)` forwards `foreign_amount`/`foreign_currency` to `LineIn`.)

- [ ] **Step 2: Run the tests to verify they fail**

Expected: FAIL with `AttributeError: 'SubmitResult' object has no attribute 'replayed'`.

- [ ] **Step 3: Implement**

`SubmitResult`: add `replayed: bool = False`. In `submit_revision`, right after the retired check (`:586-587`) and before `# 5. revision`:

```python
    if not created:
        replay = _replayed(db, statement, payload, derived)
        if replay is not None:
            return SubmitResult(statement, replay, {}, [], replayed=True)
```

```python
LINE_COLUMNS = ("seq", "txn_date", "posted_date", "merchant_raw", "printed_amount", "foreign_amount", "foreign_currency",
                "flow_amount", "line_kind", "installment_seq", "installment_total")


def _replayed(db: Session, statement: AccountStatement, payload: RevisionIn,
              derived: list[derive.DerivedLine]) -> StatementRevision | None:
    """The current revision when the payload is persisted-equal to it (spec §5): same file, parser and version,
    statement kind and currency, every header column, and every stored line column position by position."""
    if statement.current_revision_id is None:
        return None
    current = db.get(StatementRevision, statement.current_revision_id)
    if (current.file_id, current.parser_version, current.parser) != (payload.file_id, payload.parser_version, payload.parser):
        return None
    if (statement.kind, statement.currency) != (payload.kind, payload.currency):
        return None
    if any(getattr(current, column) != getattr(payload, column) for column in HEADER_COLUMNS):
        return None
    stored = db.execute(select(StatementLine).where(StatementLine.revision_id == current.id)
                        .order_by(StatementLine.seq)).scalars().all()
    if len(stored) != len(derived):
        return None
    for line, d in zip(stored, derived):
        sent = (d.line.seq, d.line.txn_date, d.line.posted_date, d.line.merchant_raw, _q(d.line.printed_amount),
                _q(d.line.foreign_amount), d.line.foreign_currency, d.flow_amount, d.line.line_kind,
                d.line.installment_seq, d.line.installment_total)
        if tuple(getattr(line, column) for column in LINE_COLUMNS) != sent:
            return None
    return current
```

`RevisionOut.replayed: bool = False`; `_revision_out` adds `"replayed": result.replayed`; in the route: `if out["mode"] == "live" and not out["replayed"]: policy_service.run_policy_safe(...)`.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_statement_ingest_service.py tests/integration/test_statement_ingest_api.py tests/integration/test_statement_lineage_transfer.py tests/integration/test_statement_confirm.py`
Expected: PASS. If an existing test resubmits identical content and expects a second revision, read it: the spec makes that a replay now — update the expectation and say so in the commit message.

- [ ] **Step 5: Commit**

```bash
git add app/services/statement_revision_service.py app/schemas/statements.py app/routers/statements.py tests/integration/test_statement_ingest_service.py
git commit -m "api: persisted-equal resubmissions replay the current revision (spec §5)"
```

---

### Task 7: Worker pipeline — the composite path

**Files:**
- Modify: `worker/api.py` (`account_map` :94)
- Modify: `worker/pipeline.py` (`Summary` :180, `process_file` :235, `_execute` :640/:680)
- Test: `tests/worker/test_api.py`, `tests/worker/test_pipeline.py`

**Interfaces:**
- Consumes: `composite.split`, `composite.bodies`, `pdf.extract_lines`.
- Produces: `ApiClient.account_map() -> tuple[dict, str, dict[int, int], dict]` (4th: `composite_map`, `{}` when the API omits it); `process_file(..., closing_days=None, composite_map=None)`; `Summary.composite_files`, `sections_parsed`, `sections_anchor`, `sections_replayed`, `sections_skipped`, `sections_failed`, `composite: list[dict]`; `_process_composite(services, lease, file_row, listed, data, password, targets, candidates, summary) -> str`.

- [ ] **Step 1: Write the failing client tests** (`tests/worker/test_api.py`)

Change `test_account_map` to unpack four values and assert the fourth is `{}`; add the composite case (PUT a `composite_map` with a seeded bank account — reuse the `seed` fixture; the key currency must match) and assert `worker_api.account_map()[3] == {"manual/國泰世華": {"91/TWD": {"account_id": <id>, "anchor_only": False}}}`; extend `test_account_map_tolerates_an_api_without_closing_days` to assert the 4-tuple `({...}, "0"*16, {}, {})`.

- [ ] **Step 2: Write the failing pipeline tests** (`tests/worker/test_pipeline.py`)

Add a fixture that puts a composite file in the manual root and replaces `pdf.extract_lines`:

```python
from composite_fixture import DECLARED, HOLDINGS, ROWS_91, statement as composite_statement
from worker import composite, pdf


@pytest.fixture
def composite_world(world, client, seed, db_session, monkeypatch):
    """The `world` drive plus one composite PDF under the manual root; `pdf.extract_lines` returns the synthetic
    statement (the ASCII PDF only has to pass unlock/extract); the parser raises if ever called."""
    services, drive_files, cfg = world
    accounts = {k: seed.account(k.replace("/", "_"), is_credit=False, currency=k.split("/")[1],
                                statement_live_from=date(2026, 9, 1) if k == "59/TWD" else None) for k in DECLARED}
    db_session.commit()
    drive_files["id-c"] = ("國泰世華/2609.pdf", make_pdf([statement_lines()]))
    composite_map = {"manual/國泰世華": {k: {"account_id": a.id, "anchor_only": DECLARED[k]} for k, a in accounts.items()}}
    client.put("/settings/reconciliation", headers={"Authorization": "Bearer spa-token"},
               json={"account_map": {"mail/信用卡/國泰世華": card_id(services)}, "composite_map": composite_map,
                     "dirty_enabled": False, "rules": {}})
    monkeypatch.setattr(pdf, "extract_lines", lambda data, password, **kw: composite_statement())
    monkeypatch.setattr(services.parser, "parse", lambda *a, **k: (_ for _ in ()).throw(AssertionError("parser called")))
    return services, drive_files, cfg, accounts
```

The `world` fixture's fake `rclone` answers `[]` for the manual root (`if not args[-1].endswith("/銀行")`); change it to serve the manual root from the same `drive_files` dict for paths that do not start with `信用卡/` or `銀行帳戶/` (the manual root is `cfg.manual_root`; check `worker/config.py:61` for its suffix and match on it). `card_id(services)` — replace with the `card` fixture's id (add `card` to the fixture parameters). Then the tests:

```python
def _statements(db_session, account_id):
    return db_session.execute(text("select id, mode, current_revision_id, opening_balance, statement_total from account_statement "
                                   "where account_id = :a order by id"), {"a": account_id}).all()


def test_composite_file_posts_one_statement_per_declared_section_without_the_parser(composite_world, db_session):
    services, drive_files, cfg, accounts = composite_world
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["composite_files"] == 1 and summary["sections_parsed"] == 2 and summary["sections_anchor"] == 2
    assert summary["sections_skipped"] == 1 and summary["sections_failed"] == 0
    assert summary["composite"][0]["sections"] == [
        {"key": "91/TWD", "outcome": "parsed", "reason": None}, {"key": "59/TWD", "outcome": "parsed", "reason": None},
        {"key": "68/TWD", "outcome": "anchor", "reason": None}, {"key": "50/JPY", "outcome": "anchor", "reason": None},
        {"key": "50/USD", "outcome": "skipped", "reason": "unmapped"}]
    rows = {r.key: r for r in db_session.execute(text(
        "select r.raw->>'section' as key, r.parser, r.guardrail_ok, s.mode, s.account_id, f.account_id as file_account "
        "from statement_revision r join account_statement s on s.id = r.statement_id join statement_file f on f.id = r.file_id "
        "where r.raw ? 'section'")).all()}
    assert set(rows) == {"91/TWD", "59/TWD", "68/TWD", "50/JPY"} and all(r.guardrail_ok for r in rows.values())
    assert rows["91/TWD"].parser == "composite-rows" and rows["68/TWD"].parser == "composite-anchor"
    assert rows["59/TWD"].mode == "live" and rows["91/TWD"].mode == "historical"
    assert all(r.file_account is None for r in rows.values())
    file = db_session.execute(text("select status, failure from statement_file where object_key like '%'||:k")).first()
    assert [r for r in _file_rows(db_session) if r.status == "parsed"]  # the composite file is parsed
    # second run: every section replays, nothing new
    again = pipeline.run(services, trigger="owner_cli")
    assert again["unchanged"] >= 1 and again.get("composite_files", 0) == 0
```

(The second-run assertion: a `parsed` file is not due, so the composite path does not run — `unchanged`. Keep the replay assertion for the re-queue test below.)

```python
def test_composite_partial_failure_keeps_earlier_sections_and_requeue_replays_them(composite_world, db_session, monkeypatch):
    services, drive_files, cfg, accounts = composite_world
    real = services.api.submit_revision
    calls = []

    def flaky(lease, body):
        calls.append(body["raw"]["section"])
        if body["raw"]["section"] == "59/TWD" and len([c for c in calls if c == "59/TWD"]) == 1:
            raise ApiError(422, "lines")  # construct per worker/api.py's ApiError signature
        return real(lease, body)

    monkeypatch.setattr(services.api, "submit_revision", flaky)
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["sections_failed"] == 1 and summary["sections_parsed"] == 1
    assert [s for s in summary["composite"][0]["sections"] if s["key"] == "59/TWD"][0] == {"key": "59/TWD", "outcome": "parse", "reason": "422"}
    assert ("failed", "parse") in _file_rows(db_session)
    assert len(_statements(db_session, accounts["91/TWD"].id)) == 1 and _statements(db_session, accounts["59/TWD"].id) == []
    db_session.execute(text("update statement_file set status = 'new', failure = null where failure = 'parse'"))
    db_session.commit()
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["sections_replayed"] == 3 and summary["sections_parsed"] == 1 and summary["sections_failed"] == 0
    assert len(_statements(db_session, accounts["91/TWD"].id)) == 1 and len(_statements(db_session, accounts["59/TWD"].id)) == 1
    assert ("parsed", None) in _file_rows(db_session)


def test_composite_split_error_fails_the_file_as_parse(composite_world, db_session, monkeypatch):
    services, *_ = composite_world
    monkeypatch.setattr(pdf, "extract_lines", lambda data, password, **kw: composite_statement(holdings={**HOLDINGS, "91/TWD": "1"}))
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["failed"] >= 1 and summary["composite"][0]["sections"] == [{"key": None, "outcome": "parse", "reason": "split_error"}]
    assert ("failed", "parse") in _file_rows(db_session)


def test_composite_guardrail_failure_is_needs_review(composite_world, db_session, monkeypatch):
    services, *_ = composite_world
    real = services.api.submit_revision

    def off_by_one(lease, body):
        if body["raw"]["section"] == "91/TWD":
            body = {**body, "statement_total": "34432"}
        return real(lease, body)

    monkeypatch.setattr(services.api, "submit_revision", off_by_one)
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["needs_review"] == 1 and ("needs_review", "guardrail") in _file_rows(db_session)
    assert [s for s in summary["composite"][0]["sections"] if s["key"] == "91/TWD"][0]["outcome"] == "guardrail"


def test_composite_runs_while_the_parser_is_latched_and_skips_ocr(composite_world, db_session, monkeypatch):
    services, *_ = composite_world
    services.parser_disabled = True
    monkeypatch.setattr(pipeline, "_execute_reset_parser_disabled", lambda s: None, raising=False)
    # the run resets parser_disabled at its start; emulate a latch that happens during the run instead:
    monkeypatch.setattr(services.gate, "ensure", lambda parser: (_ for _ in ()).throw(parser_mod.ParserDisabled("latched")))
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["composite_files"] == 1 and summary["sections_parsed"] == 2  # composite needs no parser
    monkeypatch.setattr(pdf, "extract", lambda data, password, **kw: (_ for _ in ()).throw(pdf.NoTextLayer("none")))
    db_session.execute(text("update statement_file set status = 'new', failure = null where kind = 'bank'"))
    db_session.commit()
    summary = pipeline.run(services, trigger="owner_cli")
    assert ("needs_review", "no_text_layer") in _file_rows(db_session) and summary["ocr_trigger"] == 0


def test_composite_pdf_failures_keep_their_classes(composite_world, db_session, monkeypatch):
    services, *_ = composite_world
    for exc, expected in ((pdf.PasswordError("password"), ("failed", "password")), (pdf.Malformed("m"), ("failed", "parse")),
                          (pdf.TooLarge("t"), ("needs_review", "too_large")), (pdf.ExtractTimeout("t"), ("failed", "transient"))):
        monkeypatch.setattr(pdf, "extract", lambda data, password, _e=exc, **kw: (_ for _ in ()).throw(_e))
        db_session.execute(text("update statement_file set status = 'new', failure = null, attempts = 0"))
        db_session.commit()
        pipeline.run(services, trigger="owner_cli")
        assert expected in _file_rows(db_session), expected


def test_composite_map_change_requeues_only_mapping_failures(composite_world, client, db_session, card):
    services, drive_files, cfg, accounts = composite_world
    client.put("/settings/reconciliation", headers={"Authorization": "Bearer spa-token"},
               json={"account_map": {"mail/信用卡/國泰世華": card.id}, "composite_map": {}, "dirty_enabled": False, "rules": {}})
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["ignored"] == 1 and ("ignored", "mapping") in _file_rows(db_session)
    db_session.execute(text("update statement_file set status = 'failed', failure = 'parse' where kind = 'card'"))
    db_session.commit()
    composite_map = {"manual/國泰世華": {k: {"account_id": a.id, "anchor_only": DECLARED[k]} for k, a in accounts.items()}}
    client.put("/settings/reconciliation", headers={"Authorization": "Bearer spa-token"},
               json={"account_map": {"mail/信用卡/國泰世華": card.id}, "composite_map": composite_map, "dirty_enabled": False, "rules": {}})
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["composite_files"] == 1 and ("failed", "parse") in _file_rows(db_session)  # the card file stays failed/parse
```

Trim the latch test to what the fixtures support: the goal is (a) a composite file is processed when `services.parser_disabled` is True at the time `process_file` runs, and (b) `NoTextLayer` on a composite file is `needs_review/no_text_layer` with no OCR; set `parser_disabled` through the gate monkeypatch only and drop the `_execute_reset_parser_disabled` line.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/worker/test_api.py tests/worker/test_pipeline.py -k "composite or account_map"`
Expected: FAIL (3-tuple unpacking, unknown summary keys).

- [ ] **Step 4: Implement**

`worker/api.py`:

```python
    def account_map(self) -> tuple[dict[str, int], str, dict[int, int], dict]:
        """(folder -> account id, mapping_version, account id -> closing day, composite folder -> section targets)."""
        data = self._call("GET", "/statements/account-map")
        closing_days = {int(k): int(v) for k, v in (data.get("closing_days") or {}).items()}
        return data["account_map"], data["mapping_version"], closing_days, data.get("composite_map") or {}
```

`worker/pipeline.py`: import `composite`; `Summary` gains `composite_files: int = 0`, `sections_parsed: int = 0`, `sections_anchor: int = 0`, `sections_replayed: int = 0`, `sections_skipped: int = 0`, `sections_failed: int = 0`, `composite: list = field(default_factory=list)`. In `_execute`: `account_map, mapping_version, closing_days, composite_map = services.api.account_map()` and pass `composite_map=composite_map` to `process_file`. `process_file(..., closing_days=None, composite_map=None)`:

```python
    folder = _folder_key(listed)
    account_id = account_map.get(folder)
    targets = (composite_map or {}).get(folder)
    closing_day = (closing_days or {}).get(account_id)
    if account_id is None and targets is None:
        ... ignored (unchanged) ...
    if targets is None and services.parser_disabled:
        ... skipped (unchanged) ...
    ... unlock / extract (unchanged) ...
    if no_text_layer:
        if targets is not None:  # composite: no OCR, the split needs the text layer's geometry (spec §4)
            return _review(services, lease, file_row, "no_text_layer", summary, has_text_layer=False)
        ... existing OCR branch ...
    services.api.update_file(... unlocked ... unchanged ...)
    if targets is not None:
        return _process_composite(services, lease, file_row, data, password, targets, candidates, summary)
    ... existing _parse_submit path unchanged ...
```

```python
OUTCOME_RANK = {"parsed": 0, "anchor": 0, "replayed": 0, "skipped": 0, "guardrail": 1, "parse": 2}


def _process_composite(services: Services, lease: Lease, file_row: dict, data: bytes, password: str | None,
                       targets: dict, candidates: list[str], summary: Summary) -> str:
    """Spec §4: split, one revision per declared section, the file status once after the loop."""
    summary.composite_files += 1
    report = {"file_id": file_row["id"], "sections": []}
    summary.composite.append(report)
    declared = {key: bool(target.get("anchor_only")) for key, target in targets.items()}
    try:
        lines = pdf.extract_lines(data, password)
        sections, split_report = composite.split(lines, declared)
        bodies = composite.bodies(sections, file_id=file_row["id"], targets=targets,
                                  parser_version=services.parser_version, masker=services.masker, candidates=candidates)
    except composite.SplitError as exc:
        log.info("composite split failed: %s", exc)  # keys and counts only, by SplitError's contract
        report["sections"].append({"key": None, "outcome": "parse", "reason": "split_error"})
        summary.sections_failed += 1
        return _fail(services, lease, file_row, "parse", summary, parser_version=services.parser_version)
    except pdf.PasswordError:
        return _fail(services, lease, file_row, "password", summary, credential_version=services.credential_version)
    except pdf.Malformed:
        return _fail(services, lease, file_row, "parse", summary, parser_version=services.parser_version)
    except pdf.TooLarge:
        return _review(services, lease, file_row, "too_large", summary)
    except pdf.ExtractTimeout:
        return _fail(services, lease, file_row, "transient", summary)
    for key in split_report.skipped_keys:
        report["sections"].append({"key": key, "outcome": "skipped", "reason": "unmapped"})
        summary.sections_skipped += 1
    worst = "parsed"
    for key, body in bodies:
        reason = None
        try:
            out = services.api.submit_revision(lease, body)
        except LeaseLost:
            raise
        except ApiError as exc:
            if exc.status != 422:
                raise
            outcome, reason = "parse", "422"
        else:
            summary.cases_opened += len(out.get("case_ids") or [])
            summary.live_revisions += 1 if out.get("mode") == "live" else 0
            if out.get("replayed"):
                outcome = "replayed"
            elif not out.get("guardrail_ok"):
                outcome = "guardrail"
            else:
                outcome = "anchor" if body["raw"]["anchor"] else "parsed"
        report["sections"].append({"key": key, "outcome": outcome, "reason": reason})
        log.info("composite section %s: %s", key, outcome)
        counter = {"parsed": "sections_parsed", "anchor": "sections_anchor", "replayed": "sections_replayed",
                   "guardrail": "sections_failed", "parse": "sections_failed"}[outcome]
        setattr(summary, counter, getattr(summary, counter) + 1)
        if OUTCOME_RANK[outcome] > OUTCOME_RANK[worst]:
            worst = outcome
    if worst == "parse":
        return _fail(services, lease, file_row, "parse", summary, parser_version=services.parser_version)
    if worst == "guardrail":
        return _review(services, lease, file_row, "guardrail", summary)
    services.retries.set(file_row["sha256"], transient_attempts=0)
    services.api.update_file(lease, file_row["id"], status="parsed", failure=None, parser_version=services.parser_version)
    summary.parsed += 1
    return "parsed"
```

`_fail` / `_review` increment `summary.failed` / `summary.needs_review` themselves (`:207-233`). `ApiError` and `LeaseLost` are already imported in `pipeline.py` (`grep -n "ApiError\|LeaseLost" worker/pipeline.py`).

- [ ] **Step 5: Run the worker suite**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/worker`
Expected: PASS. Then the full suite once: `timeout 1500 .venv/bin/python -m pytest -q -p no:cacheprovider tests 2>&1 | tee /dev/stderr | grep -q " failed" && echo RED || echo GREEN`.

- [ ] **Step 6: Commit**

```bash
git add worker/api.py worker/pipeline.py tests/worker/test_api.py tests/worker/test_pipeline.py
git commit -m "worker: composite folders post one revision per section, file status once after the loop (spec §4)"
```

---

### Task 8: README and operator notes

**Files:**
- Modify: `README.md` (after the "One file, one statement" paragraph at :285-301; the settings paragraph at :246; the `account-map` route description wherever `closing_days` is documented; the run summary paragraph at :505)
- Modify: `deploy/statements/README.md` (operator re-queue statement)

- [ ] **Step 1: Write the "Composite statements" paragraph** in `README.md` right after the retire paragraph:

> Composite statements (2026-10-18): a manual folder listed in `composite_map` (`PUT /settings/reconciliation`, `{"manual/國泰世華": {"91/TWD": {"account_id": 67}, "50/JPY": {"account_id": 81, "anchor_only": true}}}`; section key = the digits the bank prints after the masked account number + the currency; targets are deposit accounts in that currency, one account per folder, never a folder that is also in `account_map`) is split by the worker without a model call: the statement period and the 持有明細 closing balances come from the text layer, every dated row from the word geometry of its page (提出 / 存入 / 餘額 by the token's right edge against the column header), the running balance is verified row by row and against the holdings balance, and any ambiguity fails the file (`failed / parse`). One `POST /statements/revisions` per declared section (`parser` `composite-rows`, or `composite-anchor` with no lines for an `anchor_only` section; `raw.section`, `raw.anchor`, `raw.rows`); `_assert_folder_maps` accepts a composite file only for the submitted section's account and currency; `statement_file.account_id` stays NULL. `line_kind` is `deposit` / `withdrawal` by the filled column, `interest` / `fee` only when the whole description equals an entry of the worker's table. File status after all sections: any 422 → `failed / parse`, any failed guardrail → `needs_review / guardrail`, else `parsed`; the run summary carries `composite_files`, `sections_*` and `composite[]` (keys, outcomes, reason labels). Recovery after a split or 422 failure, or after adding a key for an already-parsed file, is the operator re-queue (`UPDATE statement_file SET status='new', failure=NULL WHERE id = …`); `retry_due` is unchanged. `mapping_version` now hashes `{"account_map", "composite_map"}` — the first deploy re-tries every `mapping` failure once. Persisted-equal resubmissions (same file, parser, version, header and stored line columns against the current revision) answer `replayed: true` without a revision or a case.

Also: `GET /statements/account-map` → add `composite_map`; the revision route → `replayed`; the summary paragraph → the new counters.

- [ ] **Step 2: Operator note** in `deploy/statements/README.md`: the re-queue SQL above and the rollout order from the spec §6 (API, worker, `statement_live_from`, `composite_map`).

- [ ] **Step 3: Commit**

```bash
git add README.md deploy/statements/README.md
git commit -m "docs: composite statements — settings, split rules, outcomes, recovery, replay"
```

---

## Self-review notes

- Spec coverage: §3 → Task 4; §4 split → Tasks 1–2; §4 bodies → Task 3; §4 posting/status/recovery → Task 7; §5 folder/NULL → Task 5; §5 replay → Task 6; §6 rollout and §8 README → Task 8; §8 tests spread over the tasks above (each spec test bullet has a named test).
- Types: `pdf.Line.words` is a tuple of `pdf.Word`; `composite.split` takes `list[Line]`; `bodies` returns `list[tuple[str, dict]]`; `ApiClient.account_map` returns a 4-tuple everywhere (Task 7 updates both callers and both tests).
- Known limitation carried into the plan: `worker/verify.py` reads `account_map` only (`:137-141`); a composite folder is reported unmapped by the dry run — out of scope (spec §7).
