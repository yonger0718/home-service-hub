import json
import os
import subprocess
import sys

import numpy as np
import pytest

from tests.worker.pdfgen import encrypt, make_pdf, statement_lines
from worker import ocr_child

HDR = {"password": None, "scale": 3.0, "max_pages": 10, "max_pixels": 30_000_000}
RAW = [[[[10, 10], [200, 10], [200, 30], [10, 30]], "MERCHANT 001", 0.97], [[[400, 12], [460, 12], [460, 28], [400, 28]], "1,234", 0.93]]
PY = sys.executable
CHILD_ARGS = ["--threads", "1", "--rlimit-as-mb", "4096", "--rlimit-cpu-s", "600"]


def backend_of(per_call):
    calls = []

    def backend(img):
        assert isinstance(img, np.ndarray) and img.ndim == 3 and img.shape[2] == 3 and img.dtype == np.uint8
        calls.append(img.shape)
        return per_call(len(calls)) if callable(per_call) else per_call
    backend.calls = calls
    return backend


# ---- run(): recognition with a ready backend ---------------------------------------------------------------------

def test_run_reads_every_page_and_reports_counts():
    b = backend_of(RAW)
    out = ocr_child.run(HDR, make_pdf([statement_lines(), ["PAGE TWO"]]), b)
    assert out["ok"] and out["pages"] == 2 and len(b.calls) == 2 and out["boxes"] == 4 and out["empty_pages"] == 0
    assert out["text"].count("\f") == 1 and "MERCHANT 001" in out["text"] and abs(out["mean_confidence"] - 0.95) < 1e-6


def test_run_allows_an_empty_page_but_not_an_empty_file():
    out = ocr_child.run(HDR, make_pdf([statement_lines(), ["x"]]), backend_of(lambda n: RAW if n == 1 else []))
    assert out["ok"] and out["empty_pages"] == 1 and out["pages"] == 2
    assert ocr_child.run(HDR, make_pdf([statement_lines()]), backend_of([])) == {"ok": False, "code": "low_confidence"}


def test_run_confidence_floor():
    low = [[[[10, 10], [200, 10], [200, 30], [10, 30]], "x", 0.5]]
    assert ocr_child.run(HDR, make_pdf([statement_lines()]), backend_of(low))["code"] == "low_confidence"


def test_run_uses_the_password():
    data = encrypt(make_pdf([statement_lines()]), "A123456789")
    assert ocr_child.run({**HDR, "password": "A123456789"}, data, backend_of(RAW))["ok"]
    assert ocr_child.run(HDR, data, backend_of(RAW)) == {"ok": False, "code": "malformed"}
    assert ocr_child.run({**HDR, "password": 123}, data, backend_of(RAW)) == {"ok": False, "code": "malformed"}


def test_page_bound_before_any_render():
    b, renders = backend_of(RAW), []
    out = ocr_child.run(HDR, make_pdf([["p"]] * 11), b, render=lambda page, scale: renders.append(1))
    assert out == {"ok": False, "code": "too_large"} and b.calls == [] and renders == []


def test_pixel_bound_before_render():
    b, renders = backend_of(RAW), []

    def spy(page, scale):
        renders.append(1)
        return np.full((10, 10, 3), 255, dtype=np.uint8)
    out = ocr_child.run({**HDR, "max_pixels": 1000}, make_pdf([statement_lines()]), b, render=spy)  # A4 at 3.0 ≈ 13 Mpx
    assert out == {"ok": False, "code": "too_large"} and renders == [] and b.calls == []
    assert ocr_child.run({**HDR, "max_pixels": 50_000_000}, make_pdf([statement_lines()]), b, render=spy)["ok"] and renders == [1]


def test_malformed_bytes_and_backend_failure():
    assert ocr_child.run(HDR, b"%PDF-1.4 not really", backend_of(RAW)) == {"ok": False, "code": "malformed"}

    def boom(img):
        raise RuntimeError("engine failed mid-page")
    assert ocr_child.run(HDR, make_pdf([statement_lines()]), boom) == {"ok": False, "code": "malformed"}


def test_memory_error_during_recognition_is_resource():
    def oom(img):
        raise MemoryError()
    assert ocr_child.run(HDR, make_pdf([statement_lines()]), oom) == {"ok": False, "code": "resource"}


def test_bad_header_values_are_malformed():
    assert ocr_child.run({**HDR, "scale": "big"}, make_pdf([statement_lines()]), backend_of(RAW)) == {"ok": False, "code": "malformed"}


