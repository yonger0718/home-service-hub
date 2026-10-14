"""R5a: parent side of the OCR child boundary (worker/ocr_child.py documents the child and the protocol).

The parent starts the child with an explicit minimal environment (never the worker's own: it holds the verify DB
URL and other configuration) and, when the parser sandbox is on, under bwrap: `--unshare-all` (no network namespace
is shared), `--die-with-parent --new-session --cap-drop ALL`, read-only binds of /usr, /lib, /lib64, the real
interpreter's prefix, the venv's site-packages (the OCR models ship inside it) and the `worker/` package at
/work/worker; `--tmpfs /tmp`, `--proc`, `--dev`; no /etc, no home, no state or config directories. The venv's
`bin/python` symlink chain is not bound: the child is the real interpreter with site-packages on PYTHONPATH. A
configuration whose secret or state paths fall under a bound prefix is refused at construction (OCRConfigError).

I/O: the header and PDF are written from a thread; stdout is read through selectors and counted as it arrives (over
STDOUT_CAP → the child is killed and the file is `malformed`); the wall-clock deadline kills the child's session group
with SIGKILL and reaps it with a bound. Classification by what the parent observed: deadline or SIGXCPU → OCRTimeout;
result code `resource` → OCRResource; `unavailable` or a failed start → OCRUnavailable (cached until `reset()`);
`too_large` → pdf.TooLarge; `low_confidence` → OCRLowConfidence; `malformed`, cap overflow, any other signal or
non-zero exit, or an invalid response → pdf.Malformed. Every exception is created with no active exception and a
fixed phrase: no child output, no stdin, no library text.
"""
from __future__ import annotations

import json
import math
import os
import selectors
import shlex
import signal
import subprocess
import sys
import sysconfig
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
READ_CHUNK = 65536
KILL_WAIT_S = 10
TIMEOUT_SIGNALS = {int(signal.SIGXCPU)}
CODES = {"unavailable", "resource", "too_large", "malformed", "low_confidence"}


def _dist_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "0"


FINGERPRINT = f"{_dist_version('rapidocr-onnxruntime')}+{_dist_version('pypdfium2')}+layout{LAYOUT_VERSION}"


class OCRUnavailable(RuntimeError):
    """The child could not start, import, build or self-test the engine (before reading the PDF)."""


class OCRTimeout(RuntimeError):
    """The parent's deadline fired or the child's CPU limit did."""


class OCRResource(RuntimeError):
    """The child ran out of memory on this file."""


class OCRLowConfidence(RuntimeError):
    """Zero boxes or mean confidence under the floor."""


class OCRConfigError(ValueError):
    """A secret or state path would be readable inside the sandbox."""


@dataclass
class OcrResult:
    extracted: pdf.Extracted
    mean_confidence: float
    boxes: int
    empty_pages: int


def service_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def interpreter() -> str:
    return os.path.realpath(sys.executable)


def site_packages() -> str:
    return os.path.realpath(sysconfig.get_paths()["purelib"])


def runtime_binds() -> list[tuple[str, str]]:
    """(source, target) read-only binds: the OS runtime, the interpreter, site-packages, the worker package."""
    binds = []
    for path in ("/usr", "/lib", "/lib64", os.path.realpath(sys.base_prefix), site_packages()):
        if os.path.exists(path) and (path, path) not in binds:
            binds.append((path, path))
    binds.append((str(service_dir() / "worker"), "/work/worker"))
    return binds


def sandbox_prefix() -> list[str]:
    argv = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL"]
    for src, dst in runtime_binds():
        argv += ["--ro-bind", src, dst]
    return argv + ["--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev", "--chdir", "/work", "--"]


def protected_paths(cfg: WorkerConfig) -> list[str]:
    """Paths that must never be readable inside the child."""
    paths = [cfg.password_file, cfg.api_token_file, cfg.state_dir, cfg.parser_config_dir, cfg.verify_parser_config_dir,
             cfg.verify_dir, service_dir().parent.parent / ".env"]
    if cfg.minio_key_file is not None:
        paths.append(cfg.minio_key_file)
    if cfg.account_map_file is not None:
        paths.append(cfg.account_map_file)
    return [os.path.realpath(str(p)) for p in paths]


def check_config(cfg: WorkerConfig) -> None:
    """Refuse a layout where a protected path sits under a bound prefix (read-only is not confidentiality)."""
    if not cfg.parser_sandbox:
        return
    for secret in protected_paths(cfg):
        for src, _dst in runtime_binds():
            if secret == src or secret.startswith(src.rstrip("/") + "/"):
                raise OCRConfigError("a protected path lies under a sandbox bind")


def _header(password: str | None, pdf_len: int, *, scale: float = SCALE, max_pages: int = MAX_PAGES,
            max_pixels: int = MAX_PIXELS) -> bytes:
    return (json.dumps({"password": password, "scale": scale, "max_pages": max_pages, "max_pixels": max_pixels,
                        "pdf_len": pdf_len}) + "\n").encode()


