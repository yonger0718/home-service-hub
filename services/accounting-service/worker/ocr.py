"""R5a: parent side of the OCR child boundary (worker/ocr_child.py documents the child and the protocol).

The parent starts the child with an explicit minimal environment (never the worker's own: it holds the verify DB
URL and other configuration) and, when the parser sandbox is on, under bwrap: `--unshare-all` (no network namespace
is shared), `--die-with-parent --new-session --cap-drop ALL`, read-only binds of /usr, /lib, /lib64, the real
interpreter's prefix, the venv's site-packages (the OCR models ship inside it) and the `worker/` package at
/work/worker; `--tmpfs /tmp`, `--proc`, `--dev`; no /etc, no home, no state or config directories. The venv's
`bin/python` symlink chain is not bound: the child is the real interpreter with site-packages on PYTHONPATH. A
configuration whose protected paths overlap a bound prefix in either direction is refused (OCRConfigError).

Once per run the parent runs the child with `--selftest` (sandbox start, imports, engine, model sessions); a failure
there is `OCRUnavailable`, cached until `reset()`, so a broken host never turns into per-file parse failures.

I/O: the header and PDF are written from a thread that owns stdin; stdout is read through selectors and counted as
it arrives (over STDOUT_CAP → the child is killed and the file is `malformed`); the wall-clock deadline kills the
child's session group with SIGKILL and reaps it with a bound; after any exit the group is killed again so no straggler
survives. Classification by what the parent observed: deadline or SIGXCPU → OCRTimeout; result code `resource` →
OCRResource; `unavailable` or a failed start → OCRUnavailable; `too_large` → pdf.TooLarge; `low_confidence` →
OCRLowConfidence; `malformed`, cap overflow, any other signal or non-zero exit, or an invalid response →
pdf.Malformed. Every exception is created with no active exception and a fixed phrase: no child output, no stdin,
no library text. `OcrResult` and `pdf.Extracted` never print the text in their repr.
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
from dataclasses import dataclass, field
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
CONFIDENCE_FLOOR = 0.80
TIMEOUT_SIGNALS = {int(signal.SIGXCPU)}
CODES = {"unavailable", "resource", "too_large", "malformed", "low_confidence"}
OK_KEYS = {"ok", "pages", "empty_pages", "boxes", "mean_confidence", "text"}
ERR_KEYS = {"ok", "code"}


def _dist_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "0"


FINGERPRINT = f"{_dist_version('rapidocr-onnxruntime')}+{_dist_version('pypdfium2')}+layout{LAYOUT_VERSION}"


class OCRUnavailable(RuntimeError):
    """The sandbox or the engine cannot start on this host (checked before any PDF is read)."""


class OCRTimeout(RuntimeError):
    """The parent's deadline fired or the child's CPU limit did."""


class OCRResource(RuntimeError):
    """The child ran out of memory on this file."""


class OCRLowConfidence(RuntimeError):
    """Zero boxes or mean confidence under the floor."""


class OCRConfigError(ValueError):
    """A protected path overlaps a sandbox bind, or the child command is unparsable."""


@dataclass
class OcrResult:
    extracted: pdf.Extracted = field(repr=False)
    mean_confidence: float
    boxes: int
    empty_pages: int


def service_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def interpreter() -> str:
    return os.path.realpath(sys.executable)


def package_dirs() -> list[str]:
    """site-packages (purelib) plus platlib when it differs (lib64 layouts)."""
    paths = sysconfig.get_paths()
    out = [os.path.realpath(paths["purelib"])]
    plat = os.path.realpath(paths["platlib"])
    if plat != out[0]:
        out.append(plat)
    return out


def site_packages() -> str:
    return package_dirs()[0]


def runtime_binds() -> list[tuple[str, str]]:
    """(source, target) read-only binds: the OS runtime, the interpreter, site-packages, the worker package."""
    binds: list[tuple[str, str]] = []
    for path in ("/usr", "/lib", "/lib64", os.path.realpath(sys.base_prefix), *package_dirs()):
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
    """Paths that must never be readable inside the child: credentials, state, the worker's config directory."""
    paths = [cfg.password_file, cfg.api_token_file, cfg.state_dir, cfg.parser_config_dir, cfg.verify_parser_config_dir,
             cfg.verify_dir, service_dir().parent.parent / ".env", Path("~/.config").expanduser()]
    if cfg.minio_key_file is not None:
        paths.append(cfg.minio_key_file)
    if cfg.account_map_file is not None:
        paths.append(cfg.account_map_file)
    return sorted({os.path.realpath(str(p)) for p in paths})


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix.rstrip("/") + "/")


