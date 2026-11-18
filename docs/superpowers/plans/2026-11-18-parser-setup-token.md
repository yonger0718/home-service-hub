# Statement parser setup-token Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The statement parser authenticates with a one-year `claude setup-token` token read from a 0400 file. A missing login is labelled `auth`, not `sandbox`. The run summary reports the token's age.

**Architecture:** Token mode is on when the token file exists. The token travels only through the child Popen environment, never through argv. Every mount binds `/cfg` read-only. An auth signature from the CLI (an assistant event with `error == "authentication_failed"`) is promoted to `ParseError("auth")` when no tool call happened.

**Tech Stack:** Python 3.13, pytest, bwrap. The fake CLI is `tests/worker/fake_claude.py`.

**Spec:** `docs/superpowers/specs/2026-11-18-parser-setup-token-design.md`

## Global Constraints

- Env var `STATEMENT_PARSER_OAUTH_TOKEN_FILE`, field `WorkerConfig.parser_oauth_token_file: Path`, default `~/.config/homehub-parser-oauth-token`.
- Child env var name: `CLAUDE_CODE_OAUTH_TOKEN`. Never use `--setenv` for it, so it is never in argv.
- The token value never appears in an exception message, log line, gate evidence, run summary, test assertion output or fixture. Tests use the fictional value `sk-ant-oat01-FAKE-TEST-TOKEN`.
- Gate evidence key `auth` takes the values `"token"` or `"login"`. Summary key `versions.parser_token_age_days` is a float rounded to 1 decimal, present only in token mode.
- The sandbox flags, `AUTH_MARKERS`, the gate key, `parse_schema.version` and the stream rules are unchanged, except for the one auth promotion in Task 1.
- Backend test recipe (worktree): symlink `.venv` to `~/workspace/home-hub/services/accounting-service/.venv`. Export the DB and OTEL vars with the grep-eval pattern. Run with `PYTHONPATH=$PWD/../shared-python-lib .venv/bin/python -m pytest tests/worker`. Worker tests do not need the full 27-minute suite; run `tests/worker` only.

## Review Focus

- A token file with a trailing newline (the usual `echo >` mistake) must still work, because the token is stripped. A file holding two tokens or containing a space raises `auth`.
- The token file is unreadable (mode 000 or a directory): `ParseError("auth")`, not a crash. `is_file()` is false for a directory, so a directory means login mode.
- The token file is rotated between two parses in one run: the second parse uses the new value, because it is read per `_run_once`.
- An assistant event with `error: authentication_failed` **and** a tool_use in the same stream keeps `sandbox` precedence.
- The verify mount (`credentials_writable=False` already) in token mode gets the token env too. The worker mount in token mode loses its `.credentials.json` bind.

---

### Task 1: Token mode, auth promotion, token age

**Files:**
- Modify: `services/accounting-service/worker/config.py`: field after `parser_config_dir` (dataclass :42, loader :128)
- Modify: `services/accounting-service/worker/parser.py`: `Envelope` (:48), `StreamValidator` assistant branch (:163), `Parser.__init__`/`_run_once`/`_drive`/`gate` (:323-427)
- Modify: `services/accounting-service/worker/ocr.py`: `protected_paths` (:141)
- Modify: `services/accounting-service/worker/pipeline.py`: `summary.versions` (:810)
- Modify: `services/accounting-service/tests/worker/fake_claude.py`: options `auth_failed: bool` (emit the captured no-login stream from the spec D3) and `record_env: Path | None` (the fake writes `{"token_set": bool, "token_sha256": hex}` of `CLAUDE_CODE_OAUTH_TOKEN` to that path, never the raw value)
- Test: `tests/worker/test_config.py`, `tests/worker/test_parser.py`, `tests/worker/test_ocr.py`, `tests/worker/test_pipeline.py`

**Interfaces:**
- Produces: `WorkerConfig.parser_oauth_token_file: Path`; `Parser.token_mode: bool`; `Envelope.auth_failed: bool`; `parser.token_age_days(path: Path, now: float | None = None) -> float`.

