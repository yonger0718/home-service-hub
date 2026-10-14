# Statement Reconciliation R5 — OCR for statements without a text layer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Statements whose PDF has no usable text layer (Type3 glyph fonts, `(cid:NNN)` garbage) are read locally with RapidOCR and then go through the unchanged masker → sandboxed parser → guardrails path, with their OCR origin recorded on the revision.

**Architecture:** `pdf.extract` additionally treats font garbage as "no text layer". A new `worker/ocr.py` renders pages with pypdfium2, reads them with RapidOCR (lazy, self-tested engine; injectable backend for tests), rebuilds rows and returns the same `pdf.Extracted` shape. `pipeline.process_file` and `verify.export_masked` take that branch on `NoTextLayer`. One parser version string carries the OCR engine version; the parser instruction gains a credit-sign rule. No database migration.

**Tech Stack:** Python 3.13, pypdfium2 5.14.0, rapidocr-onnxruntime 1.4.4 (onnxruntime 1.31.0), existing worker package (pdfplumber/pypdf, bwrap parser), pytest.

**Spec:** `docs/superpowers/specs/2026-10-08-statement-reconciliation-design.md`, section "R5 OCR design (2026-10-14, PR "R5")". Read R5.1–R5.10 before any task; the spec is the authority, this plan argues from it.

**Workspace:** worktree `/home/opc/workspace/home-hub-reconcile`, branch `feat/reconciliation-r5-ocr` (from origin/main 73e8d18). All paths below are relative to `services/accounting-service/` in that worktree. Run tests with `.venv/bin/python -m pytest` from that directory. Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Global Constraints

- Page images and OCR text are statement content: never written to disk (other than the masked snapshots verify already writes), never logged, never put in exception text; exceptions from the OCR module carry class names and fixed phrases only, like `worker/pdf.py` (`raise … from None`).
- Trigger (R5.1): `NoTextLayer` when page 1 has fewer than 200 non-whitespace characters **or** `(cid:` tokens are ≥ 10 % of its whitespace-separated tokens.
- OCR bounds (R5.2/R5.8): `max_pages=10`, `timeout_s=120`, `scale=3.0`, threads = `cfg.ocr_threads` (default 2), mean confidence floor `0.80`.
- Row reconstruction (R5.2): group by vertical centre, tolerance = half the median box height; left to right; spaces proportional to the gap, one per 0.6 × median height, clamped to 1..12; pages joined with `\f`; `tables = []`.
- Provenance (R5.3): revision `parser = "claude-cli+ocr"`; `raw.notes` = parser notes plus one line `ocr: mean_confidence=<2 dp> boxes=<n> scale=<scale>`.
- Version (R5.4): `parse_schema.version(cli_version, model, ocr_version)` → `claude-cli-<cli>-<model>-<instr8>-<schema8>-ocr-<ocr_version>` for every file.
- Instruction (R5.5): append exactly: `On a card statement every negative row is a credit: a payment received is payment, a cash-back or points credit is reward, and any other reversal (退款, 取消, 分期調整 / 轉分期 reversing a purchase) is refund; never label a negative card row balance_adjustment.`
- Outcomes (R5.3): `OCRUnavailable` → run error code `ocr_unavailable` once, file untouched and counted `skipped`; `OCRTimeout`/`OCRLowConfidence` → `needs_review/no_text_layer` with `has_text_layer=False`; `TooLarge` → `needs_review/too_large`; a render failure (`pdf.Malformed`) → `failed/parse` at the current parser version; `cfg.ocr_enabled=False` → `needs_review/no_text_layer` as today.
- Config (R5.7): `STATEMENT_OCR` (default `true`), `STATEMENT_OCR_THREADS` (default `2`).
- Dependencies pinned in `requirements-worker.txt`: `rapidocr-onnxruntime==1.4.4`, `pypdfium2==5.14.0`, `onnxruntime==1.31.0`.
- Surgical changes only (repo CLAUDE.md rules 2/3): touch what a task names; match the existing style (docstrings that state the ruling, `# noqa: BLE001` on broad excepts, no library text in exceptions).

## Review Focus

1. A PDF whose page 1 is garbage but later pages have real text: still OCR'd whole (the trigger reads page 1 only, as the existing rule does) — Task 2 test `test_cid_garbage_is_no_text_layer` covers the trigger; Task 3 `test_read_renders_every_page` shows all pages are read.
2. A page the engine cannot read (blank or decorative): zero boxes → mean confidence 0.0 → `needs_review/no_text_layer`, never a parsed empty statement — Task 3 `test_read_zero_boxes_is_low_confidence`, Task 5 `test_ocr_low_confidence_needs_review`.
3. A long scanned PDF: 11+ pages → `needs_review/too_large` before any OCR work — Task 3 `test_read_page_bound`, Task 5 `test_ocr_too_many_pages_is_too_large`.
4. The engine failing to load on a host (wheel missing, broken onnxruntime): the run finishes, one `ocr_unavailable` code, the files stay `new` for the next run, nothing is marked failed — Task 5 `test_ocr_unavailable_leaves_the_file_for_the_next_run`.
5. pypdfium2 refusing bytes that pypdf unlocked (odd encryption): `pdf.Malformed` → `failed/parse`, contained to the file — Task 3 `test_read_malformed_bytes`, Task 5 `test_ocr_render_failure_is_a_parse_failure`.

---

### Task 1: Dependencies and configuration

**Files:**
- Modify: `requirements-worker.txt`
- Modify: `worker/config.py` (dataclass fields after `account_map_file`; `load()`)
- Modify: `README.md` (env table, after the `STATEMENT_PARSER_TIMEOUT` row near line 365)
- Test: `tests/worker/test_config.py`

**Interfaces:**
- Produces: `WorkerConfig.ocr_enabled: bool`, `WorkerConfig.ocr_threads: int` (read by Tasks 5 and 6).

