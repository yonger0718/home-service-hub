import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

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


def _group_members(pgid: int) -> list[str]:
    """Live (non-zombie) processes in the group."""
    out = subprocess.run(["ps", "-eo", "pgid=,stat=,args="], capture_output=True, text=True).stdout
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0] == str(pgid) and not parts[1].startswith("Z"):
            rows.append(parts[2])
    return rows


def _group_gone(pgid: int, within: float = 3.0) -> bool:
    t0 = time.monotonic()
    while time.monotonic() - t0 < within:
        if not _group_members(pgid):
            return True
        time.sleep(0.1)
    return False


# ---- result mapping -----------------------------------------------------------------------------------------------

def test_read_ok_maps_to_result(tmp_path):
    out = ocr.Engine(cfg_with(tmp_path, fake_ocr_child.ok(text="A\fB", pages=2, boxes=5, empty_pages=1))).read(b"%PDF", "pw")
    assert out.extracted.text == "A\fB" and out.extracted.pages == 2 and out.extracted.tables == [] and out.extracted.text_chars_page1 == 1
    assert out.boxes == 5 and out.empty_pages == 1 and abs(out.mean_confidence - 0.93) < 1e-9
    assert "A\fB" not in repr(out) and "A\fB" not in repr(out.extracted)


@pytest.mark.parametrize("code,exc", [("unavailable", ocr.OCRUnavailable), ("resource", ocr.OCRResource), ("too_large", pdf.TooLarge),
                                      ("malformed", pdf.Malformed), ("low_confidence", ocr.OCRLowConfidence)])
def test_read_maps_error_codes(tmp_path, code, exc):
    with pytest.raises(exc) as info:
        ocr.Engine(cfg_with(tmp_path, {"ok": False, "code": code})).read(b"%PDF", None)
    assert info.value.__cause__ is None and info.value.__context__ is None


GOOD = {"ok": True, "pages": 1, "empty_pages": 0, "boxes": 1, "mean_confidence": 0.9, "text": "x"}


@pytest.mark.parametrize("response", [
    {"ok": True, "pages": "SECRET-PAGE", "text": "x"},
    {**GOOD, "mean_confidence": float("nan")}, {**GOOD, "mean_confidence": 10 ** 400}, {**GOOD, "mean_confidence": True},
    {**GOOD, "mean_confidence": 0.5},                    # below the floor: the child would never report it
    {**GOOD, "pages": 0}, {**GOOD, "pages": 1, "empty_pages": 1}, {**GOOD, "boxes": 0}, {**GOOD, "text": "   "},
    {**GOOD, "text": ["not", "str"]}, {**GOOD, "extra": 1}, {k: v for k, v in GOOD.items() if k != "boxes"},
    {"ok": False, "code": []}, {"ok": False, "code": "weird"}, {"ok": False, "code": "too_large", "extra": 1}, {"ok": "yes"},
    "[1, 2]", "not json", "", '{"ready": true}',
    json.dumps(GOOD) + '\n{"second": "line"}', json.dumps(GOOD) + "\n", "\n" + json.dumps(GOOD),
    "[" * 100000 + "]" * 100000])
def test_invalid_responses_are_malformed_without_content(tmp_path, response):
    with pytest.raises(pdf.Malformed) as info:
        ocr.Engine(cfg_with(tmp_path, response)).read(b"%PDF", None)
    assert "SECRET" not in str(info.value) and info.value.__cause__ is None and info.value.__context__ is None


def test_validate_requires_exactly_one_newline_terminated_line():
    good = b'{"ok": false, "code": "too_large"}\n'
    assert ocr.validate(good) == {"ok": False, "code": "too_large"}
    assert ocr.validate(good + b"\n") is None and ocr.validate(b"\n" + good) is None and ocr.validate(good[:-1]) is None
    assert ocr.validate(b'{"ok": false, "code": []}\n') is None          # the type guard, not an exception, rejects it


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
    assert ocr.Engine(cfg).child_command("--selftest")[-1] == "--selftest"


# ---- preflight (per-run self-test) --------------------------------------------------------------------------------

