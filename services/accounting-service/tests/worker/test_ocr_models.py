"""Operator evidence for the OCR child boundary (R5a): the real engine inside the real sandbox.

Enabled by RUN_OCR_MODEL_TESTS=1; when enabled, a missing wheel FAILS (plain imports, no importorskip). Records the
SHA, platform, interpreter and OCR fingerprint on stdout for the release record. Models come from the wheel: the
sandbox has no network, a fresh tmpfs HOME and only the runtime bound.
"""
import json
import os
import platform
import shutil
import subprocess
import sys

import pytest

from tests.worker.pdfgen import make_pdf
from worker import config, constraints, ocr

CONSTRAINTS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "requirements-worker-constraints-aarch64-py313.txt")

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
    evidence = {"sha": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
                "platform": platform.platform(), "python": sys.version.split()[0], "fingerprint": ocr.FINGERPRINT,
                "constraints_sha256": constraints.sha256_of(__import__("pathlib").Path(CONSTRAINTS)),
                "mean_confidence": round(out.mean_confidence, 3), "boxes": out.boxes}
    assert len(evidence["constraints_sha256"]) == 64  # the record carries the hash of the constraints bytes
    print(json.dumps(evidence))


@pytest.mark.skipif(not ENABLED, reason="set RUN_OCR_MODEL_TESTS=1")
def test_real_child_stderr_carries_no_page_content():
    import pypdfium2  # noqa: F401
    import rapidocr_onnxruntime  # noqa: F401
    engine = ocr.Engine(config.load({"STATEMENT_PARSER_SANDBOX": "true"}))
    data = make_pdf([PAGE])
    proc = subprocess.run(engine.argv(), input=ocr._header(None, len(data)) + data, capture_output=True, env=engine.env(), timeout=180)
    assert proc.returncode == 0
    lines = proc.stdout.splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["ok"] is True
    err = proc.stderr.decode(errors="replace")
    for s in ("MERCHANT", "1,234", "6,912", "STATEMENT 2026"):
        assert s not in err
