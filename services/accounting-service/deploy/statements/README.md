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
After a new login run `gate.sh` again so the gate key (which hashes the contents of the login directory's files up to 1 MiB, `size:mtime_ns` above that, excluding the top-level `.credentials.json`) matches. The operator gate runs the canary with this login too when the directory exists. An expired OAuth access token makes the sandboxed CLI emit `system/api_retry` events before it refreshes (the parser ignores them since 2026-10-13); the read-only `claude-verify` copy cannot refresh, so re-take it (chmod 500/400) after any refresh, or `verify` fails with `auth`.

## Pinned CLI copy

`~/.local/bin/claude` is the auto-updater's symlink. Copy the pinned binary and set `STATEMENT_PARSER_CLI` to it:

    mkdir -p ~/.local/state/home-hub-parser/bin
    cp "$(readlink -f ~/.local/bin/claude)" ~/.local/state/home-hub-parser/bin/claude-2.1.296
    chmod 500 ~/.local/state/home-hub-parser/bin/claude-2.1.296

Bump the pin: copy the new binary, update `STATEMENT_PARSER_CLI` and `STATEMENT_PARSER_CLI_VERSION`, run `gate.sh`.
The parser version changes, so files that failed `parse`/`guardrail`/`no_text_layer`/`too_large` are re-parsed once
(one model call per file).

`verify` exits 1 when its totals show `errors`, `listing_failed`, `parse_failed > 0` or `verify_errors > 0`, 2 when refused, 3 when another worker holds the lock.

## OCR child boundary (R5a)

`worker/ocr_child.py` renders a PDF with pypdfium2 and reads it with RapidOCR (models ship in the
`rapidocr-onnxruntime` wheel; nothing is downloaded). `worker/ocr.py` starts it as
`<real interpreter> -B -m worker.ocr_child --threads N --rlimit-as-mb M --rlimit-cpu-s S` with an explicit minimal
environment and, with `STATEMENT_PARSER_SANDBOX=true`, under bubblewrap: `--unshare-all` (no network),
`--die-with-parent --new-session --cap-drop ALL`, read-only binds of `/usr`, `/lib`, `/lib64`, the interpreter's
prefix, the venv's `site-packages` and `worker/` at `/work/worker`; `/etc`, home, state and config directories are
absent, and a configuration whose protected paths fall under a bound prefix is refused (`OCRConfigError`).

Child order: rlimits (no core dumps, address space, CPU) → fd 1 moved to `/dev/null` with the protocol line written on
a saved descriptor → readiness (imports, engine, self-test) → header line read byte by byte → exactly `pdf_len`
bytes → page bound → per page a pixel bound before any allocation → recognise → one JSON line. The parent streams
stdout with a 2 MB cap, enforces the wall-clock deadline by killing the child's session group, and classifies by
what it observed: deadline or SIGXCPU → retry later (`OCRTimeout`), `resource` (MemoryError in the child) →
retry later, `unavailable` or a failed start → `OCRUnavailable` (cached for the run), cap overflow / crash / any
other non-zero exit / invalid response → `Malformed`. Nothing of the statement reaches disk, logs or exceptions.

R5c wires the child into the pipeline and `export-masked` (next sections). Operator evidence on the host, as the worker user:

```
cd ~/workspace/home-hub/services/accounting-service
.venv/bin/python -m pytest tests/worker/test_ocr.py tests/worker/test_ocr_child.py -q        # includes real-bwrap teardown and path-denial tests
RUN_OCR_MODEL_TESTS=1 .venv/bin/python -m pytest tests/worker/test_ocr_models.py -q -rs -s   # real engine inside the sandbox; 2 passed, 0 skipped
```
Record the printed JSON (SHA, platform, Python, OCR fingerprint) with the release.
## OCR masking (R5b)

`Masker.mask_ocr` is the OCR-path entry point (wired by R5c; the text-layer path `Masker.text`/`render` is
unchanged). Per line: NFKC, then invisible format characters and nonspacing marks that did not compose dropped →
OCR confusables (O/o → 0, I/l/| → 1) corrected inside runs of digit-like tokens that are at least 70 % digits, or
that are 12 or more characters long whatever their digit share (a card group read as OOOO or llll; a token made only
of pipes is a table border and never joins a run), and in the digit part of an ID-shaped token → three views of the
line, each mapped back to line positions: compacted (no whitespace and no group separators — dash, en/em dash,
hyphen, minus sign, dot, middle dots, slash, pipe — casefolded per character), alphanumeric-only (holder names, so
punctuation between name parts is ignored) and digits-only (printed dates and spans already claimed by a literal or
an ID blanked, the same separators dropped) → literal spans (the ID, holder names, birth patterns, password
candidates ≥ 4 characters, boundaries judged on the real line: an ASCII letter or digit next to the span blocks it,
a CJK neighbour does not, plus the text path's date exclusions for numeric spans), any ID-shaped span
`[A-Za-z][12]\d{8}` whose letter may also be a Cyrillic or Greek lookalike (only at that position), any run of 12 or
more digits → only the matched spans are replaced (overlaps merged), so the other columns of a row survive → then
the shared rules (`text`). The email rule is main's pattern, applied only when the text contains `@`, so a long
dotted line without an address stays linear.

Known limitations (ruling 2026-10-14): a standalone numeric token equal to a password candidate is masked exactly as
on the text path (privacy over completeness; a balance broken that way fails the guardrail and goes to review);
single-spaced digit groups of three or more digits mask as on the text path, so a row of bare amounts without
thousands separators masks the same way on both paths, and pipes or dashes between short numbers count like spaces
(`350 | 1200 | 15000` is one 12-digit run); an identifier wrapped across two OCR lines is not reassembled and the
external parser receives both lines in one stdin blob (`4111 2222 3333` / `4444` → `[NUM…3333]` / `4444`); a
decimal amount whose digits equal a password candidate masks (`12.34` with candidate `1234`); NFKC also rewrites
compatibility characters in unmasked text (① → 1, full-width punctuation).

## OCR integration (R5c)

Install both venvs (worker and verify) with the frozen constraints, so the OCR wheels and everything under them are
the versions the evidence tests ran against. The file is generated fail-closed by `python -m worker.constraints
--python .venv/bin/python --uv /usr/local/bin/uv` (every installed distribution exactly once as `name==version`; a
URL, VCS, editable or unpinned requirement, a duplicate or an omission stops it; only the local editable shared
library is exempt) and the operator evidence line carries the SHA-256 of its bytes:

    uv pip install --python .venv/bin/python -r requirements.txt -r requirements-worker.txt -c requirements-worker-constraints-aarch64-py313.txt

A file whose page 1 has fewer than 200 characters of text, or whose page 1 glyphs are at least 45 % unmapped
`(cid:N)` references (a Type3 font without a ToUnicode map: 遠東, 渣打; 國泰 cards sit at 36–40 % and stay on the text
path), has no usable text layer. With `STATEMENT_OCR=true` the worker
hands the PDF bytes and the unlock password to the R5a child on its main thread (the engine fails closed from any
other thread) and the file then takes the text-layer path: the OCR text goes through `Masker.mask_ocr`, the parser
`STDIN_CAP` and the sandboxed parser; the revision's `parser` is `claude-cli+ocr` and `raw.notes` ends with one line
`ocr: mean_confidence=0.93 boxes=262 empty_pages=0 scale=3.0` after the parser's own notes. The guardrails are the
check, as for every other file; `has_text_layer` stays false. `STATEMENT_OCR=false` restores the old behaviour
(`needs_review/no_text_layer`).

A text-layer file whose parse fails with parse/schema or an API 422 (中信存款's digits-only text, the odd 華南/元大
statement) is OCR'd once in the same run and parsed once more — the **fallback**; the revision then carries
`parser=claude-cli+ocr` and a second note line `ocr: fallback after text-layer parse failure`, `has_text_layer`
stays true. It never fires on guardrail, transient, sandbox, password, mapping or too_large outcomes, nor while the
parser latch is set. If OCR is unavailable the file stays `failed/parse` and the run reports `ocr_unavailable`; any
other OCR outcome leaves it `failed/parse`. A timeout, exit or schema failure on the second parse ends
`failed/parse` at the current parser version and retries only at the next one, so the fallback costs at most one
OCR start and one extra parser call per file per version; a sandbox, auth or CLI-version failure on that second
call is a host fault: the parser latch is set and the row is `failed/sandbox`, and once `gate.sh` clears the latch
the file is processed again (it may fall back again). OCR text that is not UTF-8 is rejected at the
same pre-mask boundary (`failed/parse`, or `errors` in export-masked). The run summary counts `ocr_trigger` and
`ocr_fallback` at the moment the child is started, whatever follows, and the journal carries one line per OCR
outcome with the outcome class only (`ocr trigger: OCRTimeout`, `ocr fallback: text too_large`).

Outcomes: the host cannot start the child or the engine (`unavailable`) → the file keeps its status (`new` stays
`new`, a review row keeps its failure), the run reports `ocr_unavailable` once, finishes `done`, and `run` and
`poll` exit 1 so the unit shows failed (`poll` carries the union of its runs' codes, so a degraded run followed by a
clean one still exits 1); a deadline, CPU limit, memory exhaustion or low confidence → `needs_review/no_text_layer`;
page or pixel bounds, OCR text over 400 KB or any OCR line over 4 096 characters (the child's text is untrusted and
the masker's cost grows with the square of a line's length) → `needs_review/too_large` before any masking; a bad
PDF, a crash or an invalid response → `failed/parse` at the current parser version. The engine's self-test runs once
per run; a host fixed between runs recovers at the next run without a restart.

The parser version is `claude-cli-<cli>-<model>-<12-hex digest>` over the full CLI version, model id, instruction,
schema and OCR fingerprint (engine versions + layout rules), 61 characters at most. Deploying R5 re-parses every
`needs_review`/`failed` file whose failure is `parse`/`guardrail`/`no_text_layer`/`too_large` once, never a `parsed`
one. The operator gate keys on the parser argv (the instruction is part of it), so this release re-runs `gate.sh`;
an OCR-only bump later does not. `STATEMENT_PARSER_CLI_VERSION` and `STATEMENT_PARSER_MODEL` must match
`[A-Za-z0-9._-]{1,64}` or the worker refuses to start.

`export-masked` takes the same branch: each snapshot's `.json` meta carries `extraction` (`text` or `ocr`) and, for
OCR, `ocr_fingerprint`; the summary counts `ocr` and sets `ocr_unavailable: true` once (those files count as
`errors`, so the command exits 1). Snapshots are cached by Drive identity: to refresh an OCR snapshot after an
engine change delete its `<sha>.txt` and `.json` and run `export-masked` again. `verify` caches parses by parser
version, so this release re-parses every snapshot once.

Operator evidence before the first real input, as the worker user, at the deployed SHA:

    RUN_OCR_MODEL_TESTS=1 .venv/bin/python -m pytest tests/worker/test_ocr_models.py -q -rs -s -p no:cacheprovider   # 2 passed, 0 skipped; keep the JSON line

Rollout (owner go for each step): install both venvs with the constraints; the evidence line above; `gate.sh`; one
`python -m worker run` (expect the OCR-class files `parsed`, text-layer counts unchanged, no `ocr_unavailable`,
exit 0); `coredumpctl list` shows nothing for the worker. R3b/R3c: an "OCR" chip on the statement detail when
`revision.parser` ends with `+ocr`.

Matcher evidence without pytest (the deploy venv has no test runner): run the R6a dry-run from the candidate
worktree as described in `services/accounting-service/scripts/README-r6-dryrun.md` with `BASE` = this checkout
before it is moved, and keep `summary.md` in the release record. `gate.sh` stays the parser/OCR evidence.

## R6b rollout (statement as truth)

Order (each step is a settings PUT with the full body). Production already runs with `policy.enabled: true` and
`create_system_entry` on (settings v8, 2026-10-16), so the new policy actions take effect as soon as each is enabled.

1. Deploy R6b with all three new actions as defaulted (`backfill_entry` and `anchor_opening` off, `replace_stand_in` on, `backfill_grace_days` 14, `backfill_accounts` `[]`). On deploy day, whatever the new flags say, these change:
   - the sweep releases a covered line only on a structural difference (`STRUCTURAL_KEYS`); cosmetic edits (e.g. name, merchant) keep the claim;
   - sweep retirement: an applied creation whose entry the owner deleted in the ledger is retired (`stand_in_retired`), including the catch-up pass for deletions from before R6b (the first sweep of each statement after the deploy), and its line is marked owner-deleted, so the policy no longer re-books it (no proposal either);
   - `needs_peer`: `create_entry` is no longer offered on payment/transfer lines; their open cases are superseded and re-created on the first reconcile (the context key changes), which makes any proposal on them stale;
   - new owner actions on cases: `backfill_entry`, `anchor_opening`, `replace_stand_in`;
   - `replace_stand_in` is on but inert until stand-ins exist (`backfill_entry` off);
   - the `policy_version` label stays as stored (e.g. `r1c-8`) until the next settings PUT, although the effective policy gained an enabled action.
2. Dry-run in policy mode (`--policy` on `r6_case_dump.py` and `r6_dryrun.py`, see "Policy mode (R6b)" in `services/accounting-service/scripts/README-r6-dryrun.md`); keep `policy-summary.md` (mode 600, aggregate only) in the release record, with the sweep regression table from the same run.
3. Back up first (spec §3.1, as on 2026-10-15; nothing prints credentials):

       TS=$(date -u +%Y%m%dT%H%M%SZ); mkdir -p ~/backups/home-hub-r6b
       docker exec stonk-postgres-1 sh -c 'pg_dump -U "$POSTGRES_USER" -Fc accounting_db' \
         > ~/backups/home-hub-r6b/accounting_db-pre-r6b-$TS.dump

   Then enable `backfill_entry` for bank accounts (set `backfill_accounts` to the bank account ids first) and keep the caps (`max_amount`, `per_statement_count`, `per_statement_total`, `monthly_total`) low; `policy.enabled` is already true, so the PUT takes effect at once. Run; watch `policy_applied`, `policy_proposed`, `policy_skipped` and `stand_in_retired` in the audit.
4. Read the `balance_gap` residual against the `needs_peer` lines (payment/transfer lines stay open until R6c).
5. Enable `anchor_opening` (cap `max_amount`, default "200000"); run; the anchor locks the account and its live bank statements and sets `needs_recheck` on each, so reconcile them before confirming.
6. Cards last.

Caps: `replace_stand_in` has amount 0 (no budget use) but counts toward `per_statement_count`. `per_statement_count`
(default 5) bounds every applied policy row per statement for good (system entries, backfills and replacements all count);
the rest become proposals. With the default `per_statement_total` "5000" and `monthly_total` "20000", a real anchor
(often far above those, under its own `max_amount` "200000") becomes a proposal the owner confirms. The policy anchors
only a target that starts less than one calendar month after `statement_live_from` and that no live bank statement of the account precedes; a target after it, or an earlier month that arrives later, is left
to the owner (the dry-run line `anchor above an earlier statement` lists the accounts). A shadow farther than
`exact_window_days` from its line is proposed (`date_drift`), not applied. Revert a wrong `anchor_opening` before any `balance_adjustment_asof` on that account (`closing_adjustments_present`).

## Operator gate (`gate.sh`)

Run it after any change to the CLI, model, sandbox flags or login directory, and to clear a latch
(`<state_dir>/parser-disabled.json`):

    services/accounting-service/deploy/statements/gate.sh

It runs two canaries, one per mount (worker login, verify login), and the host checks for each mount: positive control
`/usr/bin/true` inside bwrap; `/etc/passwd` and `$HOME` absent inside; only `/tmp` writable (for the verify mount
`/cfg/.credentials.json` must be read-only too); login directory content digest unchanged across the canary; and the
descendants of the sandbox child sampled at t=0 and every 50 ms during the canary, which passes only when `claude` or
`bwrap` was actually observed and nothing but bwrap/claude/git/sh was (the CLI spawns a transient `sh` for its own `git` probe; comm sampling cannot tell that from a tool shell, so the stream validator is the primary control and sampling is defence in depth). A failing, empty or erroring sampler fails the check.
Evidence is written to `<state_dir>/gate-<hash>.json` (`host.worker` and `host.verify_login`; one file per parser
configuration); the latch is removed only when every check of both mounts ran and passed.

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
