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


class SubprocessRunner:
    def run(self, args, *, stdin=None, timeout=None, env=None) -> Result:
        try:
            done = subprocess.run(args, input=stdin, capture_output=True, timeout=timeout, env=env,
                                  start_new_session=True, check=False)
        except subprocess.TimeoutExpired as exc:
            return Result(-1, exc.stdout or b"", (exc.stderr or b"") + b"\ntimeout")
        return Result(done.returncode, done.stdout, done.stderr)


class FakeRunner:
    def __init__(self, handlers: dict[str, Callable[[list[str], bytes | None], Result]]):
        self.handlers = handlers
        self.calls: list[list[str]] = []

    def run(self, args, *, stdin=None, timeout=None, env=None) -> Result:
        self.calls.append(list(args))
        return self.handlers[args[0]](list(args), stdin)
