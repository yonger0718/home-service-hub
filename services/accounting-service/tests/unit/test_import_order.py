"""Every settlement module imports cleanly as the FIRST import of a fresh interpreter (issue #247).

The app imports `reconciliation_service` before `trade_settlement_service`, which hid a cycle:
`trade_settlement_service` -> `reconciliation_actions` (package) -> `settlement_actions`, which used to read
`tss.TRANSFER_NAME` at import time while `trade_settlement_service` was still initialising. Standalone scripts
(`scripts/r6_case_dump.py`, the rollout dry runs) import the settlement service first and crashed with
`AttributeError: partially initialized module`. Each entry point runs in its own interpreter so the order is really
"first", not whatever pytest imported earlier.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[2]
SHARED_LIB = SERVICE_ROOT.parent / "shared-python-lib"

ENTRY_POINTS = [
    "app.services.trade_settlement_service",
    "app.services.reconciliation_actions.settlement_actions",
    "app.services.reconciliation_actions",
    "app.services.reconciliation_service",
    "app.services.stand_ins",
]


@pytest.mark.parametrize("module", ENTRY_POINTS)
def test_settlement_modules_import_first_in_a_fresh_interpreter(module: str) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(SERVICE_ROOT), str(SHARED_LIB)] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"], cwd=SERVICE_ROOT, env=env, capture_output=True, text=True,
        timeout=120, check=False)
    assert result.returncode == 0, f"import {module} failed as the first import:\n{result.stderr[-2000:]}"