# ---- main(): protocol, order, fd isolation (in-process on pipes) --------------------------------------------------

def _pipes():
    r_in, w_in = os.pipe()
    r_out, w_out = os.pipe()
    return r_in, w_in, r_out, w_out


def _collect(r_out) -> bytes:
    chunks = []
    while True:
        c = os.read(r_out, 65536)
        if not c:
            return b"".join(chunks)
        chunks.append(c)


def _feed(w_in, header: dict, data: bytes):
    os.write(w_in, json.dumps(header).encode() + b"\n" + data)
    os.close(w_in)


def test_main_roundtrip_on_pipes():
    r_in, w_in, r_out, w_out = _pipes()
    data = make_pdf([statement_lines()])
    _feed(w_in, {**HDR, "pdf_len": len(data)}, data)
    rc = ocr_child.main(CHILD_ARGS, backend=backend_of(RAW), stdin_fd=r_in, stdout_fd=w_out, limits=False)
    os.close(w_out)
    out = _collect(r_out)
    os.close(r_out); os.close(r_in)
    assert rc == 0 and out.count(b"\n") == 1 and json.loads(out)["ok"] and json.loads(out)["pages"] == 1


def test_main_readiness_precedes_any_pdf_byte(monkeypatch):
    """With readiness failing, the whole stdin payload is still unread afterwards: not one byte was consumed."""
    monkeypatch.setattr(ocr_child, "load_engine", lambda threads: (_ for _ in ()).throw(ocr_child.Unavailable()))
    r_in, w_in, r_out, w_out = _pipes()
    data = make_pdf([statement_lines()])
    payload = json.dumps({**HDR, "pdf_len": len(data)}).encode() + b"\n" + data
    os.write(w_in, payload); os.close(w_in)
    rc = ocr_child.main(CHILD_ARGS, backend=None, stdin_fd=r_in, stdout_fd=w_out, limits=False)
    os.close(w_out)
    assert rc == 0 and json.loads(_collect(r_out)) == {"ok": False, "code": "unavailable"}
    assert _collect(r_in) == payload  # nothing consumed
    os.close(r_out); os.close(r_in)


def test_main_reads_exactly_pdf_len_and_rejects_short_payloads():
    r_in, w_in, r_out, w_out = _pipes()
    data = make_pdf([statement_lines()])
    os.write(w_in, json.dumps({**HDR, "pdf_len": len(data)}).encode() + b"\n" + data + b"TRAILING-JUNK"); os.close(w_in)
    ocr_child.main(CHILD_ARGS, backend=backend_of(RAW), stdin_fd=r_in, stdout_fd=w_out, limits=False)
    os.close(w_out)
    assert json.loads(_collect(r_out))["ok"]
    assert _collect(r_in) == b"TRAILING-JUNK"  # exact-length read: nothing past pdf_len is consumed
    os.close(r_out); os.close(r_in)
    r_in, w_in, r_out, w_out = _pipes()
    os.write(w_in, json.dumps({**HDR, "pdf_len": len(data) + 5}).encode() + b"\n" + data); os.close(w_in)
    ocr_child.main(CHILD_ARGS, backend=backend_of(RAW), stdin_fd=r_in, stdout_fd=w_out, limits=False)
    os.close(w_out)
    assert json.loads(_collect(r_out)) == {"ok": False, "code": "malformed"}
    os.close(r_out); os.close(r_in)


@pytest.mark.parametrize("header_line", [b"not json\n", b"[1,2]\n", b'{"pdf_len": "9"}\n', b'{"pdf_len": -1}\n', b"x" * 5000 + b"\n"])
def test_main_bad_headers_are_malformed(header_line):
    r_in, w_in, r_out, w_out = _pipes()
    os.write(w_in, header_line); os.close(w_in)
    ocr_child.main(CHILD_ARGS, backend=backend_of(RAW), stdin_fd=r_in, stdout_fd=w_out, limits=False)
    os.close(w_out)
    assert json.loads(_collect(r_out)) == {"ok": False, "code": "malformed"}
    os.close(r_out); os.close(r_in)


