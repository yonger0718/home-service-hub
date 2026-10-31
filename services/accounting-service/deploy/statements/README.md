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

All three units read `EnvironmentFile=-%h/.config/homehub-statements.env` (optional, mode 0600) for `STATEMENT_*`
overrides; every variable has a default (table in the service README).

## Install (timers stay disabled for now)

During the interim deviation (rulings 3/11: `opc`, scope `export-masked` and `verify` only, no real-data `run`) the units are
installed but the timers are NOT enabled:

    mkdir -p ~/.config/systemd/user
    ln -sf ~/workspace/home-hub/services/accounting-service/deploy/statements/homehub-{statements,portfolio-flex,portfolio-cathay}*.{service,timer} ~/.config/systemd/user/
    systemctl --user daemon-reload

Today's hard guard is the feature flag: with `ACCOUNTING_RECONCILIATION_ENABLED` off the statement routes answer 404, so a
`run` or `poll` cannot submit anything.

## After the section 3 checklist is complete

Only once the dedicated users exist (checklist at the end of this file) and the feature is switched on:

    systemctl --user enable --now homehub-statements-poll.timer homehub-statements-daily.timer
    systemctl --user list-timers 'homehub-*'

A second worker (overlapping tick, manual run during the timer) prints `already running` and exits 0, so overlapping
ticks never fail the unit (`gate`, `verify` and `export-masked` exit 3 instead). For manual runs load the env file:
`set -a; . ~/.config/homehub-statements.env; set +a`. Without the worker API token and the password file the run path exits 2.

## IB Flex statement timer (Phase 2, 2026-10-20)

`homehub-portfolio-flex.service` / `.timer` run `python -m worker flex` daily at 15:30 Asia/Taipei (`Persistent=true`). The
worker asks the IB Flex Web Service for the saved Flex Query (SendRequest, then GetStatement, polling while IB prepares it),
keeps the XML, and POSTs it to the portfolio service (`POST /api/portfolio/imports/ib-flex`). Design: spec
`docs/superpowers/specs/2026-10-19-portfolio-integration-design.md` section 7; command and summary keys: "IB Flex statements" in
the service README. The timer is installed with the glob above and NOT enabled until the backfill below is done.

Credentials file `~/.config/homehub-portfolio-flex.env` (worker user, mode 0600), two lines, written by the owner with
`read -s` (the token never on a command line, in shell history, in chat or in the repo):

    sudo -u homehub-worker -H bash -c 'umask 077; read -rs -p "token: " T; echo; read -rp "query id: " Q; printf "IB_FLEX_TOKEN=%s\nIB_FLEX_QUERY_ID=%s\n" "$T" "$Q" > ~/.config/homehub-portfolio-flex.env; chmod 600 ~/.config/homehub-portfolio-flex.env; unset T Q'

Every manual `flex` command below is written out in full: it runs as the worker user, from the service directory, with
`~/.config/homehub-statements.env` loaded (a run without the env file may use another `STATEMENT_STATE_DIR`, hence another
lock and latch).

Overrides (all optional, in `~/.config/homehub-statements.env`): `STATEMENT_FLEX_CREDENTIALS_FILE`, `STATEMENT_FLEX_BASE_URL`,
`STATEMENT_FLEX_TIMEOUT`, `STATEMENT_FLEX_POLL_ATTEMPTS`, `STATEMENT_FLEX_POLL_INTERVAL`, `STATEMENT_PORTFOLIO_API_URL`
(defaults in the service README config table). `STATEMENT_STATE_DIR` is `<state_dir>` below.

State files (0600, directory 0700): `<state_dir>/flex/flex-<ts>-<pid>.xml` (every fetched statement, kept for re-posting),
`<state_dir>/flex-disabled.json` (the latch), `<state_dir>/.flex.lock` (a second run prints `already running`, exit 3).

