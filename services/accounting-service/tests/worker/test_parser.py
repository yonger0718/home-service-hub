import json
import os
import time

import pytest

from tests.worker import fake_claude
from worker import config, parse_schema, parser
from worker.runner import SubprocessRunner


@pytest.fixture
def cfg(tmp_path):
    cli = fake_claude.write(tmp_path / "claude", fake_claude.transcript())
    return config.load({"STATEMENT_STATE_DIR": str(tmp_path / "state"), "STATEMENT_PARSER_CLI": str(cli),
                        "STATEMENT_PARSER_SANDBOX": "false", "STATEMENT_PARSER_ALLOW_UNSANDBOXED": "true", "STATEMENT_PARSER_TIMEOUT": "5",
                        "STATEMENT_PARSER_ATTEMPTS": "1"})


def test_schema_is_strict_and_amounts_are_decimal_strings():
    schema = parse_schema.json_schema()
    assert schema["additionalProperties"] is False
    with pytest.raises(ValueError):
        parse_schema.StatementParse.model_validate({**fake_claude.GOOD, "statement_total": "5,80"})
    with pytest.raises(ValueError):
        parse_schema.StatementParse.model_validate({**fake_claude.GOOD, "extra": 1})
    assert len(parse_schema.StatementParse.model_validate(fake_claude.GOOD).lines) == 2


def test_command_without_sandbox_has_the_pinned_flags(cfg):
    argv = parser.command(cfg, "{}")
    assert argv[0] == str(cfg.parser_cli) and argv[1:4] == ["-p", "--safe-mode", "--tools"]
    for flag in ("--disallowedTools", "--strict-mcp-config", "--permission-prompts", "--no-session-persistence",
                 "--max-turns", "--output-format", "--verbose", "--json-schema", "--model"):
        assert flag in argv
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5-5" and argv[-1] == parse_schema.INSTRUCTION


def test_command_with_sandbox_binds_only_the_stated_surfaces(tmp_path):
    cli = tmp_path / "bin" / "claude"
    cfg = config.load({"STATEMENT_PARSER_SANDBOX": "true", "STATEMENT_PARSER_CONFIG_DIR": str(tmp_path / "cfg"),
                       "STATEMENT_PARSER_CLI": str(cli)})
    argv = parser.command(cfg, "{}")
    assert argv[0] == "bwrap" and "--unshare-all" in argv and "--share-net" in argv and "--cap-drop" in argv
    assert argv[argv.index("--chdir") + 1] == "/work"
    i = argv.index("/opt/claude/claude")
    assert argv[i - 2:i + 1] == ["--ro-bind", str(cli), "/opt/claude/claude"]
    assert argv[argv.index("--bind") + 1] == str(tmp_path / "cfg" / ".credentials.json")
    host_paths = [a for a in argv if a.startswith("/") and not a.startswith(str(tmp_path))]
    assert all(p.startswith(("/usr", "/lib", "/etc/pki", "/etc/ssl", "/etc/resolv.conf", "/etc/hosts", "/opt/claude",
                             "/cfg", "/tmp", "/work", "/proc", "/dev")) for p in host_paths), host_paths
    env_pairs = [(argv[i + 1], argv[i + 2]) for i, a in enumerate(argv) if a == "--setenv"]
    assert dict(env_pairs) == {"HOME": "/tmp", "CLAUDE_CONFIG_DIR": "/cfg", "PATH": "/opt/claude:/usr/bin"}
    ro = parser.command(cfg, "{}", config_dir=tmp_path / "ro", credentials_writable=False)
    assert "--bind" not in ro and ["--ro-bind", str(tmp_path / "ro"), "/cfg"] == ro[ro.index("/cfg") - 2:ro.index("/cfg") + 1]


def test_parse_happy_path(cfg):
    out = parser.Parser(cfg, SubprocessRunner()).parse("masked text")
    assert out.statement_total == "580" and out.lines[1].merchant_raw == "BOOKS"


def test_version_pin(cfg, tmp_path):
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(), version="2.1.295 (Claude Code)")
    with pytest.raises(parser.ParseError) as err:
        parser.Parser(cfg, SubprocessRunner()).parse("x")
    assert err.value.reason == "cli_version"


