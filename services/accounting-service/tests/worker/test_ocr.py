import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time

import pytest

from tests.worker import fake_ocr_child
from tests.worker.pdfgen import make_pdf, statement_lines
from worker import config, ocr, pdf

HAS_BWRAP = shutil.which("bwrap") is not None


def cfg_with(tmp_path, response=None, name="child.sh", timeout="2", **kw):
    child = fake_ocr_child.write(tmp_path / name, response, **kw)
    return config.load({"STATEMENT_OCR_CHILD": str(child), "STATEMENT_PARSER_SANDBOX": "false", "STATEMENT_OCR_TIMEOUT": timeout,
                        "STATEMENT_STATE_DIR": str(tmp_path / "state"), "STATEMENT_PASSWORD_FILE": str(tmp_path / "pw"),
                        "STATEMENT_API_TOKEN_FILE": str(tmp_path / "tok")})


def _session_alive(pgid: int) -> bool:
    out = subprocess.run(["ps", "-eo", "pgid=,args="], capture_output=True, text=True).stdout
    return any(line.split()[0] == str(pgid) for line in out.splitlines() if line.strip())


# ---- result mapping -----------------------------------------------------------------------------------------------

def test_read_ok_maps_to_result(tmp_path):
    out = ocr.Engine(cfg_with(tmp_path, fake_ocr_child.ok(text="A\fB", pages=2, boxes=5, empty_pages=1))).read(b"%PDF", "pw")
    assert out.extracted.text == "A\fB" and out.extracted.pages == 2 and out.extracted.tables == [] and out.extracted.text_chars_page1 == 1
    assert out.boxes == 5 and out.empty_pages == 1 and abs(out.mean_confidence - 0.93) < 1e-9


@pytest.mark.parametrize("code,exc", [("unavailable", ocr.OCRUnavailable), ("resource", ocr.OCRResource), ("too_large", pdf.TooLarge),
                                      ("malformed", pdf.Malformed), ("low_confidence", ocr.OCRLowConfidence)])
def test_read_maps_error_codes(tmp_path, code, exc):
    with pytest.raises(exc) as info:
        ocr.Engine(cfg_with(tmp_path, {"ok": False, "code": code})).read(b"%PDF", None)
    assert info.value.__cause__ is None and info.value.__context__ is None


@pytest.mark.parametrize("response", [
    {"ok": True, "pages": "SECRET-PAGE", "text": "x"},
    {"ok": True, "pages": 1, "empty_pages": 0, "boxes": 1, "mean_confidence": float("nan"), "text": "x"},
    {"ok": True, "pages": 1, "empty_pages": 0, "boxes": 1, "mean_confidence": 10 ** 400, "text": "x"},
    {"ok": True, "pages": 1, "empty_pages": 0, "boxes": 1, "mean_confidence": True, "text": "x"},
    {"ok": True, "pages": 0, "empty_pages": 0, "boxes": 0, "mean_confidence": 0.9, "text": "x"},
    {"ok": True, "pages": 1, "empty_pages": 2, "boxes": 1, "mean_confidence": 0.9, "text": "x"},
    {"ok": True, "pages": 1, "empty_pages": 0, "boxes": 1, "mean_confidence": 0.9, "text": ["not", "str"]},
    {"ok": False, "code": []}, {"ok": False, "code": "weird"}, {"ok": "yes"}, "[1, 2]", "not json", "",
    '{"ok": true, "pages": 1, "empty_pages": 0, "boxes": 1, "mean_confidence": 0.9, "text": "x"}\n{"second": "line"}',
    "[" * 100000 + "]" * 100000])
def test_invalid_responses_are_malformed_without_content(tmp_path, response):
    with pytest.raises(pdf.Malformed) as info:
        ocr.Engine(cfg_with(tmp_path, response)).read(b"%PDF", None)
    assert "SECRET" not in str(info.value) and info.value.__cause__ is None and info.value.__context__ is None


def test_validate_rejects_blank_extra_lines_only_when_they_carry_content():
    good = b'{"ok": false, "code": "too_large"}\n'
    assert ocr.validate(good) == {"ok": False, "code": "too_large"}
    assert ocr.validate(good + b"\n\n") == {"ok": False, "code": "too_large"}   # trailing newlines are tolerated
    assert ocr.validate(good + b" \n") is None                                  # a second non-empty line is not


