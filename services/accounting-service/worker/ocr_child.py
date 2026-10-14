"""R5a: the OCR child. Started by worker/ocr.py as
`<interpreter> -B -m worker.ocr_child --threads N --rlimit-as-mb M --rlimit-cpu-s S`, under bwrap with no network
and a minimal environment. Nothing secret travels in argv; the password comes in the stdin header.

Order (AGENT-118 rulings): rlimits (core 0, address space, CPU) → fd isolation (fd 1 is moved to /dev/null and the
protocol line is written on a saved descriptor, so native libraries cannot interleave with it) → readiness (import
pypdfium2/numpy/rapidocr, build the engine, self-test on a 32x32 white image) → header line, read byte by byte,
bounded → exactly `pdf_len` bytes → page bound → per page: pixel bound before any allocation, render, recognise, rows →
confidence floor → one JSON line. Readiness therefore runs before any PDF byte is accepted, and a failing host never
sees the statement. Nothing is written to disk; nothing but the protocol line reaches the saved descriptor.

Result codes: ok → {"ok": true, "pages", "empty_pages", "boxes", "mean_confidence", "text"}; otherwise
{"ok": false, "code": "unavailable" | "resource" | "too_large" | "malformed" | "low_confidence"}. `resource` is a
MemoryError anywhere (readiness or recognition) — a retryable condition, not a parse failure.

Thread setting (rapidocr-onnxruntime 1.4.4 on this host): RapidOCR(intra_op_num_threads=N, inter_op_num_threads=1).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from io import BytesIO

from worker import ocr_text

HEADER_MAX = 4096
CONFIDENCE_FLOOR = 0.80
SELF_TEST_SIDE = 32
DEFAULT_AS_MB = 2048
DEFAULT_CPU_S = 150
DEFAULT_THREADS = 2
CODES = ("unavailable", "resource", "too_large", "malformed", "low_confidence")


class Unavailable(RuntimeError):
    """Imports, engine construction or the self-test failed."""


def apply_rlimits(as_mb: int, cpu_s: int) -> None:
    import resource  # noqa: PLC0415

    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    for res, value in ((resource.RLIMIT_AS, int(as_mb) * 1024 * 1024), (resource.RLIMIT_CPU, int(cpu_s))):
        _soft, hard = resource.getrlimit(res)
        cap = value if hard == resource.RLIM_INFINITY else min(value, hard)
        resource.setrlimit(res, (cap, hard))


def load_engine(threads: int):
    """RapidOCR with the wheel's bundled models; returns a callable img -> [[quad, text, score], ...]."""
    try:
        import numpy as np  # noqa: PLC0415
        import pypdfium2  # noqa: F401, PLC0415 — the renderer is part of readiness
        from rapidocr_onnxruntime import RapidOCR  # noqa: PLC0415

        engine = RapidOCR(intra_op_num_threads=threads, inter_op_num_threads=1)
        engine(np.full((SELF_TEST_SIDE, SELF_TEST_SIDE, 3), 255, dtype=np.uint8))
    except MemoryError:
        raise
    except Exception:  # noqa: BLE001 — the library text never leaves the child
        raise Unavailable() from None

    def backend(img):
        result, _elapse = engine(img)
        return result or []
    return backend


def _render(page, scale: float):
    return page.render(scale=scale, rev_byteorder=True).to_numpy()