@pytest.mark.parametrize("variant, reason", [
    ({"extra_tool": True}, "sandbox"), ({"tool_name": "Bash"}, "sandbox"), ({"tools": ("StructuredOutput", "Bash")}, "sandbox"),
    ({"num_turns": 3}, "sandbox"), ({"hook_event": True}, "sandbox"), ({"duplicate_result": True}, "sandbox"),
    ({"garbage_line": True}, "sandbox"), ({"extra_tool": True, "exit_code": 1}, "sandbox"),
    ({"message_not_object": True}, "sandbox"), ({"user_extra_block": True}, "sandbox"),
    ({"tool_result_id_mismatch": True}, "sandbox"), ({"assistant_extra_tool_use_after_text": True}, "sandbox"),
    ({"extra_tool": True, "sleep_after": 30}, "sandbox"), ({"extra_tool": True, "flood_after": True}, "sandbox"),
    ({"tool_name": "Bash", "sleep_after": 30}, "sandbox"),
    ({"tools": ("StructuredOutput", "Bash"), "sleep_after": 30}, "sandbox"),
    ({"tool_name": "Bash", "flood_after": True}, "sandbox"),
    ({"deep_json_line": True}, "sandbox"), ({"tools": 5}, "sandbox"), ({"tools": {"StructuredOutput": 1}}, "sandbox"),
    ({"no_ids": True}, "sandbox"),
    ({"other_system_subtype": True}, "sandbox"), ({"assistant_after_user": True}, "sandbox"),
    ({"thinking_prelude": True, "assistant_extra_tool_use_after_text": True}, "sandbox"),
    ({"subtype": "error_max_turns"}, "exit"), ({"exit_code": 1}, "exit"),
    ({"output": {**fake_claude.GOOD, "statement_total": "abc"}}, "schema")])
def test_envelope_violations_and_precedence(cfg, tmp_path, variant, reason):
    exit_code = variant.pop("exit_code", 0)
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(**variant), exit_code=exit_code)
    with pytest.raises(parser.ParseError) as err:
        parser.Parser(cfg, SubprocessRunner()).parse("x")
    assert err.value.reason == reason


def test_timeout_kills_the_whole_process_group(cfg, tmp_path):
    marker = tmp_path / "grandchild.pid"
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(), sleep=30, grandchild_pid_file=marker)
    cfg = config.load({"STATEMENT_PARSER_CLI": str(tmp_path / "claude"), "STATEMENT_PARSER_SANDBOX": "false", "STATEMENT_PARSER_ALLOW_UNSANDBOXED": "true",
                       "STATEMENT_PARSER_TIMEOUT": "1", "STATEMENT_PARSER_ATTEMPTS": "1",
                       "STATEMENT_STATE_DIR": str(tmp_path / "state")})
    with pytest.raises(parser.ParseError) as err:
        parser.Parser(cfg, SubprocessRunner()).parse("x")
    assert err.value.reason == "timeout"
    pid = int(marker.read_text())
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)  # the grandchild died with the group


def test_child_that_closes_pipes_but_stays_alive_is_a_timeout(cfg, tmp_path):
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(), close_pipes_then_sleep=30)
    cfg = config.load({"STATEMENT_PARSER_CLI": str(tmp_path / "claude"), "STATEMENT_PARSER_SANDBOX": "false", "STATEMENT_PARSER_ALLOW_UNSANDBOXED": "true",
                       "STATEMENT_PARSER_TIMEOUT": "1", "STATEMENT_PARSER_ATTEMPTS": "1",
                       "STATEMENT_STATE_DIR": str(tmp_path / "state")})
    with pytest.raises(parser.ParseError) as err:
        parser.Parser(cfg, SubprocessRunner()).parse("x")
    assert err.value.reason == "timeout"


def test_child_that_never_reads_stdin_still_times_out(cfg, tmp_path):
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(), sleep=30, read_stdin=False)
    cfg = config.load({"STATEMENT_PARSER_CLI": str(tmp_path / "claude"), "STATEMENT_PARSER_SANDBOX": "false", "STATEMENT_PARSER_ALLOW_UNSANDBOXED": "true",
                       "STATEMENT_PARSER_TIMEOUT": "1", "STATEMENT_PARSER_ATTEMPTS": "1",
                       "STATEMENT_STATE_DIR": str(tmp_path / "state")})
    started = time.monotonic()
    with pytest.raises(parser.ParseError) as err:
        parser.Parser(cfg, SubprocessRunner()).parse("x" * 300_000)  # larger than a pipe buffer
    assert err.value.reason == "timeout" and time.monotonic() - started < 4


