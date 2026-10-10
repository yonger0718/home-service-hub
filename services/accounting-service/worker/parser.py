"""The sandboxed model-as-a-function parser (§5.5, rulings 1–2, 8–9): command, strict stream, bounded I/O, gate."""
from __future__ import annotations

import hashlib
import json
import os
import selectors
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from pydantic import ValidationError

from worker import parse_schema
from worker.config import WorkerConfig
from worker.runner import Runner

STDIN_CAP = 400 * 1024
STDOUT_CAP = 2 * 1024 * 1024
STRUCTURED_TOOL = "StructuredOutput"
CANARY = "MASKED STATEMENT CANARY\n" + "\n".join(f"2026/09/{i:02d} CANARY SHOP {i} 100" for i in range(1, 12))
GATE_MAX_AGE = timedelta(hours=24)
FORBIDDEN_PREFIXES = ("hook", "subagent", "permission")


class ParseError(RuntimeError):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason if not detail else f"{reason}: {detail}")
        self.reason = reason


class ParserDisabled(RuntimeError):
    pass


@dataclass
class Envelope:
    structured_output: dict | None = None
    num_turns: int | None = None
    tools: list[str] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    result_subtype: str | None = None
    assistant_messages: int = 0
    violations: list[str] = field(default_factory=list)


@dataclass
class GateReport:
    ok: bool
    reasons: list[str]
    evidence: dict


def _sandbox_prefix(cfg: WorkerConfig, config_dir: Path, credentials_writable: bool) -> list[str]:
    binds: list[str] = []
    for path in ("/usr", "/lib", "/lib64", "/etc/pki", "/etc/ssl", "/etc/resolv.conf", "/etc/hosts"):
        if os.path.exists(path):
            binds += ["--ro-bind", path, path]
    cfg_binds = ["--ro-bind", str(config_dir), "/cfg"]
    if credentials_writable:
        cfg_binds += ["--bind", str(config_dir / ".credentials.json"), "/cfg/.credentials.json"]
    return ["bwrap", "--unshare-all", "--share-net", "--die-with-parent", "--new-session", "--cap-drop", "ALL",
            *binds, "--ro-bind", str(cfg.parser_cli), "/opt/claude/claude", *cfg_binds,
            "--tmpfs", "/tmp", "--tmpfs", "/work", "--proc", "/proc", "--dev", "/dev", "--chdir", "/work",
            "--setenv", "HOME", "/tmp", "--setenv", "CLAUDE_CONFIG_DIR", "/cfg",
            "--setenv", "PATH", "/opt/claude:/usr/bin", "--", "/opt/claude/claude"]


def command(cfg: WorkerConfig, schema_json: str, *, config_dir: Path | None = None,
            credentials_writable: bool = True) -> list[str]:
    config_dir = config_dir or cfg.parser_config_dir
    head = _sandbox_prefix(cfg, config_dir, credentials_writable) if cfg.parser_sandbox else [str(cfg.parser_cli)]
    return [*head, "-p", "--safe-mode", "--tools", "", "--disallowedTools", "mcp__*", "--strict-mcp-config",
            "--mcp-config", '{"mcpServers":{}}', "--permission-prompts", "none", "--no-session-persistence",
            "--max-turns", "1", "--output-format", "stream-json", "--verbose", "--json-schema", schema_json,
            "--model", cfg.parser_model, parse_schema.INSTRUCTION]


def consume(lines: Iterable[bytes]) -> Envelope:
    """Convenience (tests): feed every line to a StreamValidator and finish."""
    validator = StreamValidator()
    for raw in lines:
        validator.feed(raw)
    return validator.finish()


def check_envelope(env: Envelope) -> list[str]:
    return list(env.violations)