def run(header: dict, data: bytes, backend, render=None) -> dict:
    """Recognise one PDF with a ready backend. `header`: password, scale, max_pages, max_pixels."""
    try:
        import pypdfium2 as pdfium  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return {"ok": False, "code": "unavailable"}
    render = render or _render
    try:
        scale = float(header.get("scale", 3.0))
        max_pages = int(header.get("max_pages", 10))
        max_pixels = int(header.get("max_pixels", 30_000_000))
        password = header.get("password")
        if password is not None and not isinstance(password, str):
            return {"ok": False, "code": "malformed"}
    except (TypeError, ValueError):
        return {"ok": False, "code": "malformed"}
    try:
        doc = pdfium.PdfDocument(BytesIO(data), password=password)
        npages = len(doc)
    except MemoryError:
        return {"ok": False, "code": "resource"}
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
            if not (math.isfinite(w) and math.isfinite(h)) or w <= 0 or h <= 0:
                return {"ok": False, "code": "malformed"}
            if math.ceil(w * scale) * math.ceil(h * scale) > max_pixels:
                return {"ok": False, "code": "too_large"}
            img = render(page, scale)
            boxes: list[ocr_text.Box] = []
            for quad, text, score in backend(img):
                xs = [float(p[0]) for p in quad]
                ys = [float(p[1]) for p in quad]
                boxes.append((min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys), str(text)))
                scores.append(float(score))
            if not boxes:
                empty_pages += 1
            boxes_total += len(boxes)
            texts.append(ocr_text.layout(boxes))
    except MemoryError:
        return {"ok": False, "code": "resource"}
    except Exception:  # noqa: BLE001 — render or engine failure on a page
        return {"ok": False, "code": "malformed"}
    mean = sum(scores) / len(scores) if scores else 0.0
    if boxes_total == 0 or mean < CONFIDENCE_FLOOR:
        return {"ok": False, "code": "low_confidence"}
    return {"ok": True, "pages": npages, "empty_pages": empty_pages, "boxes": boxes_total, "mean_confidence": mean,
            "text": "\f".join(texts)}


def read_header(fd: int) -> dict | None:
    """One line, read a byte at a time (nothing past the newline is consumed), bounded by HEADER_MAX."""
    buf = bytearray()
    while len(buf) < HEADER_MAX:
        chunk = os.read(fd, 1)
        if not chunk:
            return None
        if chunk == b"\n":
            break
        buf.extend(chunk)
    else:
        return None
    try:
        header = json.loads(bytes(buf))
    except ValueError:
        return None
    return header if isinstance(header, dict) else None


def read_exact(fd: int, length: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < length:
        chunk = os.read(fd, min(65536, length - len(buf)))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="worker.ocr_child", add_help=False)
    ap.add_argument("--threads", type=int, default=DEFAULT_THREADS)
    ap.add_argument("--rlimit-as-mb", type=int, default=DEFAULT_AS_MB)
    ap.add_argument("--rlimit-cpu-s", type=int, default=DEFAULT_CPU_S)
    return ap.parse_args(argv)


def main(argv: list[str] | None = None, *, backend=None, stdin_fd: int = 0, stdout_fd: int = 1,
         limits: bool = True) -> int:
    """`stdin_fd`/`stdout_fd` are parameters so tests can run the protocol in-process on pipes; `limits=False` keeps a
    test process's rlimits untouched (the subprocess tests cover the real thing)."""
    args = parse_args(argv)
    if limits:
        apply_rlimits(args.rlimit_as_mb, args.rlimit_cpu_s)
    proto_fd = os.dup(stdout_fd)  # the only descriptor the protocol line is written on
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, stdout_fd)  # native stdout (fd 1 in production) goes nowhere
    os.close(devnull)

    def emit(result: dict) -> int:
        os.write(proto_fd, (json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"))
        os.close(proto_fd)
        return 0

    try:
        ready = backend if backend is not None else load_engine(args.threads)
        import pypdfium2  # noqa: F401, PLC0415
    except MemoryError:
        return emit({"ok": False, "code": "resource"})
    except Exception:  # noqa: BLE001
        return emit({"ok": False, "code": "unavailable"})
    header = read_header(stdin_fd)
    if header is None or type(header.get("pdf_len")) is not int or header["pdf_len"] < 0:
        return emit({"ok": False, "code": "malformed"})
    data = read_exact(stdin_fd, header["pdf_len"])
    if data is None:
        return emit({"ok": False, "code": "malformed"})
    return emit(run(header, data, ready))


if __name__ == "__main__":
    sys.exit(main())
