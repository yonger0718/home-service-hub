# Statement parser setup-token Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The statement parser authenticates with a one-year `claude setup-token` token read from a 0400 file. A missing login is labelled `auth`, not `sandbox`. The run summary reports the token's age.

**Architecture:** Token mode is on when the token file is a regular file. The token travels only through the child Popen environment, never through argv. Every mount binds `/cfg` read-only. Child output echoing the full token is scrubbed at raw-line ingestion. One exact no-login prefix (spec D3) is promoted from `sandbox` to `auth`, and the worker summary names the latched reason.

**Tech Stack:** Python 3.13, pytest, bwrap. The fake CLI is `tests/worker/fake_claude.py`.

**Spec:** `docs/superpowers/specs/2026-11-18-parser-setup-token-design.md` (rev 2, after AGENT-515; re-check PASS-with-minor, its P3s are folded here)

## Global Constraints

- Env var `STATEMENT_PARSER_OAUTH_TOKEN_FILE`, field `WorkerConfig.parser_oauth_token_file: Path`, default `~/.config/homehub-parser-oauth-token`.
- Child env var name: `CLAUDE_CODE_OAUTH_TOKEN`. Never use `--setenv` for it, so it is never in argv.
- Token alphabet `^[A-Za-z0-9_-]+$`, minimum 20 characters, after a strict-ASCII decode and `.strip()`. Error messages are exactly `token file unreadable` and `token file malformed`, raised `from None`.
- The token value never appears in an exception message, log line, gate evidence or file, run summary, test assertion output or fixture. Tests use the fictional values `sk-ant-oat01-FAKE-TEST-TOKEN` and `sk-ant-oat01-FAKE-TEST-TOKEN-2`.
- Gate evidence key `auth` takes the values `"token"` or `"login"`. Summary key `versions.parser_token_age_days` is a float rounded to 1 decimal, present only in token mode.
- Unchanged: the sandbox flags, `AUTH_MARKERS`, the gate key, `parse_schema.version`, the fail-fast pump and all stream rules other than the D3 promotion.
- Backend test recipe (worktree): symlink `.venv` to `~/workspace/home-hub/services/accounting-service/.venv`. Export `POSTGRES_*`/`ACCOUNTING_DB` and `OTEL_SERVICE_NAME_ACCOUNTING`/`OTEL_COLLECTOR_ENDPOINT_GRPC` with the grep-eval pattern. Run with `PYTHONPATH=$PWD/../shared-python-lib .venv/bin/python -m pytest tests/worker`. Run `tests/worker` only, not the 27-minute full suite.

## Review Focus

- A trailing newline in the token file is accepted. A space, NUL, non-UTF-8 byte, an empty file, a file deleted after `Parser` construction, or a `PermissionError` (injected by monkeypatching `Path.read_bytes`, not by chmod) raises `auth` with a fixed message.
- A directory at the token path means login mode, not auth.
- A token rotated between two parses: the second parse uses the new value.
- The no-login prefix promotes to `auth` only when exact. Each of these stays `sandbox`:
  - the auth assistant event with malformed or mixed content (e.g. `[{"type":"text"}, null]`, or a `tool_use` block);
  - a second assistant event;
  - a first violation other than `result out of order`;
  - a result with `is_error: false`;
  - the auth event split across read chunks versus in one chunk (both must classify the same).
- The worker and verify parsers in token mode both get the token env (parametrized).

---

### Task 1: Token mode, scrubbing, OCR protection, token age

**Files:**
- Modify: `services/accounting-service/worker/config.py`: field after `parser_config_dir` (dataclass :42, loader :128)
- Modify: `services/accounting-service/worker/parser.py`: `StreamValidator.__init__`/`feed` (:119), `Parser.__init__`/`_run_once`/`gate` (:323-427), new `_read_token`, `token_age_days`
- Modify: `services/accounting-service/worker/ocr.py`: `protected_paths` (:141)
- Modify: `services/accounting-service/worker/pipeline.py`: `summary.versions` (:810)
- Modify: `services/accounting-service/tests/worker/fake_claude.py`. Add two options:
  - `record_env: Path | None`: writes `{"token_set": bool, "token_sha256": hex}` for `CLAUDE_CODE_OAUTH_TOKEN`, never the raw value.
  - `echo_env_in: str | None`: one of `"tools"`, `"subtype"` or `"tool_name"`; places the env token value in that field.