def test_stdin_and_output_caps(cfg, tmp_path):
    with pytest.raises(parser.ParseError) as err:
        parser.Parser(cfg, SubprocessRunner()).parse("x" * (parser.STDIN_CAP + 1))
    assert err.value.reason == "stdin_too_large"
    fake_claude.write(tmp_path / "claude", "", stdout_bytes=b"{" + b"x" * (parser.STDOUT_CAP + 1))
    with pytest.raises(parser.ParseError) as err:
        parser.Parser(cfg, SubprocessRunner()).parse("x")
    assert err.value.reason == "output_too_large"
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(), stderr_bytes=b"e" * (parser.STDOUT_CAP + 1))
    with pytest.raises(parser.ParseError) as err:
        parser.Parser(cfg, SubprocessRunner()).parse("x")
    assert err.value.reason == "output_too_large"


def test_gate_passes_on_the_canary_and_fails_on_a_second_tool(cfg, tmp_path):
    report = parser.Parser(cfg, SubprocessRunner()).gate()
    assert report.ok and report.evidence["num_turns"] == 2 and report.evidence["tools"] == ["StructuredOutput"]
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(extra_tool=True))
    report = parser.Parser(cfg, SubprocessRunner()).gate()
    assert not report.ok and any("tool" in r for r in report.reasons)


def test_operator_gate_clears_only_when_everything_passes(cfg, tmp_path):
    prs = parser.Parser(cfg, SubprocessRunner())
    gate = parser.Gate(cfg)
    gate.latch("earlier", gate.key(prs))
    good_verify = parser.Parser(cfg, SubprocessRunner(), config_dir=tmp_path / "ro", credentials_writable=False)
    assert gate.run_operator_gate(prs, verify_parser=good_verify).ok and not gate.latch_path.exists()
    missing = gate.run_operator_gate(prs)  # no verify-login canary -> never clears
    assert not missing.ok and "verify login missing" in missing.reasons and gate.latch_path.exists()
    assert json.loads(gate.evidence_path_for(gate.key(prs)).read_text())["evidence"]["checks_ran"]["verify_login"] is False
    assert gate.run_operator_gate(prs, verify_parser=good_verify).ok and not gate.latch_path.exists()
    bad_cli = fake_claude.write(tmp_path / "claude2", fake_claude.transcript(extra_tool=True))
    bad_cfg = config.load({"STATEMENT_PARSER_CLI": str(bad_cli), "STATEMENT_PARSER_SANDBOX": "false", "STATEMENT_PARSER_ALLOW_UNSANDBOXED": "true",
                           "STATEMENT_STATE_DIR": str(cfg.state_dir)})
    bad_verify = parser.Parser(bad_cfg, SubprocessRunner(), config_dir=tmp_path / "ro", credentials_writable=False)
    report = gate.run_operator_gate(prs, verify_parser=bad_verify)
    assert not report.ok and gate.latch_path.exists() and any(r.startswith("verify:") for r in report.reasons)


def test_gate_ensure_caches_and_latches(cfg, tmp_path):
    prs = parser.Parser(cfg, SubprocessRunner())
    gate = parser.Gate(cfg)
    gate.ensure(prs)  # runs the canary once
    assert gate.evidence_path_for(gate.key(prs)).exists()
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(extra_tool=True))
    gate.ensure(prs)  # cached evidence, same key → no new canary, still allowed
    stale = json.loads(gate.evidence_path_for(gate.key(prs)).read_text())
    stale["checked_at"] = "2000-01-01T00:00:00+00:00"
    gate.evidence_path_for(gate.key(prs)).write_text(json.dumps(stale))
    with pytest.raises(parser.ParserDisabled):
        gate.ensure(prs)  # stale → re-run → violation → latch
    assert (cfg.state_dir / "parser-disabled.json").exists()
    fake_claude.write(tmp_path / "claude", fake_claude.transcript())
    with pytest.raises(parser.ParserDisabled):
        gate.ensure(prs)  # latched until an operator gate clears it
    good_verify = parser.Parser(cfg, SubprocessRunner(), config_dir=tmp_path / "ro", credentials_writable=False)
    assert gate.run_operator_gate(prs, verify_parser=good_verify).ok
    assert not (cfg.state_dir / "parser-disabled.json").exists()