- [ ] **Step 1: Install the new wheels into the test venv** (network needed once)

```bash
cd /home/opc/workspace/home-hub-reconcile/services/accounting-service
/usr/local/bin/uv pip install --python .venv/bin/python "rapidocr-onnxruntime==1.4.4" "pypdfium2==5.14.0" "onnxruntime==1.31.0"
.venv/bin/python -c "import rapidocr_onnxruntime, pypdfium2, onnxruntime; print(onnxruntime.__version__)"
```
Expected: prints `1.31.0`. (An onnxruntime warning about `pci_bus_id` on stderr is normal on this VM.)

- [ ] **Step 2: Write the failing config tests** (append to `tests/worker/test_config.py`)

```python
def test_ocr_defaults_on_with_two_threads():
    cfg = config.load({})
    assert cfg.ocr_enabled is True and cfg.ocr_threads == 2


def test_ocr_can_be_disabled_and_threads_set():
    cfg = config.load({"STATEMENT_OCR": "false", "STATEMENT_OCR_THREADS": "4"})
    assert cfg.ocr_enabled is False and cfg.ocr_threads == 4
```

- [ ] **Step 3: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/worker/test_config.py -q`
Expected: FAIL, `AttributeError: 'WorkerConfig' object has no attribute 'ocr_enabled'`.

- [ ] **Step 4: Add the fields and the loader lines**

In `worker/config.py`, after `account_map_file: Path | None` add:
```python
    ocr_enabled: bool = True   # R5: OCR for files without a text layer
    ocr_threads: int = 2
```
In `load()`, after the `account_map_file=` line add:
```python
        ocr_enabled=_str(env, "STATEMENT_OCR", "true").lower() in TRUE,
        ocr_threads=int(_str(env, "STATEMENT_OCR_THREADS", "2")),
```

- [ ] **Step 5: Pin the dependencies**

Append to `requirements-worker.txt`:
```
rapidocr-onnxruntime==1.4.4
pypdfium2==5.14.0
onnxruntime==1.31.0
```

- [ ] **Step 6: Document the variables** — in `README.md` env table add after the `STATEMENT_PARSER_TIMEOUT` row:
```
| `STATEMENT_OCR` | `true` | OCR (RapidOCR, local) for PDFs without a text layer (R5); `false` sends them to needs_review/no_text_layer |
| `STATEMENT_OCR_THREADS` | `2` | onnxruntime intra-op threads for OCR |
```

- [ ] **Step 7: Run the tests**

Run: `.venv/bin/python -m pytest tests/worker/test_config.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add requirements-worker.txt worker/config.py README.md tests/worker/test_config.py
git commit -m "feat(worker): R5 OCR dependencies and config

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Font-garbage detection in `pdf.extract` (R5.1)

**Files:**
- Modify: `worker/pdf.py` (`extract`, the `page1 < 200` check around line 125)
- Test: `tests/worker/test_pdf.py`

**Interfaces:**
- Produces: `pdf.is_font_garbage(text: str) -> bool` (module-level, pure) — reused by the test and documented for Task 3's docstring.

- [ ] **Step 1: Write the failing tests** (append to `tests/worker/test_pdf.py`)

```python
def test_cid_garbage_is_no_text_layer():
    garbage = ["(cid:12)(cid:40) (cid:7)(cid:9)(cid:33) (cid:5) (cid:88)(cid:2) 1,234 (cid:4)"] * 30
    with pytest.raises(pdf.NoTextLayer):
        pdf.extract(make_pdf([garbage]), None)


def test_is_font_garbage_threshold():
    assert pdf.is_font_garbage("(cid:1) (cid:2) (cid:3) 1234 abc") is True      # 3 of 5 tokens
    assert pdf.is_font_garbage("a b c d e f g h i (cid:1)") is False            # 1 of 10 tokens
    assert pdf.is_font_garbage("") is False
```
(`make_pdf` puts each list entry on its own line with a Type1 font, so pdfplumber extracts the literal `(cid:NN)` strings; the page then has well over 200 characters, which is why the token rule, not the length rule, must trigger.)

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/worker/test_pdf.py -q -k "garbage"`
Expected: `test_cid_garbage_is_no_text_layer` FAILS (`DID NOT RAISE`), `test_is_font_garbage_threshold` FAILS (`AttributeError`).

- [ ] **Step 3: Implement**

In `worker/pdf.py` add above `extract`:
```python
GARBAGE_TOKEN = "(cid:"
GARBAGE_SHARE = 0.10


def is_font_garbage(text: str) -> bool:
    """R5.1: a Type3 font without a ToUnicode map extracts as `(cid:NNN)` tokens; at least 10 % of the
    whitespace-separated tokens being such tokens means there is no usable text layer."""
    tokens = text.split()
    if not tokens:
        return False
    return sum(1 for t in tokens if GARBAGE_TOKEN in t) / len(tokens) >= GARBAGE_SHARE
```
Replace the existing check
```python
                page1 = len(texts[0].strip()) if texts else 0
                if page1 < 200:
                    raise NoTextLayer(f"page 1 has {page1} characters") from None
```
with
```python
                page1 = len(texts[0].strip()) if texts else 0
                if page1 < 200:
                    raise NoTextLayer(f"page 1 has {page1} characters") from None
                if is_font_garbage(texts[0]):
                    raise NoTextLayer("page 1 is font garbage") from None
```
Update the module docstring's first line to mention garbage: `Unlock (pypdf) and extract (pdfplumber) within the §5.4 bounds; R5.1 treats (cid:) font garbage as no text layer.`

- [ ] **Step 4: Run the pdf tests**

Run: `.venv/bin/python -m pytest tests/worker/test_pdf.py -q`
Expected: PASS (all, including the pre-existing ones).

- [ ] **Step 5: Commit**

```bash
git add worker/pdf.py tests/worker/test_pdf.py
git commit -m "feat(worker): treat (cid:) font garbage as no text layer (R5.1)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: The OCR module (R5.2)