# ---- protocol bytes -----------------------------------------------------------------------------------------------

def test_header_and_bytes_reach_the_child(tmp_path):
    hdr, body = tmp_path / "hdr", tmp_path / "body"
    child = fake_ocr_child.write(tmp_path / "rec.sh", fake_ocr_child.ok(), record_header_to=hdr, record_stdin_to=body)
    cfg = config.load({"STATEMENT_OCR_CHILD": str(child), "STATEMENT_PARSER_SANDBOX": "false"})
    ocr.Engine(cfg).read(b"%PDF-bytes", "secret")
    assert json.loads(hdr.read_text()) == {"password": "secret", "scale": 3.0, "max_pages": 10, "max_pixels": 30_000_000, "pdf_len": 10}
    assert body.read_bytes() == b"%PDF-bytes"


def test_child_command_carries_limits_and_no_secrets():
    cfg = config.load({"STATEMENT_PARSER_SANDBOX": "false", "STATEMENT_OCR_THREADS": "3", "STATEMENT_OCR_RLIMIT_MB": "777"})
    cmd = ocr.Engine(cfg).child_command()
    assert cmd[:4] == [ocr.interpreter(), "-B", "-m", "worker.ocr_child"]
    assert cmd[4:] == ["--threads", "3", "--rlimit-as-mb", "777", "--rlimit-cpu-s", "150"]


# ---- bounds, termination, classification -------------------------------------------------------------------------

def test_timeout_kills_the_child_and_its_grandchild(tmp_path):
    engine = ocr.Engine(cfg_with(tmp_path, fake_ocr_child.ok(), sleep=30, grandchild=True))
    t0 = time.monotonic()
    with pytest.raises(ocr.OCRTimeout) as info:
        engine.read(b"%PDF", None)
    assert time.monotonic() - t0 < 15 and info.value.__context__ is None
    assert engine.last_pgid and not _session_alive(engine.last_pgid)


def test_stdout_cap_stops_a_flooding_child(tmp_path):
    engine = ocr.Engine(cfg_with(tmp_path, None, flood=True, timeout="20"))
    t0 = time.monotonic()
    with pytest.raises(pdf.Malformed):
        engine.read(b"%PDF", None)
    assert time.monotonic() - t0 < 5 and engine.last_stdout_bytes <= ocr.STDOUT_CAP + ocr.READ_CHUNK
    assert not _session_alive(engine.last_pgid)


def test_exit_classification(tmp_path):
    with pytest.raises(pdf.Malformed):   # non-zero exit (a python exception in the child)
        ocr.Engine(cfg_with(tmp_path, None, exit_code=1)).read(b"%PDF", None)
    with pytest.raises(pdf.Malformed):   # crash signal
        ocr.Engine(cfg_with(tmp_path, None, signal=int(signal.SIGSEGV))).read(b"%PDF", None)
    with pytest.raises(pdf.Malformed):   # an unexplained SIGKILL is not a timeout: nothing observed by the parent
        ocr.Engine(cfg_with(tmp_path, None, signal=int(signal.SIGKILL))).read(b"%PDF", None)
    with pytest.raises(ocr.OCRTimeout):  # CPU limit → retry later
        ocr.Engine(cfg_with(tmp_path, None, signal=int(signal.SIGXCPU))).read(b"%PDF", None)
    with pytest.raises(ocr.OCRTimeout):  # bwrap reports a signalled child as 128+n
        ocr.Engine(cfg_with(tmp_path, None, exit_code=128 + int(signal.SIGXCPU))).read(b"%PDF", None)
    with pytest.raises(pdf.Malformed):
        ocr.Engine(cfg_with(tmp_path, None, exit_code=128 + int(signal.SIGKILL))).read(b"%PDF", None)


def test_unavailable_is_cached_for_the_run_and_reset_at_the_boundary(tmp_path):
    engine = ocr.Engine(cfg_with(tmp_path, {"ok": False, "code": "unavailable"}))
    with pytest.raises(ocr.OCRUnavailable):
        engine.read(b"%PDF", None)
    fake_ocr_child.write(tmp_path / "child.sh", fake_ocr_child.ok())
    with pytest.raises(ocr.OCRUnavailable):
        engine.read(b"%PDF", None)
    assert engine.starts == 1
    engine.reset()
    assert engine.read(b"%PDF", None).extracted.pages == 1 and engine.starts == 2