Latch rule. Only a plain daily run (no `--dry-run`, `--query-id` or `--from-file`) latches. A non-transient failure of
a plain run writes the latch and every later run exits 1 (`flex disabled`) until it is cleared: an
IB status code outside `IB_TRANSIENT_CODES` (1001, 1003-1009, 1018, 1019, 1021), an unparseable response (`xml`), an HTTP
3xx / 4xx other than 408/429 (`http:3xx`), a portfolio 4xx other than 408/429. `--dry-run` / `--query-id` / `--from-file`
runs report the same failures as `failed` codes without latching. These do NOT latch: the IB transient codes, HTTP 5xx / 408 / 429, timeouts, connection errors,
`get:pending` (statement not ready after the poll attempts), a malformed portfolio response (`portfolio:body`) and
`stale_fx` (a successful post that still has missing-FX rows dated more than 3 days ago: the rate will not appear by
itself, add it to `fx_rates`; exits 1 without latching). A run
that fails without latching still exits 1, so the unit shows failed: `systemctl --user list-units --failed`, and in Loki
`{job="homehub", unit="homehub-portfolio-flex.service"} |= "\"codes\": [\""` shows a query that is pending every day.

Recovery after fixing the cause (a plain daily run; it clears the latch only when the run ends with no codes, so a
`stale_fx` day keeps the latch; `--dry-run`, `--query-id` and `--from-file` never clear it):

    sudo -u homehub-worker -H bash -c 'cd ~/workspace/home-hub/services/accounting-service && set -a && . ~/.config/homehub-statements.env && set +a && .venv/bin/python -m worker flex --unlatch'

A saved statement is re-posted with:

    sudo -u homehub-worker -H bash -c 'cd ~/workspace/home-hub/services/accounting-service && set -a && . ~/.config/homehub-statements.env && set +a && .venv/bin/python -m worker flex --from-file <state_dir>/flex/flex-<ts>-<pid>.xml'

One-off backfill of the manual-import era (before enabling the timer). The backfill query must select the same Trade
and CashTransaction fields as the daily query:

    sudo -u homehub-worker -H bash -c 'cd ~/workspace/home-hub/services/accounting-service && set -a && . ~/.config/homehub-statements.env && set +a && .venv/bin/python -m worker flex --query-id <backfill-query-id> --legacy-until 2026-06-30 --dry-run'

Read `candidates`, `legacy_duplicates` and `error_codes` in the JSON line (`created` is 0 on a dry run by design). The
pairing evidence (row ids that would adopt a Flex fingerprint) is in the API response, not in the summary:

    sudo -u homehub-worker -H curl -s -F file=@<state_dir>/flex/flex-<ts>-<pid>.xml 'http://127.0.0.1:8001/api/portfolio/imports/ib-flex?dry_run=true&legacy_until=2026-06-30' | jq .legacy_pairs

If the numbers are as expected run the same worker command without `--dry-run`:

    sudo -u homehub-worker -H bash -c 'cd ~/workspace/home-hub/services/accounting-service && set -a && . ~/.config/homehub-statements.env && set +a && .venv/bin/python -m worker flex --query-id <backfill-query-id> --legacy-until 2026-06-30'

Each paired old row adopts its Flex fingerprint, so no later import can re-create it; a row that already carries a Flex
fingerprint is never paired again. An `ambiguous_legacy` count above 0 needs the owner before the real run.
From then on the IB Transfer-History CSV is never uploaded again. Then the daily dry run:

    sudo -u homehub-worker -H bash -c 'cd ~/workspace/home-hub/services/accounting-service && set -a && . ~/.config/homehub-statements.env && set +a && .venv/bin/python -m worker flex --dry-run'

`candidates` must be 0 (or only rows newer than the backfill) and `skipped_duplicates` must equal the overlap with the
backfill; stop and ask the owner otherwise. Then a real one:

    sudo -u homehub-worker -H bash -c 'cd ~/workspace/home-hub/services/accounting-service && set -a && . ~/.config/homehub-statements.env && set +a && .venv/bin/python -m worker flex'

Before enabling the timer add `_SYSTEMD_USER_UNIT=homehub-portfolio-flex.service`
to the Alloy journal matcher in `/home/opc/workspace/monitoring/alloy/config.alloy` (outside this repo), else the unit's
lines never reach Loki. Then:

    systemctl --user enable --now homehub-portfolio-flex.timer
    journalctl --user -u homehub-portfolio-flex.service

Deleting the backfill query in IB Account Management after the import is mandatory.

## 國泰證券 daily statement timer (2026-10-29)