**Files:**
- Create: `worker/ocr.py`
- Test: `tests/worker/test_ocr.py` (new)

**Interfaces:**
- Consumes: `pdf.Extracted(text, tables, pages, text_chars_page1)` (dataclass in `worker/pdf.py`), `pdf.TooLarge`, `pdf.Malformed`, `pdf._Alarm` pattern (copy, do not import the private class).
- Produces (used by Tasks 4, 5, 6):
  - `ENGINE_VERSION: str` — `importlib.metadata.version("rapidocr-onnxruntime")`, or `"0"` when the package is absent.
  - `class OCRUnavailable(RuntimeError)`, `class OCRTimeout(RuntimeError)`, `class OCRLowConfidence(RuntimeError)`.
  - `Box = tuple[float, float, float, float, str, float]` — `(x, y, w, h, text, score)` in pixels.
  - `def layout(boxes: list[Box]) -> str`.
  - `@dataclass class OcrResult: extracted: pdf.Extracted; mean_confidence: float; boxes: int`.
  - `class Engine: __init__(self, threads: int = 2, backend: Callable[[np.ndarray], list] | None = None)`; `read(self, data: bytes, password: str | None, *, max_pages: int = 10, timeout_s: int = 120, scale: float = 3.0) -> OcrResult`.
  - `SCALE = 3.0`, `CONFIDENCE_FLOOR = 0.80`, `MAX_PAGES = 10`, `TIMEOUT_S = 120` (module constants the pipeline passes/reads).

- [ ] **Step 1: Check the RapidOCR constructor on this host** (so the thread setting is real, not guessed; pypdfium2 5.14 was checked on 2026-10-14: `PdfDocument(input, password=None)`, `PdfPage.render(scale=…, **kwargs)` accepts `rev_byteorder=True`, `PdfBitmap.to_numpy()` exists)

```bash
.venv/bin/python - <<'PY'
import inspect, pathlib, rapidocr_onnxruntime
from rapidocr_onnxruntime import RapidOCR
print(inspect.signature(RapidOCR.__init__))
cfg = pathlib.Path(rapidocr_onnxruntime.__file__).with_name("config.yaml")
print([l.strip() for l in cfg.read_text().splitlines() if "thread" in l])
PY
```
Decision rule: if the signature accepts `**kwargs` and `config.yaml` has `intra_op_num_threads` under `Global`, construct with `RapidOCR(intra_op_num_threads=threads, inter_op_num_threads=1)`; if the key is nested differently, pass it as that key (`RapidOCR(**{"Global.intra_op_num_threads": threads})` style is NOT supported — use the key names the file shows). Record the finding in the module docstring.

- [ ] **Step 2: Write the failing tests** (`tests/worker/test_ocr.py`)

```python
import numpy as np
import pytest

from tests.worker.pdfgen import encrypt, make_pdf, statement_lines
from worker import ocr, pdf


def box(x, y, w, h, text, score=0.95):
    return (float(x), float(y), float(w), float(h), text, float(score))


def test_layout_groups_rows_by_centre_and_keeps_column_gaps():
    # two rows; the second row has a tall Chinese box and a short digit box with the same centre
    rows = [box(10, 100, 60, 20, "2026/09/01"), box(400, 102, 80, 16, "1,234"), box(120, 100, 150, 20, "COFFEE"),
            box(10, 140, 60, 28, "商店"), box(400, 146, 60, 16, "56")]
    out = ocr.layout(rows)
    lines = out.split("\n")
    assert lines[0].startswith("2026/09/01") and "COFFEE" in lines[0] and lines[0].endswith("1,234")
    assert lines[1].startswith("商店") and lines[1].endswith("56")
    # the gap between COFFEE (ends at 270) and 1,234 (starts at 400) is several spaces, clamped at 12
    gap = lines[0][lines[0].index("COFFEE") + len("COFFEE"):lines[0].index("1,234")]
    assert 2 <= len(gap) <= 12 and gap.strip() == ""


def test_layout_empty():
    assert ocr.layout([]) == ""


def _backend(boxes_per_call):
    calls = []

    def backend(img):
        assert isinstance(img, np.ndarray) and img.ndim == 3 and img.shape[2] == 3
        calls.append(img.shape)
        return boxes_per_call
    backend.calls = calls
    return backend


RAW = [[[[10, 10], [200, 10], [200, 30], [10, 30]], "MERCHANT 001", 0.97],
       [[[400, 12], [460, 12], [460, 28], [400, 28]], "1,234", 0.93]]


def test_read_renders_every_page_and_reports_confidence():
    backend = _backend(RAW)
    engine = ocr.Engine(backend=backend)
    out = engine.read(make_pdf([statement_lines(), ["PAGE TWO"]]), None)
    assert len(backend.calls) == 2 and out.extracted.pages == 2 and out.boxes == 4
    assert out.extracted.text.count("\f") == 1 and "MERCHANT 001" in out.extracted.text
    assert abs(out.mean_confidence - 0.95) < 1e-6 and out.extracted.tables == []
    assert out.extracted.text_chars_page1 == len(out.extracted.text.split("\f")[0].strip())


def test_read_uses_the_password():
    backend = _backend(RAW)
    data = encrypt(make_pdf([statement_lines()]), "A123456789")
    assert ocr.Engine(backend=backend).read(data, "A123456789").extracted.pages == 1


def test_read_zero_boxes_is_low_confidence():
    with pytest.raises(ocr.OCRLowConfidence):
        ocr.Engine(backend=_backend([])).read(make_pdf([statement_lines()]), None)


def test_read_low_confidence_floor():
    low = [[[[10, 10], [200, 10], [200, 30], [10, 30]], "x", 0.5]]
    with pytest.raises(ocr.OCRLowConfidence):
        ocr.Engine(backend=_backend(low)).read(make_pdf([statement_lines()]), None)


def test_read_page_bound():
    with pytest.raises(pdf.TooLarge):
        ocr.Engine(backend=_backend(RAW)).read(make_pdf([["p"]] * 11), None, max_pages=10)
    assert ocr.Engine(backend=_backend(RAW)).read(make_pdf([["p"]] * 10), None, max_pages=10).extracted.pages == 10


def test_read_malformed_bytes():
    with pytest.raises(pdf.Malformed):
        ocr.Engine(backend=_backend(RAW)).read(b"%PDF-1.4 not really", None)


def test_read_timeout():
    import time

    def slow(img):  # the self-test call is not under the alarm; the per-page call is
        time.sleep(2)
        return RAW
    with pytest.raises(ocr.OCRTimeout):
        ocr.Engine(backend=slow).read(make_pdf([statement_lines()]), None, timeout_s=1)


def test_engine_unavailable_when_backend_cannot_load(monkeypatch):
    engine = ocr.Engine()
    monkeypatch.setattr(engine, "_load", lambda: (_ for _ in ()).throw(ocr.OCRUnavailable("engine")))
    with pytest.raises(ocr.OCRUnavailable):
        engine.read(make_pdf([statement_lines()]), None)


def test_engine_self_test_failure_is_unavailable(monkeypatch):
    def broken(img):
        raise RuntimeError("model missing")
    engine = ocr.Engine(backend=broken)
    with pytest.raises(ocr.OCRUnavailable):
        engine.read(make_pdf([statement_lines()]), None)


def test_errors_carry_no_content():
    try:
        ocr.Engine(backend=_backend([])).read(make_pdf([statement_lines()]), None)
    except ocr.OCRLowConfidence as exc:
        assert "MERCHANT" not in str(exc) and exc.__cause__ is None


def test_engine_version_is_a_string():
    assert isinstance(ocr.ENGINE_VERSION, str) and ocr.ENGINE_VERSION
```