class StreamValidator:
    """Ruling 15: incremental validation of the stream-json lines; `violation` is set at the first problem."""

    def __init__(self):
        self.envelope = Envelope()
        self.violation: str | None = None
        self._expect = "init"
        self._tool_use_id: str | None = None

    def _bad(self, why: str) -> None:
        self.envelope.violations.append(why)
        if self.violation is None:
            self.violation = why

    def feed(self, raw: bytes) -> None:
        raw = raw.strip()
        if not raw:
            return
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            return self._bad("non-json line")
        if not isinstance(event, dict):
            return self._bad("non-object event")
        kind, sub = str(event.get("type", "")), str(event.get("subtype", "") or "")
        env = self.envelope
        env.events.append(f"{kind}/{sub}")
        if kind.startswith(FORBIDDEN_PREFIXES) or sub.startswith(FORBIDDEN_PREFIXES):
            return self._bad(f"forbidden event {kind}/{sub}")
        if kind == "system" and sub == "init":
            if self._expect != "init":
                self._bad("duplicate init")
            env.tools = list(event.get("tools") or [])
            self._expect = "assistant"
            return
        blocks: list[dict] = []
        if kind in ("assistant", "user"):
            message = event.get("message")
            if not isinstance(message, dict) or not isinstance(message.get("content"), list):
                return self._bad(f"{kind} message is not an object with a content list")
            blocks = [b for b in message["content"] if isinstance(b, dict)]
            if len(blocks) != len(message["content"]):
                return self._bad(f"{kind} block is not an object")
        if kind == "assistant":
            env.assistant_messages += 1
            if self._expect != "assistant":
                self._bad("assistant out of order")
            uses = [b for b in blocks if b.get("type") == "tool_use"]
            if any(b.get("type") not in ("text", "tool_use") for b in blocks):
                self._bad("assistant block of another type")
            if len(uses) != 1:
                self._bad(f"assistant tool_use count {len(uses)}")
            for b in uses:
                env.tool_names.append(str(b.get("name", "")))
                self._tool_use_id = b.get("id")
            self._expect = "user"
            return
        if kind == "user":
            if self._expect != "user":
                self._bad("user out of order")
            if len(blocks) != 1 or blocks[0].get("type") != "tool_result":
                self._bad("user message is not exactly one tool_result")
            elif blocks[0].get("tool_use_id") != self._tool_use_id:
                self._bad("tool_result id mismatch")
            self._expect = "result"
            return
        if kind == "rate_limit_event":
            return
        if kind == "result":
            if env.result_subtype is not None:
                self._bad("duplicate result")
            if self._expect != "result":
                self._bad("result out of order")
            env.result_subtype = sub
            env.num_turns = event.get("num_turns")
            env.structured_output = event.get("structured_output")
            self._expect = "end"
            return
        self._bad(f"unknown event {kind}/{sub}")

    def finish(self) -> Envelope:
        env = self.envelope
        if env.tools != [STRUCTURED_TOOL]:
            self._bad(f"tools exposed: {env.tools}")
        if env.tool_names != [STRUCTURED_TOOL]:
            self._bad(f"tool calls: {env.tool_names}")
        if env.assistant_messages != 1:
            self._bad(f"assistant messages: {env.assistant_messages}")
        if env.num_turns != 2:
            self._bad(f"num_turns: {env.num_turns}")
        return env