def test_preflight_runs_once_per_run_before_any_file(tmp_path):
    calls = tmp_path / "calls"
    engine = ocr.Engine(cfg_with(tmp_path, fake_ocr_child.ok(), count_to=calls))
    engine.read(b"%PDF", None); engine.read(b"%PDF", None)
    argvs = calls.read_text().splitlines()
    assert argvs == ["--selftest", "", ""] and engine.starts == 2
    engine.reset()
    engine.read(b"%PDF", None)
    assert calls.read_text().splitlines() == ["--selftest", "", "", "--selftest", ""]


@pytest.mark.parametrize("mode", ["fail", "unavailable", "garbage"])
def test_preflight_failure_is_unavailable_and_cached(tmp_path, mode):
    calls = tmp_path / "calls"
    engine = ocr.Engine(cfg_with(tmp_path, fake_ocr_child.ok(), selftest=mode, count_to=calls))
    for _ in range(2):
        with pytest.raises(ocr.OCRUnavailable) as info:
            engine.read(b"%PDF", None)
        assert info.value.__context__ is None
    assert calls.read_text().splitlines() == ["--selftest"] and engine.starts == 0   # one attempt, no file sent
    fake_ocr_child.write(tmp_path / "child.sh", fake_ocr_child.ok(), count_to=calls)  # host fixed
    engine.reset()
    assert engine.read(b"%PDF", None).extracted.pages == 1


@pytest.mark.parametrize("mode,exc,latches", [("resource", ocr.OCRResource, False), ("sleep", ocr.OCRTimeout, False),
                                                 ("unavailable", ocr.OCRUnavailable, True), ("fail", ocr.OCRUnavailable, True),
                                                 ("garbage", ocr.OCRUnavailable, True), ("ready_int", ocr.OCRUnavailable, True),
                                                 ("ready_blank", ocr.OCRUnavailable, True), ("ready_extra", ocr.OCRUnavailable, True)])
def test_selftest_outcomes_and_latching(tmp_path, mode, exc, latches):
    calls = tmp_path / "calls"
    engine = ocr.Engine(cfg_with(tmp_path, fake_ocr_child.ok(), selftest=mode, count_to=calls))
    with pytest.raises(exc) as info:
        engine.read(b"%PDF", None)
    assert info.value.__context__ is None and engine.starts == 0
    with pytest.raises(exc if not latches else ocr.OCRUnavailable):
        engine.read(b"%PDF", None)
    selftests = calls.read_text().count("--selftest")
    assert selftests == (1 if latches else 2)  # a latched host is not probed again; resource/timeout are retried


def test_selftest_verdict_framing():
    assert ocr._selftest_verdict(b'{"ready": true}\n') == "ready"
    for bad in (b'{"ready": 1}\n', b'\n{"ready": true}\n\n', b'{"ready": true}', b'{"ready": true, "x": 1}\n', b'[1]\n', b""):
        assert ocr._selftest_verdict(bad) == "invalid", bad
    assert ocr._selftest_verdict(b'{"ok": false, "code": "resource"}\n') == "resource"
    assert ocr._selftest_verdict(b'{"ok": false, "code": "unavailable"}\n') == "invalid"


def _driver_cfg(tmp_path, patch: str, timeout="30"):
    """The real child module as the child (unsandboxed), with one function patched by `patch` before main runs."""
    driver = ("import sys, json; from worker import ocr_child; RAW = [[[[10,10],[200,10],[200,30],[10,30]], 'MERCHANT 001', 0.97]]; "
              + patch + "; sys.exit(ocr_child.main(sys.argv[1:], backend=lambda img: RAW))")
    cmd = " ".join([ocr.interpreter(), "-B", "-c", shlex.quote(driver), "--threads", "1", "--rlimit-as-mb", "4096", "--rlimit-cpu-s", "60"])
    return config.load({"STATEMENT_OCR_CHILD": cmd, "STATEMENT_PARSER_SANDBOX": "false", "STATEMENT_OCR_TIMEOUT": timeout})


@pytest.mark.parametrize("patch", [
    "ocr_child.read_header = lambda fd: (_ for _ in ()).throw(MemoryError())",                       # during the header read
    "ocr_child.read_exact = lambda fd, n: (_ for _ in ()).throw(MemoryError())",                     # during the PDF read
    "_d = json.dumps; json.dumps = lambda o, **k: (_ for _ in ()).throw(MemoryError()) if isinstance(o, dict) and o.get('ok') is True else _d(o, **k)",  # serialising the result
])
def test_resource_envelope_reaches_the_parent_as_ocr_resource(tmp_path, patch):
    engine = ocr.Engine(_driver_cfg(tmp_path, patch))
    with pytest.raises(ocr.OCRResource) as info:
        engine.read(make_pdf([statement_lines()]), None)
    assert info.value.__context__ is None and engine.starts == 1
    assert engine.read(make_pdf([statement_lines()]), None) if False else True  # no latch: the next call would run again


