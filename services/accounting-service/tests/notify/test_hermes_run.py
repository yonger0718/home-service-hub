import contextlib
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

from notify import hermes_run
from notify.hermes_run import HERMES_ENV_KEYS, HermesError, HermesResult, hermes_argv, run_hermes


class FakeHandle:
    """``timeout=True``: a child that never exits (``poll`` stays None, ``wait`` times out)."""

    def __init__(self, returncode=0, timeout=False, pid=4242):
        self.pid = pid
        self.returncode = None if timeout else returncode
        self._timeout = timeout
        self.waits = []

    def poll(self):
        return self.returncode

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
                env={"PATH": "/bin"}, cwd=tmp_path, max_output_bytes=1000, archive=tmp_path, runner=runner,
                kill=lambda pg, sig: None)  # a fake handle's pid must never reach the real os.killpg
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
    start = time.monotonic()
    with pytest.raises(HermesError) as ei:
        _run(tmp_path, runner, timeout_s=0.4, kill=lambda pg, sig: kills.append((pg, sig)))
    assert 0.4 <= time.monotonic() - start < 3
    assert kills == [(4243, signal.SIGKILL)]
    assert str(ei.value) == f"hermes {tmp_path}"
    assert runner.handle.waits == [5]  # only the reap after the kill


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
        _run(tmp_path, make_runner(timeout=True), timeout_s=0.3, kill=lambda pg, sig: None)


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


# --- #286: the cap holds while the child runs ---

def sh_runner(script: str, handles: list | None = None):
    """A real child in its own session, wired to the fds run_hermes hands over."""
    def runner(argv, env, cwd, out_fd, err_fd):
        h = subprocess.Popen(["sh", "-c", script], stdin=subprocess.DEVNULL, stdout=out_fd, stderr=err_fd,
                             start_new_session=True)
        if handles is not None:
            handles.append(h)
        return h
    return runner


def test_overflowing_child_is_killed_and_file_stays_at_the_cap(tmp_path):
    handles: list = []
    start = time.monotonic()
    with pytest.raises(HermesError):
        _run(tmp_path, sh_runner("yes aaaaaaa | head -c 5000000; sleep 8", handles), timeout_s=60, kill=os.killpg)
    assert time.monotonic() - start < 5  # killed at the cap, not waited out
    assert handles[0].returncode == -signal.SIGKILL
    data = (tmp_path / "hermes.out").read_bytes()
    assert data == (b"aaaaaaa\n" * 125)[:1000] + b"\n[truncated]\n"


def test_overflow_on_stderr_kills_too(tmp_path):
    handles: list = []
    with pytest.raises(HermesError):
        _run(tmp_path, sh_runner("yes eeeeeee | head -c 5000000 >&2; sleep 8", handles), timeout_s=60,
             kill=os.killpg)
    assert handles[0].returncode == -signal.SIGKILL
    assert (tmp_path / "hermes.err").stat().st_size == 1000 + len(b"\n[truncated]\n")


def test_overflow_kills_a_child_that_is_still_running(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "getpgid", lambda pid: pid + 1)
    kills = []
    runner = make_runner(out=b"x" * 11, timeout=True)
    start = time.monotonic()
    with pytest.raises(HermesError):
        _run(tmp_path, runner, max_output_bytes=10, timeout_s=30, kill=lambda pg, sig: kills.append((pg, sig)))
    assert kills == [(4243, signal.SIGKILL)] and time.monotonic() - start < 5  # at the cap, not at the timeout


def test_overflow_never_signals_a_child_that_already_exited(tmp_path, monkeypatch):
    """Once the child is reaped its pid can belong to someone else: no kill, the run still fails."""
    monkeypatch.setattr(os, "getpgid", lambda pid: pid + 1)
    kills = []
    with pytest.raises(HermesError):
        _run(tmp_path, make_runner(out=b"x" * 11), max_output_bytes=10, kill=lambda pg, sig: kills.append((pg, sig)))
    assert kills == []
    assert (tmp_path / "hermes.out").read_bytes() == b"x" * 10 + b"\n[truncated]\n"