- [ ] **Step 1: Write the failing tests**
  - `test_config.py::test_parser_oauth_token_file_default_and_override`: `config.load({}).parser_oauth_token_file == Path("~/.config/homehub-parser-oauth-token").expanduser()`. The env override maps to the given path.
  - `test_parser.py::test_token_mode_drops_the_writable_credentials_bind`: with the token file present, `Parser(cfg, runner).argv()` has no `/cfg/.credentials.json` element and has `--ro-bind <config_dir> /cfg`. `.token_mode is True`. With no file, the current argv is byte-identical (compare with `parser.command(cfg, schema)`).
  - `test_parser.py::test_token_reaches_the_child_env_not_argv` (fake CLI, `record_env`): parse succeeds. The recorded `token_set` is true and its sha256 equals the sha256 of the stripped file content. `"sk-ant-oat01-FAKE-TEST-TOKEN"` is not in any argv element.
  - `test_parser.py::test_token_file_is_stripped_and_reread`: the file holds the token plus `"\n"`, so the recorded hash is that of the stripped token. Rewrite the file with a second fake token and parse again: the hash changes.
  - `test_parser.py::test_bad_token_file_is_auth` (parametrized: empty, `"a b"`, mode 000): `parse()` raises `ParseError` with `reason == "auth"`, and `str(exc)` does not contain the file content.
  - `test_parser.py::test_login_failure_stream_is_auth` (`fake_claude.transcript(auth_failed=True)`, rc 1, empty stderr): `ParseError.reason == "auth"`.
  - `test_parser.py::test_auth_signature_with_a_tool_call_stays_sandbox`: auth-failed assistant event plus a `tool_use` gives `reason == "sandbox"`.
  - `test_parser.py::test_gate_evidence_names_the_auth_mode`: `Parser.gate().evidence["auth"]` is `"token"` with the file and `"login"` without it. The evidence JSON does not contain the token.
  - `test_parser.py::test_token_age_days`: a file with mtime `now - 3.04 * 86400` gives `3.0`.
  - `test_ocr.py::test_protected_paths_include_the_parser_token_file`: a token file set under `tmp_path` (outside `~/.config`) is in `protected_paths`.
  - `test_pipeline.py`: an existing run-status test, with the token file present, shows `versions["parser_token_age_days"]` as a float. Without the file, the key is absent.

- [ ] **Step 2: Run them, expect FAIL** (`AttributeError`/`KeyError` or wrong reason)
  Run: `PYTHONPATH=$PWD/../shared-python-lib .venv/bin/python -m pytest tests/worker/test_config.py tests/worker/test_parser.py tests/worker/test_ocr.py tests/worker/test_pipeline.py -q`

- [ ] **Step 3: Implement**
  - config: the field, plus the loader line `_path(env, "STATEMENT_PARSER_OAUTH_TOKEN_FILE", "~/.config/homehub-parser-oauth-token")`.
  - `Parser.__init__`: `self.token_mode = cfg.parser_oauth_token_file.is_file()`; in token mode set `self.credentials_writable = False`.
  - `_run_once`: in token mode read the file (`OSError` → `ParseError("auth", "token file unreadable")`) and `.strip()`. An empty value or any whitespace inside raises `ParseError("auth", "token file malformed")`. Otherwise `env["CLAUDE_CODE_OAUTH_TOKEN"] = token`.
  - `StreamValidator` assistant branch: `if event.get("error") == "authentication_failed": env.auth_failed = True`, recorded before the existing checks.
  - `_drive`: before both violation checks, `if envelope.auth_failed and not envelope.tool_names: raise ParseError("auth")`. Use the validator's envelope in the `pump()` except path as well.
  - `gate()`: `evidence["auth"] = "token" if self.token_mode else "login"`.
  - `token_age_days`: `round(((now if now is not None else time.time()) - path.stat().st_mtime) / 86400, 1)`.
  - ocr: append `cfg.parser_oauth_token_file`.
  - pipeline: when `services.parser.token_mode` is true, add `parser_token_age_days` to `summary.versions`. On `OSError`, omit it.

- [ ] **Step 4: Run the four files, then `tests/worker` in full; expect PASS**

- [ ] **Step 5: Commit** `feat(worker): parser setup-token mode and auth classification (#385)`

### Task 2: Deploy notes

**Files:**
- Modify: `services/accounting-service/deploy/statements/README.md`: the "Verify parser login copy" section (:301-316), the gate section (:495-505), and the token-file entry in the env list wherever `STATEMENT_PARSER_CONFIG_DIR` is listed.

- [ ] **Step 1:** Add a section "Parser token (setup-token, preferred)" with the spec D6 steps 1–4 verbatim. It covers:
  - the `read -rs` install command, mode 0400, separate copies for the worker and verify users, and that the token never goes through argv;
  - one-year expiry, with Grafana warning at 358 days of `parser_token_age_days`;
  - that bare mode ignores the env var and the parser does not use `--bare`;
  - rollback: remove the file, then `/login` plus a refresh stopgap.
  Mark the existing `/login` instructions as the fallback "login mode". Note in the gate section that evidence carries `auth`.
- [ ] **Step 2:** `git diff --check` is clean, and every path named in the section exists in `worker/config.py` defaults.
- [ ] **Step 3: Commit** `docs(statements): parser setup-token deploy notes (#385)`