- [ ] **Step 3: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/worker/test_ocr.py -q`
Expected: FAIL at import (`ModuleNotFoundError: No module named 'worker.ocr'`).

- [ ] **Step 4: Implement `worker/ocr.py`**

```python
"""R5.2: local OCR for PDFs without a usable text layer.

Rendering: pypdfium2 (one RGB bitmap per page, in memory). Reading: RapidOCR (rapidocr-onnxruntime; the PP-OCRv4
det/cls/rec models ship inside the wheel, nothing is downloaded at run time). Rows are rebuilt from the boxes so the
parser sees a statement-shaped text. The result has the `pdf.Extracted` shape, so the masker and the parser do not
know the text came from OCR.

Privacy: page bitmaps and OCR text stay in this process; nothing is written to disk or logged. Exceptions carry
fixed phrases only and are raised `from None`, like worker/pdf.py.

Thread setting (verified on this host, rapidocr-onnxruntime 1.4.4): RapidOCR(intra_op_num_threads=N, inter_op_num_threads=1).
"""
from __future__ import annotations

import signal
import threading
from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata
from io import BytesIO

from worker import pdf

try:
    ENGINE_VERSION = metadata.version("rapidocr-onnxruntime")
except metadata.PackageNotFoundError:  # the version string must exist even where the wheel is absent (R5.4)
    ENGINE_VERSION = "0"

SCALE = 3.0            # 72 dpi × 3 = 216 dpi
CONFIDENCE_FLOOR = 0.80
MAX_PAGES = 10
TIMEOUT_S = 120
SELF_TEST_SIDE = 32

Box = tuple[float, float, float, float, str, float]  # x, y, w, h, text, score (pixels)


class OCRUnavailable(RuntimeError):
    """The engine could not be imported, loaded or self-tested."""


class OCRTimeout(RuntimeError):
    """The per-file deadline fired."""


class OCRLowConfidence(RuntimeError):
    """Mean recognition confidence below CONFIDENCE_FLOOR (zero boxes count as 0.0)."""


@dataclass
class OcrResult:
    extracted: pdf.Extracted
    mean_confidence: float
    boxes: int


def layout(boxes: list[Box]) -> str:
    """Rows by vertical centre (tolerance half the median box height), left to right, spaces proportional to the
    horizontal gap: one space per 0.6 × median height, clamped to 1..12."""
    if not boxes:
        return ""
    heights = sorted(b[3] for b in boxes)
    med_h = heights[len(heights) // 2] or 1.0
    ordered = sorted(boxes, key=lambda b: (b[1] + b[3] / 2, b[0]))
    rows: list[list[Box]] = []
    centre = None
    for b in ordered:
        c = b[1] + b[3] / 2
        if rows and centre is not None and abs(c - centre) <= med_h * 0.5:
            rows[-1].append(b)
        else:
            rows.append([b])
            centre = c
    char_w = max(med_h * 0.6, 1.0)
    lines = []
    for row in rows:
        row.sort(key=lambda b: b[0])
        parts: list[str] = []
        prev_end = None
        for x, _y, w, _h, text, _s in row:
            if prev_end is not None:
                parts.append(" " * max(1, min(12, int((x - prev_end) / char_w))))
            parts.append(text)
            prev_end = x + w
        lines.append("".join(parts))
    return "\n".join(lines)


class _Alarm:
    """SIGALRM bound on one file (main thread only), mirroring worker/pdf.py; previous handler restored."""

    def __init__(self, seconds: int, fire):
        self.seconds, self.fire = seconds, fire
        self.active = threading.current_thread() is threading.main_thread()
        self._previous = None

    def __enter__(self):
        if self.active:
            self._previous = signal.signal(signal.SIGALRM, self.fire)
            signal.alarm(self.seconds)
        return self

    def __exit__(self, *exc):
        if self.active:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, self._previous)


