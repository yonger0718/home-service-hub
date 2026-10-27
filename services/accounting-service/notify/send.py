"""Delivery through `hermes send`: one process per chunk, in order. Nothing else lives here."""
from __future__ import annotations

import subprocess
from collections.abc import Callable


class SendError(Exception):
    """hermes could not be started or exited non-zero; carries no message text."""


def _run_hermes(argv: list[str], text: str) -> int:
    try:
        return subprocess.run(argv, input=text, text=True, check=False, timeout=60).returncode
    except (OSError, subprocess.SubprocessError):
        return -1


def deliver(messages: list[str], target: str, runner: Callable[[list[str], str], int] | None = None) -> None:
    run = runner or _run_hermes  # resolved at call time so tests can replace _run_hermes
    for text in messages:
        if run(["hermes", "send", "-t", target, "-q"], text) != 0:
            raise SendError("hermes send failed")
