# Statement parser: long-lived subscription token (#385, part 2)

Status: draft for one Multica spec+plan round. Part 1 of #385 (Grafana/Loki alerts) is already live outside the repo.

## Why

The statement parser runs the pinned Claude Code CLI (`claude -p`, 2.1.296) inside bwrap. It authenticates with the
worker's interactive `/login` (Claude.ai subscription OAuth).

That login has two lifetimes:
- The access token lasts 8 h. The sandboxed CLI cannot persist a refresh, so a host stopgap refreshes it hourly. That stopgap is the worker-user timer `homehub-parser-refresh`, which also re-copies the verify login.
- The refresh credential lasts about 4 weeks. Refreshes do not extend it.

On 2026-11-08 the refresh credential expired. The parser latched `parser_disabled` and 11 statements went unparsed for 10 days.

`claude setup-token` issues a **one-year** subscription OAuth token. It can only make model requests, which is all the parser does, and it is billed to the same Max subscription. The CLI reads it from `CLAUDE_CODE_OAUTH_TOKEN`, which ranks above a stored `/login`.

Verified on the host on 2026-11-18, using the worker checkout's `parser.command()` argv, an **empty** config dir and fictional stdin:

| Case | Result |
|---|---|
| Unsandboxed, token in env | rc 0, structured output |
| Sandboxed (bwrap, `/cfg` read-only, no `.credentials.json` bind), token in env | rc 0, structured output, config dir still empty afterwards |
| Sandboxed, no token (control) | rc 1, result text `Not logged in · Please run /login`, **stderr empty** |

## Goals

- When a token file is present, the parser (worker and verify mounts) authenticates with it. No writable credentials file is bound into the sandbox, and no refresh stopgap is needed.
- The token never appears in argv, logs, gate evidence, run summaries or the repo. It travels only through the child process environment, and child output echoing it is scrubbed (D7).
- An auth failure is classified as `auth` (host-fault latch) even when the CLI reports it only in the result event, as in the control case above.
- The run summary exposes the token's age, so Grafana can warn before the one-year expiry.

## Non-goals

- No Console API key (owner chose setup-token on 2026-11-18).
- No change to the sandbox flags, the CLI pin, the model, the stream validator or the gate key.
- No automatic token renewal (impossible: `setup-token` needs a browser).
- The refresh stopgap script is removed from the host at deploy. It is not folded into the repo.

## Design

### D1 Config

`STATEMENT_PARSER_OAUTH_TOKEN_FILE` maps to `WorkerConfig.parser_oauth_token_file: Path`. The default is `~/.config/homehub-parser-oauth-token`.

- **Token mode** is on when that path is a regular file (`Path.is_file()`).
- When the file is absent, behaviour is exactly as today (login mode). This keeps rollout and rollback to "add or remove one file".

### D2 Parser

- `Parser.__init__` records `token_mode = cfg.parser_oauth_token_file.is_file()`.
  - A directory or a missing path means **login mode**.
  - Token mode forces `credentials_writable = False`, so every mount, the worker's included, binds `/cfg` read-only with no `.credentials.json` bind.
- `_run_once` reads the token file **on every call**, so a rotated token needs no restart. The reader is `_read_token(path) -> str`:
  - It reads bytes and decodes them as strict ASCII.
  - It strips surrounding whitespace, so a trailing newline is accepted.
  - It requires the result to match `^[A-Za-z0-9_-]+$`. That rejects empty values, internal whitespace, NUL and any non-ASCII byte.
  - On any `OSError` (missing after start, permission), `UnicodeDecodeError` or pattern miss it raises `ParseError("auth", "token file unreadable")` or `ParseError("auth", "token file malformed")`. These fixed messages never carry the content or the original exception text. The original exception is suppressed (`from None`).
- The token goes into the hand-built Popen `env` as `CLAUDE_CODE_OAUTH_TOKEN`. bwrap inherits the environment, and `--setenv` overrides only HOME, CLAUDE_CONFIG_DIR and PATH. It is never passed through `--setenv` or argv, which `ps` shows.
- This applies to both the worker parser and the verify parser (`verify.py:287`, `cli.py:200`): both read the same configured file of their own user.
- `cli_version()` is unchanged: `--version` needs no auth.
- Gate evidence (`Parser.gate`) gains `"auth": "token" | "login"`.

### D3 Auth classification

Today a missing or expired login is reported as `ParseError("sandbox")`. It still latches, but the label sends the operator looking for a sandbox breach. This was the 2026-11-08 symptom: `ParseError sandbox "result out of order"`.

The real no-login stream, captured on the host on 2026-11-18 through `parser.command()`, is:

```
{"type":"system","subtype":"init", ...}
{"type":"assistant","error":"authentication_failed","is_api_error_message":true,"message":{"content":[{"type":"text", ...}]}, ...}
{"type":"result","subtype":"success","is_error":true,"num_turns":1,"result":"Not logged in · Please run /login"}
```