def test_main_isolates_native_stdout_from_the_protocol_line():
    """A library writing to the stdout descriptor during readiness or recognition cannot reach the protocol stream."""
    r_in, w_in, r_out, w_out = _pipes()
    data = make_pdf([statement_lines()])
    _feed(w_in, {**HDR, "pdf_len": len(data)}, data)

    def noisy(img):
        os.write(w_out, b"native diagnostic line\n")  # fd 1 in production
        return RAW
    rc = ocr_child.main(CHILD_ARGS, backend=noisy, stdin_fd=r_in, stdout_fd=w_out, limits=False)
    os.close(w_out)
    out = _collect(r_out)
    os.close(r_out); os.close(r_in)
    assert rc == 0 and out.count(b"\n") == 1 and json.loads(out)["ok"] and b"native" not in out


def test_main_memory_error_in_readiness_is_resource(monkeypatch):
    monkeypatch.setattr(ocr_child, "load_engine", lambda threads: (_ for _ in ()).throw(MemoryError()))
    r_in, w_in, r_out, w_out = _pipes()
    os.write(w_in, b"{}\n"); os.close(w_in)
    ocr_child.main(CHILD_ARGS, backend=None, stdin_fd=r_in, stdout_fd=w_out, limits=False)
    os.close(w_out)
    assert json.loads(_collect(r_out)) == {"ok": False, "code": "resource"}
    os.close(r_out); os.close(r_in)


# ---- subprocess: real fd 1, real rlimits ---------------------------------------------------------------------------