def test_timed_out_run_leaves_capped_files(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "getpgid", lambda pid: pid + 1)
    with pytest.raises(HermesError):
        _run(tmp_path, make_runner(out=b"x" * 11, err=b"y" * 3, timeout=True), max_output_bytes=10, timeout_s=0.3)
    assert (tmp_path / "hermes.out").read_bytes() == b"x" * 10 + b"\n[truncated]\n"
    assert (tmp_path / "hermes.err").read_bytes() == b"yyy"


def test_grandchild_holding_the_pipe_does_not_hang(tmp_path, monkeypatch):
    monkeypatch.setattr(hermes_run, "_DRAIN_SECONDS", 0.3)
    handles: list = []
    start = time.monotonic()
    try:
        res = _run(tmp_path, sh_runner("(sleep 20 &); echo done", handles), timeout_s=60, kill=os.killpg)
        assert res.stdout == "done\n" and time.monotonic() - start < 10
    finally:
        with contextlib.suppress(OSError):
            os.killpg(handles[0].pid, signal.SIGKILL)  # our own session: the stray sleep


def test_output_larger_than_the_pipe_buffer_is_read_whole(tmp_path):
    res = _run(tmp_path, sh_runner("yes abcdefg | head -c 300000"), max_output_bytes=1_000_000, kill=os.killpg)
    assert len(res.stdout) == 300000 and res.stdout.endswith("abcdefg\n")


def test_real_child_that_times_out_is_killed(tmp_path):
    handles: list = []
    start = time.monotonic()
    with pytest.raises(HermesError):
        _run(tmp_path, sh_runner("echo partial; sleep 30", handles), timeout_s=0.5, kill=os.killpg)
    assert time.monotonic() - start < 10 and handles[0].returncode == -signal.SIGKILL
    assert (tmp_path / "hermes.out").read_bytes() == b"partial\n"


def test_reader_marks_the_stream_over_when_the_file_cannot_be_written(tmp_path):
    r, w = os.pipe()
    target = tmp_path / "ro"
    target.write_bytes(b"")
    file_fd = os.open(target, os.O_RDONLY)  # a write to it fails (EBADF), like a full disk would
    called = []
    reader = hermes_run._Capped(r, file_fd, 100, lambda: called.append(1))
    reader.start()
    os.write(w, b"data")
    os.close(w)
    reader.join(timeout=5)
    assert not reader.is_alive() and reader.over and called == [1]


def test_real_child_output_and_exit_code(tmp_path):
    res = _run(tmp_path, sh_runner("printf out; printf err >&2"), kill=os.killpg)
    assert (res.stdout, res.stderr, res.returncode) == ("out", "err", 0)
    assert oct((tmp_path / "hermes.out").stat().st_mode & 0o777) == "0o600"
    with pytest.raises(HermesError):
        _run(tmp_path, sh_runner("exit 3"), kill=os.killpg)


def test_unopenable_output_file_is_hermes_error(tmp_path):
    (tmp_path / "hermes.err").mkdir()
    calls = []
    with pytest.raises(HermesError) as ei:
        _run(tmp_path, make_runner(calls=calls))
    assert str(ei.value) == f"hermes {tmp_path}" and calls == []


def test_reader_thread_overrides_nothing_of_thread_but_run():
    """Thread has private methods of its own (``_stop`` before 3.13 is called by ``join``): a reader that
    redefines one breaks on those versions."""
    import threading

    own = {n for n in vars(hermes_run._Capped) if not (n.startswith("__") and n.endswith("__"))}
    clash = sorted(n for n in own - {"run"} if hasattr(threading.Thread, n))
    py311 = {"_stop", "_started", "_is_stopped", "_tstate_lock", "_delete", "_bootstrap", "_bootstrap_inner",
             "_set_ident", "_set_native_id", "_set_tstate_lock", "_wait_for_tstate_lock", "_reset_internal_locks"}
    assert clash == [] and not own & py311
