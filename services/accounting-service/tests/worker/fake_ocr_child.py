"""Stand-ins for the OCR child used by the parent tests: shell scripts run unsandboxed through STATEMENT_OCR_CHILD.
The parent runs `<child> --selftest` once per run before any file; a stand-in answers that with {"ready": true}
unless `selftest` says otherwise."""
import json
import shlex
from pathlib import Path


def write(path: Path, response, *, sleep: float = 0, exit_code: int = 0, flood: bool = False, grandchild: bool = False,
          dump_env: bool = False, record_header_to: Path | None = None, record_stdin_to: Path | None = None,
          signal: int | None = None, selftest: str = "ok", count_to: Path | None = None) -> Path:
    """`selftest`: "ok" → ready line; "fail" → exit 1 with no output; "unavailable" → the unavailable code;
    "garbage" → a non-protocol line. `count_to`: append one line per invocation (argv) for call counting."""
    lines = ["#!/bin/sh"]
    if count_to is not None:
        lines.append(f"echo \"$@\" >> {shlex.quote(str(count_to))}")
    selftest_body = {"ok": "printf '{\"ready\": true}\\n'; exit 0", "fail": "exit 1",
                     "unavailable": "printf '{\"ok\": false, \"code\": \"unavailable\"}\\n'; exit 0",
                     "resource": "printf '{\"ok\": false, \"code\": \"resource\"}\\n'; exit 0",
                     "sleep": "sleep 30; exit 0", "ready_int": "printf '{\"ready\": 1}\\n'; exit 0",
                     "ready_blank": "printf '\\n{\"ready\": true}\\n\\n'; exit 0",
                     "ready_extra": "printf '{\"ready\": true, \"x\": 1}\\n'; exit 0",
                     "garbage": "printf 'hello\\n'; exit 0"}[selftest]
    lines.append(f'if [ "$1" = "--selftest" ]; then {selftest_body}; fi')
    if record_header_to is not None:
        lines.append(f"IFS= read -r line; printf '%s\\n' \"$line\" > {shlex.quote(str(record_header_to))}")  # byte-wise: nothing past the newline is consumed
    if record_stdin_to is not None:
        lines.append(f"cat > {shlex.quote(str(record_stdin_to))}")
    else:
        lines.append("cat >/dev/null")
    if grandchild:
        lines.append("(trap '' TERM; sleep 300) >/dev/null 2>&1 &")  # a straggler that does not hold the protocol pipe
    if sleep:
        lines.append(f"sleep {sleep}")
    if flood:
        lines.append("yes xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")  # never stops
    elif dump_env:
        lines.append("printf '{\"ok\": true, \"pages\": 1, \"empty_pages\": 0, \"boxes\": 1, \"mean_confidence\": 0.9, \"text\": \"'; "
                     "env | tr '\\n' ';' | sed 's/\"/_/g; s/\\\\/_/g'; printf '\"}\\n'")
    elif response is not None:
        body = response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
        lines.append(f"printf '%s\\n' {shlex.quote(body)}")
    if signal is not None:
        lines.append(f"kill -{signal} $$")
    lines.append(f"exit {exit_code}")
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o700)
    return path


def ok(text="MERCHANT 001        1,234", pages=1, boxes=2, mean_confidence=0.93, empty_pages=0) -> dict:
    return {"ok": True, "pages": pages, "empty_pages": empty_pages, "boxes": boxes, "mean_confidence": mean_confidence, "text": text}
