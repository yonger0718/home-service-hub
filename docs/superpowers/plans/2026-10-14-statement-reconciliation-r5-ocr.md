# Statement Reconciliation R5 — OCR for statements without a text layer — Implementation Plan (rev 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Statements whose PDF has no usable text layer (Type3 glyph fonts, `(cid:NNN)` garbage) are read locally by an OCR child process, normalised, masked and guarded, then go through the unchanged sandboxed parser → guardrails path with their OCR origin recorded on the revision.

**Architecture:** `pdf.extract` treats font garbage as "no text layer". A child process (`worker/ocr_child.py`, run by `worker/ocr.py` under bwrap with no network and rlimits) renders pages with pypdfium2, reads them with RapidOCR and returns bounded JSON; `worker/ocr_text.py` holds the pure row-reconstruction and identifier-normalisation helpers; `Masker.guard_lines` is a line-level leak net. `pipeline.process_file` and `verify.export_masked` take the branch on `NoTextLayer`. One bounded parser version string (12-hex fingerprint) covers instruction, schema and OCR; the instruction gains a credit-sign rule. No migration.

**Tech Stack:** Python 3.13, pypdfium2 5.14.0, rapidocr-onnxruntime 1.4.4 (onnxruntime 1.31.0), bubblewrap, the existing worker package, pytest.

**Spec:** `docs/superpowers/specs/2026-10-08-statement-reconciliation-design.md`, section "R5 OCR design (2026-10-14, PR "R5" — rev 2 after AGENT-118)", R5.1–R5.10. The spec is the authority.

**Workspace:** worktree `/home/opc/workspace/home-hub-reconcile`, branch `feat/reconciliation-r5-ocr` (from origin/main 73e8d18). Paths below are relative to `services/accounting-service/`. Run tests with `.venv/bin/python -m pytest` from that directory. Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Global Constraints