def test_subprocess_protocol_with_native_noise_on_fd1():
    script = ("import os, sys\nfrom worker import ocr_child\n"
              "def backend(img):\n    os.write(1, b'NATIVE NOISE\\n'); sys.stdout.write('python print\\n'); sys.stdout.flush(); return %r\n"
              "sys.exit(ocr_child.main(sys.argv[1:], backend=backend))\n" % (RAW,))
    data = make_pdf([statement_lines()])
    proc = subprocess.run([PY, "-c", script, *CHILD_ARGS], input=json.dumps({**HDR, "pdf_len": len(data)}).encode() + b"\n" + data,
                          capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[:300]
    assert proc.stdout.count(b"\n") == 1 and json.loads(proc.stdout)["ok"] and b"NOISE" not in proc.stdout and b"print" not in proc.stdout


def test_subprocess_limits_are_set_before_readiness():
    script = ("import sys, json, resource\nfrom worker import ocr_child\n"
              "def load(threads):\n"
              "    print(json.dumps({'as': resource.getrlimit(resource.RLIMIT_AS)[0], 'cpu': resource.getrlimit(resource.RLIMIT_CPU)[0],"
              " 'core': resource.getrlimit(resource.RLIMIT_CORE)[0], 'threads': threads}), file=sys.stderr)\n"
              "    return lambda img: %r\n"
              "ocr_child.load_engine = load\n"
              "sys.exit(ocr_child.main(sys.argv[1:]))\n" % (RAW,))
    data = make_pdf([statement_lines()])
    proc = subprocess.run([PY, "-c", script, "--threads", "3", "--rlimit-as-mb", "777", "--rlimit-cpu-s", "33"],
                          input=json.dumps({**HDR, "pdf_len": len(data)}).encode() + b"\n" + data, capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[:300]
    seen = json.loads(proc.stderr.decode().splitlines()[-1])
    assert seen == {"as": 777 * 1024 * 1024, "cpu": 33, "core": 0, "threads": 3}
    assert json.loads(proc.stdout)["ok"]


# ---- review round: readiness order, selftest, resource classification, header clamps ------------------------------

def test_selftest_reports_ready_without_touching_stdin():
    r_in, w_in, r_out, w_out = _pipes()
    os.write(w_in, b"never read"); os.close(w_in)
    rc = ocr_child.main(CHILD_ARGS + ["--selftest"], backend=backend_of(RAW), stdin_fd=r_in, stdout_fd=w_out, limits=False)
    os.close(w_out)
    assert rc == 0 and json.loads(_collect(r_out)) == {"ready": True} and _collect(r_in) == b"never read"
    os.close(r_out); os.close(r_in)


def test_native_noise_during_readiness_never_reaches_the_protocol(monkeypatch):
    r_in, w_in, r_out, w_out = _pipes()
    data = make_pdf([statement_lines()])
    _feed(w_in, {**HDR, "pdf_len": len(data)}, data)

    def noisy_load(threads):
        os.write(w_out, b"ORT banner during import\n")  # fd 1 in production
        return backend_of(RAW)
    monkeypatch.setattr(ocr_child, "load_engine", noisy_load)
    rc = ocr_child.main(CHILD_ARGS, backend=None, stdin_fd=r_in, stdout_fd=w_out, limits=False)
    os.close(w_out)
    out = _collect(r_out)
    os.close(r_out); os.close(r_in)
    assert rc == 0 and out.count(b"\n") == 1 and json.loads(out)["ok"] and b"banner" not in out


@pytest.mark.parametrize("exc,code", [(MemoryError(), "resource"), (OSError(12, "Cannot allocate memory"), "resource"),
                                      (RuntimeError("[ONNXRuntimeError] : 1 : FAIL : bad_alloc"), "resource"),
                                      (RuntimeError("Failed to allocate memory for requested buffer"), "resource"),
                                      (RuntimeError("engine failed mid-page"), "malformed")])
def test_backend_failures_are_classified(exc, code):
    def failing(img):
        raise exc
    assert ocr_child.run(HDR, make_pdf([statement_lines()]), failing) == {"ok": False, "code": code}


def test_readiness_failures_are_classified(monkeypatch):
    for exc, code in ((OSError(12, "Cannot allocate memory"), "resource"), (RuntimeError("bad_alloc"), "resource"),
                      (ImportError("no module"), "unavailable")):
        def load(threads, exc=exc):
            raise exc
        monkeypatch.setattr(ocr_child, "load_engine", load)
        r_in, w_in, r_out, w_out = _pipes()
        os.write(w_in, b"{}\n"); os.close(w_in)
        ocr_child.main(CHILD_ARGS, backend=None, stdin_fd=r_in, stdout_fd=w_out, limits=False)
        os.close(w_out)
        assert json.loads(_collect(r_out)) == {"ok": False, "code": code}
        os.close(r_out); os.close(r_in)


@pytest.mark.parametrize("header", [{**HDR, "scale": 0}, {**HDR, "scale": float("nan")}, {**HDR, "scale": 99},
                                    {**HDR, "max_pixels": 0}, {**HDR, "max_pages": 0}, {**HDR, "max_pixels": 10 ** 12}])
def test_header_bounds_are_clamped(header):
    assert ocr_child.run(header, make_pdf([statement_lines()]), backend_of(RAW)) == {"ok": False, "code": "malformed"}


def test_pdf_len_over_the_cap_is_malformed_before_reading():
    r_in, w_in, r_out, w_out = _pipes()
    os.write(w_in, json.dumps({**HDR, "pdf_len": ocr_child.MAX_PDF_LEN + 1}).encode() + b"\nXX"); os.close(w_in)
    ocr_child.main(CHILD_ARGS, backend=backend_of(RAW), stdin_fd=r_in, stdout_fd=w_out, limits=False)
    os.close(w_out)
    assert json.loads(_collect(r_out)) == {"ok": False, "code": "malformed"} and _collect(r_in) == b"XX"
    os.close(r_out); os.close(r_in)


def test_subprocess_order_limits_then_isolation_then_readiness():
    """Inside a real child: when readiness runs, the limits are already set and fd 1 is already /dev/null."""
    script = ("import sys, os, json, resource, stat\nfrom worker import ocr_child\n"
              "def load(threads):\n"
              "    st = os.fstat(1); devnull = os.stat(os.devnull)\n"
              "    print(json.dumps({'as': resource.getrlimit(resource.RLIMIT_AS)[0], 'core': resource.getrlimit(resource.RLIMIT_CORE)[0],"
              " 'fd1_is_devnull': (st.st_dev, st.st_ino) == (devnull.st_dev, devnull.st_ino)}), file=sys.stderr)\n"
              "    os.write(1, b'NOISE\\n'); return lambda img: %r\n"
              "ocr_child.load_engine = load\n"
              "sys.exit(ocr_child.main(sys.argv[1:]))\n" % (RAW,))
    data = make_pdf([statement_lines()])
    proc = subprocess.run([PY, "-c", script, "--threads", "1", "--rlimit-as-mb", "777", "--rlimit-cpu-s", "33"],
                          input=json.dumps({**HDR, "pdf_len": len(data)}).encode() + b"\n" + data, capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[:300]
    seen = json.loads(proc.stderr.decode().splitlines()[-1])
    assert seen == {"as": 777 * 1024 * 1024, "core": 0, "fd1_is_devnull": True}
    assert proc.stdout.count(b"\n") == 1 and json.loads(proc.stdout)["ok"] and b"NOISE" not in proc.stdout
