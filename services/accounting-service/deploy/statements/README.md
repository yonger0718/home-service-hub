# Statement worker deploy notes (R2)

Ops files for the statement ingest worker (`python -m worker`, see "Statement ingest worker (R2)" in
`services/accounting-service/README.md`). Nothing here is installed by the repo; copy or link by hand.

Interim principal: the units are systemd **user** units for `opc` (linger is already on; timers not enabled, see below). The same files move to the
dedicated `homehub-worker` user when the section 3 split below is done. The reconciliation feature flag stays off
in production and no real-data `run`/`backfill` happens until then; the first real use is `export-masked` + `verify`.

## Files

| File | Purpose |
|---|---|
| `homehub-statements-poll.service` / `.timer` | every 5 minutes: `python -m worker poll` (claims enqueued runs only) |
| `homehub-statements.service` / `homehub-statements-daily.timer` | 03:30 Asia/Taipei, `Persistent=true`: `python -m worker run --trigger timer` (full run, ends with the sweep) |
| `gate.sh` | operator evidence: `exec .venv/bin/python -m worker gate` (prints JSON, exit 0 only when every check passed) |

Both services read `EnvironmentFile=-%h/.config/homehub-statements.env` (optional, mode 0600) for `STATEMENT_*`
overrides; every variable has a default (table in the service README).

## Install (timers stay disabled for now)

During the interim deviation (rulings 3/11: `opc`, scope `export-masked` and `verify` only, no real-data `run`) the units are
installed but the timers are NOT enabled:

    mkdir -p ~/.config/systemd/user
    ln -sf ~/workspace/home-hub/services/accounting-service/deploy/statements/homehub-statements*.{service,timer} ~/.config/systemd/user/
    systemctl --user daemon-reload

Today's hard guard is the feature flag: with `ACCOUNTING_RECONCILIATION_ENABLED` off the statement routes answer 404, so a
`run` or `poll` cannot submit anything.

## After the section 3 checklist is complete

Only once the dedicated users exist (checklist at the end of this file) and the feature is switched on:

    systemctl --user enable --now homehub-statements-poll.timer homehub-statements-daily.timer
    systemctl --user list-timers 'homehub-statements*'

A second worker (overlapping tick, manual run during the timer) prints `already running` and exits 0, so overlapping
ticks never fail the unit (`gate`, `verify` and `export-masked` exit 3 instead). For manual runs load the env file:
`set -a; . ~/.config/homehub-statements.env; set +a`. Without the worker API token and the password file the run path exits 2.

## Read-only database role for `verify` (ruling 11)

`verify` requires `STATEMENT_VERIFY_DB_URL` (no fallback to the application URL). Run as the database superuser
(`docker exec -i stonk-postgres-1 psql -U "$POSTGRES_USER" accounting_db`):

    CREATE ROLE accounting_ro LOGIN PASSWORD '<generated>';
    GRANT CONNECT ON DATABASE accounting_db TO accounting_ro;
    GRANT USAGE ON SCHEMA public TO accounting_ro;
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO accounting_ro;
    ALTER DEFAULT PRIVILEGES FOR ROLE <migration role> IN SCHEMA public GRANT SELECT ON TABLES TO accounting_ro;
    ALTER ROLE accounting_ro SET default_transaction_read_only = on;
    REVOKE CREATE ON SCHEMA public FROM PUBLIC;

`ALTER DEFAULT PRIVILEGES` only covers tables created by the role that runs it (or named in `FOR ROLE`), so it must name the role
that runs Alembic migrations, the application's database user from `.env` (`POSTGRES_USER`); otherwise tables created by later
migrations are unreadable and `verify` fails with permission denied. (The `REVOKE ... FROM PUBLIC` removes the implicit schema CREATE for every role without a `TO accounting_ro` clause;
check that no other application role relied on it before running it.) Audit; attach the output to the PR, it lists
privileges only:

    SELECT rolname, rolsuper, rolcreaterole, rolcreatedb, rolinherit FROM pg_roles WHERE rolname = 'accounting_ro';
    SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants
     WHERE grantee IN ('accounting_ro', 'PUBLIC') AND table_schema = 'public' AND privilege_type <> 'SELECT';
    SELECT r.rolname AS member_of FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid
     JOIN pg_roles u ON u.oid = m.member WHERE u.rolname = 'accounting_ro';

Expected: no superuser, createrole or createdb; the second query returns no rows (no non-SELECT grant for
`accounting_ro` or `PUBLIC`); the third returns no rows (no role memberships). Denied-write check, against a disposable
synthetic table only (never a real table): connect as `accounting_ro`, `SHOW transaction_read_only;` must say `on`,
and `INSERT INTO <synthetic> VALUES (1);` must fail with a read-only error. Then repeat with the read-only default
overridden, which any role may attempt (`SET default_transaction_read_only = off; BEGIN READ WRITE; INSERT INTO
<synthetic> VALUES (1);`): the INSERT must now fail with `permission denied for table` (the privilege grants are the
real guard; the read-only default only catches mistakes), then `ROLLBACK`.