rc is 1 and stderr is empty. The I/O pump checks violations **fail-fast** (`_ChildIO._check_violation`, parser.py:236-247). The out-of-order `result` therefore raises `ParseError("sandbox", "result out of order")` during `pump()` and kills the child. Nothing after that line is read.

The promotion contract is therefore limited to the stream **prefix up to and including the first violation**:

- `StreamValidator` records `auth_signature = True` only for an `assistant` event that:
  - is the **first** assistant event;
  - carries the CLI's top-level fields `error == "authentication_failed"` **and** `is_api_error_message is True`;
  - has content that is a well-formed list of `text` blocks only.

  It records this before any shape rejection. It also records `result_is_error` from the result event.
- In `_drive`'s `pump()` except-path and after `finish()`, `ParseError("sandbox")` becomes `ParseError("auth")` only when **all** of these hold. Every other case keeps today's sandbox precedence and kill behaviour.
  1. `auth_signature`;
  2. `assistant_messages == 1` and `tool_names == []`;
  3. the validator's **first** violation is exactly `result out of order`;
  4. `result_is_error is True`.
- The existing stderr `AUTH_MARKERS` check is unchanged.

The worker summary also names the reason:
- `_latch_host_fault` (pipeline.py:421) appends the safe reason (`auth`, `sandbox` or `cli_version`, a fixed vocabulary) to `summary.errors` next to `parser_disabled`.
- The gate-failure path (pipeline.py:818) maps the latched reason in the same way.
- This holds for real-input, OCR-repair (:463) and gate paths. `verify.py:368` already does it.

### D4 OCR child

`ocr.protected_paths()` adds `cfg.parser_oauth_token_file`, `cfg.flex_credentials_file` and `cfg.portfolio_token_file` (when it is not None). The last two were already missing from the list.

All three are configurable outside `~/.config`. `check_config` then refuses any runtime bind that overlaps them, comparing realpaths.

### D5 Token age

In token mode the run summary's `versions` block gains `parser_token_age_days`: the age of the token file's mtime in days, as a float with 1 decimal. An `OSError` omits the key. In login mode the key is absent.

mtime is the issuance date only if the file is never rewritten in place. Deploy copies use `install -p` or `cp -p`, and a new token gets a new file. The README says so.

Grafana (outside the repo) alerts when the age exceeds 358 days, 7 days before the one-year expiry.

### D6 Deploy (documented in `deploy/statements/README.md`, done by hand after merge)

1. The owner runs `setup-token` with a throwaway `CLAUDE_CONFIG_DIR` and stores the token with `read -rs` into `~homehub-worker/.config/homehub-parser-oauth-token` (0400). Done 2026-11-18.
2. Install the same token for the verify user (`~homehub-verify/.config/homehub-parser-oauth-token`, 0400) with `install -p`, which keeps the mtime. A setup-token does not rotate on use, so sharing it is safe, unlike a `/login` copy.
3. Deploy the worker code, then run `gate.sh` (both mounts; evidence `auth: token`).
4. Disable and remove `homehub-parser-refresh.{service,timer}` and its script. Move `.credentials.json` out of both parser login dirs into a 0700 backup, so that the token is the only credential.
5. Grafana: replace the `hh-parser-refresh-*` and `hh-parser-login-expiring` rules with `hh-parser-token-expiring` (age > 358). Keep the statements rules. Remove the 2026-12-09 re-login reminder timer.

Rollback: remove the token file. Login mode returns, but needs a fresh `/login` plus the refresh stopgap.

### D7 Echo scrubbing (defence in depth)

The token lives in the child's environment. The model has no tools, so it cannot read that environment. Only a misbehaving CLI binary could echo it, and such a binary already has the network and could exfiltrate it directly.

Even so, child-controlled strings reach `ParseError` messages, gate reasons and evidence (event/type/subtype/tool names, parser.py:138,148,212,420), so:

- In token mode `StreamValidator` receives the token and replaces every occurrence of it in each **raw line** with `[redacted]`, before JSON parsing. The token alphabet `[A-Za-z0-9_-]` needs no JSON escaping, so the raw text matches.
- Everything downstream (events, tools, violations, gate evidence and reasons, the persisted gate file, summary, logs) is therefore derived from scrubbed text.
- **Out of scope:** an echo of a *partial* or transformed token. The threat-model boundary above applies.

## Risks

- **A future CLI pin ignores the env var.** The gate canary fails with `auth`, the run latches and alerts. The README notes that bare mode ignores the variable and that the parser does not use `--bare`.
- **The token is revoked early**, for example by a password change or a subscription change. Same latch-and-alert path. Re-run D6 step 1.
