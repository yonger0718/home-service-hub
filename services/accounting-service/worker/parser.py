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
from typing import Callable, Iterable

from pydantic import ValidationError

from worker import parse_schema
from worker.config import WorkerConfig
from worker.runner import Runner

STDIN_CAP = 400 * 1024
STDOUT_CAP = 2 * 1024 * 1024
STRUCTURED_TOOL = "StructuredOutput"
CANARY = "MASKED STATEMENT CANARY\n" + "\n".join(f"2026/09/{i:02d} CANARY SHOP {i} 100" for i in range(1, 12))
GATE_MAX_AGE = timedelta(hours=24)
IGNORED_SYSTEM_SUBTYPES = ("thinking_tokens", "ui_invalidate")  # CLI housekeeping, ignored anywhere
FORBIDDEN_PREFIXES = ("hook", "subagent", "permission")


class ParseError(RuntimeError):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason if not detail else f"{reason}: {detail}")
        self.reason = reason
        self.envelope: Envelope | None = None  # the partial stream observed, attached by Parser._run_once


class ParserDisabled(RuntimeError):
    pass


class GateUnavailable(RuntimeError):
    """The canary could not run (CLI exit, timeout, outage): nothing is latched, parsing waits for the next run."""


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
    transient: bool = False  # the only failure was exit/timeout: not evidence of a violation


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
    """Ruling 15: incremental validation of the stream-json lines; `violation` is set at the first problem, at the
    moment it appears. Messages carry counts and our own constants, never model-controlled strings."""

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
        try:
            self._feed(raw)
        except Exception:  # RecursionError from json.loads, TypeError on odd shapes, ...: a violation, never a crash
            self._bad("malformed event")

    def _feed(self, raw: bytes) -> None:
        raw = raw.strip()
        if not raw:
            return
        try:
            event = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._bad("non-json line")
        if not isinstance(event, dict):
            return self._bad("non-object event")
        kind, sub = str(event.get("type", "")), str(event.get("subtype", "") or "")
        env = self.envelope
        if len(env.events) < 50:
            env.events.append(f"{kind[:40]}/{sub[:40]}")
        if kind.startswith(FORBIDDEN_PREFIXES) or sub.startswith(FORBIDDEN_PREFIXES):
            return self._bad("forbidden event type")
        if kind == "system" and sub == "init":
            if self._expect != "init":
                self._bad("duplicate init")
            tools = event.get("tools")
            if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
                self._bad("tools exposed: invalid")
            else:
                env.tools = list(tools)
                if tools != [STRUCTURED_TOOL]:
                    self._bad(f"tools exposed: {len(tools)}")
            self._expect = "assistant"
            return
        if kind == "system" and sub in IGNORED_SYSTEM_SUBTYPES:
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
            if any(b.get("type") not in ("thinking", "text", "tool_use") for b in blocks):
                self._bad("assistant block of another type")
            if len(uses) > 1 or (uses and env.tool_names):
                self._bad(f"assistant tool_use count {len(uses) + len(env.tool_names)}")
            if not uses:  # thinking/text only: the tool_use message is still to come
                return
            for b in uses:
                env.tool_names.append(str(b.get("name", ""))[:64])
                if b.get("name") != STRUCTURED_TOOL:
                    self._bad("tool call other than StructuredOutput")
                tool_id = b.get("id")
                if not isinstance(tool_id, str) or not tool_id:
                    self._bad("tool_use without an id")
                else:
                    self._tool_use_id = tool_id
            self._expect = "user"
            return
        if kind == "user":
            if self._expect != "user":
                self._bad("user out of order")
            if len(blocks) != 1 or blocks[0].get("type") != "tool_result":
                self._bad("user message is not exactly one tool_result")
            else:
                ref = blocks[0].get("tool_use_id")
                if not isinstance(ref, str) or not ref or ref != self._tool_use_id:
                    self._bad("tool_result id missing or mismatched")
            self._expect = "result"
            return
        if kind == "rate_limit_event":
            if self._expect != "result":
                self._bad("rate_limit_event out of order")
            return
        if kind == "result":
            if env.result_subtype is not None:
                self._bad("duplicate result")
            if self._expect != "result":
                self._bad("result out of order")
            env.result_subtype = sub
            env.num_turns = event.get("num_turns")
            env.structured_output = event.get("structured_output")
            if type(env.num_turns) is not int or env.num_turns != 2:
                self._bad("num_turns is not 2")
            self._expect = "end"
            return
        self._bad(f"unknown event {kind[:30]}/{sub[:30]}")

    def finish(self) -> Envelope:
        env = self.envelope
        if env.result_subtype is not None:  # a stream that never reached a result is an exit/timeout, not a violation
            if env.tool_names != [STRUCTURED_TOOL]:
                self._bad(f"tool calls: {len(env.tool_names)}")
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
        self._sel = selectors.DefaultSelector()
        self._writer = threading.Thread(target=self._write, args=(stdin,), daemon=True)

    def _check_violation(self) -> None:
        if self.validator.violation:
            raise ParseError("sandbox", self.validator.violation)

    def _feed_lines(self, chunk: bytes) -> None:
        if b"\n" not in chunk:
            self._pending.extend(chunk)
            return
        *lines, rest = (bytes(self._pending) + chunk).split(b"\n")
        self._pending = bytearray(rest)
        for line in lines:
            self.validator.feed(line)
            self._check_violation()

    def _write(self, data: bytes) -> None:
        try:
            self.proc.stdin.write(data)
            self.proc.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            self.stdin_error = "stdin closed by the child"

    def pump(self) -> None:
        self._writer.start()
        self._sel.register(self.proc.stdout, selectors.EVENT_READ, self.out)
        self._sel.register(self.proc.stderr, selectors.EVENT_READ, self.err)
        open_pipes = 2
        while open_pipes:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise ParseError("timeout")
            for key, _ in self._sel.select(timeout=min(remaining, 0.25)):
                if time.monotonic() >= self.deadline:
                    raise ParseError("timeout")
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    self._sel.unregister(key.fileobj)
                    open_pipes -= 1
                    continue
                key.data.extend(chunk)
                if key.data is self.out:
                    self._feed_lines(chunk)  # a violation in this chunk wins over the cap below
                if len(key.data) > STDOUT_CAP:
                    raise ParseError("output_too_large")
        if self._pending:
            self.validator.feed(bytes(self._pending))
            self._check_violation()
        try:
            self.proc.wait(timeout=max(0.0, self.deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            if self.proc.poll() is None:  # exited right at the deadline is not a timeout
                raise ParseError("timeout") from exc

    def close(self) -> None:
        self._sel.close()
        for pipe in (self.proc.stdout, self.proc.stderr, self.proc.stdin):
            try:
                pipe.close()
            except (OSError, ValueError):
                pass
        if self._writer.ident is not None:  # never started when an on_start hook raised
            self._writer.join(timeout=1)


def _kill(proc: subprocess.Popen) -> None:
    """SIGKILL the child's session. Under bwrap the CLI runs in its own session (--new-session) inside bwrap's pid
    namespace; --die-with-parent plus killing bwrap's group (which is what start_new_session gave us) ends it."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _safe_errors(exc: ValidationError) -> str:
    """Pydantic errors reduced to `loc: type` (no input values, no model-controlled key names)."""
    parts = []
    for err in exc.errors(include_input=False, include_url=False):
        loc = list(err["loc"])
        if err["type"] == "extra_forbidden" and loc:
            loc[-1] = "<extra>"
        parts.append(f"{'.'.join(str(x) for x in loc)}: {err['type']}")
    return "; ".join(parts)[:300]


class Parser:
    def __init__(self, cfg: WorkerConfig, runner: Runner, *, config_dir: Path | None = None,
                 credentials_writable: bool = True):
        if not cfg.parser_sandbox and not cfg.parser_allow_unsandboxed:
            raise RuntimeError("unsandboxed parser is refused; set STATEMENT_PARSER_ALLOW_UNSANDBOXED=true")
        self.cfg, self.runner = cfg, runner
        self.config_dir, self.credentials_writable = config_dir or cfg.parser_config_dir, credentials_writable
        self._schema = json.dumps(parse_schema.json_schema(), ensure_ascii=False, separators=(",", ":"))

    def argv(self) -> list[str]:
        return command(self.cfg, self._schema, config_dir=self.config_dir, credentials_writable=self.credentials_writable)

    def cli_version(self) -> str:
        env = {k: os.environ[k] for k in ("HOME", "PATH") if k in os.environ}
        result = self.runner.run([str(self.cfg.parser_cli), "--version"], timeout=30, env=env)
        words = result.stdout.decode("utf-8", "replace").split() if result.returncode == 0 else []
        return words[0] if words else ""

    def _check_version(self) -> None:
        found = self.cli_version()
        if found != self.cfg.parser_cli_version:
            raise ParseError("cli_version", f"found {found or 'none'}, pinned {self.cfg.parser_cli_version}")

    def _run_once(self, stdin: bytes, on_start: Callable[[subprocess.Popen], None] | None = None) -> Envelope:
        env = {"HOME": os.environ.get("HOME", "/tmp"), "PATH": os.environ.get("PATH", "/usr/bin")}
        try:
            proc = subprocess.Popen(self.argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=env, start_new_session=True)
        except OSError as exc:
            raise ParseError("exit", f"cannot start the parser ({type(exc).__name__})") from exc
        io = _ChildIO(proc, stdin, time.monotonic() + self.cfg.parser_timeout_s)
        try:
            return self._drive(proc, io, on_start)
        except ParseError as exc:
            exc.envelope = io.validator.envelope
            raise

    def _drive(self, proc: subprocess.Popen, io: "_ChildIO",
               on_start: Callable[[subprocess.Popen], None] | None) -> Envelope:
        try:
            if on_start is not None:
                on_start(proc)
            try:
                io.pump()
            except ParseError as exc:
                if exc.reason != "sandbox" and io.validator.envelope.violations:  # a violation beats I/O failures
                    raise ParseError("sandbox", "; ".join(io.validator.envelope.violations)[:300]) from exc
                raise
        finally:
            _kill(proc)  # every path: the session (and any descendant) dies, then the pipes close
            io.close()
        envelope = io.validator.finish()
        if envelope.violations:  # sandbox precedence over exit/schema (AGENT-95 Must 3)
            raise ParseError("sandbox", "; ".join(envelope.violations)[:300])
        if proc.returncode != 0 or envelope.result_subtype != "success":
            text = bytes(io.err[:4096]).decode("utf-8", "replace").lower()
            if proc.returncode != 0 and any(marker in text for marker in AUTH_MARKERS):
                raise ParseError("auth")
            raise ParseError("exit", f"rc={proc.returncode} subtype_success={envelope.result_subtype == 'success'}")
        return envelope

    def _validated(self, envelope: Envelope) -> parse_schema.StatementParse:
        try:
            return parse_schema.StatementParse.model_validate(envelope.structured_output or {})
        except ValidationError as exc:
            raise ParseError("schema", _safe_errors(exc)) from exc

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

    def gate(self, on_start: Callable[[subprocess.Popen], None] | None = None) -> GateReport:
        reasons: list[str] = []
        transient = False
        evidence: dict = {"pinned": self.cfg.parser_cli_version, "model": self.cfg.parser_model,
                          "sandbox": self.cfg.parser_sandbox}
        try:
            self._check_version()
            envelope = self._run_once(CANARY.encode(), on_start)
            evidence.update(num_turns=envelope.num_turns, tools=envelope.tools, tool_calls=envelope.tool_names,
                            events=envelope.events)
            parse_schema.StatementParse.model_validate(envelope.structured_output or {})
        except ParseError as exc:
            reasons.append(str(exc)[:300])
            transient = exc.reason in ("exit", "timeout")
            seen = exc.envelope
            if seen is not None:
                evidence.update(events=[e[:64] for e in seen.events], num_turns=seen.num_turns,
                                tools=[t[:64] for t in seen.tools], tool_calls=len(seen.tool_names))
        except ValidationError as exc:
            reasons.append(f"schema: {_safe_errors(exc)}")
        except Exception as exc:  # a missing bwrap/CLI or any surprise is a gate failure, never a crash
            reasons.append(f"error: {type(exc).__name__}")
        return GateReport(not reasons, reasons, evidence, transient and len(reasons) == 1)


def _dir_digest(directory: Path) -> str | None:
    """sha256 over (relative name, content hash | size:mtime_ns for files > 1 MiB) of every file in the login dir
    except the top-level .credentials.json."""
    digest = hashlib.sha256()
    try:
        for path in sorted(directory.rglob("*")):
            rel = path.relative_to(directory)
            if str(rel) == ".credentials.json" or not path.is_file():
                continue
            st = path.stat()
            if st.st_size <= 1024 * 1024:
                stamp = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                stamp = f"{st.st_size}:{st.st_mtime_ns}"
            digest.update(f"{rel}\0{stamp}\n".encode())
    except OSError:
        return None
    return digest.hexdigest()


class Gate:
    """Ruling 9: mandatory, cached for 24 h per key, latched on any violation until an operator gate succeeds."""

    def __init__(self, cfg: WorkerConfig):
        self.cfg = cfg
        self.latch_path = cfg.state_dir / "parser-disabled.json"

    def evidence_path_for(self, key: dict) -> Path:
        """One evidence file per parser configuration, so a verify run never overwrites the worker's evidence."""
        digest = hashlib.sha256((str(key.get("argv_sha256", "")) + str(key.get("sandbox", "")) + str(key.get("config_dir", ""))).encode()).hexdigest()[:12]
        return self.cfg.state_dir / f"gate-{digest}.json"

    def key(self, parser: Parser) -> dict:
        return {"cli_version": parser.cli_version(), "model": self.cfg.parser_model,
                "config_dir": str(parser.config_dir), "config_dir_digest": _dir_digest(parser.config_dir),
                "argv_sha256": hashlib.sha256("\0".join(parser.argv()).encode()).hexdigest(),
                "sandbox": self.cfg.parser_sandbox}

    def _write(self, path: Path, data: dict) -> None:
        self.cfg.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = path.with_name(path.name + ".tmp")
        tmp.unlink(missing_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, default=str)
        os.replace(tmp, path)

    def latch(self, reason: str, key: dict) -> None:
        self._write(self.latch_path, {"reason": reason[:300], "at": datetime.now(timezone.utc).isoformat(), "key": key})

    def clear(self) -> None:
        self.latch_path.unlink(missing_ok=True)

    def _fresh(self, key: dict) -> bool:
        try:
            evidence = json.loads(self.evidence_path_for(key).read_text(encoding="utf-8"))
            checked = datetime.fromisoformat(evidence["checked_at"])
            if checked.tzinfo is None:
                return False
            age = datetime.now(timezone.utc) - checked
            return bool(evidence.get("ok")) and evidence.get("key") == key and timedelta(0) <= age < GATE_MAX_AGE
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return False  # missing, corrupt or odd evidence is stale: re-run the canary

    def _key_failure(self, exc: Exception) -> None:
        reason = f"key: {type(exc).__name__}"
        self._write(self.evidence_path_for({}), {"ok": False, "reasons": [reason], "evidence": {}, "key": {},
                                         "checked_at": datetime.now(timezone.utc).isoformat()})
        self.latch(reason, {})

    def ensure(self, parser: Parser) -> None:
        if self.latch_path.exists():
            raise ParserDisabled("parser disabled until an operator gate succeeds")
        try:
            key = self.key(parser)
        except Exception as exc:
            self._key_failure(exc)
            raise ParserDisabled("gate failed") from exc
        if self._fresh(key):
            return
        report = parser.gate()
        self._write(self.evidence_path_for(key), {"ok": report.ok, "reasons": report.reasons, "evidence": report.evidence,
                                         "key": key, "checked_at": datetime.now(timezone.utc).isoformat()})
        if not report.ok:
            if report.transient:
                raise GateUnavailable("canary could not run")
            self.latch("; ".join(report.reasons), key)
            raise ParserDisabled("gate failed")

    def run_operator_gate(self, parser: Parser, *, verify_parser: Parser | None = None) -> GateReport:
        """`python -m worker gate` (ruling 16): the latch clears only when the worker canary passed, a verify-login
        canary ran and passed, and (sandbox on) every host check ran and passed; otherwise it is (re)latched."""
        try:
            key = self.key(parser)
        except Exception as exc:
            self._key_failure(exc)
            return GateReport(False, [f"key: {type(exc).__name__}"], {})
        sandbox = self.cfg.parser_sandbox
        host: dict = {}
        host_failed: list[str] = []

        def canary(which: str, prs: Parser) -> GateReport:
            if not sandbox:
                return prs.gate()
            try:
                report, checks = host_gate(self.cfg, prs)
            except Exception as exc:  # noqa: BLE001
                host_failed.append(f"host.{which}: error {type(exc).__name__}")
                return GateReport(False, ["error: host checks"], {})
            host[which] = checks
            host_failed.extend(f"host.{which}: {k}" for k, ok in checks["ok_by_check"].items() if not ok)
            return report

        report = canary("worker", parser)
        evidence: dict = {"worker": report.evidence}
        reasons = list(report.reasons)
        checks_ran = {"worker": True, "verify_login": False, "host": False}
        if verify_parser is None:
            reasons.append("verify login missing")
        else:
            vreport = canary("verify_login", verify_parser)
            checks_ran["verify_login"] = True
            evidence["verify_login"] = vreport.evidence
            reasons += [f"verify: {r}" for r in vreport.reasons]
        if sandbox:
            checks_ran["host"] = "worker" in host and (verify_parser is None or "verify_login" in host)
            evidence["host"] = host
            reasons += host_failed
        evidence["checks_ran"] = checks_ran
        self._write(self.evidence_path_for(key), {"ok": not reasons, "reasons": reasons, "evidence": evidence, "key": key,
                                         "checked_at": datetime.now(timezone.utc).isoformat()})
        if reasons:
            self.latch("; ".join(reasons), key)
        else:
            self.clear()
        return GateReport(not reasons, reasons, evidence)


AUTH_MARKERS = ("not logged in", "please run /login", "authentication_error", "invalid api key", "oauth")
SAMPLE_INTERVAL_S = 0.05
ALLOWED_COMMS = {"bwrap", "claude", "git"}  # git = the CLI's own child


def _descendant_comms(pid: int) -> set[str]:
    """`comm` of every descendant of `pid` (recursive over `ps --ppid`). Raises RuntimeError when `ps` misbehaves:
    rc 0, or rc 1 with nothing on stdout/stderr (a leaf: no children), are the only normal outcomes."""
    seen: set[str] = set()
    stack = [pid]
    while stack:
        result = subprocess.run(["ps", "-o", "pid=,comm=", "--ppid", str(stack.pop())], capture_output=True, text=True,
                                timeout=5)
        out = result.stdout or ""
        if result.returncode not in (0, 1) or (result.returncode == 1 and (out.strip() or getattr(result, "stderr", ""))):
            raise RuntimeError("ps failed")
        for line in out.splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2 and parts[0].isdigit():
                seen.add(parts[1].strip().removesuffix(" <defunct>"))
                stack.append(int(parts[0]))
    return seen


class ProcessSampler:
    """Samples descendants of the sandbox child: once immediately (t=0, in `on_start`), then every 50 ms. The check
    passes only when the sampler never failed, saw at least one of claude/bwrap, and saw nothing else but git."""

    def __init__(self):
        self.seen: set[str] = set()
        self.failed = False
        self.started = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _once(self, pid: int) -> None:
        try:
            self.seen.update(_descendant_comms(pid))
        except Exception:  # noqa: BLE001 — any sampler problem is a failed check, never a pass
            self.failed = True

    def _loop(self, pid: int) -> None:
        while not self._stop.wait(SAMPLE_INTERVAL_S):
            self._once(pid)

    def on_start(self, proc: subprocess.Popen) -> None:
        self.started = True
        self._once(proc.pid)
        self._thread = threading.Thread(target=self._loop, args=(proc.pid,), daemon=True)
        self._thread.start()

    def finish(self) -> bool:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2)
        return (self.started and not self.failed and bool(self.seen & {"bwrap", "claude"})
                and self.seen <= ALLOWED_COMMS)


def host_gate(cfg: WorkerConfig, parser: Parser) -> tuple[GateReport, dict]:
    """Static sandbox probes for this parser's argv plus ONE canary sampled for descendants (§5.5/§16 C7).
    Returns the canary report and the host checks (`ok_by_check` has no canary key; the report carries it)."""
    if not cfg.parser_sandbox:
        return parser.gate(), {"ok_by_check": {"sandbox_enabled": False}}
    argv = parser.argv()
    prefix = argv[:argv.index("--")]
    checks: dict[str, bool] = {}

    def run(cmd: list[str]) -> int | None:
        try:
            return subprocess.run([*prefix, "--", *cmd], capture_output=True, timeout=30).returncode
        except subprocess.TimeoutExpired:
            return None

    checks["positive_control"] = run(["/usr/bin/true"]) == 0
    checks["etc_passwd_absent"] = run(["/usr/bin/test", "-e", "/etc/passwd"]) == 1  # 1 = absent; other = broken
    checks["home_absent"] = run(["/usr/bin/test", "-e", os.path.expanduser("~")]) == 1
    cfg_rc = run(["/usr/bin/touch", "/cfg/x"])
    creds_ok = cfg_rc is not None and cfg_rc != 0
    if not parser.credentials_writable:  # the verify mount: even the credentials file is read-only
        creds_rc = run(["/usr/bin/touch", "/cfg/.credentials.json"])
        creds_ok = creds_ok and creds_rc is not None and creds_rc != 0
    checks["credentials_only_writable"] = run(["/usr/bin/touch", "/tmp/x"]) == 0 and creds_ok
    before = _dir_digest(parser.config_dir)
    sampler = ProcessSampler()
    report = parser.gate(sampler.on_start)
    checks["only_cli_processes"] = sampler.finish()
    checks["login_dir_unchanged"] = before is not None and before == _dir_digest(parser.config_dir)
    return report, {"ok_by_check": checks, "processes_seen": sorted(sampler.seen),
                    "sampler_failed": sampler.failed, "canary_reasons": report.reasons,
                    "canary_events": report.evidence.get("events")}


def host_evidence(cfg: WorkerConfig, parser: Parser) -> dict:
    """`host_gate` as one dict, with the canary result as a check of its own."""
    report, host = host_gate(cfg, parser)
    if "canary_reasons" in host:
        host["ok_by_check"]["canary"] = report.ok
    return host