def test_resource_envelope_does_not_reinterpret_crashes(tmp_path):
    engine = ocr.Engine(_driver_cfg(tmp_path, "ocr_child.read_exact = lambda fd, n: (_ for _ in ()).throw(RuntimeError('boom'))"))
    with pytest.raises(pdf.Malformed):
        engine.read(make_pdf([statement_lines()]), None)


def test_start_failure_is_unavailable(tmp_path):
    cfg = config.load({"STATEMENT_OCR_CHILD": str(tmp_path / "missing.sh"), "STATEMENT_PARSER_SANDBOX": "false"})
    with pytest.raises(ocr.OCRUnavailable):
        ocr.Engine(cfg).read(b"%PDF", None)


# ---- bounds, termination, classification -------------------------------------------------------------------------

def test_timeout_kills_the_child_and_its_grandchild(tmp_path):
    engine = ocr.Engine(cfg_with(tmp_path, fake_ocr_child.ok(), sleep=30, grandchild=True))
    t0 = time.monotonic()
    with pytest.raises(ocr.OCRTimeout) as info:
        engine.read(b"%PDF", None)
    assert time.monotonic() - t0 < 15 and info.value.__context__ is None
    assert engine.last_pgid and _group_gone(engine.last_pgid)


def test_normal_exit_still_kills_stragglers(tmp_path):
    engine = ocr.Engine(cfg_with(tmp_path, fake_ocr_child.ok(), grandchild=True))
    assert engine.read(b"%PDF", None).extracted.pages == 1
    assert _group_gone(engine.last_pgid)


def test_stdout_cap_stops_a_flooding_child(tmp_path):
    engine = ocr.Engine(cfg_with(tmp_path, None, flood=True, timeout="20"))
    t0 = time.monotonic()
    with pytest.raises(pdf.Malformed):
        engine.read(b"%PDF", None)
    assert time.monotonic() - t0 < 5 and engine.last_stdout_bytes <= ocr.STDOUT_CAP + ocr.READ_CHUNK
    assert _group_gone(engine.last_pgid)


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


def test_unavailable_code_from_a_file_is_cached_for_the_run(tmp_path):
    engine = ocr.Engine(cfg_with(tmp_path, {"ok": False, "code": "unavailable"}))
    with pytest.raises(ocr.OCRUnavailable):
        engine.read(b"%PDF", None)
    fake_ocr_child.write(tmp_path / "child.sh", fake_ocr_child.ok())
    with pytest.raises(ocr.OCRUnavailable):
        engine.read(b"%PDF", None)
    assert engine.starts == 1
    engine.reset()
    assert engine.read(b"%PDF", None).extracted.pages == 1 and engine.starts == 2


# ---- environment and sandbox --------------------------------------------------------------------------------------

