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
                        "STATEMENT_PARSER_SANDBOX": "false", "STATEMENT_PARSER_TIMEOUT": "5",
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
    cfg = config.load({"STATEMENT_PARSER_CLI": str(tmp_path / "claude"), "STATEMENT_PARSER_SANDBOX": "false",
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
    cfg = config.load({"STATEMENT_PARSER_CLI": str(tmp_path / "claude"), "STATEMENT_PARSER_SANDBOX": "false",
                       "STATEMENT_PARSER_TIMEOUT": "1", "STATEMENT_PARSER_ATTEMPTS": "1",
                       "STATEMENT_STATE_DIR": str(tmp_path / "state")})
    with pytest.raises(parser.ParseError) as err:
        parser.Parser(cfg, SubprocessRunner()).parse("x")
    assert err.value.reason == "timeout"


def test_child_that_never_reads_stdin_still_times_out(cfg, tmp_path):
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(), sleep=30, read_stdin=False)
    cfg = config.load({"STATEMENT_PARSER_CLI": str(tmp_path / "claude"), "STATEMENT_PARSER_SANDBOX": "false",
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
    bad_cli = fake_claude.write(tmp_path / "claude2", fake_claude.transcript(extra_tool=True))
    bad_cfg = config.load({"STATEMENT_PARSER_CLI": str(bad_cli), "STATEMENT_PARSER_SANDBOX": "false",
                           "STATEMENT_STATE_DIR": str(cfg.state_dir)})
    bad_verify = parser.Parser(bad_cfg, SubprocessRunner(), config_dir=tmp_path / "ro", credentials_writable=False)
    report = gate.run_operator_gate(prs, verify_parser=bad_verify)
    assert not report.ok and gate.latch_path.exists() and any(r.startswith("verify:") for r in report.reasons)


def test_gate_ensure_caches_and_latches(cfg, tmp_path):
    prs = parser.Parser(cfg, SubprocessRunner())
    gate = parser.Gate(cfg)
    gate.ensure(prs)  # runs the canary once
    assert (cfg.state_dir / "gate.json").exists()
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(extra_tool=True))
    gate.ensure(prs)  # cached evidence, same key → no new canary, still allowed
    stale = json.loads((cfg.state_dir / "gate.json").read_text())
    stale["checked_at"] = "2000-01-01T00:00:00+00:00"
    (cfg.state_dir / "gate.json").write_text(json.dumps(stale))
    with pytest.raises(parser.ParserDisabled):
        gate.ensure(prs)  # stale → re-run → violation → latch
    assert (cfg.state_dir / "parser-disabled.json").exists()
    fake_claude.write(tmp_path / "claude", fake_claude.transcript())
    with pytest.raises(parser.ParserDisabled):
        gate.ensure(prs)  # latched until an operator gate clears it
    assert gate.run_operator_gate(prs).ok and not (cfg.state_dir / "parser-disabled.json").exists()


def test_parser_version_string():
    v = parse_schema.version("2.1.296", "claude-sonnet-5-5")
    assert v.startswith("claude-cli-2.1.296-claude-sonnet-5-5-") and len(v.split("-")[-1]) == 8