def test_parser_version_string():
    v = parse_schema.version("2.1.296", "claude-sonnet-5-5")
    assert v.startswith("claude-cli-2.1.296-claude-sonnet-5-5-") and len(v.split("-")[-1]) == 8


def test_unsandboxed_parser_is_refused_without_the_flag(tmp_path):
    cfg = config.load({"STATEMENT_PARSER_SANDBOX": "false", "STATEMENT_STATE_DIR": str(tmp_path)})
    with pytest.raises(RuntimeError, match="unsandboxed parser is refused"):
        parser.Parser(cfg, SubprocessRunner())


def test_schema_error_does_not_leak_input_values(cfg, tmp_path):
    bad = {**fake_claude.GOOD, "statement_total": "王小明 [NUM…1234]", "secret_key_name": 1}
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(bad))
    with pytest.raises(parser.ParseError) as err:
        parser.Parser(cfg, SubprocessRunner()).parse("x")
    assert err.value.reason == "schema" and "statement_total" in str(err.value)
    assert "王小明" not in str(err.value) and "NUM" not in str(err.value) and "secret_key_name" not in str(err.value)
    assert len(str(err.value)) < 400


def test_sandbox_message_does_not_echo_model_names(cfg, tmp_path):
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(tool_name="EvilTool"))
    with pytest.raises(parser.ParseError) as err:
        parser.Parser(cfg, SubprocessRunner()).parse("x")
    assert err.value.reason == "sandbox" and "EvilTool" not in str(err.value)


class _VersionOnly:
    def run(self, args, *, stdin=None, timeout=None, env=None):
        from worker.runner import Result
        return Result(0, b"2.1.296 (Claude Code)\n", b"")


def test_missing_cli_binary_is_a_gate_failure_and_latches(tmp_path):
    cfg = config.load({"STATEMENT_PARSER_CLI": str(tmp_path / "nope"), "STATEMENT_PARSER_SANDBOX": "false",
                       "STATEMENT_PARSER_ALLOW_UNSANDBOXED": "true", "STATEMENT_STATE_DIR": str(tmp_path / "state")})
    prs = parser.Parser(cfg, _VersionOnly())  # version check passes, Popen raises FileNotFoundError
    report = prs.gate()
    assert not report.ok and report.reasons
    gate = parser.Gate(cfg)
    with pytest.raises(parser.ParserDisabled):
        gate.ensure(prs)
    assert gate.latch_path.exists() and gate.evidence_path_for(gate.key(prs)).exists()


def test_corrupt_or_naive_gate_evidence_is_stale_not_a_crash(cfg):
    prs = parser.Parser(cfg, SubprocessRunner())
    gate = parser.Gate(cfg)
    gate.ensure(prs)
    gate.evidence_path_for(gate.key(prs)).write_text("{not json")
    gate.ensure(prs)
    data = json.loads(gate.evidence_path_for(gate.key(prs)).read_text())
    data["checked_at"] = "2026-10-10T00:00:00"  # naive
    gate.evidence_path_for(gate.key(prs)).write_text(json.dumps(data))
    gate.ensure(prs)
    assert json.loads(gate.evidence_path_for(gate.key(prs)).read_text())["checked_at"].endswith("+00:00")


def test_gate_key_tracks_the_login_dir_but_not_credentials(cfg, tmp_path):
    login = tmp_path / "login"
    login.mkdir()
    prs = parser.Parser(cfg, SubprocessRunner(), config_dir=login)
    gate = parser.Gate(cfg)
    first = gate.key(prs)["config_dir_digest"]
    (login / ".credentials.json").write_text("{}")
    assert gate.key(prs)["config_dir_digest"] == first
    (login / "settings.json").write_text("{}")
    assert gate.key(prs)["config_dir_digest"] != first