class Engine:
    """One engine per run, loaded lazily on first `read`, self-tested on a white square.

    `backend(img: np.ndarray) -> list[[box(4 points), text, score], ...]` is RapidOCR's call shape; tests inject one.
    """

    def __init__(self, threads: int = 2, backend: Callable | None = None):
        self.threads = threads
        self._backend = backend
        self._ready = False

    @staticmethod
    def _fire(signum, frame):
        raise OCRTimeout("ocr timed out")

    def _load(self) -> None:
        if self._backend is not None:
            return
        try:
            from rapidocr_onnxruntime import RapidOCR  # noqa: PLC0415 — imported here so a missing wheel is a run code, not a crash

            engine = RapidOCR(intra_op_num_threads=self.threads, inter_op_num_threads=1)
        except Exception:  # noqa: BLE001 — import or model load; the library text never leaves the process
            raise OCRUnavailable("engine") from None

        def backend(img):
            result, _elapse = engine(img)
            return result or []
        self._backend = backend

    def _ensure(self) -> None:
        if self._ready:
            return
        self._load()
        try:
            import numpy as np  # noqa: PLC0415

            self._backend(np.full((SELF_TEST_SIDE, SELF_TEST_SIDE, 3), 255, dtype=np.uint8))
        except Exception:  # noqa: BLE001
            raise OCRUnavailable("self-test") from None
        self._ready = True

    def read(self, data: bytes, password: str | None, *, max_pages: int = MAX_PAGES, timeout_s: int = TIMEOUT_S,
             scale: float = SCALE) -> OcrResult:
        self._ensure()
        import numpy as np  # noqa: PLC0415
        import pypdfium2 as pdfium  # noqa: PLC0415

        with _Alarm(timeout_s, self._fire):
            try:
                doc = pdfium.PdfDocument(BytesIO(data), password=password)
                npages = len(doc)
            except OCRTimeout:
                raise
            except Exception:  # noqa: BLE001
                raise pdf.Malformed("malformed") from None
            if npages > max_pages:
                raise pdf.TooLarge(f"{npages} pages") from None
            texts: list[str] = []
            scores: list[float] = []
            total = 0
            try:
                for page in doc:
                    img = page.render(scale=scale, rev_byteorder=True).to_numpy()  # (h, w, 3) uint8, RGB; no Pillow
                    boxes: list[Box] = []
                    for quad, text, score in self._backend(img):
                        xs = [p[0] for p in quad]
                        ys = [p[1] for p in quad]
                        boxes.append((min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys), str(text), float(score)))
                        scores.append(float(score))
                    total += len(boxes)
                    texts.append(layout(boxes))
            except OCRTimeout:
                raise
            except Exception:  # noqa: BLE001 — rendering or engine failure on a page
                raise pdf.Malformed("malformed") from None
        mean = sum(scores) / len(scores) if scores else 0.0
        if mean < CONFIDENCE_FLOOR:
            raise OCRLowConfidence("low confidence") from None
        page1 = len(texts[0].strip()) if texts else 0
        return OcrResult(pdf.Extracted("\f".join(texts), [], npages, page1), mean, total)
```
Note `pdf.Extracted` is constructed positionally `(text, tables, pages, text_chars_page1)` — check the dataclass field order in `worker/pdf.py` (line ~49) and use keyword arguments if it differs.

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/worker/test_ocr.py -q`
Expected: PASS (`test_read_timeout` takes about 3 s: a 2 s self-test call, then the 1 s alarm fires during the page call).

- [ ] **Step 6: Commit**

```bash
git add worker/ocr.py tests/worker/test_ocr.py
git commit -m "feat(worker): OCR module — pypdfium2 render, RapidOCR read, row layout (R5.2)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: One parser version string and the credit-sign instruction (R5.4, R5.5)

**Files:**
- Modify: `worker/parse_schema.py` (`INSTRUCTION`, `version()`)
- Modify: `worker/pipeline.py:110` (`build`), `worker/verify.py:200` (`run`)
- Test: `tests/worker/test_parser.py` (the `version` test near line 192), new assertions

**Interfaces:**
- Consumes: `ocr.ENGINE_VERSION` (Task 3).
- Produces: `parse_schema.version(cli_version: str, model: str, ocr_version: str) -> str`.

- [ ] **Step 1: Write/adjust the failing tests** — in `tests/worker/test_parser.py` replace the body of the existing version test (lines ~192–193) with:

```python
    v = parse_schema.version("2.1.296", "claude-sonnet-5-5", "1.4.4")
    assert v.startswith("claude-cli-2.1.296-claude-sonnet-5-5-") and v.endswith("-ocr-1.4.4")
    instr, schema = v.split("-")[-4:-2]
    assert len(instr) == 8 and len(schema) == 8
    assert parse_schema.version("2.1.296", "claude-sonnet-5-5", "1.4.5") != v
```
and append:
```python
def test_instruction_states_the_card_credit_rule():
    assert "never label a negative card row balance_adjustment" in parse_schema.INSTRUCTION
    assert "分期調整" in parse_schema.INSTRUCTION
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/worker/test_parser.py -q -k "version or credit_rule"`
Expected: FAIL (`TypeError: version() takes 2 positional arguments` and the assertion on INSTRUCTION).

- [ ] **Step 3: Implement**

In `worker/parse_schema.py`:
- append to the `INSTRUCTION` tuple, as the last string element (before the closing parenthesis), exactly:
```python
    " On a card statement every negative row is a credit: a payment received is payment, a cash-back or points "
    "credit is reward, and any other reversal (退款, 取消, 分期調整 / 轉分期 reversing a purchase) is refund; never "
    "label a negative card row balance_adjustment."