def test_start_failure_is_unavailable(tmp_path):
    cfg = config.load({"STATEMENT_OCR_CHILD": str(tmp_path / "missing.sh"), "STATEMENT_PARSER_SANDBOX": "false"})
    with pytest.raises(ocr.OCRUnavailable):
        ocr.Engine(cfg).read(b"%PDF", None)


# ---- environment and sandbox --------------------------------------------------------------------------------------

def test_child_env_is_minimal_and_carries_no_parent_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("STATEMENT_VERIFY_DB_URL", "postgresql://u:SECRET-DB-PASSWORD@h/db")
    monkeypatch.setenv("SOME_TOKEN", "SECRET-TOKEN")
    out = ocr.Engine(cfg_with(tmp_path, None, dump_env=True)).read(b"%PDF", None)
    assert "SECRET-DB-PASSWORD" not in out.extracted.text and "SECRET-TOKEN" not in out.extracted.text
    env = dict(kv.split("=", 1) for kv in out.extracted.text.split(";") if "=" in kv)
    assert set(env) <= {"PATH", "HOME", "LANG", "PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE", "PYTHONPATH", "PWD", "SHLVL", "_", "OLDPWD"}
    assert env["HOME"] == "/tmp"


def test_env_sandboxed_and_unsandboxed():
    sb = ocr.Engine(config.load({"STATEMENT_PARSER_SANDBOX": "true"})).env()
    plain = ocr.Engine(config.load({"STATEMENT_PARSER_SANDBOX": "false"})).env()
    assert sb == {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
                  "PYTHONNOUSERSITE": "1", "PYTHONPATH": "/work:" + ocr.site_packages()}
    assert plain["PYTHONPATH"] == f"{ocr.service_dir()}:{ocr.site_packages()}"


def test_argv_sandboxed_no_network_minimal_binds(tmp_path):
    cfg = config.load({"STATEMENT_PARSER_SANDBOX": "true", "STATEMENT_STATE_DIR": str(tmp_path / "state"),
                       "STATEMENT_PARSER_CONFIG_DIR": str(tmp_path / "cfg"), "STATEMENT_PASSWORD_FILE": str(tmp_path / "pw")})
    argv = ocr.Engine(cfg).argv()
    assert argv[0] == "bwrap" and "--unshare-all" in argv and "--share-net" not in argv and "--bind" not in argv
    for flag in ("--die-with-parent", "--new-session", "--cap-drop", "--tmpfs", "--proc", "--dev"):
        assert flag in argv
    binds = [(argv[i + 1], argv[i + 2]) for i, a in enumerate(argv) if a == "--ro-bind"]
    sources = [s for s, _ in binds]
    assert "/usr" in sources and ocr.site_packages() in sources and os.path.realpath(sys.base_prefix) in sources
    assert (str(ocr.service_dir() / "worker"), "/work/worker") in binds
    for forbidden in (str(tmp_path), os.path.expanduser("~"), str(ocr.service_dir()), "/etc", str(ocr.service_dir().parent.parent)):
        assert forbidden not in sources
    assert argv[argv.index("--") + 1] == ocr.interpreter() and argv[-6:-4] == ["--threads", "2"]


def test_argv_unsandboxed_and_override(tmp_path):
    plain = ocr.Engine(config.load({"STATEMENT_PARSER_SANDBOX": "false"})).argv()
    assert plain[:4] == [ocr.interpreter(), "-B", "-m", "worker.ocr_child"]
    assert ocr.Engine(cfg_with(tmp_path, fake_ocr_child.ok())).argv()[0].endswith("child.sh")


def test_config_overlapping_a_bind_is_refused():
    inside = os.path.join(ocr.site_packages(), "secret-token")  # a protected path under a bound prefix
    with pytest.raises(ocr.OCRConfigError):
        ocr.Engine(config.load({"STATEMENT_PARSER_SANDBOX": "true", "STATEMENT_API_TOKEN_FILE": inside}))
    ocr.Engine(config.load({"STATEMENT_PARSER_SANDBOX": "false", "STATEMENT_API_TOKEN_FILE": inside}))  # unsandboxed: no bind


