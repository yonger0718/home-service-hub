"""AGENT-124 Must 3: an invalid STATEMENT_PARSER_CLI_VERSION / STATEMENT_PARSER_MODEL exits cleanly from every command."""
import os
import subprocess
import sys

import pytest

SERVICE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.mark.parametrize("command", [["run"], ["verify"], ["export-masked", "--for-verify"], ["poll"]])
@pytest.mark.parametrize("key,value", [("STATEMENT_PARSER_MODEL", "bad/model"), ("STATEMENT_PARSER_CLI_VERSION", "2.1.296 beta")])
def test_invalid_config_exits_2_without_a_traceback(tmp_path, command, key, value):
    env = {k: v for k, v in os.environ.items() if not k.startswith("STATEMENT_")}
    env.update({key: value, "STATEMENT_STATE_DIR": str(tmp_path), "PYTHONPATH": SERVICE})
    proc = subprocess.run([sys.executable, "-m", "worker", *command], cwd=SERVICE, env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 2, proc.stderr[-500:]
    assert "Traceback" not in proc.stderr and "config: " in proc.stderr and key in proc.stderr
    assert value not in proc.stderr and value not in proc.stdout  # the value is never echoed
