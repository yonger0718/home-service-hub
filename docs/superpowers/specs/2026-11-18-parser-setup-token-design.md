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
- The token never appears in argv, logs, gate evidence, run summaries or the repo. It travels only through the child process environment.
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
- In token mode, `credentials_writable` is forced to `False`. So `_sandbox_prefix` binds `/cfg` read-only with no `.credentials.json` bind, for the worker mount too.
- `_run_once` reads the token file on every call, so a rotated token needs no restart.
  - It strips surrounding whitespace.
  - An empty value, internal whitespace or an `OSError` raises `ParseError("auth")`, which latches like any auth failure.
  - It adds `CLAUDE_CODE_OAUTH_TOKEN` to the hand-built Popen `env`. bwrap inherits the environment, and `--setenv` overrides only HOME, CLAUDE_CONFIG_DIR and PATH.
  - The token is never passed through `--setenv`, which would put it in argv and make it visible in `ps`.
- `cli_version()` is unchanged: `--version` needs no auth.
- Gate evidence (`Parser.gate`) gains `"auth": "token" | "login"`. It never includes the token or the path contents.

### D3 Auth classification

Today a missing or expired login is reported as `ParseError("sandbox")`, because the stream validator sees violations and sandbox precedence wins. It still latches, but the label sends the operator looking for a sandbox breach. This was the 2026-11-08 symptom: `ParseError sandbox "result out of order"`.

The real no-login stream, captured on the host on 2026-11-18 through `parser.command()`, is:

```
{"type":"system","subtype":"init"}
{"type":"assistant","error":"authentication_failed", ...}
{"type":"result","subtype":"success","is_error":true,"num_turns":1,"result":"Not logged in · Please run /login"}
```

rc is 1, stderr is empty, and the violations are `result out of order`, `num_turns is not 2` and `tool calls: 0`.

Change:
- `Envelope` gains `auth_failed: bool`.
- `StreamValidator` sets it when an `assistant` event carries the CLI's top-level field `error == "authentication_failed"`. This is a structured field the CLI writes. The model's content cannot set it.
- In `_drive`, **before** the sandbox-precedence check, a stream with `auth_failed` raises `ParseError("auth")`, provided `tool_names` is empty, meaning no tool call happened at all.
- A stream that also made a tool call keeps today's sandbox precedence.
- The existing stderr `AUTH_MARKERS` check stays as is.

Both `auth` and `sandbox` latch (`pipeline.py` :463/:506, `verify.py` :368), so only the label and the summary's `errors` entry change. That is enough for the Grafana errors rule and for the operator to see "auth".

### D4 OCR child

`ocr.protected_paths()` adds `cfg.parser_oauth_token_file`. The file already sits under `~/.config`, which is protected, but the path is configurable.

### D5 Token age

In token mode the run summary's `versions` block gains `parser_token_age_days`: the age of the token file's mtime in days, as a float with 1 decimal. This is the date `setup-token` was saved. In login mode the key is absent.

Grafana (outside the repo) then alerts when it exceeds 358 days, 7 days before the one-year expiry.

### D6 Deploy (documented in `deploy/statements/README.md`, done by hand after merge)

1. The owner runs `setup-token` with a throwaway `CLAUDE_CONFIG_DIR` and stores the token with `read -rs` into `~homehub-worker/.config/homehub-parser-oauth-token` (0400). Done 2026-11-18.
2. Install the same token for the verify user (`~homehub-verify/.config/homehub-parser-oauth-token`, 0400). A setup-token does not rotate on use, so sharing it is safe, unlike a `/login` copy.
3. Deploy the worker code, then run `gate.sh` (both mounts; evidence `auth: token`).
4. Disable and remove `homehub-parser-refresh.{service,timer}` and its script. Move `.credentials.json` out of both parser login dirs into a 0700 backup, so that the token is the only credential.
5. Grafana: replace the `hh-parser-refresh-*` and `hh-parser-login-expiring` rules with `hh-parser-token-expiring` (age > 358). Keep the statements rules. Remove the 2026-12-09 re-login reminder timer.

Rollback: remove the token file. Login mode returns, but needs a fresh `/login` plus the refresh stopgap.

## Risks

- **A future CLI pin ignores the env var.** The gate canary fails with `auth`, the run latches and alerts. The README notes that bare mode ignores the variable and that the parser does not use `--bare`.
- **The token is revoked early**, for example by a password change or a subscription change. Same latch-and-alert path. Re-run D6 step 1.