- Statement content (page images, OCR text, PDF bytes) never reaches disk (other than verify's masked snapshots), logs, exception messages or the run summary. Child stderr is discarded. Exceptions carry fixed phrases and are raised `from None`.
- Trigger (R5.1): page-1 stripped length < 200 **or** `(cid:` tokens ≥ 10 % of page-1 tokens → `NoTextLayer`.
- Child (R5.2): bwrap `--unshare-all` with **no** `--share-net`, `--die-with-parent --new-session --cap-drop ALL`, ro-binds `/usr`, `/lib`, `/lib64`, base prefix, venv prefix, service dir; `--tmpfs /tmp --proc /proc --dev /dev`, `HOME=/tmp`, `PYTHONPATH=<service dir>`, `--chdir <service dir>`; rlimits set by the child from the header: AS (default 2048 MB), CPU (150 s), CORE 0; readiness (import, engine build, 32×32 self-test) before reading the PDF; `max_pages` 10, `max_pixels` 30 000 000 per page checked before allocation; `scale` 3.0; zero boxes on a page allowed, zero boxes overall or mean confidence < 0.80 → `low_confidence`; parent deadline `cfg.ocr_timeout_s` (120 s) including load, kill process group + reap; stdout cap 2 MB.
- Normalisation (R5.2a): merge adjacent same-row boxes with gap < 1.5 × char width (char width = 0.6 × median height): two all-digit boxes → one space; concatenation matching `[A-Z][12]\d{8}` → no space; two CJK-only boxes with concatenation ≤ 4 chars → no space. Other joins: spaces = gap / char width, clamped 1..12. `LAYOUT_VERSION` bumps on any change.
- Guard (R5.2b): a line whose whitespace-stripped form contains an identity literal (ID number, holder name ≥ 2 chars, birth pattern, password candidate ≥ 4 chars) → `[LINE MASKED]`; OCR branch only.
- Outcomes (R5.3): unavailable → `ocr_unavailable` once, file untouched, `skipped`, run `done`, CLI exit 1; timeout / low confidence → `needs_review/no_text_layer`, `has_text_layer=False`; too large → `needs_review/too_large`; malformed/crash/bad output → `failed/parse`; disabled → `needs_review/no_text_layer`.
- Provenance (R5.3): `parser = "claude-cli+ocr"`, notes line `ocr: mean_confidence=<2 dp> boxes=<n> empty_pages=<n> scale=<scale>`.
- Version (R5.4): `claude-cli-<cli>-<model>-<fp12>`, `fp12 = sha256(INSTRUCTION ‖ "\n" ‖ schema json ‖ "\n" ‖ ocr_fingerprint)[:12]`; raise if len > 64. `ocr.FINGERPRINT = "<rapidocr version>+<pypdfium2 version>+layout<LAYOUT_VERSION>"` (`"0+0+layout<n>"` when a wheel is absent).
- Instruction (R5.5): append exactly `On a card statement every negative row is a credit: a payment received is payment, a cash-back or points credit is reward, and any other reversal (退款, 取消, 分期調整 / 轉分期 reversing a purchase) is refund; never label a negative card row balance_adjustment.`
- Config (R5.7): `STATEMENT_OCR` (true), `STATEMENT_OCR_THREADS` (2), `STATEMENT_OCR_TIMEOUT` (120), `STATEMENT_OCR_RLIMIT_MB` (2048), `STATEMENT_OCR_CHILD` (unset; a command path replacing the python child, tests only).
- Pins: `rapidocr-onnxruntime==1.4.4`, `pypdfium2==5.14.0`, `onnxruntime==1.31.0` in `requirements-worker.txt`; resolved set in `requirements-worker-constraints-aarch64-py313.txt`.
- Surgical changes; match existing style (`# noqa: BLE001` on broad excepts, docstrings stating the ruling).

## Review Focus

1. Split card number across OCR boxes with multi-space padding reaching the parser or a snapshot — Task 3 `test_normalise_joins_split_digit_groups`, Task 4 `test_guard_lines_masks_split_name`, Task 7 `test_ocr_parser_stdin_carries_no_split_identifier`, Task 8 `test_export_masked_snapshot_carries_no_split_identifier`.
2. A native hang inside PDFium/onnxruntime — Task 6 `test_read_timeout_kills_and_reaps` (sleeping fake child), Task 7 `test_ocr_timeout_needs_review_and_next_file_runs`.
3. A broken host (missing renderer or engine) — Task 5 `test_run_reports_unavailable_before_reading_the_pdf`, Task 6 `test_unavailable_is_cached_for_the_run`, Task 7 `test_ocr_unavailable_leaves_files_for_the_next_run_and_exits_1`.
4. A one-page PDF with a huge MediaBox — Task 5 `test_pixel_bound_before_render`, Task 7 `test_ocr_too_many_pages_or_pixels_is_too_large`.
5. The 64-character version bound — Task 2 `test_version_is_bounded_and_changes_with_ocr`.

---

### Task 1: Dependencies, constraints file, configuration

**Files:**
- Modify: `requirements-worker.txt`; Create: `requirements-worker-constraints-aarch64-py313.txt`
- Modify: `worker/config.py` (fields after `account_map_file`; `load()`)
- Modify: `README.md` (env table after the `STATEMENT_PARSER_TIMEOUT` row, near line 365)
- Test: `tests/worker/test_config.py`

**Interfaces:**
- Produces: `WorkerConfig.ocr_enabled: bool`, `ocr_threads: int`, `ocr_timeout_s: int`, `ocr_rlimit_mb: int`, `ocr_child: Path | None`.

- [ ] **Step 1: Pins and constraints**

Append to `requirements-worker.txt`:
```
rapidocr-onnxruntime==1.4.4
pypdfium2==5.14.0
onnxruntime==1.31.0
```
Generate the resolved set for this host and install into the test venv:
```bash
cd /home/opc/workspace/home-hub-reconcile/services/accounting-service
/usr/local/bin/uv pip compile requirements-worker.txt -o requirements-worker-constraints-aarch64-py313.txt --python .venv/bin/python --no-header
/usr/local/bin/uv pip install --python .venv/bin/python -r requirements.txt -r requirements-worker.txt -c requirements-worker-constraints-aarch64-py313.txt
.venv/bin/python -c "import rapidocr_onnxruntime, pypdfium2, onnxruntime; print(onnxruntime.__version__)"
```
Expected: prints `1.31.0`; the constraints file lists every transitive wheel with `==`. (If `uv pip compile` rejects the relative `../shared-python-lib` line, pass `--constraint` only for the worker file by compiling a temp copy without that line; record what you did in the commit body.)

- [ ] **Step 2: Failing config tests** (append to `tests/worker/test_config.py`)

```python
def test_ocr_defaults():
    cfg = config.load({})
    assert (cfg.ocr_enabled, cfg.ocr_threads, cfg.ocr_timeout_s, cfg.ocr_rlimit_mb, cfg.ocr_child) == (True, 2, 120, 2048, None)


def test_ocr_overrides(tmp_path):
    cfg = config.load({"STATEMENT_OCR": "false", "STATEMENT_OCR_THREADS": "4", "STATEMENT_OCR_TIMEOUT": "30",
                       "STATEMENT_OCR_RLIMIT_MB": "512", "STATEMENT_OCR_CHILD": str(tmp_path / "fake")})
    assert (cfg.ocr_enabled, cfg.ocr_threads, cfg.ocr_timeout_s, cfg.ocr_rlimit_mb) == (False, 4, 30, 512)
    assert cfg.ocr_child == tmp_path / "fake"
```

- [ ] **Step 3: Run → FAIL** (`.venv/bin/python -m pytest tests/worker/test_config.py -q`, `AttributeError`).

- [ ] **Step 4: Implement** — in `worker/config.py` after `account_map_file: Path | None`:
```python
    ocr_enabled: bool = True          # R5: OCR child for files without a text layer
    ocr_threads: int = 2
    ocr_timeout_s: int = 120
    ocr_rlimit_mb: int = 2048
    ocr_child: Path | None = None     # tests: a command replacing `python -m worker.ocr_child`
```
in `load()` after `account_map_file=…`:
```python
        ocr_enabled=_str(env, "STATEMENT_OCR", "true").lower() in TRUE,
        ocr_threads=int(_str(env, "STATEMENT_OCR_THREADS", "2")),
        ocr_timeout_s=int(_str(env, "STATEMENT_OCR_TIMEOUT", "120")),
        ocr_rlimit_mb=int(_str(env, "STATEMENT_OCR_RLIMIT_MB", "2048")),
        ocr_child=Path(child).expanduser() if (child := _opt(env, "STATEMENT_OCR_CHILD")) else None,
```
README env rows:
```
| `STATEMENT_OCR` | `true` | OCR child (RapidOCR, local, no network) for PDFs without a text layer (R5); `false` → needs_review/no_text_layer |
| `STATEMENT_OCR_THREADS` | `2` | onnxruntime intra-op threads in the OCR child |
| `STATEMENT_OCR_TIMEOUT` | `120` | wall-clock seconds per file for the OCR child, model load included |
| `STATEMENT_OCR_RLIMIT_MB` | `2048` | RLIMIT_AS of the OCR child (its measured peak is 1.2 GB) |
| `STATEMENT_OCR_CHILD` | unset | tests only: a command that replaces the python OCR child |
```

- [ ] **Step 5: Run → PASS**, then commit:
```bash
git add requirements-worker.txt requirements-worker-constraints-aarch64-py313.txt worker/config.py README.md tests/worker/test_config.py
git commit -m "feat(worker): R5 OCR dependencies, constraints and config

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Garbage trigger, bounded version, instruction rule, matching regressions (R5.1, R5.4, R5.5)

**Files:**
- Modify: `worker/pdf.py` (`extract`, the `page1 < 200` check), `worker/parse_schema.py` (`INSTRUCTION`, `version`), `worker/pipeline.py:110`, `worker/verify.py:200`
- Create: `worker/ocr.py` **stub only** with `FINGERPRINT` (Task 6 fills the rest): see Step 4
- Test: `tests/worker/test_pdf.py`, `tests/worker/test_parser.py`, `tests/unit/test_statement_matching.py`

**Interfaces:**
- Produces: `pdf.is_font_garbage(text: str) -> bool`; `parse_schema.version(cli_version, model, ocr_fingerprint) -> str`; `ocr.FINGERPRINT: str`.

- [ ] **Step 1: Failing tests**

`tests/worker/test_pdf.py`:
```python
def test_cid_garbage_is_no_text_layer():
    garbage = ["(cid:12)(cid:40) (cid:7)(cid:9)(cid:33) (cid:5) (cid:88)(cid:2) 1,234 (cid:4)"] * 30
    with pytest.raises(pdf.NoTextLayer):
        pdf.extract(make_pdf([garbage]), None)


def test_is_font_garbage_threshold():
    assert pdf.is_font_garbage("(cid:1) a b c d e f g h i") is True        # exactly 1 of 10 = 10 %
    assert pdf.is_font_garbage("(cid:1) a b c d e f g h i j") is False      # 1 of 11, below
    assert pdf.is_font_garbage("") is False
```
`tests/worker/test_parser.py` — replace the version test body (near line 192):
```python
    v = parse_schema.version("2.1.296", "claude-sonnet-5-5", "1.4.4+5.14.0+layout1")
    assert v.startswith("claude-cli-2.1.296-claude-sonnet-5-5-") and len(v.split("-")[-1]) == 12 and len(v) <= 64
    assert parse_schema.version("2.1.296", "claude-sonnet-5-5", "1.4.5+5.14.0+layout1") != v
    with pytest.raises(ValueError):
        parse_schema.version("2.1.296", "x" * 60, "f")


def test_version_is_bounded_and_changes_with_ocr():
    a = parse_schema.version("2.1.296", "claude-sonnet-5-5", "1.4.4+5.14.0+layout1")
    b = parse_schema.version("2.1.296", "claude-sonnet-5-5", "1.4.4+5.14.0+layout2")
    assert a != b and max(len(a), len(b)) <= 64


def test_instruction_states_the_card_credit_rule():
    assert "never label a negative card row balance_adjustment" in parse_schema.INSTRUCTION and "分期調整" in parse_schema.INSTRUCTION
```
`tests/unit/test_statement_matching.py` (append):
```python
def test_refund_line_claims_a_refund_entry_only():
    res = run([L(1, date(2026, 9, 12), "200", kind="refund")], [E(60, date(2026, 9, 12), "200", kind="refund", refunds_entry_id=None)])
    assert [c.representation.rule for c in res.claims] == ["refund"]


def test_refund_line_does_not_claim_an_expense_of_the_same_amount():
    res = run([L(1, date(2026, 9, 12), "200", kind="refund")], [E(61, date(2026, 9, 12), "200", kind="expense")])
    assert res.claims == []
```

- [ ] **Step 2: Run → FAIL** (`.venv/bin/python -m pytest tests/worker/test_pdf.py tests/worker/test_parser.py tests/unit/test_statement_matching.py -q -k "garbage or version or credit_rule or refund_line"`).

- [ ] **Step 3: Implement**

`worker/pdf.py` — above `extract`:
```python
GARBAGE_TOKEN = "(cid:"
GARBAGE_SHARE = 0.10


def is_font_garbage(text: str) -> bool:
    """R5.1: a Type3 font without a ToUnicode map extracts as `(cid:NNN)` tokens; at least 10 % of the whitespace-separated
    tokens being such tokens means there is no usable text layer."""
    tokens = text.split()
    return bool(tokens) and sum(1 for t in tokens if GARBAGE_TOKEN in t) / len(tokens) >= GARBAGE_SHARE
```
and after the `page1 < 200` raise:
```python
                if is_font_garbage(texts[0]):
                    raise NoTextLayer("page 1 is font garbage") from None
```
`worker/parse_schema.py` — append the sentence to `INSTRUCTION` as the last element:
```python
    " On a card statement every negative row is a credit: a payment received is payment, a cash-back or points "
    "credit is reward, and any other reversal (退款, 取消, 分期調整 / 轉分期 reversing a purchase) is refund; never "
    "label a negative card row balance_adjustment."
```
and
```python
VERSION_MAX = 64  # app.schemas RevisionIn.parser_version and statement_file.parser_version are String(64)


def version(cli_version: str, model: str, ocr_fingerprint: str) -> str:
    """One bounded version for every file (R5.4): instruction, schema and OCR (engine versions + layout) fold into a
    12-hex fingerprint, so any of them changing re-queues needs_review/failed files and never parsed ones."""
    digest = hashlib.sha256("\n".join([INSTRUCTION, json.dumps(json_schema(), sort_keys=True), ocr_fingerprint]).encode()).hexdigest()[:12]
    out = f"claude-cli-{cli_version}-{model}-{digest}"
    if len(out) > VERSION_MAX:
        raise ValueError("parser version exceeds 64 characters")
    return out
```
`worker/ocr.py` (stub for now; Task 6 replaces the file but keeps these names):
```python
"""R5.2: OCR child wrapper. (Stub: FINGERPRINT only; the Engine arrives with Task 6.)"""
from __future__ import annotations

from importlib import metadata

from worker.ocr_text import LAYOUT_VERSION


def _dist_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "0"


FINGERPRINT = f"{_dist_version('rapidocr-onnxruntime')}+{_dist_version('pypdfium2')}+layout{LAYOUT_VERSION}"
```
and `worker/ocr_text.py` with only `LAYOUT_VERSION = "1"` for now (Task 3 fills it).
`worker/pipeline.py:110` → `parse_schema.version(services.cli_version, cfg.parser_model, ocr_mod.FINGERPRINT)`; `worker/verify.py:200` → `parse_schema.version(prs.cli_version() or "unknown", cfg.parser_model, ocr_mod.FINGERPRINT)`; both import `from worker import ocr as ocr_mod`.

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/worker tests/unit/test_statement_matching.py -q` → PASS (the pipeline test checks only the `claude-cli-2.1.296-` prefix).

- [ ] **Step 5: Commit**
```bash
git add worker/pdf.py worker/parse_schema.py worker/ocr.py worker/ocr_text.py worker/pipeline.py worker/verify.py tests/worker/test_pdf.py tests/worker/test_parser.py tests/unit/test_statement_matching.py
git commit -m "feat(worker): font-garbage trigger, bounded parser version with OCR fingerprint, card credit-sign rule (R5.1/R5.4/R5.5)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Row reconstruction and identifier normalisation (`worker/ocr_text.py`, R5.2a)

**Files:**
- Modify: `worker/ocr_text.py` (replace the stub)
- Test: `tests/worker/test_ocr_text.py` (new)

**Interfaces:**
- Produces: `LAYOUT_VERSION = "1"`, `Box = tuple[float, float, float, float, str]` (x, y, w, h, text), `char_width(boxes) -> float`, `rows_of(boxes) -> list[list[Box]]`, `normalise_row(row, char_w) -> list[Box]`, `layout(boxes) -> str` (groups, normalises, joins), `ID_RE`.

- [ ] **Step 1: Failing tests** (`tests/worker/test_ocr_text.py`)

```python
from worker import ocr_text as t


def box(x, y, w, h, text):
    return (float(x), float(y), float(w), float(h), text)


def test_rows_group_by_centre_with_mixed_heights():
    rows = t.rows_of([box(10, 100, 60, 20, "A"), box(400, 102, 80, 16, "B"), box(10, 140, 60, 28, "C"), box(400, 146, 60, 16, "D")])
    assert [[b[4] for b in r] for r in rows] == [["A", "B"], ["C", "D"]]


def test_layout_keeps_column_gaps_and_clamps():
    out = t.layout([box(10, 100, 60, 20, "2026/09/01"), box(120, 100, 150, 20, "COFFEE"), box(400, 102, 80, 16, "1,234")])
    assert out.startswith("2026/09/01") and out.endswith("1,234")
    gap = out[out.index("COFFEE") + 6:out.index("1,234")]
    assert 2 <= len(gap) <= 12 and gap.strip() == ""


def test_normalise_joins_split_digit_groups_with_one_space():
    # a card number printed as four groups; OCR gives four boxes ~1 char apart (char_w = 12 for height 20)
    boxes = [box(10, 100, 48, 20, "4111"), box(70, 100, 48, 20, "2222"), box(130, 100, 48, 20, "3333"), box(190, 100, 48, 20, "4444")]
    assert t.layout(boxes) == "4111 2222 3333 4444"


def test_normalise_joins_split_id_without_space():
    assert t.layout([box(10, 100, 36, 20, "A12"), box(50, 100, 84, 20, "3456789")]) == "A123456789"


def test_normalise_joins_split_short_cjk_name():
    assert t.layout([box(10, 100, 20, 20, "王"), box(34, 100, 40, 20, "小明")]) == "王小明"


def test_normalise_never_merges_columns():
    # a date box and an amount box in different columns (gap of many chars) stay apart with padding
    out = t.layout([box(10, 100, 96, 20, "20260901"), box(400, 100, 60, 20, "1234")])
    assert out.startswith("20260901 ") and out.endswith("1234") and "202609011234" not in out and "20260901 1234" not in out


def test_normalise_leaves_long_cjk_apart():
    out = t.layout([box(10, 100, 60, 20, "全聯福"), box(74, 100, 60, 20, "利中心")])
    assert out == "全聯福 利中心"


def test_layout_empty():
    assert t.layout([]) == ""
```

- [ ] **Step 2: Run → FAIL** (`.venv/bin/python -m pytest tests/worker/test_ocr_text.py -q`).

- [ ] **Step 3: Implement `worker/ocr_text.py`**

```python
"""R5.2a: rows and identifiers from OCR boxes. Pure functions, no heavy imports: the child and the tests share them.

Why normalise: the masker (§5.4) masks digit groups joined by ONE space or dash and literal names/IDs; OCR splits a
card number or a name into separate boxes that the gap padding would otherwise keep apart. Merging only *adjacent*
boxes closer than 1.5 characters never touches column gaps (dates and amounts sit many characters apart).
LAYOUT_VERSION is part of the parser version (R5.4): bump it when any rule here changes.
"""
from __future__ import annotations

import re

LAYOUT_VERSION = "1"
Box = tuple[float, float, float, float, str]  # x, y, w, h, text (pixels)
ID_RE = re.compile(r"^[A-Z][12]\d{8}$")
DIGITS_RE = re.compile(r"^\d+$")
CJK_RE = re.compile(r"^[一-鿿]+$")
MERGE_GAP_CHARS = 1.5
MAX_PAD = 12


def char_width(boxes: list[Box]) -> float:
    heights = sorted(b[3] for b in boxes)
    med_h = heights[len(heights) // 2] if heights else 1.0
    return max(med_h * 0.6, 1.0)


def rows_of(boxes: list[Box]) -> list[list[Box]]:
    """Group by vertical centre, tolerance half the median height; rows top to bottom, boxes left to right."""
    if not boxes:
        return []
    heights = sorted(b[3] for b in boxes)
    tol = (heights[len(heights) // 2] or 1.0) * 0.5
    rows: list[list[Box]] = []
    centre = None
    for b in sorted(boxes, key=lambda b: (b[1] + b[3] / 2, b[0])):
        c = b[1] + b[3] / 2
        if rows and abs(c - centre) <= tol:
            rows[-1].append(b)
        else:
            rows.append([b])
            centre = c
    for row in rows:
        row.sort(key=lambda b: b[0])
    return rows


def _merge(a: Box, b: Box, sep: str) -> Box:
    return (a[0], min(a[1], b[1]), (b[0] + b[2]) - a[0], max(a[3], b[3]), a[4] + sep + b[4])


def normalise_row(row: list[Box], char_w: float) -> list[Box]:
    """Rejoin identifiers split across adjacent boxes (gap < 1.5 chars): digit groups with one space, an ID without
    a space, two short CJK boxes (name) without a space. Everything else is left for layout padding."""
    out: list[Box] = []
    for b in row:
        if out:
            a = out[-1]
            gap = b[0] - (a[0] + a[2])
            if gap < char_w * MERGE_GAP_CHARS:
                ta, tb = a[4], b[4]
                if DIGITS_RE.match(ta.split()[-1]) and DIGITS_RE.match(tb):
                    out[-1] = _merge(a, b, " ")
                    continue
                if ID_RE.match(ta + tb):
                    out[-1] = _merge(a, b, "")
                    continue
                if CJK_RE.match(ta) and CJK_RE.match(tb) and len(ta) + len(tb) <= 4:
                    out[-1] = _merge(a, b, "")
                    continue
        out.append(b)
    return out


def layout(boxes: list[Box]) -> str:
    if not boxes:
        return ""
    char_w = char_width(boxes)
    lines = []
    for row in rows_of(boxes):
        parts: list[str] = []
        prev_end = None
        for x, _y, w, _h, text in normalise_row(row, char_w):
            if prev_end is not None:
                parts.append(" " * max(1, min(MAX_PAD, int((x - prev_end) / char_w))))
            parts.append(text)
            prev_end = x + w
        lines.append("".join(parts))
    return "\n".join(lines)
```

- [ ] **Step 4: Run → PASS**; commit:
```bash
git add worker/ocr_text.py tests/worker/test_ocr_text.py
git commit -m "feat(worker): OCR row reconstruction with split-identifier normalisation (R5.2a)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Line-level masking guard (`Masker.guard_lines`, R5.2b)

**Files:**
- Modify: `worker/mask.py` (new method on `Masker`)
- Test: `tests/worker/test_mask.py`

**Interfaces:**
- Produces: `Masker.guard_lines(self, text: str, extra_literals=()) -> str`; `Masker.mask_ocr(self, text: str, extra_literals=()) -> str` = `guard_lines(self.text(text, extra_literals), extra_literals)`.

- [ ] **Step 1: Failing tests** (append to `tests/worker/test_mask.py`; the module builds `mask.Masker(IDENT)` with `IDENT = passwords.Identity(id_number="A123456789", birth_date="19900101", holder_names=["王小明", "WANG XIAO MING"])`)

```python
def test_guard_lines_masks_split_name_and_id():
    m = mask.Masker(IDENT)
    text = "MERCHANT 001        1,234\n持卡人 王 小 明\nA12 3456789 some row\nWANG XIAO  MING here"
    assert m.guard_lines(text).split("\n") == ["MERCHANT 001        1,234", "[LINE MASKED]", "[LINE MASKED]", "[LINE MASKED]"]


def test_guard_lines_masks_split_birth_and_password_literal():
    m = mask.Masker(IDENT)
    assert m.guard_lines("生日 1990 /01/ 01") == "[LINE MASKED]"
    assert m.guard_lines("pw 01 23 45 here", extra_literals=["012345"]) == "[LINE MASKED]"
    assert m.guard_lines("pw 01 23 here", extra_literals=["0123"]) == "[LINE MASKED]"
    assert m.guard_lines("short 01 2", extra_literals=["012"]) == "short 01 2"  # candidates under 4 chars never guard


def test_guard_lines_leaves_clean_lines():
    m = mask.Masker(IDENT)
    text = "2026/09/01 COFFEE 120\n王 alone is not a name\n[NUM…3456] already masked"
    assert m.guard_lines(text) == text


def test_mask_ocr_applies_masker_then_guard():
    m = mask.Masker(IDENT)
    out = m.mask_ocr("卡號 4111 2222 3333 4444\n戶名 王 小 明\nMERCHANT 001        1,234")
    lines = out.split("\n")
    assert "4111 2222 3333" not in lines[0] and "[NUM…4444]" in lines[0]
    assert lines[1] == "[LINE MASKED]" and lines[2] == "MERCHANT 001        1,234"
```

- [ ] **Step 2: Run → FAIL**.

- [ ] **Step 3: Implement** — in `worker/mask.py`, inside `Masker`:
```python
    def guard_lines(self, s: str, extra_literals=()) -> str:
        """R5.2b: a line whose whitespace-stripped form still holds an identity literal (ID number, holder name,
        birth pattern, password candidate >= 4 chars) is replaced whole. OCR text only: boxes split identifiers."""
        literals = [x for x in (self.identity.id_number, *self.names, *self.literals, *extra_literals) if x and len(x) >= 2]
        literals = [x for x in literals if not (x in extra_literals and len(x) < 4)]
        out = []
        for line in s.split("\n"):
            compact = "".join(line.split())
            out.append("[LINE MASKED]" if any(lit in compact for lit in literals) else line)
        return "\n".join(out)

    def mask_ocr(self, s: str, extra_literals=()) -> str:
        return self.guard_lines(self.text(s, extra_literals), extra_literals)
```
(`self.names` already excludes names shorter than 2; `self.literals` are the birth patterns.)

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/worker/test_mask.py -q` → PASS; commit:
```bash
git add worker/mask.py tests/worker/test_mask.py
git commit -m "feat(worker): line-level masking guard for OCR text (R5.2b)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: The OCR child (`worker/ocr_child.py`, R5.2)

**Files:**
- Create: `worker/ocr_child.py`
- Test: `tests/worker/test_ocr_child.py` (new)

**Interfaces:**
- Consumes: `ocr_text.layout`, `Box`.
- Produces: `Unavailable` (exception), `load_engine(threads) -> Callable[[np.ndarray], list]`, `run(header: dict, data: bytes, backend=None) -> dict` (pure given a backend), `apply_rlimits(header)`, `main() -> int` (stdin/stdout protocol). Header keys: `password`, `scale`, `max_pages`, `max_pixels`, `threads`, `rlimit_as_mb`, `rlimit_cpu_s`. Result: `{"ok": True, "pages", "empty_pages", "boxes", "mean_confidence", "text"}` or `{"ok": False, "code": "unavailable"|"too_large"|"malformed"|"low_confidence"}`.

- [ ] **Step 1: Failing tests** (`tests/worker/test_ocr_child.py`)

```python
import io
import json
import subprocess
import sys

import numpy as np
import pytest

from tests.worker.pdfgen import encrypt, make_pdf, statement_lines
from worker import ocr_child

HDR = {"password": None, "scale": 3.0, "max_pages": 10, "max_pixels": 30_000_000, "threads": 2, "rlimit_as_mb": 2048, "rlimit_cpu_s": 150}
RAW = [[[[10, 10], [200, 10], [200, 30], [10, 30]], "MERCHANT 001", 0.97], [[[400, 12], [460, 12], [460, 28], [400, 28]], "1,234", 0.93]]


def backend_of(per_call):
    calls = []

    def backend(img):
        assert isinstance(img, np.ndarray) and img.ndim == 3 and img.shape[2] == 3 and img.dtype == np.uint8
        calls.append(img.shape)
        return per_call(len(calls)) if callable(per_call) else per_call
    backend.calls = calls
    return backend


def test_run_reads_every_page_and_reports_counts():
    b = backend_of(RAW)
    out = ocr_child.run(HDR, make_pdf([statement_lines(), ["PAGE TWO"]]), backend=b)
    assert out["ok"] and out["pages"] == 2 and len(b.calls) == 2 and out["boxes"] == 4 and out["empty_pages"] == 0
    assert out["text"].count("\f") == 1 and "MERCHANT 001" in out["text"] and abs(out["mean_confidence"] - 0.95) < 1e-6


def test_run_allows_an_empty_page_but_not_an_empty_file():
    out = ocr_child.run(HDR, make_pdf([statement_lines(), ["x"]]), backend=backend_of(lambda n: RAW if n == 1 else []))
    assert out["ok"] and out["empty_pages"] == 1 and out["pages"] == 2
    assert ocr_child.run(HDR, make_pdf([statement_lines()]), backend=backend_of([])) == {"ok": False, "code": "low_confidence"}


def test_run_confidence_floor():
    low = [[[[10, 10], [200, 10], [200, 30], [10, 30]], "x", 0.5]]
    assert ocr_child.run(HDR, make_pdf([statement_lines()]), backend=backend_of(low))["code"] == "low_confidence"


def test_run_uses_the_password():
    data = encrypt(make_pdf([statement_lines()]), "A123456789")
    assert ocr_child.run({**HDR, "password": "A123456789"}, data, backend=backend_of(RAW))["ok"]
    assert ocr_child.run(HDR, data, backend=backend_of(RAW)) == {"ok": False, "code": "malformed"}


def test_page_bound_before_any_render():
    b = backend_of(RAW)
    assert ocr_child.run(HDR, make_pdf([["p"]] * 11), backend=b) == {"ok": False, "code": "too_large"} and b.calls == []


def test_pixel_bound_before_render():
    b = backend_of(RAW)
    out = ocr_child.run({**HDR, "max_pixels": 1000}, make_pdf([statement_lines()]), backend=b)  # A4 at scale 3 is ~13 Mpx
    assert out == {"ok": False, "code": "too_large"} and b.calls == []


def test_malformed_bytes():
    assert ocr_child.run(HDR, b"%PDF-1.4 not really", backend=backend_of(RAW)) == {"ok": False, "code": "malformed"}


def test_backend_failure_on_a_page_is_malformed():
    def boom(img):
        raise RuntimeError("engine failed mid-page")
    assert ocr_child.run(HDR, make_pdf([statement_lines()]), backend=boom) == {"ok": False, "code": "malformed"}


def test_run_reports_unavailable_before_reading_the_pdf(monkeypatch):
    monkeypatch.setattr(ocr_child, "load_engine", lambda threads: (_ for _ in ()).throw(ocr_child.Unavailable()))
    assert ocr_child.run(HDR, b"garbage", backend=None) == {"ok": False, "code": "unavailable"}


def test_main_protocol_roundtrip(tmp_path):
    """The real child (no bwrap) with a fake backend injected through an env hook used only by this test module."""
    script = (
        "import sys, json\n"
        "from worker import ocr_child\n"
        "sys.exit(ocr_child.main(backend=lambda img: %r))\n" % (RAW,)
    )
    data = make_pdf([statement_lines()])
    proc = subprocess.run([sys.executable, "-c", script], input=json.dumps(HDR).encode() + b"\n" + data, capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[:200]
    out = json.loads(proc.stdout.decode().splitlines()[-1])
    assert out["ok"] and out["pages"] == 1


def test_apply_rlimits_sets_core_zero_and_address_space():
    import resource
    before = resource.getrlimit(resource.RLIMIT_CORE)
    try:
        ocr_child.apply_rlimits({"rlimit_as_mb": 4096, "rlimit_cpu_s": 600})
        assert resource.getrlimit(resource.RLIMIT_CORE)[0] == 0
    finally:
        resource.setrlimit(resource.RLIMIT_CORE, before)
```

- [ ] **Step 2: Run → FAIL** (`ModuleNotFoundError: worker.ocr_child`).

- [ ] **Step 3: Verify the RapidOCR constructor on this host** (do not guess the thread kwargs):
```bash
.venv/bin/python - <<'PY'
import inspect, pathlib, rapidocr_onnxruntime
from rapidocr_onnxruntime import RapidOCR
print(inspect.signature(RapidOCR.__init__))
cfg = pathlib.Path(rapidocr_onnxruntime.__file__).with_name("config.yaml")
print([l.strip() for l in cfg.read_text().splitlines() if "thread" in l])
PY
```
Use `RapidOCR(intra_op_num_threads=threads, inter_op_num_threads=1)` if the signature takes `**kwargs` and the yaml has `intra_op_num_threads`; otherwise use the key names the yaml shows, and record the finding in the module docstring.

- [ ] **Step 4: Implement `worker/ocr_child.py`**

```python
"""R5.2: the OCR child. `python -B -m worker.ocr_child` reads one JSON header line and then raw PDF bytes on stdin,
prints one JSON line on stdout and exits 0. It runs under bwrap with no network (worker/ocr.py builds the argv);
it never writes to disk and never prints anything but the result line (native libraries may write to stderr, which
the parent discards).

Order of work: rlimits → readiness (imports, engine, 32x32 self-test; failure = "unavailable", reported BEFORE the
PDF is touched) → page bound → per page: pixel bound before allocation, render, recognise, rows (ocr_text.layout) →
confidence floor. Thread setting verified on this host (Task 5 Step 3): <finding>.
"""
from __future__ import annotations

import json
import sys
from io import BytesIO

from worker import ocr_text

SELF_TEST_SIDE = 32
CONFIDENCE_FLOOR = 0.80
STDIN_HEADER_MAX = 4096


class Unavailable(RuntimeError):
    """Imports, engine construction or the self-test failed."""


def apply_rlimits(header: dict) -> None:
    import resource  # noqa: PLC0415

    as_bytes = int(header.get("rlimit_as_mb", 2048)) * 1024 * 1024
    cpu = int(header.get("rlimit_cpu_s", 150))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    for res, value in ((resource.RLIMIT_AS, as_bytes), (resource.RLIMIT_CPU, cpu)):
        soft, hard = resource.getrlimit(res)
        cap = value if hard == resource.RLIM_INFINITY else min(value, hard)
        resource.setrlimit(res, (cap, hard))


def load_engine(threads: int):
    """RapidOCR with the wheel's bundled models; a callable img -> [[quad, text, score], ...]."""
    try:
        import numpy as np  # noqa: PLC0415
        from rapidocr_onnxruntime import RapidOCR  # noqa: PLC0415

        engine = RapidOCR(intra_op_num_threads=threads, inter_op_num_threads=1)
        engine(np.full((SELF_TEST_SIDE, SELF_TEST_SIDE, 3), 255, dtype=np.uint8))
    except Exception:  # noqa: BLE001 — the library text never leaves the child
        raise Unavailable() from None

    def backend(img):
        result, _elapse = engine(img)
        return result or []
    return backend


def _ready(threads: int, backend):
    if backend is not None:
        return backend
    return load_engine(threads)


def run(header: dict, data: bytes, backend=None) -> dict:
    try:
        backend = _ready(int(header.get("threads", 2)), backend)
        import pypdfium2 as pdfium  # noqa: PLC0415 — the renderer is part of readiness
        import numpy as np  # noqa: PLC0415
    except Unavailable:
        return {"ok": False, "code": "unavailable"}
    except Exception:  # noqa: BLE001
        return {"ok": False, "code": "unavailable"}
    scale = float(header.get("scale", 3.0))
    max_pages = int(header.get("max_pages", 10))
    max_pixels = int(header.get("max_pixels", 30_000_000))
    try:
        doc = pdfium.PdfDocument(BytesIO(data), password=header.get("password"))
        npages = len(doc)
    except Exception:  # noqa: BLE001
        return {"ok": False, "code": "malformed"}
    if npages > max_pages:
        return {"ok": False, "code": "too_large"}
    texts: list[str] = []
    scores: list[float] = []
    boxes_total = 0
    empty_pages = 0
    try:
        for page in doc:
            w, h = page.get_size()
            if w * scale * h * scale > max_pixels:
                return {"ok": False, "code": "too_large"}
            img = page.render(scale=scale, rev_byteorder=True).to_numpy()
            boxes: list[ocr_text.Box] = []
            for quad, text, score in backend(img):
                xs = [p[0] for p in quad]
                ys = [p[1] for p in quad]
                boxes.append((float(min(xs)), float(min(ys)), float(max(xs) - min(xs)), float(max(ys) - min(ys)), str(text)))
                scores.append(float(score))
            if not boxes:
                empty_pages += 1
            boxes_total += len(boxes)
            texts.append(ocr_text.layout(boxes))
    except Exception:  # noqa: BLE001 — render or engine failure on a page
        return {"ok": False, "code": "malformed"}
    mean = sum(scores) / len(scores) if scores else 0.0
    if boxes_total == 0 or mean < CONFIDENCE_FLOOR:
        return {"ok": False, "code": "low_confidence"}
    return {"ok": True, "pages": npages, "empty_pages": empty_pages, "boxes": boxes_total, "mean_confidence": mean,
            "text": "\f".join(texts)}


def main(backend=None) -> int:
    stdin = sys.stdin.buffer
    header_line = stdin.readline(STDIN_HEADER_MAX)
    try:
        header = json.loads(header_line)
    except Exception:  # noqa: BLE001
        sys.stdout.write(json.dumps({"ok": False, "code": "malformed"}) + "\n")
        return 0
    apply_rlimits(header)
    data = stdin.read()
    sys.stdout.write(json.dumps(run(header, data, backend=backend), ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```
Notes: `page.get_size()` returns points (width, height) at 72 dpi; pixels = width × scale × height × scale. The malformed test with the wrong password passes because pypdfium2 raises on open.

- [ ] **Step 5: Run** `.venv/bin/python -m pytest tests/worker/test_ocr_child.py -q` → PASS. (`test_main_protocol_roundtrip` runs the real module as a subprocess with `apply_rlimits`; the AS cap of 2048 MB is fine for a fake backend.)

- [ ] **Step 6: Commit**
```bash
git add worker/ocr_child.py tests/worker/test_ocr_child.py
git commit -m "feat(worker): OCR child process — readiness, bounds, render, recognise, rows (R5.2)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: The parent `worker/ocr.py` — sandboxed child, deadline, outcomes (R5.2, R5.8)

**Files:**
- Modify: `worker/ocr.py` (replace the stub, keep `FINGERPRINT`/`_dist_version`)
- Create: `tests/worker/fake_ocr_child.py`
- Test: `tests/worker/test_ocr.py` (new)

**Interfaces:**
- Consumes: `cfg.parser_sandbox`, `cfg.ocr_child`, `cfg.ocr_threads`, `cfg.ocr_timeout_s`, `cfg.ocr_rlimit_mb`.
- Produces: `OCRUnavailable`, `OCRTimeout`, `OCRLowConfidence` (RuntimeError subclasses); `OcrResult(extracted: pdf.Extracted, mean_confidence: float, boxes: int, empty_pages: int)`; `Engine(cfg)` with `argv() -> list[str]`, `read(data, password) -> OcrResult`; constants `SCALE = 3.0`, `MAX_PAGES = 10`, `MAX_PIXELS = 30_000_000`, `RLIMIT_CPU_S = 150`, `STDOUT_CAP = 2 * 1024 * 1024`.

- [ ] **Step 1: The fake child** (`tests/worker/fake_ocr_child.py`)

```python
"""A stand-in for `python -m worker.ocr_child`: consumes stdin, optionally sleeps, prints a canned line, exits."""
import json
import shlex
from pathlib import Path


def write(path: Path, response: dict | str | None, *, sleep: float = 0, exit_code: int = 0, stdout_bytes: int | None = None) -> Path:
    lines = ["#!/bin/sh", "cat >/dev/null"]
    if sleep:
        lines.append(f"sleep {sleep}")
    if stdout_bytes is not None:
        lines.append(f"head -c {stdout_bytes} /dev/zero | tr '\\0' 'x'; echo")
    elif response is not None:
        body = response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
        lines.append(f"printf '%s\\n' {shlex.quote(body)}")
    lines.append(f"exit {exit_code}")
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o700)
    return path


def ok(text="MERCHANT 001        1,234", pages=1, boxes=2, mean_confidence=0.93, empty_pages=0) -> dict:
    return {"ok": True, "pages": pages, "empty_pages": empty_pages, "boxes": boxes, "mean_confidence": mean_confidence, "text": text}
```

- [ ] **Step 2: Failing tests** (`tests/worker/test_ocr.py`)

```python
import os
import signal
import subprocess
import time

import pytest

from tests.worker import fake_ocr_child
from worker import config, ocr, pdf


def cfg_with(tmp_path, response=None, **kw):
    child = fake_ocr_child.write(tmp_path / "child.sh", response, **kw)
    return config.load({"STATEMENT_OCR_CHILD": str(child), "STATEMENT_PARSER_SANDBOX": "false", "STATEMENT_OCR_TIMEOUT": "2"})


def test_read_ok_maps_to_result(tmp_path):
    out = ocr.Engine(cfg_with(tmp_path, fake_ocr_child.ok(text="A\fB", pages=2, boxes=5, empty_pages=1))).read(b"%PDF", "pw")
    assert out.extracted.text == "A\fB" and out.extracted.pages == 2 and out.extracted.tables == [] and out.extracted.text_chars_page1 == 1
    assert out.boxes == 5 and out.empty_pages == 1 and abs(out.mean_confidence - 0.93) < 1e-9


@pytest.mark.parametrize("code,exc", [("unavailable", ocr.OCRUnavailable), ("too_large", pdf.TooLarge), ("malformed", pdf.Malformed), ("low_confidence", ocr.OCRLowConfidence)])
def test_read_maps_error_codes(tmp_path, code, exc):
    with pytest.raises(exc):
        ocr.Engine(cfg_with(tmp_path, {"ok": False, "code": code})).read(b"%PDF", None)


def test_header_and_bytes_reach_the_child(tmp_path):
    recorder = tmp_path / "child.sh"
    recorder.write_text("#!/bin/sh\nhead -1 > %s\ncat > %s\nprintf '%%s\\n' '%s'\n" % (tmp_path / "hdr", tmp_path / "pdf", __import__("json").dumps(fake_ocr_child.ok())))
    recorder.chmod(0o700)
    cfg = config.load({"STATEMENT_OCR_CHILD": str(recorder), "STATEMENT_PARSER_SANDBOX": "false", "STATEMENT_OCR_THREADS": "3", "STATEMENT_OCR_RLIMIT_MB": "777"})
    ocr.Engine(cfg).read(b"%PDF-bytes", "secret")
    hdr = __import__("json").loads((tmp_path / "hdr").read_text())
    assert hdr == {"password": "secret", "scale": 3.0, "max_pages": 10, "max_pixels": 30_000_000, "threads": 3, "rlimit_as_mb": 777, "rlimit_cpu_s": 150}
    assert (tmp_path / "pdf").read_bytes() == b"%PDF-bytes"


def test_read_timeout_kills_and_reaps(tmp_path):
    engine = ocr.Engine(cfg_with(tmp_path, fake_ocr_child.ok(), sleep=30))
    t0 = time.monotonic()
    with pytest.raises(ocr.OCRTimeout):
        engine.read(b"%PDF", None)
    assert time.monotonic() - t0 < 10
    # nothing left behind: no child of ours is still sleeping
    assert not [l for l in subprocess.run(["ps", "-o", "pid=,args=", "--ppid", str(os.getpid())], capture_output=True, text=True).stdout.splitlines() if "sleep 30" in l]


def test_crash_and_garbage_are_malformed(tmp_path):
    with pytest.raises(pdf.Malformed):
        ocr.Engine(cfg_with(tmp_path, None, exit_code=139)).read(b"%PDF", None)
    with pytest.raises(pdf.Malformed):
        ocr.Engine(cfg_with(tmp_path, "not json at all")).read(b"%PDF", None)
    with pytest.raises(pdf.Malformed):
        ocr.Engine(cfg_with(tmp_path, None, stdout_bytes=ocr.STDOUT_CAP + 10)).read(b"%PDF", None)


def test_unavailable_is_cached_for_the_run(tmp_path):
    engine = ocr.Engine(cfg_with(tmp_path, {"ok": False, "code": "unavailable"}))
    with pytest.raises(ocr.OCRUnavailable):
        engine.read(b"%PDF", None)
    fake_ocr_child.write(tmp_path / "child.sh", fake_ocr_child.ok())  # the child would now succeed
    with pytest.raises(ocr.OCRUnavailable):  # but the run already knows the host is broken
        engine.read(b"%PDF", None)
    assert engine.starts == 1


def test_argv_sandboxed_has_no_network_and_binds_the_runtime(tmp_path):
    cfg = config.load({"STATEMENT_PARSER_SANDBOX": "true"})
    argv = ocr.Engine(cfg).argv()
    assert argv[0] == "bwrap" and "--unshare-all" in argv and "--share-net" not in argv
    assert "--cap-drop" in argv and "--die-with-parent" in argv and "--new-session" in argv
    binds = [argv[i + 1] for i, a in enumerate(argv) if a == "--ro-bind"]
    import sys
    assert os.path.realpath(sys.prefix) in binds and os.path.realpath(sys.base_prefix) in binds and "/usr" in binds
    assert argv[-3:] == ["-B", "-m", "worker.ocr_child"] and "--tmpfs" in argv and "--proc" in argv
    assert argv[argv.index("--setenv") + 1:argv.index("--setenv") + 3] == ["HOME", "/tmp"]


def test_argv_unsandboxed_and_override(tmp_path):
    import sys
    plain = ocr.Engine(config.load({"STATEMENT_PARSER_SANDBOX": "false"})).argv()
    assert plain == [sys.executable, "-B", "-m", "worker.ocr_child"]
    assert ocr.Engine(cfg_with(tmp_path, fake_ocr_child.ok())).argv()[0].endswith("child.sh")


def test_errors_carry_no_content(tmp_path):
    try:
        ocr.Engine(cfg_with(tmp_path, {"ok": False, "code": "low_confidence"})).read(b"%PDF SECRET-BYTES", None)
    except ocr.OCRLowConfidence as exc:
        assert "SECRET" not in str(exc) and exc.__cause__ is None and exc.__context__ is None


def test_fingerprint_shape():
    assert ocr.FINGERPRINT.count("+") == 2 and ocr.FINGERPRINT.endswith("layout1")
```

- [ ] **Step 3: Run → FAIL**.

- [ ] **Step 4: Implement `worker/ocr.py`**

```python
"""R5.2/R5.8: parent side of OCR. Starts `python -B -m worker.ocr_child` (under bwrap without network when the parser
sandbox is on), feeds the header line and the PDF bytes, enforces the wall-clock deadline with a process-group kill
and reap, caps stdout, discards stderr, and maps the child's result to the pipeline's outcomes. The parent never
holds page images; the child's text comes back as the `pdf.Extracted` shape so the masker and the parser need not
know it came from OCR.

Outcome mapping (R5.3): header/result "unavailable" → OCRUnavailable (cached for the rest of the run); deadline →
OCRTimeout; "too_large" → pdf.TooLarge; "low_confidence" → OCRLowConfidence; "malformed", a non-zero exit, a kill,
unparsable or over-cap stdout → pdf.Malformed (per file).
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from worker import pdf
from worker.config import WorkerConfig
from worker.ocr_text import LAYOUT_VERSION

SCALE = 3.0
MAX_PAGES = 10
MAX_PIXELS = 30_000_000
RLIMIT_CPU_S = 150
STDOUT_CAP = 2 * 1024 * 1024
KILL_GRACE_S = 5


def _dist_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "0"


FINGERPRINT = f"{_dist_version('rapidocr-onnxruntime')}+{_dist_version('pypdfium2')}+layout{LAYOUT_VERSION}"


class OCRUnavailable(RuntimeError):
    """The child could not import, build or self-test the engine (before reading the PDF)."""


class OCRTimeout(RuntimeError):
    """The per-file deadline fired; the child was killed."""


class OCRLowConfidence(RuntimeError):
    """Zero boxes or mean confidence under the floor."""


@dataclass
class OcrResult:
    extracted: pdf.Extracted
    mean_confidence: float
    boxes: int
    empty_pages: int


def service_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _sandbox_prefix() -> list[str]:
    binds: list[str] = []
    for path in ("/usr", "/lib", "/lib64", os.path.realpath(sys.base_prefix), os.path.realpath(sys.prefix), str(service_dir())):
        if os.path.exists(path) and path not in binds:
            binds += ["--ro-bind", path, path]
    return ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL", *binds,
            "--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev", "--chdir", str(service_dir()),
            "--setenv", "HOME", "/tmp", "--setenv", "PYTHONPATH", str(service_dir()),
            "--setenv", "PYTHONDONTWRITEBYTECODE", "1", "--"]


class Engine:
    def __init__(self, cfg: WorkerConfig):
        self.cfg = cfg
        self.starts = 0
        self._unavailable = False

    def argv(self) -> list[str]:
        if self.cfg.ocr_child is not None:
            return [str(self.cfg.ocr_child)]
        child = [sys.executable, "-B", "-m", "worker.ocr_child"]
        return (_sandbox_prefix() + child) if self.cfg.parser_sandbox else child

    def _header(self, password: str | None) -> bytes:
        return (json.dumps({"password": password, "scale": SCALE, "max_pages": MAX_PAGES, "max_pixels": MAX_PIXELS,
                            "threads": self.cfg.ocr_threads, "rlimit_as_mb": self.cfg.ocr_rlimit_mb,
                            "rlimit_cpu_s": RLIMIT_CPU_S}) + "\n").encode()

    def read(self, data: bytes, password: str | None) -> OcrResult:
        if self._unavailable:
            raise OCRUnavailable("ocr unavailable") from None
        self.starts += 1
        try:
            proc = subprocess.Popen(self.argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    start_new_session=True)
        except OSError:
            self._unavailable = True
            raise OCRUnavailable("ocr unavailable") from None
        try:
            out, _ = proc.communicate(self._header(password) + data, timeout=self.cfg.ocr_timeout_s)
        except subprocess.TimeoutExpired:
            self._kill(proc)
            raise OCRTimeout("ocr timed out") from None
        if proc.returncode != 0 or len(out) > STDOUT_CAP:
            raise pdf.Malformed("malformed") from None
        try:
            lines = out.decode("utf-8").splitlines()
            result = json.loads(lines[-1]) if lines else {}
        except Exception:  # noqa: BLE001
            raise pdf.Malformed("malformed") from None
        if not isinstance(result, dict):
            raise pdf.Malformed("malformed") from None
        if result.get("ok") is True:
            text = str(result.get("text", ""))
            pages = int(result.get("pages", 0))
            page1 = len(text.split("\f")[0].strip()) if text else 0
            return OcrResult(pdf.Extracted(text, [], pages, page1), float(result.get("mean_confidence", 0.0)),
                             int(result.get("boxes", 0)), int(result.get("empty_pages", 0)))
        code = result.get("code")
        if code == "unavailable":
            self._unavailable = True
            raise OCRUnavailable("ocr unavailable") from None
        if code == "too_large":
            raise pdf.TooLarge("too large") from None
        if code == "low_confidence":
            raise OCRLowConfidence("low confidence") from None
        raise pdf.Malformed("malformed") from None

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        """Kill the child's whole session (bwrap and its grandchildren) and reap it."""
        for sig in (signal.SIGKILL,):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                pass
        try:
            proc.communicate(timeout=KILL_GRACE_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
```
`communicate(timeout=...)` enforces the deadline from the parent regardless of what the child is doing (native call, model load, self-test). `exc.__context__ is None` holds because every raise happens outside an active exception, except the JSON branch which uses `from None` inside `except` — set `exc.__context__` explicitly there if the test fails: `err = pdf.Malformed("malformed"); err.__context__ = None; raise err`.

- [ ] **Step 5: Run** `.venv/bin/python -m pytest tests/worker/test_ocr.py tests/worker/test_parser.py -q` → PASS (the `ps` check in the timeout test may need `--ppid` replaced by scanning `ps -eo pid,args` for the fake script path when the shell re-parents; keep the assertion that no `sleep 30` from this test survives).

- [ ] **Step 6: Commit**
```bash
git add worker/ocr.py tests/worker/fake_ocr_child.py tests/worker/test_ocr.py
git commit -m "feat(worker): OCR parent — sandboxed child, deadline with kill and reap, bounded outcomes (R5.2/R5.8)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: The pipeline OCR branch and the degraded exit code (R5.3)

**Files:**
- Modify: `worker/pipeline.py` (`Services`, `build`, `process_file` ~lines 243–292, `revision_body`), `worker/cli.py:103`
- Test: `tests/worker/test_pipeline.py`

**Interfaces:**
- Consumes: Task 6 (`ocr.Engine`, errors, `OcrResult`, `SCALE`), Task 4 (`Masker.mask_ocr`).
- Produces: `Services.ocr: ocr_mod.Engine | None`; `revision_body(parsed, *, file_id, account_id, parser_version, parser="claude-cli", extra_note=None)`.

- [ ] **Step 1: Failing tests** (append to `tests/worker/test_pipeline.py`)

```python
from tests.worker import fake_ocr_child


def _no_text_world(world, tmp_path, response=None, **kw):
    """Drive holds a PDF whose page 1 is too short for a text layer; the OCR child is the fake script."""
    services, drive_files, cfg = world
    drive_files["id-1"] = ("信用卡/國泰世華/2026-09_國泰世華.pdf", encrypt(make_pdf([["short"]]), "A123456789"))
    child = fake_ocr_child.write(tmp_path / "ocr-child.sh", response, **kw)
    import dataclasses
    cfg = dataclasses.replace(cfg, ocr_child=child, ocr_timeout_s=2)
    services.cfg = cfg
    from worker import ocr
    services.ocr = ocr.Engine(cfg)
    return services, drive_files, cfg


OCR_TEXT = "\n".join(statement_lines()) + "\n持卡人 王 小 明\n卡號 4111 2222 3333 4444"


def _record_parser(services):
    seen = []
    real = services.parser.parse

    def parse(masked):
        seen.append(masked)
        return real(masked)
    services.parser.parse = parse
    return seen


def test_ocr_path_parses_and_records_provenance(world, tmp_path, db_session):
    services, _, _ = _no_text_world(world, tmp_path, fake_ocr_child.ok(text=OCR_TEXT, boxes=42))
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["parsed"] == 1 and services.ocr.starts == 1
    assert db_session.execute(text("select status, has_text_layer, text_chars from statement_file")).one() == ("parsed", False, len(OCR_TEXT))
    rev = db_session.execute(text("select parser, raw from statement_revision")).one()
    assert rev[0] == "claude-cli+ocr" and "ocr: mean_confidence=0.93 boxes=42 empty_pages=0 scale=3.0" in rev[1]["notes"]


def test_ocr_parser_stdin_carries_no_split_identifier(world, tmp_path):
    services, _, _ = _no_text_world(world, tmp_path, fake_ocr_child.ok(text=OCR_TEXT))
    seen = _record_parser(services)
    pipeline.run(services, trigger="owner_cli")
    assert len(seen) == 1
    compact = seen[0].replace(" ", "")
    assert "王小明" not in compact and "4111222233334444" not in compact and "A123456789" not in compact
    assert "MERCHANT 001" in seen[0]  # the statement body still reaches the parser


def test_text_layer_file_makes_zero_ocr_calls(world, tmp_path):
    services, _, cfg = world
    from worker import ocr
    services.ocr = ocr.Engine(cfg)
    assert pipeline.run(services, trigger="owner_cli")["parsed"] == 1 and services.ocr.starts == 0


def test_password_reaches_the_ocr_child(world, tmp_path):
    rec = tmp_path / "ocr-child.sh"
    rec.write_text("#!/bin/sh\nhead -1 > %s\ncat >/dev/null\nprintf '%%s\\n' '%s'\n" % (tmp_path / "hdr", json.dumps(fake_ocr_child.ok())))
    rec.chmod(0o700)
    services, _, _ = _no_text_world(world, tmp_path, None)
    services.cfg = services.cfg.__class__(**{**services.cfg.__dict__, "ocr_child": rec})
    from worker import ocr
    services.ocr = ocr.Engine(services.cfg)
    pipeline.run(services, trigger="owner_cli")
    assert json.loads((tmp_path / "hdr").read_text())["password"] == "A123456789"


def test_ocr_disabled_keeps_todays_behaviour(world, tmp_path, db_session):
    services, _, _ = _no_text_world(world, tmp_path, fake_ocr_child.ok())
    services.ocr = None
    assert pipeline.run(services, trigger="owner_cli")["needs_review"] == 1
    assert _file_rows(db_session) == [("needs_review", "no_text_layer")]


@pytest.mark.parametrize("code,expected", [("low_confidence", ("needs_review", "no_text_layer")), ("too_large", ("needs_review", "too_large")), ("malformed", ("failed", "parse"))])
def test_ocr_child_codes_map_to_file_outcomes(world, tmp_path, db_session, code, expected):
    services, _, _ = _no_text_world(world, tmp_path, {"ok": False, "code": code})
    pipeline.run(services, trigger="owner_cli")
    assert _file_rows(db_session) == [expected]
    if expected[0] == "needs_review":
        assert db_session.execute(text("select has_text_layer from statement_file")).scalar_one() is False


def test_ocr_timeout_needs_review_and_next_file_runs(world, tmp_path, db_session):
    services, drive_files, _ = _no_text_world(world, tmp_path, fake_ocr_child.ok(), sleep=30)
    drive_files["id-2"] = ("信用卡/國泰世華/2026-08_國泰世華.pdf", encrypt(make_pdf([statement_lines(26)]), "A123456789"))
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["needs_review"] == 1 and summary["parsed"] == 1
    assert sorted(_file_rows(db_session)) == [("needs_review", "no_text_layer"), ("parsed", None)]


def test_ocr_unavailable_leaves_files_for_the_next_run_and_exits_1(world, tmp_path, db_session, monkeypatch, capsys):
    services, drive_files, cfg = _no_text_world(world, tmp_path, {"ok": False, "code": "unavailable"})
    drive_files["id-2"] = ("信用卡/國泰世華/2026-08_國泰世華.pdf", encrypt(make_pdf([["tiny"]]), "A123456789"))
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["errors"] == ["ocr_unavailable"] and summary["skipped"] == 2 and summary["failed"] == 0 and services.ocr.starts == 1
    assert _file_rows(db_session) == [("new", None), ("new", None)]
    # the CLI reports the degraded run with exit 1 while the run itself is `done`
    assert db_session.execute(text("select status from ingest_run order by id desc limit 1")).scalar_one() == "done"
    from worker import cli
    monkeypatch.setattr(pipeline, "build", lambda c, client, runner: services)
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: summary)
    for key, value in (("STATEMENT_API_TOKEN_FILE", cfg.api_token_file), ("STATEMENT_PASSWORD_FILE", cfg.password_file),
                       ("STATEMENT_STATE_DIR", cfg.state_dir), ("STATEMENT_API_URL", cfg.api_url)):
        monkeypatch.setenv(key, str(value))  # keep the CLI inside tmp_path (its singleton lock lives in the state dir)
    assert cli.main(["run"]) == 1
    # a later run with a working child parses both
    fake_ocr_child.write(tmp_path / "ocr-child.sh", fake_ocr_child.ok())
    from worker import ocr
    services.ocr = ocr.Engine(cfg)
    assert pipeline.run(services, trigger="owner_cli")["parsed"] == 2


def test_ocr_unavailable_leaves_an_existing_needs_review_row_alone(world, tmp_path, db_session):
    services, _, cfg = _no_text_world(world, tmp_path, {"ok": False, "code": "low_confidence"})
    pipeline.run(services, trigger="owner_cli")
    assert _file_rows(db_session) == [("needs_review", "no_text_layer")]
    db_session.execute(text("update statement_file set parser_version='old'")); db_session.commit()  # make it due again
    fake_ocr_child.write(tmp_path / "ocr-child.sh", {"ok": False, "code": "unavailable"})
    from worker import ocr
    services.ocr = ocr.Engine(cfg)
    pipeline.run(services, trigger="owner_cli")
    assert _file_rows(db_session) == [("needs_review", "no_text_layer")]


def test_ocr_text_over_stdin_cap_is_too_large_without_a_parser_call(world, tmp_path, db_session):
    from worker import parser as parser_mod
    big = "x" * (parser_mod.STDIN_CAP + 100)
    services, _, _ = _no_text_world(world, tmp_path, fake_ocr_child.ok(text=big))
    seen = _record_parser(services)
    pipeline.run(services, trigger="owner_cli")
    assert seen == [] and _file_rows(db_session) == [("needs_review", "too_large")]


def test_build_creates_an_engine_only_when_enabled(world, client):
    services, _, cfg = world
    assert services.ocr is not None and services.ocr.cfg is cfg
    import dataclasses
    assert pipeline.build(dataclasses.replace(cfg, ocr_enabled=False), client, services.runner).ocr is None
```
(`json` and `pytest` are already imported at the top of the file.)

- [ ] **Step 2: Run → FAIL**.

- [ ] **Step 3: Implement**

`worker/pipeline.py`:
1. `Services`: after `runner: Runner` add `ocr: ocr_mod.Engine | None = None`.
2. `build`: after `services.api = ApiClient(...)`: `services.ocr = ocr_mod.Engine(cfg) if cfg.ocr_enabled else None`.
3. `revision_body`:
```python
def revision_body(parsed: parse_schema.StatementParse, *, file_id: int, account_id: int, parser_version: str,
                  parser: str = "claude-cli", extra_note: str | None = None) -> dict:
    """`parser` names the path that produced the text (R5.3: `claude-cli+ocr`); `extra_note` is appended to notes."""
    lines = [line.model_dump(mode="json") for line in parsed.lines]
    notes = parsed.notes if extra_note is None else ((parsed.notes + "\n") if parsed.notes else "") + extra_note
    return {"file_id": file_id, "account_id": account_id, "kind": parsed.kind, "parser": parser,
            "parser_version": parser_version, "currency": parsed.currency,
            "period_start": parsed.period_start.isoformat(), "period_end": parsed.period_end.isoformat(),
            "closing_date": parsed.closing_date.isoformat() if parsed.closing_date else None,
            "due_date": parsed.due_date.isoformat() if parsed.due_date else None,
            "opening_balance": parsed.opening_balance, "statement_total": parsed.statement_total,
            "minimum_payment": parsed.minimum_payment, "lines": lines, "raw": {"notes": notes}}
```
4. `process_file`: replace from `try:\n        unlocked = pdf.unlock(data, candidates)` through `masked = services.masker.render(extracted, candidates)` with:
```python
    password = None
    ocr_note: str | None = None
    try:
        unlocked = pdf.unlock(data, candidates)
        password = candidates[unlocked.password_index] if unlocked.password_index is not None else None
        extracted = pdf.extract(data, password)
    except pdf.PasswordError:
        return _fail(services, lease, file_row, "password", summary, credential_version=services.credential_version)
    except pdf.Malformed:
        return _fail(services, lease, file_row, "parse", summary, parser_version=services.parser_version)
    except pdf.NoTextLayer:
        if services.ocr is None:
            return _review(services, lease, file_row, "no_text_layer", summary, has_text_layer=False)
        try:  # R5.3: the OCR child reads the pages; from here the path is the text-layer one
            result = services.ocr.read(data, password)
        except ocr_mod.OCRUnavailable:
            if "ocr_unavailable" not in summary.errors:
                summary.errors.append("ocr_unavailable")
            summary.skipped += 1
            return "skipped"
        except (ocr_mod.OCRTimeout, ocr_mod.OCRLowConfidence):
            return _review(services, lease, file_row, "no_text_layer", summary, has_text_layer=False)
        except pdf.TooLarge:
            return _review(services, lease, file_row, "too_large", summary, has_text_layer=False)
        except pdf.Malformed:
            return _fail(services, lease, file_row, "parse", summary, parser_version=services.parser_version)
        extracted = result.extracted
        ocr_note = (f"ocr: mean_confidence={result.mean_confidence:.2f} boxes={result.boxes} "
                    f"empty_pages={result.empty_pages} scale={ocr_mod.SCALE}")
    except pdf.TooLarge:
        return _review(services, lease, file_row, "too_large", summary)
    except pdf.ExtractTimeout:
        return _fail(services, lease, file_row, "transient", summary)
    services.api.update_file(lease, file_row["id"], status="unlocked", failure=None, has_text_layer=ocr_note is None,
                             text_chars=len(extracted.text), pages=extracted.pages,
                             credential_version=services.credential_version)
    masked = services.masker.mask_ocr(extracted.text, candidates) if ocr_note else services.masker.render(extracted, candidates)
```
and the `revision_body(...)` call → `revision_body(parsed, file_id=file_row["id"], account_id=account_id, parser_version=services.parser_version, parser="claude-cli+ocr" if ocr_note else "claude-cli", extra_note=ocr_note)`. The existing STDIN_CAP check stays between masking and the parser.

`worker/cli.py:103`:
```python
            bad = summary.get("bad_runs", 0) or any(code in summary.get("errors", []) for code in ("parser_disabled", "ocr_unavailable"))
```
and update the comment above it: `# 0 when the run finished done (transient codes included); 1 for a failed/aborted run, a disabled parser or an unavailable OCR child (degraded: files wait for the next run)`.

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/worker -q` → PASS (adjust `test_password_reaches_the_ocr_child` to build its cfg with `dataclasses.replace` if the `__dict__` trick trips the frozen dataclass).

- [ ] **Step 5: Commit**
```bash
git add worker/pipeline.py worker/cli.py tests/worker/test_pipeline.py
git commit -m "feat(worker): OCR branch in the per-file pipeline, provenance, degraded exit (R5.3)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: `export_masked` takes the same branch with provenance (R5.6)

**Files:**
- Modify: `worker/verify.py` (`export_masked`)
- Test: `tests/worker/test_verify.py`

- [ ] **Step 1: Failing tests** (append)

```python
from tests.worker import fake_ocr_child


def _ocr_cfg(cfg, tmp_path, response, **kw):
    import dataclasses
    child = fake_ocr_child.write(tmp_path / "ocr-child.sh", response, **kw)
    return dataclasses.replace(cfg, ocr_child=child, ocr_timeout_s=2)


OCR_TEXT = "\n".join(statement_lines()) + "\n持卡人 王 小 明\n卡號 4111 2222 3333 4444"


def test_export_masked_snapshot_carries_no_split_identifier(tmp_path):
    cfg, runner = _drive_world(tmp_path, {"a": encrypt(make_pdf([["short"]]), "A123456789")}, pw_text="STATEMENT_ID_NUMBER=A123456789\nSTATEMENT_HOLDER_NAMES=王小明\n信用卡/國泰世華=$ID\n")
    out = verify.export_masked(_ocr_cfg(cfg, tmp_path, fake_ocr_child.ok(text=OCR_TEXT)), runner, folder=None, limit=None)
    assert out["exported"] == 1 and out["ocr"] == 1 and out["no_text_layer"] == 0
    snap = next((tmp_path / "v" / "masked").glob("*.txt")).read_text(encoding="utf-8")
    compact = snap.replace(" ", "")
    assert "MERCHANT 001" in snap and "王小明" not in compact and "4111222233334444" not in compact and "A123456789" not in compact
    meta = json.loads(next((tmp_path / "v" / "masked").glob("*.json")).read_text(encoding="utf-8"))
    from worker import ocr
    assert meta["extraction"] == "ocr" and meta["ocr_fingerprint"] == ocr.FINGERPRINT


def test_export_masked_text_layer_meta_says_text(tmp_path):
    cfg, runner = _drive_world(tmp_path, {"a": encrypt(make_pdf([statement_lines()]), "A123456789")})
    verify.export_masked(_ocr_cfg(cfg, tmp_path, fake_ocr_child.ok()), runner, folder=None, limit=None)
    meta = json.loads(next((tmp_path / "v" / "masked").glob("*.json")).read_text(encoding="utf-8"))
    assert meta["extraction"] == "text" and "ocr_fingerprint" not in meta


@pytest.mark.parametrize("response,kw,counter", [({"ok": False, "code": "low_confidence"}, {}, "no_text_layer"), ({"ok": False, "code": "too_large"}, {}, "too_large"), ({"ok": False, "code": "malformed"}, {}, "errors"), (fake_ocr_child.ok(), {"sleep": 30}, "no_text_layer")])
def test_export_masked_ocr_failures_are_counted(tmp_path, response, kw, counter):
    cfg, runner = _drive_world(tmp_path, {"a": encrypt(make_pdf([["short"]]), "A123456789")})
    out = verify.export_masked(_ocr_cfg(cfg, tmp_path, response, **kw), runner, folder=None, limit=None)
    assert out["exported"] == 0 and out["ocr"] == 0 and out[counter] == 1


def test_export_masked_unavailable_is_one_code_for_the_run(tmp_path):
    cfg, runner = _drive_world(tmp_path, {"a": encrypt(make_pdf([["short"]]), "A123456789"), "b": encrypt(make_pdf([["tiny"]]), "A123456789")})
    out = verify.export_masked(_ocr_cfg(cfg, tmp_path, {"ok": False, "code": "unavailable"}), runner, folder=None, limit=None)
    assert out["ocr_unavailable"] is True and out["exported"] == 0 and out["errors"] == 2


def test_export_masked_with_ocr_disabled(tmp_path):
    import dataclasses
    cfg, runner = _drive_world(tmp_path, {"a": encrypt(make_pdf([["short"]]), "A123456789")})
    out = verify.export_masked(dataclasses.replace(cfg, ocr_enabled=False), runner, folder=None, limit=None)
    assert out["no_text_layer"] == 1 and out["ocr"] == 0
```

- [ ] **Step 2: Run → FAIL**.

- [ ] **Step 3: Implement** — in `export_masked`:

After `masker = …`: `engine = ocr_mod.Engine(cfg) if cfg.ocr_enabled else None`. `out` gains `"ocr": 0`. Replace the `except pdf.NoTextLayer:` arm with:
```python
        except pdf.NoTextLayer:
            if engine is None:
                out["no_text_layer"] += 1
                continue
            try:
                extracted = engine.read(data, password).extracted
                from_ocr = True
            except ocr_mod.OCRUnavailable:
                out["errors"] += 1
                out["ocr_unavailable"] = True
                continue
            except (ocr_mod.OCRTimeout, ocr_mod.OCRLowConfidence):
                out["no_text_layer"] += 1
                continue
            except pdf.TooLarge:
                out["too_large"] += 1
                continue
            except pdf.Malformed:
                out["errors"] += 1
                continue
```
Set `from_ocr = False` before the `try` that unlocks. Replace `rendered = masker.render(extracted, candidates.get(key, []))` with
```python
        literals = candidates.get(key, [])
        rendered = masker.mask_ocr(extracted.text, literals) if from_ocr else masker.render(extracted, literals)
```
In the meta JSON add `"extraction": "ocr" if from_ocr else "text"` and, only when `from_ocr`, `"ocr_fingerprint": ocr_mod.FINGERPRINT`. Increment `out["ocr"]` right after `out["exported"] += 1` when `from_ocr` (so it counts published OCR snapshots).

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/worker/test_verify.py -q` → PASS; commit:
```bash
git add worker/verify.py tests/worker/test_verify.py
git commit -m "feat(worker): export-masked OCR branch with snapshot provenance (R5.6)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Optional offline real-engine test, deploy notes, spec notes (R5.7, R5.9, R5.10)

**Files:**
- Modify: `pytest.ini`; Create: `tests/worker/test_ocr_models.py`
- Modify: `deploy/statements/README.md` ("Install" section; new "OCR (R5)" section after "Pinned CLI copy")
- Modify: the design spec (append "R5 implementation notes")

- [ ] **Step 1:** `pytest.ini`:
```
[pytest]
pythonpath = .
markers =
    ocr_models: runs the real OCR child under bwrap with no network (set RUN_OCR_MODEL_TESTS=1)
```

- [ ] **Step 2:** `tests/worker/test_ocr_models.py`
```python
"""Operator evidence (R5.7): the real child, under bwrap with no network and a clean HOME, reads a rendered page.
Skipped unless RUN_OCR_MODEL_TESTS=1. Proves the models come from the wheel (no download possible)."""
import os
import shutil

import pytest

from tests.worker.pdfgen import make_pdf
from worker import config, ocr

pytestmark = pytest.mark.ocr_models


@pytest.mark.skipif(os.environ.get("RUN_OCR_MODEL_TESTS") != "1", reason="set RUN_OCR_MODEL_TESTS=1")
def test_real_child_offline_reads_digits():
    pytest.importorskip("rapidocr_onnxruntime")
    assert shutil.which("bwrap"), "bubblewrap required"
    engine = ocr.Engine(config.load({"STATEMENT_PARSER_SANDBOX": "true", "STATEMENT_OCR_TIMEOUT": "180"}))
    assert "--share-net" not in engine.argv()
    page = ["STATEMENT 2026/09/30", "MERCHANT 001        1,234", "MERCHANT 002        5,678", "TOTAL        6,912"] * 6
    out = engine.read(make_pdf([page]), None)
    assert out.extracted.pages == 1 and out.mean_confidence >= 0.8 and out.boxes >= 20
    digits = out.extracted.text.replace(",", "").replace(".", "")
    assert "1234" in digits and "5678" in digits and "6912" in digits
```

- [ ] **Step 3: Run it once:** `RUN_OCR_MODEL_TESTS=1 .venv/bin/python -m pytest tests/worker/test_ocr_models.py -q -p no:cacheprovider` → PASS (first load ~10 s). If bwrap refuses a bind on this host, fix `_sandbox_prefix` (Task 6) rather than the test; record the exact bind list used in the commit body.

- [ ] **Step 4: Deploy README** — "Install": the venv install line becomes `uv pip install -r requirements.txt -r requirements-worker.txt -c requirements-worker-constraints-aarch64-py313.txt`, for **both** the worker and the verify venvs. New section:
```
## OCR (R5)

Files without a usable text layer (Type3 glyph fonts, `(cid:NNN)` garbage) are read by a child process
(`python -m worker.ocr_child`, pypdfium2 + RapidOCR) that the worker starts under bubblewrap with no network, a
2048 MB address-space limit, a 150 s CPU limit, no core dumps and a 120 s wall-clock deadline; PDF bytes go in on
stdin and one JSON line comes back. The text is normalised (split card numbers, IDs and names rejoined), masked
and line-guarded before the parser sees it. The revision's `parser` is `claude-cli+ocr`; `raw.notes` carries the
mean confidence. Models ship in the `rapidocr-onnxruntime` wheel; nothing is downloaded at run time (the
`ocr_models` test proves it under bwrap). `STATEMENT_OCR=false` restores the old behaviour.

The parser version is now `claude-cli-<cli>-<model>-<12-hex fingerprint>` over instruction, schema and OCR
(engine versions + layout rules); deploying R5 re-parses every needs_review/failed file whose failure is
parse/guardrail/no_text_layer/too_large once, never a parsed one. The operator gate keys on the parser argv (the
instruction is part of it), so this release re-runs the gate; an OCR-only bump later does not.

A run that cannot start the OCR child (missing wheel, broken onnxruntime) reports `ocr_unavailable` in its summary,
leaves those files for the next run and exits 1, so the systemd unit shows failed. Verify snapshots record
`extraction` and `ocr_fingerprint`; to refresh an OCR snapshot after an engine change, delete its `<sha>.txt` and
`.json` and run `export-masked` again. After the first real run, check `coredumpctl list` and the worker journal
for native-library output; none is expected.
```

- [ ] **Step 5: Spec notes** — append after the R5 design section:
```
### R5 implementation notes (2026-10-14, PR "R5")
- Child protocol and bwrap binds as designed; thread setting verified on the host: <Task 5 Step 3 finding>.
- `parse_schema.version(cli, model, ocr_fingerprint)` → 12-hex fingerprint, bounded at 64; tests adjusted.
- `revision_body` gained `parser` and `extra_note`; `Masker.mask_ocr` = text masking + line guard.
- `export_masked` meta: `extraction`, `ocr_fingerprint`; summary `ocr`, `ocr_unavailable`.
- CLI exits 1 on `ocr_unavailable` (degraded, retryable).
- Offline real-engine test `tests/worker/test_ocr_models.py` behind `RUN_OCR_MODEL_TESTS=1`; bwrap binds used: <list>.
```
Replace both angle-bracket phrases with the actual findings.

- [ ] **Step 6:** `.venv/bin/python -m pytest tests/worker tests/unit -q` → PASS.

- [ ] **Step 7: Commit**
```bash
git add pytest.ini tests/worker/test_ocr_models.py deploy/statements/README.md docs/superpowers/specs/2026-10-08-statement-reconciliation-design.md
git commit -m "docs(worker): R5 OCR deploy notes, offline real-engine evidence test, implementation notes

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Rollout (after merge; owner go for each step)

1. As `homehub-worker` and `homehub-verify`: `git fetch && checkout --detach <sha>`, then `uv pip install -r requirements.txt -r requirements-worker.txt -c requirements-worker-constraints-aarch64-py313.txt` in each venv; `RUN_OCR_MODEL_TESTS=1 pytest tests/worker/test_ocr_models.py` once as the worker (offline evidence on the real host).
2. `deploy/statements/gate.sh` as the worker.
3. `python -m worker run` as the worker: expect the 12 OCR-class files `parsed` (≥ 10 with guardrails ok), the 17 guardrail files re-parsed (informational), text-layer `parsed` counts unchanged, no `ocr_unavailable`, exit 0.
4. `coredumpctl list` and the worker journal: no native output with content. Record counts in `~/reports/home-hub-r5-release-<ts>/` and the memory file.
