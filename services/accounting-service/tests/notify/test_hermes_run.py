import os
import signal
import subprocess
from pathlib import Path

import pytest

from notify import hermes_run
from notify.hermes_run import HERMES_ENV_KEYS, HermesError, HermesResult, hermes_argv, run_hermes


class FakeHandle:
    def __init__(self, returncode=0, timeout=False, pid=4242):
        self.pid = pid
        self.returncode = returncode
        self._timeout = timeout
        self.waits = []

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self._timeout:
            raise subprocess.TimeoutExpired("hermes", timeout)
        return self.returncode


def make_runner(out=b"", err=b"", returncode=0, timeout=False, calls=None):
    handle = FakeHandle(returncode, timeout)

    def runner(argv, env, cwd, out_fd, err_fd):
        if calls is not None:
            calls.append((argv, env, cwd))
        os.write(out_fd, out)
        os.write(err_fd, err)
        return handle

    runner.handle = handle
    return runner


def _prompt(tmp_path, text="hello 你好"):
    p = tmp_path / "prompt.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _run(tmp_path, runner, **kw):
    args = dict(prompt_path=_prompt(tmp_path), toolsets="web", timeout_s=30, model="m", provider="p",
                env={"PATH": "/bin"}, cwd=tmp_path, max_output_bytes=1000, archive=tmp_path, runner=runner)
    args.update(kw)
    return run_hermes(**args)


def test_env_keys_pinned():
    assert HERMES_ENV_KEYS == ("PATH", "HOME", "LANG", "TZ")


def test_argv_is_pinned():
    assert hermes_argv("PROMPT", "web", "mod", "prov") == [
        "hermes", "-z", "PROMPT", "-t", "web", "--ignore-user-config", "--ignore-rules",
        "--provider", "prov", "-m", "mod"]


def test_prompt_limit_boundary():
    hermes_argv("a" * 120_000, "web", "m", "p")
    with pytest.raises(ValueError) as ei:
        hermes_argv("a" * 120_001, "web", "m", "p")
    assert "aaaa" not in str(ei.value)


def test_prompt_over_arg_limit_raises():
    with pytest.raises(ValueError):
        hermes_argv("é" * 60_001, "web", "m", "p")


@pytest.mark.parametrize("ts", ["web,browser", "all", "safe", "web, terminal", ""])
def test_toolset_must_be_exactly_web(ts):
    with pytest.raises(ValueError):
        hermes_argv("x", ts, "m", "p")


def test_env_and_cwd_passed_through_unchanged(tmp_path):
    calls = []
    env = {"PATH": "/x", "HOME": "/h"}
    res = _run(tmp_path, make_runner(out=b"ok", err=b"w", calls=calls), env=env)
    argv, got_env, got_cwd = calls[0]
    assert got_env is env and got_cwd == tmp_path
    assert argv[:3] == ["hermes", "-z", "hello 你好"]
    assert isinstance(res, HermesResult)
    assert (res.stdout, res.stderr, res.returncode, res.truncated) == ("ok", "w", 0, False)
    assert res.seconds >= 0
    assert (tmp_path / "hermes.out").stat().st_mode & 0o777 == 0o600


def test_nonzero_exit_raises_reason_only(tmp_path):
    with pytest.raises(HermesError) as ei:
        _run(tmp_path, make_runner(out=b"SECRET-OUT", err=b"SECRET-ERR", returncode=3))
    assert ei.value.reason == "hermes" and ei.value.archive == tmp_path
    assert str(ei.value) == f"hermes {tmp_path}"
    assert "SECRET" not in str(ei.value) and "hello" not in str(ei.value)


def test_output_capped_and_fails(tmp_path):
    with pytest.raises(HermesError):
        _run(tmp_path, make_runner(out=b"x" * 11), max_output_bytes=10)
    assert (tmp_path / "hermes.out").read_bytes() == b"x" * 10 + b"\n[truncated]\n"


def test_timeout_kills_group(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "getpgid", lambda pid: pid + 1)
    kills = []
    runner = make_runner(timeout=True)
    with pytest.raises(HermesError) as ei:
        _run(tmp_path, runner, kill=lambda pg, sig: kills.append((pg, sig)))
    assert kills == [(4243, signal.SIGKILL)]
    assert str(ei.value) == f"hermes {tmp_path}"
    assert runner.handle.waits == [30, 5]


def test_default_runner_builds_popen_with_new_session(tmp_path, monkeypatch):
    seen = {}

    def fake_popen(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return "handle"

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    out = os.open(tmp_path / "o", os.O_WRONLY | os.O_CREAT)
    err = os.open(tmp_path / "e", os.O_WRONLY | os.O_CREAT)
    try:
        assert hermes_run._default_runner(["hermes"], {"A": "1"}, tmp_path, out, err) == "handle"
    finally:
        os.close(out)
        os.close(err)
    kw = seen["kw"]
    assert kw["start_new_session"] is True and kw["stdin"] == subprocess.DEVNULL
    assert kw["env"] == {"A": "1"} and kw["cwd"] == tmp_path
    assert kw["stdout"] == out and kw["stderr"] == err


def test_failed_start_is_hermes_error_and_fds_closed(tmp_path):
    fds = []

    def runner(argv, env, cwd, out_fd, err_fd):
        fds.extend([out_fd, err_fd])
        raise FileNotFoundError("hermes")

    with pytest.raises(HermesError) as ei:
        _run(tmp_path, runner)
    assert str(ei.value) == f"hermes {tmp_path}"
    assert len(fds) == 2
    for fd in fds:
        with pytest.raises(OSError):
            os.fstat(fd)


def test_unreadable_prompt_is_hermes_error(tmp_path):
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"\xff\xfe\xfa")
    with pytest.raises(HermesError) as ei:
        _run(tmp_path, make_runner(), prompt_path=bad)
    assert str(ei.value) == f"hermes {tmp_path}"
    with pytest.raises(HermesError):
        _run(tmp_path, make_runner(), prompt_path=tmp_path / "missing.txt")


def test_timeout_with_vanished_group_still_hermes_error(tmp_path, monkeypatch):
    def gone(pid):
        raise ProcessLookupError

    monkeypatch.setattr(os, "getpgid", gone)
    with pytest.raises(HermesError):
        _run(tmp_path, make_runner(timeout=True), kill=lambda pg, sig: None)


def test_stderr_overflow_truncated_and_fails(tmp_path):
    with pytest.raises(HermesError):
        _run(tmp_path, make_runner(err=b"y" * 11), max_output_bytes=10)
    assert (tmp_path / "hermes.err").read_bytes() == b"y" * 10 + b"\n[truncated]\n"


def test_invalid_utf8_replaced_and_empty_stdout(tmp_path):
    res = _run(tmp_path, make_runner(out=b"a\xffb"))
    assert res.stdout == "a�b"
    assert _run(tmp_path, make_runner()).stdout == ""


def test_oversized_prompt_is_hermes_error_and_runner_never_called(tmp_path):
    calls = []
    p = tmp_path / "big.txt"
    p.write_bytes(b"x" * 120_001)
    with pytest.raises(HermesError):
        _run(tmp_path, make_runner(calls=calls), prompt_path=p)
    assert calls == []