class _ChildIO:
    """Bounded, deadline-driven I/O with the child: stdin from a thread, stdout/stderr via selectors, stdout lines
    validated as they complete (ruling 15)."""

    def __init__(self, proc: subprocess.Popen, stdin: bytes, deadline: float):
        self.proc, self.deadline = proc, deadline
        self.out, self.err = bytearray(), bytearray()
        self.validator = StreamValidator()
        self._pending = bytearray()
        self.stdin_error: str | None = None
        self._writer = threading.Thread(target=self._write, args=(stdin,), daemon=True)

    def _feed_lines(self, chunk: bytes) -> None:
        self._pending.extend(chunk)
        while b"\n" in self._pending:
            line, _, rest = bytes(self._pending).partition(b"\n")
            self._pending = bytearray(rest)
            self.validator.feed(line)
            if self.validator.violation:
                raise ParseError("sandbox", self.validator.violation)

    def _write(self, data: bytes) -> None:
        try:
            self.proc.stdin.write(data)
            self.proc.stdin.close()
        except (BrokenPipeError, OSError):
            self.stdin_error = "stdin closed by the child"

    def pump(self) -> None:
        self._writer.start()
        sel = selectors.DefaultSelector()
        sel.register(self.proc.stdout, selectors.EVENT_READ, self.out)
        sel.register(self.proc.stderr, selectors.EVENT_READ, self.err)
        open_pipes = 2
        while open_pipes:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise ParseError("timeout")
            for key, _ in sel.select(timeout=min(remaining, 0.25)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    sel.unregister(key.fileobj)
                    open_pipes -= 1
                    continue
                key.data.extend(chunk)
                if len(key.data) > STDOUT_CAP:
                    raise ParseError("output_too_large")
                if key.data is self.out:
                    self._feed_lines(chunk)
        try:
            self.proc.wait(timeout=max(0.0, self.deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            raise ParseError("timeout") from exc
        if self._pending:
            self.validator.feed(bytes(self._pending))
            if self.validator.violation:
                raise ParseError("sandbox", self.validator.violation)

    def close(self) -> None:
        for pipe in (self.proc.stdout, self.proc.stderr, self.proc.stdin):
            try:
                pipe.close()
            except (OSError, ValueError):
                pass
        self._writer.join(timeout=1)


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


class Parser:
    def __init__(self, cfg: WorkerConfig, runner: Runner, *, config_dir: Path | None = None,
                 credentials_writable: bool = True):
        self.cfg, self.runner = cfg, runner
        self.config_dir, self.credentials_writable = config_dir or cfg.parser_config_dir, credentials_writable
        self._schema = json.dumps(parse_schema.json_schema(), ensure_ascii=False, separators=(",", ":"))

    def argv(self) -> list[str]:
        return command(self.cfg, self._schema, config_dir=self.config_dir, credentials_writable=self.credentials_writable)

    def cli_version(self) -> str:
        result = self.runner.run([str(self.cfg.parser_cli), "--version"], timeout=30)
        return result.stdout.decode("utf-8", "replace").split()[0] if result.returncode == 0 and result.stdout else ""

    def _check_version(self) -> None:
        found = self.cli_version()
        if found != self.cfg.parser_cli_version:
            raise ParseError("cli_version", f"found {found or 'none'}, pinned {self.cfg.parser_cli_version}")

    def _run_once(self, stdin: bytes) -> Envelope:
        env = {"HOME": os.environ.get("HOME", "/tmp"), "PATH": os.environ.get("PATH", "/usr/bin")}
        proc = subprocess.Popen(self.argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env, start_new_session=True)
        io = _ChildIO(proc, stdin, time.monotonic() + self.cfg.parser_timeout_s)
        try:
            io.pump()
        finally:
            _kill(proc)  # every path: the session (and any descendant) dies, then the pipes close
            io.close()
        envelope = io.validator.finish()
        if envelope.violations:  # sandbox precedence over exit/schema (AGENT-95 Must 3)
            raise ParseError("sandbox", "; ".join(envelope.violations)[:500])
        if proc.returncode != 0 or envelope.result_subtype != "success":
            text = bytes(io.err).decode("utf-8", "replace")
            if "auth" in text.lower() or "login" in text.lower():
                raise ParseError("auth")
            raise ParseError("exit", f"rc={proc.returncode} subtype={envelope.result_subtype}")
        return envelope

    def _validated(self, envelope: Envelope) -> parse_schema.StatementParse:
        try:
            return parse_schema.StatementParse.model_validate(envelope.structured_output or {})
        except ValidationError as exc:
            raise ParseError("schema", str(exc)[:500]) from exc

    def parse(self, masked_text: str) -> parse_schema.StatementParse:
        stdin = masked_text.encode("utf-8")
        if len(stdin) > STDIN_CAP:
            raise ParseError("stdin_too_large")
        self._check_version()
        last: ParseError | None = None
        for _ in range(max(1, self.cfg.parser_attempts)):
            try:
                return self._validated(self._run_once(stdin))
            except ParseError as exc:
                if exc.reason in ("sandbox", "cli_version", "stdin_too_large", "auth"):
                    raise
                last = exc
        assert last is not None
        raise last

    def gate(self) -> GateReport:
        reasons: list[str] = []
        evidence: dict = {"pinned": self.cfg.parser_cli_version, "model": self.cfg.parser_model,
                          "sandbox": self.cfg.parser_sandbox}
        try:
            self._check_version()
            envelope = self._run_once(CANARY.encode())
            evidence.update(num_turns=envelope.num_turns, tools=envelope.tools, tool_calls=envelope.tool_names,
                            events=envelope.events)
            parse_schema.StatementParse.model_validate(envelope.structured_output or {})
        except ParseError as exc:
            reasons.append(f"{exc.reason}: {exc}"[:300])
        except ValidationError as exc:
            reasons.append(f"schema: {str(exc)[:200]}")
        return GateReport(not reasons, reasons, evidence)


class Gate:
    """Ruling 9: mandatory, cached for 24 h per key, latched on any violation until an operator gate succeeds."""

    def __init__(self, cfg: WorkerConfig):
        self.cfg = cfg
        self.evidence_path = cfg.state_dir / "gate.json"
        self.latch_path = cfg.state_dir / "parser-disabled.json"

    def key(self, parser: Parser) -> dict:
        try:
            mtime = parser.config_dir.stat().st_mtime
        except FileNotFoundError:
            mtime = None
        return {"cli_version": parser.cli_version(), "model": self.cfg.parser_model, "config_dir_mtime": mtime,
                "argv_sha256": hashlib.sha256("\0".join(parser.argv()).encode()).hexdigest(),
                "sandbox": self.cfg.parser_sandbox}

    def _write(self, path: Path, data: dict) -> None:
        self.cfg.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, default=str)

    def latch(self, reason: str, key: dict) -> None:
        self._write(self.latch_path, {"reason": reason[:300], "at": datetime.now(timezone.utc).isoformat(), "key": key})

    def clear(self) -> None:
        self.latch_path.unlink(missing_ok=True)

    def ensure(self, parser: Parser) -> None:
        if self.latch_path.exists():
            raise ParserDisabled("parser disabled until an operator gate succeeds")
        key = self.key(parser)
        if self.evidence_path.exists():
            evidence = json.loads(self.evidence_path.read_text(encoding="utf-8"))
            checked = datetime.fromisoformat(evidence.get("checked_at", "2000-01-01T00:00:00+00:00"))
            if evidence.get("ok") and evidence.get("key") == key and datetime.now(timezone.utc) - checked < GATE_MAX_AGE:
                return
        report = parser.gate()
        self._write(self.evidence_path, {"ok": report.ok, "reasons": report.reasons, "evidence": report.evidence,
                                         "key": key, "checked_at": datetime.now(timezone.utc).isoformat()})
        if not report.ok:
            self.latch("; ".join(report.reasons), key)
            raise ParserDisabled("gate failed")

    def run_operator_gate(self, parser: Parser, *, verify_parser: Parser | None = None,
                          host_checks: bool = False) -> GateReport:
        """`python -m worker gate` (ruling 16): worker canary, verify-login canary, host checks; the latch is
        cleared only when everything passed, else (re)latched."""
        report = parser.gate()
        evidence = {"worker": report.evidence}
        reasons = list(report.reasons)
        if verify_parser is not None:
            vreport = verify_parser.gate()
            evidence["verify_login"] = vreport.evidence
            reasons += [f"verify: {r}" for r in vreport.reasons]
        if host_checks:
            checks = host_evidence(self.cfg, parser)
            evidence["host"] = checks
            reasons += [f"host: {k}" for k, ok in checks["ok_by_check"].items() if not ok]
        key = self.key(parser)
        self._write(self.evidence_path, {"ok": not reasons, "reasons": reasons, "evidence": evidence, "key": key,
                                         "checked_at": datetime.now(timezone.utc).isoformat()})
        if reasons:
            self.latch("; ".join(reasons), key)
        else:
            self.clear()
        return GateReport(not reasons, reasons, evidence)


def host_evidence(cfg: WorkerConfig, parser: Parser) -> dict:
    """Host-side checks (§5.5/§16 C7): positive control, host paths absent inside the sandbox, login dir unchanged
    across a canary, no stray processes observed DURING the canary (sampled every 100 ms)."""
    argv = parser.argv()
    if not cfg.parser_sandbox:
        return {"ok_by_check": {"sandbox_enabled": False}}
    prefix = argv[:argv.index("--")]
    checks: dict[str, bool] = {}

    def run(cmd: list[str]) -> int:
        return subprocess.run([*prefix, "--", *cmd], capture_output=True, timeout=30).returncode

    checks["positive_control"] = run(["/usr/bin/true"]) == 0
    checks["etc_passwd_absent"] = run(["/usr/bin/test", "-e", "/etc/passwd"]) == 1  # 1 = absent; other = broken
    checks["home_absent"] = run(["/usr/bin/test", "-e", os.path.expanduser("~")]) == 1
    checks["credentials_only_writable"] = run(["/usr/bin/sh", "-c", "touch /cfg/x 2>/dev/null"]) != 0
    before = {p: p.stat().st_mtime for p in parser.config_dir.rglob("*") if p.is_file()}
    seen: set[str] = set()
    stop = threading.Event()

    def sample() -> None:
        while not stop.wait(0.1):
            out = subprocess.run(["ps", "-u", str(os.getuid()), "-o", "comm="], capture_output=True, text=True).stdout
            seen.update(line.strip() for line in out.splitlines() if line.strip() in ("claude", "bwrap", "node", "sh", "bash", "python3"))

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    report = parser.gate()
    stop.set()
    sampler.join(1)
    after = {p: p.stat().st_mtime for p in parser.config_dir.rglob("*") if p.is_file()}
    changed = sorted(str(p.relative_to(parser.config_dir)) for p in after if before.get(p) != after[p])
    checks["login_dir_unchanged"] = changed in ([], [".credentials.json"])
    checks["only_cli_processes"] = seen <= {"claude", "bwrap", "python3"}  # python3 = this worker
    checks["canary"] = report.ok
    return {"ok_by_check": checks, "processes_seen": sorted(seen), "changed_files": changed}
