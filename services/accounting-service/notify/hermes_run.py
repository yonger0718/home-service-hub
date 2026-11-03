"""Pinned Hermes one-shot runner: exact argv, capped file output, no message text in errors."""
from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

HERMES_ENV_KEYS: tuple[str, ...] = ("PATH", "HOME", "LANG", "TZ")  # Task 1 probe: no provider vars
MAX_PROMPT_BYTES = 120_000  # Linux MAX_ARG_STRLEN is 131072
TRUNCATED_MARKER = b"\n[truncated]\n"


class HermesError(Exception):
    """Timeout, non-zero exit or output overflow; carries the archive path only, never output text."""

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


# no RLIMIT_FSIZE; the timeout bounds a runaway writer
def _default_runner(argv, env, cwd, out_fd, err_fd):
    return subprocess.Popen(argv, env=env, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out_fd,
                            stderr=err_fd, start_new_session=True)


def _read_capped(path: Path, limit: int) -> tuple[str, bool]:
    size = path.stat().st_size
    over = size > limit
    if over:
        with open(path, "r+b") as f:
            f.truncate(limit)
            f.seek(limit)
            f.write(TRUNCATED_MARKER)
    return path.read_bytes().decode("utf-8", errors="replace"), over


def run_hermes(prompt_path: Path, toolsets: str, timeout_s: int, model: str, provider: str,
               env: dict[str, str], cwd: Path, max_output_bytes: int, archive: Path,
               runner: Callable | None = None, kill: Callable = os.killpg) -> HermesResult:
    try:
        prompt = Path(prompt_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise HermesError(archive) from None
    argv = hermes_argv(prompt, toolsets, model, provider)
    out_path, err_path = archive / "hermes.out", archive / "hermes.err"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    out_fd = os.open(out_path, flags, 0o600)
    try:
        err_fd = os.open(err_path, flags, 0o600)
        try:
            start = time.monotonic()
            try:
                handle = (runner or _default_runner)(argv, env, cwd, out_fd, err_fd)
            except OSError:
                raise HermesError(archive) from None
        finally:
            os.close(err_fd)
    finally:
        os.close(out_fd)
    try:
        handle.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            kill(os.getpgid(handle.pid), signal.SIGKILL)
        try:
            handle.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        raise HermesError(archive) from None
    seconds = time.monotonic() - start
    stdout, out_over = _read_capped(out_path, max_output_bytes)
    stderr, err_over = _read_capped(err_path, max_output_bytes)
    if out_over or err_over or handle.returncode != 0:
        raise HermesError(archive)
    return HermesResult(stdout, stderr, handle.returncode, seconds, False)
