"""R5.7: the frozen constraints file for the worker and verify venvs, generated fail-closed.

    python -m worker.constraints --python .venv/bin/python [--uv /usr/local/bin/uv] [--out requirements-worker-constraints-aarch64-py313.txt]

Every distribution installed in the venv must appear exactly once as an exact `name==version` pin; a URL, VCS,
editable or unpinned requirement, a duplicate, or an installed distribution missing from the freeze output is an
error, and nothing is written. The only exemption is the repository's own editable shared library
(`home-service-shared` at `services/shared-python-lib`). The operator evidence prints the SHA-256 of the file's bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

EXEMPT_NAME = "home-service-shared"           # the repository's own shared library (distribution name)
EXEMPT_PATH = "services/shared-python-lib"    # … and its checkout, as `uv pip freeze` reports the editable install
_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.+!_-]*)$")


class ConstraintsError(ValueError):
    """An unpinned, duplicated or missing distribution; the file is not written."""


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def is_exempt(line: str) -> bool:
    """Exactly the local shared library: its normalized distribution name, or an editable (`-e`) line whose location
    ends with its checkout directory. A similarly named external distribution is not exempt."""
    text = line.strip()
    if normalize(text) == EXEMPT_NAME:
        return True
    if text.startswith("-e "):
        location = text[3:].strip().rstrip("/")
        return location.endswith("/" + EXEMPT_PATH) or location == EXEMPT_PATH
    return False


def build(freeze_lines: list[str], installed: list[str]) -> list[str]:
    """Exact pins, one per installed distribution, sorted. `freeze_lines` is `uv pip freeze` output, `installed` the
    distribution names from `uv pip list`. Raises ConstraintsError on anything that is not an exact pin (URL, VCS,
    editable, unpinned), on duplicates and on installed distributions the freeze did not report."""
    pins: dict[str, str] = {}
    problems: list[str] = []
    for raw in freeze_lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if is_exempt(line):
            continue
        m = _PIN.match(line)
        if not m:
            kind = "editable" if line.startswith("-e") else "url" if " @ " in line else "vcs" if "+" in line.split("==")[0] else "unpinned"
            problems.append(f"{kind}: {line.split()[0][:60]}")
            continue
        name = normalize(m.group(1))
        if name in pins:
            problems.append(f"duplicate: {name}")
            continue
        pins[name] = f"{m.group(1)}=={m.group(2)}"
    wanted = {normalize(n) for n in installed if normalize(n) != EXEMPT_NAME}
    for missing in sorted(wanted - set(pins)):
        problems.append(f"missing from freeze: {missing}")
    for extra in sorted(set(pins) - wanted):
        problems.append(f"not installed: {extra}")
    if problems:
        raise ConstraintsError("; ".join(problems))
    return sorted(pins.values(), key=str.lower)


def render(pins: list[str], *, today: date | None = None) -> str:
    header = [f"# R5: every non-editable distribution of the worker venv on aarch64 / CPython 3.13, frozen {today or date.today()}",
              "# with: python -m worker.constraints --python .venv/bin/python   (fail-closed: exact pins only, one per",
              "# installed distribution; the local editable home-service-shared is the only exemption).",
              "# Deploy installs the worker and verify venvs with -c this file."]
    return "\n".join(header + pins) + "\n"


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="worker.constraints")
    ap.add_argument("--python", required=True)
    ap.add_argument("--uv", default="uv")
    ap.add_argument("--out", default="requirements-worker-constraints-aarch64-py313.txt")
    args = ap.parse_args(argv)
    freeze = subprocess.run([args.uv, "pip", "freeze", "--python", args.python], capture_output=True, text=True, check=True).stdout.splitlines()
    listed = json.loads(subprocess.run([args.uv, "pip", "list", "--python", args.python, "--format", "json"], capture_output=True, text=True, check=True).stdout)
    try:
        pins = build(freeze, [row["name"] for row in listed])
    except ConstraintsError as exc:
        print(f"constraints: {exc}", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.write_text(render(pins), encoding="utf-8")
    print(json.dumps({"file": str(out), "entries": len(pins), "sha256": sha256_of(out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
