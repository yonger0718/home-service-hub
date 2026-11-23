"""Pinned Hermes one-shot runner: exact argv, output capped while it runs, no message text in errors."""
from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

HERMES_ENV_KEYS: tuple[str, ...] = ("PATH", "HOME", "LANG", "TZ")  # Task 1 probe: no provider vars
MAX_PROMPT_BYTES = 120_000  # Linux MAX_ARG_STRLEN is 131072
_DRAIN_SECONDS = 2.0  # how long a finished run waits for its pipes to close (a grandchild may hold one)
_POLL_SECONDS = 0.2  # how soon the wait loop notices a stream that passed the cap
TRUNCATED_MARKER = b"\n[truncated]\n"


class HermesError(Exception):
    """Timeout, non-zero exit, output overflow or an output file that cannot be written; carries the archive
    path only, never output text."""

    reason = "hermes"

    def __init__(self, archive: Path):
        super().__init__(f"hermes {archive}")
        self.archive = archive


@dataclass(frozen=True)
class HermesResult:
    stdout: str
    stderr: str
    returncode: int
    seconds: float
    truncated: bool  # always False in a returned result; overflow raises HermesError — kept for the interface


def hermes_argv(prompt: str, toolsets: str, model: str, provider: str) -> list[str]:
    if {s.strip() for s in toolsets.split(",")} != {"web"}:
        raise ValueError("toolsets must be exactly web")
    if len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
        raise ValueError("prompt exceeds the argument size limit")
    return ["hermes", "-z", prompt, "-t", "web", "--ignore-user-config", "--ignore-rules",
            "--provider", provider, "-m", model]


# No RLIMIT_FSIZE: it would also limit Hermes's own state database. The child writes into pipes instead.
def _default_runner(argv, env, cwd, out_fd, err_fd):
    return subprocess.Popen(argv, env=env, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out_fd,
                            stderr=err_fd, start_new_session=True)


def _close(*fds: int) -> None:
    for fd in fds:
        with contextlib.suppress(OSError):
            os.close(fd)


class _Capped(threading.Thread):
    """Copy a pipe into a file, at most ``limit`` bytes. Past the cap (or when the file cannot be written) the
    stream is marked ``over``, ``on_over`` is called once, and the rest is read and dropped so the child never
    blocks on a full pipe."""

    def __init__(self, read_fd: int, file_fd: int, limit: int, on_over: Callable[[], None]):
        super().__init__(daemon=True)
        self._read_fd, self._file_fd, self._limit, self._on_over = read_fd, file_fd, limit, on_over
        self.over = False

    def _put(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            view = view[os.write(self._file_fd, view):]

    def _mark_over(self, marker: bool) -> None:
        self.over = True
        if marker:
            with contextlib.suppress(OSError):
                self._put(TRUNCATED_MARKER)
        self._on_over()

    def run(self) -> None:
        written = 0
        try:
            while chunk := os.read(self._read_fd, 65536):
                if self.over:
                    continue
                room = self._limit - written
                try:
                    self._put(chunk[:room])
                except OSError:  # disk full, quota: nothing more can be kept
                    self._mark_over(marker=False)
                    continue
                written += min(len(chunk), room)
                if len(chunk) > room:
                    self._mark_over(marker=True)
        except OSError:
            self.over = True
        finally:
            _close(self._read_fd, self._file_fd)


def run_hermes(prompt_path: Path, toolsets: str, timeout_s: int, model: str, provider: str,
               env: dict[str, str], cwd: Path, max_output_bytes: int, archive: Path,
               runner: Callable | None = None, kill: Callable = os.killpg) -> HermesResult:
    try:
        prompt = Path(prompt_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise HermesError(archive) from None
    try:
        argv = hermes_argv(prompt, toolsets, model, provider)
    except ValueError:
        raise HermesError(archive) from None
    out_path, err_path = archive / "hermes.out", archive / "hermes.err"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    opened: list[int] = []
    try:
        out_file = os.open(out_path, flags, 0o600)
        opened.append(out_file)
        err_file = os.open(err_path, flags, 0o600)
        opened.append(err_file)
        out_r, out_w = os.pipe()
        opened += [out_r, out_w]
        err_r, err_w = os.pipe()
        opened += [err_r, err_w]
    except OSError:
        _close(*opened)
        raise HermesError(archive) from None

    over = threading.Event()  # set by a reader: its stream passed the cap or its file cannot be written
    readers = (_Capped(out_r, out_file, max_output_bytes, over.set),
               _Capped(err_r, err_file, max_output_bytes, over.set))
    for r in readers:
        r.start()  # from here each reader owns (and closes) its two fds
    start = time.monotonic()
    try:
        try:
            handle = (runner or _default_runner)(argv, env, cwd, out_w, err_w)
        except OSError:
            raise HermesError(archive) from None
        finally:
            _close(out_w, err_w)  # the child holds its own copies; the readers see EOF when it is gone
        # The kill is sent from this thread only, and only while poll() says the child has not been reaped: after
        # the reap its pid can belong to another process.
        deadline = start + timeout_s
        timed_out = False
        while handle.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            if over.wait(min(_POLL_SECONDS, remaining)):
                break
        if handle.poll() is None:  # over the cap or out of time, and still running
            with contextlib.suppress(OSError):
                kill(os.getpgid(handle.pid), signal.SIGKILL)
            try:
                handle.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        if timed_out:
            raise HermesError(archive)
    finally:
        drain_until = time.monotonic() + _DRAIN_SECONDS
        for r in readers:
            r.join(timeout=max(0.0, drain_until - time.monotonic()))
    seconds = time.monotonic() - start
    if any(r.over for r in readers) or handle.returncode != 0:
        raise HermesError(archive)
    try:
        stdout = out_path.read_bytes().decode("utf-8", errors="replace")
        stderr = err_path.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        raise HermesError(archive) from None
    return HermesResult(stdout, stderr, handle.returncode, seconds, False)