def check_config(cfg: WorkerConfig) -> None:
    """Refuse a layout where a protected path lies under a bind or a bind lies under a protected directory (read-only
    is not confidentiality), and an unparsable child command."""
    if cfg.ocr_child:
        try:
            if not shlex.split(cfg.ocr_child):
                raise OCRConfigError("empty child command")
        except ValueError:
            raise OCRConfigError("unparsable child command") from None
    if not cfg.parser_sandbox:
        return
    for secret in protected_paths(cfg):
        for src, _dst in runtime_binds():
            if _under(secret, src) or _under(src, secret):
                raise OCRConfigError("a protected path overlaps a sandbox bind")


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
        self._ready = False

    def reset(self) -> None:
        """Run boundary: a host that was broken may have been fixed between runs, and vice versa."""
        self._unavailable = False
        self._ready = False

    def child_command(self, *selftest: str) -> list[str]:
        if self.cfg.ocr_child:
            return shlex.split(self.cfg.ocr_child) + list(selftest)
        return [interpreter(), "-B", "-m", "worker.ocr_child", "--threads", str(self.cfg.ocr_threads),
                "--rlimit-as-mb", str(self.cfg.ocr_rlimit_mb), "--rlimit-cpu-s", str(RLIMIT_CPU_S), *selftest]

    def argv(self, *selftest: str) -> list[str]:
        return (sandbox_prefix() if self.cfg.parser_sandbox else []) + self.child_command(*selftest)

    def env(self) -> dict[str, str]:
        work = "/work" if self.cfg.parser_sandbox else str(service_dir())
        threads = str(self.cfg.ocr_threads)
        return {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1", "PYTHONPATH": ":".join([work, *package_dirs()]),
                "OMP_NUM_THREADS": threads, "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "MALLOC_ARENA_MAX": "2"}

    def preflight(self) -> None:
        """Once per run: the child with --selftest must start inside the sandbox, load the engine and report
        `{"ready": true}` as exactly one terminated line. A readiness or start failure (sandbox, imports, engine, an
        invalid or non-ready response, a crash) latches `unavailable` until `reset()`. Two outcomes do not latch
        (AGENT-118 ruling on Must 1b): a self-test that reports `resource` → OCRResource, and an observed deadline or
        SIGXCPU → OCRTimeout; the next call runs the self-test again."""
        if self._unavailable:
            raise OCRUnavailable("ocr unavailable")
        if self._ready:
            return
        outcome, payload = self._exchange(b"", self.argv("--selftest"), self.cfg.ocr_timeout_s)
        if outcome == "timeout":
            raise OCRTimeout("ocr timed out")
        if outcome == "ok":
            verdict = _selftest_verdict(payload)
            if verdict == "ready":
                self._ready = True
                return
            if verdict == "resource":
                raise OCRResource("ocr out of memory")
        self._unavailable = True
        raise OCRUnavailable("ocr unavailable")

    def read(self, data: bytes, password: str | None) -> OcrResult:
        """Call from a long-lived thread (the worker's main thread, as the parser does): bwrap's --die-with-parent
        is tied to the thread that spawned it, so a short-lived caller thread would tear the child down itself."""
        self.preflight()
        self.starts += 1
        outcome, payload = self._exchange(_header(password, len(data)) + data, self.argv(), self.cfg.ocr_timeout_s)
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

    def _exchange(self, stdin: bytes, argv: list[str], timeout_s: float) -> tuple[str, bytes]:
        """(outcome, stdout) with outcome in ok | timeout | malformed | start_failed. Never raises for child
        behaviour; an unexpected error in the parent kills the child before propagating."""
        env = self.env()
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    env=env, start_new_session=True)
        except OSError:
            return "start_failed", b""
        self.last_pgid = proc.pid
        out = bytearray()
        outcome = "ok"
        writer = threading.Thread(target=_write_stdin, args=(proc, stdin), daemon=True)
        try:
            deadline = time.monotonic() + timeout_s
            writer.start()
            with selectors.DefaultSelector() as sel:
                sel.register(proc.stdout, selectors.EVENT_READ)
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
            self.last_stdout_bytes = len(out)
            if outcome == "ok":
                try:
                    proc.wait(timeout=max(0.0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    outcome = "timeout"
        except BaseException:
            _kill(proc)
            _close_stdout(proc)
            writer.join(timeout=1)
            raise
        _kill(proc)  # every path: the leader if still alive, then any straggler in its group; reaped with a bound
        _close_stdout(proc)
        writer.join(timeout=1)
        if outcome != "ok":
            return outcome, b""
        rc = proc.returncode
        if rc != 0:
            sig = -rc if rc < 0 else (rc - 128 if rc > 128 else None)
            return ("timeout" if sig in TIMEOUT_SIGNALS else "malformed"), b""
        return "ok", bytes(out)


def _write_stdin(proc: subprocess.Popen, data: bytes) -> None:
    """The writer thread owns stdin: it writes, then closes; the main thread never touches the pipe."""
    try:
        proc.stdin.write(data)
    except (BrokenPipeError, OSError, ValueError):
        pass  # the child may exit early (unavailable) without reading the PDF
    try:
        proc.stdin.close()
    except (BrokenPipeError, OSError, ValueError):
        pass


def _kill(proc: subprocess.Popen) -> None:
    """SIGKILL the child's session (bwrap and everything under it via --die-with-parent), then reap with a bound. A
    group that already died yields ProcessLookupError; a group with stragglers keeps its id alive, so it cannot be
    recycled under us."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=KILL_WAIT_S)
    except subprocess.TimeoutExpired:
        pass


def _close_stdout(proc: subprocess.Popen) -> None:
    try:
        proc.stdout.close()
    except (OSError, ValueError):
        pass


def _selftest_verdict(payload: bytes) -> str:
    """'ready' for exactly one terminated line `{"ready": true}` (the key set exact, the value the boolean True),
    'resource' for a well-formed resource response, otherwise 'invalid'. Never raises."""
    try:
        if len(payload) > STDOUT_CAP or payload.count(b"\n") != 1 or not payload.endswith(b"\n"):
            return "invalid"
        obj = json.loads(payload[:-1].decode("utf-8"))
        if isinstance(obj, dict) and set(obj) == {"ready"} and obj["ready"] is True:
            return "ready"
        if isinstance(obj, dict) and set(obj) == ERR_KEYS and obj["ok"] is False and obj["code"] == "resource":
            return "resource"
        return "invalid"
    except (UnicodeDecodeError, ValueError, RecursionError, OverflowError):
        return "invalid"


def _number(value, lo: float, hi: float) -> float | None:
    """A finite int/float (never bool) within [lo, hi]; huge ints are rejected before any float conversion."""
    if type(value) is int:
        return float(value) if lo <= value <= hi else None
    if type(value) is float:
        return value if math.isfinite(value) and lo <= value <= hi else None
    return None


def validate(payload: bytes) -> dict | None:
    """Exactly one newline-terminated JSON object line with exactly the expected keys, types and ranges, and the
    child's own invariants re-checked (a successful result has boxes, confidence at or above the floor, a non-empty
    text and at least one non-empty page). Otherwise None. Never raises."""
    try:
        if len(payload) > STDOUT_CAP or payload.count(b"\n") != 1 or not payload.endswith(b"\n"):
            return None
        obj = json.loads(payload[:-1].decode("utf-8"))
        if not isinstance(obj, dict):
            return None
        ok = obj.get("ok")
        if ok is True and set(obj) == OK_KEYS:
            pages, empty, boxes, text = obj["pages"], obj["empty_pages"], obj["boxes"], obj["text"]
            if not all(type(v) is int for v in (pages, empty, boxes)):
                return None
            if not (1 <= pages <= MAX_PAGES) or not (0 <= empty < pages) or boxes < 1 or not isinstance(text, str) or not text.strip():
                return None
            conf = _number(obj["mean_confidence"], CONFIDENCE_FLOOR, 1.0)
            if conf is None:
                return None
            return {"ok": True, "pages": pages, "empty_pages": empty, "boxes": boxes, "mean_confidence": conf, "text": text}
        if ok is False and set(obj) == ERR_KEYS:
            code = obj["code"]
            if isinstance(code, str) and code in CODES:
                return {"ok": False, "code": code}
        return None
    except (UnicodeDecodeError, ValueError, RecursionError, OverflowError):
        return None