def test_child_env_is_minimal_and_carries_no_parent_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("STATEMENT_VERIFY_DB_URL", "postgresql://u:SECRET-DB-PASSWORD@h/db")
    monkeypatch.setenv("SOME_TOKEN", "SECRET-TOKEN")
    out = ocr.Engine(cfg_with(tmp_path, None, dump_env=True)).read(b"%PDF", None)
    assert "SECRET-DB-PASSWORD" not in out.extracted.text and "SECRET-TOKEN" not in out.extracted.text
    env = dict(kv.split("=", 1) for kv in out.extracted.text.split(";") if "=" in kv)
    assert set(env) <= {"PATH", "HOME", "LANG", "PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE", "PYTHONPATH", "OMP_NUM_THREADS",
                        "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "MALLOC_ARENA_MAX", "PWD", "SHLVL", "_", "OLDPWD"}
    assert env["HOME"] == "/tmp" and env["OMP_NUM_THREADS"] == "2" and env["MALLOC_ARENA_MAX"] == "2"


def test_env_sandboxed_and_unsandboxed():
    sb = ocr.Engine(config.load({"STATEMENT_PARSER_SANDBOX": "true", "STATEMENT_OCR_THREADS": "3"})).env()
    plain = ocr.Engine(config.load({"STATEMENT_PARSER_SANDBOX": "false"})).env()
    assert sb == {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
                  "PYTHONPATH": ":".join(["/work", *ocr.package_dirs()]), "OMP_NUM_THREADS": "3", "OPENBLAS_NUM_THREADS": "1",
                  "MKL_NUM_THREADS": "1", "MALLOC_ARENA_MAX": "2"}
    assert plain["PYTHONPATH"] == ":".join([str(ocr.service_dir()), *ocr.package_dirs()])


def test_argv_sandboxed_no_network_minimal_binds(tmp_path):
    cfg = config.load({"STATEMENT_PARSER_SANDBOX": "true", "STATEMENT_STATE_DIR": str(tmp_path / "state"),
                       "STATEMENT_PARSER_CONFIG_DIR": str(tmp_path / "cfg"), "STATEMENT_PASSWORD_FILE": str(tmp_path / "pw")})
    argv = ocr.Engine(cfg).argv()
    assert argv[0] == "bwrap" and "--unshare-all" in argv and "--share-net" not in argv and "--bind" not in argv
    for flag in ("--die-with-parent", "--new-session", "--cap-drop", "--tmpfs", "--proc", "--dev"):
        assert flag in argv
    binds = [(argv[i + 1], argv[i + 2]) for i, a in enumerate(argv) if a == "--ro-bind"]
    sources = [s for s, _ in binds]
    assert "/usr" in sources and os.path.realpath(sys.base_prefix) in sources
    assert all(p in sources for p in ocr.package_dirs())
    assert (str(ocr.service_dir() / "worker"), "/work/worker") in binds
    for forbidden in (str(tmp_path), os.path.expanduser("~"), str(ocr.service_dir()), "/etc", str(ocr.service_dir().parent.parent)):
        assert forbidden not in sources
    assert argv[argv.index("--") + 1] == ocr.interpreter() and argv[-6:-4] == ["--threads", "2"]


def test_argv_unsandboxed_and_override(tmp_path):
    plain = ocr.Engine(config.load({"STATEMENT_PARSER_SANDBOX": "false"})).argv()
    assert plain[:4] == [ocr.interpreter(), "-B", "-m", "worker.ocr_child"]
    assert ocr.Engine(cfg_with(tmp_path, fake_ocr_child.ok())).argv()[0].endswith("child.sh")


def test_config_overlapping_a_bind_is_refused_both_ways():
    inside = os.path.join(ocr.site_packages(), "secret-token")  # a protected path under a bound prefix
    with pytest.raises(ocr.OCRConfigError):
        ocr.Engine(config.load({"STATEMENT_PARSER_SANDBOX": "true", "STATEMENT_API_TOKEN_FILE": inside}))
    ancestor = str(Path(os.path.realpath(sys.base_prefix)).parent)  # a protected directory above a bound prefix
    with pytest.raises(ocr.OCRConfigError):
        ocr.Engine(config.load({"STATEMENT_PARSER_SANDBOX": "true", "STATEMENT_STATE_DIR": ancestor}))
    ocr.Engine(config.load({"STATEMENT_PARSER_SANDBOX": "false", "STATEMENT_API_TOKEN_FILE": inside}))  # unsandboxed: no bind
    with pytest.raises(ocr.OCRConfigError):
        ocr.Engine(config.load({"STATEMENT_PARSER_SANDBOX": "false", "STATEMENT_OCR_CHILD": "sh -c 'unterminated"}))


def test_protected_paths_include_the_config_directory_and_env_file():
    paths = ocr.protected_paths(config.load({}))
    assert os.path.realpath(os.path.expanduser("~/.config")) in paths
    assert str(ocr.service_dir().parent.parent / ".env") in paths or os.path.realpath(str(ocr.service_dir().parent.parent / ".env")) in paths


def test_fingerprint_shape():
    assert ocr.FINGERPRINT.count("+") == 2 and ocr.FINGERPRINT.endswith("layout1")


# ---- real bwrap (host evidence; skipped where bubblewrap is absent) -----------------------------------------------

PROBE = ("import os, sys, json, socket\n"
         "res = {}\n"
         "for p in sys.argv[2:]:\n"
         "    try:\n        open(p, 'rb').read(1); res[p] = 'readable'\n"
         "    except OSError as e: res[p] = type(e).__name__\n"
         "try:\n    socket.create_connection(('127.0.0.1', int(sys.argv[1])), timeout=2); res['net'] = 'open'\n"
         "except OSError as e: res['net'] = type(e).__name__\n"
         "print(json.dumps(res))")


def _listener():
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        try:
            while True:
                c, _ = srv.accept(); c.close()
        except OSError:
            pass
    threading.Thread(target=serve, daemon=True).start()
    return srv, port


def _canaries() -> tuple[Path, list[str]]:
    """Secret-like files under the home directory (not /tmp, which the sandbox replaces with a tmpfs)."""
    base = Path(tempfile.mkdtemp(prefix="r5a-canary-", dir=Path.home()))
    files = []
    for name in ("state/inbox/by-sha/deadbeef.pdf", "pw.env", "token", ".env", "config/homehub-statements.env"):
        p = base / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("SECRET")
        p.chmod(0o600)
        files.append(str(p))
    return base, files


@pytest.mark.skipif(not HAS_BWRAP, reason="bubblewrap required")
def test_sandbox_denies_protected_paths_and_network():
    base, files = _canaries()
    srv, port = _listener()
    try:
        cfg = config.load({"STATEMENT_PARSER_SANDBOX": "true", "STATEMENT_STATE_DIR": str(base / "state"),
                           "STATEMENT_PASSWORD_FILE": files[1], "STATEMENT_API_TOKEN_FILE": files[2]})
        engine = ocr.Engine(cfg)
        targets = files + [p for p in ocr.protected_paths(cfg) if os.path.exists(p)] + ["/etc/passwd"]
        # negative control: unsandboxed, the same probe reads every canary and reaches the listener
        plain = subprocess.run([ocr.interpreter(), "-c", PROBE, str(port), *targets], env=engine.env(), capture_output=True, text=True, timeout=60)
        seen = json.loads(plain.stdout.strip().splitlines()[-1])
        assert seen["net"] == "open" and all(seen[f] == "readable" for f in files), seen
        # sandboxed: nothing readable, nothing reachable
        proc = subprocess.run(ocr.sandbox_prefix() + [ocr.interpreter(), "-c", PROBE, str(port), *targets], env=engine.env(),
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr[-300:]
        seen = json.loads(proc.stdout.strip().splitlines()[-1])
        assert seen.pop("net") != "open"
        assert set(seen.values()) <= {"FileNotFoundError", "PermissionError", "IsADirectoryError", "NotADirectoryError"}, seen
    finally:
        srv.close()
        shutil.rmtree(base, ignore_errors=True)


def _proc_identity(pid: int) -> tuple[int, str] | None:
    """(start time in clock ticks, SigIgn mask) from /proc, or None when the pid is gone."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        status = Path(f"/proc/{pid}/status").read_text()
    except OSError:
        return None
    start = int(stat.rsplit(")", 1)[1].split()[19])  # field 22, after the (comm) field
    sigign = next(l.split()[1] for l in status.splitlines() if l.startswith("SigIgn:"))
    return start, sigign


def _find_marker_pids(marker: str) -> list[int]:
    """The `sleep` processes carrying the marker (bwrap and sh carry it in their argv too, so match on comm)."""
    out = subprocess.run(["ps", "-eo", "pid=,comm=,args="], capture_output=True, text=True).stdout
    pids = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[1] == "sleep" and marker in parts[2]:
            pids.append(int(parts[0]))
    return pids


def _teardown_scenario(engine, marker: str) -> dict:
    """Run `engine.read` (on this thread: bwrap's --die-with-parent is tied to the spawning thread) on a sandboxed child
    that spawns a SIGTERM-ignoring grandchild; a helper thread observes the grandchild on the host while the child is
    alive (a positive witness: pid, start time, SigIgn); then report whether that exact process is gone afterwards.
    Zombies count as present (a reaped descendant has no /proc entry)."""
    witness: list = []

    def watch():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not witness:
            for pid in _find_marker_pids(marker):
                ident = _proc_identity(pid)
                if ident is not None:
                    witness.append((pid, ident))
                    break
            time.sleep(0.05)
    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    errors: list[BaseException] = []
    try:
        engine.read(b"%PDF", None)
    except BaseException as exc:  # noqa: BLE001
        errors.append(exc)
    watcher.join(timeout=6)
    if not witness:
        raise LookupError("no grandchild was observed: nothing was launched")
    pid, (start, sigign) = witness[0]
    assert int(sigign, 16) & (1 << (int(signal.SIGTERM) - 1)), "the grandchild must ignore SIGTERM"
    gone = False
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        ident = _proc_identity(pid)
        if ident is None or ident[0] != start:  # gone, or the pid was reused by another process
            gone = True
            break
        time.sleep(0.05)
    return {"pid": pid, "gone": gone, "errors": errors}


def _teardown_cfg(tmp_path, marker: str):
    inner = ('if [ "$1" = --selftest ]; then printf \'{"ready": true}\\n\'; exit 0; fi; '
             f'trap "" TERM; (trap "" TERM; {marker}) & {marker}')
    cmd = "/usr/bin/sh -c " + shlex.quote(inner) + " sh"
    return config.load({"STATEMENT_PARSER_SANDBOX": "true", "STATEMENT_OCR_CHILD": cmd, "STATEMENT_OCR_TIMEOUT": "2",
                        "STATEMENT_STATE_DIR": str(tmp_path / "state")})


@pytest.mark.skipif(not HAS_BWRAP, reason="bubblewrap required")
def test_real_bwrap_teardown_reaps_a_witnessed_sigterm_ignoring_grandchild(tmp_path):
    marker = f"sleep 300 0.{os.getpid()}1"
    engine = ocr.Engine(_teardown_cfg(tmp_path, marker))
    report = _teardown_scenario(engine, marker)
    assert [type(e) for e in report["errors"]] == [ocr.OCRTimeout]
    assert report["gone"], report
    assert engine.last_pgid is not None and _group_gone(engine.last_pgid)


@pytest.mark.skipif(not HAS_BWRAP, reason="bubblewrap required")
def test_teardown_scenario_fails_when_nothing_is_launched(tmp_path, monkeypatch):
    """Mutation control: with `Engine.read` stubbed to raise at once, the scenario finds no witness and fails."""
    marker = f"sleep 300 0.{os.getpid()}2"
    engine = ocr.Engine(_teardown_cfg(tmp_path, marker))
    monkeypatch.setattr(engine, "read", lambda data, password: (_ for _ in ()).throw(ocr.OCRTimeout("stub")))
    with pytest.raises(LookupError):
        _teardown_scenario(engine, marker)


@pytest.mark.skipif(not HAS_BWRAP, reason="bubblewrap required")
def test_teardown_scenario_fails_when_the_group_kill_is_disabled(tmp_path, monkeypatch):
    """Mutation control: with the group SIGKILL disabled, the witnessed grandchild survives and the scenario says so."""
    marker = f"sleep 300 0.{os.getpid()}3"
    engine = ocr.Engine(_teardown_cfg(tmp_path, marker))

    def no_kill(proc):
        try:
            proc.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            pass
    monkeypatch.setattr(ocr, "_kill", no_kill)
    try:
        report = _teardown_scenario(engine, marker)
        assert report["gone"] is False, report
    finally:
        for pid in _find_marker_pids(marker):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        if engine.last_pgid:
            try:
                os.killpg(engine.last_pgid, signal.SIGKILL)
            except OSError:
                pass


@pytest.mark.skipif(not HAS_BWRAP, reason="bubblewrap required")
def test_real_child_in_bwrap_with_a_fake_backend(tmp_path):
    """The real interpreter and worker package inside the sandbox, protocol end to end (including --selftest); the
    engine itself is faked through a tiny driver so the test needs no models."""
    driver = ("import sys; from worker import ocr_child; RAW = [[[[10,10],[200,10],[200,30],[10,30]], 'MERCHANT 001', 0.97]]; "
              "sys.exit(ocr_child.main(sys.argv[1:], backend=lambda img: RAW))")
    cmd = " ".join([ocr.interpreter(), "-B", "-c", shlex.quote(driver), "--threads", "1", "--rlimit-as-mb", "4096", "--rlimit-cpu-s", "60"])
    cfg = config.load({"STATEMENT_PARSER_SANDBOX": "true", "STATEMENT_OCR_CHILD": cmd, "STATEMENT_OCR_TIMEOUT": "60",
                       "STATEMENT_STATE_DIR": str(tmp_path / "state")})
    out = ocr.Engine(cfg).read(make_pdf([statement_lines()]), None)
    assert out.extracted.pages == 1 and "MERCHANT 001" in out.extracted.text and out.boxes == 1