def test_fingerprint_shape():
    assert ocr.FINGERPRINT.count("+") == 2 and ocr.FINGERPRINT.endswith("layout1")


# ---- real bwrap (host evidence; skipped where bubblewrap is absent) -----------------------------------------------

@pytest.mark.skipif(not HAS_BWRAP, reason="bubblewrap required")
def test_sandbox_denies_protected_paths_and_network(tmp_path):
    cfg = config.load({"STATEMENT_PARSER_SANDBOX": "true", "STATEMENT_STATE_DIR": str(tmp_path / "state"),
                       "STATEMENT_PASSWORD_FILE": str(tmp_path / "pw.env"), "STATEMENT_API_TOKEN_FILE": str(tmp_path / "token")})
    (tmp_path / "state").mkdir(); (tmp_path / "pw.env").write_text("secret"); (tmp_path / "token").write_text("secret")
    env_file = ocr.service_dir().parent.parent / ".env"
    probe = ("import os, sys, json, socket\n"
             "res = {p: os.path.exists(p) for p in sys.argv[1:]}\n"
             "try:\n    socket.create_connection(('127.0.0.1', 8000), timeout=1); res['net'] = 'open'\n"
             "except OSError as e: res['net'] = type(e).__name__\n"
             "print(json.dumps(res))")
    paths = ocr.protected_paths(cfg) + [str(env_file), os.path.expanduser("~/.config"), "/etc/passwd"]
    proc = subprocess.run(ocr.sandbox_prefix() + [ocr.interpreter(), "-c", probe, *paths], env=ocr.Engine(cfg).env(),
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-300:]
    seen = json.loads(proc.stdout.strip().splitlines()[-1])
    assert seen.pop("net") != "open"
    assert set(seen.values()) == {False}, seen


@pytest.mark.skipif(not HAS_BWRAP, reason="bubblewrap required")
def test_real_bwrap_teardown_reaps_a_sigterm_ignoring_grandchild(tmp_path):
    marker = f"sleep 300 0.{os.getpid()}"  # GNU sleep sums its arguments; the second one is a unique ps marker
    cmd = f"/usr/bin/sh -c 'trap \"\" TERM; (trap \"\" TERM; {marker}) & {marker}'"
    cfg = config.load({"STATEMENT_PARSER_SANDBOX": "true", "STATEMENT_OCR_CHILD": cmd, "STATEMENT_OCR_TIMEOUT": "2",
                       "STATEMENT_STATE_DIR": str(tmp_path / "state")})
    engine = ocr.Engine(cfg)
    with pytest.raises(ocr.OCRTimeout):
        engine.read(b"%PDF", None)
    time.sleep(0.5)
    survivors = [l for l in subprocess.run(["ps", "-eo", "args="], capture_output=True, text=True).stdout.splitlines()
                 if f"sleep 300 0.{os.getpid()}" in l and "ps -eo" not in l]
    assert survivors == []


@pytest.mark.skipif(not HAS_BWRAP, reason="bubblewrap required")
def test_real_child_in_bwrap_with_a_fake_backend(tmp_path):
    """The real interpreter and worker package inside the sandbox, protocol end to end; the engine itself is faked
    through a tiny driver so the test needs no models."""
    driver = ("import sys; from worker import ocr_child; RAW = [[[[10,10],[200,10],[200,30],[10,30]], 'MERCHANT 001', 0.97]]; "
              "sys.exit(ocr_child.main(sys.argv[1:], backend=lambda img: RAW))")
    cmd = " ".join([ocr.interpreter(), "-B", "-c", shlex.quote(driver), "--threads", "1", "--rlimit-as-mb", "4096", "--rlimit-cpu-s", "60"])
    cfg = config.load({"STATEMENT_PARSER_SANDBOX": "true", "STATEMENT_OCR_CHILD": cmd,
                       "STATEMENT_OCR_TIMEOUT": "60", "STATEMENT_STATE_DIR": str(tmp_path / "state")})
    out = ocr.Engine(cfg).read(make_pdf([statement_lines()]), None)
    assert out.extracted.pages == 1 and "MERCHANT 001" in out.extracted.text and out.boxes == 1