@pytest.mark.parametrize("kind", ["directory", "not_executable"])
def test_unlaunchable_cli_latches_with_evidence(tmp_path, kind):
    cli = tmp_path / "claude"
    if kind == "directory":
        cli.mkdir()
    else:
        cli.write_text("#!/bin/sh\n")
        cli.chmod(0o600)
    cfg = config.load({"STATEMENT_PARSER_CLI": str(cli), "STATEMENT_PARSER_SANDBOX": "false",
                       "STATEMENT_PARSER_ALLOW_UNSANDBOXED": "true", "STATEMENT_STATE_DIR": str(tmp_path / "state")})
    prs = parser.Parser(cfg, SubprocessRunner())
    gate = parser.Gate(cfg)
    with pytest.raises(parser.ParserDisabled):
        gate.ensure(prs)
    assert gate.latch_path.exists() and json.loads(gate.evidence_path_for(gate.key(prs)).read_text())["ok"] is False


def test_key_failure_latches(cfg):
    class Boom:
        def run(self, *a, **k):
            raise PermissionError("x")
    gate = parser.Gate(cfg)
    with pytest.raises(parser.ParserDisabled):
        gate.ensure(parser.Parser(cfg, Boom()))
    assert json.loads(gate.evidence_path_for({}).read_text())["reasons"] == ["key: PermissionError"] and gate.latch_path.exists()
    gate.clear()
    report = gate.run_operator_gate(parser.Parser(cfg, Boom()))
    assert not report.ok and report.reasons == ["key: PermissionError"] and gate.latch_path.exists()


def test_login_dir_digest_sees_same_size_same_mtime_edits(cfg, tmp_path):
    login = tmp_path / "login"
    login.mkdir()
    f = login / "settings.json"
    f.write_text("aaaa")
    st = f.stat()
    prs = parser.Parser(cfg, SubprocessRunner(), config_dir=login)
    gate = parser.Gate(cfg)
    before = gate.key(prs)["config_dir_digest"]
    f.write_text("bbbb")
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert gate.key(prs)["config_dir_digest"] != before


def test_raising_on_start_hook_surfaces_its_own_error(cfg):
    def hook(proc):
        raise KeyError("hook")
    report = parser.Parser(cfg, SubprocessRunner()).gate(hook)
    assert not report.ok and report.reasons == ["error: KeyError"]


def test_thinking_prelude_parses(cfg, tmp_path):
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(thinking_prelude=True))
    assert parser.Parser(cfg, SubprocessRunner()).parse("x").statement_total == "580"


def test_thinking_tokens_only_between_init_and_result():
    v = parser.StreamValidator()
    v.feed(b'{"type":"system","subtype":"thinking_tokens"}')
    assert v.violation


def test_failed_gate_keeps_the_observed_events(cfg, tmp_path):
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(other_system_subtype=True))
    report = parser.Parser(cfg, SubprocessRunner()).gate()
    assert not report.ok and "system/other" in report.reasons[0]
    assert "system/other_subtype" in report.evidence["events"] and report.evidence["tools"] == ["StructuredOutput"]
    assert "num_turns" in report.evidence and "tool_calls" in report.evidence


def test_host_evidence_records_the_canary_report(tmp_path, monkeypatch):
    cfg = config.load({"STATEMENT_PARSER_SANDBOX": "true", "STATEMENT_PARSER_CONFIG_DIR": str(tmp_path / "cfg"),
                       "STATEMENT_PARSER_CLI": str(tmp_path / "claude"), "STATEMENT_STATE_DIR": str(tmp_path / "s")})
    prs = parser.Parser(cfg, SubprocessRunner())
    failed = parser.GateReport(False, ["sandbox: unknown event system/foo"], {"events": ["system/init", "system/foo"]})
    monkeypatch.setattr(prs, "gate", lambda on_start=None: failed)

    class Done:
        def __init__(self, rc):
            self.returncode, self.stdout = rc, ""

    def fake_run(cmd, **kw):
        tail = cmd[cmd.index("--") + 1:] if "--" in cmd else cmd
        return Done(1 if tail[:2] == ["/usr/bin/test", "-e"] or tail == ["/usr/bin/touch", "/cfg/x"] else 0)

    monkeypatch.setattr(parser.subprocess, "run", fake_run)
    out = parser.host_evidence(cfg, prs)
    assert out["ok_by_check"]["canary"] is False
    assert out["canary_reasons"] == failed.reasons and out["canary_events"] == ["system/init", "system/foo"]