- Test: `tests/worker/test_config.py`, `test_parser.py`, `test_ocr.py`, `test_pipeline.py`, `test_verify.py`

**Interfaces:**
- Produces:
  - `WorkerConfig.parser_oauth_token_file: Path`
  - `Parser.token_mode: bool`
  - `parser._read_token(path: Path) -> str`
  - `parser.token_age_days(path: Path, now: float | None = None) -> float`
  - `StreamValidator(secret: str | None = None)`

- [ ] **Step 1: Write the failing tests**
  - `test_config.py::test_parser_oauth_token_file_default_and_override`: the default equals `Path("~/.config/homehub-parser-oauth-token").expanduser()`; the env override is honoured.
  - `test_parser.py::test_token_mode_drops_the_writable_credentials_bind`:
    - With the file present: no `/cfg/.credentials.json` element in argv; `--ro-bind <config_dir> /cfg` present; `token_mode is True`.
    - With no file: argv equals `parser.command(cfg, schema)` byte for byte.
    - With a directory at the path: login mode.
  - `test_parser.py::test_token_reaches_the_child_env_not_argv`: parametrized for the worker parser and the verify parser (`credentials_writable=False`, `config_dir=` verify dir), using `record_env`.
    - The parse succeeds.
    - `token_set` is true, and the recorded hash equals the sha256 of the stripped token.
    - The token is in no argv element.
  - `test_parser.py::test_token_file_is_stripped_and_reread`:
    - Write `TOKEN + "\n"`; the recorded hash is that of `TOKEN`.
    - Rewrite the file with `TOKEN-2`; the next parse records the new hash.
  - `test_parser.py::test_bad_token_file_is_auth`: parametrized over
    - `b""`
    - `b"a b"`
    - `b"tok\x00en"`
    - `b"\xff\xfe"`
    - deleted after construction
    - `PermissionError` via monkeypatch

    Each raises `ParseError` with `reason == "auth"` and message `token file unreadable` or `token file malformed`. `exc.__cause__ is None`. No fake content appears in `str(exc)`.
  - `test_parser.py::test_echoed_token_is_scrubbed`: parametrized over `echo_env_in` in {`tools`, `subtype`, `tool_name`}. After `Parser.gate()` and after a failed `parse()`, the fake token is absent from:
    - `json.dumps(report.__dict__, default=str)`
    - `str(exc)`
    - the persisted gate evidence file, via `Gate.run_operator_gate` with `tmp_path` as the state dir

    Assert `[redacted]` only where the field is kept, i.e. the validator envelope's `events`/`tools` and evidence `tools`. Fixed messages such as `tool call other than StructuredOutput`, and the failed gate's `tool_calls` count, carry no replacement string. Do not add tool names to evidence to satisfy the test.
    Also feed `_ChildIO._feed_lines` an echo line split mid-token across two fragments: only `[redacted]` survives.
  - `test_parser.py::test_gate_evidence_names_the_auth_mode`: `evidence["auth"]` is `"token"` or `"login"`.
  - `test_parser.py::test_token_age_days`: a file with mtime `now - 3.04 * 86400` gives `3.0`.
  - `test_ocr.py::test_protected_paths_include_credential_files`:
    - With custom paths under `tmp_path` for the parser token file, `flex_credentials_file` and `portfolio_token_file`, all three realpaths are in `protected_paths`.
    - With `portfolio_token_file=None`, nothing is added for it.
    - `check_config` raises when a runtime bind overlaps any of the three (follow the pattern of the existing overlap test in `test_ocr.py`).
    - A parser token path that is a **symlink** pointing inside a runtime bind is refused by `check_config` (realpath normalization, `ocr.py:149,167-170`).
  - `test_pipeline.py`: in an existing run-status test with the token file present, `versions["parser_token_age_days"]` is a float. Without the file, the key is absent.

- [ ] **Step 2: Run them, expect FAIL**
  Run: `PYTHONPATH=$PWD/../shared-python-lib .venv/bin/python -m pytest tests/worker/test_config.py tests/worker/test_parser.py tests/worker/test_ocr.py tests/worker/test_pipeline.py tests/worker/test_verify.py -q`