`homehub-portfolio-cathay.service` / `.timer` run `python -m worker cathay` daily at 06:40 Asia/Taipei (`Persistent=true`).
The worker lists the Drive folder `投資/國泰證券/日對帳單` (`STATEMENT_CATHAY_FOLDER`, also the password-file key), downloads
each new PDF, unlocks and parses it, and POSTs the trades to the portfolio service
(`POST /api/portfolio/imports/cathay-statement`), then reports the run (`/runs`, best effort; the notifier reads it).
Design: spec `docs/superpowers/specs/2026-10-29-cathay-daily-statement-design.md`. The timer is installed with the glob
above and NOT enabled until the deploy order below is done. Statements dated before the cutover (`STATEMENT_CATHAY_CUTOVER`,
default 2026-10-16) are never committed: they are counted as `pre_cutover`, and `--validate-history` covers them as a dry run.

Password line, added by the owner to the worker's password file (`STATEMENT_PASSWORD_FILE`, default
`~/.config/homehub-statement-passwords.env`), same format as the other folders (the key is the Drive folder):

    投資/國泰證券/日對帳單=$ID

Overrides (all optional, in `~/.config/homehub-statements.env`): `STATEMENT_CATHAY_FOLDER`, `STATEMENT_CATHAY_CUTOVER`,
`STATEMENT_CATHAY_TIMEOUT_S` (portfolio request timeout, default 60), `STATEMENT_PORTFOLIO_API_URL`. `STATEMENT_STATE_DIR`
is `<state_dir>` below. Every manual command is written out in full for the reason given in the flex section.

State files (0600, directory 0700): `<state_dir>/cathay-state.json` (settled files keyed by Drive id and sha256; a listed file whose Drive id and md5
match a settled entry is skipped without download), `<state_dir>/cathay-disabled.json` (the latch), `<state_dir>/cathay-history-report.json` (the last
`--validate-history` report), `<state_dir>/.cathay.lock` (a second run prints `already running`, exit 3).

Flags (`python -m worker cathay`; the summary is one JSON line, exit 1 when `codes` is non-empty):

| Flag | Effect |
|---|---|
| (none) | plain daily commit run: post every new file, write state, report the run (the only run that reports, also when refused while latched) |
| `--dry-run` | post with `dry_run=true`; no state, no latch, no run report; nothing under `<state_dir>/inbox/` (downloads are staged and deleted) |
| `--validate-history` | dry run over every file including pre-cutover dates; still reads the state file (a corrupt one stops the run) but skips nothing and writes nothing to it; writes `cathay-history-report.json` (`mismatch_dates`, `held` as date + reason code without the product name, `errors`); prints only `counts` |
| `--file <drive-id>` / `--since DATE` / `--until DATE` | restrict the run to one Drive file / a trade-date range |
| `--retry DATE` | re-post the settled file of that trade date (for example a `mismatch` after the rows are fixed); a date before the cutover stays `pre_cutover` |
| `--ack DATE` | acknowledge a pending day at the portfolio (`POST …/days/DATE/ack`); touches neither Drive nor state, works while latched, exclusive with every other flag (exit 2 otherwise) |
| `--unlatch` | clear the latch; see below |

Settled states. `pre_cutover` is terminal like `imported`: a file dated before the cutover is skipped on later runs. If `STATEMENT_CATHAY_CUTOVER` is ever moved
earlier, those dates must be re-posted one at a time with `--retry DATE`. `held` is never written and is posted again on every run.

Run report. Only a plain daily commit run (no `--file`, `--since`, `--until`, `--retry`, `--unlatch`, `--ack`, `--dry-run`, `--validate-history`) posts the run
report. A plain daily run refused because the job is latched posts `result: latched` with the latch reason as `code` (`files_seen` and `posted` 0), then exits 1.

Latch rule. Only a commit run (no `--dry-run`, no `--validate-history`) latches. A non-transient failure writes the latch and
every later run exits 1 (`cathay disabled`) until it is cleared: `decrypt` (no password fits), `pdf_malformed`,
`pdf_too_large`, `extract` (text extraction failed), a parser error code, a portfolio 4xx other than 408/429. These do NOT latch: Drive and listing errors,
portfolio 5xx / 408 / 429, timeouts and connection errors, `portfolio:body`, an missing or unreadable password file (`password_file`) or a corrupt state file (`state_file`). A missing password file
fails the run before Drive is listed, so the daily run reports it. A run
that fails without latching still exits 1, so the unit shows failed: `systemctl --user list-units --failed`.
Exit codes: 0 ok, 1 codes / latched / nothing pending for `--ack`, 2 bad flags, 3 already running.