```
- change `version`:
```python
def version(cli_version: str, model: str, ocr_version: str) -> str:
    """One version for every file (R5.4): the OCR engine version is part of it, so an engine bump re-queues
    needs_review/failed files exactly like an instruction or schema change."""
    instr = hashlib.sha256(INSTRUCTION.encode()).hexdigest()[:8]
    schema = hashlib.sha256(json.dumps(json_schema(), sort_keys=True).encode()).hexdigest()[:8]
    return f"claude-cli-{cli_version}-{model}-{instr}-{schema}-ocr-{ocr_version}"
```
In `worker/pipeline.py` line 110: `services.parser_version = parse_schema.version(services.cli_version, cfg.parser_model, ocr_mod.ENGINE_VERSION)` and add `from worker import ocr as ocr_mod` to the imports (line 17 block).
In `worker/verify.py` line 200: `version = parse_schema.version(prs.cli_version() or "unknown", cfg.parser_model, ocr_mod.ENGINE_VERSION)` with the same import.

- [ ] **Step 4: Run the worker test suite**

Run: `.venv/bin/python -m pytest tests/worker -q`
Expected: PASS. (`tests/worker/test_pipeline.py::test_end_to_end_parses_and_is_idempotent` checks only the `claude-cli-2.1.296-` prefix.)

- [ ] **Step 5: Commit**

```bash
git add worker/parse_schema.py worker/pipeline.py worker/verify.py tests/worker/test_parser.py
git commit -m "feat(worker): parser version carries the OCR engine; card credit-sign rule (R5.4, R5.5)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: The pipeline OCR branch (R5.3)

**Files:**
- Modify: `worker/pipeline.py` (`Services`, `build`, `process_file` lines ~243–292, `revision_body`)
- Test: `tests/worker/test_pipeline.py`

**Interfaces:**
- Consumes: `ocr.Engine`, `ocr.OcrResult`, `ocr.OCRUnavailable/OCRTimeout/OCRLowConfidence`, constants (Task 3); `cfg.ocr_enabled/ocr_threads` (Task 1).
- Produces: `Services.ocr: ocr_mod.Engine | None`; `revision_body(parsed, *, file_id, account_id, parser_version, parser="claude-cli", extra_note=None)`.

- [ ] **Step 1: Write the failing tests** (append to `tests/worker/test_pipeline.py`; they reuse `world` and `_file_rows`)

```python
def _no_text_world(world):
    """Replace the Drive PDF with one whose page 1 is too short for a text layer."""
    services, drive_files, cfg = world
    drive_files["id-1"] = ("信用卡/國泰世華/2026-09_國泰世華.pdf", encrypt(make_pdf([["short"]]), "A123456789"))
    return services, drive_files, cfg


class _FakeEngine:
    def __init__(self, outcome):
        self.outcome, self.calls = outcome, 0

    def read(self, data, password, **kw):
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _ocr_ok():
    from worker import ocr, pdf
    text = "\n".join(statement_lines())
    return ocr.OcrResult(pdf.Extracted(text, [], 1, len(text)), 0.93, 42)


def test_ocr_path_parses_and_records_provenance(world, db_session):
    services, _, _ = _no_text_world(world)
    services.ocr = _FakeEngine(_ocr_ok())
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["parsed"] == 1 and services.ocr.calls == 1
    row = db_session.execute(text("select status, has_text_layer, text_chars from statement_file")).one()
    assert row == ("parsed", False, len("\n".join(statement_lines())))
    rev = db_session.execute(text("select parser, raw from statement_revision")).one()
    assert rev[0] == "claude-cli+ocr" and "ocr: mean_confidence=0.93 boxes=42 scale=3.0" in rev[1]["notes"]


def test_ocr_disabled_keeps_todays_behaviour(world, db_session):
    services, _, _ = _no_text_world(world)
    services.ocr = None
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["needs_review"] == 1 and _file_rows(db_session) == [("needs_review", "no_text_layer")]


def test_ocr_low_confidence_needs_review(world, db_session):
    from worker import ocr
    services, _, _ = _no_text_world(world)
    services.ocr = _FakeEngine(ocr.OCRLowConfidence("low confidence"))
    pipeline.run(services, trigger="owner_cli")
    row = db_session.execute(text("select status, failure, has_text_layer from statement_file")).one()
    assert row == ("needs_review", "no_text_layer", False)


def test_ocr_timeout_needs_review(world, db_session):
    from worker import ocr
    services, _, _ = _no_text_world(world)
    services.ocr = _FakeEngine(ocr.OCRTimeout("ocr timed out"))
    pipeline.run(services, trigger="owner_cli")
    assert _file_rows(db_session) == [("needs_review", "no_text_layer")]


def test_ocr_too_many_pages_is_too_large(world, db_session):
    from worker import pdf
    services, _, _ = _no_text_world(world)
    services.ocr = _FakeEngine(pdf.TooLarge("11 pages"))
    pipeline.run(services, trigger="owner_cli")
    assert _file_rows(db_session) == [("needs_review", "too_large")]


def test_ocr_render_failure_is_a_parse_failure(world, db_session):
    from worker import pdf
    services, _, _ = _no_text_world(world)
    services.ocr = _FakeEngine(pdf.Malformed("malformed"))
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["failed"] == 1 and _file_rows(db_session) == [("failed", "parse")]


def test_ocr_unavailable_leaves_the_file_for_the_next_run(world, db_session):
    from worker import ocr
    services, _, _ = _no_text_world(world)
    services.ocr = _FakeEngine(ocr.OCRUnavailable("engine"))
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["errors"] == ["ocr_unavailable"] and summary["skipped"] == 1 and summary["failed"] == 0
    assert _file_rows(db_session) == [("new", None)]
    # a later run with a working engine parses it
    services.ocr = _FakeEngine(_ocr_ok())
    assert pipeline.run(services, trigger="owner_cli")["parsed"] == 1


def test_build_creates_an_engine_only_when_enabled(world, tmp_path, client):
    services, _, cfg = world
    assert services.ocr is not None and services.ocr.threads == cfg.ocr_threads
    import dataclasses
    off = pipeline.build(dataclasses.replace(cfg, ocr_enabled=False), client, services.runner)
    assert off.ocr is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/worker/test_pipeline.py -q -k "ocr"`
