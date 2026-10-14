"""R5.7: the fail-closed constraints generator (AGENT-124 Must 4)."""
import hashlib
import json
import subprocess
import sys

import pytest

from worker import constraints

INSTALLED = ["numpy", "onnxruntime", "pypdfium2", "rapidocr-onnxruntime", "home-service-shared"]
FREEZE = ["numpy==2.5.4", "onnxruntime==1.31.0", "pypdfium2==5.14.0", "rapidocr-onnxruntime==1.4.4",
          "-e file:///home/x/services/shared-python-lib"]


def test_exact_pins_one_per_installed_distribution():
    pins = constraints.build(FREEZE, INSTALLED)
    assert pins == ["numpy==2.5.4", "onnxruntime==1.31.0", "pypdfium2==5.14.0", "rapidocr-onnxruntime==1.4.4"]
    text = constraints.render(pins)
    assert text.startswith("# R5:") and text.endswith("rapidocr-onnxruntime==1.4.4\n") and text.count("==") == 4


@pytest.mark.parametrize("bad,word", [
    ("pkg @ https://example.invalid/pkg-1.0-py3-none-any.whl", "url"),
    ("git+https://example.invalid/pkg.git@abc#egg=pkg", "vcs"),
    ("-e file:///home/x/other-editable", "editable"),
    ("pkg>=1.0", "unpinned"),
    ("pkg", "unpinned"),
])
def test_non_exact_requirements_fail_closed(bad, word):
    with pytest.raises(constraints.ConstraintsError, match=word):
        constraints.build(FREEZE + [bad], INSTALLED + ["pkg"])


def test_duplicate_and_omission_fail_closed():
    with pytest.raises(constraints.ConstraintsError, match="duplicate: numpy"):
        constraints.build(FREEZE + ["NumPy==2.5.4"], INSTALLED)
    with pytest.raises(constraints.ConstraintsError, match="missing from freeze: pillow"):
        constraints.build(FREEZE, INSTALLED + ["pillow"])
    with pytest.raises(constraints.ConstraintsError, match="not installed: pillow"):
        constraints.build(FREEZE + ["pillow==12.3.0"], INSTALLED)


def test_only_the_local_shared_package_is_exempt():
    assert constraints.build(FREEZE, INSTALLED) == constraints.build(FREEZE[:-1], INSTALLED[:-1])
    assert constraints.is_exempt("-e file:///x/services/shared-python-lib") and constraints.is_exempt("home-service-shared")
    assert not constraints.is_exempt("-e file:///x/services/other-lib")


def test_committed_file_is_exactly_what_the_generator_produces_from_this_venv(tmp_path):
    """The committed constraints equal a fresh fail-closed generation from the test venv (the deploy set)."""
    uv = subprocess.run(["sh", "-c", "command -v uv || ls /usr/local/bin/uv ~/.local/bin/uv 2>/dev/null | head -1"], capture_output=True, text=True).stdout.strip()
    if not uv:
        pytest.skip("uv not available")
    out = tmp_path / "c.txt"
    rc = constraints.main(["--python", sys.executable, "--uv", uv, "--out", str(out)])
    assert rc == 0
    committed = [l for l in open("requirements-worker-constraints-aarch64-py313.txt", encoding="utf-8") if "==" in l]
    fresh = [l for l in out.read_text(encoding="utf-8").splitlines(keepends=True) if "==" in l]
    assert committed == fresh
    assert constraints.sha256_of(out) == hashlib.sha256(out.read_bytes()).hexdigest()