Recovery after fixing the cause (a plain daily run; it clears the latch only when the run ends with no codes, so
`--file`, `--since`, `--until`, `--retry` and dry runs never clear it). A statement the parser cannot read latches again on every `--unlatch`; the way out is a
parser fix, or moving that file out of the Drive folder and importing that day by CSV:

    sudo -u homehub-worker -H bash -c 'cd ~/workspace/home-hub/services/accounting-service && set -a && . ~/.config/homehub-statements.env && set +a && .venv/bin/python -m worker cathay --unlatch'

Deploy order (the portfolio side is already live):

1. Install the units with the glob above (`systemctl --user daemon-reload`); the owner adds the password line.
2. Validate the history once and show the owner the report; nothing is written to the portfolio or the state:

       sudo -u homehub-worker -H bash -c 'cd ~/workspace/home-hub/services/accounting-service && set -a && . ~/.config/homehub-statements.env && set +a && .venv/bin/python -m worker cathay --validate-history'
       jq . <state_dir>/cathay-history-report.json

   Read `counts`, `mismatch_dates`, `held` and `errors`; stop and ask the owner about any `mismatch` or `error`.
3. One manual commit run, then check the summary (`codes` empty, `posted` as expected):

       sudo -u homehub-worker -H bash -c 'cd ~/workspace/home-hub/services/accounting-service && set -a && . ~/.config/homehub-statements.env && set +a && .venv/bin/python -m worker cathay'

4. Before enabling the timer add `_SYSTEMD_USER_UNIT=homehub-portfolio-cathay.service` to the Alloy journal matcher in
   `/home/opc/workspace/monitoring/alloy/config.alloy` (outside this repo), else the unit's lines never reach Loki. Then:

       systemctl --user enable --now homehub-portfolio-cathay.timer
       journalctl --user -u homehub-portfolio-cathay.service

In Loki `{job="homehub", unit="homehub-portfolio-cathay.service"} |~ "\"codes\": \\[\"|cathay disabled"` shows a run that fails every day, including runs refused while latched (those print `cathay disabled`).

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
   - sweep retirement: an applied creation whose entry the owner deleted in the ledger is retired (`stand_in_retired`), including the catch-up pass for deletions from before R6b: pre-R6b deletions are retired by the first daily run (the pending selection includes statements with stranded creations, clean statements too), and its line is marked owner-deleted, so the policy no longer re-books it (no proposal either);
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

Card closing days (2026-10-17): the `closing_day` guardrail checks card statements only. Set each card's `closing_day`
correctly before its statements are parsed: a card revision whose `period_end` or `closing_date` lies more than 3 days
from it goes to `parse_review`, so a wrong `closing_day` sends every statement of that card there; a card without one
skips the check, and bank statements are never checked.

Caps: `replace_stand_in` has amount 0 (no budget use) but counts toward `per_statement_count`. `per_statement_count`
(default 5) bounds every applied policy row per statement for good (system entries, backfills and replacements all count);
the rest become proposals. With the default `per_statement_total` "5000" and `monthly_total` "20000", a real anchor
(often far above those, under its own `max_amount` "200000") becomes a proposal the owner confirms. The policy anchors
only a target that starts less than one calendar month after `statement_live_from` and that no live bank statement of the account precedes; a target after it, or an earlier month that arrives later, is left
to the owner (the dry-run line `anchor above an earlier statement` lists the accounts). A shadow farther than
`exact_window_days` from its line is proposed (`date_drift`), not applied. Revert a wrong `anchor_opening` before any `balance_adjustment_asof` on that account (`closing_adjustments_present`).

Duplicate statements (one PDF parsed twice with different periods, before the 2026-10-17 file guard): retire the copy with `POST /accounts/{id}/statements/{sid}/retire` and an `admin` token, body `{"note": "..."}` (service README "One file, one statement"). A 409 `anchor_depends` / `actions_applied` names the actions to revert first where they can be reverted; a statement with an irreversible, snapshot-refused or partially stranded applied action has no supported retire path (owner / Lead; never edit the applied marker). Never delete or rewrite a `statement_retired` audit row: it is the retired marker.

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

## Composite statements (國泰 split)

Rollout order (design spec section 6): deploy the API, then the worker (the worker tolerates an API without `composite_map`, the API tolerates old workers); the changed `mapping_version` re-tries every `mapping` failure once, so the 臺灣銀行一般 files return to `ignored / mapping`. Then the owner sets `statement_live_from` on the mapped accounts (periods before it land historical), then PUTs `composite_map` (with the current `account_map`, `dirty_enabled`, `rules`, `policy` and `account_siblings`, since a PUT replaces them wholesale); the map change bumps `mapping_version` and the next poll re-queues the files. Verify that the files are `parsed`, every section in the run summary's `composite[]` is `parsed` or `anchor`, and each statement's closing balance equals the 持有明細 balance.