Expected: FAIL (`AttributeError` on `services.ocr` / assertions).

- [ ] **Step 3: Implement**

In `worker/pipeline.py`:

1. Import: `from worker import ocr as ocr_mod` (already added in Task 4; keep one import).
2. `Services`: after `runner: Runner` add `ocr: ocr_mod.Engine | None = None`.
3. `build`: after `services.api = ApiClient(...)` add `services.ocr = ocr_mod.Engine(threads=cfg.ocr_threads) if cfg.ocr_enabled else None`.
4. `revision_body`: new signature and body
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
5. `process_file`: replace the block from `try:\n        unlocked = pdf.unlock(data, candidates)` through the `services.api.update_file(lease, file_row["id"], status="unlocked", ...)` call with:
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
        try:  # R5.3: read the pages locally; the rest of the path is unchanged
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
        ocr_note = f"ocr: mean_confidence={result.mean_confidence:.2f} boxes={result.boxes} scale={ocr_mod.SCALE}"
    except pdf.TooLarge:
        return _review(services, lease, file_row, "too_large", summary)
    except pdf.ExtractTimeout:
        return _fail(services, lease, file_row, "transient", summary)
    services.api.update_file(lease, file_row["id"], status="unlocked", failure=None, has_text_layer=ocr_note is None,
                             text_chars=len(extracted.text), pages=extracted.pages,
                             credential_version=services.credential_version)
```
and change the `revision_body(...)` call below to
```python
    body = revision_body(parsed, file_id=file_row["id"], account_id=account_id, parser_version=services.parser_version,
                         parser="claude-cli+ocr" if ocr_note else "claude-cli", extra_note=ocr_note)
```
Keep every other line of `process_file` as it is. Note `scale=3.0` renders as `scale=3.0` in the f-string (float repr), which the test expects.

- [ ] **Step 4: Run the worker tests**

Run: `.venv/bin/python -m pytest tests/worker -q`
Expected: PASS (new and old; `test_summary_carries_codes_not_text` still passes because `ocr_unavailable` is a bounded code).

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline.py tests/worker/test_pipeline.py
git commit -m "feat(worker): OCR branch in the per-file pipeline with provenance and bounded outcomes (R5.3)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: `export_masked` takes the same branch (R5.6)

**Files:**
- Modify: `worker/verify.py` (`export_masked`: engine creation after `masker = …`, the `except pdf.NoTextLayer` arm, the summary dict)
- Test: `tests/worker/test_verify.py`

**Interfaces:**
- Consumes: `ocr.Engine`, `cfg.ocr_enabled/ocr_threads`.
- Produces: `export_masked` summary gains `"ocr": <count>`.

- [ ] **Step 1: Write the failing tests** (append to `tests/worker/test_verify.py`)

```python
def test_export_masked_ocrs_a_file_without_a_text_layer(tmp_path, monkeypatch):
    from worker import ocr, pdf
    pdfs = {"a": encrypt(make_pdf([["short"]]), "A123456789")}
    cfg, runner = _drive_world(tmp_path, pdfs)

    class Engine:
        def __init__(self, threads=2, backend=None):
            self.threads = threads

        def read(self, data, password, **kw):
            assert password == "A123456789"
            text = "\n".join(statement_lines())
            return ocr.OcrResult(pdf.Extracted(text, [], 1, len(text)), 0.9, 10)
    monkeypatch.setattr(ocr, "Engine", Engine)
    out = verify.export_masked(cfg, runner, folder=None, limit=None)
    assert out["exported"] == 1 and out["ocr"] == 1 and out["no_text_layer"] == 0
    snap = next((tmp_path / "v" / "masked").glob("*.txt")).read_text(encoding="utf-8")
    assert "MERCHANT 001" in snap and "A123456789" not in snap


def test_export_masked_counts_ocr_failures_as_no_text_layer(tmp_path, monkeypatch):
    from worker import ocr
    cfg, runner = _drive_world(tmp_path, {"a": encrypt(make_pdf([["short"]]), "A123456789")})

    class Engine:
        def __init__(self, threads=2, backend=None):
            pass

        def read(self, data, password, **kw):
            raise ocr.OCRLowConfidence("low confidence")
    monkeypatch.setattr(ocr, "Engine", Engine)
    out = verify.export_masked(cfg, runner, folder=None, limit=None)
    assert out["exported"] == 0 and out["no_text_layer"] == 1 and out["ocr"] == 0


def test_export_masked_with_ocr_disabled(tmp_path):
    import dataclasses
    cfg, runner = _drive_world(tmp_path, {"a": encrypt(make_pdf([["short"]]), "A123456789")})
    out = verify.export_masked(dataclasses.replace(cfg, ocr_enabled=False), runner, folder=None, limit=None)
    assert out["no_text_layer"] == 1 and out["ocr"] == 0
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/worker/test_verify.py -q -k "export_masked"`
Expected: the three new tests FAIL (`KeyError: 'ocr'` / assertion).

- [ ] **Step 3: Implement** — in `worker/verify.py`, `export_masked`:

After `masker = mask.Masker(...)` add:
```python
    engine = ocr_mod.Engine(threads=cfg.ocr_threads) if cfg.ocr_enabled else None  # R5.6: same branch as the run
```
Add `"ocr": 0` to the `out` dict. Replace the `except pdf.NoTextLayer:` arm with:
```python
        except pdf.NoTextLayer:
            if engine is None:
                out["no_text_layer"] += 1
                continue
            try:
                extracted = engine.read(data, password).extracted
                out["ocr"] += 1
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
`password` is assigned inside the same `try` before `pdf.extract`, so it is in scope. Import: `from worker import ocr as ocr_mod` (added in Task 4).

