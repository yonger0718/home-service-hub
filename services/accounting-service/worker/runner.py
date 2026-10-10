"""One seam for every external program (rclone, bwrap, claude): tests inject a FakeRunner."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Callable, Protocol


@dataclass(frozen=True)
class Result:
    returncode: int
    stdout: bytes
    stderr: bytes


class Runner(Protocol):
    def run(self, args: list[str], *, stdin: bytes | None = None, timeout: float | None = None,
            env: dict | None = None) -> Result: ...


class _Finished:
    """What FakeRunner.start returns: a process that already ended."""

    def __init__(self, returncode: int):
        self.returncode = returncode

    def wait(self, timeout=None) -> int:
        return self.returncode

    def poll(self) -> int:
        return self.returncode

    def kill(self) -> None:
        return None


class SubprocessRunner:
    def start(self, args):
        """Start a long-running child (own session, output discarded) for callers that watch it themselves."""
        return subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)

    def run(self, args, *, stdin=None, timeout=None, env=None) -> Result:
        try:
            done = subprocess.run(args, input=stdin, capture_output=True, timeout=timeout, env=env,
                                  start_new_session=True, check=False)
        except subprocess.TimeoutExpired as exc:
            return Result(-1, exc.stdout or b"", (exc.stderr or b"") + b"\ntimeout")
        except FileNotFoundError:
            return Result(127, b"", b"not found")
        except OSError:  # PermissionError, NotADirectoryError, IsADirectoryError, ...: cannot launch
            return Result(126, b"", b"cannot execute")
        return Result(done.returncode, done.stdout, done.stderr)


class FakeRunner:
    def __init__(self, handlers: dict[str, Callable[[list[str], bytes | None], Result]]):
        self.handlers = handlers
        self.calls: list[list[str]] = []

    def start(self, args):
        self.calls.append(list(args))
        return _Finished(self.handlers[args[0]](list(args), None).returncode)

    def run(self, args, *, stdin=None, timeout=None, env=None) -> Result:
        self.calls.append(list(args))
        return self.handlers[args[0]](list(args), stdin)
