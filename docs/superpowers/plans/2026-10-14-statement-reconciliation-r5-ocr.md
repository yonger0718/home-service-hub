# Statement Reconciliation R5 — OCR for statements without a text layer — Implementation Plan (rev 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Statements whose PDF has no usable text layer are read locally by a sandboxed OCR child process, normalised, masked and guarded, then go through the unchanged sandboxed parser → guardrails path with their OCR origin recorded on the revision.

**Architecture:** `pdf.extract` treats font garbage as "no text layer". `worker/ocr.py` starts `worker/ocr_child.py` (bwrap, no network, minimal env, rlimits, bounded streaming I/O, deadline with session kill) which renders pages with pypdfium2, reads them with RapidOCR and answers one JSON line; `worker/ocr_text.py` holds the pure geometric layout and the text-level identifier normalisation; `Masker.mask_ocr` = normalise → mask → non-numeric line guard. `pipeline.process_file` and `verify.export_masked` take the branch on `NoTextLayer`. One bounded parser version (12-hex fingerprint) covers CLI, model, instruction, schema and OCR; the instruction gains a credit-sign rule. No migration.

**Tech Stack:** Python 3.13, pypdfium2 5.14.0, rapidocr-onnxruntime 1.4.4 (onnxruntime 1.31.0), bubblewrap, pytest.

**Spec:** `docs/superpowers/specs/2026-10-08-statement-reconciliation-design.md`, section "R5 OCR design … rev 3", R5.1–R5.10. The spec is the authority.

**Workspace:** worktree `/home/opc/workspace/home-hub-reconcile`, branch `feat/reconciliation-r5-ocr` (from origin/main 73e8d18). Paths relative to `services/accounting-service/`; run tests with `.venv/bin/python -m pytest` there. Commits end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Global Constraints