- [ ] **Step 3: Implement** per spec D1, D2, D4, D5 and D7:
  - Scrubbing is `raw.replace(secret.encode(), b"[redacted]")` at the top of `StreamValidator.feed`.
  - `Parser._run_once` builds the validator with the token it just read.
  - `_ChildIO` gets its validator from `_run_once`.

- [ ] **Step 4: Run the five files, then `tests/worker` in full; expect PASS**

- [ ] **Step 5: Commit** `feat(worker): parser setup-token mode (#385)`

### Task 2: Auth classification and summary reason

**Files:**
- Modify: `services/accounting-service/worker/parser.py`: `Envelope` (:48), the `StreamValidator` assistant and result branches (:163, :200), `_drive` (:357-380)
- Modify: `services/accounting-service/worker/pipeline.py`: `_latch_host_fault` (:421) and the gate `ParserDisabled` path (:818)
- Modify: `services/accounting-service/tests/worker/fake_claude.py`: `auth_failed: bool` emits the spec D3 prefix (system/init, assistant with `error` and `is_api_error_message` and a text block, result with `is_error: true` and `num_turns: 1`). Add knobs for the Review Focus counter-cases: `auth_content` override, `auth_second_assistant`, `auth_result_is_error`.
- Test: `tests/worker/test_parser.py`, `tests/worker/test_pipeline.py`

**Interfaces:**
- Consumes: Task 1's `StreamValidator(secret=...)`.
- Produces: `Envelope.auth_signature: bool` and `Envelope.result_is_error: bool | None`. `summary.errors` may now contain `auth`, `sandbox` or `cli_version` beside `parser_disabled`.

- [ ] **Step 1: Write the failing tests**
  - `test_parser.py::test_login_failure_prefix_is_auth`: the fake runs with `auth_failed=True`, rc 1 and empty stderr; `reason == "auth"`. Run it twice: once with the whole prefix in one chunk, and once by feeding `_ChildIO._feed_lines` two explicit byte fragments that split mid-line inside the assistant event. The fake's `_split_after_assistant` only splits after whole lines, so do not use it for this. Same result both times.
  - `test_parser.py::test_auth_lookalikes_stay_sandbox`: parametrized over the Review Focus counter-cases; each gives `reason == "sandbox"`.
  - `test_pipeline.py::test_latched_auth_names_the_reason`: three paths all give `"auth" in summary["errors"]` and `"parser_disabled" in summary["errors"]`. The paths are a real-input parse, an OCR repair parse and a latched gate (`Gate.latch("auth on real input", key)`, then `run`).

- [ ] **Step 2: Run, expect FAIL**

- [ ] **Step 3: Implement** spec D3 exactly:
  - The promotion helper is `_promote_auth(validator) -> bool`, checked in the `pump()` except-path (sandbox only) and after `finish()`.
  - The gate path reads the reason from the latch file's recorded text (`<reason> on real input` or the gate canary reason). It maps that text to the fixed vocabulary; an unknown text maps to nothing extra.

- [ ] **Step 4: Run `tests/worker` in full; expect PASS**

- [ ] **Step 5: Commit** `fix(worker): label a missing parser login as auth (#385)`

### Task 3: Deploy notes

**Files:**
- Modify: `services/accounting-service/deploy/statements/README.md`: the "Verify parser login copy" section (:301-316), the gate section (:495-505), and the token-file entry in the env list wherever `STATEMENT_PARSER_CONFIG_DIR` is listed.

- [ ] **Step 1:** Add a section "Parser token (setup-token, preferred)" with the spec D6 steps 1–4 verbatim, including `install -p` for the verify copy (mtime = issuance date). It covers:
  - the `read -rs` install command, mode 0400, separate copies for the worker and verify users, and that the token never goes through argv;
  - one-year expiry, with Grafana warning at 358 days of `parser_token_age_days`;
  - that bare mode ignores the env var and the parser does not use `--bare`;
  - rollback: remove the file, then `/login` plus a refresh stopgap.
  Mark the existing `/login` instructions as the fallback "login mode". Note in the gate section that evidence carries `auth`.
- [ ] **Step 2:** `git diff --check` is clean, and every path named in the section exists in `worker/config.py` defaults.
- [ ] **Step 3: Commit** `docs(statements): parser setup-token deploy notes (#385)`