Operator re-queue (after a split or 422 failure that a code or settings change has fixed, or after adding a key for an already-parsed file): `UPDATE statement_file SET status='new', failure=NULL WHERE id = <id>;`. There is no automatic retry without a `parser_version` change; `retry_due` is unchanged.

Watching composite files: a composite failure is deliberate and silent by design, so after each monthly 國泰 file check the run summary. It is logged as one JSON line and shipped to Loki by Alloy; in Grafana Explore the query `{job="homehub-statements"} |= "composite" | json | sections_failed > 0` (adjust the label to the dashboard's) lists runs with a failed section, and the `composite[].sections[]` entries carry the key, outcome and reason (`split_error`, `422`). A `split_error` after a bank layout change needs a rule change in `worker/composite.py`, not a re-queue.

## verify user: parser credentials and the gate latch (2026-10-20)

- `python -m worker verify` needs the statement password file for composite folders (`STATEMENT_PASSWORD_FILE`, default
  `~/.config/homehub-statement-passwords.env`, 0600, owned by the verify user) — install it from the owner's copy with
  `sudo install -m 600 -o homehub-verify -g homehub-verify <src> <dst>`; it is read only when a composite folder is met.
- The verify user's parser login (`STATEMENT_VERIFY_PARSER_CONFIG_DIR`, default `~/.local/state/home-hub-parser/claude-verify`)
  is its OWN login, made with `CLAUDE_CONFIG_DIR="$dst" claude /login` as the verify user (the "Verify parser login copy"
  section above) — never a copy of the worker's or the owner's login: a copied refresh token rotates on use and logs the
  other holder out. Nothing refreshes it automatically; re-login when `verify` reports `auth`.
- An expired login fails the canary and latches `parser-disabled.json` in the VERIFY user's state dir. `python -m worker gate`
  cannot clear that latch when run as the verify user (it canaries the worker's `claude` config dir, which the verify user
  does not have). Clear it by moving the latch file aside after the re-login; the next `verify` run re-canaries.
- A one-off verify of a few files: set `STATEMENT_VERIFY_DIR` to a fresh directory holding `masked/<sha>.json` metas and
  `pdf-cache/inbox/by-sha/<sha>.pdf`; the usual verify dir re-verifies every snapshot it holds (hundreds of parses).

## R7 Phase 1 rollout (portfolio settlement integration)

Worker: `homehub-portfolio-cathay.service` needs `STATEMENT_API_TOKEN_FILE` (default `~/.config/homehub-statement-token`) to call
HomeHub (`POST /reconciliation/settlements/run`) after a plain commit run. Without the file the run still succeeds and its
summary shows `settlement_run = "skipped:no_token"`. The worker deploy is an update of the `%h/workspace/home-hub` checkout the
unit runs from (see the 國泰證券 daily statement timer section above).

Order:

1. Deploy the portfolio release (migration `e9settle`); confirm `CASH_LEG_ENABLED=true` in its env.
2. Deploy the accounting API (migration `d5e1f7a3b9c2`; `PORTFOLIO_BASE_URL` in its env if not the default `http://127.0.0.1:8001`,
   optional `PORTFOLIO_READ_TOKEN`) and the worker checkout.
3. Settings PUT, BEFORE the owner SQL of step 4: `book_settlement` is enabled by default (`max_amount` "0", cap-exempt), so
   any reconcile of 67 after step 4 would book under the stored settings. `PUT /settings/reconciliation` replaces the whole
   document, so GET the current one, edit, PUT it back whole. For the dry run set
   `policy.actions.book_settlement.enabled = false` and
   `settlement = {"stand_in_enabled": false, "enabled_from": null, "target_accounts": {"67": 117}, "twin_window_days": 3}`
   (`dividend_category_id` stays null). GET it back and check both switches read `false`.
4. Owner SQL: `UPDATE account SET settlement_source = 'portfolio:cathay' WHERE id = 67;`. `valuation_source` on 117 waits for the
   SPA follow-up that renders `balance: null` as 「市值暫不可得」 (rollout gate).
5. Dry run: `r6_case_dump.py` baseline and candidate, then `r6_dryrun.py --settlements [--stand-ins report.json]`
   (the stand-in report is `run_stand_ins(engine, dry_run=True)` as JSON: read-only, `booking_id: null`, ignores
   `stand_in_enabled`, `previewed_settings` shows what it assumed). The candidate dump reads the settlement feed and lists
   each settlement line's outcome (`book_settlement`, `ambiguous`, `pending`, `mismatch`, `drift`, `stand_in_claim`) as the
   next reconcile would derive it, without writing. The owner reviews the listing case by case, one case per
   question; counts from 2026-10-19 are not expected to hold.

   Before step 6 the owner confirms 67's `statement_live_from` and the chosen `enabled_from` are on or after 2026-01-01
   (the bank business-day table starts there).
6. Enable: `book_settlement.enabled = true`; `settlement.stand_in_enabled = true` with `enabled_from` = the go-live date (no
   stand-ins for days settled before it); reconcile 67's live statements; check the first stand-in on the next settlement day.
   `POST /reconciliation/settlements/run` needs the INGEST scope; the daily sweep runs the stand-in job first (`stand_ins` in its answer).

Rollback: once the first `settlement` case exists, an accounting API rolled back below this release cannot load it (the
case kind is a new enum value: inbox and case reads return 500), and the migrations' downgrades leave the enum values in
place. So there is no code rollback after that point: rollback = disable (`policy.actions.book_settlement.enabled = false`,
`settlement.stand_in_enabled = false`, one whole-document settings PUT) and roll forward with a fix. Before the first
settlement case (steps 1–2 only), the previous release can still be restored.

Disabling stops new bookings, not every write. While 67 keeps its `settlement_source`:

- the stand-in job (06:40 worker call and the daily sweep) still runs for every account with a known
  `settlement_source` and a booked stand-in (`trade_settlement_service._accounts`): `housekeep` tombstones owner-deleted /
  owner-edited pairs; `check_row` retires an unclaimed stand-in whose owner twin appeared (`twin`) or whose feed row is gone
  or no longer bookable (`gone` / the feed reason), and a changed feed row retires the stand-in (`replaced`) and then fails
  the disabled gate — a `skipped` row with reason `disabled`, `replaced_waiting`, and no transfer for that day any more;
- each reconcile of a 67 bank statement printing a settlement line still reads the feed, runs `housekeep` and the owner-twin
  retirements, and links claimed stand-ins to their lines; the owner can still apply `book_settlement` by hand (only the
  policy's action is switched off).

To freeze everything, also clear the source: owner SQL `UPDATE account SET settlement_source = NULL WHERE id = 67;` (or the
admin `PATCH /accounts/67` with `{"settlement_source": null}`). A NULL source takes 67 out of both paths:
`trade_settlement_service.applies()` is false, so the reconcile makes no feed read, no `housekeep`, no twin retirement, no
claim link and no settlement outcome (its `settlements` count block is `{}`), and `_accounts()` / `recheck_statement_ids()`
select only `settlement_source IN SETTLEMENT_SOURCES`, so the job and the re-check list skip 67. `book_settlement` is then
refused `no_target`. The booked pairs and stand-ins stay in the ledger as they are; 67's settlement lines reconcile as
ordinary bank lines (no `settlement_*` hints) until the source is set again, and owner deletes / edits made meanwhile are
tombstoned by the first reconcile or job run after it is restored.

Check afterwards (counts and codes only):

- `run_stand_ins(engine, dry_run=True)` with the source cleared: `booked` and `skipped` empty, `touched_dates` `{}`,
  `housekeeping` all zero (a dry run previews as if `stand_in_enabled` were true, so with the source kept it still lists
  the days it would book — `previewed_settings.stand_in_enabled` shows the stored `false`);
- `POST /reconciliation/settlements/run` (or the worker summary's `settlement_run`): `status` `disabled`, `booked` 0;
  with the source cleared every `housekeeping` count is 0 — with it kept, `retired` / `replaced_waiting` /
  `owner_deleted` / `owner_edited` show the continuing writes above;
- `SELECT status, reason, count(*) FROM settlement_booking WHERE account_id = 67 GROUP BY 1, 2;` is unchanged between two
  runs once the source is cleared;
- a reconcile of a 67 statement answers `settlements: {}` and `settlement_source_unavailable: false`; the candidate
  `r6_case_dump.py` lists no `book_settlement` or `stand_in_claim` outcome for 67.