- [ ] **Step 4: Run the verify tests**

Run: `.venv/bin/python -m pytest tests/worker/test_verify.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/verify.py tests/worker/test_verify.py
git commit -m "feat(worker): export-masked OCRs files without a text layer (R5.6)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Optional real-model test, deploy notes, spec notes (R5.7, R5.9, R5.10)

**Files:**
- Modify: `pytest.ini` (markers)
- Create: `tests/worker/test_ocr_models.py`
- Modify: `deploy/statements/README.md` (section "Install", and a new "OCR (R5)" section after "Pinned CLI copy")
- Modify: `docs/superpowers/specs/2026-10-08-statement-reconciliation-design.md` (append "R5 implementation notes")

- [ ] **Step 1: Marker** — `pytest.ini` becomes:
```
[pytest]
pythonpath = .
markers =
    ocr_models: runs the real RapidOCR engine (slow, needs the wheel; set RUN_OCR_MODEL_TESTS=1)
```

- [ ] **Step 2: The optional test** (`tests/worker/test_ocr_models.py`)

```python
"""Real engine on a rendered synthetic page. Skipped unless RUN_OCR_MODEL_TESTS=1 (the wheel ships the models;
the test also proves nothing is downloaded by running with no proxy and a bogus HTTP(S)_PROXY)."""
import os

import pytest

from tests.worker.pdfgen import make_pdf
from worker import ocr

pytestmark = pytest.mark.ocr_models


@pytest.mark.skipif(os.environ.get("RUN_OCR_MODEL_TESTS") != "1", reason="set RUN_OCR_MODEL_TESTS=1")
def test_real_engine_reads_digits(monkeypatch):
    pytest.importorskip("rapidocr_onnxruntime")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")  # any download attempt would fail
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    page = ["STATEMENT 2026/09/30", "MERCHANT 001        1,234", "MERCHANT 002        5,678", "TOTAL        6,912"] * 6
    out = ocr.Engine(threads=2).read(make_pdf([page]), None)
    assert out.extracted.pages == 1 and out.mean_confidence >= 0.8
    assert "1,234" in out.extracted.text and "6,912" in out.extracted.text
```

- [ ] **Step 3: Run it for real once**

Run: `RUN_OCR_MODEL_TESTS=1 .venv/bin/python -m pytest tests/worker/test_ocr_models.py -q -p no:cacheprovider`
Expected: PASS in under a minute (first engine load ~10 s). If the digits are read with spaces or dots (`1.234`), relax only the digit assertions to `"1" in` / `"6" in` style and note it in the commit body; the production evidence is the spike (12/12 balance equations held).

- [ ] **Step 4: Deploy README** — in `deploy/statements/README.md`:
  - In "Install", where the venv is created, state: `uv pip install -r requirements.txt -r requirements-worker.txt` must run again for R5 in **both** the worker venv (`/home/homehub-worker/workspace/home-hub/services/accounting-service/.venv`) and the verify venv (`/home/homehub-verify/...`), as those users.
  - New section after "Pinned CLI copy":
```
## OCR (R5)

Files without a usable text layer (Type3 glyph fonts, `(cid:NNN)` garbage) are rendered with pypdfium2 and read
with RapidOCR inside the worker process, then masked and parsed like any other file; the revision's `parser` is
`claude-cli+ocr` and `raw.notes` carries the mean confidence. Models ship in the `rapidocr-onnxruntime` wheel;
nothing is downloaded at run time. `STATEMENT_OCR=false` restores the old behaviour (needs_review/no_text_layer).
Bounds: 10 pages, 120 s, 2 threads (`STATEMENT_OCR_THREADS`), confidence floor 0.80. The parser version now ends in
`-ocr-<engine version>`, so deploying R5 re-parses every needs_review/failed file once and never a parsed one.
Health: a run that cannot load the engine reports `ocr_unavailable` in its summary and leaves those files for the
next run.
```

- [ ] **Step 5: Spec notes** — append to the design spec, after the R5 design section:
```
### R5 implementation notes (2026-10-14, PR "R5")
- `worker/ocr.py` as designed; thread setting verified on the host: <what Task 3 Step 1 found>.
- `parse_schema.version` takes the OCR engine version; test adjusted (the last dash-separated part is the engine version).
- `revision_body` gained `parser` and `extra_note`; the API's `parser` field is free text, no schema change.
- `export_masked` summary gains `ocr` (and `ocr_unavailable: true` when the engine cannot load).
- Optional real-model test `tests/worker/test_ocr_models.py` behind `RUN_OCR_MODEL_TESTS=1`.
```
Fill in the angle-bracket phrase with the actual finding; no placeholders may remain.

- [ ] **Step 6: Full worker suite + the service unit suite**

Run: `.venv/bin/python -m pytest tests/worker tests/unit -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add pytest.ini tests/worker/test_ocr_models.py deploy/statements/README.md docs/superpowers/specs/2026-10-08-statement-reconciliation-design.md
git commit -m "docs(worker): R5 OCR deploy notes, optional real-model test, implementation notes

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Rollout (after merge; owner go for each step, as before)

1. Deploy: as `homehub-api` nothing changes (no migration); as `homehub-worker` and `homehub-verify`: `git fetch && checkout --detach <sha>`, then `uv pip install -r requirements.txt -r requirements-worker.txt` in each venv.
2. `deploy/statements/gate.sh` as the worker (parser unchanged, but the gate re-keys on the new parser version).
3. `python -m worker run` as the worker: expect the 12 OCR-class files `parsed` (≥ 10 with guardrails ok), the 17 guardrail files re-parsed (informational), text-layer `parsed` counts unchanged, summary without `ocr_unavailable`.
4. Record counts in `~/reports/home-hub-r5-release-<ts>/` and the memory file.
