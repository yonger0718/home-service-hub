"""Stand-ins for the OCR child used by the parent tests: shell scripts run unsandboxed through STATEMENT_OCR_CHILD."""
import json
import shlex
from pathlib import Path


def write(path: Path, response, *, sleep: float = 0, exit_code: int = 0, flood: bool = False, grandchild: bool = False,
          dump_env: bool = False, record_header_to: Path | None = None, record_stdin_to: Path | None = None,
          signal: int | None = None) -> Path:
    lines = ["#!/bin/sh"]
    if record_header_to is not None:
        lines.append(f"IFS= read -r line; printf '%s\\n' \"$line\" > {shlex.quote(str(record_header_to))}")  # byte-wise: nothing past the newline is consumed
    if record_stdin_to is not None:
        lines.append(f"cat > {shlex.quote(str(record_stdin_to))}")
    else:
        lines.append("cat >/dev/null")
    if grandchild:
        lines.append("(trap '' TERM; sleep 300) &")
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