class Engine:
    def __init__(self, cfg: WorkerConfig):
        check_config(cfg)
        self.cfg = cfg
        self.starts = 0
        self.last_pgid: int | None = None
        self.last_stdout_bytes = 0
        self._unavailable = False

    def reset(self) -> None:
        """Run boundary: a host that was broken may have been fixed between runs."""
        self._unavailable = False

    def child_command(self) -> list[str]:
        if self.cfg.ocr_child:
            return shlex.split(self.cfg.ocr_child)
        return [interpreter(), "-B", "-m", "worker.ocr_child", "--threads", str(self.cfg.ocr_threads),
                "--rlimit-as-mb", str(self.cfg.ocr_rlimit_mb), "--rlimit-cpu-s", str(RLIMIT_CPU_S)]

    def argv(self) -> list[str]:
        return (sandbox_prefix() if self.cfg.parser_sandbox else []) + self.child_command()

    def env(self) -> dict[str, str]:
        work = "/work" if self.cfg.parser_sandbox else str(service_dir())
        return {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1", "PYTHONPATH": f"{work}:{site_packages()}"}

    def read(self, data: bytes, password: str | None) -> OcrResult:
        if self._unavailable:
            raise OCRUnavailable("ocr unavailable")
        self.starts += 1
        outcome, payload = self._exchange(_header(password, len(data)) + data)
        # from here on no exception is active, so every raise has __context__ None
        if outcome == "timeout":
            raise OCRTimeout("ocr timed out")
        if outcome == "malformed":
            raise pdf.Malformed("malformed")
        if outcome == "start_failed":
            self._unavailable = True
            raise OCRUnavailable("ocr unavailable")
        result = validate(payload)
        if result is None:
            raise pdf.Malformed("malformed")
        if result["ok"]:
            text = result["text"]
            page1 = len(text.split("\f")[0].strip()) if text else 0
            return OcrResult(pdf.Extracted(text, [], result["pages"], page1), result["mean_confidence"],
                             result["boxes"], result["empty_pages"])
        code = result["code"]
        if code == "unavailable":
            self._unavailable = True
            raise OCRUnavailable("ocr unavailable")
        if code == "resource":
            raise OCRResource("ocr out of memory")
        if code == "too_large":
            raise pdf.TooLarge("too large")
        if code == "low_confidence":
            raise OCRLowConfidence("low confidence")
        raise pdf.Malformed("malformed")

    def _exchange(self, stdin: bytes) -> tuple[str, bytes]:
        """(outcome, stdout): outcome in ok | timeout | malformed | start_failed. Never raises."""
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
                chunk = os.read(proc.stdout.fileno(), READ_CHUNK)
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
            return ("timeout" if sig in TIMEOUT_SIGNALS else "malformed"), b""
        return "ok", bytes(out)


def _write_stdin(proc: subprocess.Popen, data: bytes) -> None:
    try:
        proc.stdin.write(data)
        proc.stdin.close()
    except (BrokenPipeError, OSError, ValueError):
        pass  # the child may exit early (unavailable) without reading the PDF


def _kill(proc: subprocess.Popen) -> None:
    """SIGKILL the child's session (bwrap and everything under it via --die-with-parent), then reap with a bound."""
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


def _number(value, lo: float, hi: float) -> float | None:
    """A finite int/float (never bool) within [lo, hi]; huge ints are rejected before any float conversion."""
    if type(value) is int:
        return float(value) if lo <= value <= hi else None
    if type(value) is float:
        return value if math.isfinite(value) and lo <= value <= hi else None
    return None


def validate(payload: bytes) -> dict | None:
    """Exactly one JSON object line with the expected fields, types and ranges; otherwise None. Never raises."""
    try:
        lines = payload.decode("utf-8").split("\n")
        lines = [line for line in lines if line != ""]
        if len(lines) != 1 or len(payload) > STDOUT_CAP:
            return None
        obj = json.loads(lines[0])
        if not isinstance(obj, dict):
            return None
        ok = obj.get("ok")
        if ok is True:
            pages, empty, boxes = obj.get("pages"), obj.get("empty_pages"), obj.get("boxes")
            text = obj.get("text")
            if not all(type(v) is int for v in (pages, empty, boxes)):
                return None
            if not (1 <= pages <= MAX_PAGES) or not (0 <= empty <= pages) or boxes < 0 or not isinstance(text, str):
                return None
            conf = _number(obj.get("mean_confidence"), 0.0, 1.0)
            if conf is None:
                return None
            return {"ok": True, "pages": pages, "empty_pages": empty, "boxes": boxes, "mean_confidence": conf, "text": text}
        if ok is False:
            code = obj.get("code")
            if isinstance(code, str) and code in CODES:
                return {"ok": False, "code": code}
        return None
    except (UnicodeDecodeError, ValueError, RecursionError, OverflowError, TypeError, AttributeError):
        return None