- Statement content (PDF bytes, page images, OCR text, child output) never reaches disk (except verify's masked snapshots), logs, exception objects or summaries. Every error raised across the child boundary is constructed outside an active exception with a fixed phrase (`__cause__` and `__context__` None).
- Child environment is exactly `{"PATH": "/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": "/work"}` (unsandboxed: `PYTHONPATH` = the service directory). bwrap argv: `--unshare-all` and never `--share-net`; `--die-with-parent --new-session --cap-drop ALL`; ro-binds only `/usr`, `/lib`, `/lib64`, `realpath(sys.base_prefix)`, `realpath(sys.prefix)`, `<service>/worker → /work/worker`; `--tmpfs /tmp --proc /proc --dev /dev --chdir /work`.
- Child order: import-time `RLIMIT_CORE=0` + baseline AS 2048 MB / CPU 150 s → header line (≤ 4 KB) → header limits → readiness (imports, engine, self-test) → on failure print `unavailable` and exit 0 without reading further → PDF bytes → page bound → per page pixel bound (`ceil(w·scale)·ceil(h·scale)`, finite, positive) before render → recognise → `layout`. Output exactly one JSON line.
- Parent: stdin written from a thread; stdout read via selectors and counted; > 2 MB → kill → `Malformed`; deadline `cfg.ocr_timeout_s` → SIGKILL to the session group, reap with a 10 s bound → `OCRTimeout`. Exit classification: SIGKILL/SIGXCPU (negative returncode or bwrap's 128+n) → `OCRTimeout`; SIGSEGV/SIGABRT/SIGBUS/SIGFPE/SIGILL or any other non-zero exit → `Malformed`. Response validation: exactly one line, a JSON object; ok=True requires `pages` int in 1..`MAX_PAGES`, `empty_pages` int in 0..pages, `boxes` int ≥ 0, `mean_confidence` finite float in 0..1, `text` str; ok=False requires `code` in {unavailable, too_large, malformed, low_confidence}; anything else → `Malformed`. `OCRUnavailable` cached per run; `Engine.reset()` at each run boundary.
- Normalisation (R5.2a, per line, OCR branch only): (1) whitespace runs (`[ \t　]+`) between two all-digit tokens → one space; (2) tokens of 4+ chars from `[0-9OoIl|]` with ≥ 2 ASCII digits: `O/o→0`, `I/l/|→1`; (3) `[A-Z][12]\d{0,8}` + digit tokens whose concatenation is exactly `[A-Z][12]\d{8}` → joined; (4) whitespace between CJK-only tokens removed when the joined run ≤ 4 chars. `LAYOUT_VERSION` bumps on change.
- Guard (R5.2b): non-numeric literals only — the ID number, holder names (≥ 2 chars, whitespace removed on both sides), password candidates ≥ 6 chars containing a letter; a matching line → `[LINE MASKED]`.
- Outcomes (R5.3) as in the spec; `run` exits 1 on `ocr_unavailable`; `poll` unions the error codes of the runs it executed and exits 1 when any had `parser_disabled` or `ocr_unavailable`.
- Provenance: `parser = "claude-cli+ocr"`; notes line `ocr: mean_confidence=<2 dp> boxes=<n> empty_pages=<n> scale=<scale>` appended after the parser's own notes.
- Version (R5.4): `claude-cli-<cli[:12]>-<model[:24]>-<fp12>`, `fp12 = sha256("\n".join([cli, model, INSTRUCTION, schema_json, ocr_fingerprint]))[:12]`; max 61 chars; `ocr.FINGERPRINT = "<rapidocr ver>+<pypdfium2 ver>+layout<LAYOUT_VERSION>"`.
- Instruction (R5.5): append exactly `On a card statement every negative row is a credit: a payment received is payment, a cash-back or points credit is reward, and any other reversal (退款, 取消, 分期調整 / 轉分期 reversing a purchase) is refund; never label a negative card row balance_adjustment.`
- Config: `STATEMENT_OCR` (true), `STATEMENT_OCR_THREADS` (2), `STATEMENT_OCR_TIMEOUT` (120), `STATEMENT_OCR_RLIMIT_MB` (2048), `STATEMENT_OCR_CHILD` (unset; tests).
- Pins: `rapidocr-onnxruntime==1.4.4`, `pypdfium2==5.14.0`, `onnxruntime==1.31.0`; constraints file = `uv pip freeze` of the venv minus editable/local packages.
- Surgical changes; match existing style.

## Review Focus

1. A split identifier with wide gaps, U+3000 or OCR confusables reaching the parser or a snapshot — Task 3 normalisation tests, Task 4 guard tests, Task 7 `test_ocr_parser_stdin_canary` (with its leak control), Task 8 `test_export_masked_snapshot_canary`.
2. A legitimate row disappearing because a password candidate equals its year or amount — Task 4 `test_guard_never_masks_dates_or_amounts`.
3. A child that never stops writing or never exits — Task 6 `test_stdout_cap_stops_a_flooding_child`, `test_timeout_kills_the_session_group`.
4. The worker's secrets visible to the child — Task 6 `test_child_env_is_minimal` (env canary), `test_argv_sandboxed_no_network_minimal_binds`.
5. A broken host looping quietly — Task 7 `test_ocr_unavailable_exits_1_and_files_wait`, `test_poll_keeps_the_degraded_code`.

---

### Task 1: Dependencies, constraints file, configuration

**Files:** `requirements-worker.txt`; create `requirements-worker-constraints-aarch64-py313.txt`; `worker/config.py`; `README.md` (env table); test `tests/worker/test_config.py`.

**Interfaces:** produces `WorkerConfig.ocr_enabled: bool`, `ocr_threads: int`, `ocr_timeout_s: int`, `ocr_rlimit_mb: int`, `ocr_child: Path | None`.

- [ ] **Step 1: Pins, install, freeze**
```bash
cd /home/opc/workspace/home-hub-reconcile/services/accounting-service
printf 'rapidocr-onnxruntime==1.4.4\npypdfium2==5.14.0\nonnxruntime==1.31.0\n' >> requirements-worker.txt
/usr/local/bin/uv pip install --python .venv/bin/python -r requirements.txt -r requirements-worker.txt
( echo "# frozen on aarch64 / CPython 3.13 on 2026-10-14; regenerate with: uv pip freeze | grep -vE '^(-e |shared)' | grep == | sort"; \
  /usr/local/bin/uv pip freeze --python .venv/bin/python | grep -vE '^(-e |shared[-_]lib|shared_python_lib)' | grep -E '==' | sort ) > requirements-worker-constraints-aarch64-py313.txt
.venv/bin/python - <<'PY'
import re
pins = dict(re.findall(r"^([A-Za-z0-9_.-]+)==([^\s]+)$", open("requirements-worker.txt").read(), re.M))
cons = {k.lower().replace("_", "-"): v for k, v in re.findall(r"^([A-Za-z0-9_.-]+)==([^\s]+)$", open("requirements-worker-constraints-aarch64-py313.txt").read(), re.M)}
missing = {n: v for n, v in pins.items() if cons.get(n.lower().replace("_", "-")) != v}
print("constraints entries", len(cons), "missing/mismatched direct pins", missing)
assert not missing
PY
```
Expected: `missing/mismatched direct pins {}`.

- [ ] **Step 2: Failing tests** (append to `tests/worker/test_config.py`)
```python
def test_ocr_defaults():
    cfg = config.load({})
    assert (cfg.ocr_enabled, cfg.ocr_threads, cfg.ocr_timeout_s, cfg.ocr_rlimit_mb, cfg.ocr_child) == (True, 2, 120, 2048, None)


def test_ocr_overrides(tmp_path):
    cfg = config.load({"STATEMENT_OCR": "false", "STATEMENT_OCR_THREADS": "4", "STATEMENT_OCR_TIMEOUT": "30",
                       "STATEMENT_OCR_RLIMIT_MB": "512", "STATEMENT_OCR_CHILD": str(tmp_path / "fake")})
    assert (cfg.ocr_enabled, cfg.ocr_threads, cfg.ocr_timeout_s, cfg.ocr_rlimit_mb, cfg.ocr_child) == (False, 4, 30, 512, tmp_path / "fake")
```
- [ ] **Step 3: Run → FAIL** (`.venv/bin/python -m pytest tests/worker/test_config.py -q`).
- [ ] **Step 4: Implement** — fields after `account_map_file: Path | None`:
```python
    ocr_enabled: bool = True          # R5: OCR child for files without a text layer
    ocr_threads: int = 2
    ocr_timeout_s: int = 120
    ocr_rlimit_mb: int = 2048
    ocr_child: Path | None = None     # tests: a command replacing `python -B -m worker.ocr_child`
```
in `load()` after `account_map_file=…`:
```python
        ocr_enabled=_str(env, "STATEMENT_OCR", "true").lower() in TRUE,
        ocr_threads=int(_str(env, "STATEMENT_OCR_THREADS", "2")),
        ocr_timeout_s=int(_str(env, "STATEMENT_OCR_TIMEOUT", "120")),
        ocr_rlimit_mb=int(_str(env, "STATEMENT_OCR_RLIMIT_MB", "2048")),
        ocr_child=Path(child).expanduser() if (child := _opt(env, "STATEMENT_OCR_CHILD")) else None,
```
README rows after `STATEMENT_PARSER_TIMEOUT`:
```
| `STATEMENT_OCR` | `true` | sandboxed OCR child (RapidOCR, no network) for PDFs without a text layer (R5); `false` → needs_review/no_text_layer |
| `STATEMENT_OCR_THREADS` | `2` | onnxruntime intra-op threads in the OCR child |
| `STATEMENT_OCR_TIMEOUT` | `120` | wall-clock seconds per file for the OCR child, model load included |
| `STATEMENT_OCR_RLIMIT_MB` | `2048` | RLIMIT_AS of the OCR child (measured peak 1.2 GB) |
| `STATEMENT_OCR_CHILD` | unset | tests only: a command that replaces the python OCR child |
```
- [ ] **Step 5: Run → PASS; commit** `feat(worker): R5 OCR dependencies, constraints and config`.

---

### Task 2: Garbage trigger, bounded version, instruction rule, matching regressions (R5.1, R5.4, R5.5)

**Files:** `worker/pdf.py`, `worker/parse_schema.py`, `worker/pipeline.py:110`, `worker/verify.py:200`; create `worker/ocr_text.py` (stub: `LAYOUT_VERSION = "1"`) and `worker/ocr.py` (stub: `FINGERPRINT`); tests `tests/worker/test_pdf.py`, `tests/worker/test_parser.py`, `tests/unit/test_statement_matching.py`.

**Interfaces:** `pdf.is_font_garbage(text) -> bool`; `parse_schema.version(cli_version, model, ocr_fingerprint) -> str` (≤ 61 chars); `ocr.FINGERPRINT`.

- [ ] **Step 1: Failing tests**

`tests/worker/test_pdf.py`:
```python
def test_cid_garbage_is_no_text_layer():
    garbage = ["(cid:12)(cid:40) (cid:7)(cid:9)(cid:33) (cid:5) (cid:88)(cid:2) 1,234 (cid:4)"] * 30
    with pytest.raises(pdf.NoTextLayer):
        pdf.extract(make_pdf([garbage]), None)


def test_is_font_garbage_threshold():
    assert pdf.is_font_garbage("(cid:1) a b c d e f g h i") is True         # exactly 1 of 10
    assert pdf.is_font_garbage("(cid:1) a b c d e f g h i j") is False       # 1 of 11
    assert pdf.is_font_garbage("") is False
    assert pdf.is_font_garbage(" ".join(["MERCHANT(cid:7)"] + ["row"] * 20)) is False  # one cid-like token amid real text
```
`tests/worker/test_parser.py` — replace the version test body (near line 192):
```python
    v = parse_schema.version("2.1.296", "claude-sonnet-5-5", "1.4.4+5.14.0+layout1")
    assert v.startswith("claude-cli-2.1.296-claude-sonnet-5-5-") and len(v.split("-")[-1]) == 12 and len(v) <= 64
    assert parse_schema.version("2.1.296", "claude-sonnet-5-5", "1.4.5+5.14.0+layout1") != v


def test_version_is_bounded_for_long_config_and_distinct():
    long_model = "claude-sonnet-5-5-20261001-preview-extended-context-x" * 2  # 106 chars
    a = parse_schema.version("2.1.296", long_model, "f")
    b = parse_schema.version("2.1.296", long_model + "z", "f")
    assert len(a) <= 64 and len(b) <= 64 and a != b and a.startswith("claude-cli-2.1.296-")
    assert parse_schema.version("2.1.2960", "claude-sonnet-5-5", "f") != parse_schema.version("2.1.296", "claude-sonnet-5-5", "f")


def test_instruction_states_the_card_credit_rule():
    assert "never label a negative card row balance_adjustment" in parse_schema.INSTRUCTION and "分期調整" in parse_schema.INSTRUCTION
```
`tests/unit/test_statement_matching.py`:
```python
def test_refund_line_claims_a_refund_entry_only():
    res = run([L(1, date(2026, 9, 12), "200", kind="refund")], [E(60, date(2026, 9, 12), "200", kind="refund", refunds_entry_id=None)])
    assert [c.representation.rule for c in res.claims] == ["refund"]


def test_refund_line_does_not_claim_an_expense_of_the_same_amount():
    res = run([L(1, date(2026, 9, 12), "200", kind="refund")], [E(61, date(2026, 9, 12), "200", kind="expense")])
    assert res.claims == []
```
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement**

`worker/pdf.py` above `extract`:
```python
GARBAGE_TOKEN = "(cid:"
GARBAGE_SHARE = 0.10


def is_font_garbage(text: str) -> bool:
    """R5.1: a Type3 font without a ToUnicode map extracts as `(cid:NNN)` tokens; at least 10 % of the whitespace-separated
    tokens being such tokens means there is no usable text layer."""
    tokens = text.split()
    return bool(tokens) and sum(1 for t in tokens if GARBAGE_TOKEN in t) / len(tokens) >= GARBAGE_SHARE
```
after the `page1 < 200` raise:
```python
                if is_font_garbage(texts[0]):
                    raise NoTextLayer("page 1 is font garbage") from None
```
`worker/parse_schema.py`: append the sentence to `INSTRUCTION` as the last element (with a leading space); replace `version`:
```python
VERSION_MAX = 64  # RevisionIn.parser_version / statement_file.parser_version are String(64)


def version(cli_version: str, model: str, ocr_fingerprint: str) -> str:
    """One bounded version for every file (R5.4): the full CLI version, model id, instruction, schema and OCR
    fingerprint fold into a 12-hex digest behind a display prefix cut to 12+24 characters (max 61 in all), so no
    configuration can exceed the 64-character bound and truncated prefixes cannot collide."""
    digest = hashlib.sha256("\n".join([cli_version, model, INSTRUCTION, json.dumps(json_schema(), sort_keys=True),
                                       ocr_fingerprint]).encode()).hexdigest()[:12]
    out = f"claude-cli-{cli_version[:12]}-{model[:24]}-{digest}"
    assert len(out) <= VERSION_MAX
    return out
```
`worker/ocr_text.py`: `LAYOUT_VERSION = "1"` only (Task 3 fills the module). `worker/ocr.py` stub:
```python
"""R5.2: OCR child wrapper (stub: FINGERPRINT only; the Engine arrives with Task 6)."""
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
`pipeline.py:110` → `parse_schema.version(services.cli_version, cfg.parser_model, ocr_mod.FINGERPRINT)`; `verify.py:200` → `parse_schema.version(prs.cli_version() or "unknown", cfg.parser_model, ocr_mod.FINGERPRINT)`; both `from worker import ocr as ocr_mod`.

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/worker tests/unit/test_statement_matching.py -q` → PASS (the pipeline end-to-end test checks only the `claude-cli-2.1.296-` prefix). **Commit** `feat(worker): font-garbage trigger, bounded parser version, card credit-sign rule (R5.1/R5.4/R5.5)`.

---

### Task 3: Geometric layout and text normalisation (`worker/ocr_text.py`, R5.2a)

**Files:** `worker/ocr_text.py`; test `tests/worker/test_ocr_text.py` (new).

**Interfaces:** `LAYOUT_VERSION = "1"`, `Box = tuple[float, float, float, float, str]`, `layout(boxes) -> str` (rows by centre, left→right, padding `gap / char_w` clamped 1..12, `char_w = 0.6 × median height`), `normalise_line(line) -> str`, `normalise_text(text) -> str`.

- [ ] **Step 1: Failing tests** (`tests/worker/test_ocr_text.py`)
```python
from worker import ocr_text as t


def box(x, y, w, h, text):
    return (float(x), float(y), float(w), float(h), text)


def test_layout_rows_by_centre_and_padding():
    out = t.layout([box(10, 100, 60, 20, "2026/09/01"), box(400, 102, 80, 16, "1,234"), box(120, 100, 150, 20, "COFFEE"),
                    box(10, 140, 60, 28, "商店"), box(400, 146, 60, 16, "56")])
    lines = out.split("\n")
    assert lines[0].startswith("2026/09/01") and "COFFEE" in lines[0] and lines[0].endswith("1,234")
    assert lines[1].startswith("商店") and lines[1].endswith("56")
    gap = lines[0][lines[0].index("COFFEE") + 6:lines[0].index("1,234")]
    assert 2 <= len(gap) <= 12 and gap.strip() == ""
    assert t.layout([]) == ""


def test_normalise_collapses_whitespace_between_digit_tokens():
    assert t.normalise_text("4111    2222\t3333　　4444") == "4111 2222 3333 4444"
    assert t.normalise_text("帳號 012   345   678   901 餘額") == "帳號 012 345 678 901 餘額"


def test_normalise_confusables_only_in_digit_like_tokens():
    assert t.normalise_text("4lll 222O 333O 444O") == "4111 2220 3330 4440"
    assert t.normalise_text("Ollie lol 10") == "Ollie lol 10"       # fewer than 2 digits: not digit-like
    assert t.normalise_text("I0O1") == "1001"


def test_normalise_rejoins_any_split_id():
    assert t.normalise_text("身分證 A12 345 6789 末") == "身分證 A123456789 末"
    assert t.normalise_text("B12   3456789") == "B123456789"
    assert t.normalise_text("A12 345 67890") == "A12 345 67890"     # 11 chars: not an ID, untouched


def test_normalise_joins_short_cjk_runs_only():
    assert t.normalise_text("戶名 王 小 明") == "戶名 王小明"
    assert t.normalise_text("全聯福 利中心") == "全聯福 利中心"        # 6 chars joined: left alone


def test_normalise_leaves_dates_and_amounts_as_printed():
    assert t.normalise_text("2026/09/01   COFFEE   1,234") == "2026/09/01   COFFEE   1,234"
    assert t.normalise_text("20260901    1234") == "20260901 1234"   # the text-layer shape; the masker's date rule decides
```
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement `worker/ocr_text.py`**
```python
"""R5.2a: rows from OCR boxes (geometric) and identifier normalisation of OCR text (before masking).

The masker (§5.4) masks digit groups joined by ONE space or dash, literal names/IDs and birth patterns; OCR splits
identifiers across boxes and pads columns with several spaces, so `normalise_text` first gives OCR text the shape
text-layer statements already have. It rewrites only identifier-shaped tokens; merchant text, dates with separators
and amounts with thousands separators are untouched. LAYOUT_VERSION is part of the parser version: bump on change.
"""
from __future__ import annotations

import re

LAYOUT_VERSION = "1"
Box = tuple[float, float, float, float, str]  # x, y, w, h, text (pixels)
MAX_PAD = 12
_TOKEN = re.compile(r"([ \t　]*)([^ \t　]+)")
_DIGITS = re.compile(r"^\d+$")
_DIGIT_LIKE = re.compile(r"^[0-9OoIl|]{4,}$")
_ID = re.compile(r"^[A-Z][12]\d{8}$")
_ID_HEAD = re.compile(r"^[A-Z][12]\d{0,8}$")
_CJK = re.compile(r"^[一-鿿]+$")
_CONFUSABLE = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1"})


def char_width(boxes: list[Box]) -> float:
    heights = sorted(b[3] for b in boxes)
    return max((heights[len(heights) // 2] if heights else 1.0) * 0.6, 1.0)


def layout(boxes: list[Box]) -> str:
    """Rows by vertical centre (tolerance half the median height), boxes left to right, spaces proportional to the gap."""
    if not boxes:
        return ""
    heights = sorted(b[3] for b in boxes)
    tol = (heights[len(heights) // 2] or 1.0) * 0.5
    char_w = char_width(boxes)
    rows: list[list[Box]] = []
    centre = None
    for b in sorted(boxes, key=lambda b: (b[1] + b[3] / 2, b[0])):
        c = b[1] + b[3] / 2
        if rows and abs(c - centre) <= tol:
            rows[-1].append(b)
        else:
            rows.append([b])
            centre = c
    lines = []
    for row in rows:
        row.sort(key=lambda b: b[0])
        parts: list[str] = []
        prev_end = None
        for x, _y, w, _h, text in row:
            if prev_end is not None:
                parts.append(" " * max(1, min(MAX_PAD, int((x - prev_end) / char_w))))
            parts.append(text)
            prev_end = x + w
        lines.append("".join(parts))
    return "\n".join(lines)


def normalise_line(line: str) -> str:
    pairs = [(m.group(1), m.group(2)) for m in _TOKEN.finditer(line)]
    if not pairs:
        return line
    seps = [s for s, _ in pairs]
    toks = [t.translate(_CONFUSABLE) if _DIGIT_LIKE.match(t) and sum(c.isdigit() for c in t) >= 2 else t for _, t in pairs]  # (2)
    i = 0
    while i < len(toks):  # (3) a split ID, configured or not
        if _ID_HEAD.match(toks[i]):
            j, joined = i + 1, toks[i]
            while j < len(toks) and _DIGITS.match(toks[j]) and len(joined) < 10:
                joined += toks[j]
                j += 1
            if _ID.match(joined):
                toks[i:j] = [joined]
                del seps[i + 1:j]
                i += 1
                continue
        i += 1
    i = 0
    while i < len(toks):  # (4) short CJK runs (split holder names)
        if _CJK.match(toks[i]):
            j, joined = i + 1, toks[i]
            while j < len(toks) and _CJK.match(toks[j]) and len(joined) + len(toks[j]) <= 4:
                joined += toks[j]
                j += 1
            if j > i + 1:
                toks[i:j] = [joined]
                del seps[i + 1:j]
        i += 1
    out = []
    for k, tok in enumerate(toks):  # (1) one space between digit tokens; other separators as laid out
        sep = " " if k > 0 and _DIGITS.match(toks[k - 1]) and _DIGITS.match(tok) else seps[k]
        out.append(sep + tok)
    return "".join(out)


def normalise_text(text: str) -> str:
    return "\n".join(normalise_line(line) for line in text.split("\n"))
```
- [ ] **Step 4: Run → PASS; commit** `feat(worker): OCR layout and identifier normalisation (R5.2a)`.

---

### Task 4: Non-numeric line guard and `mask_ocr` (R5.2b)

**Files:** `worker/mask.py`; test `tests/worker/test_mask.py` (the module builds `mask.Masker(IDENT)` with `IDENT = passwords.Identity(id_number="A123456789", birth_date="19900101", holder_names=["王小明", "WANG XIAO MING"])`).

**Interfaces:** `Masker.guard_lines(text, extra_literals=()) -> str`; `Masker.mask_ocr(text, extra_literals=()) -> str`.

- [ ] **Step 1: Failing tests** (append)
```python
def test_guard_lines_masks_split_names_and_id():
    m = mask.Masker(IDENT)
    text = "MERCHANT 001        1,234\n持卡人 王 小 明\nWANG XIAO  MING here\nA12 3456789 some row"
    assert m.guard_lines(text).split("\n") == ["MERCHANT 001        1,234", "[LINE MASKED]", "[LINE MASKED]", "[LINE MASKED]"]


def test_guard_lines_alnum_password_only():
    m = mask.Masker(IDENT)
    assert m.guard_lines("pw ab 12 34 here", extra_literals=["ab1234"]) == "[LINE MASKED]"
    assert m.guard_lines("pw 01 23 45 67 here", extra_literals=["01234567"]) == "pw 01 23 45 67 here"   # numeric: the masker's job
    assert m.guard_lines("pw ab 12 here", extra_literals=["ab12"]) == "pw ab 12 here"                   # under 6 chars


def test_guard_never_masks_dates_or_amounts():
    m = mask.Masker(IDENT)
    text = "2026/09/01 COFFEE 120\n2026/09/02 BOOKS 12026\n20260903 TAXI 2026"
    assert m.guard_lines(text, extra_literals=["2026", "12026", "19900101"]) == text
    out = m.mask_ocr(text, extra_literals=["2026", "12026"])
    assert "2026/09/01 COFFEE 120" in out and "12026" in out and "20260903" in out


def test_mask_ocr_normalises_then_masks_then_guards():
    m = mask.Masker(IDENT)
    text = ("卡號 4111    2222　　3333    4444\n身分證 A12 345 6789\n戶名 王 小 明\n生日 1990 0101\n"
            "4lll 2222 3333 444O\nMERCHANT 001        1,234")
    lines = m.mask_ocr(text).split("\n")
    assert lines[0] == "卡號 [NUM…4444]" and lines[1] == "身分證 [ID]" and lines[2] == "戶名 [NAME]"
    assert lines[3] == "生日 [NUM…0101]" and lines[4] == "[NUM…4440]" and lines[5] == "MERCHANT 001        1,234"
    compact = "".join(lines).replace(" ", "")
    for secret in ("4111", "2222", "3333", "A123456789", "王小明", "19900101"):
        assert secret not in compact
```
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** (methods on `Masker`):
```python
    def _guard_literals(self, extra_literals=()) -> list[str]:
        """R5.2b: non-numeric identity literals with their whitespace removed (names may be printed with spaces)."""
        out = []
        if self.identity.id_number:
            out.append(self.identity.id_number)
        out += ["".join(n.split()) for n in self.names]
        out += [x for x in extra_literals if len(x) >= 6 and any(c.isalpha() for c in x)]
        return [x for x in out if len(x) >= 2]

    def guard_lines(self, s: str, extra_literals=()) -> str:
        """A line whose whitespace-stripped form still holds a non-numeric identity literal is replaced whole. Numeric
        literals are left to `text`, whose date and amount exclusions keep transaction rows intact."""
        literals = self._guard_literals(extra_literals)
        out = []
        for line in s.split("\n"):
            compact = "".join(line.split())
            out.append("[LINE MASKED]" if any(lit in compact for lit in literals) else line)
        return "\n".join(out)

    def mask_ocr(self, s: str, extra_literals=()) -> str:
        """OCR branch: normalise identifier shapes (R5.2a), mask, then guard (R5.2b)."""
        from worker import ocr_text  # noqa: PLC0415 — local import keeps mask importable on its own

        return self.guard_lines(self.text(ocr_text.normalise_text(s), extra_literals), extra_literals)
```
- [ ] **Step 4: Run** `tests/worker/test_mask.py` → PASS; **commit** `feat(worker): non-numeric line guard and mask_ocr (R5.2b)`.

---

### Task 5: The OCR child (`worker/ocr_child.py`, R5.2)

**Files:** create `worker/ocr_child.py`; test `tests/worker/test_ocr_child.py` (new).

**Interfaces:** `Unavailable`; `load_engine(threads)`; `apply_rlimits(header)`; `run(header, data, backend=None, render=None) -> dict`; `Reader(stream)` with `header()` and `pdf()`; `main(backend=None, stdin=None, stdout=None, reader=None) -> int`; constants `HEADER_MAX = 4096`, `CONFIDENCE_FLOOR = 0.80`, `BASE_AS_MB = 2048`, `BASE_CPU_S = 150`. Default `render(page, scale)` = `page.render(scale=scale, rev_byteorder=True).to_numpy()`.

- [ ] **Step 1: Failing tests**
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
    b, renders = backend_of(RAW), []
    out = ocr_child.run(HDR, make_pdf([["p"]] * 11), backend=b, render=lambda page, scale: renders.append(1))
    assert out == {"ok": False, "code": "too_large"} and b.calls == [] and renders == []


def test_pixel_bound_before_render():
    b, renders = backend_of(RAW), []

    def spy(page, scale):
        renders.append(1)
        return np.full((10, 10, 3), 255, dtype=np.uint8)
    out = ocr_child.run({**HDR, "max_pixels": 1000}, make_pdf([statement_lines()]), backend=b, render=spy)   # A4 at 3.0 ≈ 13 Mpx
    assert out == {"ok": False, "code": "too_large"} and renders == [] and b.calls == []
    assert ocr_child.run({**HDR, "max_pixels": 50_000_000}, make_pdf([statement_lines()]), backend=b, render=spy)["ok"] and renders == [1]


def test_malformed_bytes_and_backend_failure():
    assert ocr_child.run(HDR, b"%PDF-1.4 not really", backend=backend_of(RAW)) == {"ok": False, "code": "malformed"}

    def boom(img):
        raise RuntimeError("engine failed mid-page")
    assert ocr_child.run(HDR, make_pdf([statement_lines()]), backend=boom) == {"ok": False, "code": "malformed"}


def test_run_reports_unavailable_without_touching_the_pdf(monkeypatch):
    monkeypatch.setattr(ocr_child, "load_engine", lambda threads: (_ for _ in ()).throw(ocr_child.Unavailable()))
    assert ocr_child.run(HDR, b"garbage", backend=None) == {"ok": False, "code": "unavailable"}


class _RefusingReader(ocr_child.Reader):
    """Serves the header; reading the PDF is a test failure."""

    def __init__(self, header: bytes):
        super().__init__(io.BytesIO(header))
        self.pdf_reads = 0

    def pdf(self):
        self.pdf_reads += 1
        raise AssertionError("PDF bytes were read")


def test_main_readiness_precedes_the_pdf(monkeypatch):
    monkeypatch.setattr(ocr_child, "load_engine", lambda threads: (_ for _ in ()).throw(ocr_child.Unavailable()))
    monkeypatch.setattr(ocr_child, "apply_rlimits", lambda header: None)
    reader = _RefusingReader(json.dumps(HDR).encode() + b"\n")
    out = io.BytesIO()
    assert ocr_child.main(stdout=out, reader=reader) == 0
    assert json.loads(out.getvalue()) == {"ok": False, "code": "unavailable"} and reader.pdf_reads == 0


def test_main_protocol_roundtrip_in_a_subprocess():
    script = ("import sys, json\nfrom worker import ocr_child\n"
              "sys.exit(ocr_child.main(backend=lambda img: %r))\n" % (RAW,))
    proc = subprocess.run([sys.executable, "-c", script], input=json.dumps(HDR).encode() + b"\n" + make_pdf([statement_lines()]),
                          capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[:200]
    lines = proc.stdout.decode().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["ok"]


def test_limits_are_applied_before_the_pdf_in_a_subprocess():
    script = ("import sys, json, resource\nfrom worker import ocr_child\n"
              "def backend(img):\n    return %r\n"
              "class Probe(ocr_child.Reader):\n"
              "    def pdf(self):\n"
              "        print(json.dumps({'as': resource.getrlimit(resource.RLIMIT_AS)[0], 'cpu': resource.getrlimit(resource.RLIMIT_CPU)[0], 'core': resource.getrlimit(resource.RLIMIT_CORE)[0]}), file=sys.stderr)\n"
              "        return super().pdf()\n"
              "sys.exit(ocr_child.main(backend=backend, reader=Probe(sys.stdin.buffer)))\n" % (RAW,))
    hdr = {**HDR, "rlimit_as_mb": 777, "rlimit_cpu_s": 33}
    proc = subprocess.run([sys.executable, "-c", script], input=json.dumps(hdr).encode() + b"\n" + make_pdf([statement_lines()]),
                          capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[:300]
    seen = json.loads(proc.stderr.decode().splitlines()[-1])
    assert seen == {"as": 777 * 1024 * 1024, "cpu": 33, "core": 0}
```
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Verify the RapidOCR constructor** (signature + `config.yaml` thread keys, as before); record the finding in the module docstring.
- [ ] **Step 4: Implement `worker/ocr_child.py`**
```python
"""R5.2: the OCR child. `python -B -m worker.ocr_child` reads one JSON header line then raw PDF bytes on stdin and
prints exactly one JSON line. The parent runs it under bwrap with no network and a minimal environment.

Order: import-time rlimits (core 0, AS/CPU baselines) → header → header limits → readiness (imports, engine,
32x32 self-test; failure = "unavailable", printed BEFORE any PDF byte is read) → PDF → page bound → per page: pixel
bound before allocation, render, recognise, rows. Nothing is written to disk; nothing but the result line is printed.
Thread setting verified on this host: <finding>.
"""
from __future__ import annotations

import json
import math
import sys
from io import BytesIO

from worker import ocr_text

HEADER_MAX = 4096
CONFIDENCE_FLOOR = 0.80
SELF_TEST_SIDE = 32
BASE_AS_MB = 2048
BASE_CPU_S = 150


class Unavailable(RuntimeError):
    """Imports, engine construction or the self-test failed."""


def apply_rlimits(header: dict) -> None:
    import resource  # noqa: PLC0415

    as_bytes = int(header.get("rlimit_as_mb", BASE_AS_MB)) * 1024 * 1024
    cpu = int(header.get("rlimit_cpu_s", BASE_CPU_S))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    for res, value in ((resource.RLIMIT_AS, as_bytes), (resource.RLIMIT_CPU, cpu)):
        _soft, hard = resource.getrlimit(res)
        cap = value if hard == resource.RLIM_INFINITY else min(value, hard)
        resource.setrlimit(res, (cap, hard))


def load_engine(threads: int):
    """RapidOCR with the wheel's bundled models; a callable img -> [[quad, text, score], ...]."""
    try:
        import numpy as np  # noqa: PLC0415
        import pypdfium2  # noqa: F401, PLC0415 — the renderer is part of readiness
        from rapidocr_onnxruntime import RapidOCR  # noqa: PLC0415

        engine = RapidOCR(intra_op_num_threads=threads, inter_op_num_threads=1)
        engine(np.full((SELF_TEST_SIDE, SELF_TEST_SIDE, 3), 255, dtype=np.uint8))
    except Exception:  # noqa: BLE001 — the library text never leaves the child
        raise Unavailable() from None

    def backend(img):
        result, _elapse = engine(img)
        return result or []
    return backend


def _render(page, scale: float):
    return page.render(scale=scale, rev_byteorder=True).to_numpy()


def run(header: dict, data: bytes, backend=None, render=None) -> dict:
    try:
        backend = backend if backend is not None else load_engine(int(header.get("threads", 2)))
        import pypdfium2 as pdfium  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return {"ok": False, "code": "unavailable"}
    render = render or _render
    scale = float(header.get("scale", 3.0))
    max_pages = int(header.get("max_pages", 10))
    max_pixels = int(header.get("max_pixels", 30_000_000))
    try:
        doc = pdfium.PdfDocument(BytesIO(data), password=header.get("password"))
        npages = len(doc)
    except Exception:  # noqa: BLE001
        return {"ok": False, "code": "malformed"}
    if npages < 1 or npages > max_pages:
        return {"ok": False, "code": "too_large"}
    texts: list[str] = []
    scores: list[float] = []
    boxes_total = empty_pages = 0
    try:
        for page in doc:
            w, h = page.get_size()
            if not (math.isfinite(w) and math.isfinite(h)) or w <= 0 or h <= 0 or math.ceil(w * scale) * math.ceil(h * scale) > max_pixels:
                return {"ok": False, "code": "too_large"}
            img = render(page, scale)
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


class Reader:
    """Stdin in two steps so readiness runs between them (tests subclass `pdf`)."""

    def __init__(self, stream):
        self.stream = stream

    def header(self) -> dict | None:
        try:
            return json.loads(self.stream.readline(HEADER_MAX))
        except Exception:  # noqa: BLE001
            return None

    def pdf(self) -> bytes:
        return self.stream.read()


def main(backend=None, stdin=None, stdout=None, reader: Reader | None = None) -> int:
    reader = reader or Reader(stdin if stdin is not None else sys.stdin.buffer)
    out = stdout if stdout is not None else sys.stdout.buffer

    def emit(result: dict) -> int:
        out.write((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"))
        out.flush()
        return 0

    header = reader.header()
    if not isinstance(header, dict):
        return emit({"ok": False, "code": "malformed"})
    apply_rlimits(header)
    try:
        backend = backend if backend is not None else load_engine(int(header.get("threads", 2)))
        import pypdfium2  # noqa: F401, PLC0415
    except Exception:  # noqa: BLE001
        return emit({"ok": False, "code": "unavailable"})
    data = reader.pdf()
    return emit(run(header, data, backend=backend))


if __name__ == "__main__":
    apply_rlimits({})  # baseline before anything is read
    sys.exit(main())
```
- [ ] **Step 5: Run** `tests/worker/test_ocr_child.py` → PASS. **Commit** `feat(worker): OCR child — readiness before the PDF, bounds, render, recognise (R5.2)`.

---

### Task 6: The parent `worker/ocr.py` (R5.2, R5.8)

**Files:** `worker/ocr.py` (replace the stub, keep `FINGERPRINT`/`_dist_version`); create `tests/worker/fake_ocr_child.py`; test `tests/worker/test_ocr.py` (new).

**Interfaces:** `OCRUnavailable`, `OCRTimeout`, `OCRLowConfidence`; `OcrResult(extracted, mean_confidence, boxes, empty_pages)`; `Engine(cfg)` with `argv()`, `env()`, `read(data, password)`, `reset()`, `starts`, `last_pgid`, `last_stdout_bytes`; `service_dir()`; constants `SCALE=3.0`, `MAX_PAGES=10`, `MAX_PIXELS=30_000_000`, `RLIMIT_CPU_S=150`, `STDOUT_CAP=2*1024*1024`, `KILL_WAIT_S=10`, `RESOURCE_SIGNALS={SIGKILL, SIGXCPU}`.

- [ ] **Step 1: The fake child** (`tests/worker/fake_ocr_child.py`)
```python
"""Stand-ins for `python -B -m worker.ocr_child` used by the parent, pipeline and verify tests."""
import json
import shlex
from pathlib import Path


def write(path: Path, response, *, sleep: float = 0, exit_code: int = 0, flood: bool = False, grandchild: bool = False,
          dump_env: bool = False, record_header_to: Path | None = None, signal: int | None = None) -> Path:
    lines = ["#!/bin/sh"]
    if record_header_to is not None:
        lines.append(f"head -1 > {shlex.quote(str(record_header_to))}")
    lines.append("cat >/dev/null")
    if grandchild:
        lines.append("sleep 300 &")
    if sleep:
        lines.append(f"sleep {sleep}")
    if flood:
        lines.append("yes xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")  # never stops
    elif dump_env:
        lines.append("printf '{\"ok\": true, \"pages\": 1, \"empty_pages\": 0, \"boxes\": 1, \"mean_confidence\": 0.9, \"text\": \"'; "
                     "env | tr '\\n' ';' | sed 's/\"/_/g; s/\\\\/_/g'; printf '\"}\\n'")
    elif response is not None:
        body = response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
        lines.append(f"printf '%s\\n' {shlex.quote(body)}")
    if signal is not None:
        lines.append(f"kill -{signal} $$")
    lines.append(f"exit {exit_code}")
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o700)
    return path


def ok(text="MERCHANT 001        1,234", pages=1, boxes=2, mean_confidence=0.93, empty_pages=0) -> dict:
    return {"ok": True, "pages": pages, "empty_pages": empty_pages, "boxes": boxes, "mean_confidence": mean_confidence, "text": text}
```
- [ ] **Step 2: Failing tests** (`tests/worker/test_ocr.py`)
```python
import json
import os
import signal
import subprocess
import sys
import time

import pytest

from tests.worker import fake_ocr_child
from worker import config, ocr, pdf


def cfg_with(tmp_path, response=None, name="child.sh", **kw):
    child = fake_ocr_child.write(tmp_path / name, response, **kw)
    return config.load({"STATEMENT_OCR_CHILD": str(child), "STATEMENT_PARSER_SANDBOX": "false", "STATEMENT_OCR_TIMEOUT": "2"})


def test_read_ok_maps_to_result(tmp_path):
    out = ocr.Engine(cfg_with(tmp_path, fake_ocr_child.ok(text="A\fB", pages=2, boxes=5, empty_pages=1))).read(b"%PDF", "pw")
    assert out.extracted.text == "A\fB" and out.extracted.pages == 2 and out.extracted.tables == [] and out.extracted.text_chars_page1 == 1
    assert out.boxes == 5 and out.empty_pages == 1 and abs(out.mean_confidence - 0.93) < 1e-9


@pytest.mark.parametrize("code,exc", [("unavailable", ocr.OCRUnavailable), ("too_large", pdf.TooLarge), ("malformed", pdf.Malformed), ("low_confidence", ocr.OCRLowConfidence)])
def test_read_maps_error_codes(tmp_path, code, exc):
    with pytest.raises(exc):
        ocr.Engine(cfg_with(tmp_path, {"ok": False, "code": code})).read(b"%PDF", None)


@pytest.mark.parametrize("response", [
    {"ok": True, "pages": "SECRET-PAGE", "text": "x"},
    {"ok": True, "pages": 1, "empty_pages": 0, "boxes": 1, "mean_confidence": float("nan"), "text": "x"},
    {"ok": True, "pages": 0, "empty_pages": 0, "boxes": 0, "mean_confidence": 0.9, "text": "x"},
    {"ok": True, "pages": 1, "empty_pages": 2, "boxes": 1, "mean_confidence": 0.9, "text": "x"},
    {"ok": False, "code": "weird"}, "[1, 2]", "not json",
    '{"ok": true, "pages": 1, "empty_pages": 0, "boxes": 1, "mean_confidence": 0.9, "text": "x"}\n{"second": "line"}'])
def test_invalid_responses_are_malformed_without_content(tmp_path, response):
    try:
        ocr.Engine(cfg_with(tmp_path, response)).read(b"%PDF", None)
    except pdf.Malformed as exc:
        assert "SECRET" not in str(exc) and exc.__cause__ is None and exc.__context__ is None
    else:
        pytest.fail("accepted an invalid response")


def test_header_and_bytes_reach_the_child(tmp_path):
    hdr = tmp_path / "hdr"
    child = fake_ocr_child.write(tmp_path / "rec.sh", fake_ocr_child.ok(), record_header_to=hdr)
    cfg = config.load({"STATEMENT_OCR_CHILD": str(child), "STATEMENT_PARSER_SANDBOX": "false", "STATEMENT_OCR_THREADS": "3", "STATEMENT_OCR_RLIMIT_MB": "777"})
    ocr.Engine(cfg).read(b"%PDF-bytes", "secret")
    assert json.loads(hdr.read_text()) == {"password": "secret", "scale": 3.0, "max_pages": 10, "max_pixels": 30_000_000, "threads": 3, "rlimit_as_mb": 777, "rlimit_cpu_s": 150}


def _session_alive(pgid: int) -> bool:
    return bool(subprocess.run(["ps", "-o", "pid=", "-g", str(pgid)], capture_output=True, text=True).stdout.strip())


def test_timeout_kills_the_session_group(tmp_path):
    engine = ocr.Engine(cfg_with(tmp_path, fake_ocr_child.ok(), sleep=30, grandchild=True))
    t0 = time.monotonic()
    with pytest.raises(ocr.OCRTimeout) as info:
        engine.read(b"%PDF", None)
    assert time.monotonic() - t0 < 15 and info.value.__context__ is None
    assert engine.last_pgid and not _session_alive(engine.last_pgid)


def test_stdout_cap_stops_a_flooding_child(tmp_path):
    engine = ocr.Engine(cfg_with(tmp_path, None, flood=True))
    t0 = time.monotonic()
    with pytest.raises(pdf.Malformed):
        engine.read(b"%PDF", None)
    assert time.monotonic() - t0 < 2 and engine.last_stdout_bytes <= ocr.STDOUT_CAP + 65536
    assert not _session_alive(engine.last_pgid)


def test_exit_classification(tmp_path):
    with pytest.raises(pdf.Malformed):   # non-zero exit (python exception)
        ocr.Engine(cfg_with(tmp_path, None, exit_code=1)).read(b"%PDF", None)
    with pytest.raises(pdf.Malformed):   # crash signal
        ocr.Engine(cfg_with(tmp_path, None, signal=signal.SIGSEGV)).read(b"%PDF", None)
    with pytest.raises(ocr.OCRTimeout):  # resource signal (CPU limit) → retry later, not a parse failure
        ocr.Engine(cfg_with(tmp_path, None, signal=signal.SIGXCPU)).read(b"%PDF", None)
    with pytest.raises(ocr.OCRTimeout):  # bwrap reports a signalled child as 128+n
        ocr.Engine(cfg_with(tmp_path, None, exit_code=128 + signal.SIGKILL)).read(b"%PDF", None)


def test_unavailable_is_cached_for_the_run_and_reset_at_the_boundary(tmp_path):
    engine = ocr.Engine(cfg_with(tmp_path, {"ok": False, "code": "unavailable"}))
    with pytest.raises(ocr.OCRUnavailable):
        engine.read(b"%PDF", None)
    fake_ocr_child.write(tmp_path / "child.sh", fake_ocr_child.ok())
    with pytest.raises(ocr.OCRUnavailable):
        engine.read(b"%PDF", None)
    assert engine.starts == 1
    engine.reset()
    assert engine.read(b"%PDF", None).extracted.pages == 1 and engine.starts == 2


def test_child_env_is_minimal(tmp_path, monkeypatch):
    monkeypatch.setenv("STATEMENT_VERIFY_DB_URL", "postgresql://u:SECRET-DB-PASSWORD@h/db")
    monkeypatch.setenv("SOME_TOKEN", "SECRET-TOKEN")
    out = ocr.Engine(cfg_with(tmp_path, None, dump_env=True)).read(b"%PDF", None)
    assert "SECRET-DB-PASSWORD" not in out.extracted.text and "SECRET-TOKEN" not in out.extracted.text
    env = dict(kv.split("=", 1) for kv in out.extracted.text.split(";") if "=" in kv)
    assert set(env) <= {"PATH", "HOME", "LANG", "PYTHONDONTWRITEBYTECODE", "PYTHONPATH", "PWD", "SHLVL", "_", "OLDPWD"}
    assert env["HOME"] == "/tmp"


def test_argv_sandboxed_no_network_minimal_binds(tmp_path):
    cfg = config.load({"STATEMENT_PARSER_SANDBOX": "true", "STATEMENT_STATE_DIR": str(tmp_path / "state"), "STATEMENT_PARSER_CONFIG_DIR": str(tmp_path / "cfg")})
    argv = ocr.Engine(cfg).argv()
    assert argv[0] == "bwrap" and "--unshare-all" in argv and "--share-net" not in argv
    for flag in ("--die-with-parent", "--new-session", "--cap-drop", "--tmpfs", "--proc", "--dev"):
        assert flag in argv
    binds = [(argv[i + 1], argv[i + 2]) for i, a in enumerate(argv) if a == "--ro-bind"]
    sources = [s for s, _ in binds]
    assert "/usr" in sources and os.path.realpath(sys.prefix) in sources and os.path.realpath(sys.base_prefix) in sources
    assert (str(ocr.service_dir() / "worker"), "/work/worker") in binds
    for forbidden in (str(tmp_path), os.path.expanduser("~"), str(ocr.service_dir()), "/etc"):
        assert forbidden not in sources
    assert "--bind" not in argv and argv[-3:] == ["-B", "-m", "worker.ocr_child"]


def test_argv_unsandboxed_and_override(tmp_path):
    assert ocr.Engine(config.load({"STATEMENT_PARSER_SANDBOX": "false"})).argv() == [sys.executable, "-B", "-m", "worker.ocr_child"]
    assert ocr.Engine(cfg_with(tmp_path, fake_ocr_child.ok())).argv()[0].endswith("child.sh")


def test_env_sandboxed_and_unsandboxed():
    sb = ocr.Engine(config.load({"STATEMENT_PARSER_SANDBOX": "true"})).env()
    plain = ocr.Engine(config.load({"STATEMENT_PARSER_SANDBOX": "false"})).env()
    assert sb == {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": "/work"}
    assert plain == {**sb, "PYTHONPATH": str(ocr.service_dir())}


def test_fingerprint_shape():
    assert ocr.FINGERPRINT.count("+") == 2 and ocr.FINGERPRINT.endswith("layout1")
```
- [ ] **Step 3: Run → FAIL.**
- [ ] **Step 4: Implement `worker/ocr.py`**
```python
"""R5.2/R5.8: parent side of OCR. Starts the child with a minimal environment (never the worker's own: it holds the
verify DB URL and other configuration), under bwrap with no network and read-only binds of the runtime only; writes
the header and the PDF from a thread; reads stdout through selectors, counting bytes (over the cap → kill); enforces
the wall-clock deadline by killing the child's session group and reaping it; validates the single result line; maps
exit status and result codes to the pipeline's outcomes (worker/ocr_child.py documents the protocol).

Every error crossing this boundary is built outside any active exception with a fixed phrase: no child output,
no stdin, no library text. `OCRUnavailable` is cached until `reset()` (the pipeline calls it at each run boundary).
"""
from __future__ import annotations

import json
import math
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
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
KILL_WAIT_S = 10
RESOURCE_SIGNALS = {int(signal.SIGKILL), int(signal.SIGXCPU)}
CODES = {"unavailable", "too_large", "malformed", "low_confidence"}


def _dist_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "0"


FINGERPRINT = f"{_dist_version('rapidocr-onnxruntime')}+{_dist_version('pypdfium2')}+layout{LAYOUT_VERSION}"


class OCRUnavailable(RuntimeError):
    """The child could not import, build or self-test the engine (before reading the PDF)."""


class OCRTimeout(RuntimeError):
    """The deadline fired or a resource limit killed the child; retry at the next parser version."""


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
    for path in ("/usr", "/lib", "/lib64", os.path.realpath(sys.base_prefix), os.path.realpath(sys.prefix)):
        if os.path.exists(path) and path not in binds:
            binds += ["--ro-bind", path, path]
    binds += ["--ro-bind", str(service_dir() / "worker"), "/work/worker"]
    return ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL", *binds,
            "--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev", "--chdir", "/work", "--"]


class Engine:
    def __init__(self, cfg: WorkerConfig):
        self.cfg = cfg
        self.starts = 0
        self.last_pgid: int | None = None
        self.last_stdout_bytes = 0
        self._unavailable = False

    def reset(self) -> None:
        """Run boundary: a host that was broken may have been fixed between runs."""
        self._unavailable = False

    def argv(self) -> list[str]:
        if self.cfg.ocr_child is not None:
            return [str(self.cfg.ocr_child)]
        child = [sys.executable, "-B", "-m", "worker.ocr_child"]
        return (_sandbox_prefix() + child) if self.cfg.parser_sandbox else child

    def env(self) -> dict[str, str]:
        return {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": "/work" if self.cfg.parser_sandbox else str(service_dir())}

    def _header(self, password: str | None) -> bytes:
        return (json.dumps({"password": password, "scale": SCALE, "max_pages": MAX_PAGES, "max_pixels": MAX_PIXELS,
                            "threads": self.cfg.ocr_threads, "rlimit_as_mb": self.cfg.ocr_rlimit_mb,
                            "rlimit_cpu_s": RLIMIT_CPU_S}) + "\n").encode()

    def read(self, data: bytes, password: str | None) -> OcrResult:
        if self._unavailable:
            raise OCRUnavailable("ocr unavailable")
        self.starts += 1
        outcome, payload = self._exchange(self._header(password) + data)
        # every raise below happens with no active exception, so __context__ stays None
        if outcome == "timeout":
            raise OCRTimeout("ocr timed out")
        if outcome == "malformed":
            raise pdf.Malformed("malformed")
        if outcome == "start_failed":
            self._unavailable = True
            raise OCRUnavailable("ocr unavailable")
        result = _validate(payload)
        if result is None:
            raise pdf.Malformed("malformed")
        if result["ok"]:
            text = result["text"]
            page1 = len(text.split("\f")[0].strip()) if text else 0
            return OcrResult(pdf.Extracted(text, [], result["pages"], page1), result["mean_confidence"], result["boxes"],
                             result["empty_pages"])
        code = result["code"]
        if code == "unavailable":
            self._unavailable = True
            raise OCRUnavailable("ocr unavailable")
        if code == "too_large":
            raise pdf.TooLarge("too large")
        if code == "low_confidence":
            raise OCRLowConfidence("low confidence")
        raise pdf.Malformed("malformed")

    def _exchange(self, stdin: bytes) -> tuple[str, bytes]:
        """(outcome, stdout) with outcome in ok | timeout | malformed | start_failed. Never raises."""
        try:
            proc = subprocess.Popen(self.argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    env=self.env(), start_new_session=True)
        except OSError:
            return "start_failed", b""
        self.last_pgid = proc.pid
        out = bytearray()
        deadline = time.monotonic() + self.cfg.ocr_timeout_s
        writer = threading.Thread(target=_write_stdin, args=(proc, stdin), daemon=True)
        writer.start()
        sel = selectors.DefaultSelector()
        sel.register(proc.stdout, selectors.EVENT_READ)
        outcome = "ok"
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    outcome = "timeout"
                    break
                if not sel.select(timeout=min(remaining, 0.25)):
                    continue
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk:
                    break
                out.extend(chunk)
                if len(out) > STDOUT_CAP:
                    outcome = "malformed"
                    break
        finally:
            sel.close()
            self.last_stdout_bytes = len(out)
        if outcome != "ok":
            _kill(proc)
        else:
            try:
                proc.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                _kill(proc)
                outcome = "timeout"
        _close(proc)
        writer.join(timeout=1)
        if outcome != "ok":
            return outcome, b""
        rc = proc.returncode
        if rc != 0:
            sig = -rc if rc < 0 else (rc - 128 if rc > 128 else None)
            return ("timeout" if sig in RESOURCE_SIGNALS else "malformed"), b""
        return "ok", bytes(out)


def _write_stdin(proc: subprocess.Popen, data: bytes) -> None:
    try:
        proc.stdin.write(data)
        proc.stdin.close()
    except (BrokenPipeError, OSError, ValueError):
        pass  # the child may exit early (unavailable) without reading the PDF


def _kill(proc: subprocess.Popen) -> None:
    """SIGKILL the child's session (bwrap and everything under it), then reap with a bound."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=KILL_WAIT_S)
    except subprocess.TimeoutExpired:
        pass


def _close(proc: subprocess.Popen) -> None:
    for pipe in (proc.stdout, proc.stdin):
        try:
            pipe.close()
        except (OSError, ValueError):
            pass


def _validate(payload: bytes) -> dict | None:
    """Exactly one JSON object line with the expected fields, types and ranges; otherwise None."""
    try:
        lines = [l for l in payload.decode("utf-8").split("\n") if l != ""]
    except UnicodeDecodeError:
        return None
    if len(lines) != 1:
        return None
    try:
        obj = json.loads(lines[0])
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get("ok") is True:
        pages, empty, boxes, conf, text = (obj.get(k) for k in ("pages", "empty_pages", "boxes", "mean_confidence", "text"))
        if not all(type(v) is int for v in (pages, empty, boxes)) or not (1 <= pages <= MAX_PAGES) or not (0 <= empty <= pages) or boxes < 0:
            return None
        if type(conf) not in (int, float) or not math.isfinite(conf) or not (0.0 <= conf <= 1.0) or not isinstance(text, str):
            return None
        return {"ok": True, "pages": pages, "empty_pages": empty, "boxes": boxes, "mean_confidence": float(conf), "text": text}
    if obj.get("ok") is False and obj.get("code") in CODES:
        return {"ok": False, "code": obj["code"]}
    return None
```
- [ ] **Step 5: Run** `tests/worker/test_ocr.py tests/worker/test_parser.py` → PASS (if `ps -g` is unavailable, use `ps -eo pgid=,pid=` and filter). **Commit** `feat(worker): OCR parent — minimal env, sandboxed child, bounded streaming, deadline kill, validated protocol (R5.2/R5.8)`.

---

### Task 7: Pipeline OCR branch, degraded exit, poll aggregation (R5.3)

**Files:** `worker/pipeline.py` (`Services`, `build`, the per-run entry `_execute`, `process_file`, `revision_body`, `poll`), `worker/cli.py` (exit rule); test `tests/worker/test_pipeline.py`.

**Interfaces:** `Services.ocr: ocr_mod.Engine | None`; `revision_body(..., parser="claude-cli", extra_note=None)`; `poll()` result `errors` = union of the executed runs' codes; `cli._bad(summary) -> bool`.

- [ ] **Step 1: Failing tests** (append; `json`, `pytest`, `text`, `encrypt`, `make_pdf`, `statement_lines` are imported at the top of the file)
```python
import dataclasses

from tests.worker import fake_ocr_child


def _ocr_world(world, tmp_path, response=None, pdf_pages=(["short"],), name="ocr-child.sh", **kw):
    """Drive holds a PDF without a text layer; the OCR child is a fake script."""
    services, drive_files, cfg = world
    drive_files["id-1"] = ("信用卡/國泰世華/2026-09_國泰世華.pdf", encrypt(make_pdf(list(pdf_pages)), "A123456789"))
    child = fake_ocr_child.write(tmp_path / name, response, **kw)
    cfg = dataclasses.replace(cfg, ocr_child=child, ocr_timeout_s=2)
    services.cfg = cfg
    from worker import ocr
    services.ocr = ocr.Engine(cfg)
    return services, drive_files, cfg


RAW_OCR_TEXT = ("\n".join(statement_lines()) + "\n2026/09/01   COFFEE   1,234\n卡號 4111    2222　　3333    4444"
                "\n持卡人 王 小 明\nWANG XIAO  MING\n身分證 A12 345 6789\n4lll 2222 3333 444O")


def _record_parser(services):
    seen = []
    real = services.parser.parse

    def parse(masked):
        seen.append(masked)
        return real(masked)
    services.parser.parse = parse
    return seen


def test_ocr_path_parses_and_records_provenance(world, tmp_path, db_session):
    services, _, _ = _ocr_world(world, tmp_path, fake_ocr_child.ok(text=RAW_OCR_TEXT, boxes=42))
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["parsed"] == 1 and services.ocr.starts == 1
    assert db_session.execute(text("select status, has_text_layer, text_chars from statement_file")).one() == ("parsed", False, len(RAW_OCR_TEXT))
    rev = db_session.execute(text("select parser, raw from statement_revision")).one()
    assert rev[0] == "claude-cli+ocr" and "ocr: mean_confidence=0.93 boxes=42 empty_pages=0 scale=3.0" in rev[1]["notes"]


def test_ocr_parser_stdin_canary(world, tmp_path):
    services, _, _ = _ocr_world(world, tmp_path, fake_ocr_child.ok(text=RAW_OCR_TEXT))
    seen = _record_parser(services)
    pipeline.run(services, trigger="owner_cli")
    assert len(seen) == 1
    compact = "".join(seen[0].split())
    for secret in ("4111", "2222", "3333", "王小明", "WANGXIAOMING", "A123456789", "4111222233334440"):
        assert secret not in compact, secret
    assert "2026/09/01   COFFEE   1,234" in seen[0] and "MERCHANT 001" in seen[0]
    leaky = "".join(services.masker.text(RAW_OCR_TEXT).split())  # control: the plain masker leaks on the same text
    assert "4111" in leaky and "王小明" in leaky


def test_text_layer_file_makes_zero_ocr_calls(world):
    services, _, cfg = world
    from worker import ocr
    services.ocr = ocr.Engine(cfg)
    assert pipeline.run(services, trigger="owner_cli")["parsed"] == 1 and services.ocr.starts == 0


def test_password_reaches_the_ocr_child(world, tmp_path):
    hdr = tmp_path / "hdr"
    services, _, _ = _ocr_world(world, tmp_path, fake_ocr_child.ok(), name="recorder.sh", record_header_to=hdr)
    pipeline.run(services, trigger="owner_cli")
    assert json.loads(hdr.read_text())["password"] == "A123456789"


def test_ocr_disabled_keeps_todays_behaviour(world, tmp_path, db_session):
    services, _, _ = _ocr_world(world, tmp_path, fake_ocr_child.ok())
    services.ocr = None
    assert pipeline.run(services, trigger="owner_cli")["needs_review"] == 1
    assert _file_rows(db_session) == [("needs_review", "no_text_layer")]


@pytest.mark.parametrize("response,kw,expected", [
    ({"ok": False, "code": "low_confidence"}, {}, ("needs_review", "no_text_layer")),
    ({"ok": False, "code": "too_large"}, {}, ("needs_review", "too_large")),
    ({"ok": False, "code": "malformed"}, {}, ("failed", "parse")),
    (None, {"exit_code": 139}, ("failed", "parse")),
    (fake_ocr_child.ok(), {"sleep": 30}, ("needs_review", "no_text_layer")),
])
def test_ocr_outcomes(world, tmp_path, db_session, response, kw, expected):
    services, _, _ = _ocr_world(world, tmp_path, response, **kw)
    pipeline.run(services, trigger="owner_cli")
    assert _file_rows(db_session) == [expected]
    if expected[0] == "needs_review":
        assert db_session.execute(text("select has_text_layer from statement_file")).scalar_one() is False


def test_ocr_timeout_then_next_file_runs(world, tmp_path, db_session):
    services, drive_files, _ = _ocr_world(world, tmp_path, fake_ocr_child.ok(), sleep=30)
    drive_files["id-2"] = ("信用卡/國泰世華/2026-08_國泰世華.pdf", encrypt(make_pdf([statement_lines(26)]), "A123456789"))
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["needs_review"] == 1 and summary["parsed"] == 1


def test_ocr_unavailable_exits_1_and_files_wait(world, tmp_path, db_session, monkeypatch):
    services, drive_files, cfg = _ocr_world(world, tmp_path, {"ok": False, "code": "unavailable"})
    drive_files["id-2"] = ("信用卡/國泰世華/2026-08_國泰世華.pdf", encrypt(make_pdf([["tiny"]]), "A123456789"))
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["errors"] == ["ocr_unavailable"] and summary["skipped"] == 2 and summary["failed"] == 0 and services.ocr.starts == 1
    assert _file_rows(db_session) == [("new", None), ("new", None)]
    assert db_session.execute(text("select status from ingest_run order by id desc limit 1")).scalar_one() == "done"
    from worker import cli
    assert cli._bad(summary) is True
    monkeypatch.setattr(pipeline, "build", lambda c, client, runner: services)
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: summary)
    for key, value in (("STATEMENT_API_TOKEN_FILE", cfg.api_token_file), ("STATEMENT_PASSWORD_FILE", cfg.password_file),
                       ("STATEMENT_STATE_DIR", cfg.state_dir), ("STATEMENT_API_URL", cfg.api_url)):
        monkeypatch.setenv(key, str(value))
    assert cli.main(["run"]) == 1
    fake_ocr_child.write(tmp_path / "ocr-child.sh", fake_ocr_child.ok())  # host fixed: the next run resets the cache
    assert pipeline.run(services, trigger="owner_cli")["parsed"] == 2 and services.ocr.starts == 3


def test_ocr_unavailable_leaves_an_existing_needs_review_row_alone(world, tmp_path, db_session):
    services, _, cfg = _ocr_world(world, tmp_path, {"ok": False, "code": "low_confidence"})
    pipeline.run(services, trigger="owner_cli")
    assert _file_rows(db_session) == [("needs_review", "no_text_layer")]
    db_session.execute(text("update statement_file set parser_version='old'"))
    db_session.commit()
    fake_ocr_child.write(tmp_path / "ocr-child.sh", {"ok": False, "code": "unavailable"})
    pipeline.run(services, trigger="owner_cli")
    assert _file_rows(db_session) == [("needs_review", "no_text_layer")]


def test_poll_keeps_the_degraded_code(world, tmp_path, db_session, client, monkeypatch):
    """A degraded enqueued run followed by a clean one: poll's result still carries ocr_unavailable."""
    services, drive_files, cfg = _ocr_world(world, tmp_path, {"ok": False, "code": "unavailable"})
    for _ in range(2):
        r = client.post("/statements/ingest-runs", headers={"Authorization": "Bearer spa-token"})
        assert r.status_code in (200, 201, 202), r.text
    calls = []
    real = pipeline._execute

    def execute(services_, summary, lease, trigger, mode):
        calls.append(1)
        if len(calls) == 2:  # second run: host fixed, Drive now text-layer only
            fake_ocr_child.write(tmp_path / "ocr-child.sh", fake_ocr_child.ok())
            drive_files["id-1"] = ("信用卡/國泰世華/2026-09_國泰世華.pdf", encrypt(make_pdf([statement_lines()]), "A123456789"))
        return real(services_, summary, lease, trigger, mode)
    monkeypatch.setattr(pipeline, "_execute", execute)
    out = pipeline.poll(services, initiator_hint=None)
    assert out["claimed"] == 2 and "ocr_unavailable" in out["errors"]
    from worker import cli
    assert cli._bad(out) is True


def test_ocr_text_over_stdin_cap_is_too_large_without_a_parser_call(world, tmp_path, db_session):
    from worker import parser as parser_mod
    services, _, _ = _ocr_world(world, tmp_path, fake_ocr_child.ok(text="x" * (parser_mod.STDIN_CAP + 100)))
    seen = _record_parser(services)
    pipeline.run(services, trigger="owner_cli")
    assert seen == [] and _file_rows(db_session) == [("needs_review", "too_large")]


def test_parser_notes_survive_beside_the_ocr_note(world, tmp_path, db_session):
    from tests.worker import fake_claude
    from worker import parser as parser_mod
    services, _, cfg = _ocr_world(world, tmp_path, fake_ocr_child.ok())
    noted = fake_claude.write(tmp_path / "claude-noted", fake_claude.transcript({**fake_claude.GOOD, "notes": "parser note"}))
    services.parser = parser_mod.Parser(dataclasses.replace(cfg, parser_cli=noted), services.runner)
    pipeline.run(services, trigger="owner_cli")
    notes = db_session.execute(text("select raw->>'notes' from statement_revision")).scalar_one()
    assert notes.startswith("parser note\n") and "ocr: mean_confidence=" in notes


def test_build_creates_an_engine_only_when_enabled(world, client):
    services, _, cfg = world
    assert services.ocr is not None and services.ocr.cfg is cfg
    assert pipeline.build(dataclasses.replace(cfg, ocr_enabled=False), client, services.runner).ocr is None
```
Before running: confirm the enqueue route and token in `app/routers/statements.py` and `tests/integration/test_statement_ingest_api.py` (the `world` fixture uses `spa-token` for settings; use the token that holds the enqueue scope there) and that `pipeline._execute` is the per-run function `poll` calls (`pipeline.py` ~598–640); adjust the names in the two tests if they differ. If a changed `parser_cli` needs a new gate, the run's own gate covers it (the fake CLI passes).

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement**

`worker/pipeline.py`:
1. `Services`: `ocr: ocr_mod.Engine | None = None` after `runner`.
2. `build`: `services.ocr = ocr_mod.Engine(cfg) if cfg.ocr_enabled else None`.
3. Run boundary: at the top of `_execute` add `if services.ocr is not None: services.ocr.reset()`.
4. `revision_body` gains `parser: str = "claude-cli"` and `extra_note: str | None = None`; body unchanged except `"parser": parser` and `"raw": {"notes": notes}` with `notes = parsed.notes if extra_note is None else ((parsed.notes + "\n") if parsed.notes else "") + extra_note`; docstring: `` `parser` names the path that produced the text (R5.3: `claude-cli+ocr`); `extra_note` is appended to the parser's notes. ``
5. `process_file`: the `try` from `unlocked = pdf.unlock(...)` through `masked = services.masker.render(extracted, candidates)` becomes:
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
and `revision_body(..., parser="claude-cli+ocr" if ocr_note else "claude-cli", extra_note=ocr_note)`. The existing STDIN_CAP check stays between masking and the parser.
6. `poll`: keep `codes: set[str] = set()`; after each successful `_execute` do `codes.update(out.get("errors", []))`; before `return` set `out["errors"] = sorted(codes)`.

`worker/cli.py`:
```python
DEGRADED_CODES = ("parser_disabled", "ocr_unavailable")


def _bad(summary: dict) -> bool:
    """Exit 1 for a failed/aborted run, a disabled parser or an unavailable OCR child (its files wait for the next run)."""
    return bool(summary.get("bad_runs", 0)) or any(code in summary.get("errors", []) for code in DEGRADED_CODES)
```
and `return 1 if _bad(summary) else 0` where the `bad = …` line was (the `poll` summary now carries `errors`).

- [ ] **Step 4: Run** `tests/worker -q` → PASS. **Commit** `feat(worker): OCR branch in the pipeline, provenance, degraded exit for run and poll (R5.3)`.

---

### Task 8: `export_masked` takes the same branch with provenance (R5.6)

**Files:** `worker/verify.py`; test `tests/worker/test_verify.py`.

- [ ] **Step 1: Failing tests** (append; `_drive_world`, `encrypt`, `make_pdf`, `statement_lines`, `json`, `pytest` exist in the module)
```python
import dataclasses

from tests.worker import fake_ocr_child

RAW_OCR_TEXT = ("\n".join(statement_lines()) + "\n2026/09/01   COFFEE   1,234\n卡號 4111    2222　　3333    4444"
                "\n持卡人 王 小 明\n身分證 A12 345 6789")
PW_NAMED = "STATEMENT_ID_NUMBER=A123456789\nSTATEMENT_HOLDER_NAMES=王小明\n信用卡/國泰世華=$ID\n"


def _ocr_cfg(cfg, tmp_path, response, **kw):
    child = fake_ocr_child.write(tmp_path / "ocr-child.sh", response, **kw)
    return dataclasses.replace(cfg, ocr_child=child, ocr_timeout_s=2)


def test_export_masked_snapshot_canary(tmp_path):
    cfg, runner = _drive_world(tmp_path, {"a": encrypt(make_pdf([["short"]]), "A123456789")}, pw_text=PW_NAMED)
    out = verify.export_masked(_ocr_cfg(cfg, tmp_path, fake_ocr_child.ok(text=RAW_OCR_TEXT)), runner, folder=None, limit=None)
    assert out["exported"] == 1 and out["ocr"] == 1 and out["no_text_layer"] == 0
    snap = next((tmp_path / "v" / "masked").glob("*.txt")).read_text(encoding="utf-8")
    compact = "".join(snap.split())
    for secret in ("4111", "2222", "王小明", "A123456789"):
        assert secret not in compact
    assert "2026/09/01   COFFEE   1,234" in snap and "MERCHANT 001" in snap
    meta = json.loads(next((tmp_path / "v" / "masked").glob("*.json")).read_text(encoding="utf-8"))
    from worker import ocr
    assert meta["extraction"] == "ocr" and meta["ocr_fingerprint"] == ocr.FINGERPRINT


def test_export_masked_text_layer_meta_says_text(tmp_path):
    cfg, runner = _drive_world(tmp_path, {"a": encrypt(make_pdf([statement_lines()]), "A123456789")})
    verify.export_masked(_ocr_cfg(cfg, tmp_path, fake_ocr_child.ok()), runner, folder=None, limit=None)
    meta = json.loads(next((tmp_path / "v" / "masked").glob("*.json")).read_text(encoding="utf-8"))
    assert meta["extraction"] == "text" and "ocr_fingerprint" not in meta


@pytest.mark.parametrize("response,kw,counter", [
    ({"ok": False, "code": "low_confidence"}, {}, "no_text_layer"), ({"ok": False, "code": "too_large"}, {}, "too_large"),
    ({"ok": False, "code": "malformed"}, {}, "errors"), (None, {"exit_code": 139}, "errors"),
    (fake_ocr_child.ok(), {"sleep": 30}, "no_text_layer"), ({"ok": True, "pages": "SECRET", "text": "x"}, {}, "errors")])
def test_export_masked_ocr_failures_are_counted(tmp_path, response, kw, counter):
    cfg, runner = _drive_world(tmp_path, {"a": encrypt(make_pdf([["short"]]), "A123456789")})
    out = verify.export_masked(_ocr_cfg(cfg, tmp_path, response, **kw), runner, folder=None, limit=None)
    assert out["exported"] == 0 and out["ocr"] == 0 and out[counter] == 1


def test_export_masked_unavailable_is_one_code_for_the_run(tmp_path):
    cfg, runner = _drive_world(tmp_path, {"a": encrypt(make_pdf([["short"]]), "A123456789"), "b": encrypt(make_pdf([["tiny"]]), "A123456789")})
    out = verify.export_masked(_ocr_cfg(cfg, tmp_path, {"ok": False, "code": "unavailable"}), runner, folder=None, limit=None)
    assert out["ocr_unavailable"] is True and out["exported"] == 0 and out["errors"] == 2


def test_export_masked_with_ocr_disabled(tmp_path):
    cfg, runner = _drive_world(tmp_path, {"a": encrypt(make_pdf([["short"]]), "A123456789")})
    out = verify.export_masked(dataclasses.replace(cfg, ocr_enabled=False), runner, folder=None, limit=None)
    assert out["no_text_layer"] == 1 and out["ocr"] == 0
```
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** — in `export_masked`: `engine = ocr_mod.Engine(cfg) if cfg.ocr_enabled else None` after `masker = …`; `out["ocr"] = 0`; `from_ocr = False` before the unlock `try`; replace the `except pdf.NoTextLayer:` arm with
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
(`password` is assigned in the same `try` before `pdf.extract`); then
```python
        literals = candidates.get(key, [])
        rendered = masker.mask_ocr(extracted.text, literals) if from_ocr else masker.render(extracted, literals)
```
meta JSON gains `"extraction": "ocr" if from_ocr else "text"` and, only when `from_ocr`, `"ocr_fingerprint": ocr_mod.FINGERPRINT`; `out["ocr"] += 1` right after `out["exported"] += 1` when `from_ocr`.
- [ ] **Step 4: Run → PASS; commit** `feat(worker): export-masked OCR branch with snapshot provenance (R5.6)`.

---

### Task 9: Offline real-engine evidence tests, deploy notes, spec notes (R5.7, R5.9, R5.10)

**Files:** `pytest.ini`; create `tests/worker/test_ocr_models.py`; `deploy/statements/README.md`; the design spec (append "R5 implementation notes").

- [ ] **Step 1:** `pytest.ini`:
```
[pytest]
pythonpath = .
markers =
    ocr_models: runs the real OCR child under bwrap with no network (set RUN_OCR_MODEL_TESTS=1)
```
- [ ] **Step 2:** `tests/worker/test_ocr_models.py`
```python
"""Operator evidence (R5.7): the real child under bwrap with no network and a fresh HOME reads a rendered page, and
its stderr carries none of the page's content. Enabled by RUN_OCR_MODEL_TESTS=1; then a missing dependency FAILS."""
import json
import os
import platform
import shutil
import subprocess
import sys

import pytest

from tests.worker.pdfgen import make_pdf
from worker import config, ocr

pytestmark = pytest.mark.ocr_models
ENABLED = os.environ.get("RUN_OCR_MODEL_TESTS") == "1"
PAGE = ["STATEMENT 2026/09/30", "MERCHANT 001        1,234", "MERCHANT 002        5,678", "TOTAL        6,912"] * 6


@pytest.mark.skipif(not ENABLED, reason="set RUN_OCR_MODEL_TESTS=1")
def test_real_child_offline_reads_digits():
    import pypdfium2  # noqa: F401 — a missing wheel must fail this enabled test, not skip it
    import rapidocr_onnxruntime  # noqa: F401
    assert shutil.which("bwrap"), "bubblewrap required"
    engine = ocr.Engine(config.load({"STATEMENT_PARSER_SANDBOX": "true", "STATEMENT_OCR_TIMEOUT": "180"}))
    assert "--share-net" not in engine.argv()
    out = engine.read(make_pdf([PAGE]), None)
    assert out.extracted.pages == 1 and out.mean_confidence >= 0.8 and out.boxes >= 20
    digits = out.extracted.text.replace(",", "").replace(".", "")
    assert "1234" in digits and "5678" in digits and "6912" in digits
    print(json.dumps({"sha": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
                      "platform": platform.platform(), "python": sys.version.split()[0], "fingerprint": ocr.FINGERPRINT,
                      "constraints": "requirements-worker-constraints-aarch64-py313.txt"}))


@pytest.mark.skipif(not ENABLED, reason="set RUN_OCR_MODEL_TESTS=1")
def test_real_child_stderr_carries_no_page_content():
    import pypdfium2  # noqa: F401
    import rapidocr_onnxruntime  # noqa: F401
    engine = ocr.Engine(config.load({"STATEMENT_PARSER_SANDBOX": "true"}))
    proc = subprocess.run(engine.argv(), input=engine._header(None) + make_pdf([PAGE]), capture_output=True, env=engine.env(), timeout=180)
    assert proc.returncode == 0 and len(proc.stdout.splitlines()) == 1
    err = proc.stderr.decode(errors="replace")
    for s in ("MERCHANT", "1,234", "6,912", "STATEMENT 2026"):
        assert s not in err
```
- [ ] **Step 3: Run it once:** `RUN_OCR_MODEL_TESTS=1 .venv/bin/python -m pytest tests/worker/test_ocr_models.py -q -rs -s -p no:cacheprovider` → `2 passed`, no skips; keep the printed JSON for the rollout record. If bwrap refuses a bind, fix `_sandbox_prefix` (Task 6) and record the final bind list in the spec notes.
- [ ] **Step 4: Deploy README** — "Install": `uv pip install -r requirements.txt -r requirements-worker.txt -c requirements-worker-constraints-aarch64-py313.txt` for both the worker and the verify venvs; new section after "Pinned CLI copy":
```
## OCR (R5)

Files without a usable text layer (Type3 glyph fonts, `(cid:NNN)` garbage) are read by a child process
(`python -B -m worker.ocr_child`: pypdfium2 + RapidOCR) that the worker starts under bubblewrap with no network, a
minimal environment, read-only binds of the runtime only, a 2048 MB address-space limit, a 150 s CPU limit, no core
dumps and a 120 s wall-clock deadline; PDF bytes go in on stdin and one JSON line comes back. The text is
normalised (split card numbers, IDs and names rejoined), masked and line-guarded before the parser sees it. The
revision's `parser` is `claude-cli+ocr`; `raw.notes` carries the mean confidence. Models ship in the
`rapidocr-onnxruntime` wheel; nothing is downloaded at run time (the `ocr_models` tests prove it under bwrap).
`STATEMENT_OCR=false` restores the old behaviour.

The parser version is `claude-cli-<cli>-<model>-<12-hex fingerprint>` over CLI, model, instruction, schema and OCR
(engine versions + layout rules); deploying R5 re-parses every needs_review/failed file whose failure is
parse/guardrail/no_text_layer/too_large once, never a parsed one. The operator gate keys on the parser argv (the
instruction is part of it), so this release re-runs the gate; an OCR-only bump later does not.

A run that cannot start the OCR child (missing wheel, broken onnxruntime) reports `ocr_unavailable`, leaves those
files for the next run and exits 1 from both `run` and `poll`, so the unit shows failed. Verify snapshots record
`extraction` and `ocr_fingerprint`; to refresh an OCR snapshot after an engine change delete its `<sha>.txt` and
`.json` and run `export-masked` again. Operator evidence before the first real input:
`RUN_OCR_MODEL_TESTS=1 .venv/bin/python -m pytest tests/worker/test_ocr_models.py -rs -s` as the worker
(2 passed, 0 skipped, printed JSON kept in the release record); after the first run `coredumpctl list` shows nothing
for the worker.
```
- [ ] **Step 5: Spec notes** — append "### R5 implementation notes (2026-10-14, PR "R5")": thread setting found; bind list used; `version()` format; `revision_body` fields; `Masker.mask_ocr`; `export_masked` meta; `cli._bad`; the `ocr_models` evidence JSON from the host. No angle brackets may remain.
- [ ] **Step 6:** `.venv/bin/python -m pytest tests/worker tests/unit -q` → PASS. **Commit** `docs(worker): R5 OCR deploy notes, offline evidence tests, implementation notes`.

---

## Rollout (after merge; owner go for each step)

1. As `homehub-worker` and `homehub-verify`: `git fetch && checkout --detach <sha>`, `uv pip install -r requirements.txt -r requirements-worker.txt -c requirements-worker-constraints-aarch64-py313.txt`; as the worker: `RUN_OCR_MODEL_TESTS=1 .venv/bin/python -m pytest tests/worker/test_ocr_models.py -rs -s` → record `2 passed, 0 skipped` plus the printed JSON in `~/reports/home-hub-r5-release-<ts>/`.
2. `deploy/statements/gate.sh` as the worker.
3. `python -m worker run` as the worker: expect the 12 OCR-class files `parsed` (≥ 10 with guardrails ok), the 17 guardrail files re-parsed (informational), text-layer `parsed` counts unchanged, no `ocr_unavailable`, exit 0.
4. `coredumpctl list` shows nothing for the worker; record counts in the release dir and the memory file.