Placement: the URL goes into `~/.config/homehub-statements.env` as `STATEMENT_VERIFY_DB_URL=...` (mode 0600) and
nowhere else (not in the root `.env`, not in a unit file, not in a log or PR text). The worker never prints it
(`WorkerConfig.verify_db_url` has `repr=False`).

## Verify parser login copy

`verify` runs the parser child with the login directory bound read-only and no writable credentials file, so an
expired token fails `verify` with `auth` instead of being refreshed. It has its own login at
`STATEMENT_VERIFY_PARSER_CONFIG_DIR` (default `~/.local/state/home-hub-parser/claude-verify`), separate from the worker
login (`STATEMENT_PARSER_CONFIG_DIR`, default `~/.local/state/home-hub-parser/claude`). Never copy
`~/.claude/.credentials.json` or one login into the other: a copied refresh token rotates on use and can log out the
owner's interactive CLI and the Multica agents.

    dst=~/.local/state/home-hub-parser/claude-verify
    chmod u+w "$dst" "$dst"/.credentials.json 2>/dev/null; mkdir -p "$dst"   # a re-login rewrites the 0400 file
    CLAUDE_CONFIG_DIR="$dst" claude /login
    chmod 400 "$dst"/.credentials.json; chmod 500 "$dst"

The worker login is created the same way (`.../claude`, then `chmod 700` the directory and `chmod 600 .credentials.json`).
After a new login run `gate.sh` again so the gate key (which hashes the contents of the login directory's files up to 1 MiB, `size:mtime_ns` above that, excluding the top-level `.credentials.json`) matches. The operator gate runs the canary with this login too when the directory exists.

## Pinned CLI copy

`~/.local/bin/claude` is the auto-updater's symlink. Copy the pinned binary and set `STATEMENT_PARSER_CLI` to it:

    mkdir -p ~/.local/state/home-hub-parser/bin
    cp "$(readlink -f ~/.local/bin/claude)" ~/.local/state/home-hub-parser/bin/claude-2.1.296
    chmod 500 ~/.local/state/home-hub-parser/bin/claude-2.1.296

Bump the pin: copy the new binary, update `STATEMENT_PARSER_CLI` and `STATEMENT_PARSER_CLI_VERSION`, run `gate.sh`.
The parser version changes, so files that failed `parse`/`guardrail`/`no_text_layer`/`too_large` are re-parsed once
(one model call per file).

## Operator gate (`gate.sh`)

Run it after any change to the CLI, model, sandbox flags or login directory, and to clear a latch
(`<state_dir>/parser-disabled.json`):

    services/accounting-service/deploy/statements/gate.sh

It runs the canary with the worker login, the canary with the verify login, and the host checks (positive control
`/usr/bin/true` inside bwrap; `/etc/passwd` and `$HOME` absent inside; login directory unchanged across a canary;
only bwrap/claude descendants observed during each canary). Evidence is written to `<state_dir>/gate-<hash>.json`
(one file per parser configuration); the latch is removed only when every check ran and passed.

## Raw PDFs on disk

Decrypted bytes are never stored, but the downloaded (still encrypted) PDFs are retained, all under 0700 directories:

- `STATEMENT_STATE_DIR/inbox/by-sha/<sha256>.pdf` (the run path's local copy; the object store, when configured, holds the same bytes);
- `STATEMENT_VERIFY_DIR/pdf-cache/inbox/by-sha/<sha256>.pdf` (the verify path's own cache, filled by `export-masked`).

Masked snapshots, parse results and reports live under `STATEMENT_VERIFY_DIR/{masked,parsed,reports}`. To purge after
a verify campaign: `rm -rf "$STATEMENT_VERIFY_DIR"` (default `~/.local/state/home-hub-verify`; the next
`export-masked` re-downloads). Purge the run inbox (`rm -rf <state_dir>/inbox`) only when the object store holds every object and no file is
`store_pending`; the local copy is the source for a pending object-store put.

## Section 3 checklist: dedicated users (replaces the interim `opc` principal)

The interim run as `opc` is an accepted deviation from design section 3 (see spec section 17), not compliance. Expiry =
completing this list:

1. Create `homehub-worker` (runs the worker, owns `~/.config/homehub-statement*` files, the state dir, the rclone
   remote and the worker parser login) and `homehub-verify` (read-only verify, owns the verify dir, the verify parser
   login copy and `STATEMENT_VERIFY_DB_URL`), separate from the application user and from `opc`.
2. Move the rclone config for `gdrive:`, the password file, the API token file (worker label, `ingest` scope only), the
   MinIO key file and the parser login to the worker user, all 0600 in 0700 directories; nothing readable by `opc` or
   by the application user.
3. `verify` imports `app.models` -> `app.database` -> `load_dotenv(.env)`, so under the user split `homehub-verify` must get its own environment without the application's write-role `.env`.
   Install the units under the worker user (`loginctl enable-linger homehub-worker`), set `STATEMENT_*` paths in that
   user's `homehub-statements.env`, run `gate.sh` as that user and keep its evidence.
4. Rotate every credential that `opc` ever held: the worker API token, the password file contents (bank passwords
   stay the owner's, rotate the Drive/rclone token and the parser login), the `accounting_ro` password.
5. Only then enable `ACCOUNTING_RECONCILIATION_ENABLED`, then `dirty_enabled`, and allow the first real-data `run`.
